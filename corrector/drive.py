"""Google Docs, read and written in place.

The whole point of this module is what it refuses to do: it never exports the
document, corrects the export and uploads it back. That round trip is what
flattens a manuscript — bold, italics, dialogue indents, paragraph styles, the
notes in the margin — and the author would get back a clean text that no longer
looks like their book.

Instead the correction travels the way it does everywhere else in this
repository: as anchored, typed edits (`corrector/edits.py`). Here they are
resolved to the Doc's own character indices and written as a `batchUpdate` of
`insertText` + `deleteContentRange` pairs. Only the corrected spans are touched;
everything around them is never named in a request, so there is nothing for the
API to restyle. Formatting survives because it is never rewritten.

Two guards make that claim hold rather than merely hope for it:

* **Nothing structural is ever deleted.** An edit whose span or replacement
  contains a newline is dropped: in Docs a newline *is* the paragraph, and
  removing one merges two paragraphs and takes one of the two paragraph styles
  with it. A corrector is allowed to change «gatto» to «gato», not to decide
  where paragraphs end.
* **The revision is pinned.** The `batchUpdate` carries the `revisionId` the
  read returned, so a document the author kept typing into between the two
  calls makes the write fail instead of applying corrections at indices that
  have since moved — which would eat the wrong characters.

Scope: the body of the document's first tab. Tables, footnotes, headers and
footers are not read and not corrected.
"""

import os
import re
import sys

from pydantic import BaseModel

from corrector import settings
from corrector.edits import Rejection, partition_edits

# Read *and* write on the author's documents. `drive.file` would be narrower,
# but it only reaches files the app itself created or the user handed over
# through Google's own file picker, and this front identifies a document by a
# pasted URL.
SCOPES = ["https://www.googleapis.com/auth/documents"]

# `https://docs.google.com/document/d/<id>/edit#heading=...` and friends.
DOCUMENT_URL = re.compile(r"/document/(?:u/\d+/)?d/([a-zA-Z0-9_-]+)")

# What a bare id looks like when it arrives without a URL around it.
DOCUMENT_ID = re.compile(r"^[a-zA-Z0-9_-]{12,}$")

# How to get the Google libraries, quoted back at whoever hit their absence.
INSTALL_HINT = 'pip install -e ".[drive]"'


class DriveError(Exception):
    """Something went wrong with the document, in words for the author.

    `status` is the HTTP status the API should answer with. The message is in
    Spanish because `api/main.py` puts it in `detail`, which the front shows
    verbatim.
    """

    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


class Segment(BaseModel):
    """One run of text: where it starts in the Doc, where it lands in the text.

    `doc_start` counts UTF-16 code units, because that is what the Docs API
    counts. `start` counts Python characters. For Spanish prose the two move
    together; for an emoji they do not, and conflating them puts an edit one
    character off for the rest of the paragraph.
    """

    doc_start: int
    start: int
    text: str


class Document(BaseModel):
    """A Doc as this module needs it: the prose, and how to find it again."""

    document_id: str
    title: str
    revision_id: str
    text: str
    segments: list[Segment]

    def index(self, offset):
        """The Doc index for an offset into `text`."""
        chosen = self.segments[0]
        for segment in self.segments:
            if segment.start > offset:
                break
            chosen = segment
        return chosen.doc_start + utf16_len(chosen.text[: offset - chosen.start])

    def seams(self):
        """Offsets in `text` where two segments meet that are not adjacent in the Doc.

        An image, a chip or a footnote marker sits between them and contributes
        no text, so extraction splices two spans that the reader never saw side
        by side. An edit anchored across such a join would delete whatever is
        sitting in the gap, so those are dropped rather than applied.
        """
        joins = set()
        for previous, following in zip(self.segments, self.segments[1:]):
            if previous.doc_start + utf16_len(previous.text) != following.doc_start:
                joins.add(following.start)
        return joins


def utf16_len(text):
    """Length in UTF-16 code units — how the Docs API measures an index."""
    return len(text.encode("utf-16-le")) // 2


def document_id(reference):
    """The document id in `reference`, which may be a URL or already an id."""
    reference = reference.strip()
    match = DOCUMENT_URL.search(reference)
    if match:
        return match.group(1)
    if DOCUMENT_ID.match(reference):
        return reference
    raise DriveError(
        "Eso no parece un documento de Google Docs. Pega la URL del documento o su identificador.",
        status=400,
    )


