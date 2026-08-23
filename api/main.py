"""The HTTP surface: submit a text, poll for the correction.

The content travels in the body. There is no endpoint that takes a path —
`POST /correct-file` was removed rather than patched, because it read any file
the process could read, and `tests/test_api/test_main.py` pins its absence.

Nothing here authenticates or rate-limits, and every submission spends money at
a provider. Keep it on `127.0.0.1` until that is settled.
"""

from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from api.jobs import Job, JobStore
from corrector import presets, settings
from corrector.correct import Corrector
from corrector.edits import apply_edits

app = FastAPI(
    title="Editor Agent API",
    version="0.1.0",
)

STORE = JobStore()

# The endpoints live on a router rather than on the app because the app serves
# them at two prefixes at once. The front always calls `/api` on its own
# origin: in development Vite proxies that to `127.0.0.1:8000` and strips the
# prefix, so the call lands here as `/jobs`; in production this same process
# serves the build, and the browser's `/api/jobs` arrives with the prefix
# intact. Answering both is what lets the front hold one URL for both
# situations, instead of a build-time switch that can only be wrong in one of
# them.
ROUTER = APIRouter()

# One pass is mostly the model deliberating, so a worker spends its time
# waiting rather than computing and threads are the right shape for it. The
# pool is bounded all the same: it is what stops an unauthenticated endpoint
# from turning N submissions into N simultaneous fan-outs at the provider.
# Beyond it jobs queue, and a queued job reports `running` — from the front's
# side "not finished yet" is the same answer either way.
WORKERS = 4

EXECUTOR = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="correct")


@lru_cache
def get_corrector():
    """The production corrector, built lazily so importing this module never
    reaches for a provider client. A FastAPI dependency rather than a
    module-level value so tests can override it with a fake `generate`.

    Which configuration it is comes from `EDITOR_AGENT_SYSTEM`, and every
    choice it offers is a row the harness has scored — see `corrector/presets.py`.
    """
    return presets.build(settings.SYSTEM)


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
    # `detail` on these two is shown to the author in the browser verbatim, so
    # it is written in the language the product is in. The provider's messages
    # in `errors` are whatever the provider said, and are not translated.
    if not request.text.strip():
        raise HTTPException(
            status_code=400,
            detail="El texto está vacío.",
        )

    words = len(request.text.split())

    if words > settings.MAX_WORDS:
        raise HTTPException(
            status_code=413,
            detail=(
                f"El texto tiene {words} palabras y el máximo son {settings.MAX_WORDS}. "
                "Divídelo y envía las partes por separado."
            ),
        )

    job = STORE.create(words)
    EXECUTOR.submit(run, job.job_id, request.text, corrector)
    return job


@ROUTER.get("/jobs/{job_id}", response_model=Job)
def read(job_id: str):
    job = STORE.get(job_id)

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


def run(job_id, text, corrector):
    """One correction pass, from a worker thread, recorded in the store.

    Nothing raises out of here: the request that submitted the work is long
    gone, so an exception has nowhere to go but a thread's traceback, and the
    poller would sit on `running` forever. Every ending is written to the job.
    """
    try:
        correction = corrector.correct(text)
    except Exception as exc:
        # The pipeline records per-call failures rather than raising, so
        # reaching here is a bug or a broken configuration, not a bad call.
        STORE.fail(job_id, f"{type(exc).__name__}: {exc}")
        return

    # A pass whose every call failed must not read as "no errors found" — that
    # is what an invalid key looked like before: a completed job, applied 0,
    # and the text handed straight back. `correct` records one error per failed
    # call and counts one call per attempt, so `errors == calls` is the whole
    # pass failing, while anything less is per-block mode losing some blocks and
    # keeping the rest. That is a partial result worth returning, with the
    # failures still in the body rather than thrown away with it.
    if correction.errors and len(correction.errors) == correction.usage.calls:
        STORE.fail(
            job_id,
            "; ".join(correction.errors),
            errors=correction.errors,
            usage=correction.usage.model_dump(),
        )
        return

    corrected_text, apply_rejections = apply_edits(
        text,
        correction.edits,
    )

    rejected = dict(correction.rejected)

    for rejection in apply_rejections:
        rejected[rejection.reason] = rejected.get(rejection.reason, 0) + 1

    STORE.complete(
        job_id,
        text=corrected_text,
        proposed=correction.proposed,
        applied=len(correction.edits) - len(apply_rejections),
        skipped=sum(rejected.values()),
        rejected=rejected,
        errors=correction.errors,
        usage=correction.usage.model_dump(),
    )
