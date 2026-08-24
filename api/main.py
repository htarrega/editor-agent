"""The JSON HTTP surface: submit a text, poll for the correction.

Everything here answers under `/api`. `api/web.py` is the other surface — the
same two operations, shown as HTML at `/` for a browser instead of JSON for a
programmatic client. Neither this module nor that one validates a submission
or looks up a job itself; `api/service.py` is the one place that does, and
both call it rather than each deciding on its own what a valid text is.

The content travels in the body. There is no endpoint that takes a path —
`POST /correct-file` was removed rather than patched, because it read any file
the process could read, and `tests/test_api/test_main.py` pins its absence.

`POST /drive/jobs` is the one submission that does not carry its content, and
it is not a hole of that shape: it names a Google Doc by URL, and what it can
reach is bounded by the author's own OAuth consent rather than by whatever the
process happens to have read access to. It answers with the same job id, polled
at the same `GET /jobs/{job_id}`. What differs is the ending — the corrections
are written back into the document in place, keeping its formatting, instead of
being handed back as text. See `corrector/drive.py`.

Nothing here authenticates or rate-limits, and every submission spends money at
a provider. Keep it on `127.0.0.1` until that is settled.
"""

from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from api.jobs import Job
from api.service import SubmissionError, get_corrector, get_job, submit_drive_job, submit_job
from api.web import WEB
from corrector.correct import Corrector

app = FastAPI(
    title="Editor Agent API",
    version="0.1.0",
)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

# The JSON endpoints live on their own router, mounted once below under
# `/api` — separate from the HTML router in `api/web.py`, which answers at
# `/`. The two used to share this router at two prefixes at once, for a Vite
# dev proxy that no longer exists now the front is server-rendered by this
# same process: `/api` is the one place the JSON contract lives now.
ROUTER = APIRouter()


class JobRequest(BaseModel):
    text: str = Field(description="The text to correct. UTF-8, in the body, no paths")


class JobCreated(BaseModel):
    """What a submission answers with: an id to poll, and nothing to report yet."""

    job_id: str
    status: str
    words: int


@ROUTER.get("/health")
def health():
    return {"status": "ok"}


@ROUTER.post("/jobs", status_code=202, response_model=JobCreated)
def submit(request: JobRequest, corrector: Corrector = Depends(get_corrector)):
    """Validate, create and enqueue via `api.service.submit_job` — see there for
    what makes a submission valid. This only translates a rejection into the
    status code the JSON contract has always answered with.
    """
    try:
        return submit_job(request.text, corrector)
    except SubmissionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


class DriveRequest(BaseModel):
    document: str = Field(description="URL del documento de Google Docs, o su identificador")


@ROUTER.post("/drive/jobs", status_code=202, response_model=JobCreated)
def submit_drive(request: DriveRequest, corrector: Corrector = Depends(get_corrector)):
    """Validate, create and enqueue via `api.service.submit_drive_job` — see
    there for what makes a submission valid. This only translates a rejection
    into the status code the JSON contract has always answered with.
    """
    try:
        return submit_drive_job(request.document, corrector)
    except SubmissionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@ROUTER.get("/jobs/{job_id}", response_model=Job)
def read(job_id: str):
    job = get_job(job_id)

    if job is None:
        raise HTTPException(
            status_code=404,
            detail="Ese trabajo no existe o ya ha caducado.",
        )

    return job


app.include_router(ROUTER, prefix="/api")

# The browser's own front: templates and vanilla JS/CSS, served by this same
# process off the same origin. `/api`, `/static` and the web router's bare
# paths (`/`, `/jobs`, `/jobs/{id}`) never overlap, so unlike the old
# StaticFiles mount this served the React build off, nothing here actually
# depends on registration order — it is kept in this order anyway because
# `/api` being the JSON contract and everything after it being the browser's
# concern is the more readable story.
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

app.include_router(WEB, include_in_schema=False)
