"""Cross-process maintenance gate using OS-owned locks, not expiring timestamps.

Active operations hold one slot. Maintenance holds the gate and every slot;
if a writer is active, maintenance fails without making changes. A crashed
process releases its OS locks. Third-party writers must also use this protocol.
"""

import threading
import time
from contextlib import ExitStack, contextmanager
from functools import wraps
from pathlib import Path

from .locking import ResourceBusy, exclusive

SLOTS = 64
_local = threading.local()


@contextmanager
def active(root, *, timeout=2):
    """Register a live operation, with thread-local reentrancy per workspace."""
    root = Path(root).resolve()
    active_roots = getattr(_local, 'roots', None)
    if active_roots is None:
        active_roots = _local.roots = {}
    if root in active_roots:
        active_roots[root] += 1
        try:
            yield
        finally:
            active_roots[root] -= 1
        return
    deadline = time.monotonic() + max(0, timeout)
    slot = ExitStack()
    while True:
        acquired = False
        try:
            with exclusive(root, 'activity_gate'):
                for index in range(SLOTS):
                    try:
                        slot.enter_context(exclusive(root, f'activity_{index}'))
                    except ResourceBusy:
                        continue
                    acquired = True
                    break
        except ResourceBusy:
            pass
        if acquired:
            break
        if time.monotonic() >= deadline:
            slot.close()
            raise ResourceBusy('maintenance_or_activity_capacity_busy')
        time.sleep(min(.05, max(0, deadline - time.monotonic())))
    active_roots[root] = 1
    try:
        yield
    finally:
        del active_roots[root]
        slot.close()


@contextmanager
def maintenance(root):
    """Reject maintenance while any registered writer is alive; prevent new ones."""
    root = Path(root).resolve()
    with ExitStack() as stack:
        stack.enter_context(exclusive(root, 'activity_gate'))
        for index in range(SLOTS):
            stack.enter_context(exclusive(root, f'activity_{index}'))
        yield


def tracked(root_for, *, timeout=2):
    """Wrap an object method without holding a SQLite transaction while waiting."""
    def decorate(function):
        @wraps(function)
        def wrapped(self, *args, **kwargs):
            with active(root_for(self), timeout=timeout):
                return function(self, *args, **kwargs)
        return wrapped
    return decorate
