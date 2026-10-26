"""Local-only model gateway and explicitly scoped Neo4j persistence."""
import json
import math
import os
from pathlib import Path
import time
from typing import Literal
import uuid
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, Field

from neurogra.benchmarks.patient_view import digest


class Config(BaseModel):
    model_config = ConfigDict(extra='forbid')
    base_url: str = 'http://127.0.0.1:11434'
    model: str = 'qwen3.8:27b'
    embedding_model: str = 'qwen3-embedding:0.6b'
    timeout: float = Field(default=300, gt=0)
    max_calls: int = Field(default=160, ge=1)
    max_embedding_calls: int = Field(default=500, ge=1)
    max_seconds: float = Field(default=3600, gt=0)
    context_tokens: int = Field(default=32768, ge=4096)
    max_input_chars: int = Field(default=24000, ge=1000)
    output_tokens: int = Field(default=4096, ge=256)
    answer_output_tokens: int = Field(default=4096, ge=256)
    answer_context_tokens: int | None = Field(default=None, ge=4096)
    answer_max_input_chars: int | None = Field(default=None, ge=1000)
    chunk_chars: int = Field(default=2400, ge=100, le=6000)
    link_threshold: float = Field(default=0.6, ge=-1, le=1)
    merge_threshold: float = Field(default=0.95, ge=-1, le=1)
    selected_graphs: int = Field(default=3, ge=1, le=10)
    evidence_blocks: int = Field(default=6, ge=1, le=20)
    evidence_excerpt_chars: int = Field(default=1600, ge=300, le=4000)
    graph_entities: int = Field(default=16, ge=1, le=100)
    graph_relationships: int = Field(default=18, ge=1, le=100)
    document_summary_chars: int = Field(default=1800, ge=300, le=6000)
    knowledge_manifest: str | None = None
    knowledge_cache: str | None = None
    model_cache: str | None = None
    graph_backend: Literal['json', 'neo4j'] = 'json'
    neo4j_uri: str = 'bolt://127.0.0.1:7687'
    neo4j_database: str = 'medgraphrag'
    neo4j_username: str = 'neo4j'
    password_env: str = 'MEDGRAPHRAG_NEO4J_PASSWORD'
    isolated_neo4j_instance: bool = False

    def validate_local(self):
        if self.model != 'qwen3.8:27b' or self.embedding_model != 'qwen3-embedding:0.6b':
            raise ValueError('required_local_qwen_models_only')
        if urlparse(self.base_url).hostname not in {'localhost', '127.0.0.1', '::1'}:
            raise ValueError('local_Ollama_only')
        if urlparse(self.neo4j_uri).hostname not in {'localhost', '127.0.0.1', '::1'}:
            raise ValueError('local_Neo4j_only')
        if self.neo4j_database == 'system' or (self.neo4j_database == 'neo4j' and not (
                self.isolated_neo4j_instance and urlparse(self.neo4j_uri).port not in {None, 7687})):
            raise ValueError('dedicated_baseline_database_required')
        if self.output_tokens >= self.context_tokens:
            raise ValueError('output_must_leave_input_context')
        if self.answer_output_tokens >= (self.answer_context_tokens or self.context_tokens):
            raise ValueError('answer_output_must_leave_input_context')

    def answer_limits(self):
        return self.model_copy(update={
            'context_tokens': self.answer_context_tokens or self.context_tokens,
            'max_input_chars': self.answer_max_input_chars or self.max_input_chars,
            'output_tokens': self.answer_output_tokens})


def write_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def request_fits(config, system, data, schema=None, reserve_bytes=0, output_tokens=None):
    messages = [{'role': 'system', 'content': system},
                {'role': 'user', 'content': json.dumps(data, ensure_ascii=False)}]
    serialized = json.dumps(messages, ensure_ascii=False)
    contract = json.dumps(schema.model_json_schema()) if schema else ''
    return (len(serialized) + len(contract) + reserve_bytes <= config.max_input_chars and
            len(serialized.encode('utf-8')) + len(contract.encode()) + reserve_bytes +
            (config.output_tokens if output_tokens is None else output_tokens) <= config.context_tokens)


