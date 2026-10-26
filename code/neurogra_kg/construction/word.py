"""Read OOXML in body order, preserving paragraph/table locators and exact text."""
from zipfile import ZipFile
from xml.etree import ElementTree as ET

from ..contracts.domain import Block, ParsedDocument, TableCell
from ..utils import identity

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


def text_of(element):
    parts = []
    def visit(node):
        if node.tag == W + 'del':
            return
        if node.tag == W + 't':
            parts.append(node.text or '')
        elif node.tag == W + 'tab':
            parts.append('\t')
        elif node.tag in {W + 'br', W + 'cr'}:
            parts.append('\n')
        else:
            for child in node: visit(child)
    visit(element)
    return ''.join(parts)


def parse_docx(document):
    blocks, issues = [], []
    with ZipFile(document.snapshot_path) as package:
        body = ET.fromstring(package.read('word/document.xml')).find(W + 'body')
        if body is None:
            raise ValueError('docx_body_missing')
        styles = {}
        if 'word/styles.xml' in package.namelist():
            for style in ET.fromstring(package.read('word/styles.xml')).findall(W + 'style'):
                outline = style.find(W + 'pPr/' + W + 'outlineLvl')
                parent = style.find(W + 'basedOn')
                styles[style.get(W + 'styleId')] = (
                    int(outline.get(W + 'val')) + 1 if outline is not None else None,
                    parent.get(W + 'val') if parent is not None else None)
        def heading_level(paragraph):
            direct = paragraph.find(W + 'pPr/' + W + 'outlineLvl')
            if direct is not None:
                return min(9, int(direct.get(W + 'val')) + 1)
            style = paragraph.find(W + 'pPr/' + W + 'pStyle')
            name = style.get(W + 'val') if style is not None else None
            visited = set()
            while name and name not in visited:
                visited.add(name)
                if name in {'Title', '标题'}:
                    return 1
                level, name = styles.get(name, (None, None))
                if level is not None and level <= 9:
                    return level
            return None
        def block(locator, kind, text, **fields):
            item = Block(block_id=identity('block', [document.document_id, locator]),
                document_id=document.document_id, page=1, page_kind='unavailable',
                source_locator=locator, order=len(blocks), kind=kind, text=text, **fields)
            blocks.append(item)
            return item
        for index, element in enumerate(body):
            locator = f'word/document.xml:body[{index}]'
            if element.tag == W + 'p':
                text = text_of(element)
                if not text.strip():
                    continue
                level = heading_level(element)
                kind = 'heading' if level else ('list_item' if element.find(W + 'pPr/' + W + 'numPr') is not None else 'paragraph')
                markers = ['fn:' + ref.get(W + 'id') for ref in element.iter(W + 'footnoteReference')]
                block(locator, kind, text, heading_level=level, reference_markers=markers)
            elif element.tag == W + 'tbl':
                cells, lines, active_merges = [], [], {}
                heuristic = False
                for row_index, row in enumerate(element.findall(W + 'tr')):
                    explicit_header = row.find(W + 'trPr/' + W + 'tblHeader') is not None
                    column, values = 0, []
                    for cell in row.findall(W + 'tc'):
                        if cell.find('.//' + W + 'tbl') is not None:
                            issues.append('docx_nested_table_requires_review:' + locator)
                        span = cell.find(W + 'tcPr/' + W + 'gridSpan')
                        width = int(span.get(W + 'val')) if span is not None else 1
                        merged = cell.find(W + 'tcPr/' + W + 'vMerge')
                        text = '\n'.join(text_of(p) for p in cell.findall(W + 'p'))
                        continuing = merged is not None and merged.get(W + 'val') != 'restart'
                        if continuing and column in active_merges:
                            active_merges[column].row_span += 1
                        else:
                            active_merges.pop(column, None)
                        item = TableCell(row=row_index, column=column, text=text, column_span=width,
                            is_header=explicit_header or row_index == 0)
                        if merged is not None and not continuing:
                            active_merges[column] = item
                        elif continuing and column not in active_merges:
                            issues.append('docx_unparsed_object_requires_review:orphan_vertical_merge:' + locator)
                        cells.append(item)
                        values.append(text)
                        column += width
                    lines.append('\t'.join(values))
                    if row_index == 0 and not explicit_header:
                        heuristic = True
                block(locator, 'table', '\n'.join(lines), cells=cells,
                    issues=['first_row_header_heuristic'] if heuristic else [])
            elif element.tag != W + 'sectPr':
                issues.append('docx_unparsed_object_requires_review:' + locator)
        if list(body.iter(W + 'ins')) or list(body.iter(W + 'del')):
            issues.append('docx_tracked_changes_requires_review')
        if list(body.iter(W + 'drawing')) or list(body.iter(W + 'object')):
            issues.append('docx_unparsed_object_requires_review:embedded_media')
        if 'word/footnotes.xml' in package.namelist():
            for note in ET.fromstring(package.read('word/footnotes.xml')).findall(W + 'footnote'):
                id = note.get(W + 'id')
                if int(id) < 1:
                    continue
                text = '\n'.join(text_of(p) for p in note.findall(W + 'p'))
                block('word/footnotes.xml:footnote[' + id + ']', 'footnote', text, marker='fn:' + id)
    return ParsedDocument(document=document, blocks=blocks, parser='ooxml_docx', parser_version='1', issues=issues)
