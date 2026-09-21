"""PDF parser protocol used by the parsing adapters."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from pydantic import Field

from neurogra.knowledge.schemas.common import StrictBaseModel
from neurogra.knowledge.schemas.source import ParsedBlock


class ParsedDocument(StrictBaseModel):
    document_id: str
    parse_id: str
    parser_name: str
    parser_version: str
    blocks: list[ParsedBlock]
    issues: list[str] = Field(default_factory=list)


class PdfParser(Protocol):
    """A parser adapter converts one immutable PDF snapshot into blocks."""

    parser_name: str
    parser_version: str

    def parse(self, snapshot_path: Path, config: object) -> ParsedDocument:
        """Parse a PDF snapshot and return normalized layout blocks."""