class LocalModel:
    def __init__(self, config, output):
        config.validate_local()
        self.config, self.output = config, Path(output)
        self.started, self.calls, self.embedding_calls = time.monotonic(), 0, 0
        self.usage = {'prompt_tokens': 0, 'output_tokens': 0, 'embedding_tokens': 0}
        self.identity = None

    def cache_path(self, kind, data):
        if not self.config.model_cache:
            return None
        root = Path(self.config.model_cache)
        root.mkdir(parents=True, exist_ok=True)
        key = digest({'kind': kind, 'models': self.identity, 'input': data})
        return root / (key + '.json')

    def cached(self, path):
        if path and path.exists():
            result = json.loads(path.read_text(encoding='utf-8'))
            if result['hash'] != digest(result['value']):
                raise ValueError('model_cache_integrity_failure')
            with (self.output / 'cache_events.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps({'event': 'model_cache_hit', 'path': str(path)}) + '\n')
            return result['value']
        return None

    def save_cache(self, path, value):
        if path:
            write_json(path, {'hash': digest(value), 'value': value})

    def peek_generation(self, system, data, schema=None):
        messages = [{'role': 'system', 'content': system},
                    {'role': 'user', 'content': json.dumps(data, ensure_ascii=False)}]
        cache = self.cache_path('generate', {'messages': messages,
                                'schema': schema.model_json_schema() if schema else None,
                                'context_tokens': self.config.context_tokens,
                                'output_tokens': self.config.output_tokens})
        value = self.cached(cache)
        return schema.model_validate(value) if value is not None and schema else value

    def preflight(self):
        with urlopen(self.config.base_url.rstrip('/') + '/api/tags', timeout=self.config.timeout) as response:
            tags = json.load(response).get('models', [])
        names = {model['name']: model for model in tags}
        required = {self.config.model, self.config.embedding_model}
        if not required <= names.keys():
            raise ValueError('required_local_models_not_installed: ' + str(sorted(required - names.keys())))
        self.identity = {name: {'name': name, 'digest': names[name].get('digest'),
                               'details': names[name].get('details')} for name in sorted(required)}
        return self.identity

    def request(self, endpoint, body, stage):
        if time.monotonic() - self.started >= self.config.max_seconds:
            raise TimeoutError('run_deadline')
        remaining = self.config.max_seconds - (time.monotonic() - self.started)
        begin = time.monotonic()
        entry = {'stage': stage, 'endpoint': endpoint, 'request': body}
        try:
            request = Request(self.config.base_url.rstrip('/') + endpoint,
                              data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
                              headers={'Content-Type': 'application/json'})
            with urlopen(request, timeout=min(self.config.timeout, remaining)) as response:
                result = json.load(response)
            entry['response'] = result
            self.usage['prompt_tokens'] += result.get('prompt_eval_count', 0) if endpoint == '/api/chat' else 0
            self.usage['output_tokens'] += result.get('eval_count', 0)
            self.usage['embedding_tokens'] += result.get('prompt_eval_count', 0) if endpoint == '/api/embed' else 0
            return result
        except Exception as exc:
            entry['error'] = str(exc)
            raise
        finally:
            entry['seconds'] = time.monotonic() - begin
            with (self.output / 'llm_calls.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(entry, ensure_ascii=False) + '\n')

    def generate(self, system, data, stage, schema=None):
        limits = (self.config.answer_limits() if stage in {
            'query_summary', 'summary_similarity', 'initial_answer', 'reference_revision', 'format_repair'}
            else self.config)
        output_tokens = limits.output_tokens
        messages = [{'role': 'system', 'content': system},
                    {'role': 'user', 'content': json.dumps(data, ensure_ascii=False)}]
        size = len(json.dumps(messages, ensure_ascii=False)) + (len(json.dumps(schema.model_json_schema())) if schema else 0)
        # UTF-8 byte count is a conservative upper bound; explicit failure, never slicing.
        token_bound = len(json.dumps(messages, ensure_ascii=False).encode('utf-8'))
        token_bound += len(json.dumps(schema.model_json_schema()).encode()) if schema else 0
        if size > limits.max_input_chars or token_bound + output_tokens > limits.context_tokens:
            raise ValueError('context_limit: explicitly reduce visible data or select fewer graphs')
        cache = self.cache_path('generate', {'messages': messages, 'schema': schema.model_json_schema() if schema else None,
                              'context_tokens': limits.context_tokens, 'output_tokens': output_tokens})
        value = self.cached(cache)
        if value is not None:
            return schema.model_validate(value) if schema else value
        if self.calls >= self.config.max_calls:
            raise ValueError('generation_budget_exceeded')
        self.calls += 1
        payload = {'model': self.config.model, 'messages': messages, 'stream': False, 'think': False,
                   'options': {'temperature': 0, 'num_ctx': limits.context_tokens,
                               'num_predict': output_tokens}}
        if schema:
            payload['format'] = schema.model_json_schema()
        result = self.request('/api/chat', payload, stage)
        if result.get('done_reason') == 'length':
            raise ValueError('model_output_truncated')
        content = result['message']['content']
        if stage in {'reference_revision', 'format_repair'}:
            (self.output / ('raw_' + stage + '.txt')).write_text(content, encoding='utf-8')
            (self.output / 'raw_response.txt').write_text(content, encoding='utf-8')
        if not content.strip():
            raise ValueError('empty_model_output')
        parsed = schema.model_validate_json(content) if schema else content
        self.save_cache(cache, parsed.model_dump() if schema else parsed)
        return parsed

    def embed(self, text):
        cache = self.cache_path('embedding', {'text': text, 'model': self.config.embedding_model})
        vector = self.cached(cache)
        if vector is not None:
            return vector
        if self.embedding_calls >= self.config.max_embedding_calls:
            raise ValueError('embedding_budget_exceeded')
        self.embedding_calls += 1
        result = self.request('/api/embed', {'model': self.config.embedding_model,
                              'input': text, 'truncate': False}, 'embedding')
        vector = result['embeddings'][0]
        if not vector or not all(type(v) in (int, float) and math.isfinite(v) for v in vector):
            raise ValueError('invalid_embedding')
        self.save_cache(cache, vector)
        return vector


class Neo4jStore:
    """No unscoped match, global merge, database creation, or deletion."""
    def __init__(self, config):
        from neo4j import GraphDatabase
        config.validate_local()
        password = os.environ.get(config.password_env)
        if not password:
            raise ValueError('missing_password_env: ' + config.password_env)
        self.database = config.neo4j_database
        self.driver = GraphDatabase.driver(config.neo4j_uri, auth=(config.neo4j_username, password))
        try:
            self.driver.verify_connectivity()
            with self.driver.session(database=self.database) as session:
                session.run('RETURN 1 AS ready').consume()
        except Exception:
            self.driver.close()
            raise

    def save(self, run_id, graph):
        # Whole graph payload preserves evidence and types without interpolated Cypher labels.
        with self.driver.session(database=self.database) as session:
            session.run('MERGE (g:MedGraphRAGArtifact {run_id:$run, gid:$gid}) '
                        'SET g.layer=$layer, g.payload=$payload, g.fingerprint=$fingerprint',
                        run=run_id, gid=graph['gid'], layer=graph['layer'],
                        payload=json.dumps(graph, ensure_ascii=False), fingerprint=digest(graph)).consume()
            for entity in graph.get('entities', []):
                session.run('MERGE (e:MedGraphRAGEntity {run_id:$run, gid:$gid, entity_id:$id}) '
                            'SET e.payload=$payload', run=run_id, gid=graph['gid'], id=entity['id'],
                            payload=json.dumps(entity, ensure_ascii=False)).consume()
            for rel in graph.get('relationships', []):
                session.run('MATCH (a:MedGraphRAGEntity {run_id:$run, gid:$gid, entity_id:$src}), '
                            '(b:MedGraphRAGEntity {run_id:$run, gid:$gid, entity_id:$tgt}) '
                            'MERGE (a)-[r:MEDGRAPH_RELATION {kind:$kind}]->(b) SET r.payload=$payload',
                            run=run_id, gid=graph['gid'], src=rel['source'], tgt=rel['target'],
                            kind=rel['kind'], payload=json.dumps(rel, ensure_ascii=False)).consume()

    def link(self, run_id, links):
        with self.driver.session(database=self.database) as session:
            for link in links:
                session.run('MATCH (a:MedGraphRAGEntity {run_id:$run, gid:$a, entity_id:$src}), '
                            '(b:MedGraphRAGEntity {run_id:$run, gid:$b, entity_id:$tgt}) '
                            'MERGE (a)-[r:REFERENCE]->(b) SET r.similarity=$similarity',
                            run=run_id, a=link['from_gid'], b=link['to_gid'], src=link['source'],
                            tgt=link['target'], similarity=link['similarity']).consume()

    def close(self):
        self.driver.close()


class JsonGraphStore:
    """Run-scoped graph snapshots, same graph/link/retrieval algorithm as Neo4j mode."""
    def __init__(self, output):
        self.output = Path(output)

    def save(self, run_id, graph):
        write_json(self.output / (graph['gid'] + '.json'), {**graph, 'run_id': run_id})

    def link(self, run_id, links):
        write_json(self.output / 'reference_links.json', {'run_id': run_id, 'links': links})

    def close(self):
        pass
