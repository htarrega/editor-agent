"""The HTTP surface: submit a text, poll for the correction.

The content travels in the body. There is no endpoint that takes a path —
`POST /correct-file` was removed rather than patched, because it read any file
the process could read, and `tests/test_api/test_main.py` pins its absence.

Nothing here authenticates or rate-limits, and every submission spends money at
a provider. Keep it on `127.0.0.1` until that is settled.
"""

from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from api.jobs import Job
from api.service import SubmissionError, get_corrector, get_job, submit_job
from corrector import settings
from corrector.correct import Corrector

app = FastAPI(
    title="Editor Agent API",
    version="0.1.0",
)

# The endpoints live on a router rather than on the app because the app serves
# them at two prefixes at once. The front always calls `/api` on its own
# origin: in development Vite proxies that to `127.0.0.1:8000` and strips the
# prefix, so the call lands here as `/jobs`; in production this same process
# serves the build, and the browser's `/api/jobs` arrives with the prefix
# intact. Answering both is what lets the front hold one URL for both
# situations, instead of a build-time switch that can only be wrong in one of
# them.
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


@ROUTER.get("/jobs/{job_id}", response_model=Job)
def read(job_id: str):
    job = get_job(job_id)

    if job is None:
        raise HTTPException(
            status_code=404,
            detail="Ese trabajo no existe o ya ha caducado.",
        )

    return job


def mount_web(app, directory=settings.WEB_DIST):
    """Serve the built front off this app, if there is a build to serve.

    Mounted at `/` and last, so it can never shadow an endpoint — Starlette
    matches in registration order and the router is already in. `html=True` is
    what answers `/` with `index.html`.

    A missing directory is the normal case, not an error: a development
    checkout has no `web/dist`, and StaticFiles refuses to point at a directory
    that is not there. Returns whether anything was mounted.
    """
    path = Path(directory)

    if not path.is_dir():
        return False

    app.mount("/", StaticFiles(directory=path, html=True), name="web")
    return True


app.include_router(ROUTER)

# The same endpoints again, where the browser looks for them in production.
# Out of the schema so `/openapi.json` describes one surface rather than each
# route twice under two names.
app.include_router(ROUTER, prefix="/api", include_in_schema=False)

mount_web(app)
