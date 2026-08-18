import json
import pathlib
import tempfile
import unittest

from evals import reuse
from evals.corruptor import CorruptedText

CONFIG = {
    "fragments": ["carta", "sidra"],
    "seed": 0,
    "rate": 0.02,
    "repeats": 1,
    "limit_words": None,
    "skip_clean": False,
}


def case(name="carta", clean="texto limpio", text="testo limpio"):
    return CorruptedText(name=name, clean=clean, text=text, gold=[])


def corpus(cases=(case(),), seeded=None):
    return {
        "cases": len(cases),
        "fingerprint": reuse.fingerprint(cases),
        "seeded_by_kind": seeded if seeded is not None else {"tilde": 3},
    }


def report(systems, config=None, body=None):
    return {
        "config": dict(CONFIG) | (config or {}),
        "corpus": body if body is not None else corpus(),
        "systems": {name: {"overall": {"f05": 0.5}} for name in systems},
    }


class Fingerprint(unittest.TestCase):
    """The one thing standing between reuse and a table that compares two
    different corpora as if they were one."""

    def test_same_cases_hash_the_same(self):
        self.assertEqual(reuse.fingerprint([case()]), reuse.fingerprint([case()]))

    def test_a_different_corruption_shows(self):
        self.assertNotEqual(
            reuse.fingerprint([case(text="testo limpio")]),
            reuse.fingerprint([case(text="texto linpio")]),
        )

    def test_an_edited_fragment_shows(self):
        # Corpus B is the clean text, so a typo fixed in a fragment invalidates
        # a cached false-positive rate even with the corruption unchanged.
        self.assertNotEqual(
            reuse.fingerprint([case(clean="texto limpio")]),
            reuse.fingerprint([case(clean="texto limpío")]),
        )

    def test_order_and_membership_matter(self):
        one, two = case(name="carta"), case(name="sidra")
        self.assertNotEqual(reuse.fingerprint([one, two]), reuse.fingerprint([two, one]))
        self.assertNotEqual(reuse.fingerprint([one]), reuse.fingerprint([one, two]))


class Incompatible(unittest.TestCase):
    def test_an_identical_run_is_reusable(self):
        self.assertEqual(reuse.incompatible(report(["null"]), CONFIG, corpus()), [])

    def test_a_different_seed_is_not(self):
        reasons = reuse.incompatible(report(["null"], {"seed": 7}), CONFIG, corpus())
        self.assertEqual(len(reasons), 1)
        self.assertIn("seed", reasons[0])

    def test_a_truncated_run_is_not(self):
        reasons = reuse.incompatible(report(["null"], {"limit_words": 300}), CONFIG, corpus())
        self.assertIn("limit_words", reasons[0])

    def test_a_run_without_corpus_b_is_not(self):
        # Its clean block is empty, so its FP/1k would read 0.00 next to rows
        # that actually measured it.
        reasons = reuse.incompatible(report(["null"], {"skip_clean": True}), CONFIG, corpus())
        self.assertIn("skip_clean", reasons[0])

    def test_a_different_corpus_is_not(self):
        other = report(["null"], body=corpus([case(text="otra cosa")]))
        self.assertEqual(reuse.incompatible(other, CONFIG, corpus()), ["corpus distinto"])

    def test_reports_older_than_the_fingerprint_fall_back_to_the_seeded_counts(self):
        legacy = report(["null"], body={"cases": 1, "seeded_by_kind": {"tilde": 3}})
        self.assertEqual(reuse.incompatible(legacy, CONFIG, corpus()), [])

        moved = report(["null"], body={"cases": 1, "seeded_by_kind": {"tilde": 4}})
        self.assertEqual(
            reuse.incompatible(moved, CONFIG, corpus()), ["errores sembrados distintos"]
        )


class Load(unittest.TestCase):
    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp())

    def write(self, name, payload):
        path = self.dir / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_takes_the_newest_report_that_has_the_system(self):
        self.write("20260101-000000.json", report(["naive-claude"]) | {"n": 1})
        self.write("20260102-000000.json", report(["naive-claude"]) | {"n": 2})
        found, _ = reuse.load("latest", ["naive-claude"], CONFIG, corpus(), self.dir)
        self.assertEqual(found["naive-claude"]["reused_from"], "20260102-000000.json")

    def test_falls_back_to_an_older_report_for_a_system_the_newest_lacks(self):
        self.write("20260101-000000.json", report(["languagetool"]))
        self.write("20260102-000000.json", report(["naive-claude"]))
        found, _ = reuse.load(
            "latest", ["languagetool", "naive-claude"], CONFIG, corpus(), self.dir
        )
        self.assertEqual(found["languagetool"]["reused_from"], "20260101-000000.json")
        self.assertEqual(found["naive-claude"]["reused_from"], "20260102-000000.json")

    def test_skips_a_newer_report_built_from_another_corpus(self):
        self.write("20260101-000000.json", report(["naive-claude"]))
        self.write("20260102-000000.json", report(["naive-claude"], {"rate": 0.05}))
        found, notes = reuse.load("latest", ["naive-claude"], CONFIG, corpus(), self.dir)
        self.assertEqual(found["naive-claude"]["reused_from"], "20260101-000000.json")
        self.assertTrue(any("descartados" in note for note in notes))

    def test_a_system_with_no_cache_is_reported_not_invented(self):
        self.write("20260101-000000.json", report(["null"]))
        found, notes = reuse.load("latest", ["null", "corrector"], CONFIG, corpus(), self.dir)
        self.assertNotIn("corrector", found)
        self.assertTrue(any("sin caché: corrector" in note for note in notes))

    def test_a_missing_results_dir_is_not_an_error(self):
        found, notes = reuse.load("latest", ["null"], CONFIG, corpus(), self.dir / "nope")
        self.assertEqual(found, {})
        self.assertTrue(any("sin caché" in note for note in notes))

    def test_a_named_report_from_another_corpus_stops_the_run(self):
        # Skipping it silently would answer a direct request with a live run.
        path = self.write("20260101-000000.json", report(["naive-claude"], {"seed": 9}))
        with self.assertRaises(ValueError):
            reuse.load(str(path), ["naive-claude"], CONFIG, corpus(), self.dir)

    def test_a_named_report_that_does_not_exist_stops_the_run(self):
        with self.assertRaises(FileNotFoundError):
            reuse.load(str(self.dir / "nope.json"), ["null"], CONFIG, corpus(), self.dir)

    def test_unreadable_reports_do_not_sink_the_run(self):
        self.write("20260102-000000.json", report(["null"]))
        (self.dir / "20260103-000000.json").write_text("{ roto", encoding="utf-8")
        found, _ = reuse.load("latest", ["null"], CONFIG, corpus(), self.dir)
        self.assertEqual(found["null"]["reused_from"], "20260102-000000.json")


if __name__ == "__main__":
    unittest.main()
