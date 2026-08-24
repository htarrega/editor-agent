"""The application service layer: one submission path, one lookup path.

`api/main.py` (the JSON API, mounted at `/api`) and `api/web.py` (the HTML
surface a browser gets at `/`) are two representations of the same two
operations — submit a text, look one up — and neither owns the logic. Both
call `submit_job` and `get_job` rather than touching `STORE` or `EXECUTOR`
directly, so what counts as a valid submission, and what a finished job looks
like, can only ever be decided in one place.

Nothing here renders anything: a JSON body and an HTML fragment are just two
ways of showing what these functions return.
"""

import re
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

from api.jobs import AppliedChange, Job, JobStore
from corrector import presets, settings
from corrector.correct import Corrector
from corrector.edits import apply_edits, partition_edits

STORE = JobStore()

# One pass is mostly the model deliberating, so a worker spends its time
# waiting rather than computing and threads are the right shape for it. The
# pool is bounded all the same: it is what stops an unauthenticated endpoint
# from turning N submissions into N simultaneous fan-outs at the provider.
# Beyond it jobs queue, and a queued job reports `running` — from either
# surface's side "not finished yet" is the same answer either way.
WORKERS = 4

EXECUTOR = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="correct")


@lru_cache
def get_corrector():
    """The production corrector, built lazily so importing this module never
    reaches for a provider client. A FastAPI dependency rather than a
    module-level value so tests can override it with a fake `generate`.

    Which configuration it is comes from `corrector/settings.py:SYSTEM` — a
    row the harness has scored, and, deliberately, not an environment
    variable: this is the one knob in `corrector.settings` that is not read
    from `os.environ`, so there is no deploy-time flip that changes what a
    request here gets served by. See `corrector/presets.py` and
    `corrector/settings.py`'s own comment on `SYSTEM` for why.
    """
    return presets.build(settings.SYSTEM)


class SubmissionError(Exception):
    """A text failed validation before a job ever existed: empty, or over the
    word ceiling.

    Carries the same status code and Spanish `detail` the JSON API has always
    answered with. `api/main.py` turns this into an `HTTPException`;
    `api/web.py` into an inline re-render of the compose form — same words,
    a different envelope around them.
    """

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def submit_job(text: str, corrector: Corrector) -> Job:
    """Validate, create, and enqueue a correction. The one place that does.

    `detail` on the two rejections is shown to the author verbatim, so it is
    written in the language the product is in. The provider's own messages,
    surfaced later through `errors`, are whatever the provider said, and are
    not translated.
    """
    if not text.strip():
        raise SubmissionError(400, "El texto está vacío.")

    words = len(text.split())

    if words > settings.MAX_WORDS:
        raise SubmissionError(
            413,
            f"El texto tiene {words} palabras y el máximo son {settings.MAX_WORDS}. "
            "Divídelo y envía las partes por separado.",
        )

    job = STORE.create(words)
    EXECUTOR.submit(run, job.job_id, text, corrector)
    return job


def get_job(job_id: str) -> Job | None:
    """Look up a job by id. `None` if it never existed, or has been evicted —
    the one place either surface asks `STORE`, so a caller that wants to
    change what "not found" means only has one function to change it in."""
    return STORE.get(job_id)


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

    # The edits `apply_edits` actually kept, spelled out one at a time rather
    # than just counted. `partition_edits` is the same left-to-right decision
    # `apply_edits` already made — recomputed rather than threaded through,
    # so this stays a read of what happened rather than a second opinion on it.
    accepted, _ = partition_edits(text, correction.edits)
    changes = [_change(text, edit) for edit in accepted]

    STORE.complete(
        job_id,
        text=corrected_text,
        proposed=correction.proposed,
        applied=len(accepted),
        skipped=sum(rejected.values()),
        rejected=rejected,
        changes=changes,
        errors=correction.errors,
        usage=correction.usage.model_dump(),
    )


_WORD_CHAR = re.compile(r"\w", re.UNICODE)


def _change(text: str, edit) -> AppliedChange:
    """An edit, shown with enough context to read at a glance.

    `edit` is already trimmed to the span that actually changes (`corrector.
    correct._resolve` does that before it ever reaches here), so a one-letter
    fix — «gatto» to «gato» — resolves to a single inserted or deleted letter,
    not a word. Exact, but «t» next to nothing does not read as anything.
    This widens the span outward to the word boundary on each side purely for
    display, so what is shown is the whole word before and after — the edit
    itself, and what it did to, is unchanged.
    """
    start, end = edit.start, edit.end
    while start > 0 and _WORD_CHAR.match(text[start - 1]):
        start -= 1
    while end < len(text) and _WORD_CHAR.match(text[end]):
        end += 1

    return AppliedChange(
        original=text[start:end],
        replacement=text[start : edit.start] + edit.replacement + text[edit.end : end],
        kind=edit.kind,
        rule=edit.rule,
    )
