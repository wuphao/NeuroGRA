"""Local Ollama transport with real call logs and embedding-only reuse."""
import json
import math
from pathlib import Path
import time
from urllib.request import Request, urlopen

from .core import canonical, digest, read_json, write_json


class Gateway:
    def __init__(self, config, output):
        self.c, self.output = config, Path(output)
        self.output.mkdir(parents=True, exist_ok=True)
        self.identity = None
        self.calls = self.embedding_calls = 0
        self.usage = {'prompt_tokens': 0, 'output_tokens': 0, 'embedding_tokens': 0}
        self.embedding_cache = self.output / 'embedding_cache'
        self.case_id = None

    def request(self, endpoint, payload=None, stage='preflight', timeout=None):
        begin = time.monotonic()
        req = Request(self.c['base_url'].rstrip('/') + endpoint,
                      data=None if payload is None else canonical(payload).encode('utf-8'),
                      headers={'Content-Type': 'application/json'})
        entry = {'case_id': self.case_id, 'stage': stage, 'endpoint': endpoint, 'request': payload}
        try:
            with urlopen(req, timeout=min(timeout or self.c['timeout_seconds'], self.c['timeout_seconds'])) as response:
                data = json.load(response)
            entry['response'] = data
            self.usage['prompt_tokens'] += data.get('prompt_eval_count', 0) if endpoint == '/api/chat' else 0
            self.usage['embedding_tokens'] += data.get('prompt_eval_count', 0) if endpoint == '/api/embed' else 0
            self.usage['output_tokens'] += data.get('eval_count', 0)
            return data
        except Exception as exc:
            entry['error'] = {'type': type(exc).__name__, 'message': str(exc)}
            raise
        finally:
            entry['seconds'] = time.monotonic() - begin
            with (self.output / 'model_calls.jsonl').open('a', encoding='utf-8') as f:
                f.write(canonical(entry) + '\n')

    def preflight(self):
        tags = self.request('/api/tags')['models']
        by_name = {m['name']: m for m in tags}
        required = {self.c['model'], self.c['embedding_model']}
        if not required <= by_name.keys():
            raise ValueError('configured_models_missing:' + str(sorted(required - by_name.keys())))
        self.identity = {name: by_name[name]['digest'] for name in sorted(required)}
        if not all(self.identity.values()):
            raise ValueError('model_digest_missing')
        return self.identity

    def generate(self, system, data, stage, schema=None, *, answer=False):
        # Compatibility with the existing MedGraphRAG summary/rating interface.
        tokens = self.c['answer_tokens'] if answer else min(1200, self.c['answer_tokens'])
        messages = [{'role': 'system', 'content': system},
                    {'role': 'user', 'content': canonical(data)}]
        if len(canonical(messages).encode('utf-8')) + tokens > self.c['context_tokens']:
            raise ValueError('conservative_context_limit_exceeded')
        payload = {'model': self.c['model'], 'messages': messages, 'stream': False,
                   'think': False, 'options': {'temperature': 0, 'num_predict': tokens,
                                             'num_ctx': self.c['context_tokens']}}
        if schema:
            payload['format'] = schema.model_json_schema()
        self.calls += 1
        result = self.request('/api/chat', payload, stage)
        if result.get('done_reason') == 'length':
            raise ValueError('model_output_truncated')
        text = result['message']['content']
        if not text.strip():
            raise ValueError('empty_model_response')
        return schema.model_validate_json(text) if schema else text

    def embed(self, text):
        if not self.identity:
            raise ValueError('preflight_required')
        key = digest([self.identity[self.c['embedding_model']], text])
        p = self.embedding_cache / (key + '.json')
        if p.is_file():
            data = read_json(p)
            if data['hash'] != digest(data['vector']):
                raise ValueError('embedding_cache_corrupted')
            return data['vector']
        self.embedding_calls += 1
        result = self.request('/api/embed', {'model': self.c['embedding_model'],
                              'input': text, 'truncate': False}, 'embedding')
        vector = result['embeddings'][0]
        if not vector or not all(type(x) in {int, float} and math.isfinite(x) for x in vector):
            raise ValueError('invalid_embedding')
        write_json(p, {'vector': vector, 'hash': digest(vector)})
        return vector
