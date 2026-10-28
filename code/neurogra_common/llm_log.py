"""Configurable UTF-8 model audit, shared by clinical and KG calls."""
import json
import hashlib
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from .identity import new_id

_lock = threading.RLock()
_path = None
_full_content = True
_max_bytes = None
_backup_count = 3


def configure_llm_log(path: Path | None, *, full_content=True, max_bytes=None, backup_count=3):
    global _path, _full_content, _max_bytes, _backup_count
    if max_bytes is not None and (max_bytes < 1024 or not 1 <= backup_count <= 10):
        raise ValueError('invalid_log_retention_policy')
    with _lock:
        _full_content, _max_bytes, _backup_count = full_content, max_bytes, backup_count
        _path = Path(path).resolve() if path is not None else None
        if _path is not None:
            _path.parent.mkdir(parents=True, exist_ok=True)
            _path.touch(exist_ok=True)
    return _path


def record_llm_event(event, **fields):
    with _lock:
        if _path is None:
            return
        record = {'timestamp': datetime.now(timezone.utc).isoformat(), 'event': event, **fields}
        if not _full_content:
            for key in ('request', 'response', 'error', 'messages', 'output'):
                if key in record:
                    raw = json.dumps(record.pop(key), ensure_ascii=False, allow_nan=False).encode('utf-8')
                    record[key + '_summary'] = {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
        data = (json.dumps(record, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')
        if _max_bytes and len(data) > _max_bytes:
            data = (json.dumps({'timestamp': record['timestamp'], 'event': event,
                'call_id': fields.get('call_id'), 'omitted': 'record_exceeds_log_limit',
                'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}) + '\n').encode('utf-8')
        if _max_bytes and _path.stat().st_size + len(data) > _max_bytes:
            for index in range(_backup_count, 0, -1):
                source = _path if index == 1 else _path.with_name(_path.name + '.' + str(index - 1))
                target = _path.with_name(_path.name + '.' + str(index))
                if source.exists():
                    source.replace(target)
        with _path.open('ab') as output:
            output.write(data)


def logged_model_call(transport, url, payload, timeout, **metadata):
    call_id = new_id('llm_call')
    started = time.monotonic()
    record_llm_event('request', call_id=call_id, metadata=metadata,
                     url=url, timeout_seconds=timeout, request=payload)
    try:
        response = transport(url, payload, timeout)
    except Exception as exc:
        record_llm_event('error', call_id=call_id, metadata=metadata,
                         elapsed_seconds=time.monotonic() - started,
                         error_type=type(exc).__name__, error=str(exc))
        raise
    record_llm_event('response', call_id=call_id, metadata=metadata,
                     elapsed_seconds=time.monotonic() - started, response=response)
    return response
