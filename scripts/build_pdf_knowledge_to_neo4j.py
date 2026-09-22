"""Build a local PDF knowledge base and publish its graph to Neo4j.

Edit the constants in the "User settings" section, then run:

    python scripts/build_pdf_knowledge_to_neo4j.py

The script accepts either one PDF file or a folder. Folder mode recursively
processes all *.pdf files. Each PDF is registered, parsed, chunked, extracted,
validated, auto-reviewed, released, and written to Neo4j when the selected
configuration uses graph_store.provider=neo4j.
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# User settings
# ---------------------------------------------------------------------------

# Write either a single PDF path or a folder path here.
INPUT_PATH = r"D:\Python Project\NeuroGRA\paper\阿尔茨海默病源性轻度认知障碍诊疗中国专家共识.pdf"
# This config already contains:
# - Ollama qwen3.6:35b
# - Ollama qwen3-embedding:0.6b
# - local Neo4j bolt://localhost:7687, neo4j / neo4j123456
CONFIG_PATH = r"configs/knowledge.local.ollama_neo4j.example.yaml"

# Metadata attached to every PDF unless you customize per file below.
SOURCE_ROLE = "clinical_guideline"
LANGUAGE = "zh"
ORGANIZATION = None
METADATA_STATUS = "pending"

# If True, pending chunks and valid clause revisions are approved automatically.
# Keep True for local development. For production curation, set False and use
# review-export / review-import manually before release.
AUTO_APPROVE = True

# Activate each successfully built release in the local SQLite registry.
# When processing a folder, the last successfully processed PDF becomes active.
ACTIVATE_RELEASE = True

# Stop immediately on the first failed PDF. If False, the script continues and
# records failures in the final summary.
STOP_ON_ERROR = False


# ---------------------------------------------------------------------------
# Script implementation
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CODE_ROOT = PROJECT_ROOT / "code"
if str(CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(CODE_ROOT))

from neurogra.knowledge.config import BuildConfig, load_config  # noqa: E402
from neurogra.knowledge.extraction.extractor import RuleBasedExtractor, context_from_chunk  # noqa: E402
from neurogra.knowledge.extraction.linker import normalize_candidates  # noqa: E402
from neurogra.knowledge.ingestion.registry import RegistrationMetadata, register_pdf  # noqa: E402
from neurogra.knowledge.parsing.simple_pdf import BasicPdfParser  # noqa: E402
from neurogra.knowledge.processing.cleaner import build_spans  # noqa: E402
from neurogra.knowledge.processing.segmenter import build_chunks  # noqa: E402
from neurogra.knowledge.release.service import ReleaseSelection, activate_release, build_release  # noqa: E402
from neurogra.knowledge.review.service import apply_decisions, export_review_batch  # noqa: E402
from neurogra.knowledge.storage.artifacts import write_jsonl  # noqa: E402
from neurogra.knowledge.storage.sqlite import Repository, init_store  # noqa: E402
from neurogra.knowledge.validation.validator import validate_revisions  # noqa: E402


def main() -> int:
    config_path = _resolve_project_path(CONFIG_PATH)
    input_path = _resolve_project_path(INPUT_PATH)
    config = load_config(config_path)
    _assert_neo4j_configured(config)
    pdf_paths = _discover_pdfs(input_path)
    if not pdf_paths:
        print(
            json.dumps(
                {"status": "error", "code": "no_pdf_found", "input_path": str(input_path)},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    results: list[dict[str, Any]] = []
    with init_store(config) as repository:
        for pdf_path in pdf_paths:
            try:
                print(f"[NeuroGRA] Processing PDF: {pdf_path}")
                result = process_pdf(pdf_path, config, repository)
                results.append(result)
                print(
                    json.dumps(
                        {
                            "status": "ok",
                            "pdf": str(pdf_path),
                            "document_id": result["document_id"],
                            "chunk_build_id": result["chunk_build_id"],
                            "release_id": result["release_id"],
                            "neo4j_release_id": result["neo4j_release_id"],
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
            except Exception as exc:  # noqa: BLE001 - batch runner should report per-file failures.
                failure = {
                    "status": "error",
                    "pdf": str(pdf_path),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                }
                results.append(failure)
                print(json.dumps(failure, ensure_ascii=False, indent=2), file=sys.stderr)
                if STOP_ON_ERROR:
                    break

    summary = {
        "status": "ok" if all(item.get("status") == "ok" for item in results) else "partial_failed",
        "config": str(config_path),
        "input_path": str(input_path),
        "pdf_count": len(pdf_paths),
        "success_count": sum(1 for item in results if item.get("status") == "ok"),
        "failure_count": sum(1 for item in results if item.get("status") != "ok"),
        "results": results,
    }
    summary_path = config.resolve_path(config.paths.output_root) / "runs" / "pdf_to_neo4j_summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({**summary, "summary_path": str(summary_path)}, ensure_ascii=False, indent=2))
    return 0 if summary["failure_count"] == 0 else 1


def process_pdf(pdf_path: Path, config: BuildConfig, repository: Repository) -> dict[str, Any]:
    metadata = RegistrationMetadata(
        source_role=SOURCE_ROLE,  # type: ignore[arg-type]
        title=pdf_path.stem,
        language=LANGUAGE,
        organization=ORGANIZATION,
        metadata_status=METADATA_STATUS,  # type: ignore[arg-type]
    )
    registration = register_pdf(pdf_path, metadata, config, repository)
    document = registration.document

    parser = BasicPdfParser()
    parsed = parser.parse(document, config.parsing)
    if config.parsing.adapter == "docling":
        parsed = parsed.model_copy(
            update={"issues": ["docling_adapter_not_available_pypdf_basic_used", *parsed.issues]}
        )
    page_count = max((block.page_no for block in parsed.blocks), default=0) or None
    if page_count is not None and document.page_count != page_count:
        document = document.model_copy(update={"page_count": page_count})
        repository.upsert_document(document)
    repository.save_parsed_document(parsed)
    blocks_path = config.resolve_path(config.paths.data_root) / "parses" / parsed.parse_id / "blocks.jsonl"
    write_jsonl(blocks_path, parsed.blocks)

    span_result = build_spans(parsed)
    repository.save_spans(span_result.spans)
    spans_path = config.resolve_path(config.paths.data_root) / "parses" / parsed.parse_id / "spans.jsonl"
    write_jsonl(spans_path, span_result.spans)

    chunk_result = build_chunks(span_result.spans, config.chunking)
    repository.save_chunks(chunk_result.chunks)
    chunks_path = (
        config.resolve_path(config.paths.data_root)
        / "chunks"
        / chunk_result.chunk_build_id
        / "chunks.jsonl"
    )
    write_jsonl(chunks_path, chunk_result.chunks)

    extractor = RuleBasedExtractor()
    candidates = []
    extraction_issues = []
    for chunk in chunk_result.chunks:
        if chunk.role != "child":
            continue
        extraction = extractor.extract(context_from_chunk(chunk))
        candidates.extend(extraction.candidates)
        extraction_issues.extend(extraction.issues)
    repository.save_candidates(candidates)
    extraction_root = config.resolve_path(config.paths.data_root) / "extractions" / chunk_result.chunk_build_id
    write_jsonl(extraction_root / "candidates.jsonl", candidates)

    normalization = normalize_candidates(candidates)
    repository.save_normalization_result(normalization)
    write_jsonl(extraction_root / "entities.jsonl", normalization.entities)
    write_jsonl(extraction_root / "mentions.jsonl", normalization.mentions)
    write_jsonl(extraction_root / "clause_revisions.jsonl", normalization.clause_revisions)

    reports = validate_revisions(
        repository.get_clause_revisions_for_chunk_build(chunk_result.chunk_build_id),
        repository,
    )
    validation_error_count = sum(1 for report in reports if report.has_errors)
    if validation_error_count:
        raise RuntimeError(f"Validation failed for {validation_error_count} clause revision(s)")

    if AUTO_APPROVE:
        batch = export_review_batch(repository, chunk_result.chunk_build_id, "auto_pdf_to_neo4j")
        review_result = apply_decisions(repository, batch.decisions)
        if review_result.blocked_count:
            raise RuntimeError(f"Auto review blocked: {review_result.issues}")
    else:
        raise RuntimeError(
            "AUTO_APPROVE is False. Export/import review decisions before release, or set AUTO_APPROVE=True."
        )

    release_result = build_release(
        ReleaseSelection(chunk_build_id=chunk_result.chunk_build_id),
        config,
        repository,
    )
    if release_result.issues:
        raise RuntimeError(f"Release failed: {release_result.issues}")

    activated = False
    if ACTIVATE_RELEASE:
        activate_release(repository, release_result.release_id)
        activated = True

    return {
        "status": "ok",
        "pdf": str(pdf_path),
        "document_id": document.document_id,
        "parse_id": parsed.parse_id,
        "span_count": len(span_result.spans),
        "chunk_build_id": chunk_result.chunk_build_id,
        "chunk_count": len(chunk_result.chunks),
        "candidate_count": len(candidates),
        "entity_count": len(normalization.entities),
        "clause_revision_count": len(normalization.clause_revisions),
        "release_id": release_result.release_id,
        "neo4j_release_id": release_result.manifest.neo4j_release_id,
        "activated": activated,
        "registration_issues": registration.issues,
        "parse_issues": parsed.issues,
        "span_issues": span_result.issues,
        "extraction_issues": extraction_issues,
        "normalization_issues": normalization.issues,
        "artifacts": {
            "blocks": str(blocks_path),
            "spans": str(spans_path),
            "chunks": str(chunks_path),
            "candidates": str(extraction_root / "candidates.jsonl"),
            "entities": str(extraction_root / "entities.jsonl"),
            "mentions": str(extraction_root / "mentions.jsonl"),
            "clause_revisions": str(extraction_root / "clause_revisions.jsonl"),
        },
    }


def _discover_pdfs(input_path: Path) -> list[Path]:
    if input_path.is_file():
        if input_path.suffix.lower() != ".pdf":
            raise ValueError(f"Input file is not a PDF: {input_path}")
        return [input_path]
    if input_path.is_dir():
        return sorted(path for path in input_path.rglob("*.pdf") if path.is_file())
    raise ValueError(f"Input path does not exist: {input_path}")


def _resolve_project_path(path_text: str) -> Path:
    path = Path(path_text)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def _assert_neo4j_configured(config: BuildConfig) -> None:
    if config.graph_store.provider != "neo4j":
        raise ValueError(
            "This script is intended to publish to Neo4j. "
            "Set graph_store.provider: neo4j in CONFIG_PATH."
        )


if __name__ == "__main__":
    raise SystemExit(main())
