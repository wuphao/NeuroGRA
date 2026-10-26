import unittest
from neurogra_kg.construction.sources import resolve_quote, validate_ref
from neurogra_kg.contracts.domain import Block
from neurogra_kg.contracts.tasks import QuoteRef


class LayoutQuoteTests(unittest.TestCase):
    def test_chinese_pdf_line_wrap_resolves_to_original_literal_range(self):
        text = '蒙特利尔量表（MoCA⁃B）\n中文版适合低教育程度老人的认知评估'
        block = Block(block_id='b', document_id='d', page=6, order=1, kind='paragraph', text=text)
        ref = resolve_quote(QuoteRef(block_id='b', quote=text.replace('\n', ''), occurrence=0), {'b': block})
        self.assertEqual(ref.quote, text)
        self.assertEqual((ref.start, ref.end), (0, len(text)))
        validate_ref(ref, {'b': block})

    def test_alignment_does_not_repair_numbers_words_glyphs_or_table_rows(self):
        for text, quote, kind in [('1\n0', '10', 'paragraph'), ('no\nX', 'yes X', 'paragraph'),
                                  ('MoCA⁃B', 'MoCA-B', 'paragraph'), ('条件\n结果', '条件结果', 'table'),
                                  ('年龄\n\n标准', '年龄标准', 'paragraph')]:
            with self.subTest(text=text):
                block = Block(block_id='b', document_id='d', page=1, order=0, kind=kind, text=text)
                with self.assertRaisesRegex(ValueError, 'source_quote_not_found'):
                    resolve_quote(QuoteRef(block_id='b', quote=quote, occurrence=0), {'b': block})
