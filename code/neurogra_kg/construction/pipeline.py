"""Resumable per-unit orchestration of L1-L4 and deterministic constraints."""

import json
from pathlib import Path

from ..catalogs import FIELDS, OPERATORS
from ..contracts.domain import KnowledgeStatement
from ..contracts.tasks import AlignmentOutput, ExtractionOutput, LogicDraft, VerificationOutput
from ..utils import atomic_json, identity
from ..runtime import implementation_binding
from .alignment import align_candidate_pairs, candidate_pairs, select_bindings
from .extraction import extract, extraction_units
from .logic import assemble_logic, number_logic
from .sources import resolve_quotes
from .terminology import EntityResolver
from .verification import apply_verification
from .localization import annotate_statement, text_language


class ConstructionPipeline:
    def __init__(self, config, llm, embedding=None, field_catalog=None, terminology=None):
        self.config, self.llm = config, llm
        self.fields = FIELDS if field_catalog is None else field_catalog
        self.resolver = EntityResolver(terminology, embedding)

    def run(self, parsed, output_dir: Path, budget, on_progress=None):
        output_dir.mkdir(parents=True, exist_ok=True)
        statements, issues, traces = [], list(parsed.issues), []
        semantic_units = []
        self.completed_units, self.failed_units = 0, 0
        full_blocks = {block.block_id: block for block in parsed.blocks}
        model_binding = self.llm.binding() if hasattr(self.llm, "binding") else {"adapter": type(self.llm).__name__}
        units = list(extraction_units(parsed, self.config.unit_max_chars))
        def progress(unit_id, event):
            if on_progress:
                on_progress({"unit_id": unit_id, "event": event, "total_units": len(units),
                    "completed_units": self.completed_units, "failed_units": self.failed_units})
        for unit_id, unit_blocks, unit_issue in units:
            budget.remaining()
            progress(unit_id, "started")
            if unit_issue:
                self.failed_units += 1
                issues.append(unit_id + ":" + unit_issue)
                progress(unit_id, "failed")
                continue
            checkpoint = output_dir / (unit_id + ".json")
            unit_hash = identity("unit", [parsed.model_dump(mode="json"), unit_id,
                implementation_binding(["construction"]),
                self.config.model_dump(mode="json"), self.fields, self.resolver.terminology, model_binding])
            if checkpoint.is_file():
                saved = json.loads(checkpoint.read_text(encoding="utf-8"))
                if saved.get("unit_hash") != unit_hash:
                    raise ValueError("construction_checkpoint_input_changed")
                expected = saved.pop("checkpoint_checksum", None)
                if expected != identity("checkpoint", saved):
                    raise ValueError("construction_checkpoint_checksum_mismatch")
                statements.extend(KnowledgeStatement.model_validate(row) for row in saved["statements"])
                issues.extend(saved["issues"])
                traces.extend(saved["alignment_trace"])
                semantic_units.append(saved["semantic_candidates"])
                self.completed_units += 1
                progress(unit_id, "restored")
                continue
            allowed = {block.block_id for block in unit_blocks}
            edges = [edge for edge in parsed.edges if edge.source in allowed and edge.target in allowed]
            unit_statements, unit_issues, unit_trace = [], [], []
            try:
                extraction = extract(self.llm, unit_id, unit_blocks, edges, budget)
                semantic_unit = {"unit_id": unit_id,
                    "statement_candidates": [candidate.model_dump(mode="json") for candidate in extraction.statements],
                    "condition_candidates": [{**candidate.model_dump(mode="json"),
                        "candidate_id": identity("condition_candidate", [parsed.document.document_id, unit_id, candidate.local_id]),
                        "source_spans": [ref.model_dump(mode="json") for ref in resolve_quotes(candidate.source_refs, full_blocks)]}
                        for candidate in extraction.conditions]}
                semantic_units.append(semantic_unit)
                atomic_json(output_dir / (unit_id + ".candidates.json"), semantic_unit)
                # Local IDs are unique per unit; persistent identities include the unit.
                pairs = candidate_pairs(extraction.statements, extraction.conditions, full_blocks, edges)
                selected = []
                if pairs:
                    alignment = align_candidate_pairs(self.llm, pairs, {
                        "statements": [item.model_dump(mode="json") for item in extraction.statements],
                        "conditions": [item.model_dump(mode="json") for item in extraction.conditions],
                        "relevant_blocks": [block.model_dump(mode="json") for block in unit_blocks],
                        "structure_edges": [edge.model_dump(mode="json") for edge in edges]}, full_blocks, budget)
                    selected, unit_trace = select_bindings(pairs, alignment.decisions,
                        self.config.alignment_weights, self.config.alignment_threshold)
                for candidate in extraction.statements:
                    candidate_issues = []
                    conditions = [condition for condition in extraction.conditions if any(
                        pair.statement_id == candidate.local_id and pair.condition_id == condition.local_id for pair in selected)]
                    tree = None
                    condition_status = candidate.condition_status
                    if conditions:
                        logic = self.llm.execute("L3", {"statement": candidate.model_dump(mode="json"),
                            "selected_conditions": [condition.model_dump(mode="json") for condition in conditions],
                            "relevant_blocks": [block.model_dump(mode="json") for block in unit_blocks],
                            "field_catalog": self.fields, "operator_catalog": OPERATORS}, LogicDraft, budget)
                        logic = number_logic(logic)
                        tree = assemble_logic(logic, candidate.local_id, conditions, full_blocks, self.fields)
                        condition_status = "constrained" if tree else "unresolved"
                    unresolved_pairs = [item for item in unit_trace if item["statement_id"] == candidate.local_id
                                        and item["relation"] == "unresolved"]
                    if unresolved_pairs:
                        tree, condition_status = None, "unresolved"
                        candidate_issues.append("unresolved_condition_assignment")
                    statement = KnowledgeStatement(
                        statement_id=identity("statement", [parsed.document.document_id, unit_id, candidate.model_dump(mode="json")]),
                        subject=self.resolver.resolve(candidate.subject_text, candidate.subject_type, budget),
                        predicate=candidate.predicate,
                        object=self.resolver.resolve(candidate.object_text, candidate.object_type, budget),
                        assertion_text=candidate.assertion_text, condition=tree, condition_status=condition_status,
                        purposes=candidate.purposes, source_refs=resolve_quotes(candidate.source_refs, full_blocks),
                        field_evidence={key: resolve_quotes(refs, full_blocks) for key, refs in candidate.field_evidence.items()},
                        issues=candidate_issues)
                    verification = self.llm.execute("L4", {"statement": statement.model_dump(mode="json"),
                        "selected_conditions": [condition.model_dump(mode="json") for condition in conditions],
                        "field_evidence": statement.model_dump(mode="json")["field_evidence"],
                        "source_blocks": [block.model_dump(mode="json") for block in unit_blocks],
                        "structure_edges": [edge.model_dump(mode="json") for edge in edges]}, VerificationOutput, budget)
                    checked = apply_verification(statement, verification, full_blocks)
                    checked.assertion_language = text_language(checked.assertion_text)
                    if self.config.localize_zh:
                        checked = annotate_statement(checked, full_blocks, self.llm, budget)
                    unit_issues.extend(issue for issue in checked.issues if issue.startswith("localization_"))
                    unit_statements.append(checked)
                candidate_ids = {candidate.local_id: identity("statement", [parsed.document.document_id, unit_id,
                    candidate.model_dump(mode="json")]) for candidate in extraction.statements}
                for item in unit_trace:
                    item["unit_id"] = unit_id
                    item["knowledge_statement_id"] = candidate_ids[item["statement_id"]]
                    item["condition_candidate_id"] = identity("condition_candidate", [parsed.document.document_id,
                        unit_id, item["condition_id"]])
                unit_issues.extend(extraction.issues)
                payload = {"unit_hash": unit_hash,
                    "statements": [item.model_dump(mode="json") for item in unit_statements],
                    "issues": unit_issues, "alignment_trace": unit_trace, "semantic_candidates": semantic_unit}
                payload["checkpoint_checksum"] = identity("checkpoint", payload)
                atomic_json(checkpoint, payload)
                self.completed_units += 1
                progress(unit_id, "completed")
            except TimeoutError:
                raise
            except Exception as exc:
                if isinstance(exc, ValueError) and hasattr(self.llm, "invalidate_last"):
                    self.llm.invalidate_last(exc)
                self.failed_units += 1
                # A failed unit must remain retryable; do not cache it as completed.
                unit_issues.append(unit_id + ":" + type(exc).__name__ + ":" + str(exc)[:120])
                atomic_json(output_dir / (unit_id + ".failed.json"), {"issues": unit_issues})
                progress(unit_id, "failed")
            statements.extend(unit_statements)
            issues.extend(unit_issues)
            traces.extend(unit_trace)
        atomic_json(output_dir / "alignment_trace.json", traces)
        entities = {entity.entity_id: entity.model_dump(mode="json") for statement in statements
                    for entity in [statement.subject, statement.object]}
        atomic_json(output_dir / "semantic_graph.json", {"entities": list(entities.values()),
            "candidate_units": semantic_units,
            "condition_mentions": [{"candidate_id": condition["candidate_id"], "block_id": ref["block_id"]}
                for unit in semantic_units for condition in unit["condition_candidates"] for ref in condition["source_spans"]],
            "statements": [statement.model_dump(mode="json") for statement in statements],
            "mentions": [{"statement_id": statement.statement_id, "block_id": ref.block_id}
                         for statement in statements for ref in statement.source_refs]})
        return statements, issues
