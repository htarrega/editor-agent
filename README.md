# editor-agent

An autonomous corrector for literary Spanish: it finds the errors in a manuscript and fixes
them without rewriting the prose. A pipeline, a harness that scores it, an HTTP API and a
browser front.

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

```bash
export DEEPSEEK_API_KEY=...      # the corrector's workhorse; the only key the product needs
export ANTHROPIC_API_KEY=...     # strong-model baseline, for the harness rows that ask
export GOOGLE_API_KEY=...        # optional, for `corrector-gemini`
```

Every knob is in `corrector/settings.py`; the harness's `EVAL_*` variables override it where
they are set, so the product and the harness cannot drift onto different configurations
without anyone noticing.

**A fresh clone cannot run the harness.** The corpus is deliberately outside the repository —
it is the author's own prose and the reports quote it back (`docs/PLAN.md`, H0). Drop clean
`.txt` files into `evals/corpus/` first, or `evals.run` stops with `FileNotFoundError`. They
have to arrive free of typos of their own: on the untouched corpus every edit counts as a
false positive.

## Modes

`EDITOR_AGENT_SYSTEM` picks one of four, and an unknown name is an error rather than a
fallback to the default. `$/10k words` is computed from each row's own input/output tokens at
the rate that actually applied when it ran (`corrector/llm.py`); numbers below are 2026-08-24
except `fast`'s, which predates a DeepSeek pricing change and is not directly comparable — see
`docs/PLAN.md`, "The deadline was a bet".

| `EDITOR_AGENT_SYSTEM` | s/document | F0.5 | P | $/10k words | |
|---|---|---|---|---|---|
| **`blocks`** | ~74 | **0.963** | 0.974 | 0.0415 | **default; what the API ships; two consistent draws** |
| `raced` | **5.6** | 0.860 | 0.933 | 0.0824 | no longer shipped — the deadline is a bet on the hour |
| `fast`¹ | **2.4** | 0.867 | 0.904 | 0.056¹ | |

¹ Not re-measured on 2026-08-24; its row is the pre-2026-08-16 one.

Two more names work with `EDITOR_AGENT_SYSTEM` but are not the default — `lean` (refuted:
cheaper, real recall lost) and `swept` (promising, one draw, not two — see
`corrector/presets.py`) — because nothing here becomes the default off less evidence than the
row it would replace. `rules-only` — no model at all — sits under every row above at 0.789
F0.5, for 0.00 s and nothing.

```bash
uvicorn api.main:app                               # blocks, the default
EDITOR_AGENT_SYSTEM=raced uvicorn api.main:app
EDITOR_AGENT_SYSTEM=fast uvicorn api.main:app

# the rows above, re-measured. One document at a time, or s/document
# measures queueing rather than the pass.
python -m evals.run --systems corrector-blocks,corrector-raced,corrector-fast,rules-only \
       --repeats 3 --concurrency 1
```

`blocks` is one call for the whole document, and most of its ~74 seconds is the model
deliberating. `raced` keeps the deliberation and drops the tail: every call goes out **three
times at once and the first answer wins**, under a hard 4.3 s deadline, with a no-reasoning
ticket queued first so no block comes back empty. That bought a quality difference inside the
harness's own run-to-run spread — on the day it was measured. Re-measured, alone and
uncontended, the deadline cost real recall: the provider was slower that hour, more blocks
missed their deliberated attempts, and the cheap fallback answered instead. `blocks` is now
both cheaper and better measured, which is why it ships.

Two cost candidates were tried past `blocks` and neither is shipped. `lean` — the same single
call, asked about fewer error types — barely shortened the model's own reasoning and cost real
recall to ask for less. `swept` runs the free rule pack *before* the call instead of after, so
the model reads a text with nothing left in it that looks like the four rule-decidable error
types, rather than being told to ignore them where it sees them; measured once, that was 15%
cheaper than `blocks` with recall slightly *up*, not down — but it is one draw, this document's
own bar for a claim is `--repeats 3`, and the DeepSeek key ran out of balance mid-run before a
second draw could confirm it. A paid Gemini key was also tried, settling a question this file
used to leave open: `gemini-2.5-flash`, one call, the whole document — F0.5 0.934 at $0.107 per
10k words, worse *and* over twice `blocks`' cost. The numbers for all of this are in
`docs/PLAN.md`.

## Run

Nothing here needs a key or the corpus:

```bash
python -m unittest discover -s tests -t .    # the whole suite, offline
ruff format . && ruff check .                # formatting and import order
```

Everything here calls a paid API, and each run prints what it spent:

```bash
python -m evals.run                          # metrics table + cost of the run
python -m evals.run --limit-words 300        # a cheap smoke run first
python -m evals.run --systems null           # no calls at all; checks the corpus loads

python -m evals.run --reuse                  # reuse cached baselines, call only what is new
python -m evals.run --reuse --fresh corrector-blocks   # ...but never cache the one being built
```

A full run is a few cents for the pipeline and about $1.32 for the strong-model baseline,
which is why `--reuse` exists. One run is a draw from a distribution — use `--repeats 3`
before believing a comparison. Every flag is in [`evals/README.md`](evals/README.md).

## API

