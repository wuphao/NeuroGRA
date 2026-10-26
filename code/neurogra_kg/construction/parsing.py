"""Layout-aware PDF and explicit structured-document ingestion."""

import json
import re
import shutil
import statistics
from pathlib import Path

from ..contracts.domain import Block, Document, DocumentInput, ParsedDocument, TableCell
from ..utils import checksum, identity
from .structure import build_structure
from .layout import merge_body_lines


class DocumentParser:
    def __init__(self, data_root: Path):
        self.data_root = Path(data_root)

    def parse(self, input: DocumentInput) -> ParsedDocument:
        path = input.path.resolve()
        if not path.is_file():
            raise FileNotFoundError("document_not_found:" + str(path))
        digest = checksum(path)
        document_id = identity("doc", [digest, input.version])
        snapshot = self.data_root / "sources" / document_id / ("source" + path.suffix.lower())
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        if snapshot.exists():
            if checksum(snapshot) != digest:
                raise ValueError("document_snapshot_checksum_mismatch")
        else:
            shutil.copyfile(path, snapshot)
        document = Document(document_id=document_id, checksum=digest, source_path=str(path),
            snapshot_path=str(snapshot), title=input.title or path.stem, version=input.version, language=input.language)
        if path.suffix.lower() == ".json":
            raw = json.loads(snapshot.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or not isinstance(raw.get("blocks"), list):
                raise ValueError("structured_document_requires_blocks")
            blocks = []
            aliases = {}
            for index, row in enumerate(raw["blocks"]):
                local_id = row.get("block_id", str(index))
                if local_id in aliases:
                    raise ValueError("duplicate_structured_block_id")
                aliases[local_id] = identity("block", [document_id, local_id])
            for index, row in enumerate(raw["blocks"]):
                row = dict(row)
                local_id = row.get("block_id", str(index))
                row.update(block_id=aliases[local_id], document_id=document_id)
                row.setdefault("order", index)
                if row.get("table_id"):
                    if row["table_id"] not in aliases:
                        raise ValueError("unknown_structured_table_id")
                    row["table_id"] = aliases[row["table_id"]]
                blocks.append(Block.model_validate(row))
            parsed = ParsedDocument(document=document, blocks=blocks, parser="structured_json",
                                    parser_version="1", issues=raw.get("issues", []))
        elif path.suffix.lower() == ".pdf":
            parsed = self._pdf(document)
        elif path.suffix.lower() == ".docx":
            from .word import parse_docx
            parsed = parse_docx(document)
        else:
            raise ValueError("supported_document_formats:pdf,json,docx")
        if not parsed.blocks:
            parsed.issues.append("no_parseable_blocks")
        parsed.edges = build_structure(parsed.blocks)
        if path.suffix.lower() == ".json":
            from ..contracts.domain import StructureEdge
            declared = []
            for row in raw.get("edges", []):
                row = dict(row)
                if row.get("source") not in aliases or row.get("target") not in aliases:
                    raise ValueError("unknown_structured_edge_endpoint")
                row.update(source=aliases[row["source"]], target=aliases[row["target"]])
                declared.append(StructureEdge.model_validate(row))
            parsed.edges = list({(edge.source, edge.target, edge.relation): edge
                for edge in [*parsed.edges, *declared]}.values())
        from .structure import continuation_context
        parsed.edges = continuation_context(parsed.blocks, parsed.edges)
        lookup = {block.block_id: block for block in parsed.blocks}
        for block in parsed.blocks:
            if any(re.match(r"\s*(?:参考文献|References)(?:\s|与|及|$)", lookup[id].text, re.I)
                   for id in block.section_path if id in lookup):
                block.issues.append("bibliography")
        return parsed

    def _pdf(self, document):
        import fitz
        blocks = []
        issues = []
        with fitz.open(document.snapshot_path) as pdf:
            if pdf.needs_pass:
                raise ValueError("encrypted_document_requires_decryption")
            for page_index, page in enumerate(pdf, start=1):
                page_dict = page.get_text("dict", sort=True)
                text_blocks = [block for block in page_dict["blocks"] if block.get("type") == 0]
                if not any(block.get("lines") for block in text_blocks):
                    # OCR is explicit: report failures rather than injecting image-model text.
                    try:
                        textpage = page.get_textpage_ocr(language="eng", full=True)
                        page_dict = page.get_text("dict", textpage=textpage, sort=True)
                        text_blocks = [b for b in page_dict["blocks"] if b.get("type") == 0]
                        issues.append(f"page_{page_index}:ocr_eng_used_language_review_required")
                    except Exception:
                        issues.append(f"page_{page_index}:ocr_required_unavailable")
                        continue
                font_sizes = [span["size"] for b in text_blocks for line in b["lines"]
                              for span in line["spans"] if span["text"].strip()]
                median = statistics.median(font_sizes) if font_sizes else 10
                superscript_markers = {span["text"].strip() for raw in text_blocks for line in raw["lines"]
                    for span in line["spans"] if span.get("flags", 0) & 1}
                tables = []
                try:
                    tables = page.find_tables().tables
                except Exception:
                    issues.append(f"page_{page_index}:table_detection_failed")
                page_blocks = []
                for table_index, table in enumerate(tables):
                    rows = table.extract()
                    cells = [TableCell(row=r, column=c, text=value or "", is_header=r == 0)
                             for r, row in enumerate(rows) for c, value in enumerate(row)]
                    table_id = identity("block", [document.document_id, page_index, "table", table_index])
                    page_blocks.append(Block(block_id=table_id, document_id=document.document_id,
                        page=page_index, order=0, kind="table", text="\n".join(
                            "\t".join(value or "" for value in row) for row in rows),
                        bbox=self._box(table.bbox, page), cells=cells,
                        issues=["first_row_header_heuristic"]))
                    for cell_index, cell in enumerate(cells):
                        if not cell.text.strip():
                            continue
                        bbox = table.rows[cell.row].cells[cell.column]
                        page_blocks.append(Block(block_id=identity("block", [table_id, cell_index]),
                            document_id=document.document_id, page=page_index, order=0,
                            kind="table_cell", text=cell.text, table_id=table_id,
                            bbox=self._box(bbox, page) if bbox else self._box(table.bbox, page),
                            issues=["table_header"] if cell.is_header else []))
                for block_index, raw in enumerate(text_blocks):
                    rect = fitz.Rect(raw["bbox"])
                    if any(fitz.Rect(table.bbox).contains(rect) for table in tables):
                        continue
                    lines = ["".join(span["text"] for span in line["spans"]) for line in raw["lines"]]
                    text = "\n".join(lines).strip()
                    if not text:
                        continue
                    spans = [span for line in raw["lines"] for span in line["spans"] if span["text"].strip()]
                    max_size = max(span["size"] for span in spans)
                    refs = [span["text"].strip() for span in spans if span.get("flags", 0) & 1
                            and re.fullmatch(r"[0-9*†‡a-z]+", span["text"].strip())]
                    note = re.match(r"^([*†‡]|[a-z]|\d{1,2})[)）.、\s]+(.+)", text, re.S)
                    kind, level, marker = "paragraph", None, None
                    local_issues = []
                    near_table = any(0 <= rect.y0 - table.bbox[3] <= page.rect.height * .15 for table in tables)
                    is_note_marker = note and (note.group(1) in {"*", "†", "‡"} or
                        (note.group(1) in superscript_markers and max_size < median * .9))
                    if is_note_marker and (rect.y0 > page.rect.height * .65 or near_table) and max_size <= median:
                        kind, marker = "footnote", note.group(1)
                        local_issues.append("footnote_type_heuristic")
                    elif len(text) < 150 and (max_size > median * 1.18 or re.match(r"^\d+(\.\d+)*\s+\S", text)
                         or (len(text) < 70 and re.match(r"^(?:[一二三四五六七八九十]+、|[（(][一二三四五六七八九十]+[）)])", text))
                         or text.strip() in {"参考文献", "References"}):
                        kind = "heading"
                        number = re.match(r"^(\d+(?:\.\d+)*)\s", text)
                        level = len(number.group(1).split(".")) if number else (3 if re.match(r"^[（(]", text) else 1 if max_size > median * 1.4 else 2)
                        local_issues.append("heading_type_heuristic")
                    elif re.match(r"^([-•▪]|\d+[)）])\s*\S", text):
                        kind = "list_item"
                    elif re.match(r"^(Table|表|Figure|图)\s*\d+", text, re.I):
                        kind = "caption"
                    if rect.y1 < page.rect.height * .09 and max_size < median:
                        local_issues.append("running_header")
                    if re.fullmatch(r"\s*[·\-]?\s*\d+\s*[·\-]?\s*", text):
                        local_issues.append("page_number")
                    page_blocks.append(Block(block_id=identity("block", [document.document_id, page_index, block_index]),
                        document_id=document.document_id, page=page_index, order=0, kind=kind, text=text,
                        bbox=self._box(rect, page), heading_level=level, marker=marker,
                        reference_markers=refs, issues=local_issues))
                # Nearby notes are candidates, not explicit table associations.
                for block in page_blocks:
                    if block.kind == "footnote" and tables:
                        nearby = [table for table in page_blocks if table.kind == "table" and table.bbox
                                  and table.bbox[3] <= block.bbox[1] and block.bbox[1] - table.bbox[3] < .15]
                        if nearby:
                            block.issues.append("possible_table_note:" + nearby[-1].block_id)
                page_blocks = merge_body_lines(page_blocks)
                for order, block in enumerate(page_blocks):
                    block.order = order
                blocks.extend(page_blocks)
        return ParsedDocument(document=document, blocks=blocks, parser="pymupdf_layout",
                              parser_version=fitz.VersionBind, issues=issues)

    @staticmethod
    def _box(rect, page):
        return tuple(float(value) for value in (
            rect[0] / page.rect.width, rect[1] / page.rect.height,
            rect[2] / page.rect.width, rect[3] / page.rect.height))
