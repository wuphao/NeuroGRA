"""PDF snapshot registration."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Literal

from pydantic import Field

from neurogra.knowledge.config import BuildConfig
from neurogra.knowledge.schemas.common import SchemaModel, SourceRole, StrictBaseModel
from neurogra.knowledge.schemas.source import Document
from neurogra.knowledge.storage.sqlite import Repository
from neurogra.knowledge.utils import sha256_file


class RegistrationMetadata(StrictBaseModel):
    source_role: SourceRole
    title: str | None = None
    language: str | None = None
    publication_date: str | None = None
    source_version: str | None = None
    doi: str | None = None
    organization: str | None = None
    metadata_status: Literal["pending", "verified"] = "pending"
    source_family_id: str | None = None
    supersedes_document_id: str | None = None


class RegistrationResult(SchemaModel):
    document: Document
    is_duplicate: bool
    snapshot_written: bool
    issues: list[str] = Field(default_factory=list)


def register_pdf(
    path: str | Path,
    metadata: RegistrationMetadata,
    config: BuildConfig,
    repository: Repository,
) -> RegistrationResult:
    """Register one explicit PDF path and store an immutable snapshot."""

    source_path = Path(path)
    if not source_path.exists():
        raise ValueError(f"PDF not found: {source_path}")
    if not source_path.is_file():
        raise ValueError(f"PDF path is not a file: {source_path}")
    _validate_pdf_header(source_path)

    checksum = sha256_file(source_path)
    document_id = f"doc_{checksum}"
    snapshot_dir = config.resolve_path(config.paths.data_root) / "sources" / document_id
    snapshot_path = snapshot_dir / "source.pdf"
    existing = repository.get_document_by_checksum(checksum)

    snapshot_written = False
    if existing is None:
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_path, snapshot_path)
        snapshot_written = True
        document = Document(
            document_id=document_id,
            checksum=checksum,
            source_path=str(source_path),
            snapshot_path=str(snapshot_path),
            **metadata.model_dump(),
        )
        repository.upsert_document(document)
        repository.record_document_event(document_id, "registered", document.model_dump(mode="json"))
        return RegistrationResult(
            document=document,
            is_duplicate=False,
            snapshot_written=snapshot_written,
        )

    updated = existing.model_copy(
        update={
            "source_path": str(source_path),
            "title": metadata.title,
            "source_role": metadata.source_role,
            "language": metadata.language,
            "publication_date": metadata.publication_date,
            "source_version": metadata.source_version,
            "doi": metadata.doi,
            "organization": metadata.organization,
            "metadata_status": metadata.metadata_status,
            "source_family_id": metadata.source_family_id,
            "supersedes_document_id": metadata.supersedes_document_id,
        }
    )
    repository.upsert_document(updated)
    repository.record_document_event(document_id, "metadata_updated", updated.model_dump(mode="json"))
    return RegistrationResult(
        document=updated,
        is_duplicate=True,
        snapshot_written=False,
        issues=["duplicate_checksum_existing_snapshot_reused"],
    )


def _validate_pdf_header(path: Path) -> None:
    with path.open("rb") as handle:
        header = handle.read(5)
    if header != b"%PDF-":
        raise ValueError(f"File does not start with a PDF header: {path}")
