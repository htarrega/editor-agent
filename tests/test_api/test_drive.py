"""`POST /api/drive/jobs`: the submission that names a document instead of carrying one.

Drive itself is faked at the seam `api/service.py` uses — `drive.read` and
`drive.write` — because what these tests are about is the endpoint's contract:
which failures are answered synchronously and which become a failed job, and
what the job says afterwards about a document nobody here can see. That the
edits land on the right characters is `tests/test_corrector/test_drive.py`'s
business, against real document payloads.
"""

import time
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from api.main import app, get_corrector
from corrector import drive, settings
from tests.test_api.test_main import FINISH_TIMEOUT, edits_json, fake_corrector


def document(text, title="Manuscrito", revision="rev-1"):
    """A `drive.Document` over one paragraph, numbered the way Google numbers one."""
    return drive.Document(
        document_id="doc-1",
        title=title,
        revision_id=revision,
        text=text,
        segments=[drive.Segment(doc_start=1, start=0, text=text)],
    )


class DriveClient(unittest.TestCase):
    """Submit a document and poll, the way the front does."""

    def setUp(self):
        self.client = TestClient(app)
        self.written = []
        self.addCleanup(app.dependency_overrides.clear)

    def use(self, corrector):
        app.dependency_overrides[get_corrector] = lambda: corrector

    def reading(self, answer):
        """Patch the read with a document, or with the error it should raise."""

        def read(reference, service=None):
            if isinstance(answer, Exception):
                raise answer
            return answer

        return mock.patch.object(drive, "read", read)

    def writing(self, error=None):
        def write(document, requests, service=None):
            if error is not None:
                raise error
            self.written.append((document.document_id, requests))

        return mock.patch.object(drive, "write", write)

    def sent(self):
        """Every request that reached Google, across all writes."""
        return [request for _, requests in self.written for request in requests]

    def submit(self, reference="https://docs.google.com/document/d/1AbC_de-FG/edit"):
        return self.client.post("/api/drive/jobs", json={"document": reference})

    def finish(self, **kwargs):
        response = self.submit(**kwargs)
        self.assertEqual(response.status_code, 202)
        job_id = response.json()["job_id"]

        deadline = time.monotonic() + FINISH_TIMEOUT
        while time.monotonic() < deadline:
            body = self.client.get(f"/api/jobs/{job_id}").json()
            if body["status"] != "running":
                return body
            time.sleep(0.01)

        self.fail(f"job {job_id} never finished")


class Submit(DriveClient):
    def test_the_job_names_the_document_it_is_correcting(self):
        self.use(fake_corrector(edits_json()))
        with self.reading(document("El gato duerme.", title="Cuaderno")), self.writing():
            response = self.submit()

        self.assertEqual(response.status_code, 202)
        body = response.json()
        self.assertEqual(body["status"], "running")
        self.assertEqual(body["words"], 3)

        job = self.client.get(f"/api/jobs/{body['job_id']}").json()
        self.assertEqual(job["document_id"], "doc-1")
        self.assertEqual(job["title"], "Cuaderno")

    def test_a_document_that_is_not_there_answers_at_once(self):
        # Read in the request rather than in the worker, precisely so this is a
        # 404 the front can show instead of a job id that fails a second later.
        self.use(fake_corrector(edits_json()))
        with self.reading(drive.DriveError("No existe ese documento.", status=404)):
            response = self.submit()

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "No existe ese documento.")

    def test_a_reference_that_is_not_a_document_is_400(self):
        self.use(fake_corrector(edits_json()))
        response = self.submit(reference="https://example.com/algo")
        self.assertEqual(response.status_code, 400)

    def test_a_document_over_the_ceiling_is_413_before_anything_is_spent(self):
        self.use(fake_corrector(edits_json()))
        long_document = document(" ".join(["palabra"] * (settings.MAX_WORDS + 1)))
        with self.reading(long_document), self.writing():
            response = self.submit()

        self.assertEqual(response.status_code, 413)
        self.assertIn("Manuscrito", response.json()["detail"])
        self.assertEqual(self.sent(), [])


class Written(DriveClient):
    def test_the_corrections_reach_the_document(self):
        # An accent rather than a doubled letter, so the edit is a genuine
        # replacement: the pipeline trims «gatto»→«gato» down to deleting one
        # «t», and a deletion would exercise only half of what is written.
        self.use(
            fake_corrector(
                edits_json({"original": "sabado", "replacement": "sábado", "kind": "ortografia"})
            )
        )
        with self.reading(document("El sabado llovió.")), self.writing():
            job = self.finish()

        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["applied"], 1)
        self.assertEqual(job["text"], "El sábado llovió.")

        document_id, requests = self.written[0]
        self.assertEqual(document_id, "doc-1")
        self.assertEqual(requests[0]["insertText"]["text"], "á")
        self.assertIn("deleteContentRange", requests[1])

    def test_a_clean_document_completes_without_being_written_to(self):
        self.use(fake_corrector(edits_json()))
        with self.reading(document("El gato duerme.")), self.writing():
            job = self.finish()

        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["applied"], 0)
        self.assertEqual(self.sent(), [])

    def test_the_text_reported_is_the_one_the_document_now_holds(self):
        # An edit Drive refuses — this one would merge two paragraphs — must be
        # missing from the reported text too. Otherwise the author reads a
        # correction here that their document does not have.
        self.use(fake_corrector(edits_json({"original": "Uno.\nDos", "replacement": "Uno. Dos"})))
        with self.reading(document("Uno.\nDos.")), self.writing():
            job = self.finish()

        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["text"], "Uno.\nDos.")
        self.assertEqual(job["applied"], 0)
        self.assertEqual(job["rejected"], {"crosses_paragraph": 1})
        self.assertEqual(job["skipped"], 1)
        self.assertEqual(self.sent(), [])


class WriteFailure(DriveClient):
    def test_a_document_that_moved_fails_the_job_rather_than_the_document(self):
        # The revision is pinned, so a document the author kept typing into
        # rejects the write. Nothing was applied, so the job must not complete
        # with a text that reads as "this is what your document says now".
        self.use(fake_corrector(edits_json({"original": "gatto", "replacement": "gato"})))
        moved = drive.DriveError("El documento ha cambiado mientras se corregía.", status=409)
        with self.reading(document("El gatto duerme.")), self.writing(error=moved):
            job = self.finish()

        self.assertEqual(job["status"], "failed")
        self.assertIsNone(job["text"])
        self.assertEqual(job["detail"], "El documento ha cambiado mientras se corregía.")

    def test_a_pass_whose_calls_all_failed_never_touches_the_document(self):
        self.use(fake_corrector(RuntimeError("provider caído")))
        with self.reading(document("El gatto duerme.")), self.writing():
            job = self.finish()

        self.assertEqual(job["status"], "failed")
        self.assertEqual(self.sent(), [])


class TextSubmissionIsUnaffected(unittest.TestCase):
    """The Drive endpoint is an addition, not a change to what already worked."""

    def setUp(self):
        self.client = TestClient(app)
        self.addCleanup(app.dependency_overrides.clear)

    def test_a_text_job_reports_no_document(self):
        app.dependency_overrides[get_corrector] = lambda: fake_corrector(edits_json())
        response = self.client.post("/api/jobs", json={"text": "El gato duerme."})
        job = self.client.get(f"/api/jobs/{response.json()['job_id']}").json()
        self.assertIsNone(job["document_id"])
        self.assertIsNone(job["title"])


if __name__ == "__main__":
    unittest.main()
