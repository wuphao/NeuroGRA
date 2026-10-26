"""Build explicit and heuristic document scope edges without a language model."""

from ..contracts.domain import Block, StructureEdge


def continuation_context(blocks, edges):
    lookup = {block.block_id: block for block in blocks}
    extra = []
    for continuation in edges:
        if continuation.relation != "CONTINUES":
            continue
        previous, current = lookup[continuation.source], lookup[continuation.target]
        if previous.kind != "table" or current.kind != "table" or previous.page == current.page:
            continue
        for edge in edges:
            if edge.relation == "HEADER_OF" and edge.target == previous.block_id:
                extra.append(StructureEdge(source=edge.source, target=current.block_id, relation="HEADER_OF",
                    certainty="heuristic", reason="cross_page_header_context_requires_resolution"))
            if edge.relation in {"NOTE_OF", "REFERS_TO"} and edge.source == previous.block_id:
                extra.append(StructureEdge(source=current.block_id, target=edge.target, relation="NOTE_OF",
                    certainty="heuristic", reason="cross_page_note_context_requires_resolution"))
    return list({(edge.source, edge.target, edge.relation): edge for edge in [*extra, *edges]}.values())


def build_structure(blocks: list[Block]) -> list[StructureEdge]:
    output = []
    headings = []
    notes = {}
    tables = {}
    ordered = sorted(blocks, key=lambda b: (b.page, b.order, b.block_id))
    for block in ordered:
        if block.kind == "footnote" and block.marker:
            notes.setdefault((block.page, block.marker), []).append(block)
        if block.kind == "table":
            tables[block.block_id] = block
    for index, block in enumerate(ordered):
        if index:
            output.append(StructureEdge(source=ordered[index - 1].block_id, target=block.block_id,
                relation="NEXT", certainty="explicit", reason="parser_reading_order"))
        if block.kind == "heading":
            level = block.heading_level or 1
            while headings and (headings[-1].heading_level or 1) >= level:
                headings.pop()
            if headings:
                output.append(StructureEdge(source=headings[-1].block_id, target=block.block_id,
                    relation="CONTAINS", certainty="heuristic", reason="heading_hierarchy"))
            headings.append(block)
        elif headings:
            output.append(StructureEdge(source=headings[-1].block_id, target=block.block_id,
                relation="CONTAINS", certainty="heuristic", reason="heading_scope"))
        block.section_path = [heading.block_id for heading in headings]
        if block.table_id:
            if block.table_id not in tables:
                block.issues.append("unresolved_table_parent")
            else:
                output.append(StructureEdge(source=block.table_id, target=block.block_id,
                    relation="CONTAINS" if block.kind == "table_cell" else "NOTE_OF",
                    certainty="explicit", reason="parser_table_parent"))
        for marker in block.reference_markers:
            candidates = notes.get((block.page, marker), [])
            if len(candidates) == 1:
                output.append(StructureEdge(source=block.block_id, target=candidates[0].block_id,
                    relation="REFERS_TO", certainty="heuristic" if "footnote_type_heuristic" in candidates[0].issues else "explicit",
                    reason="matching_superscript_marker"))
            else:
                block.issues.append("unresolved_footnote_marker:" + marker)
        if block.kind == "table":
            for cell_block in ordered:
                if cell_block.table_id == block.block_id and "table_header" in cell_block.issues:
                    output.append(StructureEdge(source=cell_block.block_id, target=block.block_id,
                        relation="HEADER_OF", certainty="heuristic" if "first_row_header_heuristic" in block.issues else "explicit",
                        reason="parser_table_header"))
        for issue in block.issues:
            if issue.startswith("possible_table_note:"):
                parent = issue.split(":", 1)[1]
                if parent in tables:
                    output.append(StructureEdge(source=parent, target=block.block_id, relation="NOTE_OF",
                        certainty="heuristic", reason="nearby_table_note_candidate"))
    page_tails = {}
    page_heads = {}
    for block in ordered:
        if block.kind in {"paragraph", "list_item", "table"}:
            page_tails[block.page] = block
            page_heads.setdefault(block.page, block)
    for page, tail in page_tails.items():
        head = page_heads.get(page + 1)
        if head is None:
            continue
        continuation = False
        if tail.kind == head.kind == "table":
            tail_cols = max((cell.column for cell in tail.cells), default=-1)
            head_cols = max((cell.column for cell in head.cells), default=-1)
            tail_header = [cell.text.strip() for cell in tail.cells if cell.is_header]
            head_header = [cell.text.strip() for cell in head.cells if cell.is_header]
            boundary = bool(tail.bbox and head.bbox and tail.bbox[3] >= .75 and head.bbox[1] <= .25)
            repeated_header = bool(tail_header and tail_header == head_header)
            continuation = tail_cols == head_cols and tail_cols >= 0 and (boundary or repeated_header)
        elif tail.kind == head.kind == "paragraph":
            continuation = bool(tail.text and not tail.text.rstrip().endswith(("。", ".", "!", "?", "！", "？")))
        if continuation:
            output.append(StructureEdge(source=tail.block_id, target=head.block_id, relation="CONTINUES",
                certainty="heuristic", reason="cross_page_candidate_requires_resolution"))
    # Deduplicate edges while preserving explanations.
    return list({(edge.source, edge.target, edge.relation): edge for edge in output}.values())
