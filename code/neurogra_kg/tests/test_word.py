import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from neurogra_kg.construction.parsing import DocumentParser
from neurogra_kg.contracts.domain import DocumentInput


class WordTests(unittest.TestCase):
    def parse(self, body, styles=''):
        namespace = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'input.docx'
            with ZipFile(path, 'w') as package:
                package.writestr('word/document.xml', f'<w:document xmlns:w="{namespace}"><w:body>{body}</w:body></w:document>')
                package.writestr('word/styles.xml', f'<w:styles xmlns:w="{namespace}">{styles}</w:styles>')
            return DocumentParser(root / 'data').parse(DocumentInput(path=path))

    def test_order_exact_text_heading_table_and_locators(self):
        parsed = self.parse('''<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>诊断</w:t></w:r></w:p>
            <w:p><w:r><w:t>甲</w:t><w:tab/><w:t>乙</w:t><w:br/><w:t>丙</w:t></w:r></w:p>
            <w:tbl><w:tr><w:trPr><w:tblHeader/></w:trPr><w:tc><w:tcPr><w:gridSpan w:val="2"/></w:tcPr><w:p><w:r><w:t>检查</w:t></w:r></w:p></w:tc></w:tr>
            <w:tr><w:tc><w:p><w:r><w:t>阳性</w:t></w:r></w:p></w:tc></w:tr></w:tbl>''',
            '<w:style w:styleId="Heading1"><w:pPr><w:outlineLvl w:val="0"/></w:pPr></w:style>')
        self.assertEqual([b.kind for b in parsed.blocks], ['heading', 'paragraph', 'table'])
        self.assertEqual(parsed.blocks[1].text, '甲\t乙\n丙')
        self.assertEqual(parsed.blocks[2].cells[0].column_span, 2)
        self.assertTrue(parsed.blocks[2].cells[0].is_header)
        self.assertEqual(parsed.blocks[2].source_locator, 'word/document.xml:body[2]')
        self.assertTrue(all(b.page_kind == 'unavailable' for b in parsed.blocks))
        self.assertEqual(parsed.issues, [])

    def test_unparsed_objects_and_revisions_require_review(self):
        parsed = self.parse('''<w:p><w:r><w:t>原文</w:t></w:r><w:del><w:r><w:delText>删除</w:delText></w:r></w:del><w:r><w:drawing/></w:r></w:p><w:sdt/>''')
        self.assertEqual(parsed.blocks[0].text, '原文')
        self.assertIn('docx_tracked_changes_requires_review', parsed.issues)
        self.assertIn('docx_unparsed_object_requires_review:embedded_media', parsed.issues)
