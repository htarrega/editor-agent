import unittest

from corrector.edits import Edit
from evals import metrics

TEXT = "El gato corrio hacia el rio y no se paro."
#       0123456789...        corrio -> [8,14)   rio -> [24,27)
GOLD = [
    Edit(start=13, end=14, replacement="ó", kind="tilde"),
    Edit(start=25, end=26, replacement="í", kind="tilde"),
]


class Scoring(unittest.TestCase):
    def test_perfect_prediction(self):
        result = metrics.score(TEXT, GOLD, list(GOLD))
        self.assertEqual(result.overall.precision, 1.0)
        self.assertEqual(result.overall.recall, 1.0)
        self.assertEqual(result.overall.f_beta(0.5), 1.0)

    def test_no_prediction_is_zero_recall_and_no_false_positives(self):
        result = metrics.score(TEXT, GOLD, [])
        self.assertEqual(result.overall.recall, 0.0)
        self.assertEqual(result.overall.fp, 0)
        self.assertEqual(result.overall.fn, 2)

    def test_wrong_correction_on_the_right_span_is_a_miss(self):
        wrong = [Edit(start=13, end=14, replacement="a"), GOLD[1]]
        result = metrics.score(TEXT, GOLD, wrong)
        self.assertEqual(result.overall.tp_gold, 1)
        self.assertEqual(result.overall.fp, 1)
        self.assertEqual(result.overall.fn, 1)

    def test_overcorrection_counts_as_a_false_positive(self):
        noisy = GOLD + [Edit(start=3, end=7, replacement="minino", kind="estilo")]
        result = metrics.score(TEXT, GOLD, noisy)
        self.assertEqual(result.overall.recall, 1.0)
        self.assertEqual(result.overall.fp, 1)
        self.assertAlmostEqual(result.overall.precision, 2 / 3)

    def test_a_wider_edit_matching_the_gold_result_still_counts(self):
        wide = [Edit(start=8, end=14, replacement="corrió"), GOLD[1]]
        result = metrics.score(TEXT, GOLD, wide)
        self.assertEqual(result.overall.tp_gold, 2)
        self.assertEqual(result.overall.fp, 0)

    def test_split_edits_covering_one_gold_still_count(self):
        text = "no lo se"
        gold = [Edit(start=6, end=8, replacement="sé", kind="tilde_diacritica")]
        split = [
            Edit(start=6, end=7, replacement="s"),
            Edit(start=7, end=8, replacement="é"),
        ]
        result = metrics.score(text, gold, split)
        self.assertEqual(result.overall.tp_gold, 1)
        self.assertEqual(result.overall.fp, 0)

    def test_a_matched_prediction_is_credited_to_the_gold_type(self):
        """Systems that do not use our taxonomy label everything "otro". Their
        hits still have to land in the row of the type they actually fixed, or
        per-type precision means nothing for the baselines."""
        untyped = [Edit(start=13, end=14, replacement="ó", kind="otro"), GOLD[1]]
        result = metrics.score(TEXT, GOLD, untyped)
        self.assertEqual(result.by_kind["tilde"].precision, 1.0)
        self.assertEqual(result.by_kind["tilde"].n_pred, 2)
        self.assertNotIn("otro", result.by_kind)

    def test_per_type_recall_uses_the_gold_type(self):
        gold = GOLD + [Edit(start=1, end=2, replacement="L", kind="mayuscula")]
        result = metrics.score(TEXT, gold, list(GOLD))
        self.assertEqual(result.by_kind["tilde"].recall, 1.0)
        self.assertEqual(result.by_kind["mayuscula"].recall, 0.0)


class FalsePositives(unittest.TestCase):
    def test_counts_by_predicted_type(self):
        counts = metrics.false_positives(
            [
                Edit(start=0, end=1, replacement="x", kind="tilde"),
                Edit(start=2, end=3, replacement="y", kind="inventado"),
            ]
        )
        self.assertEqual(counts, {"tilde": 1, "otro": 1})


class ClusterBridging(unittest.TestCase):
    """A predicted edit that spans two gold edits merges them into one
    all-or-nothing cluster. Deliberate — you cannot apply both — but it means a
    chatty system loses credit for corrections it got right."""

    def test_a_bridging_overcorrection_costs_both_edits_it_spans(self):
        # Reaches from the first gold edit to the second, chaining all three
        # into one cluster that no longer renders the same text.
        noisy = list(GOLD) + [Edit(start=14, end=25, replacement=" HACIA EL ")]
        result = metrics.score(TEXT, GOLD, noisy)
        self.assertEqual(result.overall.tp_gold, 0)
        self.assertEqual(result.overall.fn, 2)

    def test_an_overcorrection_that_reaches_only_one_costs_only_that_one(self):
        noisy = list(GOLD) + [Edit(start=14, end=24, replacement=" HACIA EL ")]
        result = metrics.score(TEXT, GOLD, noisy)
        self.assertEqual(result.overall.tp_gold, 1)
        self.assertEqual(result.overall.fn, 1)


class Stylometry(unittest.TestCase):
    def test_identical_text_has_no_voice_drift(self):
        self.assertEqual(metrics.voice_distance(TEXT, TEXT), 0.0)

    def test_flattening_punctuation_shows_up(self):
        original = "Corrió, sin mirar, hacia el río; y calló."
        flattened = "Corrió sin mirar hacia el río y calló."
        self.assertGreater(metrics.voice_distance(original, flattened), 0.0)

    def test_features_are_finite_on_empty_text(self):
        for value in metrics.features("").values():
            self.assertEqual(value, 0.0)


if __name__ == "__main__":
    unittest.main()
