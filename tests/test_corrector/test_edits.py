import unittest

from corrector.edits import (
    Edit,
    ProposedEdit,
    apply_edits,
    diff_edits,
    line_spans,
    partition_edits,
    resolve_edits,
    trim,
)


class ApplyEdits(unittest.TestCase):
    def test_splices_in_order(self):
        text = "el gato duerme"
        edits = [
            Edit(start=0, end=2, replacement="La"),
            Edit(start=3, end=7, replacement="gata"),
        ]
        result, rejected = apply_edits(text, edits)
        self.assertEqual(result, "La gata duerme")
        self.assertEqual(rejected, [])

    def test_drops_overlapping(self):
        text = "el gato"
        edits = [
            Edit(start=0, end=2, replacement="La"),
            Edit(start=1, end=7, replacement="a gata"),
        ]
        result, rejected = apply_edits(text, edits)
        self.assertEqual(result, "La gato")
        self.assertEqual([r.reason for r in rejected], ["overlapping"])

    def test_drops_out_of_bounds(self):
        result, rejected = apply_edits("hola", [Edit(start=2, end=99, replacement="x")])
        self.assertEqual(result, "hola")
        self.assertEqual([r.reason for r in rejected], ["out_of_bounds"])


class PartitionEdits(unittest.TestCase):
    """The split `apply_edits` is built on: same decisions, but the survivors
    come back as edits a caller can inspect, not just as spliced text."""

    def test_accepts_non_overlapping_in_order(self):
        text = "el gato duerme"
        edits = [
            Edit(start=3, end=7, replacement="gata"),
            Edit(start=0, end=2, replacement="La"),
        ]
        accepted, rejected = partition_edits(text, edits)
        self.assertEqual([(e.start, e.end) for e in accepted], [(0, 2), (3, 7)])
        self.assertEqual(rejected, [])

    def test_rejects_match_what_apply_edits_drops(self):
        text = "el gato"
        edits = [
            Edit(start=0, end=2, replacement="La"),
            Edit(start=1, end=7, replacement="a gata"),
        ]
        accepted, rejected = partition_edits(text, edits)
        self.assertEqual(len(accepted), 1)
        self.assertEqual([r.reason for r in rejected], ["overlapping"])


class ResolveEdits(unittest.TestCase):
    def test_unique_anchor(self):
        edits, rejected = resolve_edits(
            "el vasu de sidra", [ProposedEdit(original="vasu", replacement="vaso")]
        )
        self.assertEqual(rejected, [])
        self.assertEqual((edits[0].start, edits[0].end), (3, 7))

    def test_missing_anchor_is_discarded_and_recorded(self):
        edits, rejected = resolve_edits(
            "el vasu", [ProposedEdit(original="copa", replacement="vaso")]
        )
        self.assertEqual(edits, [])
        self.assertEqual(rejected[0].reason, "anchor_not_found")

    def test_ambiguous_anchor_is_discarded(self):
        edits, rejected = resolve_edits(
            "vasu y vasu", [ProposedEdit(original="vasu", replacement="vaso")]
        )
        self.assertEqual(edits, [])
        self.assertEqual(rejected[0].reason, "anchor_ambiguous")


class LineScopedAnchors(unittest.TestCase):
    """A word is ambiguous in a chapter and unique in its paragraph. The line
    is what makes an anchor short enough to be worth emitting."""

    TEXT = "el vasu de sidra\nel vasu de agua\n"

    def test_line_picks_between_repeated_anchors(self):
        edits, rejected = resolve_edits(
            self.TEXT, [ProposedEdit(original="vasu", replacement="vaso", line=2)]
        )
        self.assertEqual(rejected, [])
        self.assertEqual(edits[0].start, self.TEXT.index("\n") + 4)

    def test_a_wrong_line_still_resolves_a_unique_anchor(self):
        # The line is a hint, not a claim: unique in the text is unambiguous
        # whatever line the model thought it was on.
        edits, rejected = resolve_edits(
            "el vasu de sidra\nel vaso de agua\n",
            [ProposedEdit(original="vasu", replacement="vaso", line=2)],
        )
        self.assertEqual(rejected, [])
        self.assertEqual(edits[0].start, 3)

    def test_out_of_range_line_falls_back_to_the_text(self):
        edits, _ = resolve_edits(
            "el vasu\n", [ProposedEdit(original="vasu", replacement="vaso", line=99)]
        )
        self.assertEqual(edits[0].start, 3)

    def test_repeated_within_the_named_line_is_still_ambiguous(self):
        edits, rejected = resolve_edits(
            "vasu y vasu\n", [ProposedEdit(original="vasu", replacement="vaso", line=1)]
        )
        self.assertEqual(edits, [])
        self.assertEqual(rejected[0].reason, "anchor_ambiguous")


class LineSpans(unittest.TestCase):
    def test_spans_slice_back_to_the_lines(self):
        text = "una\n\ntres\n"
        self.assertEqual([text[a:b] for a, b in line_spans(text)], ["una", "", "tres", ""])

    def test_a_text_without_newlines_is_one_line(self):
        self.assertEqual(line_spans("hola"), [(0, 4)])


class DiffEdits(unittest.TestCase):
    def test_round_trip(self):
        source = "El picao asoma. La sidra es onda y pareja.\n\nY el dejo no se nota."
        target = "El picado asoma. La sidra es honda y pareja.\n\nY el dejo no se nota."
        rebuilt, rejected = apply_edits(source, diff_edits(source, target))
        self.assertEqual(rebuilt, target)
        self.assertEqual(rejected, [])

    def test_spans_are_minimal(self):
        edits = diff_edits("corrio hacia el rio", "corrió hacia el río")
        self.assertEqual(len(edits), 2)
        self.assertEqual([e.replacement for e in edits], ["ó", "í"])

    def test_identical_text_yields_nothing(self):
        self.assertEqual(diff_edits("sin cambios", "sin cambios"), [])


class Trim(unittest.TestCase):
    def test_shrinks_to_the_changing_span(self):
        text = "tambien"
        edit = trim(text, Edit(start=0, end=7, replacement="también"))
        self.assertEqual((edit.start, edit.end, edit.replacement), (5, 6, "é"))


if __name__ == "__main__":
    unittest.main()