A FastAPI wrapper over the pipeline, at `/api`. It needs `DEEPSEEK_API_KEY`, and no endpoint
takes a path: the content travels in the body. Work is submitted and polled rather than
awaited — `blocks` takes 60–90 s and `raced` answers in about five, and one contract covers
both, so changing the mode never changes how a client talks to it.

```bash
uvicorn api.main:app

curl -X POST localhost:8000/api/jobs \
  -H 'Content-Type: application/json' \
  -d '{"text": "El niño comio una manzana, y luego se fue ha casa."}'
# {"job_id": "...", "status": "running", "words": 11}

curl localhost:8000/api/jobs/<job_id>
# {"status": "completed", "text": "El niño comió una manzana, y luego se fue a casa.", ...}
```

| | |
|---|---|
| `POST /api/jobs` | `{"text": ...}` in, a job id out. `400` if empty, `413` over `EDITOR_AGENT_MAX_WORDS` |
| `GET /api/jobs/{id}` | `status`, and on completion `text`, `applied`, `proposed`, `skipped`, `changes`, `errors`, `detail` |
| `GET /api/health` | answers without building a corrector or reaching a provider |

This is the one JSON contract, for any programmatic client, and it is not migrated to HTML —
see [Front](#front) for the browser's own surface, which calls the same `submit_job`/`get_job`
underneath but answers at the bare paths (`/`, `/jobs`, `/jobs/{id}`) with HTML instead.

A job whose every call failed ends `failed` with the reason in `detail`, never `completed`
carrying the original text: that would be indistinguishable from "no errors found". One that
lost only some of its calls completes with what the rest produced, failures in `errors`.

`changes` is `applied` spelled out one edit at a time — `original`, `replacement`, `kind`,
`rule` — widened to its word boundary from whatever minimal span the pipeline actually
touched, so a one-letter fix reads as the word it landed in rather than a lone character.

The 2,000-word ceiling is measured, not policy — there is no document-level pass yet
(`docs/PLAN.md`, H5), so above it the pipeline runs where nobody has scored it.

## Front

A browser front over the API — paste or upload a manuscript, get it back corrected. Server-
rendered: `templates/` (Jinja2) and `static/` (vendored HTMX, vanilla CSS/JS, no build step)
are served by the same process as the API, so one command is the whole development setup:

```bash
uvicorn api.main:app                     # http://localhost:8000
```

There is no second dev server and nothing to build first — HTMX makes the requests the old
React front made from the browser, the templates render what the JSON API's `Job` already
carries, and `static/app.js` is left with only what genuinely has to run client-side: reading
an uploaded file into the textarea, the clipboard, the blob download, Ctrl/Cmd+Enter to
submit. `GET /`, `POST /jobs` and `GET /jobs/{id}` answer HTML at these same paths the JSON
API used to share with the browser; the JSON contract itself did not move — see
[API](#api) — it kept `/api` and gave up the bare paths in exchange.

## Deploy

One image, one stage: there is no build to run first, so the same process that answers
`/api` serves `templates/` and `static/` straight off disk.

```bash
docker build -t editor-agent .
docker run -p 127.0.0.1:8000:8000 -e DEEPSEEK_API_KEY=... editor-agent
```

`http://localhost:8000` is the front; the API is under `/api`. Nothing else is required —
`ANTHROPIC_API_KEY` is the harness's, not the product's, and the corpus is never in the image.

| variable | |
|---|---|
| `DEEPSEEK_API_KEY` | required; nothing corrects without it |
| `EDITOR_AGENT_SYSTEM` | `blocks` (default), `raced`, `fast`, `lean` or `swept` — see [Modes](#modes) |
| `EDITOR_AGENT_MAX_WORDS` | the `413` ceiling, 2,000 by default |

`/api/health` is the image's own `HEALTHCHECK` and the right readiness probe anywhere else.

Two things to settle before it faces anyone but you:

- **Nothing authenticates or rate-limits a submission (`POST /api/jobs` or the front's own
  `POST /jobs`), and every one spends money at a provider.** Publish it on `127.0.0.1` as
  above, or put something authenticating in front.
- **Jobs live in the process's memory, newest 256 kept.** One container: a restart loses what
  is in flight, and a second replica behind a round-robin answers `404` to half the polls.
  Scaling out means moving the store out of the process first.

Without a container it is the same one piece by hand:

```bash
pip install .
uvicorn api.main:app --host 0.0.0.0
```

## Layout

| | |
|---|---|
| `corrector/` | the pipeline — the product. Imports nothing from `evals/` |
| `evals/` | the harness that measures it |
| `api/` | the JSON API (`/api`) and the HTML web router (`/`) — one service layer, two representations; neither duplicates the other's validation or job lookup |
| `templates/` + `static/` | the browser front: Jinja2 templates and vendored HTMX/CSS/JS, rendered and served by `api/`. No package, no build step |
| `tests/` | `test_corrector/`, `test_evals/`, `test_api/` and `test_web/`, mirroring the four |

`templates/` and `static/` are not Python and `pip install -e .` does not install them; they
just have to be on disk next to `api/` wherever it runs, which `Dockerfile` and the commands
above both already arrange. The test directories carry a `test_` prefix so none can shadow the
package it tests. The suite runs offline, and the one test that wants the corpus skips when it
is absent.
