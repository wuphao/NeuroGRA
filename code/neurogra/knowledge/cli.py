"""Command line interface for module-one knowledge construction."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable

from pydantic import ValidationError

from neurogra.knowledge.config import dump_schema, load_config, normalize_config_dict
from neurogra.knowledge.extraction.extractor import RuleBasedExtractor, context_from_chunk
from neurogra.knowledge.extraction.linker import normalize_candidates
from neurogra.knowledge.evaluation.metrics import evaluate_retrieval
from neurogra.knowledge.ingestion.registry import RegistrationMetadata, register_pdf
from neurogra.knowledge.indexing.lexical import SearchRequest, build_text_index, search_text
from neurogra.knowledge.parsing.simple_pdf import BasicPdfParser
from neurogra.knowledge.processing.cleaner import build_spans
from neurogra.knowledge.processing.segmenter import build_chunks
from neurogra.knowledge.release.service import ReleaseSelection, activate_release, build_release
from neurogra.knowledge.retrieval.service import search
from neurogra.knowledge.review.service import apply_decisions, export_review_batch
from neurogra.knowledge.schemas.lifecycle import ReviewDecision
from neurogra.knowledge.storage.artifacts import write_jsonl
from neurogra.knowledge.storage.sqlite import init_store
from neurogra.knowledge.validation.validator import validate_revisions

EXIT_SUCCESS = 0
EXIT_CONFIG = 2
EXIT_DEPENDENCY = 3
EXIT_VALIDATION = 4
EXIT_BLOCKED = 5


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m neurogra.knowledge.cli")
    parser.add_argument(
        "--config",
        default="configs/knowledge.default.yaml",
        help="Path to module-one YAML configuration.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("config-check", help="Validate config and print JSON.")
    subparsers.add_parser("init-store", help="Initialize SQLite authoritative store.")

    ingest_parser = subparsers.add_parser("ingest", help="Register an explicit PDF file.")
    ingest_parser.add_argument("--pdf", required=True, help="PDF path to register.")
    ingest_parser.add_argument(
        "--metadata",
        required=True,
        help="JSON metadata file with at least source_role.",
    )

    parse_parser = subparsers.add_parser("parse", help="Parse a registered document into blocks.")
    parse_parser.add_argument("--document-id", required=True, help="Registered document ID.")

    spans_parser = subparsers.add_parser("build-spans", help="Clean parsed blocks into source spans.")
    spans_parser.add_argument("--parse-id", required=True, help="Parse ID to clean.")

    chunks_parser = subparsers.add_parser("build-chunks", help="Build parent/child chunks from spans.")
    chunks_parser.add_argument("--parse-id", required=True, help="Parse ID whose spans should be chunked.")

    index_parser = subparsers.add_parser("build-index", help="Build a development BM25 index.")
    index_parser.add_argument("--chunk-build-id", required=True, help="Chunk build ID to index.")

    search_parser = subparsers.add_parser("search-dev", help="Search a development BM25 index.")
    search_parser.add_argument("--chunk-build-id", required=True, help="Chunk build ID to search.")
    search_parser.add_argument("--query", required=True, help="Search query.")
    search_parser.add_argument("--top-k", type=int, default=5, help="Number of hits to return.")

    extract_parser = subparsers.add_parser("extract-candidates", help="Extract candidate clauses from child chunks.")
    extract_parser.add_argument("--chunk-build-id", required=True, help="Chunk build ID to extract from.")

    normalize_parser = subparsers.add_parser("normalize-candidates", help="Normalize extracted candidates.")
    normalize_parser.add_argument("--chunk-build-id", required=True, help="Chunk build ID whose candidates should be normalized.")

    validate_parser = subparsers.add_parser("validate-clauses", help="Validate normalized clause revisions.")
    validate_parser.add_argument("--chunk-build-id", required=True, help="Chunk build ID whose clauses should be validated.")

    review_export_parser = subparsers.add_parser("review-export", help="Export a review decision JSONL template.")
    review_export_parser.add_argument("--chunk-build-id", required=True, help="Chunk build ID to review.")
    review_export_parser.add_argument("--reviewer-id", default="local_reviewer", help="Reviewer identifier.")
    review_export_parser.add_argument("--out", default=None, help="Optional output JSONL path.")

    review_import_parser = subparsers.add_parser("review-import", help="Import review decisions from JSONL.")
    review_import_parser.add_argument("--file", required=True, help="Decision JSONL file.")

    release_build_parser = subparsers.add_parser("release-build", help="Build an immutable local release.")
    release_build_parser.add_argument("--chunk-build-id", required=True, help="Approved chunk build ID.")

    release_activate_parser = subparsers.add_parser("release-activate", help="Activate a validated release.")
    release_activate_parser.add_argument("--release-id", required=True, help="Release ID.")

    search_parser = subparsers.add_parser("search", help="Search the active released knowledge base.")
    search_parser.add_argument("--query", required=True, help="Search query.")
    search_parser.add_argument("--top-k", type=int, default=5, help="Number of hits.")
    search_parser.add_argument("--release-id", default=None, help="Optional release ID.")

    eval_parser = subparsers.add_parser("evaluate", help="Evaluate retrieval against JSONL annotations.")
    eval_parser.add_argument("--dataset", required=True, help="JSONL file with query and relevant_span_ids.")
    eval_parser.add_argument("--top-k", type=int, default=5, help="Recall@k cutoff.")
    eval_parser.add_argument("--release-id", default=None, help="Optional release ID.")

    schema_parser = subparsers.add_parser("schema-export", help="Export JSON Schema files.")
    schema_parser.add_argument(
        "--out",
        default="data/knowledge/schemas/build_config.schema.json",
        help="Output path for BuildConfig JSON Schema.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return _dispatch(args)
    except ValidationError as exc:
        _print_error("validation_error", exc.errors(include_url=False))
        return EXIT_CONFIG
    except ValueError as exc:
        _print_error("config_error", str(exc))
        return EXIT_CONFIG
    except OSError as exc:
        _print_error("dependency_or_filesystem_error", str(exc))
        return EXIT_DEPENDENCY


def _dispatch(args: argparse.Namespace) -> int:
    commands: dict[str, Callable[[argparse.Namespace], int]] = {
        "config-check": _config_check,
        "init-store": _init_store,
        "ingest": _ingest,
        "parse": _parse,
        "build-spans": _build_spans,
        "build-chunks": _build_chunks,
        "build-index": _build_index,
        "search-dev": _search_dev,
        "extract-candidates": _extract_candidates,
        "normalize-candidates": _normalize_candidates,
        "validate-clauses": _validate_clauses,
        "review-export": _review_export,
        "review-import": _review_import,
        "release-build": _release_build,
        "release-activate": _release_activate,
        "search": _search,
        "evaluate": _evaluate,
        "schema-export": _schema_export,
    }
    return commands[args.command](args)


def _config_check(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    payload = {
        "status": "ok",
        "config_hash": config.fingerprint(),
        "config": normalize_config_dict(config),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return EXIT_SUCCESS


def _init_store(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    with init_store(config) as repo:
        payload = {
            "status": "ok",
            "database": str(repo.db_path),
            "migrations": repo.migration_versions(),
            "tables": repo.table_names(),
        }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return EXIT_SUCCESS


def _ingest(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    metadata_path = Path(args.metadata)
    metadata = RegistrationMetadata.model_validate(
        json.loads(metadata_path.read_text(encoding="utf-8"))
    )
    with init_store(config) as repo:
        result = register_pdf(args.pdf, metadata, config, repo)
    print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True))
    return EXIT_SUCCESS


def _parse(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    with init_store(config) as repo:
        document = repo.get_document(args.document_id)
        parser = BasicPdfParser()
        parsed = parser.parse(document, config.parsing)
        issues = list(parsed.issues)
        if config.parsing.adapter == "docling":
            issues.insert(0, "docling_adapter_not_available_pypdf_basic_used")
            parsed = parsed.model_copy(update={"issues": issues})
        page_count = max((block.page_no for block in parsed.blocks), default=0) or None
        if page_count is not None and document.page_count != page_count:
            repo.upsert_document(document.model_copy(update={"page_count": page_count}))
        repo.save_parsed_document(parsed)
        blocks_path = (
            config.resolve_path(config.paths.data_root)
            / "parses"
            / parsed.parse_id
            / "blocks.jsonl"
        )
        write_jsonl(blocks_path, parsed.blocks)
        payload = {
            "status": "ok",
            "document_id": parsed.document_id,
            "parse_id": parsed.parse_id,
            "parser": parsed.parser_name,
            "parser_version": parsed.parser_version,
            "block_count": len(parsed.blocks),
            "issues": parsed.issues,
            "artifacts": {"blocks": str(blocks_path)},
        }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return EXIT_SUCCESS


def _build_spans(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    with init_store(config) as repo:
        parsed = repo.get_parsed_document(args.parse_id)
        result = build_spans(parsed)
        repo.save_spans(result.spans)
        spans_path = (
            config.resolve_path(config.paths.data_root)
            / "parses"
            / parsed.parse_id
            / "spans.jsonl"
        )
        write_jsonl(spans_path, result.spans)
        payload = {
            "status": "ok",
            "document_id": result.document_id,
            "parse_id": result.parse_id,
            "span_count": len(result.spans),
            "removed_repeated_lines": result.removed_repeated_lines,
            "issues": result.issues,
            "artifacts": {"spans": str(spans_path)},
        }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return EXIT_SUCCESS


def _build_chunks(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    with init_store(config) as repo:
        spans = repo.get_spans_for_parse(args.parse_id)
        result = build_chunks(spans, config.chunking)
        repo.save_chunks(result.chunks)
        chunks_path = (
            config.resolve_path(config.paths.data_root)
            / "chunks"
            / result.chunk_build_id
            / "chunks.jsonl"
        )
        write_jsonl(chunks_path, result.chunks)
        payload = {
            "status": "ok",
            "document_id": result.document_id,
            "chunk_build_id": result.chunk_build_id,
            "chunk_count": len(result.chunks),
            "child_count": len([chunk for chunk in result.chunks if chunk.role == "child"]),
            "parent_count": len([chunk for chunk in result.chunks if chunk.role == "parent"]),
            "issues": result.issues,
            "artifacts": {"chunks": str(chunks_path)},
        }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return EXIT_SUCCESS


def _build_index(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    with init_store(config) as repo:
        chunks = repo.get_chunks_for_build(args.chunk_build_id)
        index_path = (
            config.resolve_path(config.paths.data_root)
            / "dev_indexes"
            / args.chunk_build_id
            / "bm25_index.json"
        )
        manifest = build_text_index(chunks, config.retrieval, index_path)
    print(json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True))
    return EXIT_SUCCESS


def _search_dev(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    index_path = (
        config.resolve_path(config.paths.data_root)
        / "dev_indexes"
        / args.chunk_build_id
        / "bm25_index.json"
    )
    with init_store(config) as repo:
        chunks = repo.get_chunks_for_build(args.chunk_build_id)
        hits = search_text(SearchRequest(query=args.query, top_k=args.top_k), chunks, index_path)
    payload = {
        "status": "ok",
        "mode": "development",
        "chunk_build_id": args.chunk_build_id,
        "query": args.query,
        "hits": [hit.model_dump(mode="json") for hit in hits],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return EXIT_SUCCESS


def _extract_candidates(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    extractor = RuleBasedExtractor()
    all_candidates = []
    extraction_runs = []
    with init_store(config) as repo:
        chunks = [chunk for chunk in repo.get_chunks_for_build(args.chunk_build_id) if chunk.role == "child"]
        for chunk in chunks:
            result = extractor.extract(context_from_chunk(chunk))
            all_candidates.extend(result.candidates)
            extraction_runs.append(result.model_dump(mode="json"))
        repo.save_candidates(all_candidates)
        candidates_path = (
            config.resolve_path(config.paths.data_root)
            / "extractions"
            / args.chunk_build_id
            / "candidates.jsonl"
        )
        write_jsonl(candidates_path, all_candidates)
        payload = {
            "status": "ok",
            "chunk_build_id": args.chunk_build_id,
            "extraction_run_count": len(extraction_runs),
            "candidate_count": len(all_candidates),
            "artifacts": {"candidates": str(candidates_path)},
            "issues": [
                issue
                for run in extraction_runs
                for issue in run.get("issues", [])
            ],
        }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return EXIT_SUCCESS


def _normalize_candidates(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    with init_store(config) as repo:
        candidates = repo.get_candidates_for_chunk_build(args.chunk_build_id)
        result = normalize_candidates(candidates)
        repo.save_normalization_result(result)
        base_path = config.resolve_path(config.paths.data_root) / "extractions" / args.chunk_build_id
        entities_path = base_path / "entities.jsonl"
        mentions_path = base_path / "mentions.jsonl"
        revisions_path = base_path / "clause_revisions.jsonl"
        write_jsonl(entities_path, result.entities)
        write_jsonl(mentions_path, result.mentions)
        write_jsonl(revisions_path, result.clause_revisions)
        payload = {
            "status": "ok",
            "chunk_build_id": args.chunk_build_id,
            "entity_count": len(result.entities),
            "mention_count": len(result.mentions),
            "clause_revision_count": len(result.clause_revisions),
            "issues": result.issues,
            "artifacts": {
                "entities": str(entities_path),
                "mentions": str(mentions_path),
                "clause_revisions": str(revisions_path),
            },
        }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return EXIT_SUCCESS


def _validate_clauses(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    with init_store(config) as repo:
        revisions = repo.get_clause_revisions_for_chunk_build(args.chunk_build_id)
        reports = validate_revisions(revisions, repo)
        payload = {
            "status": "ok",
            "chunk_build_id": args.chunk_build_id,
            "revision_count": len(revisions),
            "error_count": sum(1 for report in reports if report.has_errors),
            "reports": [report.model_dump(mode="json") for report in reports],
        }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return EXIT_SUCCESS


def _review_export(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    with init_store(config) as repo:
        batch = export_review_batch(repo, args.chunk_build_id, args.reviewer_id)
        out_path = Path(args.out) if args.out else (
            config.resolve_path(config.paths.data_root)
            / "reviews"
            / args.chunk_build_id
            / "decisions.jsonl"
        )
        write_jsonl(out_path, batch.decisions)
        payload = {
            "status": "ok",
            "batch_id": batch.batch_id,
            "decision_count": len(batch.decisions),
            "artifacts": {"decisions": str(out_path)},
        }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return EXIT_SUCCESS


def _review_import(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    decision_path = Path(args.file)
    decisions = [
        ReviewDecision.model_validate_json(line)
        for line in decision_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    with init_store(config) as repo:
        result = apply_decisions(repo, decisions)
    print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True))
    return EXIT_SUCCESS


def _release_build(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    with init_store(config) as repo:
        result = build_release(ReleaseSelection(chunk_build_id=args.chunk_build_id), config, repo)
    print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True))
    return EXIT_SUCCESS if not result.issues else EXIT_BLOCKED


def _release_activate(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    with init_store(config) as repo:
        result = activate_release(repo, args.release_id)
    print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True))
    return EXIT_SUCCESS


def _search(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    with init_store(config) as repo:
        response = search(repo, args.query, top_k=args.top_k, release_id=args.release_id)
    print(json.dumps(response.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True))
    return EXIT_SUCCESS


def _evaluate(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    with init_store(config) as repo:
        result = evaluate_retrieval(repo, Path(args.dataset), top_k=args.top_k, release_id=args.release_id)
    print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True))
    return EXIT_SUCCESS


def _schema_export(args: argparse.Namespace) -> int:
    load_config(args.config)
    output_path = Path(args.out)
    dump_schema(output_path)
    print(json.dumps({"status": "ok", "schema": str(output_path)}, ensure_ascii=False))
    return EXIT_SUCCESS


def _print_error(code: str, detail: object) -> None:
    print(json.dumps({"status": "error", "code": code, "detail": detail}, ensure_ascii=False), file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
