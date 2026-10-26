"""Immutable, checksummed source and statement packages."""

import json
from pathlib import Path

from ..contracts.domain import Block, KnowledgeStatement
from ..utils import atomic_json, checksum, identity
from ..construction.sources import condition_leaves, validate_ref
from .indexes import LocalVectorStore
from ..catalogs import FIELDS, PURPOSE_LABELS, RELATION_LABELS
from ..terminology import entity_names, terminology_binding
from ..construction.localization import localized_text

INDEX_TEXT_VERSION = "bilingual_statement_v1"


def condition_text(condition):
    if condition is None:
        return ""
    if condition.kind in {"ALL", "ANY", "NOT"}:
        if condition.kind == "NOT":
            return "非 NOT (" + condition_text(condition.child) + ")"
        connector = " 且 AND " if condition.kind == "ALL" else " 或 OR "
        return "(" + connector.join(condition_text(c) for c in condition.children) + ")"
    if condition.kind == "TEXT":
        text = localized_text(condition.text, condition.zh_annotation)
        return text + (" / " + condition.text if text != condition.text else "")
    label = FIELDS.get(condition.field, {}).get("description", condition.field)
    symbols = {"eq": "=", "ne": "!=", "gt": ">", "gte": ">=", "lt": "<", "lte": "<=", "in": "属于 in", "exists": "记录存在 exists"}
    text = f"{label} [{condition.field}] {symbols[condition.operator]} {json.dumps(condition.value, ensure_ascii=False)}"
    for name, value in [("单位", condition.unit), ("方法", condition.method), ("时间", condition.time_scope), ("人群", condition.population)]:
        if value:
            text += f"；{name}: {value}"
    return text


def statement_display_text(statement):
    return localized_text(statement.assertion_text, statement.zh_annotation)


def statement_text(statement):
    return "\n".join(dict.fromkeys([
        " / ".join(entity_names(statement.subject)), RELATION_LABELS[statement.predicate] + " " + statement.predicate,
        " / ".join(entity_names(statement.object)), statement_display_text(statement), statement.assertion_text,
        "用途:" + ",".join(PURPOSE_LABELS[p] + " " + p for p in statement.purposes),
        "条件:" + condition_text(statement.condition), "条件状态:" + statement.condition_status]))


def create_release(data_root, registry, build_id, statements, parsed_documents, embedding, graph, budget):
    accepted = [statement for statement in statements if statement.status in {"approved", "source_checked"}]
    if not accepted:
        raise ValueError("no_source_checked_statements_to_publish")
    blocks = {block.block_id: block for document in parsed_documents for block in document.blocks}
    for statement in accepted:
        for ref in [*statement.source_refs, *[ref for leaf in condition_leaves(statement.condition) for ref in leaf.source_refs]]:
            validate_ref(ref, blocks)
    data = {"statements": [statement.model_dump(mode="json") for statement in accepted],
            "documents": [document.document.model_dump(mode="json") for document in parsed_documents],
            "blocks": [block.model_dump(mode="json") for block in blocks.values()],
            "edges": [edge.model_dump(mode="json") for document in parsed_documents for edge in document.edges]}
    release_id = identity("release", [INDEX_TEXT_VERSION, terminology_binding(), data, embedding.model_digest(), embedding.config.model_dump(mode="json"), type(graph).__name__])
    directory = Path(data_root) / "releases" / release_id
    existing = directory / "manifest.json"
    if existing.is_file():
        manifest = json.loads(existing.read_text(encoding="utf-8"))
        load_release(manifest)
        graph.publish(release_id, accepted, blocks)
        registry.publish(release_id, manifest)
        return manifest
    directory.mkdir(parents=True, exist_ok=True)
    resources = [{"id": "statement:" + statement.statement_id, "kind": "statement",
                  "statement_ids": [statement.statement_id], "text": statement_text(statement),
                  "text_template_version": INDEX_TEXT_VERSION, "terminology_binding": terminology_binding()} for statement in accepted]
    block_statements = {}
    for statement in accepted:
        refs = [*statement.source_refs, *[ref for leaf in condition_leaves(statement.condition) for ref in leaf.source_refs]]
        for ref in refs:
            block_statements.setdefault(ref.block_id, set()).add(statement.statement_id)
    for block in blocks.values():
        if block.text.strip() and "container_only" not in block.issues:
            resources.append({"id": "block:" + block.block_id, "kind": "original",
                              "statement_ids": sorted(block_statements.get(block.block_id, [])), "text": block.text})
    atomic_json(directory / "corpus.json", data)
    atomic_json(directory / "resources.json", resources)
    vector = LocalVectorStore.build(directory, resources, embedding, release_id, budget)
    manifest = {"release_id": release_id, "build_id": build_id, "directory": str(directory.resolve()),
                "index_text_version": INDEX_TEXT_VERSION, "terminology_binding": terminology_binding(),
                "chinese_expression_summary": {state: sum(
                    (s.zh_annotation.status if s.zh_annotation else "not_available") == state for s in accepted)
                    for state in ["verified", "unverified", "failed", "not_available"]},
                "graph_provider": type(graph).__name__, "statement_ids": [s.statement_id for s in accepted],
                "model_digest": vector["model_digest"], "checksums": {
                    name: checksum(directory / name) for name in ["corpus.json", "resources.json", "vector_manifest.json", "vectors.npy"]}}
    graph.publish(release_id, accepted, blocks)
    # No active pointer until graph and every local artifact have succeeded.
    atomic_json(existing, manifest)
    registry.publish(release_id, manifest)
    return manifest


def load_release(manifest):
    directory = Path(manifest["directory"])
    for name, expected in manifest["checksums"].items():
        if checksum(directory / name) != expected:
            raise ValueError("release_artifact_checksum_mismatch:" + name)
    data = json.loads((directory / "corpus.json").read_text(encoding="utf-8"))
    statements = [KnowledgeStatement.model_validate(row) for row in data["statements"]]
    blocks = {row["block_id"]: Block.model_validate(row) for row in data["blocks"]}
    if sorted(statement.statement_id for statement in statements) != sorted(manifest["statement_ids"]):
        raise ValueError("release_statement_membership_mismatch")
    resources = json.loads((directory / "resources.json").read_text(encoding="utf-8"))
    return statements, blocks, resources, data
