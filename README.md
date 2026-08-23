# editor-agent

An autonomous corrector for literary Spanish. It finds the errors in a manuscript and
fixes them without rewriting the prose: a pipeline, a harness that scores it, an HTTP API
and a browser front.

- Design: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- Milestones and state: [`docs/PLAN.md`](docs/PLAN.md) — start at «Where we are»
- Evaluation harness: [`evals/README.md`](evals/README.md)

## Install

```bash
git clone git@github.com:htarrega/editor-agent.git
cd editor-agent

python3 -m venv venv
source venv/bin/activate
pip install -e ".[dev]"          # drop [dev] for the pipeline without ruff
```

The cheap model does the correcting. The strong one is the baseline it is measured against,
and is called only when a run asks for that row.

```bash
export DEEPSEEK_API_KEY=...      # the corrector's workhorse; the only one the product needs
export ANTHROPIC_API_KEY=...     # strong-model baseline, for the harness rows that ask
export GOOGLE_API_KEY=...        # optional, for `corrector-gemini`
```

`corrector/settings.py` holds every knob the pipeline reads. The harness's `EVAL_*`
variables win where they are set and fall back to these, so the two sides cannot drift onto
different configurations without anyone noticing.

**A fresh clone cannot run the harness yet.** The corpus is deliberately outside the
repository — it is the author's own prose and the reports quote it back (`docs/PLAN.md`,
H0). Drop clean `.txt` files into `evals/corpus/` first, or `evals.run` stops with
`FileNotFoundError`. A fragment must arrive free of typos of its own: on the untouched
corpus every edit counts as a false positive.

## Modes

Three configurations ship. `EDITOR_AGENT_SYSTEM` picks one, and `corrector/presets.py`
refuses a name that is not among them — a typo must not fall back to the default, or a run
looks fine while measuring something nobody asked for.

| `EDITOR_AGENT_SYSTEM` | s/document | worst | F0.5 | P | $/10k words | |
|---|---|---|---|---|---|---|
| **`raced`** | **4.35** | 4.78 | 0.919 | 0.936 | 0.171 | **the default, and what the API ships** |
| `blocks` | ~88 | ~90 | **0.947** | 0.960 | **0.019** | the reference row: best measured, and what the harness scores by default |
| `fast` | **2.4** | 3.5 | 0.867 | 0.904 | 0.056 | |

Seconds are per document, wall clock, on a 2,000-word fragment; `--repeats 3` on the
8,254-word corpus. `rules-only` — no model at all — sits under all three at 0.789 F0.5 for
0.00 s and nothing, which is what the model is being paid for.

```bash
uvicorn api.main:app                               # raced, the default
EDITOR_AGENT_SYSTEM=blocks uvicorn api.main:app    # the reference row
EDITOR_AGENT_SYSTEM=fast uvicorn api.main:app

# the rows above, re-measured. One document at a time, or s/document
# measures queueing rather than the pass.
python -m evals.run --systems corrector-blocks,corrector-raced,corrector-fast,rules-only \
       --repeats 3 --concurrency 1
```

### Why the default is not the best row

`blocks` is one call for the whole document and the best F0.5 measured, but ~87% of its 88
seconds is the model deliberating, and every row that took the clock down took quality with
it. `raced` takes the clock down without giving up the deliberation: one block per call
scores 0.948 on its own but takes 19 s, and all 19 are the tail — the median call is 4.3 s.
So each call is issued **three times at once and the first answer wins**, under a hard 4.3 s
deadline, with a fast no-reasoning ticket queued first for every block so nothing comes back
empty. All 32 measured documents finished under five seconds.

The quality difference is 0.036 against a run-to-run spread of 0.043 — smaller than this
harness can resolve — and it costs 9× the money. That trade was never the harness's to make;
**the author took it.** What the harness measures did not move with it: `blocks` is still
what `python -m evals.run` scores by default and what every cached report quotes, or the
rows would stop meaning what they say.

Precision is where the clock is paid for, and it is worth seeing on real prose before
choosing: on an 834-word fragment `blocks` applied 10 edits and invented none, `raced` found
13 — three the slow pass missed — and got one wrong, `fast` found 6 and got the same one
wrong. Details and the Gemini lead are in `docs/PLAN.md`.

## Run

Nothing below needs a key or the corpus:

```bash
python -m unittest discover -s tests -t .    # the whole suite, offline
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

A FastAPI wrapper over the pipeline. It needs `DEEPSEEK_API_KEY` and never touches the
filesystem. Work is submitted and polled rather than awaited: the default mode answers in
about four seconds, but `blocks` takes 60–90 s on a 2k-word fragment, and that is not a wait
a single request can hide. One contract for every mode, so changing `EDITOR_AGENT_SYSTEM`
never changes how the front talks to it.

```bash
uvicorn api.main:app

