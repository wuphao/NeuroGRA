"""Text generation across native Ollama and three Chat Completions providers."""

import json
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

PROVIDERS = {
    'ollama': ('Ollama', 'http://localhost:11434', None),
    'openrouter': ('OpenRouter', 'https://openrouter.ai/api/v1', 'OPENROUTER_API_KEY'),
    'deepseek': ('DeepSeek', 'https://api.deepseek.com', 'DEEPSEEK_API_KEY'),
    'openai': ('OpenAI', 'https://api.openai.com/v1', 'OPENAI_API_KEY'),
}
KEY_FILE = '.env.llm.local'
EXTERNAL_FLAG = 'LLM_ALLOW_EXTERNAL_PATIENT_DATA'


class GenerationError(ValueError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def read_credentials(root):
    root = Path(root).resolve()
    path = (root / KEY_FILE).resolve()
    if not path.is_relative_to(root):
        raise GenerationError('模型 Key 文件必须位于当前工作空间。')
    keys = {key for _, _, key in PROVIDERS.values() if key} | {EXTERNAL_FLAG}
    values = {}
    try:
        if path.is_file():
            for line in path.read_text(encoding='utf-8-sig').splitlines():
                name, separator, value = line.partition('=')
                if separator and name.strip() in keys:
                    values[name.strip()] = value.strip().strip('"\'')
    except (OSError, UnicodeError):
        raise GenerationError('无法读取模型 Key 文件，请检查文件格式和权限。') from None
    return values


def provider_endpoint(config):
    url = config.base_url.rstrip('/') if config.provider == 'ollama' else PROVIDERS[config.provider][1]
    try:
        parsed = urlsplit(url)
        if (parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment):
            raise ValueError
    except ValueError:
        raise GenerationError('模型地址必须为无内嵌凭据、参数或片段的 HTTP/HTTPS 地址。') from None
    return url


def local_model(config):
    return (config.provider == 'ollama' and not config.model.lower().endswith(('-cloud', ':cloud'))
            and urlsplit(provider_endpoint(config)).hostname in {'127.0.0.1', 'localhost', '::1'})


def patient_endpoint_allowed(root, url, model=''):
    """Apply the same patient-data outbound setting to retrieval model endpoints."""
    try:
        parsed = urlsplit(url)
        local = (parsed.hostname in {'127.0.0.1', 'localhost', '::1'} and not parsed.username and
                 not parsed.password and not str(model).lower().endswith(('-cloud', ':cloud')))
    except (TypeError, ValueError):
        local = False
    return local or read_credentials(root).get(EXTERNAL_FLAG, '').lower() == 'true'


def secure_json(url, payload, timeout, *, headers):
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode('utf-8')
    request = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json', **headers})
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        reason = {401: '鉴权失败，请检查本地 Key。', 403: '当前 Key 无权访问该模型。',
                  404: '接口或模型不存在。', 429: '触发限流或额度不足。'}.get(error.code, '请检查模型及供应商服务。')
        raise GenerationError(f'模型服务返回 HTTP {error.code}。{reason}') from None
    except (OSError, ValueError):
        raise GenerationError('模型服务无法连接或返回格式无效。') from None


class GenerationClient:
    def __init__(self, config, root, *, patient_data=True, transport=None, secure_transport=secure_json):
        self.config = config
        self.provider = config.provider
        self.base_url = provider_endpoint(config)
        self.native = self.provider == 'ollama'
        self.transport = transport
        self.secure_transport = secure_transport
        local = local_model(config)
        values = {} if local else read_credentials(root)
        if patient_data and not local and values.get(EXTERNAL_FLAG, '').lower() != 'true':
            raise GenerationError(f'当前模型在本机以外。病例资料外发开关位于 {KEY_FILE}。')
        key_name = PROVIDERS[self.provider][2]
        self.key = values.get(key_name, '') if key_name else ''
        if key_name and (not self.key or not self.key.isascii() or any(c.isspace() for c in self.key)):
            raise GenerationError(f'请在 {KEY_FILE} 中填写有效的 {key_name}。')

    @property
    def chat_url(self):
        return self.base_url + ('/api/chat' if self.native else '/chat/completions')

    def request(self, url, payload, timeout):
        if self.native and self.transport is not None:
            return self.transport(url, payload, timeout)
        headers = {'Authorization': 'Bearer ' + self.key} if self.key else {}
        return self.secure_transport(url, payload, timeout, headers=headers)

    def models(self):
        result = self.request(self.base_url + ('/api/tags' if self.native else '/models'), None, 10)
        values, field = (result.get('models', []), 'name') if self.native else (result.get('data', []), 'id')
        return sorted({item[field] for item in values if isinstance(item, dict) and isinstance(item.get(field), str)})[:2000]

    def payload(self, messages, *, tools=None, schema=None, output_tokens=None):
        limit = output_tokens or self.config.max_output_tokens
        payload = {'model': self.config.model, 'messages': messages, 'stream': False}
        if self.native:
            payload.update(think=False, options={'num_predict': limit, 'num_ctx': self.config.context_tokens})
            if schema is not None:
                payload['options']['temperature'] = 0
                payload['format'] = schema
        else:
            payload['max_tokens' if self.provider == 'deepseek' else 'max_completion_tokens'] = limit
            if self.provider == 'deepseek':
                payload['thinking'] = {'type': 'disabled'}
            if schema is not None:
                payload['response_format'] = {'type': 'json_object'}
                payload['messages'] = [{**messages[0], 'content': messages[0]['content'] +
                    '\nReturn a JSON object matching this schema:\n' + json.dumps(schema, ensure_ascii=False)}, *messages[1:]]
        if tools:
            payload['tools'] = tools
        return payload

    def normalize(self, response):
        if self.native:
            return response
        try:
            choice = response['choices'][0]
            message = choice['message']
            usage = response.get('usage') or {}
            return {'message': message, 'prompt_eval_count': usage.get('prompt_tokens'),
                    'eval_count': usage.get('completion_tokens')}
        except (KeyError, TypeError, IndexError, AttributeError):
            raise GenerationError('模型未返回有效的对话结果。') from None

    def complete(self, messages, timeout, *, tools=None, output_tokens=None):
        payload = self.payload(messages, tools=tools, output_tokens=output_tokens)
        return self.normalize(self.request(self.chat_url, payload, timeout))['message']
