import unittest

from neurogra.knowledge.processing.segmenter import _split_child_text, count_tokens


class ChunkBoundaryTests(unittest.TestCase):
    def test_long_unpunctuated_text_respects_limit_without_losing_content(self):
        for text in ("认知障碍" * 50, "MRI AD " * 80, "短句。" + "检查记录" * 80 + "。末尾"):
            with self.subTest(text=text[:20]):
                chunks = _split_child_text(text, 16)
                self.assertTrue(all(0 < count_tokens(chunk) <= 16 for chunk in chunks))
                self.assertEqual("".join("".join(chunks).split()), "".join(text.split()))