curl -X POST localhost:8000/jobs \
  -H 'Content-Type: application/json' \
  -d '{"text": "El niño comio una manzana, y luego se fue ha casa."}'
# {"job_id": "...", "status": "running", "words": 11}

curl localhost:8000/jobs/<job_id>
# {"status": "completed", "text": "El niño comió una manzana, y luego se fue a casa.", ...}
```

Every endpoint answers at `/api/...` as well, because that is where the browser looks once
the same process serves the front — see [Deploy](#deploy).

The finished body carries the corrected text and what was proposed, applied and rejected. A
job whose every call failed ends `failed` with the reason in `detail`, rather than
completing with the original text in a way indistinguishable from "no errors found"; a job
that lost only some of its calls completes with what the rest produced, failures in
`errors`.

Texts over `EDITOR_AGENT_MAX_WORDS` (2,000) are refused at submit with a `413`. That is a
measured ceiling, not a policy: there is no document-level pass yet (`docs/PLAN.md`, H5), so
above it the pipeline runs where nobody has scored it.

`GET /health` answers without touching a provider or building a corrector, so it is safe to
poll. `POST /correct-file` is gone — it read any path the process could read, and
`tests/test_api/test_main.py` pins its absence.

## Front

A browser front over the API — paste or upload a manuscript, get it back corrected. Its own
npm project, and it needs the API running beside it:

```bash
uvicorn api.main:app                     # one terminal
cd web && npm install && npm run dev     # the other, http://localhost:5173
```

The front always calls `/api` on its own origin: Vite proxies it to `127.0.0.1:8000` in
development and strips the prefix, and in production the API process serves the build and
answers the prefix itself. No CORS in either, and the two cannot drift apart. Details in
[`web/README.md`](web/README.md).

## Deploy

One image. The front is built inside it and the API process serves the build, so the browser
talks to a single origin — the shape the Vite proxy imitates in development.

```bash
docker build -t editor-agent .
docker run -p 127.0.0.1:8000:8000 -e DEEPSEEK_API_KEY=... editor-agent
```

`http://localhost:8000` is the front; the API is under `/api` and at the root. Nothing else
is required: `ANTHROPIC_API_KEY` is the harness's, not the product's, and the corpus is
never in the image.

| variable | |
|---|---|
| `DEEPSEEK_API_KEY` | required — nothing corrects without it |
| `EDITOR_AGENT_SYSTEM` | `raced` (default), `blocks` or `fast` — see [Modes](#modes) |
| `EDITOR_AGENT_MAX_WORDS` | the `413` ceiling, 2,000 by default |
| `EDITOR_AGENT_WEB_DIST` | where the build is; the image already points it at `/app/web/dist` |

The image declares `/health` as its `HEALTHCHECK`, and it is the right readiness probe
anywhere else: it answers without building a corrector or reaching the provider.

Two things to settle before it faces anyone but you:

- **Nothing authenticates or rate-limits `POST /jobs`, and every submission spends money at
  a provider.** Publish it on `127.0.0.1` as above, or put something that authenticates in
  front of it.
- **Jobs live in the process's memory, and the newest 256 are kept.** One container: a
  restart loses what is in flight, and a second replica behind a round-robin answers `404`
  to half the polls. Scaling out means moving the store out of the process first.

Without a container it is the same three pieces by hand:

```bash
pip install .
cd web && npm ci && npm run build && cd ..
EDITOR_AGENT_WEB_DIST=web/dist uvicorn api.main:app --host 0.0.0.0
```

## Layout

| package | what it is |
|---|---|
| `corrector/` | the pipeline — the product. Imports nothing from `evals/` |
| `evals/` | the harness that measures it |
| `api/` | the HTTP wrapper over the pipeline, and what serves the built front |
| `web/` | the browser front, over the HTTP wrapper — [`web/README.md`](web/README.md) |
| `tests/` | `tests/test_corrector/`, `tests/test_evals/` and `tests/test_api/`, mirroring the three |

`web/` is the only part `pip install -e .` does not install: it is an npm project of its own
and the Python packaging ignores it entirely. It talks to `api/` across HTTP and shares no
code with it, so the two can be built and tested apart — what they share is the endpoint
contract, written down in both READMEs, and one container at deploy time.

Installing the project is what makes `corrector` importable from outside the repository
root. The test directories carry a `test_` prefix so neither can shadow the package it
tests: a directory named `corrector/` under `tests/` is a second, empty `corrector` on the
import path the moment a runner puts `tests/` on it.

The tests run offline — the corpus lives outside the repository (`docs/PLAN.md`, H0), and
the one test that wants it skips when it is absent.
