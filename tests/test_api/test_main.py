"""The HTTP surface: status codes and the invariants the JSON body must hold.

The corrector is swapped out through `get_corrector`'s dependency override, so
these tests drive the real FastAPI app without importing `openai` or reaching a
network. The work still goes through the real thread pool: a fake `generate`
answers in microseconds, so `finish` below returns as soon as the worker has
written the job, and what is exercised is the path production takes.
"""

import json
import threading
import time
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from api import main
from api.jobs import Job, JobStore
from api.main import app, get_corrector
from corrector import presets, settings
from corrector.correct import Corrector
from corrector.llm import Reply

# Generous: it bounds a hang, it does not pace anything. A fake `generate`
# finishes in microseconds, so a run that reaches this has stopped working.
FINISH_TIMEOUT = 10.0


def fake_corrector(reply):
    """A Corrector wired to a `generate` that answers with `reply`, or raises it."""

    def generate(model, system, user):
        if isinstance(reply, Exception):
            raise reply
        return Reply(text=reply, input_tokens=10, output_tokens=5)

    return Corrector("deepseek-v4-flash", generate)


def edits_json(*items):
    return json.dumps({"edits": list(items)})


class JobClient(unittest.TestCase):
    """Submit and poll, the way the front does."""

    def setUp(self):
        self.client = TestClient(app)
        self.addCleanup(app.dependency_overrides.clear)

    def use(self, corrector):
        app.dependency_overrides[get_corrector] = lambda: corrector

    def submit(self, text):
        return self.client.post("/jobs", json={"text": text})

    def finish(self, text):
        """Submit, poll until the job stops running, return the finished body."""
        response = self.submit(text)
        self.assertEqual(response.status_code, 202)
        job_id = response.json()["job_id"]

        deadline = time.monotonic() + FINISH_TIMEOUT
        while time.monotonic() < deadline:
            body = self.client.get(f"/jobs/{job_id}").json()
            if body["status"] != "running":
                return body
            time.sleep(0.01)

        self.fail(f"job {job_id} never finished")


class Submit(JobClient):
    def test_submission_is_accepted_not_awaited(self):
        self.use(fake_corrector(edits_json()))
        response = self.submit("el gatto duerme.")
        self.assertEqual(response.status_code, 202)
        body = response.json()
        self.assertEqual(body["status"], "running")
        self.assertEqual(body["words"], 3)
        self.assertTrue(body["job_id"])

    def test_empty_text_is_400(self):
        self.use(fake_corrector(edits_json()))
        self.assertEqual(self.submit("   \n  ").status_code, 400)

    def test_text_over_the_ceiling_is_413_at_submit(self):
        # Refused before a job exists, so nothing is spent at the provider.
        self.use(fake_corrector(edits_json()))
        with mock.patch.object(settings, "MAX_WORDS", 5):
            response = self.submit("una dos tres cuatro cinco seis")
        self.assertEqual(response.status_code, 413)
        self.assertIn("6", response.json()["detail"])

    def test_a_text_at_the_ceiling_is_accepted(self):
        self.use(fake_corrector(edits_json()))
        with mock.patch.object(settings, "MAX_WORDS", 5):
            response = self.submit("una dos tres cuatro cinco")
        self.assertEqual(response.status_code, 202)

    def test_unknown_job_is_404(self):
        self.assertEqual(self.client.get("/jobs/no-such-job").status_code, 404)

    def test_the_path_taking_endpoint_is_gone(self):
        # It read any file the process could read. Removed rather than patched
        # (docs/PLAN.md, «Interfaces»); this is what keeps it from coming back.
        response = self.client.post("/correct-file", json={"file_path": "/etc/passwd"})
        self.assertIn(response.status_code, (404, 405))

    def test_health_answers_without_a_corrector(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})


