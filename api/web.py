"""The browser surface: paste or upload a manuscript, watch it get corrected.

Same two operations as `/api/*` — submit a text, look one up — shown as HTML
instead of JSON. Validation, job creation and lookup all happen in
`api/service.py`; this module only decides which template answers, never how
a submission is judged valid or how a job is stored.

Routes here answer HTML and nothing else: `GET /` (the compose form),
`POST /jobs` (submit, HTMX-swapped into the page), `GET /jobs/{job_id}`
(the poll target `editor/running.html` points `hx-get` at). A `200` on every
one of them, deliberately, including a rejected submission — an HTMX fragment
target re-renders in place on a validation error the same way it does on
success; a browser-level `4xx` on a same-page swap is friction a JSON client
never has to pay and an HTML one does not need either.
"""

import re
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from api.service import SubmissionError, get_corrector, get_job, submit_job
from corrector.correct import Corrector

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
TEMPLATES = Jinja2Templates(directory=TEMPLATES_DIR)

WEB = APIRouter()


@WEB.get("/", response_class=HTMLResponse)
def compose(request: Request):
    return TEMPLATES.TemplateResponse(
        request,
        "index.html",
        {"text": "", "filename": "", "error": None},
    )


@WEB.post("/jobs", response_class=HTMLResponse)
def create_job(
    request: Request,
    text: str = Form(...),
    filename: str = Form(""),
    corrector: Corrector = Depends(get_corrector),
):
    try:
        job = submit_job(text, corrector)
    except SubmissionError as exc:
        # Same rejection `api.service.submit_job` raises for the JSON API,
        # re-rendered in place instead of turned into an HTTP error status —
        # see the module docstring for why.
        return TEMPLATES.TemplateResponse(
            request,
            "editor/compose.html",
            {"text": text, "filename": filename, "error": exc.detail},
        )

    return TEMPLATES.TemplateResponse(
        request,
        "editor/running.html",
        {"job": job, "filename": filename},
    )


@WEB.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_status(request: Request, job_id: str, filename: str = ""):
    job = get_job(job_id)

    if job is None:
        # Evicted, or an id nobody ever issued. Treated as a terminal state
        # like any other rather than a bare 404: this endpoint is also what
        # `editor/running.html` polls, and a non-2xx response is one htmx
        # will not swap in by default, which would leave a poller stuck on
        # "Corrigiendo…" forever instead of showing why it stopped.
        return TEMPLATES.TemplateResponse(
            request,
            "editor/error.html",
            {"detail": "Ese trabajo no existe o ya ha caducado."},
        )

    if job.status == "running":
        return TEMPLATES.TemplateResponse(
            request,
            "editor/running.html",
            {"job": job, "filename": filename},
        )

    if job.status == "failed":
        return TEMPLATES.TemplateResponse(
            request,
            "editor/error.html",
            {"detail": job.detail or "El corrector no ha podido terminar."},
        )

    paragraphs = _paragraphs(job.text or "")
    return TEMPLATES.TemplateResponse(
        request,
        "editor/result.html",
        {
            "job": job,
            "paragraphs": paragraphs,
            "words": len((job.text or "").split()),
            "download_name": _download_name(filename),
        },
    )


def _paragraphs(text: str) -> list[str]:
    """Blank-line-separated paragraphs, trimmed and with the empty ones dropped.

    A single newline stays inside a paragraph; a blank line is what splits two
    — the same rule the retired React front used (`web/src/lib/text.ts`,
    `toParagraphs`), kept here purely for how the result reads, not for
    anything the correction pass itself relies on.
    """
    return [paragraph.strip() for paragraph in re.split(r"\n{2,}", text) if paragraph.strip()]


def _download_name(filename: str) -> str:
    """ "novela.docx" -> "novela-corregido.txt"; no upload -> a plain default.

    Mirrors `correctedFileName` from the retired React front's `text.ts`. The
    upload itself never reaches the server — `static/app.js` reads the file
    client-side into the same textarea `text` comes from — so this is the one
    place asked to remember what it used to be called.
    """
    base = re.sub(r"\.[^.]+$", "", filename) if filename else "manuscrito"
    return f"{base}-corregido.txt"
