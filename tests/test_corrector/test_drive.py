"""The Doc side of a correction: what is read, and what is written back.

Everything here runs against literal document payloads and a fake service. The
Google libraries are an optional extra and no test may need them, but that is
the smaller reason. The bigger one is that the part which has to be exactly
right — an offset in the extracted text becoming an index in the document — is
arithmetic, and arithmetic is worth pinning against a document written out by
hand rather than against whatever a live call happened to return.
"""

import unittest

from corrector import drive
from corrector.edits import Edit, apply_edits


def document(*paragraphs, title="Manuscrito", revision="rev-1"):
    """A `documents.get` payload with its indices worked out.

    A paragraph is a list of pieces: a string is a run of text, an integer is
    something that occupies that many indices and carries no text — an image, a
    chip, a footnote marker. Index 0 is the section break every document opens
    with, so the prose starts at 1, exactly as Google numbers it.
    """
    content = [{"startIndex": 0, "endIndex": 1, "sectionBreak": {}}]
    cursor = 1

    for pieces in paragraphs:
        elements, start = [], cursor
        for piece in pieces:
            if isinstance(piece, str):
                length = drive.utf16_len(piece)
                body = {"textRun": {"content": piece, "textStyle": {"italic": True}}}
            else:
                length = piece
                body = {"inlineObjectElement": {"inlineObjectId": "imagen"}}
            elements.append({"startIndex": cursor, "endIndex": cursor + length, **body})
            cursor += length
        content.append(
            {"startIndex": start, "endIndex": cursor, "paragraph": {"elements": elements}}
        )

    return {
        "documentId": "doc-1",
        "title": title,
        "revisionId": revision,
        "body": {"content": content},
    }


def parse(*paragraphs, **kwargs):
    return drive.parse("doc-1", document(*paragraphs, **kwargs))


class FakeRequest:
    def __init__(self, answer):
        self.answer = answer

    def execute(self):
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


class FakeDocuments:
    """The two calls this module makes, and a record of how it made them."""

    def __init__(self, payload=None, answer=None):
        self.payload = payload
        self.answer = answer
        self.gets = []
        self.updates = []

    def get(self, documentId=None):
        self.gets.append(documentId)
        return FakeRequest(self.payload)

    def batchUpdate(self, documentId=None, body=None):  # noqa: N802 - Google's name
        self.updates.append((documentId, body))
        return FakeRequest(self.answer if self.answer is not None else {})


class FakeService:
    def __init__(self, documents):
        self._documents = documents

    def documents(self):
        return self._documents


class Response:
    """Enough of an httplib2 response for `_call` to recognise Google talking."""

    def __init__(self, status):
        self.status = status


class GoogleError(Exception):
    def __init__(self, status, message=""):
        super().__init__(message)
        self.resp = Response(status)


class Reference(unittest.TestCase):
    def test_a_document_url_yields_its_id(self):
        self.assertEqual(
            drive.document_id("https://docs.google.com/document/d/1AbC_de-FG/edit"),
            "1AbC_de-FG",
        )

    def test_the_account_prefix_does_not_confuse_it(self):
        self.assertEqual(
            drive.document_id("https://docs.google.com/document/u/2/d/1AbC_de-FG/edit#heading=h.x"),
            "1AbC_de-FG",
        )

    def test_a_bare_id_is_taken_as_is(self):
        self.assertEqual(drive.document_id("  1AbCdeFGhijk  "), "1AbCdeFGhijk")

    def test_something_else_is_refused_before_any_call(self):
        with self.assertRaises(drive.DriveError) as caught:
            drive.document_id("https://example.com/algo")
        self.assertEqual(caught.exception.status, 400)


class Reading(unittest.TestCase):
    def test_the_text_is_the_runs_in_order(self):
        parsed = parse(["El gato ", "duerme.\n"], ["Y sueña.\n"])
        self.assertEqual(parsed.text, "El gato duerme.\nY sueña.\n")

    def test_a_document_with_no_prose_is_refused(self):
        with self.assertRaises(drive.DriveError) as caught:
            drive.parse("doc-1", {"body": {"content": []}})
        self.assertEqual(caught.exception.status, 400)

    def test_tables_are_not_read_so_they_cannot_be_edited(self):
        payload = document(["Un párrafo.\n"])
        payload["body"]["content"].append({"table": {"rows": 1}})
        parsed = drive.parse("doc-1", payload)
        self.assertEqual(parsed.text, "Un párrafo.\n")

    def test_a_tabbed_document_falls_back_to_its_first_tab(self):
        payload = document(["En la pestaña.\n"])
        parsed = drive.parse("doc-1", {"title": "T", "tabs": [{"documentTab": payload}]})
        self.assertEqual(parsed.text, "En la pestaña.\n")

    def test_the_revision_is_kept_for_the_write(self):
        self.assertEqual(parse(["Hola.\n"], revision="rev-9").revision_id, "rev-9")