class Poll(JobClient):
    def test_a_job_reads_running_until_the_pass_returns(self):
        # The point of the whole shape: the response comes back while the work
        # is still going. A fake that blocks is the only way to observe it.
        released = threading.Event()
        self.addCleanup(released.set)

        def generate(model, system, user):
            released.wait(FINISH_TIMEOUT)
            return Reply(text=edits_json(), input_tokens=10, output_tokens=5)

        self.use(Corrector("deepseek-v4-flash", generate))

        job_id = self.submit("el gatto duerme.").json()["job_id"]
        self.assertEqual(self.client.get(f"/jobs/{job_id}").json()["status"], "running")

        released.set()

        deadline = time.monotonic() + FINISH_TIMEOUT
        while time.monotonic() < deadline:
            if self.client.get(f"/jobs/{job_id}").json()["status"] != "running":
                break
            time.sleep(0.01)
        else:
            self.fail("job never left running")

    def test_happy_path_applies_the_edit(self):
        item = {"line": 1, "original": "el gatto", "replacement": "el gato"}
        self.use(fake_corrector(edits_json(item)))
        body = self.finish("el gatto duerme.")
        self.assertEqual(body["status"], "completed")
        self.assertEqual(body["text"], "el gato duerme.")
        self.assertEqual(body["applied"], 1)
        self.assertEqual(body["skipped"], 0)
        self.assertEqual(body["rejected"], {})
        self.assertEqual(body["errors"], [])
        self.assertIsNone(body["detail"])

    def test_skipped_matches_rejected_including_apply_stage(self):
        # Two anchors that each resolve cleanly on their own but overlap once
        # applied. The resolve stage rejects nothing; only apply_edits does.
        # `skipped` has to count that rejection too, not just `rejected`.
        items = [
            {"original": "gato negro", "replacement": "1111111111"},
            {"original": "negro corre", "replacement": "22222222222"},
        ]
        self.use(fake_corrector(edits_json(*items)))
        body = self.finish("el gato negro corre.")
        self.assertEqual(body["rejected"], {"overlapping": 1})
        self.assertEqual(body["skipped"], sum(body["rejected"].values()))
        self.assertEqual(body["applied"], 1)

    def test_a_failed_call_fails_the_job_rather_than_completing_it(self):
        # Without this, an invalid API key looks exactly like "no errors
        # found": completed, applied 0, text unchanged.
        self.use(fake_corrector(RuntimeError("invalid api key")))
        body = self.finish("El texto de prueba.")
        self.assertEqual(body["status"], "failed")
        self.assertIsNone(body["text"])
        self.assertIn("invalid api key", body["detail"])

    def test_a_crashing_corrector_fails_the_job_rather_than_hanging_it(self):
        # Nothing may escape the worker: the submitting request is long gone,
        # so an exception with nowhere to go leaves the front polling forever.
        broken = mock.Mock()
        broken.correct.side_effect = ValueError("configuración rota")
        self.use(broken)
        body = self.finish("El texto de prueba.")
        self.assertEqual(body["status"], "failed")
        self.assertIn("configuración rota", body["detail"])


class PerBlockPartialFailure(JobClient):
    """A partial failure is a partial result, not a failed job.

    `_correct_per_block` appends one error per failed block and counts one call
    per attempted block, so the endpoint separates "every call failed" from
    "one block of many did" by comparing the two. Reading `errors` as a plain
    total-failure flag would throw away the blocks that worked.
    """

    def use_replies(self, replies):
        answers = iter(replies)
        lock = threading.Lock()

        def generate(model, system, user):
            with lock:
                answer = next(answers)
            if isinstance(answer, Exception):
                raise answer
            return Reply(text=answer, input_tokens=10, output_tokens=5)

        # `block_words=None` is one block per line, so the texts below are two blocks.
        self.use(Corrector("deepseek-v4-flash", generate, block_words=None, blocks_per_call=1))

    def test_one_failed_block_still_returns_the_others_work(self):
        edit = {"original": "el gatto", "replacement": "el gato"}
        self.use_replies([edits_json(edit), RuntimeError("boom")])
        body = self.finish("el gatto duerme.\nla segunda linea.")
        self.assertEqual(body["status"], "completed")
        self.assertEqual(body["text"], "el gato duerme.\nla segunda linea.")
        self.assertEqual(body["applied"], 1)
        self.assertEqual(len(body["errors"]), 1)
        self.assertIn("boom", body["errors"][0])

    def test_every_block_failing_fails_the_job(self):
        self.use_replies([RuntimeError("boom"), RuntimeError("boom")])
        body = self.finish("el gatto duerme.\nla segunda linea.")
        self.assertEqual(body["status"], "failed")
        self.assertIsNone(body["text"])


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
            mock.patch.object(presets, "bounded_deepseek", fake_bounded),
        ):
            main.get_corrector.cache_clear()
            self.addCleanup(main.get_corrector.cache_clear)
            corrector = main.get_corrector()

        self.assertEqual(corrector.model, "sentinel-model")
        self.assertEqual(recorded["effort"], "sentinel-effort")
        self.assertEqual(corrector.block_words, 7)

    def test_the_system_setting_chooses_the_configuration(self):
        # Without this, `EDITOR_AGENT_SYSTEM` could be read once at import and
        # the API would keep running whatever was set when it started.
        with mock.patch.object(settings, "SYSTEM", "raced"):
            main.get_corrector.cache_clear()
            self.addCleanup(main.get_corrector.cache_clear)
            corrector = main.get_corrector()

        self.assertEqual(corrector.window_blocks, 1)
        self.assertEqual(corrector.attempts, 3)
        self.assertEqual(corrector.deadline, 4.3)

    def test_an_unknown_system_stops_the_api_rather_than_defaulting(self):
        with mock.patch.object(settings, "SYSTEM", "turbo"):
            main.get_corrector.cache_clear()
            self.addCleanup(main.get_corrector.cache_clear)
            with self.assertRaises(ValueError):
                main.get_corrector()


