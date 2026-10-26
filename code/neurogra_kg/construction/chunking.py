"""Source-preserving structural fragments, never character-prefix truncation."""

from ..contracts.domain import StructureEdge
from ..utils import identity


def split_document_units(parsed, max_chars):
    target = max(200, max_chars // 2)
    originals = list(parsed.blocks)
    lookup = {block.block_id: block for block in originals}
    additions, added_edges = [], []
    for block in originals:
        if block.fragment_of or "container_only" in block.issues:
            continue
        if block.kind not in {"paragraph", "list_item", "table"} or len(block.text) <= target:
            continue
        block.issues.append("container_only")
        if block.kind == "table":
            from .table_chunks import split_table
            table_result = split_table(block, max(200, max_chars // 3))
            if table_result is not None:
                fragments, header_edges = table_result
                if not fragments:
                    block.issues.remove("container_only")
                    continue
                additions.extend(fragments)
                added_edges.extend(header_edges)
                bodies = [fragment for fragment in fragments if fragment.kind == "table"]
                for index, fragment in enumerate(bodies):
                    added_edges.append(StructureEdge(source=block.block_id, target=fragment.block_id,
                        relation="CONTAINS", certainty="explicit", reason="source_table_row_window"))
                    if index:
                        added_edges.append(StructureEdge(source=bodies[index-1].block_id, target=fragment.block_id,
                            relation="CONTINUES", certainty="explicit", reason="source_table_row_windows"))
                    for edge in parsed.edges:
                        if edge.source == block.block_id and edge.relation in {"REFERS_TO", "NOTE_OF"}:
                            note = lookup.get(edge.target)
                            if edge.relation == "REFERS_TO" and note and note.marker not in fragment.reference_markers:
                                added_edges.append(edge.model_copy(update={"source": fragment.block_id,
                                    "relation": "NOTE_OF", "certainty": "heuristic", "reason": "other_table_rows_note_context"}))
                            else:
                                added_edges.append(edge.model_copy(update={"source": fragment.block_id}))
                        if edge.target == block.block_id and edge.relation in {"HEADER_OF", "NOTE_OF"}:
                            added_edges.append(edge.model_copy(update={"target": fragment.block_id}))
                continue
        start = 0
        previous = None
        while start < len(block.text):
            end = min(start + target, len(block.text))
            if end < len(block.text):
                candidates = [block.text.rfind(separator, start + target // 2, end)
                              for separator in ["\n", "。", ". ", ";", "；"]]
                split = max(candidates)
                if split > start:
                    end = split + 1
            fragment_id = identity("fragment", [block.block_id, start, end])
            fragment = block.model_copy(deep=True, update={"block_id": fragment_id,
                "text": block.text[start:end], "fragment_of": block.block_id,
                "source_start": start, "source_end": end,
                "issues": [issue for issue in block.issues if issue != "container_only"] + ["source_fragment"],
                "cells": []})
            additions.append(fragment)
            added_edges.append(StructureEdge(source=block.block_id, target=fragment_id, relation="CONTAINS",
                                            certainty="explicit", reason="source_character_window"))
            if previous:
                added_edges.append(StructureEdge(source=previous, target=fragment_id, relation="CONTINUES",
                                                certainty="explicit", reason="same_source_block_fragment"))
            for edge in parsed.edges:
                if edge.source == block.block_id and edge.relation in {"REFERS_TO", "NOTE_OF"}:
                    added_edges.append(edge.model_copy(update={"source": fragment_id}))
                if edge.target == block.block_id and edge.relation == "HEADER_OF":
                    added_edges.append(edge.model_copy(update={"target": fragment_id}))
            previous = fragment_id
            start = end
    parsed.blocks.extend(additions)
    parsed.edges.extend(added_edges)
    return parsed
