"""Column-aware reading order and conservative line coalescing."""

import re

from ..utils import identity


def column_order(blocks):
    def by_y(values):
        return sorted(values, key=lambda b: (b.bbox[1] if b.bbox else 0, b.bbox[0] if b.bbox else 0,
                                             b.kind != "table"))
    narrow = [block for block in blocks if block.bbox and block.bbox[2] - block.bbox[0] < .55]
    left = [block for block in narrow if block.bbox[0] < .45]
    right = [block for block in narrow if block.bbox[0] >= .45]
    if len(left) < 3 or len(right) < 3:
        return by_y(blocks)
    # Full-width objects delimit column regions rather than interleaving every line.
    wide = by_y([block for block in blocks if block not in narrow])
    remaining = list(narrow)
    ordered = []
    for boundary in wide:
        region = [block for block in remaining if block.bbox[1] < boundary.bbox[1]]
        ordered.extend(by_y([block for block in region if block.bbox[0] < .45]))
        ordered.extend(by_y([block for block in region if block.bbox[0] >= .45]))
        remaining = [block for block in remaining if block not in region]
        ordered.append(boundary)
    ordered.extend(by_y([block for block in remaining if block.bbox[0] < .45]))
    ordered.extend(by_y([block for block in remaining if block.bbox[0] >= .45]))
    return ordered


def merge_body_lines(blocks):
    ordered = column_order(blocks)
    output = []
    for block in ordered:
        previous = output[-1] if output else None
        join = False
        if previous and previous.kind == block.kind == "paragraph" and previous.bbox and block.bbox:
            a, b = previous.bbox, block.bbox
            overlap = min(a[2], b[2]) - max(a[0], b[0])
            same_column = overlap > .6 * min(a[2] - a[0], b[2] - b[0])
            same_column = same_column and (a[0] < .45) == (b[0] < .45)
            next_is_item = bool(re.match(r"^(?:[一二三四五六七八九十]+、|\d+[.、)）]|[（(]\d+[)）])", block.text))
            ended = previous.text.rstrip().endswith(("。", ".", "！", "？", "!", "?", "：", ":"))
            join = same_column and -.04 <= b[1] - a[3] <= .012 and not ended and not next_is_item
            join = join and not any(flag in block.issues + previous.issues for flag in ["running_header", "page_number"])
        if join:
            previous.block_id = identity("merged", [previous.block_id, block.block_id])
            previous.text += "\n" + block.text
            previous.bbox = (min(previous.bbox[0], block.bbox[0]), min(previous.bbox[1], block.bbox[1]),
                             max(previous.bbox[2], block.bbox[2]), max(previous.bbox[3], block.bbox[3]))
            previous.reference_markers = sorted(set(previous.reference_markers + block.reference_markers))
            previous.issues = sorted(set(previous.issues + block.issues + ["layout_coalesced"]))
        else:
            output.append(block.model_copy(deep=True))
    for index, block in enumerate(output):
        block.order = index
    return output
