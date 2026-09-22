"""Basic pypdf parser used until the Docling adapter is wired in."""

from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from neurogra.knowledge.config import ParsingConfig
from neurogra.knowledge.parsing.base import ParsedDocument
from neurogra.knowledge.schemas.common import ExtractionMethod
from neurogra.knowledge.schemas.source import Document, ParsedBlock
from neurogra.knowledge.utils import stable_hash


class BasicPdfParser:
    """Extract page text into paragraph-like blocks with conservative provenance."""

    parser_name = "pypdf_basic"

    @property
    def parser_version(self) -> str:
        import pypdf

        return getattr(pypdf, "__version__", "unknown")

    def parse(self, document: Document, config: ParsingConfig) -> ParsedDocument:
        parse_config = config.model_dump(mode="json")
        parse_id = stable_hash(
            {
                "document_id": document.document_id,
                "parser_name": self.parser_name,
                "parser_version": self.parser_version,
                "config": parse_config,
            },
            "parse",
        )
        snapshot_path = Path(document.snapshot_path)
        try:
            reader = PdfReader(snapshot_path)
        except PdfReadError as exc:
            raise ValueError(f"PDF could not be read: {snapshot_path}") from exc

        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception as exc:  # pragma: no cover - depends on encrypted fixture
                raise ValueError(f"Encrypted PDF could not be opened: {snapshot_path}") from exc

        blocks: list[ParsedBlock] = []
        issues: list[str] = []
        for page_index, page in enumerate(reader.pages, start=1):
            try:
                text = page.extract_text() or ""
            except Exception as exc:  # pragma: no cover - parser library edge case
                issues.append(f"page_{page_index}:extract_text_failed:{exc.__class__.__name__}")
                text = ""
            page_width = float(page.mediabox.width or 1)
            page_height = float(page.mediabox.height or 1)
            paragraphs = _split_page_text(text)
            if not paragraphs:
                issues.append(f"page_{page_index}:no_native_text")
                blocks.append(
                    _make_block(
                        parse_id=parse_id,
                        document_id=document.document_id,
                        page_no=page_index,
                        reading_order=0,
                        raw_text="",
                        page_width=page_width,
                        page_height=page_height,
                        issues=["no_native_text", "ocr_required"],
                    )
                )
                continue
            if len(text.strip()) < config.min_text_chars_per_page:
                issues.append(f"page_{page_index}:native_text_below_threshold")
            for order, paragraph in enumerate(paragraphs):
                block_issues: list[str] = []
                if len(text.strip()) < config.min_text_chars_per_page:
                    block_issues.append("native_text_below_threshold")
                blocks.append(
                    _make_block(
                        parse_id=parse_id,
                        document_id=document.document_id,
                        page_no=page_index,
                        reading_order=order,
                        raw_text=paragraph,
                        page_width=page_width,
                        page_height=page_height,
                        issues=block_issues,
                    )
                )

        return ParsedDocument(
            document_id=document.document_id,
            parse_id=parse_id,
            parser_name=self.parser_name,
            parser_version=self.parser_version,
            config_hash=stable_hash(parse_config, "parsecfg"),
            blocks=blocks,
            issues=issues,
        )


def _split_page_text(text: str) -> list[str]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    paragraphs: list[str] = []
    current: list[str] = []
    for line in normalized.split("\n"):
        stripped = line.strip()
        if not stripped:
            if current:
                paragraphs.append("\n".join(current))
                current = []
            continue
        current.append(stripped)
    if current:
        paragraphs.append("\n".join(current))
    return paragraphs


def _make_block(
    *,
    parse_id: str,
    document_id: str,
    page_no: int,
    reading_order: int,
    raw_text: str,
    page_width: float,
    page_height: float,
    issues: list[str],
) -> ParsedBlock:
    block_id = stable_hash(
        {
            "parse_id": parse_id,
            "page_no": page_no,
            "reading_order": reading_order,
            "raw_text": raw_text,
        },
        "block",
    )
    return ParsedBlock(
        block_id=block_id,
        parse_id=parse_id,
        document_id=document_id,
        page_no=page_no,
        reading_order=reading_order,
        block_type="paragraph",
        raw_text=raw_text,
        bbox=None,
        page_width=page_width,
        page_height=page_height,
        extraction_method=ExtractionMethod.NATIVE_TEXT,
        parser_quality=None,
        issues=issues,
    )