class Store(unittest.TestCase):
    """The bits of the store the HTTP tests cannot reach."""

    def test_a_finished_job_keeps_what_it_was_given(self):
        store = JobStore()
        job = store.create(words=3)
        store.complete(job.job_id, text="corregido", applied=1)
        finished = store.get(job.job_id)
        self.assertEqual(finished.status, "completed")
        self.assertEqual(finished.text, "corregido")
        self.assertEqual(finished.applied, 1)
        self.assertEqual(finished.words, 3)

    def test_finishing_an_unknown_job_is_not_an_error(self):
        # It was evicted while the pass ran. There is nobody left to hand the
        # result to, and a worker thread has nowhere to raise.
        store = JobStore()
        store.complete("gone", text="corregido")

    def test_the_oldest_finished_job_is_dropped_at_capacity(self):
        store = JobStore(capacity=2)
        first = store.create(words=1)
        store.complete(first.job_id, text="uno")
        second = store.create(words=1)
        store.complete(second.job_id, text="dos")
        third = store.create(words=1)

        self.assertIsNone(store.get(first.job_id))
        self.assertIsNotNone(store.get(second.job_id))
        self.assertIsNotNone(store.get(third.job_id))

    def test_a_running_job_is_never_evicted(self):
        # Evicting one loses a paid call and leaves the front polling an id
        # that will never answer, so the cap yields instead.
        store = JobStore(capacity=1)
        running = [store.create(words=1) for _ in range(3)]
        for job in running:
            self.assertIsNotNone(store.get(job.job_id))

        # The backlog is cleared by the next submission, not by finishing:
        # trimming is what `create` does, so an idle store stays over its cap
        # until something else arrives to push the finished jobs out.
        for job in running:
            store.complete(job.job_id, text="listo")
        self.assertEqual(sum(store.get(job.job_id) is not None for job in running), 3)

        store.create(words=1)
        self.assertEqual(sum(store.get(job.job_id) is not None for job in running), 0)

    def test_a_fresh_job_carries_no_result(self):
        job = JobStore().create(words=11)
        self.assertEqual(job.status, "running")
        self.assertIsNone(job.text)
        self.assertIsNone(job.detail)
        self.assertEqual(job.errors, [])

    def test_ids_are_unique(self):
        store = JobStore()
        ids = {store.create(words=1).job_id for _ in range(100)}
        self.assertEqual(len(ids), 100)

    def test_the_model_is_what_the_front_reads(self):
        # The front's `Job` type in web/src/lib/proofread.ts. A field renamed
        # here is a field the browser silently reads as undefined.
        expected = {
            "job_id",
            "status",
            "text",
            "applied",
            "proposed",
            "skipped",
            "errors",
            "detail",
        }
        self.assertLessEqual(expected, set(Job.model_fields))


if __name__ == "__main__":
    unittest.main()
