"""SQLite repository and migrations for authoritative records."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable

from neurogra.knowledge.config import BuildConfig
from neurogra.knowledge.extraction.linker import NormalizationResult
from neurogra.knowledge.parsing.base import ParsedDocument
from neurogra.knowledge.schemas.graph import CandidateClause, Entity
from neurogra.knowledge.schemas.lifecycle import ReleaseManifest, ReviewDecision
from neurogra.knowledge.schemas.source import Document, ParsedBlock, SourceSpan
from neurogra.knowledge.schemas.text import Chunk, Citation
from neurogra.knowledge.utils import model_to_json

SCHEMA_VERSION = 2


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
    ),
    2: (
        """
        CREATE TABLE IF NOT EXISTS document_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
            event_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_document_events_document ON document_events(document_id)",
    ),
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

    def get_document_by_checksum(self, checksum: str) -> Document | None:
        row = self.connection.execute(
            "SELECT payload_json FROM documents WHERE checksum = ?",
            (checksum,),
        ).fetchone()
        if row is None:
            return None
        return Document.model_validate_json(str(row["payload_json"]))

    def get_document(self, document_id: str) -> Document:
        row = self.connection.execute(
            "SELECT payload_json FROM documents WHERE document_id = ?",
            (document_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Unknown document_id: {document_id}")
        return Document.model_validate_json(str(row["payload_json"]))

    def upsert_document(self, document: Document) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO documents (
                    document_id, checksum, source_path, snapshot_path, title,
                    source_role, language, metadata_status, payload_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(document_id) DO UPDATE SET
                    source_path = excluded.source_path,
                    title = excluded.title,
                    source_role = excluded.source_role,
                    language = excluded.language,
                    metadata_status = excluded.metadata_status,
                    payload_json = excluded.payload_json
                """,
                (
                    document.document_id,
                    document.checksum,
                    document.source_path,
                    document.snapshot_path,
                    document.title,
                    document.source_role.value,
                    document.language,
                    document.metadata_status,
                    model_to_json(document),
                    document.created_at.isoformat(),
                ),
            )

    def record_document_event(self, document_id: str, event_type: str, payload: object) -> None:
        import json

        with self.connection:
            self.connection.execute(
                """
                INSERT INTO document_events(document_id, event_type, payload_json)
                VALUES (?, ?, ?)
                """,
                (
                    document_id,
                    event_type,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str),
                ),
            )

    def save_parsed_document(self, parsed_document: ParsedDocument) -> None:
        with self.connection:
            self._delete_parse_derived_data(parsed_document.parse_id)
            self.connection.execute(
                """
                INSERT OR REPLACE INTO parses (
                    parse_id, document_id, parser_name, parser_version, config_hash,
                    payload_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    parsed_document.parse_id,
                    parsed_document.document_id,
                    parsed_document.parser_name,
                    parsed_document.parser_version,
                    parsed_document.config_hash,
                    model_to_json(parsed_document),
                ),
            )
            self.connection.execute(
                "DELETE FROM blocks WHERE parse_id = ?",
                (parsed_document.parse_id,),
            )
            for block in parsed_document.blocks:
                self.connection.execute(
                    """
                    INSERT INTO blocks (
                        block_id, parse_id, document_id, page_no, reading_order,
                        block_type, payload_json, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        block.block_id,
                        block.parse_id,
                        block.document_id,
                        block.page_no,
                        block.reading_order,
                        block.block_type,
                        model_to_json(block),
                        block.created_at.isoformat(),
                    ),
                )

    def _delete_parse_derived_data(self, parse_id: str) -> None:
        """Remove stale derived rows before rewriting a deterministic parse.

        Parse IDs are deterministic for a document/config pair. Re-running the
        same PDF therefore rewrites the same parse and block IDs. Downstream
        rows such as spans, chunks, candidates, clause revisions, and evidence
        rows still point at the old block/span records, so they must be removed
        before the block table is rewritten.
        """

        span_rows = self.connection.execute(
            "SELECT span_id FROM spans WHERE parse_id = ?",
            (parse_id,),
        ).fetchall()
        span_ids = [str(row["span_id"]) for row in span_rows]
        chunk_ids: list[str] = []
        if span_ids:
            placeholders = ",".join("?" for _ in span_ids)
            chunk_rows = self.connection.execute(
                f"""
                SELECT DISTINCT chunk_id
                FROM chunk_spans
                WHERE span_id IN ({placeholders})
                """,
                span_ids,
            ).fetchall()
            chunk_ids = [str(row["chunk_id"]) for row in chunk_rows]

        candidate_ids: list[str] = []
        if chunk_ids:
            placeholders = ",".join("?" for _ in chunk_ids)
            candidate_rows = self.connection.execute(
                f"""
                SELECT candidate_id
                FROM candidates
                WHERE chunk_id IN ({placeholders})
                """,
                chunk_ids,
            ).fetchall()
            candidate_ids = [str(row["candidate_id"]) for row in candidate_rows]

        revision_ids: list[str] = []
        clause_ids: list[str] = []
        if candidate_ids:
            placeholders = ",".join("?" for _ in candidate_ids)
            revision_rows = self.connection.execute(
                f"""
                SELECT revision_id, clause_id
                FROM clause_revisions
                WHERE candidate_id IN ({placeholders})
                """,
                candidate_ids,
            ).fetchall()
            revision_ids = [str(row["revision_id"]) for row in revision_rows]
            clause_ids = sorted({str(row["clause_id"]) for row in revision_rows})

        if revision_ids:
            placeholders = ",".join("?" for _ in revision_ids)
            self.connection.execute(
                f"DELETE FROM clause_evidence WHERE revision_id IN ({placeholders})",
                revision_ids,
            )
            self.connection.execute(
                f"DELETE FROM clause_entities WHERE revision_id IN ({placeholders})",
                revision_ids,
            )
            self.connection.execute(
                f"DELETE FROM clause_revisions WHERE revision_id IN ({placeholders})",
                revision_ids,
            )

        if clause_ids:
            placeholders = ",".join("?" for _ in clause_ids)
            self.connection.execute(
                f"DELETE FROM clauses WHERE clause_id IN ({placeholders})",
                clause_ids,
            )

        if candidate_ids:
            placeholders = ",".join("?" for _ in candidate_ids)
            self.connection.execute(
                f"DELETE FROM candidates WHERE candidate_id IN ({placeholders})",
                candidate_ids,
            )

        if chunk_ids:
            placeholders = ",".join("?" for _ in chunk_ids)
            self.connection.execute(
                f"DELETE FROM chunk_spans WHERE chunk_id IN ({placeholders})",
                chunk_ids,
            )
            self.connection.execute(
                f"""
                DELETE FROM chunks
                WHERE chunk_id IN ({placeholders})
                  AND parent_chunk_id IS NOT NULL
                """,
                chunk_ids,
            )
            self.connection.execute(
                f"DELETE FROM chunks WHERE chunk_id IN ({placeholders})",
                chunk_ids,
            )

        if span_ids:
            placeholders = ",".join("?" for _ in span_ids)
            self.connection.execute(
                f"DELETE FROM span_locators WHERE span_id IN ({placeholders})",
                span_ids,
            )
            self.connection.execute(
                f"DELETE FROM spans WHERE span_id IN ({placeholders})",
                span_ids,
            )

        self.connection.execute(
            "DELETE FROM blocks WHERE parse_id = ?",
            (parse_id,),
        )

    def get_parsed_document(self, parse_id: str) -> ParsedDocument:
        row = self.connection.execute(
            "SELECT payload_json FROM parses WHERE parse_id = ?",
            (parse_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Unknown parse_id: {parse_id}")
        parsed = ParsedDocument.model_validate_json(str(row["payload_json"]))
        block_rows = self.connection.execute(
            "SELECT payload_json FROM blocks WHERE parse_id = ? ORDER BY page_no, reading_order",
            (parse_id,),
        ).fetchall()
        blocks = [ParsedBlock.model_validate_json(str(item["payload_json"])) for item in block_rows]
        return parsed.model_copy(update={"blocks": blocks})

    def save_spans(self, spans: list[SourceSpan]) -> None:
        with self.connection:
            for span in spans:
                self.connection.execute(
                    """
                    INSERT OR REPLACE INTO spans (
                        span_id, parse_id, document_id, payload_json, created_at
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        span.span_id,
                        span.parse_id,
                        span.document_id,
                        model_to_json(span),
                        span.created_at.isoformat(),
                    ),
                )
                self.connection.execute(
                    "DELETE FROM span_locators WHERE span_id = ?",
                    (span.span_id,),
                )
                for ordinal, locator in enumerate(span.locators):
                    self.connection.execute(
                        """
                        INSERT INTO span_locators(span_id, ordinal, block_id, start, end)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            span.span_id,
                            ordinal,
                            locator.block_id,
                            locator.start,
                            locator.end,
                        ),
                    )

    def get_spans_for_parse(self, parse_id: str) -> list[SourceSpan]:
        rows = self.connection.execute(
            """
            SELECT s.payload_json
            FROM spans s
            LEFT JOIN span_locators sl ON sl.span_id = s.span_id AND sl.ordinal = 0
            LEFT JOIN blocks b ON b.block_id = sl.block_id
            WHERE s.parse_id = ?
            ORDER BY b.page_no, b.reading_order, s.span_id
            """,
            (parse_id,),
        ).fetchall()
        return [SourceSpan.model_validate_json(str(row["payload_json"])) for row in rows]

    def save_chunks(self, chunks: list[Chunk]) -> None:
        with self.connection:
            for chunk in chunks:
                self.connection.execute(
                    """
                    INSERT OR REPLACE INTO chunks (
                        chunk_id, chunk_build_id, document_id, parent_chunk_id, role,
                        text_review_status, payload_json, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chunk.chunk_id,
                        chunk.chunk_build_id,
                        chunk.document_id,
                        chunk.parent_chunk_id,
                        chunk.role,
                        chunk.text_review_status,
                        model_to_json(chunk),
                        chunk.created_at.isoformat(),
                    ),
                )
                self.connection.execute(
                    "DELETE FROM chunk_spans WHERE chunk_id = ?",
                    (chunk.chunk_id,),
                )
                for ordinal, span_id in enumerate(chunk.span_ids):
                    self.connection.execute(
                        """
                        INSERT INTO chunk_spans(chunk_id, span_id, ordinal)
                        VALUES (?, ?, ?)
                        """,
                        (chunk.chunk_id, span_id, ordinal),
                    )

    def get_chunks_for_build(self, chunk_build_id: str) -> list[Chunk]:
        rows = self.connection.execute(
            """
            SELECT payload_json FROM chunks
            WHERE chunk_build_id = ?
            ORDER BY role DESC, chunk_id
            """,
            (chunk_build_id,),
        ).fetchall()
        return [Chunk.model_validate_json(str(row["payload_json"])) for row in rows]

    def save_candidates(self, candidates: list[CandidateClause]) -> None:
        with self.connection:
            for candidate in candidates:
                self.connection.execute(
                    """
                    INSERT OR REPLACE INTO candidates (
                        candidate_id, extraction_run_id, chunk_id, payload_json, created_at
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        candidate.candidate_id,
                        candidate.extraction_run_id,
                        candidate.chunk_id,
                        model_to_json(candidate),
                        candidate.created_at.isoformat(),
                    ),
                )

    def get_candidates_for_run(self, extraction_run_id: str) -> list[CandidateClause]:
        rows = self.connection.execute(
            "SELECT payload_json FROM candidates WHERE extraction_run_id = ? ORDER BY candidate_id",
            (extraction_run_id,),
        ).fetchall()
        return [CandidateClause.model_validate_json(str(row["payload_json"])) for row in rows]

    def get_candidates_for_chunk_build(self, chunk_build_id: str) -> list[CandidateClause]:
        rows = self.connection.execute(
            """
            SELECT c.payload_json
            FROM candidates c
            JOIN chunks ch ON ch.chunk_id = c.chunk_id
            WHERE ch.chunk_build_id = ?
            ORDER BY c.candidate_id
            """,
            (chunk_build_id,),
        ).fetchall()
        return [CandidateClause.model_validate_json(str(row["payload_json"])) for row in rows]

    def chunk_ids_for_candidates(self, candidate_ids: list[str]) -> dict[str, str]:
        if not candidate_ids:
            return {}
        unique_ids = sorted(set(candidate_ids))
        placeholders = ",".join("?" for _ in unique_ids)
        rows = self.connection.execute(
            f"""
            SELECT candidate_id, chunk_id
            FROM candidates
            WHERE candidate_id IN ({placeholders})
            """,
            unique_ids,
        ).fetchall()
        return {str(row["candidate_id"]): str(row["chunk_id"]) for row in rows if row["chunk_id"] is not None}

    def save_normalization_result(self, result: NormalizationResult) -> None:
        with self.connection:
            for entity in result.entities:
                self.connection.execute(
                    """
                    INSERT OR REPLACE INTO entities (
                        entity_id, canonical_name, entity_type, terminology_version,
                        status, payload_json, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entity.entity_id,
                        entity.canonical_name,
                        entity.entity_type,
                        entity.terminology_version,
                        entity.status,
                        model_to_json(entity),
                        entity.created_at.isoformat(),
                    ),
                )
            for mention in result.mentions:
                self.connection.execute(
                    """
                    INSERT OR REPLACE INTO mentions (
                        mention_id, selected_entity_id, link_status, payload_json, created_at
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        mention.mention_id,
                        mention.selected_entity_id,
                        mention.link_status,
                        model_to_json(mention),
                        mention.created_at.isoformat(),
                    ),
                )
            for revision in result.clause_revisions:
                self.connection.execute(
                    """
                    INSERT OR IGNORE INTO clauses(clause_id, candidate_id, created_at)
                    VALUES (?, ?, ?)
                    """,
                    (revision.clause_id, revision.candidate_id, revision.created_at.isoformat()),
                )
                self.connection.execute(
                    """
                    INSERT OR REPLACE INTO clause_revisions (
                        revision_id, clause_id, candidate_id, subject_entity_id,
                        object_entity_id, predicate, review_status, payload_json, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        revision.revision_id,
                        revision.clause_id,
                        revision.candidate_id,
                        revision.subject_entity_id,
                        revision.object_entity_id,
                        revision.predicate,
                        revision.review_status,
                        model_to_json(revision),
                        revision.created_at.isoformat(),
                    ),
                )
                self.connection.execute(
                    "DELETE FROM clause_evidence WHERE revision_id = ?",
                    (revision.revision_id,),
                )
                for ordinal, evidence in enumerate(revision.evidence_refs):
                    self.connection.execute(
                        """
                        INSERT INTO clause_evidence(revision_id, ordinal, span_id, start, end, quote)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            revision.revision_id,
                            ordinal,
                            evidence.span_id,
                            evidence.start,
                            evidence.end,
                            evidence.quote,
                        ),
                    )
                self.connection.execute(
                    "DELETE FROM clause_entities WHERE revision_id = ?",
                    (revision.revision_id,),
                )
                for role, entity_id in (
                    ("subject", revision.subject_entity_id),
                    ("object", revision.object_entity_id),
                ):
                    self.connection.execute(
                        """
                        INSERT INTO clause_entities(revision_id, role, entity_id)
                        VALUES (?, ?, ?)
                        """,
                        (revision.revision_id, role, entity_id),
                    )

    def get_span(self, span_id: str) -> SourceSpan | None:
        row = self.connection.execute(
            "SELECT payload_json FROM spans WHERE span_id = ?",
            (span_id,),
        ).fetchone()
        if row is None:
            return None
        return SourceSpan.model_validate_json(str(row["payload_json"]))

    def entity_exists(self, entity_id: str) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM entities WHERE entity_id = ?",
            (entity_id,),
        ).fetchone()
        return row is not None

    def entity_type(self, entity_id: str) -> str | None:
        row = self.connection.execute(
            "SELECT entity_type FROM entities WHERE entity_id = ?",
            (entity_id,),
        ).fetchone()
        return None if row is None else str(row["entity_type"])

    def get_entities(self, entity_ids: list[str]) -> list[Entity]:
        if not entity_ids:
            return []
        unique_ids = sorted(set(entity_ids))
        placeholders = ",".join("?" for _ in unique_ids)
        rows = self.connection.execute(
            f"""
            SELECT payload_json
            FROM entities
            WHERE entity_id IN ({placeholders})
            ORDER BY entity_id
            """,
            unique_ids,
        ).fetchall()
        return [Entity.model_validate_json(str(row["payload_json"])) for row in rows]

    def get_clause_revision(self, revision_id: str) -> "ClauseRevision":
        from neurogra.knowledge.schemas.graph import ClauseRevision

        row = self.connection.execute(
            "SELECT payload_json FROM clause_revisions WHERE revision_id = ?",
            (revision_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Unknown revision_id: {revision_id}")
        return ClauseRevision.model_validate_json(str(row["payload_json"]))

    def update_clause_revision(self, revision: "ClauseRevision") -> None:
        with self.connection:
            self.connection.execute(
                """
                UPDATE clause_revisions
                SET subject_entity_id = ?, object_entity_id = ?, predicate = ?,
                    review_status = ?, payload_json = ?
                WHERE revision_id = ?
                """,
                (
                    revision.subject_entity_id,
                    revision.object_entity_id,
                    revision.predicate,
                    revision.review_status,
                    model_to_json(revision),
                    revision.revision_id,
                ),
            )

    def get_clause_revisions_for_chunk_build(self, chunk_build_id: str) -> list["ClauseRevision"]:
        from neurogra.knowledge.schemas.graph import ClauseRevision

        rows = self.connection.execute(
            """
            SELECT DISTINCT cr.payload_json
            FROM clause_revisions cr
            JOIN candidates c ON c.candidate_id = cr.candidate_id
            JOIN chunks ch ON ch.chunk_id = c.chunk_id
            WHERE ch.chunk_build_id = ?
            ORDER BY cr.revision_id
            """,
            (chunk_build_id,),
        ).fetchall()
        return [ClauseRevision.model_validate_json(str(row["payload_json"])) for row in rows]

    def update_chunk_review_status(self, chunk_id: str, status: str) -> None:
        chunk_row = self.connection.execute(
            "SELECT payload_json FROM chunks WHERE chunk_id = ?",
            (chunk_id,),
        ).fetchone()
        if chunk_row is None:
            raise ValueError(f"Unknown chunk_id: {chunk_id}")
        chunk = Chunk.model_validate_json(str(chunk_row["payload_json"])).model_copy(
            update={"text_review_status": status}
        )
        with self.connection:
            self.connection.execute(
                "UPDATE chunks SET text_review_status = ?, payload_json = ? WHERE chunk_id = ?",
                (status, model_to_json(chunk), chunk_id),
            )

    def update_clause_review_status(self, revision_id: str, status: str) -> None:
        revision = self.get_clause_revision(revision_id).model_copy(update={"review_status": status})
        self.update_clause_revision(revision)

    def save_review_decision(self, decision: ReviewDecision) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT OR REPLACE INTO reviews (
                    decision_id, object_type, object_id, action, reviewer_id,
                    payload_json, decided_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision.decision_id,
                    decision.object_type,
                    decision.object_id,
                    decision.action,
                    decision.reviewer_id,
                    model_to_json(decision),
                    decision.decided_at.isoformat(),
                ),
            )

    def save_release(self, manifest: ReleaseManifest, status: str) -> None:
        existing = self.connection.execute(
            "SELECT status FROM releases WHERE release_id = ?",
            (manifest.release_id,),
        ).fetchone()
        final_status = "active" if existing is not None and existing["status"] == "active" else status
        with self.connection:
            self.connection.execute(
                """
                INSERT OR REPLACE INTO releases(release_id, status, manifest_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    manifest.release_id,
                    final_status,
                    model_to_json(manifest),
                    manifest.created_at.isoformat(),
                ),
            )
            self.connection.execute(
                "DELETE FROM release_members WHERE release_id = ?",
                (manifest.release_id,),
            )
            for ordinal, chunk_id in enumerate(manifest.chunk_ids):
                self.connection.execute(
                    """
                    INSERT INTO release_members(release_id, object_type, object_id, ordinal)
                    VALUES (?, 'chunk', ?, ?)
                    """,
                    (manifest.release_id, chunk_id, ordinal),
                )
            for ordinal, revision_id in enumerate(manifest.clause_revision_ids):
                self.connection.execute(
                    """
                    INSERT INTO release_members(release_id, object_type, object_id, ordinal)
                    VALUES (?, 'clause_revision', ?, ?)
                    """,
                    (manifest.release_id, revision_id, ordinal),
                )

    def get_release_manifest(self, release_id: str) -> ReleaseManifest:
        row = self.connection.execute(
            "SELECT manifest_json FROM releases WHERE release_id = ?",
            (release_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Unknown release_id: {release_id}")
        return ReleaseManifest.model_validate_json(str(row["manifest_json"]))

    def release_status(self, release_id: str) -> str:
        row = self.connection.execute(
            "SELECT status FROM releases WHERE release_id = ?",
            (release_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Unknown release_id: {release_id}")
        return str(row["status"])

    def activate_release(self, release_id: str) -> None:
        status = self.release_status(release_id)
        # Re-running an idempotent build can encounter the same release that
        # was already activated by a previous run.
        if status == "active":
            return
        if status != "validated":
            raise ValueError(f"Release is not validated: {release_id}")
        with self.connection:
            self.connection.execute(
                "UPDATE releases SET status = 'retired' WHERE status = 'active'"
            )
            self.connection.execute(
                "UPDATE releases SET status = 'active' WHERE release_id = ?",
                (release_id,),
            )
            self.connection.execute(
                """
                INSERT INTO active_release(singleton_id, release_id)
                VALUES (1, ?)
                ON CONFLICT(singleton_id) DO UPDATE SET
                    release_id = excluded.release_id,
                    activated_at = CURRENT_TIMESTAMP
                """,
                (release_id,),
            )

    def active_release_id(self) -> str:
        row = self.connection.execute(
            "SELECT release_id FROM active_release WHERE singleton_id = 1",
        ).fetchone()
        if row is None:
            raise ValueError("No active release")
        return str(row["release_id"])

    def parse_ids_for_documents(self, document_ids: list[str]) -> list[str]:
        if not document_ids:
            return []
        placeholders = ",".join("?" for _ in document_ids)
        rows = self.connection.execute(
            f"SELECT parse_id FROM parses WHERE document_id IN ({placeholders}) ORDER BY parse_id",
            document_ids,
        ).fetchall()
        return [str(row["parse_id"]) for row in rows]

    def parser_versions_for_documents(self, document_ids: list[str]) -> dict[str, str]:
        if not document_ids:
            return {}
        placeholders = ",".join("?" for _ in document_ids)
        rows = self.connection.execute(
            f"""
            SELECT parser_name, parser_version
            FROM parses
            WHERE document_id IN ({placeholders})
            ORDER BY parse_id
            """,
            document_ids,
        ).fetchall()
        return {str(row["parser_name"]): str(row["parser_version"]) for row in rows}

    def citations_for_chunk(self, chunk: Chunk) -> list[Citation]:
        citations: list[Citation] = []
        for span_id in chunk.span_ids:
            row = self.connection.execute(
                """
                SELECT
                    d.payload_json AS document_json,
                    b.payload_json AS block_json,
                    s.payload_json AS span_json
                FROM spans s
                JOIN documents d ON d.document_id = s.document_id
                LEFT JOIN span_locators sl ON sl.span_id = s.span_id AND sl.ordinal = 0
                LEFT JOIN blocks b ON b.block_id = sl.block_id
                WHERE s.span_id = ?
                """,
                (span_id,),
            ).fetchone()
            if row is None:
                continue
            document = Document.model_validate_json(str(row["document_json"]))
            span = SourceSpan.model_validate_json(str(row["span_json"]))
            block = (
                ParsedBlock.model_validate_json(str(row["block_json"]))
                if row["block_json"] is not None
                else None
            )
            quote = chunk.retrieval_text[:300] if chunk.retrieval_text else span.normalized_text[:300]
            citations.append(
                Citation(
                    document_id=document.document_id,
                    title=document.title,
                    source_role=document.source_role,
                    parse_id=span.parse_id,
                    span_id=span.span_id,
                    page_no=block.page_no if block is not None else 1,
                    printed_page_label=block.printed_page_label if block is not None else None,
                    bbox=block.bbox if block is not None else None,
                    quote=quote,
                    extraction_method=block.extraction_method if block is not None else "native_text",
                )
            )
        return citations

    def span_source_contexts(self, span_ids: list[str]) -> dict[str, dict[str, object]]:
        if not span_ids:
            return {}
        unique_ids = sorted(set(span_ids))
        placeholders = ",".join("?" for _ in unique_ids)
        rows = self.connection.execute(
            f"""
            SELECT
                s.span_id AS span_id,
                d.title AS source_title,
                d.source_path AS source_path,
                d.source_role AS source_role,
                b.block_id AS block_id,
                b.page_no AS page_no,
                b.payload_json AS block_json
            FROM spans s
            JOIN documents d ON d.document_id = s.document_id
            LEFT JOIN span_locators sl ON sl.span_id = s.span_id AND sl.ordinal = 0
            LEFT JOIN blocks b ON b.block_id = sl.block_id
            WHERE s.span_id IN ({placeholders})
            """,
            unique_ids,
        ).fetchall()
        contexts: dict[str, dict[str, object]] = {}
        for row in rows:
            block = (
                ParsedBlock.model_validate_json(str(row["block_json"]))
                if row["block_json"] is not None
                else None
            )
            contexts[str(row["span_id"])] = {
                "source_title": row["source_title"],
                "source_path": row["source_path"],
                "source_role": row["source_role"],
                "block_id": row["block_id"],
                "page_no": row["page_no"],
                "printed_page_label": None if block is None else block.printed_page_label,
                "bbox": None if block is None else block.bbox,
            }
        return contexts


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
