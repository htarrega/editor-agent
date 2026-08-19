import unittest

from corrector.blocks import block_spans
from corrector.correct import render
from corrector.edits import ProposedEdit, line_spans, resolve_edits

PARAGRAPH = (
    "El vasu de sidra estaba en la mesa. Fuera llovía sin ganas. "
    "Nadie dijo nada durante un rato largo. Luego se marchó."
)
TEXT = f"{PARAGRAPH}\n\n—Dijistes que vendrías —dijo el vasu.\n"


def texts(text, spans):
    return [text[start:end] for start, end in spans]


class Default(unittest.TestCase):
    """Without a budget the numbering has to be the one H1 measured."""

    def test_a_block_is_a_line(self):
        spans = block_spans(TEXT)
        self.assertEqual(texts(TEXT, spans), TEXT.split("\n")[:-1])

    def test_trailing_blank_lines_are_dropped(self):
        self.assertEqual(block_spans("hola\n\n\n"), [(0, 4)])

    def test_interior_blank_lines_keep_their_number(self):
        self.assertEqual(texts(TEXT, block_spans(TEXT))[1], "")

    def test_max_words_leaves_short_lines_alone(self):
        self.assertEqual(block_spans(TEXT, 50), block_spans(TEXT))


class Cutting(unittest.TestCase):
    def test_a_long_line_is_cut_into_blocks_within_budget(self):
        spans = block_spans(TEXT, 8)
        self.assertGreater(len(spans), len(block_spans(TEXT)))
        for start, end in spans:
            self.assertLessEqual(len(TEXT[start:end].split()), 8)

    def test_blocks_are_whole_sentences(self):
        for piece in texts(TEXT, block_spans(TEXT, 8)):
            self.assertFalse(piece and piece[0].isspace(), piece)
            self.assertIn(piece[-1:] or ".", ".!?…»\"'")

    def test_a_sentence_longer_than_the_budget_stays_whole(self):
        text = "Una sola frase muy larga que no termina hasta el final del todo.\n"
        self.assertEqual(block_spans(text, 3), block_spans(text))

    def test_a_line_with_no_boundary_goes_through_whole(self):
        text = "—Vamos —dijo ella, y nadie la siguió por la calle mojada\n"
        self.assertEqual(block_spans(text, 3), block_spans(text))

    def test_closing_quote_travels_with_the_period(self):
        text = "Dijo «vámonos». Nadie se movió.\n"
        self.assertEqual(texts(text, block_spans(text, 3))[0], "Dijo «vámonos».")

    def test_cutting_never_loses_a_word(self):
        spans = block_spans(TEXT, 8)
        self.assertEqual(" ".join(texts(TEXT, spans)).split(), TEXT.split())


class SameNumbering(unittest.TestCase):
    """What the prompt numbers and what an anchor resolves inside are one thing."""

    def test_render_numbers_the_spans_it_is_given(self):
        spans = block_spans(TEXT, 8)
        rendered = render(TEXT, spans)
        for number, (start, end) in enumerate(spans, 1):
            self.assertIn(f"[{number}]\n{TEXT[start:end]}", rendered)

    def test_an_anchor_resolves_inside_its_recut_block(self):
        spans = block_spans(TEXT, 8)
        block = next(n for n, (s, e) in enumerate(spans, 1) if "llovía" in TEXT[s:e])
        edits, rejected = resolve_edits(
            TEXT, [ProposedEdit(original="llovía", replacement="llovia", line=block)], spans
        )
        self.assertEqual(rejected, [])
        self.assertEqual(TEXT[edits[0].start : edits[0].end], "llovía")

    def test_a_line_number_against_recut_spans_would_land_elsewhere(self):
        """Why the spans travel with the render instead of being recomputed.

        Under line numbering «vasu» sits in block 1; once the paragraph is cut
        it does not, and resolving the old number against the new spans is the
        drift the shared spans exist to prevent.
        """
        recut = block_spans(TEXT, 8)
        self.assertNotEqual(recut[0], line_spans(TEXT)[0])