def read(reference, service=None):
    """Fetch a document and flatten its body to text, keeping the index map."""
    identifier = document_id(reference)
    service = service or build_service()
    payload = _call(service.documents().get(documentId=identifier))
    return parse(identifier, payload)


def parse(identifier, payload):
    """Build a `Document` from what `documents.get` answered.

    Separated from the network so the mapping — the part that has to be exactly
    right — is testable against a literal document, with no credentials and no
    calls.
    """
    body = _body(payload)
    segments, text = [], []
    offset = 0

    for element in body.get("content", []):
        paragraph = element.get("paragraph")
        if paragraph is None:
            # A table, a table of contents, a section break. Not read, so not
            # corrected: an edit can only be applied where an index was mapped.
            continue
        for piece in paragraph.get("elements", []):
            run = piece.get("textRun")
            if run is None:
                continue
            content = run.get("content", "")
            if not content:
                continue
            segments.append(
                Segment(doc_start=piece.get("startIndex", 0), start=offset, text=content)
            )
            text.append(content)
            offset += len(content)

    if not segments:
        raise DriveError("El documento no tiene texto que corregir.", status=400)

    return Document(
        document_id=identifier,
        title=payload.get("title", ""),
        revision_id=payload.get("revisionId", ""),
        text="".join(text),
        segments=segments,
    )


def plan(document, edits):
    """Turn resolved edits into `batchUpdate` requests.

    Returns the requests, the edits they carry, and what was dropped on the
    way. The accepted list is what the caller must use to build the copy it
    shows the author: anything else and the preview claims a correction the
    document does not have.

    Two things make the request list correct rather than merely plausible:

    * **Descending order.** Every request is expressed in indices of the
      document as it stands when that request runs. Working from the end
      backwards, each edit changes only text that later requests never mention.
    * **Insert before delete.** `insertText` inherits the style of the
      character before the insertion point, so the replacement goes in at the
      *end* of the span being replaced — inheriting the style of the span
      itself — and only then is the original deleted. Insert first at the start
      instead and «gatto» in italics would come back as «gato» in whatever the
      preceding space happened to be.
    """
    survivors, rejected = [], []
    joins = document.seams()

    for edit in edits:
        span = document.text[edit.start : edit.end]
        if "\n" in span or "\n" in edit.replacement:
            rejected.append(Rejection(reason="crosses_paragraph", detail=repr(edit)))
            continue
        if any(edit.start < join < edit.end for join in joins):
            rejected.append(Rejection(reason="crosses_element", detail=repr(edit)))
            continue
        survivors.append(edit)

    accepted, overlapping = partition_edits(document.text, survivors)
    rejected.extend(overlapping)

    requests = []
    for edit in sorted(accepted, key=lambda e: e.start, reverse=True):
        start = document.index(edit.start)
        end = document.index(edit.end)
        if edit.replacement:
            requests.append({"insertText": {"location": {"index": end}, "text": edit.replacement}})
        if end > start:
            requests.append(
                {
                    "deleteContentRange": {
                        "range": {"startIndex": start, "endIndex": end},
                    }
                }
            )

    return requests, accepted, rejected


def write(document, requests, service=None):
    """Apply the requests, refusing to write if the document has moved on.

    `requiredRevisionId` is the whole safety story. Indices were computed
    against the revision that was read; if the author typed a sentence into the
    document in the meantime, every index below their cursor has shifted and
    applying anyway would delete the wrong characters. Better to fail and ask
    them to run it again.
    """
    if not requests:
        return
    service = service or build_service()
    _call(
        service.documents().batchUpdate(
            documentId=document.document_id,
            body={
                "requests": requests,
                "writeControl": {"requiredRevisionId": document.revision_id},
            },
        )
    )


def build_service():
    """A Docs client, built per call rather than shared.

    The generated client wraps an `httplib2` connection, which is not safe to
    share between threads, and this runs on the API's worker pool. Building one
    costs a local object; getting it wrong costs two corrections interleaved on
    one socket.
    """
    build = _import()
    return build("docs", "v1", credentials=credentials(), cache_discovery=False)


