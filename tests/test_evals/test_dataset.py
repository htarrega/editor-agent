import unittest

from evals.dataset import normalise, truncate


class Normalise(unittest.TestCase):
    """Every metric in the harness is computed downstream of this, so a
    regression here moves all the numbers with no visible cause."""

    def test_strips_a_byte_order_mark(self):
        self.assertEqual(normalise("﻿hola"), "hola\n")

    def test_folds_crlf(self):
        self.assertEqual(normalise("a\r\nb"), "a\nb\n")

    def test_replaces_non_breaking_spaces(self):
        self.assertEqual(normalise("a b"), "a b\n")

    def test_drops_trailing_spaces_and_blank_line_runs(self):
        self.assertEqual(normalise("a   \n\n\n\n\nb  "), "a\n\nb\n")

    def test_leaves_a_single_blank_line_alone(self):
        self.assertEqual(normalise("a\n\nb"), "a\n\nb\n")

    def test_is_idempotent(self):
        once = normalise("﻿a  \r\n\n\n\nb ")
        self.assertEqual(normalise(once), once)


class Truncate(unittest.TestCase):
    def test_cuts_at_a_paragraph_boundary(self):
        text = "uno dos tres\n\ncuatro cinco seis\n\nsiete ocho nueve"
        self.assertEqual(truncate(text, 4), "uno dos tres\n\ncuatro cinco seis\n")

    def test_keeps_everything_when_the_limit_is_high(self):
        text = "uno dos\n\ntres cuatro"
        self.assertEqual(truncate(text, 99), text + "\n")


if __name__ == "__main__":
    unittest.main()
