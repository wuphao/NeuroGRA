"""OS-owned cross-process locks; files can remain after an abnormal exit."""
import os
import re
from contextlib import contextmanager
from pathlib import Path


class ResourceBusy(RuntimeError):
    pass


@contextmanager
def exclusive(root, name):
    if not re.fullmatch(r'[A-Za-z0-9_-]+', name):
        raise ValueError('invalid_lock_name')
    root = Path(root).resolve()
    directory = (root / 'data/platform/locks').resolve()
    if not directory.is_relative_to(root):
        raise ValueError('lock_directory_outside_workspace')
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / (name + '.lock')).open('a+b') as handle:
        if handle.seek(0, 2) == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ResourceBusy('resource_owned_by_active_process') from None
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
