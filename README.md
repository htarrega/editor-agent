# editor-agent

An autonomous text correction agent, with access to local files and Google Drive.

- Design: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- Milestones and state: [`docs/PLAN.md`](docs/PLAN.md) — start at «Where to pick this up»
- Evaluation harness: [`evals/README.md`](evals/README.md)

## Install

```bash
git clone git@github.com:htarrega/editor-agent.git
cd editor-agent

python3 -m venv venv
source venv/bin/activate
pip install -e ".[dev]"          # drop [dev] for the pipeline without ruff
```

Two provider keys. The cheap model does the correcting; the strong one is the baseline it
is measured against, and is only called when a run asks for that row.

```bash
export DEEPSEEK_API_KEY=...      # the corrector's workhorse
export ANTHROPIC_API_KEY=...     # strong-model baseline
```

`corrector/settings.py` holds the three knobs the pipeline reads — `EDITOR_AGENT_MODEL`,
`EDITOR_AGENT_EFFORT`, `EDITOR_AGENT_BLOCK_WORDS`. The harness's `EVAL_*` variables still
win where they are set, and fall back to these, so both sides cannot drift onto different
configurations without anyone noticing.

**A fresh clone cannot run the harness yet.** The corpus is deliberately not in the
repository — it is the author's own prose, and the reports quote it back (`docs/PLAN.md`,
H0). Drop one or more clean `.txt` files into `evals/corpus/` first; without them
`evals.run` stops with `FileNotFoundError: no .txt fragments in evals/corpus`. A fragment
must go in free of typos of its own: on the untouched corpus every edit counts as a false
positive, so a pre-existing typo contaminates the headline metric.

## Speed

The default pass takes **~88 s** on a 2,000-word document, and about 87% of that is the model
deliberating (`docs/PLAN.md`, H1). `corrector-fast` is the other end of that trade:

```bash
python -m evals.run --systems corrector-blocks,rules-only,corrector-fast \
       --repeats 3 --concurrency 1        # --concurrency 1: one document at a time,
                                          # or the s/doc column measures queueing
```

| | F0.5 | P | FP/1k clean | s per document | worst | $/10k words |
|---|---|---|---|---|---|---|
| `corrector-blocks` (default) | **0.947** | 0.960 | 0.12 | ~88 | ~90 | **0.019** |
| **`corrector-raced`** | 0.919 | 0.936 | 0.36 | **4.35** | **4.78** | 0.171 |
| `corrector-fast` | 0.867 | 0.904 | 0.24 | 2.4 | 3.5 | 0.056 |
| `rules-only` | 0.789 | 0.970 | 0.12 | **0.00** | 0.01 | **0.000** |
| `corrector-gemini` | 0.994¹ | 1.000 | — | 31 | — | — |

`corrector-raced` is the row that meets a five-second budget without giving up the
deliberation that recall is made of. One block per call at `reasoning_effort=minimal` already
scores 0.948 on its own — the default's number — but takes 19 s, and all 19 are the tail: the
median call is 4.3 s and the slowest is 19. So each call is issued **three times at once and
the first answer wins**, under a hard 4.3 s deadline, with a fast no-reasoning ticket queued
first for every block so nothing can come back empty. Every one of 32 measured documents
finished under five seconds, the worst at 4.78, with no failed calls.

The quality difference is 0.036 against a run-to-run spread of 0.043 — smaller than the noise
this harness can resolve. It costs 9× the money (still cents, and still 9× cheaper than a
strong model with a naive prompt). **The default does not move**: 20× the speed for 9× the
bill is not a trade the harness can make for you.

¹ one draw on one fragment — the `--repeats 3` run lost 15 of 16 calls to the free tier's
20-requests-a-day limit. `corrector-gemini` is one call for the whole document and is both
better and three times faster than today's default. A paid Google key is the single most
valuable thing anyone can add here.

## Run

Nothing below needs a key or the corpus:

```bash
python -m unittest discover -s tests -t .    # 130 tests, offline
ruff format . && ruff check .                # formatting and import order
```

Everything below calls a paid API, and each run prints what it spent:

```bash
python -m evals.run                          # metrics table + cost of the run
python -m evals.run --limit-words 300        # a cheap smoke run first
python -m evals.run --systems null           # no calls at all; checks the corpus loads

python -m evals.run --reuse                  # reuse cached baselines, call only what is new
python -m evals.run --reuse --fresh corrector-blocks   # ...but never cache the one being built
```

A full run is a few cents for the pipeline and about $1.32 for the strong-model baseline,
which is why `--reuse` exists. One run is a draw from a distribution rather than a
measurement — use `--repeats 3` before believing a comparison. The flags are documented in
[`evals/README.md`](evals/README.md).

## API

A FastAPI wrapper over the pipeline. It needs `DEEPSEEK_API_KEY`; it never touches the
filesystem. Work is submitted and polled rather than awaited — a pass runs 60–90 s on a
2k-word fragment and that is not a wait a single request can hide.

```bash
uvicorn api.main:app

curl -X POST localhost:8000/jobs \
  -H 'Content-Type: application/json' \
  -d '{"text": "El niño comio una manzana, y luego se fue ha casa."}'
# {"job_id": "...", "status": "running", "words": 11}

curl localhost:8000/jobs/<job_id>
# {"status": "completed", "text": "El niño comió una manzana, y luego se fue a casa.", ...}
```

The finished body carries the corrected text and what was proposed, applied and rejected. A
job whose every call failed ends `failed` with the reason in `detail`, instead of completing
with the original text in a way that is indistinguishable from "no errors found"; a job that
lost only some of its calls completes with what the rest produced, failures in `errors`.

Texts over `EDITOR_AGENT_MAX_WORDS` (2,000) are refused at submit with a `413`. That is a
measured ceiling rather than a policy — there is no document-level pass yet (`docs/PLAN.md`,
H5), so above it the pipeline runs where nobody has scored it.

Jobs live in the process's memory: one container, and a restart loses what was in flight.

**Nothing authenticates or rate-limits `POST /jobs`, and every call spends money at a
provider.** Keep it on `127.0.0.1` until that is settled.

## Layout

| package | what it is |
|---|---|
| `corrector/` | the pipeline — the product. Imports nothing from `evals/` |
| `evals/` | the harness that measures it |
| `api/` | the HTTP wrapper over the pipeline |
| `tests/` | `tests/test_corrector/`, `tests/test_evals/` and `tests/test_api/`, mirroring the three |

Installing the project is what makes `corrector` importable from outside the repository
root. The test directories carry a `test_` prefix so that neither can ever shadow the
package it tests: a directory named `corrector/` under `tests/` is a second, empty
`corrector` on the import path the moment a runner puts `tests/` on it.

The tests run offline — the corpus lives outside the repository (`docs/PLAN.md`, H0), and
the one test that wants it skips when it is absent.
