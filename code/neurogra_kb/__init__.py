"""Independent raw-text knowledge publications, without graph construction."""

from .api import TextKnowledgeService
from .config import KBConfig, load_config

__all__ = ["TextKnowledgeService", "KBConfig", "load_config"]
