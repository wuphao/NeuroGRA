"""Independent, source-bound conditional medical knowledge service."""

__version__ = "0.1.0"


def __getattr__(name):
    if name == "KnowledgeGraphService":
        from .api import KnowledgeGraphService
        return KnowledgeGraphService
    raise AttributeError(name)
