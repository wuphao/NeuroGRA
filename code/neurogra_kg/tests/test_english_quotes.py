import unittest
from neurogra_kg.construction.sources import resolve_quote, validate_ref
from neurogra_kg.contracts.domain import Block
from neurogra_kg.contracts.tasks import QuoteRef


class EnglishQuoteTests(unittest.TestCase):
    def block(self,text,kind='paragraph'):
        return Block(block_id='b',document_id='d',page=1,order=0,kind=kind,text=text)
    def resolve(self,text,quote,occurrence=0,kind='paragraph'):
        block=self.block(text,kind)
        ref=resolve_quote(QuoteRef(block_id='b',quote=quote,occurrence=occurrence),{'b':block})
        validate_ref(ref,{'b':block})
        return ref
    def test_line_wrapping_preserves_literal_evidence(self):
        text='Clinical criteria remain\nthe cornerstone.'
        ref=self.resolve(text,'remain the cornerstone')
        self.assertEqual(ref.quote,'remain\nthe cornerstone')
        self.assertEqual(self.resolve('no\nX','no X').quote,'no\nX')
    def test_ligatures_and_hyphenated_wraps_preserve_original(self):
        text='speciﬁcity and ad-\nvanced imaging'
        ref=self.resolve(text,'specificity and advanced imaging')
        self.assertEqual(ref.quote,text)
        self.assertEqual(self.resolve('patho-\nphysiology','patho-physiology').quote,'patho-\nphysiology')
    def test_ligature_partial_match_is_rejected(self):
        for quote in ['f','i']:
            with self.assertRaises(ValueError):self.resolve('ﬁ',quote)
    def test_changes_to_words_numbers_horizontal_spaces_and_table_rows_are_rejected(self):
        for text,quote,kind in [('age 18','age 19','paragraph'),('not present','present only','paragraph'),
                                ('notable','no table','paragraph'),('a  b','a b','paragraph'),
                                ('a\nb','a b','table')]:
            with self.assertRaises(ValueError):self.resolve(text,quote,kind=kind)
    def test_occurrence_maps_repeated_layout_quotes(self):
        ref=self.resolve('core\ncriteria; core\ncriteria','core criteria',1)
        self.assertEqual(ref.start,15)

    def test_uppercase_compound_keeps_hyphen_and_original_offsets(self):
        self.assertEqual(self.resolve('a non-\nAD phenotype', 'a non-AD phenotype').quote, 'a non-\nAD phenotype')
        with self.assertRaises(ValueError): self.resolve('a non-\nAD phenotype', 'a nonAD phenotype')

    def test_mixed_word_wrap_renderings_in_one_quote(self):
        text = 'magnetic resonance imag-\ning, cerebrospinal ﬂuid (that we re-\nfer to) in decision-\nmaking.'
        quote = 'magnetic resonance imaging, cerebrospinal fluid (that we re- fer to) in decision- making.'
        self.assertEqual(self.resolve(text, quote).quote, text)
        self.assertEqual(self.resolve('syn-\ndrome and syn-\ndrome', 'syn- drome and syndrome').quote, 'syn-\ndrome and syn-\ndrome')

    def test_mixed_wraps_do_not_join_paragraphs_or_invent_ellipsis(self):
        for text, quote in [('ad-\nvanced\n\nimaging', 'advanced imaging'),
                            ('a non-\nAD phenotype', 'a non-AD ... phenotype'),
                            ('re- fer', 'refer'), ('ad-\nvanced  imaging', 'advanced imaging')]:
            with self.assertRaises(ValueError): self.resolve(text, quote)

if __name__=='__main__':unittest.main()