class Mapping(unittest.TestCase):
    """Plain-text offsets to document indices. Everything else rests on this."""

    def test_the_first_character_is_index_one(self):
        parsed = parse(["El gato.\n"])
        self.assertEqual(parsed.index(0), 1)

    def test_an_offset_maps_across_a_run_boundary(self):
        # «gato» is split down the middle by a change of style, which is exactly
        # the case a whole-document export would flatten.
        parsed = parse(["El ga", "to duerme.\n"])
        self.assertEqual(parsed.text.index("gato"), 3)
        self.assertEqual(parsed.index(3), 4)
        self.assertEqual(parsed.index(7), 8)

    def test_accents_are_one_index_each(self):
        parsed = parse(["Él bajó.\n"])
        self.assertEqual(parsed.index(len("Él bajó.")), 1 + len("Él bajó."))

    def test_an_emoji_counts_as_two(self):
        # The Docs API counts UTF-16 code units; Python counts characters. For
        # anything outside the basic plane the two part company, and everything
        # after it in the paragraph lands one index short.
        parsed = parse(["Mar 🌊 y sal.\n"])
        offset = parsed.text.index("sal")
        self.assertEqual(parsed.index(offset), 1 + offset + 1)

    def test_a_second_paragraph_continues_the_numbering(self):
        parsed = parse(["Uno.\n"], ["Dos.\n"])
        self.assertEqual(parsed.index(parsed.text.index("Dos")), 1 + len("Uno.\n"))


class Planning(unittest.TestCase):
    def test_the_replacement_goes_in_before_the_original_comes_out(self):
        # Order is the whole formatting story. `insertText` inherits the style
        # of the character before it, so the new word is inserted at the end of
        # the old one — inside the italics — and only then is the old one
        # deleted. Insert at the start instead and it would inherit whatever
        # the preceding space happened to be styled as.
        parsed = parse(["El gatto duerme.\n"])
        start = parsed.text.index("gatto")
        requests, accepted, rejected = drive.plan(
            parsed, [Edit(start=start, end=start + 5, replacement="gato")]
        )
        self.assertEqual(len(accepted), 1)
        self.assertEqual(rejected, [])
        self.assertEqual(
            requests,
            [
                {"insertText": {"location": {"index": 9}, "text": "gato"}},
                {"deleteContentRange": {"range": {"startIndex": 4, "endIndex": 9}}},
            ],
        )

    def test_edits_are_written_from_the_end_backwards(self):
        # Every request is expressed in the indices of the document as it stands
        # when that request runs. Working backwards, each edit only ever touches
        # text that no later request mentions.
        parsed = parse(["El gatto duerme y el perrro ladra.\n"])
        first = parsed.text.index("gatto")
        second = parsed.text.index("perrro")
        requests, accepted, _ = drive.plan(
            parsed,
            [
                Edit(start=first, end=first + 5, replacement="gato"),
                Edit(start=second, end=second + 6, replacement="perro"),
            ],
        )
        self.assertEqual(len(accepted), 2)
        indices = [
            request["deleteContentRange"]["range"]["startIndex"]
            for request in requests
            if "deleteContentRange" in request
        ]
        self.assertEqual(indices, sorted(indices, reverse=True))

    def test_a_deletion_emits_no_insert(self):
        parsed = parse(["Un  espacio de más.\n"])
        start = parsed.text.index("  ")
        requests, accepted, _ = drive.plan(
            parsed, [Edit(start=start, end=start + 1, replacement="")]
        )
        self.assertEqual(len(accepted), 1)
        self.assertEqual(
            requests, [{"deleteContentRange": {"range": {"startIndex": 3, "endIndex": 4}}}]
        )

    def test_an_edit_spanning_a_paragraph_break_is_dropped(self):
        # A newline *is* the paragraph in Docs: delete it and two paragraphs
        # merge, one of the two paragraph styles disappearing with it. A
        # corrector fixes «gatto»; it does not decide where paragraphs end.
        parsed = parse(["Uno.\n"], ["Dos.\n"])
        start = parsed.text.index("o.\nD")
        requests, accepted, rejected = drive.plan(
            parsed, [Edit(start=start, end=start + 4, replacement="o. D")]
        )
        self.assertEqual(requests, [])
        self.assertEqual(accepted, [])
        self.assertEqual([rejection.reason for rejection in rejected], ["crosses_paragraph"])

    def test_a_replacement_that_would_split_a_paragraph_is_dropped(self):
        parsed = parse(["Uno y dos.\n"])
        start = parsed.text.index(" y ")
        _, accepted, rejected = drive.plan(
            parsed, [Edit(start=start, end=start + 3, replacement=".\n")]
        )
        self.assertEqual(accepted, [])
        self.assertEqual([rejection.reason for rejection in rejected], ["crosses_paragraph"])

    def test_an_edit_reaching_across_an_image_is_dropped(self):
        # Extraction skips the image, so the two runs are spliced together and
        # read as adjacent. Applying an edit across that join would delete the
        # image sitting in the gap.
        parsed = parse(["El gat", 1, "to duerme.\n"])
        start = parsed.text.index("gatto")
        _, accepted, rejected = drive.plan(
            parsed, [Edit(start=start, end=start + 5, replacement="gato")]
        )
        self.assertEqual(accepted, [])
        self.assertEqual([rejection.reason for rejection in rejected], ["crosses_element"])

    def test_an_edit_beside_an_image_still_applies(self):
        parsed = parse(["El gatto ", 1, " duerme.\n"])
        start = parsed.text.index("gatto")
        requests, accepted, rejected = drive.plan(
            parsed, [Edit(start=start, end=start + 5, replacement="gato")]
        )
        self.assertEqual(len(accepted), 1)
        self.assertEqual(rejected, [])
        self.assertEqual(requests[0]["insertText"]["text"], "gato")

    def test_what_is_accepted_is_exactly_what_the_preview_will_show(self):
        # The invariant the whole feature rests on: the job reports a text built
        # by `apply_edits` from the accepted edits, so `apply_edits` must have
        # nothing left to drop. Anything it dropped here would be a correction
        # the author reads in the browser and does not have in their document.
        parsed = parse(["El gatto duerme.\n"])
        start = parsed.text.index("gatto")
        overlapping = [
            Edit(start=start, end=start + 5, replacement="gato"),
            Edit(start=start + 1, end=start + 3, replacement="X"),
        ]
        _, accepted, rejected = drive.plan(parsed, overlapping)
        text, leftover = apply_edits(parsed.text, accepted)
        self.assertEqual(leftover, [])
        self.assertEqual(text, "El gato duerme.\n")
        self.assertEqual([rejection.reason for rejection in rejected], ["overlapping"])


