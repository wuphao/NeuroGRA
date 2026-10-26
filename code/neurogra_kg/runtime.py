"""Local resource serialization and explicit per-run budgets."""

import threading
import time
from pathlib import Path
from dataclasses import dataclass, field


# LLM and embedding adapters share this lock in each SDK process.
MODEL_LOCK = threading.RLock()


def implementation_binding(groups):
    """Bind recoverable jobs to executable algorithms, independently of installation path."""
    from .utils import checksum, identity
    root = Path(__file__).resolve().parent
    paths = {root / name for name in ["api.py", "catalogs.py", "terminology.py", "config.py", "runtime.py", "utils.py"]}
    paths.update((root.parent / "neurogra_common").glob("*.py"))
    for group in ["contracts", *groups]:
        paths.update((root / group).glob("*.py"))
    return identity("implementation", {str(path.relative_to(root.parent)).replace("\\", "/"): checksum(path)
        for path in sorted(paths) if path.is_file()})


@dataclass
class Budget:
    max_seconds: float
    max_llm_calls: int = 100
    max_embedding_calls: int = 100
    started: float = field(default_factory=time.monotonic)
    llm_calls: int = 0
    embedding_calls: int = 0
    on_reserve: object = None

    def remaining(self):
        value = self.max_seconds - (time.monotonic() - self.started)
        if value <= 0:
            raise TimeoutError("kg_run_budget_exhausted")
        return value

    def reserve(self, kind):
        self.remaining()
        attr = kind + "_calls"
        if getattr(self, attr) >= getattr(self, "max_" + attr):
            raise RuntimeError("kg_" + kind + "_call_budget_exhausted")
        setattr(self, attr, getattr(self, attr) + 1)
        if self.on_reserve:
            self.on_reserve(self)
