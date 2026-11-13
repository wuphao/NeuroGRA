"""Local chat storage and the scoped Hermes memory adapter."""

import importlib
import os
import sys
import threading
from contextlib import contextmanager
from pathlib import Path

from .chat_database import ChatDatabase
from .patient_workspace import WorkspaceError


def evochat_root():
    return Path(os.environ.get('NEUROGRA_EVOCHAT_ROOT') or Path.home() / 'Desktop' / 'EvoChat').resolve()


class EvoChatStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.lock = threading.Lock()
        self.db = None
        self.memory_module = None

    def database(self):
        with self.lock:
            if self.db is None:
                self.db = ChatDatabase(self.root)
            return self.db

    def load_memory(self):
        with self.lock:
            if self.memory_module is not None:
                return
            hermes = evochat_root() / 'vendor' / 'hermes-agent'
            if not (hermes / 'tools' / 'memory_tool.py').is_file():
                raise WorkspaceError('找不到 EvoChat 记忆模块，请配置 NEUROGRA_EVOCHAT_ROOT。', 503)
            if str(hermes) not in sys.path:
                sys.path.append(str(hermes))
            try:
                memory = importlib.import_module('tools.memory_tool')
                constants = importlib.import_module('hermes_constants')
            except ImportError:
                raise WorkspaceError('无法载入 EvoChat 记忆模块。', 503) from None
            if not Path(memory.__file__).resolve().is_relative_to(hermes):
                raise WorkspaceError('载入的 Hermes 记忆模块与配置的源码目录不匹配。', 503)
            self.hermes_constants = constants
            self.memory_module = memory

    @contextmanager
    def memory(self, scope):
        self.load_memory()
        home = (self.root / 'data/platform/evochat' / scope).resolve()
        if not (home / 'memories').resolve().is_relative_to(self.root):
            raise WorkspaceError('记忆目录超出工作空间。', 503)
        token = self.hermes_constants.set_hermes_home_override(home)
        try:
            store = self.memory_module.MemoryStore()
            store.load_from_disk()
            yield store
        finally:
            self.hermes_constants.reset_hermes_home_override(token)

    def transaction(self):
        return self.database().transaction()
