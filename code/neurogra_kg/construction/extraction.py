"""Structured extraction units include relevant headings and footnotes."""

from ..catalogs import ENTITY_TYPES, PURPOSES, RELATIONS
from ..contracts.tasks import ExtractionDraft, ExtractionOutput, ExtractedStatement, ExtractedCondition
from ..utils import identity
from .sources import resolve_quotes


def extraction_units(parsed, max_chars):
    blocks = {block.block_id: block for block in parsed.blocks}
    bodies = [block for block in parsed.blocks if block.kind in {"paragraph", "list_item", "table"}
              and not set(block.issues) & {"container_only", "running_header", "page_number", "bibliography"}]
    for body in bodies:
        if "table_cell_text_mapping_unresolved" in body.issues:
            yield body.block_id, None, "table_cell_text_mapping_unresolved"
            continue
        selected = {body.block_id, *body.section_path}
        for edge in parsed.edges:
            if edge.source == body.block_id and edge.relation in {"REFERS_TO", "CONTAINS", "NOTE_OF"}:
                selected.add(edge.target)
            if edge.target == body.block_id and edge.relation in {"NOTE_OF", "HEADER_OF"}:
                selected.add(edge.source)
            if edge.target == body.block_id and edge.relation == "CONTINUES":
                previous = blocks.get(edge.source)
                if previous and previous.page == body.page:
                    continue  # Same-block fragments already retain full source windows.
                if previous and previous.kind == "table":
                    selected.update(header.source for header in parsed.edges
                        if header.target == previous.block_id and header.relation == "HEADER_OF")
                if previous and "container_only" not in previous.issues:
                    selected.add(previous.block_id)
                elif previous:
                    fragments = [block for block in parsed.blocks if block.fragment_of == previous.block_id and block.kind == previous.kind]
                    if fragments:
                        selected.add(max(fragments, key=lambda block: block.source_end or 0).block_id)
        # Nearby unresolved notes are candidate context, not already attached conditions.
        for block in parsed.blocks:
            if block.page == body.page and block.kind == "footnote" and abs(block.order - body.order) <= 3:
                selected.add(block.block_id)
        values = [blocks[key] for key in selected if key in blocks]
        values.sort(key=lambda block: (block.page, block.order))
        if sum(len(block.text) for block in values) > max_chars:
            # Refuse rather than discard conditions or alter source offsets.
            yield body.block_id, None, "structural_unit_over_budget_requires_split"
        else:
            yield body.block_id, values, None


def extract(llm, unit_id, blocks, edges, budget):
    payload = {"unit_id": unit_id, "blocks": [block.model_dump(mode="json") for block in blocks],
               "structure_edges": [edge.model_dump(mode="json") for edge in edges],
               "relation_catalog": RELATIONS, "purpose_catalog": PURPOSES, "entity_types": ENTITY_TYPES}
    draft = llm.execute("L1", payload, ExtractionDraft, budget)
    # Assign candidate identities after parsing; no local IDs originate in the model.
    scope = [unit_id, [block.model_dump(mode="json") for block in blocks]]
    output = ExtractionOutput(
        statements=[ExtractedStatement(**row.model_dump(), local_id=identity(
            "statement_candidate", [scope, index, row.model_dump(mode="json")]))
            for index, row in enumerate(draft.statements)],
        conditions=[ExtractedCondition(**row.model_dump(), local_id=identity(
            "condition_candidate", [scope, index, row.model_dump(mode="json")]))
            for index, row in enumerate(draft.conditions)], issues=draft.issues)
    lookup = {block.block_id: block for block in blocks}
    ids = [item.local_id for item in output.statements + output.conditions]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate_extraction_local_id")
    for item in output.statements + output.conditions:
        resolve_quotes(item.source_refs, lookup)
    for statement in output.statements:
        if not {"subject", "relation", "object"} <= set(statement.field_evidence):
            raise ValueError("missing_statement_field_evidence")
        for refs in statement.field_evidence.values():
            resolve_quotes(refs, lookup)
    return output