class Writing(unittest.TestCase):
    def test_the_write_pins_the_revision_that_was_read(self):
        # Indices were computed against that revision. If the author has typed
        # since, every index below their cursor has moved and applying anyway
        # would eat the wrong characters, so the write must fail instead.
        documents = FakeDocuments()
        parsed = parse(["El gatto duerme.\n"], revision="rev-7")
        requests, _, _ = drive.plan(parsed, [Edit(start=3, end=8, replacement="gato")])

        drive.write(parsed, requests, service=FakeService(documents))

        document_id, body = documents.updates[0]
        self.assertEqual(document_id, "doc-1")
        self.assertEqual(body["writeControl"], {"requiredRevisionId": "rev-7"})
        self.assertEqual(body["requests"], requests)

    def test_a_clean_document_is_not_written_to_at_all(self):
        documents = FakeDocuments()
        drive.write(parse(["Impecable.\n"]), [], service=FakeService(documents))
        self.assertEqual(documents.updates, [])

    def test_reading_goes_through_the_id_not_the_url(self):
        documents = FakeDocuments(payload=document(["Hola.\n"]))
        parsed = drive.read(
            "https://docs.google.com/document/d/1AbC_de-FG/edit",
            service=FakeService(documents),
        )
        self.assertEqual(documents.gets, ["1AbC_de-FG"])
        self.assertEqual(parsed.text, "Hola.\n")


class Failures(unittest.TestCase):
    """Google's statuses, as something the author can read and act on."""

    def read(self, status, message=""):
        documents = FakeDocuments(payload=GoogleError(status, message))
        with self.assertRaises(drive.DriveError) as caught:
            drive.read("1AbCdeFGhijk", service=FakeService(documents))
        return caught.exception

    def test_a_missing_document_is_404(self):
        self.assertEqual(self.read(404).status, 404)

    def test_no_permission_is_403(self):
        self.assertEqual(self.read(403).status, 403)

    def test_a_moved_revision_is_409(self):
        error = self.read(400, "requiredRevisionId is stale")
        self.assertEqual(error.status, 409)
        self.assertIn("Vuelve a intentarlo", str(error))

    def test_anything_else_is_502(self):
        self.assertEqual(self.read(500).status, 502)

    def test_an_error_that_is_not_google_is_not_dressed_up_as_one(self):
        documents = FakeDocuments(payload=ValueError("roto"))
        with self.assertRaises(ValueError):
            drive.read("1AbCdeFGhijk", service=FakeService(documents))


if __name__ == "__main__":
    unittest.main()
