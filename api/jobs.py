"""Where a submitted correction lives while it runs.

A pass takes 60–90 s, which is longer than a request may block, so the work is
accepted and polled for rather than awaited. That needs somewhere to keep the
result between the two requests, and this is it: a dictionary in the API
process's memory.

That is the whole storage story, deliberately. One container, and a restart
loses what was in flight. Anything better wants a queue, and a queue wants an
operational story this project does not have yet (docs/PLAN.md, «Interfaces»).
"""

import threading
import uuid
from typing import Literal

from pydantic import BaseModel

# How many finished jobs are kept before the oldest are dropped. The process is
# meant to stay up for days and every submission adds a corrected document to a
# dictionary that nothing ever removes from; without a cap that is a slow leak
# of the largest thing the API touches. A poller that comes back after 256 other
# jobs have finished gets a 404, which is the same answer it would get after a
# restart and is already a case it has to handle.
CAPACITY = 256

Status = Literal["running", "completed", "failed"]


class AppliedChange(BaseModel):
    """One correction that made it into the final text — apart from the text itself.

    `original` is read off the accepted edit's own span in the *source* text
    rather than carried from the model's proposal, so what this shows is
    guaranteed to be what the manuscript actually said before the swap, not
    what the model claimed it was replacing.
    """

    original: str
    replacement: str
    kind: str
    rule: str


class Job(BaseModel):
    """A submitted correction: what it is doing, and what it produced.

    `text` is `None` until the pass completes, and stays `None` when it fails —
    handing the original text back on failure is indistinguishable from "this
    text was already clean", which is the mistake this whole shape exists to
    avoid.

    `changes` is every edit that survived into `text`, in reading order — the
    same count `applied` gives as a number, spelled out one correction at a
    time. It stays empty until the job completes, and stays empty on a text
    that needed no correcting at all.
    """

    job_id: str
    status: Status
    words: int
    text: str | None = None
    proposed: int = 0
    applied: int = 0
    skipped: int = 0
    rejected: dict[str, int] = {}
    changes: list[AppliedChange] = []
    errors: list[str] = []
    detail: str | None = None
    usage: dict = {}


class JobStore:
    """The jobs, and the lock that lets a worker thread and a poller share them.

    A job is never mutated in place: finishing one replaces the whole object
    under the lock. That is what lets `get` hand the caller the model itself
    rather than a copy — there is no window in which a poller can observe a job
    that is half written.
    """

    def __init__(self, capacity=CAPACITY):
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._capacity = capacity

    def create(self, words):
        job = Job(job_id=uuid.uuid4().hex, status="running", words=words)
        with self._lock:
            self._jobs[job.job_id] = job
            self._evict()
        return job

    def get(self, job_id):
        with self._lock:
            return self._jobs.get(job_id)

    def complete(self, job_id, **fields):
        self._finish(job_id, "completed", fields)

    def fail(self, job_id, detail, **fields):
        self._finish(job_id, "failed", {**fields, "detail": detail})

    def _finish(self, job_id, status, fields):
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                # Evicted, or a store reused across tests. The work is done and
                # there is nobody left to hand it to; dropping it is the whole
                # of the correct behaviour.
                return
            self._jobs[job_id] = job.model_copy(update={"status": status, **fields})

    def _evict(self):
        """Drop the oldest finished jobs until the store is back inside its cap.

        A running job is never evicted, however old: it is work in progress
        whose result nobody has seen yet, and dropping it loses a paid call and
        leaves the front polling an id that will never answer. If every job is
        running the cap simply yields — the alternative is throwing away the
        thing the user is waiting for.
        """
        while len(self._jobs) > self._capacity:
            oldest = next(
                (job_id for job_id, job in self._jobs.items() if job.status != "running"),
                None,
            )
            if oldest is None:
                return
            del self._jobs[oldest]
