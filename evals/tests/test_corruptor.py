import unittest

from corrector.taxonomy import ERROR_TYPES
from evals import corruptor
from evals.dataset import load_fragments

SAMPLE = (
    "El indiano quiere limpio, dijo que la francesa tenía razón. ¿Puedes odiar a "
    "una mujer cuyo nombre no sabes pronunciar? Olaya alzó el vaso hacia el "
    "ventanal y le dijo a Madeleine que no se vendía. Ella no se dio cuenta de "
    "que el fallo estaba ahí. «Esto no se vende», repitió más tarde.\n"
    "—Aquí, Olaya —dijo Madeleine—. El picado asoma.\n"
    "Tú lo notas, contestó ella, y también supo que era verdad porque lo había "
    "probado. Le preguntaste al maestro y él no dijo nada.\n"
)


class RoundTrip(unittest.TestCase):
    """The invariant that makes the gold trustworthy."""

    def test_gold_restores_the_clean_text(self):
        for seed in range(6):
            for rate in (0.01, 0.03, 0.08):
                with self.subTest(seed=seed, rate=rate):
                    result = corruptor.corrupt(SAMPLE, rate=rate, seed=seed)
                    self.assertTrue(corruptor.restores_clean(result))

    def test_round_trip_holds_on_the_real_corpus(self):
        try:
            fragments = load_fragments()
        except FileNotFoundError:
            self.skipTest("el corpus vive fuera del repositorio")
        for fragment in fragments:
            for seed in range(3):
                with self.subTest(fragment=fragment.name, seed=seed):
                    result = corruptor.corrupt(fragment.text, rate=0.02, seed=seed)
                    self.assertTrue(corruptor.restores_clean(result))
                    self.assertNotEqual(result.text, result.clean)
                    self.assertGreater(len(result.gold), 0)

    def test_each_rule_round_trips_on_its_own(self):
        for kind in corruptor.RULES:
            with self.subTest(kind=kind):
                result = corruptor.corrupt(SAMPLE, rate=0.2, seed=1, kinds={kind})
                self.assertTrue(corruptor.restores_clean(result))
                for edit in result.gold:
                    self.assertEqual(edit.kind, kind)


class Determinism(unittest.TestCase):
    def test_same_seed_same_corruption(self):
        a = corruptor.corrupt(SAMPLE, rate=0.05, seed=7)
        b = corruptor.corrupt(SAMPLE, rate=0.05, seed=7)
        self.assertEqual(a.text, b.text)
        self.assertEqual(a.gold, b.gold)

    def test_different_seeds_differ(self):
        a = corruptor.corrupt(SAMPLE, rate=0.05, seed=1)
        b = corruptor.corrupt(SAMPLE, rate=0.05, seed=2)
        self.assertNotEqual(a.text, b.text)


class Coverage(unittest.TestCase):
    def test_types_are_spread_rather_than_dominated_by_one(self):
        result = corruptor.corrupt(SAMPLE, rate=0.15, seed=3)
        counts = result.counts_by_kind()
        self.assertGreaterEqual(len(counts), 5)
        self.assertLessEqual(max(counts.values()), 3)

    def test_every_rule_kind_is_in_the_taxonomy(self):
        """H4 pairs each new rule with a new error type. Without this, adding a
        rule and forgetting the taxonomy makes metrics bucket that type into
        "otro" — its false positives vanish from the row it was added for."""
        self.assertEqual(set(corruptor.RULES) - set(ERROR_TYPES), set())


if __name__ == "__main__":
    unittest.main()
