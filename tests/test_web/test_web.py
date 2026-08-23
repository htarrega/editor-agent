"""The HTML web surface: the same job lifecycle `tests/test_api/test_main.py`
drives as JSON, answered here as HTMX fragments instead.

Same fixture pattern as there: `get_corrector` is overridden with a fake
`generate`, so these drive the real FastAPI app through `TestClient` without
importing `openai` or reaching a provider. `api/web.py` calls the exact same
`api.service.submit_job`/`get_job` the JSON router does, so what is under
test here is only the rendering — the lifecycle itself is already pinned.
"""

import json
import re
import threading
import time
import unittest

from fastapi.testclient import TestClient

from api.main import app
from api.service import STORE, get_corrector
from corrector.correct import Corrector
from corrector.llm import Reply

# Generous: it bounds a hang, it does not pace anything. A fake `generate`
# finishes in microseconds, so a run that reaches this has stopped working.
FINISH_TIMEOUT = 10.0

JOB_ID_IN_HTML = re.compile(r"/jobs/([0-9a-f]+)")


def fake_corrector(reply):
    """A Corrector wired to a `generate` that answers with `reply`, or raises it."""

    def generate(model, system, user):
        if isinstance(reply, Exception):
            raise reply
        return Reply(text=reply, input_tokens=10, output_tokens=5)

    return Corrector("deepseek-v4-flash", generate)


def edits_json(*items):
    return json.dumps({"edits": list(items)})


class WebClient(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.addCleanup(app.dependency_overrides.clear)

    def use(self, corrector):
        app.dependency_overrides[get_corrector] = lambda: corrector

    def submit(self, text):
        return self.client.post("/jobs", data={"text": text})

    def job_id_from(self, response):
        match = JOB_ID_IN_HTML.search(response.text)
        self.assertIsNotNone(match, f"no job id in the response: {response.text!r}")
        return match.group(1)

    def finish(self, text):
        """Submit, poll until the fragment stops asking to be polled again."""
        job_id = self.job_id_from(self.submit(text))

        deadline = time.monotonic() + FINISH_TIMEOUT
        while time.monotonic() < deadline:
            body = self.client.get(f"/jobs/{job_id}").text
            if "hx-trigger" not in body:
                return body
            time.sleep(0.01)

        self.fail(f"job {job_id} never finished")


class Compose(unittest.TestCase):
    def test_root_renders_the_compose_form(self):
        response = TestClient(app).get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn('id="compose-form"', response.text)
        self.assertIn('name="text"', response.text)


class Submit(WebClient):
    def test_empty_text_is_rerendered_with_an_inline_error(self):
        self.use(fake_corrector(edits_json()))
        before = len(STORE._jobs)

        response = self.submit("   \n  ")

        # A `200`, not a `400`: an HTMX fragment target re-renders in place on
        # a validation error, it does not receive a browser-level error status
        # — see the module docstring in api/web.py.
        self.assertEqual(response.status_code, 200)
        self.assertIn("El texto está vacío.", response.text)
        self.assertNotIn('id="job-status"', response.text)
        self.assertEqual(len(STORE._jobs), before, "an invalid submission must not create a job")

    def test_a_valid_submission_starts_polling(self):
        self.use(fake_corrector(edits_json()))

        response = self.submit("el gatto duerme.")

        # `200`, matching the fragment-swap convention above rather than the
        # JSON API's `202` — the still-running state is what the fragment's
        # content (and its `hx-trigger`) communicates, not the status code.
        self.assertEqual(response.status_code, 200)
        self.assertIn('id="job-status"', response.text)
        self.assertIn("hx-get", response.text)


class Poll(WebClient):
    def test_a_running_job_keeps_polling(self):
        # The point of the whole shape: the fragment still asks to be polled
        # again while the work is still going. A fake that blocks is the only
        # way to observe it.
        released = threading.Event()
        self.addCleanup(released.set)

        def generate(model, system, user):
            released.wait(FINISH_TIMEOUT)
            return Reply(text=edits_json(), input_tokens=10, output_tokens=5)

        self.use(Corrector("deepseek-v4-flash", generate))

        job_id = self.job_id_from(self.submit("el gatto duerme."))
        response = self.client.get(f"/jobs/{job_id}")
        self.assertIn("hx-trigger", response.text)

    def test_a_completed_job_shows_the_result_without_polling(self):
        item = {"line": 1, "original": "el gatto", "replacement": "el gato"}
        self.use(fake_corrector(edits_json(item)))

        body = self.finish("el gatto duerme.")

        self.assertIn('id="job-result"', body)
        self.assertIn("el gato duerme.", body)
        self.assertNotIn("hx-trigger", body)

    def test_a_completed_job_lists_its_changes_apart_from_the_text(self):
        # The point: what was corrected is visible on its own, not just folded
        # into the corrected text the author has to diff against the original
        # by eye.
        item = {"line": 1, "original": "el gatto", "replacement": "el gato"}
        self.use(fake_corrector(edits_json(item)))

        body = self.finish("el gatto duerme.")

        self.assertIn('class="changes-list"', body)
        # Widened to the whole word for legibility — see `api/service.py:_change`.
        self.assertIn('<span class="change-before">gatto</span>', body)
        self.assertIn('<span class="change-after">gato</span>', body)

    def test_a_clean_text_shows_no_changes_to_list(self):
        self.use(fake_corrector(edits_json()))

        body = self.finish("El gato duerme.")

        self.assertNotIn('class="changes-list"', body)
        self.assertIn("Sin cambios: el texto ya estaba limpio.", body)

    def test_a_failed_job_shows_the_detail(self):
        # Without this, an invalid API key would have nowhere to surface: the
        # fragment either shows the corrected text or explains why there is
        # none.
        self.use(fake_corrector(RuntimeError("invalid api key")))

        body = self.finish("El texto de prueba.")

        self.assertIn("invalid api key", body)
        self.assertNotIn("hx-trigger", body)


if __name__ == "__main__":
    unittest.main()
