"""Full, UTF-8 JSONL audit of actual model requests, shared by clinical and KG calls."""
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from .identity import new_id

_lock = threading.RLock()
_path = None


def configure_llm_log(path: Path | None):
    global _path
    with _lock:
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
        with _path.open('a', encoding='utf-8') as output:
            output.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + '\n')


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