def credentials(interactive=False):
    """The author's OAuth credentials, refreshed or freshly consented to.

    `interactive` is what separates `python -m corrector.drive login` from the
    API: a server thread must never open a browser and block on a human, so it
    fails with an instruction instead.
    """
    _import()
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    token = os.path.expanduser(settings.GOOGLE_TOKEN)
    secrets = os.path.expanduser(settings.GOOGLE_CLIENT_SECRETS)

    stored = Credentials.from_authorized_user_file(token, SCOPES) if os.path.exists(token) else None

    if stored and stored.valid:
        return stored

    if stored and stored.expired and stored.refresh_token:
        try:
            stored.refresh(Request())
        except RefreshError:
            stored = None
        else:
            _save(token, stored)
            return stored

    if not interactive:
        raise DriveError(
            "No hay acceso autorizado a Google Docs. Ejecuta "
            "«python -m corrector.drive login» y vuelve a intentarlo.",
            status=401,
        )

    if not os.path.exists(secrets):
        raise DriveError(
            f"Falta el fichero de credenciales de Google en {secrets}. Descárgalo desde "
            "Google Cloud Console (OAuth client ID, tipo «Desktop app») y déjalo ahí, o "
            "apunta EDITOR_AGENT_GOOGLE_CLIENT_SECRETS a donde esté.",
            status=401,
        )

    flow = InstalledAppFlow.from_client_secrets_file(secrets, SCOPES)
    fresh = flow.run_local_server(port=0)
    _save(token, fresh)
    return fresh


def _save(path, credentials):
    """Write the token, readable only by its owner: it is a key to the author's Docs."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with open(descriptor, "w") as handle:
        handle.write(credentials.to_json())


def _body(payload):
    """The body to correct: the legacy one, or the first tab's.

    A document with tabs answers with `tabs` and leaves `body` empty unless the
    caller asked otherwise. Only the first tab is read — correcting the rest
    means deciding what «the document» means when it is several, and that is a
    product question nobody has answered yet.
    """
    body = payload.get("body")
    if body and body.get("content"):
        return body
    tabs = payload.get("tabs") or []
    if tabs:
        return tabs[0].get("documentTab", {}).get("body", {})
    return {}


def _import():
    """`googleapiclient.discovery.build`, or an error that says how to get it.

    The Google libraries are an optional extra: a clone that only ever posts
    text to the API should not have to install three of them, and importing
    this module must not be what decides whether the API starts.
    """
    try:
        from googleapiclient.discovery import build
    except ModuleNotFoundError as exc:  # pragma: no cover - depends on the install
        raise DriveError(
            f"Falta la integración con Google Docs. Instálala con «{INSTALL_HINT}».",
            status=501,
        ) from exc
    return build


def _call(request):
    """Execute a Docs request, turning Google's failures into `DriveError`.

    Which failures those are is decided by the response the exception carries,
    not by its class. That keeps this module from having to import
    googleapiclient merely to name an exception type — the Google libraries are
    an optional extra, and the tests exercise every path here through a fake
    service, on a machine that need not have them installed. Anything without a
    response is not Google answering, and is left alone.
    """
    try:
        return request.execute()
    except Exception as exc:
        status = getattr(getattr(exc, "resp", None), "status", None)
        if status is None:
            raise
        raise _translate(exc, status) from exc


def _translate(exc, status):
    if status == 404:
        return DriveError("No existe ese documento, o tu cuenta no lo ve.", status=404)
    if status in (401, 403):
        return DriveError(
            "Tu cuenta no tiene permiso para editar ese documento.",
            status=403,
        )
    if status == 400 and "revision" in str(exc).lower():
        return DriveError(
            "El documento ha cambiado mientras se corregía y no se ha escrito nada. "
            "Vuelve a intentarlo.",
            status=409,
        )
    return DriveError(f"Google Docs ha respondido {status}.", status=502)


def _main(argv):
    """`login`, or a document reference to correct end to end.

    A way to exercise the whole cycle — read, correct, write — without a browser
    and without the API, which is what «done» for this asks for: a full cycle on
    a real document of the author's.
    """
    from corrector import presets

    if not argv:
        print("uso: python -m corrector.drive login | <url o id del documento>")
        return 2

    if argv[0] == "login":
        credentials(interactive=True)
        print(f"Autorizado. Token en {os.path.expanduser(settings.GOOGLE_TOKEN)}")
        return 0

    document = read(argv[0])
    words = len(document.text.split())
    print(f"«{document.title}» — {words} palabras")

    correction = presets.build(settings.SYSTEM).correct(document.text)
    requests, accepted, rejected = plan(document, correction.edits)
    write(document, requests)

    print(
        f"propuestas {correction.proposed}, aplicadas {len(accepted)}, descartadas {len(rejected)}"
    )
    for rejection in rejected:
        print(f"  · {rejection.reason}: {rejection.detail}")
    print("escrito en el documento" if accepted else "sin cambios")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(_main(sys.argv[1:]))
    except DriveError as error:
        print(error, file=sys.stderr)
        raise SystemExit(1) from error
