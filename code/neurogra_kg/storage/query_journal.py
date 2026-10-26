"""Atomic query checkpoints with an OS-owned per-run lease."""
import json
import os
import time
from pathlib import Path

from ..utils import atomic_json, identity, safe_identifier


class QueryJournal:
    def __init__(self, root: Path, run_id: str):
        self.directory = root / safe_identifier(run_id)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "checkpoint.json"
        self.data = {}

    def __enter__(self):
        self.handle = (self.directory / "lease").open("a+b")
        self.handle.seek(0, 2)
        if not self.handle.tell():
            self.handle.write(b"0"); self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.handle.close()
            raise RuntimeError("query_run_already_active") from exc
        if self.path.is_file():
            try:
                self.data = self.read(self.path)
            except Exception:
                self.__exit__(None, None, None)
                raise
        return self

    def __exit__(self, *args):
        if os.name == "nt":
            import msvcrt
            self.handle.seek(0)
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
        self.handle.close()

    @staticmethod
    def read(path):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        expected = data.pop("checksum", None)
        if expected != identity("query_checkpoint", data):
            raise ValueError("query_checkpoint_checksum_mismatch")
        return data

    def save(self, **updates):
        self.data.update(updates)
        self.data["updated_at"] = time.time()
        payload = dict(self.data)
        payload["checksum"] = identity("query_checkpoint", payload)
        atomic_json(self.path, payload)
