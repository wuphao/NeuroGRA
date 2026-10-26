"""Literal evidence resolution. Reject unknown IDs and unlocatable quotes."""

import re

from ..contracts.domain import Block, SourceRef
from ..contracts.tasks import QuoteRef


def chinese_layout_view(text):
    """Remove Chinese line-wrap breaks while retaining every original position."""
    def cjk(char):
        return '\u3400' <= char <= '\u9fff'
    chars, positions = [], []
    for index, char in enumerate(text):
        if (char == '\n' and 0 < index < len(text) - 1
                and text[index - 1] != '\n' and text[index + 1] != '\n'
                and (cjk(text[index - 1]) or cjk(text[index + 1]))):
            continue
        chars.append(char)
        positions.append(index)
    return ''.join(chars), positions


def english_layout_view(text, join_hyphens=False):
    """Map PDF line wraps/typographic ligatures, preserving original offsets."""
    ligatures = {'ﬀ':'ff','ﬁ':'fi','ﬂ':'fl','ﬃ':'ffi','ﬄ':'ffl','ﬅ':'st','ﬆ':'st'}
    chars, positions = [], []
    index = 0
    while index < len(text):
        char = text[index]
        if (char == '-' and index > 0 and index + 2 < len(text) and text[index + 1] == '\n'
                and text[index - 1].isascii() and text[index - 1].isalpha()
                and text[index + 2].isascii() and text[index + 2].islower()):
            if not join_hyphens:
                chars.append(char);positions.append(index)
            index += 2
            continue
        if (char == '\n' and 0 < index < len(text) - 1
                and text[index-1] != '\n' and text[index+1] != '\n'):
            char = ' '
        for value in ligatures.get(char, char):
            chars.append(value);positions.append(index)
        index += 1
    return ''.join(chars), positions


def mapped_match(view, positions, quote, occurrence):
    cursor, seen = 0, 0
    while quote:
        found = view.find(quote, cursor)
        if found < 0:
            return None
        end = found + len(quote)
        cursor = end
        # A match cannot start or end inside an expanded ligature.
        if (found and positions[found] == positions[found-1]) or (end < len(positions) and positions[end-1] == positions[end]):
            continue
        if seen == occurrence:
            return positions[found], positions[end-1] + 1
        seen += 1
    return None


def mixed_english_wrap_match(text, quote, occurrence):
    """Align independent PDF word wraps without editing words or real spaces."""
    ligatures = {'ﬀ':'ff','ﬁ':'fi','ﬂ':'fl','ﬃ':'ffi','ﬄ':'ffl','ﬅ':'st','ﬆ':'st'}
    view, positions = [], []
    for index, char in enumerate(text):
        for value in ligatures.get(char, char):
            view.append(value); positions.append(index)
    view = ''.join(view)
    quote = ''.join(ligatures.get(char, char) for char in quote)
    parts = []
    for index, char in enumerate(quote):
        # A literal space can represent a single PDF line wrap, never a
        # paragraph break or a run of horizontal whitespace.
        parts.append(r'(?: |(?<!\n)\n(?!\n))' if char == ' ' else re.escape(char))
        following = quote[index + 1] if index + 1 < len(quote) else ''
        if char.isascii() and char.isalpha() and following.isascii() and following.islower():
            parts.append(r'(?:-\n)?')
        elif char == '-' and following.isascii() and following.isalpha():
            # Preserve real hyphens, including non-\nAD; uppercase starts
            # cannot lose the hyphen via the lowercase word-wrap rule.
            parts.append(r'(?:\n)?')
    seen = 0
    for match in re.finditer(''.join(parts), view):
        start, end = match.span()
        if start == end:
            continue
        if (start and positions[start] == positions[start - 1]) or (end < len(positions) and positions[end - 1] == positions[end]):
            continue
        if seen == occurrence:
            return positions[start], positions[end - 1] + 1
        seen += 1
    return None


def resolve_quote(ref: QuoteRef, blocks: dict[str, Block]) -> SourceRef:
    if ref.block_id not in blocks:
        raise ValueError("unknown_source_block:" + ref.block_id)
    text = blocks[ref.block_id].text
    offset = 0
    start = -1
    for _ in range(ref.occurrence + 1):
        start = text.find(ref.quote, offset)
        if start < 0:
            # Only prose layout is aligned; table rows and horizontal spaces
            # are never merged. Stored evidence remains the ORIGINAL substring.
            if blocks[ref.block_id].kind not in {'paragraph', 'list_item'}:
                raise ValueError("source_quote_not_found:" + ref.block_id)
            view, positions = chinese_layout_view(text)
            quote, _ = chinese_layout_view(ref.quote)
            match = mapped_match(view, positions, quote, ref.occurrence)
            if match is None:
                for join in [False, True]:
                    view, positions = english_layout_view(text, join)
                    quote, _ = english_layout_view(ref.quote, join)
                    match = mapped_match(view, positions, quote, ref.occurrence)
                    if match is not None:
                        break
            if match is None:
                match = mixed_english_wrap_match(text, ref.quote, ref.occurrence)
            if match is None:
                raise ValueError("source_quote_not_found:" + ref.block_id)
            start, offset = match
            return SourceRef(block_id=ref.block_id, start=start, end=offset, quote=text[start:offset])
        offset = start + len(ref.quote)
    return SourceRef(block_id=ref.block_id, start=start, end=offset, quote=ref.quote)


def resolve_quotes(refs, blocks):
    return [resolve_quote(ref, blocks) for ref in refs]


def validate_ref(ref: SourceRef, blocks: dict[str, Block]):
    block = blocks.get(ref.block_id)
    if block is None or block.text[ref.start:ref.end] != ref.quote:
        raise ValueError("invalid_source_range:" + ref.block_id)


def condition_leaves(condition):
    if condition is None:
        return []
    if condition.kind in {"ATOM", "TEXT"}:
        return [condition]
    if condition.kind == "NOT":
        return condition_leaves(condition.child)
    return [leaf for child in condition.children for leaf in condition_leaves(child)]
