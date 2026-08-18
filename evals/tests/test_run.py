import contextlib
import io
import json
import pathlib
import tempfile
import unittest
from unittest import mock

from evals import run
from evals.dataset import Fragment


def fragments():
    return [Fragment(name="prueba", text="El vaso de sidra. La casa es honda y pareja.\n")]


class Fresh(unittest.TestCase):
    """The system under development has a cache too, from its own previous run.
    Reusing that one is how a run publishes last week's numbers as this week's."""

    def go(self, out, *extra):
        argv = ["--systems", "null", "--out", str(out), "--skip-clean", *extra]
        with mock.patch.object(run, "load_fragments", side_effect=lambda **kw: fragments()):
            with contextlib.redirect_stdout(io.StringIO()):
                run.main(argv)
        newest = max(out.glob("*.json"), key=lambda p: p.name)
        return json.loads(newest.read_text(encoding="utf-8"))["systems"]["null"]

    def test_reuse_takes_the_cached_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp)
            self.go(out, "--tag", "a")
            self.assertIn("reused_from", self.go(out, "--tag", "b", "--reuse"))

    def test_fresh_runs_it_live_anyway(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp)
            self.go(out, "--tag", "a")
            row = self.go(out, "--tag", "b", "--reuse", "--fresh", "null")
            self.assertNotIn("reused_from", row)


class UsageLine(unittest.TestCase):
    """H1 asks for tokens and latency from the first run, so they have to be on
    the report and not only in the raw JSON."""

    USAGE = {
        "calls": 4,
        "input_tokens": 8000,
        "output_tokens": 2000,
        "reasoning_tokens": 1200,
        "seconds": 40.0,
    }

    def test_reports_per_call_figures(self):
        line = run.usage_line(self.USAGE)
        self.assertIn("2,000 tok entrada", line)
        self.assertIn("500 salida", line)
        self.assertIn("300 razonando", line)
        self.assertIn("10.0 s por llamada", line)

    def test_a_model_that_does_not_reason_says_nothing_about_it(self):
        line = run.usage_line(self.USAGE | {"reasoning_tokens": 0})
        self.assertNotIn("razonando", line)

    def test_an_older_report_without_the_field_still_renders(self):
        usage = {k: v for k, v in self.USAGE.items() if k != "reasoning_tokens"}
        self.assertNotIn("razonando", run.usage_line(usage))


class Diagnostics(unittest.TestCase):
    """The two lines that turn the per-edit record into a decision: is a miss a
    blind spot or a budget problem, and does an off-schema label predict a bad
    edit."""

    def detail(self, *rows):
        base = {"side": "gold", "hit": True, "kind": "tilde", "before": "a", "after": "b"}
        return [base | row for row in rows]

    def test_coverage_splits_recall_by_position(self):
        line = run.coverage_line(
            self.detail(
                {"at": 0.1, "hit": True},
                {"at": 0.2, "hit": True},
                {"at": 0.8, "hit": False},
                {"at": 0.9, "hit": True},
            )
        )
        self.assertIn("1ª mitad 2/2 (1.000)", line)
        self.assertIn("2ª mitad 1/2 (0.500)", line)

    def test_coverage_ignores_the_predicted_side(self):
        line = run.coverage_line(self.detail({"at": 0.1}, {"at": 0.9, "side": "pred"}))
        self.assertIn("1ª mitad 1/1", line)
        self.assertNotIn("2ª mitad", line)

    def test_no_detail_means_no_line(self):
        # Reports written before the per-edit record still have to render.
        self.assertIsNone(run.coverage_line(None))
        self.assertIsNone(run.offschema_line([]))

    def test_offschema_counts_labels_the_taxonomy_never_offered(self):
        line = run.offschema_line(
            self.detail(
                {"side": "pred", "kind": "tilde", "at": 0.1},
                {"side": "pred", "kind": "ortotipografía", "at": 0.2, "hit": False},
                {"side": "pred", "kind": "vaguedad", "at": 0.3, "hit": False},
            )
        )
        self.assertIn("2 de 3 propuestas", line)
        self.assertIn("2 de ellas falsas", line)

    def test_a_clean_taxonomy_gets_no_line(self):
        rows = self.detail({"side": "pred", "kind": "tilde", "at": 0.1})
        self.assertIsNone(run.offschema_line(rows))


if __name__ == "__main__":
    unittest.main()
