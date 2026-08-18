import unittest

from corrector.edits import (
    Edit,
    ProposedEdit,
    apply_edits,
    diff_edits,
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
