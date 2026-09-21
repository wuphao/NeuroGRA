"""SQLite repository and migrations for authoritative records."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable

from neurogra.knowledge.config import BuildConfig

SCHEMA_VERSION = 1


MIGRATIONS: dict[int, Iterable[str]] = {
    1: (
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS documents (
            document_id TEXT PRIMARY KEY,
            checksum TEXT NOT NULL UNIQUE,
            source_path TEXT NOT NULL,
            snapshot_path TEXT NOT NULL,
            title TEXT,
            source_role TEXT NOT NULL,
            language TEXT,
            metadata_status TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS parses (
            parse_id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
            parser_name TEXT NOT NULL,
            parser_version TEXT NOT NULL,
            config_hash TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS blocks (
            block_id TEXT PRIMARY KEY,
            parse_id TEXT NOT NULL REFERENCES parses(parse_id) ON DELETE CASCADE,
            document_id TEXT NOT NULL,
            page_no INTEGER NOT NULL,
            reading_order INTEGER NOT NULL,
            block_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS spans (
            span_id TEXT PRIMARY KEY,
            parse_id TEXT NOT NULL REFERENCES parses(parse_id) ON DELETE CASCADE,
            document_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS span_locators (
            span_id TEXT NOT NULL REFERENCES spans(span_id) ON DELETE CASCADE,
            ordinal INTEGER NOT NULL,
            block_id TEXT NOT NULL REFERENCES blocks(block_id) ON DELETE CASCADE,
            start INTEGER NOT NULL,
            end INTEGER NOT NULL,
            PRIMARY KEY (span_id, ordinal)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS chunks (
            chunk_id TEXT PRIMARY KEY,
            chunk_build_id TEXT NOT NULL,
            document_id TEXT NOT NULL,
            parent_chunk_id TEXT REFERENCES chunks(chunk_id),
            role TEXT NOT NULL,
            text_review_status TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS chunk_spans (
            chunk_id TEXT NOT NULL REFERENCES chunks(chunk_id) ON DELETE CASCADE,
            span_id TEXT NOT NULL REFERENCES spans(span_id),
            ordinal INTEGER NOT NULL,
            PRIMARY KEY (chunk_id, ordinal)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS entities (
            entity_id TEXT PRIMARY KEY,
            canonical_name TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            terminology_version TEXT NOT NULL,
            status TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS mentions (
            mention_id TEXT PRIMARY KEY,
            selected_entity_id TEXT REFERENCES entities(entity_id),
            link_status TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS candidates (
            candidate_id TEXT PRIMARY KEY,
            extraction_run_id TEXT NOT NULL,
            chunk_id TEXT,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS clauses (
            clause_id TEXT PRIMARY KEY,
            candidate_id TEXT REFERENCES candidates(candidate_id),
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS clause_revisions (
            revision_id TEXT PRIMARY KEY,
            clause_id TEXT NOT NULL REFERENCES clauses(clause_id) ON DELETE CASCADE,
            candidate_id TEXT NOT NULL,
            subject_entity_id TEXT,
            object_entity_id TEXT,
            predicate TEXT NOT NULL,
            review_status TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS clause_evidence (
            revision_id TEXT NOT NULL REFERENCES clause_revisions(revision_id) ON DELETE CASCADE,
            ordinal INTEGER NOT NULL,
            span_id TEXT NOT NULL REFERENCES spans(span_id),
            start INTEGER NOT NULL,
            end INTEGER NOT NULL,
            quote TEXT NOT NULL,
            PRIMARY KEY (revision_id, ordinal)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS clause_entities (
            revision_id TEXT NOT NULL REFERENCES clause_revisions(revision_id) ON DELETE CASCADE,
            role TEXT NOT NULL,
            entity_id TEXT NOT NULL REFERENCES entities(entity_id),
            PRIMARY KEY (revision_id, role, entity_id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS reviews (
            decision_id TEXT PRIMARY KEY,
            object_type TEXT NOT NULL,
            object_id TEXT NOT NULL,
            action TEXT NOT NULL,
            reviewer_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            decided_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS tasks (
            task_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            stage TEXT NOT NULL,
            cache_key TEXT NOT NULL,
            status TEXT NOT NULL,
            attempt INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS artifacts (
            artifact_id TEXT PRIMARY KEY,
            task_id TEXT REFERENCES tasks(task_id) ON DELETE CASCADE,
            path TEXT NOT NULL,
            checksum TEXT,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS releases (
            release_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            manifest_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS release_members (
            release_id TEXT NOT NULL REFERENCES releases(release_id) ON DELETE CASCADE,
            object_type TEXT NOT NULL,
            object_id TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            PRIMARY KEY (release_id, object_type, object_id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS active_release (
            singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
            release_id TEXT NOT NULL REFERENCES releases(release_id),
            activated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_blocks_parse_page ON blocks(parse_id, page_no)",
        "CREATE INDEX IF NOT EXISTS idx_spans_document ON spans(document_id)",
        "CREATE INDEX IF NOT EXISTS idx_chunks_document_role ON chunks(document_id, role)",
        "CREATE INDEX IF NOT EXISTS idx_clause_revisions_status ON clause_revisions(review_status)",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_cache_success ON tasks(cache_key) WHERE status = 'succeeded'",
    )
}


class Repository:
    """Small repository wrapper for SQLite connection lifecycle."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.connection = sqlite3.connect(db_path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "Repository":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def migration_versions(self) -> list[int]:
        rows = self.connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
        return [int(row["version"]) for row in rows]

    def table_names(self) -> list[str]:
        rows = self.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        ).fetchall()
        return [str(row["name"]) for row in rows]


def init_store(config: BuildConfig) -> Repository:
    """Create the SQLite database and apply pending migrations."""

    db_path = config.resolve_path(config.database.path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    repo = Repository(db_path)
    try:
        _apply_migrations(repo.connection)
    except Exception:
        repo.close()
        raise
    return repo


def _apply_migrations(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    applied = {
        int(row["version"])
        for row in connection.execute("SELECT version FROM schema_migrations").fetchall()
    }
    for version in sorted(MIGRATIONS):
        if version in applied:
            continue
        with connection:
            for statement in MIGRATIONS[version]:
                connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_migrations(version) VALUES (?)",
                (version,),
            )
