import unittest

from neurogra_kg.construction.layout import column_order, merge_body_lines
from neurogra_kg.contracts.domain import Block


class LayoutTests(unittest.TestCase):
    def block(self, id, x, y, text):
        return Block(block_id=id, document_id="d", page=1, order=0, kind="paragraph", text=text,
                     bbox=(x, y, x + .3, y + .025))

    def test_two_columns_are_not_interleaved(self):
        blocks = [self.block("r1", .55, .1, "R"), self.block("l1", .1, .1, "L"),
                  self.block("l2", .1, .13, "L"), self.block("r2", .55, .13, "R"),
                  self.block("l3", .1, .16, "L"), self.block("r3", .55, .16, "R")]
        self.assertEqual([block.block_id for block in column_order(blocks)], ["l1", "l2", "l3", "r1", "r2", "r3"])

    def test_coalescing_never_crosses_columns(self):
        blocks = [self.block("l1", .1, .1, "left unfinished"), self.block("l2", .1, .13, "continuation."),
                  self.block("r1", .55, .1, "right unfinished"), self.block("r2", .55, .13, "continuation.")]
        # Explicitly ordered with >=3 rows so column detection has sufficient evidence.
        blocks += [self.block("l3", .1, .16, "new paragraph."), self.block("r3", .55, .16, "new paragraph.")]
        merged = merge_body_lines(blocks)
        self.assertTrue(any(block.text == "left unfinished\ncontinuation." for block in merged))
        self.assertFalse(any("left" in block.text and "right" in block.text for block in merged))


if __name__ == "__main__":
    unittest.main()
