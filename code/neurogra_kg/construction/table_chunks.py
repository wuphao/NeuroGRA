"""Whole-row table windows with independently sourced header context."""
from ..contracts.domain import Block, StructureEdge
from ..utils import identity


def referenced_rows(block, refs):
    """Resolve quote windows to original row numbers; return header/body sets."""
    from .sources import resolve_quotes
    rows = {}
    for cell in block.cells:
        rows.setdefault(cell.row, []).append(cell)
    windows, cursor = [], 0
    for row, cells in sorted(rows.items()):
        cells.sort(key=lambda cell: cell.column)
        text = "\t".join(cell.text for cell in cells)
        end = cursor + len(text)
        if block.text[cursor:end] != text:
            return set(), set()
        cell_cursor = cursor
        for cell in cells:
            windows.append((cell.row, cell_cursor, cell_cursor + len(cell.text), cell.is_header, cell.row_span))
            cell_cursor += len(cell.text) + 1
        cursor = end + (1 if block.text[end:end+1] == "\n" else 0)
    selected, headers = set(), set()
    for ref in resolve_quotes(refs, {block.block_id: block}):
        for row, start, end, is_header, row_span in windows:
            if ref.start < end and start < ref.end:
                selected.update(range(row, row + row_span))
                if is_header:
                    headers.add(row)
    return selected, headers


def split_table(block, target):
    rows = {}
    for cell in block.cells:
        rows.setdefault(cell.row, []).append(cell)
    if not rows:
        return None
    spans, cursor = [], 0
    for row, cells in sorted(rows.items()):
        cells.sort(key=lambda c: c.column)
        text = "\t".join(cell.text for cell in cells)
        if cursor + len(text) < len(block.text):
            text += "\n"
        if block.text[cursor:cursor + len(text)] != text:
            block.issues.append("table_cell_text_mapping_unresolved")
            return [], []
        spans.append((cursor, cursor + len(text), cells))
        cursor += len(text)
    if cursor != len(block.text):
        block.issues.append("table_cell_text_mapping_unresolved")
        return [], []
    # A merged cell is one scope: never cut between rows covered by its row_span.
    groups, pending, through = [], [], -1
    for index, span in enumerate(spans):
        pending.append(span)
        through = max(through, max(cell.row + cell.row_span - 1 for cell in span[2]))
        next_row = min(cell.row for cell in spans[index + 1][2]) if index + 1 < len(spans) else None
        if next_row is None or next_row > through:
            groups.append((pending[0][0], pending[-1][1], [cell for item in pending for cell in item[2]]))
            pending, through = [], -1
    windows = []
    for start, end, cells in groups:
        if windows and end - windows[-1][0] <= target:
            windows[-1][1] = end
            windows[-1][2].extend(cells)
        else:
            windows.append([start, end, list(cells)])
    fragments, headers, edges = [], [], []
    for start, end, cells in spans:
        if any(cell.is_header for cell in cells):
            header = block.model_copy(deep=True, update={"block_id": identity("header", [block.block_id, start, end]),
                "kind": "table_cell", "table_id": block.block_id, "text": block.text[start:end],
                "fragment_of": block.block_id, "source_start": start, "source_end": end,
                "cells": [cell.model_copy(deep=True) for cell in cells], "issues": ["table_header", "source_fragment"]})
            headers.append(header)
    certainty = "heuristic" if "first_row_header_heuristic" in block.issues else "explicit"
    for start, end, cells in windows:
        fragment = block.model_copy(deep=True, update={"block_id": identity("fragment", [block.block_id, start, end]),
            "table_id": block.block_id, "text": block.text[start:end], "cells": [cell.model_copy(deep=True) for cell in cells],
            "reference_markers": [marker for marker in block.reference_markers
                if marker in block.text[start:end] or any(marker in header.text for header in headers)],
            "fragment_of": block.block_id, "source_start": start, "source_end": end,
            "issues": [issue for issue in block.issues if issue != "container_only"] + ["source_fragment", "whole_table_rows"]})
        fragments.append(fragment)
        for header in headers:
            edges.append(StructureEdge(source=header.block_id, target=fragment.block_id, relation="HEADER_OF",
                certainty=certainty, reason="source_table_header_rows"))
    return fragments + headers, edges
