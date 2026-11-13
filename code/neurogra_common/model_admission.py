"""Optional per-call model admission, shared by clinical and knowledge adapters.

The host installs a context-local factory; standalone SDK use remains unchanged.
Context variables propagate through asyncio.to_thread without global mutable state.
"""

from contextlib import contextmanager, nullcontext
from contextvars import ContextVar

_factory = ContextVar('neurogra_model_admission', default=None)


@contextmanager
def model_admission(factory):
    token = _factory.set(factory)
    try:
        yield
    finally:
        _factory.reset(token)


def admit_model(scope):
    factory = _factory.get()
    return factory(scope) if factory is not None else nullcontext()
