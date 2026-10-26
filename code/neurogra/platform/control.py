"""Optional execution hooks, propagated through asyncio.to_thread contexts."""
from contextlib import contextmanager
from contextvars import ContextVar

_check = ContextVar('platform_cancel_check', default=None)
_event = ContextVar('platform_progress_event', default=None)


def check_cancelled():
    callback = _check.get()
    if callback:
        callback()


def report_event(kind, payload):
    callback = _event.get()
    if callback:
        callback(kind, payload)


@contextmanager
def execution_control(check, event):
    check_token, event_token = _check.set(check), _event.set(event)
    try:
        yield
    finally:
        _check.reset(check_token)
        _event.reset(event_token)
