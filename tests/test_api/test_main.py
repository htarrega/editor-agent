"""The HTTP surface: status codes and the invariants the JSON body must hold.

The corrector is swapped out through `get_corrector`'s dependency override, so
these tests drive the real FastAPI app without importing `openai` or reaching
a network.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from api import main
from api.main import app, get_corrector
from corrector import settings
from corrector.correct import Corrector
from corrector.llm import Reply


def fake_corrector(reply):
    """A Corrector wired to a `generate` that answers with `reply`, or raises it."""

    def generate(model, system, user):
        if isinstance(reply, Exception):
            raise reply
        return Reply(text=reply, input_tokens=10, output_tokens=5)

    return Corrector("deepseek-v4-flash", generate)


def edits_json(*items):
    return json.dumps({"edits": list(items)})


class CorrectFile(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.addCleanup(app.dependency_overrides.clear)

    def use(self, corrector):
        app.dependency_overrides[get_corrector] = lambda: corrector

    def write(self, content, binary=False):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "text.txt"
        if binary:
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
        return str(path)

    def test_missing_file_is_404(self):
        self.use(fake_corrector(edits_json()))
        response = self.client.post("/correct-file", json={"file_path": "/no/such/file.txt"})
        self.assertEqual(response.status_code, 404)

    def test_non_utf8_file_is_400(self):
        self.use(fake_corrector(edits_json()))
        path = self.write(b"\xff\xfe not utf-8", binary=True)
        response = self.client.post("/correct-file", json={"file_path": path})
        self.assertEqual(response.status_code, 400)

    def test_a_failed_call_is_502_not_a_clean_bill_of_health(self):
        # Without this, an invalid API key looks exactly like "no errors
        # found": 200, status completed, applied 0, text unchanged.
        self.use(fake_corrector(RuntimeError("invalid api key")))
        path = self.write("El texto de prueba.")
        response = self.client.post("/correct-file", json={"file_path": path})
        self.assertEqual(response.status_code, 502)
        self.assertIn("invalid api key", response.json()["detail"])

    def test_happy_path_applies_the_edit(self):
        item = {"line": 1, "original": "el gatto", "replacement": "el gato"}
        self.use(fake_corrector(edits_json(item)))
        path = self.write("el gatto duerme.")
        response = self.client.post("/correct-file", json={"file_path": path})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "completed")
        self.assertEqual(body["text"], "el gato duerme.")
        self.assertEqual(body["applied"], 1)
        self.assertEqual(body["skipped"], 0)
        self.assertEqual(body["rejected"], {})
        self.assertEqual(body["errors"], [])

    def test_skipped_matches_rejected_including_apply_stage(self):
        # Two anchors that each resolve cleanly on their own but overlap once
        # applied. The resolve stage rejects nothing; only apply_edits does.
        # `skipped` has to count that rejection too, not just `rejected`.
        text = "el gato negro corre."
        items = [
            {"original": "gato negro", "replacement": "1111111111"},
            {"original": "negro corre", "replacement": "22222222222"},
        ]
        self.use(fake_corrector(edits_json(*items)))
        path = self.write(text)
        response = self.client.post("/correct-file", json={"file_path": path})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["rejected"], {"overlapping": 1})
        self.assertEqual(body["skipped"], sum(body["rejected"].values()))
        self.assertEqual(body["applied"], 1)


class ProductionCorrectorReadsSettings(unittest.TestCase):
    """The API must honour the same knobs the harness reads.

    `EDITOR_AGENT_EFFORT` and `EDITOR_AGENT_BLOCK_WORDS` were added to
    `corrector.settings` while `get_corrector` still hardcoded `"minimal"` and
    let `block_words` fall back to the module default — so setting either one
    moved the harness and left the API where it was.
    """

    def test_get_corrector_reads_settings_rather_than_literals(self):
        recorded = {}

        def fake_bounded(effort):
            recorded["effort"] = effort
            return lambda model, system, user: None

        with (
            mock.patch.object(settings, "MODEL", "sentinel-model"),
            mock.patch.object(settings, "EFFORT", "sentinel-effort"),
            mock.patch.object(settings, "BLOCK_WORDS", 7),
            mock.patch.object(main, "bounded_deepseek", fake_bounded),
        ):
            main.get_corrector.cache_clear()
            self.addCleanup(main.get_corrector.cache_clear)
            corrector = main.get_corrector()

        self.assertEqual(corrector.model, "sentinel-model")
        self.assertEqual(recorded["effort"], "sentinel-effort")
        self.assertEqual(corrector.block_words, 7)


class PerBlockPartialFailure(unittest.TestCase):
    """A partial failure is a partial result, not a failed request.

    `_correct_per_block` appends one error per failed block and counts one call
    per attempted block, so the endpoint separates "every call failed" from
    "one block of many did" by comparing the two. Reading `errors` as a plain
    total-failure flag would throw away the blocks that worked.
    """

    def setUp(self):
        self.client = TestClient(app)
        self.addCleanup(app.dependency_overrides.clear)

    def post(self, replies, text):
        answers = iter(replies)

        def generate(model, system, user):
            answer = next(answers)
            if isinstance(answer, Exception):
                raise answer
            return Reply(text=answer, input_tokens=10, output_tokens=5)

        # `block_words=None` is one block per line, so the text below is two blocks.
        corrector = Corrector("deepseek-v4-flash", generate, block_words=None, blocks_per_call=1)
        app.dependency_overrides[get_corrector] = lambda: corrector

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "text.txt"
        path.write_text(text, encoding="utf-8")
        return self.client.post("/correct-file", json={"file_path": str(path)})

    def test_one_failed_block_still_returns_the_others_work(self):
        edit = {"original": "el gatto", "replacement": "el gato"}
        response = self.post(
            [edits_json(edit), RuntimeError("boom")],
            "el gatto duerme.\nla segunda linea.",
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["text"], "el gato duerme.\nla segunda linea.")
        self.assertEqual(body["applied"], 1)
        self.assertEqual(len(body["errors"]), 1)
        self.assertIn("boom", body["errors"][0])

    def test_every_block_failing_is_still_502(self):
        response = self.post(
            [RuntimeError("boom"), RuntimeError("boom")],
            "el gatto duerme.\nla segunda linea.",
        )
        self.assertEqual(response.status_code, 502)


if __name__ == "__main__":
    unittest.main()
