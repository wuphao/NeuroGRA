"""SQLite object journal and transactional active-release pointer."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ..utils import canonical


class Registry:
    def __init__(self, path: Path, read_only=False):
        self.read_only = read_only
        self.path = path
        if read_only:
            if not path.is_file():
                raise FileNotFoundError("kg_registry_not_found")
            self.connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=30)
            self.connection.row_factory = sqlite3.Row
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, timeout=30)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS objects (
              kind TEXT NOT NULL, id TEXT NOT NULL, payload TEXT NOT NULL,
              updated_at TEXT NOT NULL, PRIMARY KEY(kind,id));
            CREATE TABLE IF NOT EXISTS releases (
              id TEXT PRIMARY KEY, manifest TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS active_release (
              singleton INTEGER PRIMARY KEY CHECK(singleton=1), id TEXT NOT NULL REFERENCES releases(id));
            CREATE TABLE IF NOT EXISTS revoked_releases (
              id TEXT PRIMARY KEY, reason TEXT NOT NULL);
        """)

    def save(self, kind, id, payload):
        if self.read_only:
            raise ValueError("read_only_registry")
        if hasattr(payload, "model_dump"):
            payload = payload.model_dump(mode="json")
        with self.connection:
            self.connection.execute("INSERT INTO objects VALUES (?,?,?,?) ON CONFLICT(kind,id) "
                "DO UPDATE SET payload=excluded.payload, updated_at=excluded.updated_at",
                (kind, id, canonical(payload), datetime.now(timezone.utc).isoformat()))

    def get(self, kind, id):
        row = self.connection.execute("SELECT payload FROM objects WHERE kind=? AND id=?", (kind, id)).fetchone()
        if row is None:
            raise KeyError(kind + ":" + id)
        return json.loads(row["payload"])

    def publish(self, release_id, manifest):
        value = canonical(manifest)
        with self.connection:
            if self.connection.execute("SELECT 1 FROM revoked_releases WHERE id=?", (release_id,)).fetchone():
                raise ValueError("release_has_been_revoked")
            row = self.connection.execute("SELECT manifest FROM releases WHERE id=?", (release_id,)).fetchone()
            if row is not None and row[0] != value:
                raise ValueError("immutable_release_conflict")
            self.connection.execute("INSERT OR IGNORE INTO releases VALUES (?,?,?)",
                                    (release_id, value, datetime.now(timezone.utc).isoformat()))
            self.connection.execute("INSERT INTO active_release VALUES (1,?) "
                                    "ON CONFLICT(singleton) DO UPDATE SET id=excluded.id", (release_id,))

    def active_release_id(self):
        row = self.connection.execute("SELECT id FROM active_release WHERE singleton=1").fetchone()
        if row is None:
            raise ValueError("no_active_kg_release")
        return row[0]

    def release(self, release_id=None):
        release_id = release_id or self.active_release_id()
        if self.connection.execute("SELECT 1 FROM revoked_releases WHERE id=?", (release_id,)).fetchone():
            raise ValueError("release_has_been_revoked")
        row = self.connection.execute("SELECT manifest FROM releases WHERE id=?", (release_id,)).fetchone()
        if row is None:
            raise KeyError("release:" + release_id)
        return json.loads(row[0])

    def revoke(self, release_id, reason):
        with self.connection:
            self.connection.execute("INSERT INTO revoked_releases VALUES (?,?) ON CONFLICT(id) DO UPDATE SET reason=excluded.reason",
                                    (release_id, reason))
            self.connection.execute("DELETE FROM active_release WHERE id=?", (release_id,))

    def close(self):
        self.connection.close()
