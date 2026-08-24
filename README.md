# editor-agent

An autonomous corrector for literary Spanish: it finds the errors in a manuscript and fixes
them without rewriting the prose. A pipeline, a harness that scores it, an HTTP API, a
browser front, and a Google Doc corrected in place with its formatting intact.

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

The API runs `bare` — one configuration, chosen in `corrector/settings.py:SYSTEM`, and not an
environment variable: unlike every other knob in that module, there is no `EDITOR_AGENT_SYSTEM`
to flip it at deploy time. Six configurations exist in `corrector/presets.py`, each pinned to a
row the harness has scored, but only `bare` is reachable through the running API; the rest are
there to be measured and compared, through the harness, not selected in production. `$/10k
words` below is computed from each row's own input/output tokens at the rate that actually
applied when it ran (`corrector/llm.py`); numbers are 2026-08-24.

| preset | s/document | F0.5 | P | $/10k words | |
|---|---|---|---|---|---|
| **`bare`** | ~7.5 | **0.902**¹ | 0.914 | **0.0083**¹ | **what the API ships; three draws, pooled** |
| `blocks` | ~74 | 0.963 | 0.974 | 0.0415 | reference row; best F0.5 measured |
| `swept` | ~60-66 | 0.942-0.952 | 0.946-0.955 | 0.0353-0.0354 | higher quality than `bare`, ~4× its cost |
| `raced` | 5.6 | 0.860 | 0.933 | 0.0824 | no longer shipped — the deadline is a bet on the hour |
| `swift` | 3.8 | 0.879 | 0.916 | 0.0881 | refuted — costs more than `blocks`, not less |
| `fast` | 2.4 | 0.870 | 0.910 | 0.0888 | refuted — same windowing cost as `swift` |

¹ Pooled across three draws (different seeds) — see `corrector/presets.py:bare`.

`lean` exists in the same file, refuted the same way — cheaper for real recall lost — and
`rules-only` — no model at all — sits under every row above at 0.789 F0.5, for 0.00 s and
nothing. Changing what the API runs means editing `corrector/settings.py:SYSTEM` and
re-deploying, which is deliberate: a choice this consequential should leave a commit.

```bash
uvicorn api.main:app                               # bare, the only thing this runs

# the rows above, re-measured — not what the running API answers with, only
# how the choice of SYSTEM was made. One document at a time, or s/document
# measures queueing rather than the pass.
python -m evals.run --systems corrector-bare,corrector-blocks,corrector-swept,rules-only \
       --repeats 3 --concurrency 1
```

`blocks` is one call for the whole document, and most of its ~74 seconds is the model
deliberating. `raced` kept the deliberation and dropped the tail: every call issued **three
times at once, first answer wins**, under a hard 4.3 s deadline, with a no-reasoning ticket
queued first so no block came back empty. That bought a quality difference inside the harness's
own run-to-run spread — on the day it was measured. Re-measured, alone and uncontended, the
deadline cost real recall: the provider was slower that hour, more blocks missed their
deliberated attempts, and the cheap fallback answered instead — F0.5 0.860, not the 0.919 it
shipped on. `blocks` replaced it: cheaper and better at once.

`swept` runs the free rule pack *before* the call instead of after, so the model reads a text
with nothing left in it that looks like the four rule-decidable error types, rather than being
told to ignore them where it sees them — confirmed on two draws, ~12-15% cheaper than `blocks`
with recall at or above it, not traded away. `bare` is `swept` with deliberation switched off
entirely. That shape was expected to fail — every earlier test of `reasoning_effort=none` in
this codebase, on raw text, cost real recall — but nobody had tried it on text the rule pack
had already cleared. On three independent draws it did not fail: F0.5 0.902 pooled, *above*
`raced`'s own 0.860, at $0.0083 per 10k words. `swift` and `fast` were the reasoned, windowed
guess for the same idea and were refuted by measurement — 549 small calls each re-sending
their context costs more in input tokens than a near-empty output saves; `bare` keeps `swept`'s
16-call shape and avoids that entirely. A paid Gemini key was tried too, settling a question
this file used to leave open: `gemini-2.5-flash`, one call, the whole document — F0.5 0.934 at
$0.107 per 10k words, worse *and* over twice `blocks`' cost. The full numbers, including the
three individual `bare` draws and what `reasoning_effort=none` occasionally does to a reply's
JSON, are in `docs/PLAN.md`.

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
awaited — `bare` answers in a few seconds, `blocks` takes 60–90 s, and one contract covers
every mode, so changing it never changes how a client talks to the API.

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
| `POST /api/drive/jobs` | `{"document": <URL o id>}` in, the same job id out — see [Google Docs](#google-docs) |
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

## Google Docs

A Doc is corrected **in place**. It is never exported, corrected and uploaded back — that
round trip is what flattens a manuscript. The pipeline's anchored edits are resolved to the
document's own indices and applied as `insertText` + `deleteContentRange`, so only corrected
words are ever named in a request and bold, italics, indents, paragraph styles and images
survive because nothing rewrote them.

```bash
pip install -e ".[drive]"        # google-api-python-client, google-auth-oauthlib
python -m corrector.drive login  # once, on the host: opens a browser, stores a token

python -m corrector.drive https://docs.google.com/document/d/<id>/edit   # the whole cycle
```

`login` is an interactive consent flow, so it happens on a machine with a browser — never
inside a container. The token it writes is what gets carried anywhere else, and it has to
stay writable: refreshing rewrites it.

There is no browser tab for this yet — `POST /api/drive/jobs` is the only way in for now.
What should trigger a correction on a real Doc (a tab on this front, a Chrome extension, an
Apps Script add-on) is still open; see `docs/PLAN.md`, H6, «The trigger».

Two guards make the in-place claim hold rather than merely hope for it, both pinned by
`tests/test_corrector/test_drive.py`. An edit whose span or replacement contains a newline is
dropped: in Docs a newline *is* the paragraph, and deleting one merges two and loses a
paragraph style. And the write pins the `revisionId` that was read, so a document the author
kept typing into fails instead of applying corrections at indices that have moved.

Setup is one OAuth client, once, and it cannot be skipped: Google does not implement dynamic
client registration, so there is no way to mint one from code. In
[Google Cloud Console](https://console.cloud.google.com/), on one project: enable the Docs
API, configure the consent screen as **External** and add yourself under **Test users**, then
create an OAuth client ID of type **Desktop app** and save the JSON as
`~/.config/editor-agent/client_secret.json` (`EDITOR_AGENT_GOOGLE_CLIENT_SECRETS` and
`EDITOR_AGENT_GOOGLE_TOKEN` move it).

**Set the app to «In production» while you are there.** The Docs scope is a sensitive one, and
Google expires the refresh token of an app left in *Testing* after seven days — meaning
`login` again every week. In production it stops expiring; the app is still unverified, so
consent shows a warning screen you click through, which for your own client is the point.

In a host install without the extra the endpoint answers `501` with the install line, and the
rest of the API is unaffected; the container image installs it.

The job's `text` is what the document says afterwards, built only from the edits Drive
accepted, so the API cannot report a correction the document does not have. Scope is the
body of the first tab: tables, footnotes, headers and footers are not read, and so are never
corrected.

**Not verified against a real document yet** — everything above is tested against literal
document payloads and a fake service (`tests/test_corrector/test_drive.py`); the arithmetic
is pinned, the round trip is not. `python -m corrector.drive <url>` is what settles it.

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
| `EDITOR_AGENT_MAX_WORDS` | the `413` ceiling, 2,000 by default |
| `EDITOR_AGENT_GOOGLE_CLIENT_SECRETS` | the OAuth client JSON — see [Google Docs](#google-docs) |
| `EDITOR_AGENT_GOOGLE_TOKEN` | the author's token; mount it **writable**, refreshing rewrites it — and see [Google Docs](#google-docs), because a writable mount does not save a token Google expired on its own |

`/api/health` is the image's own `HEALTHCHECK` and the right readiness probe anywhere else.

**Google Docs needs one extra thing in a container**, and it does not happen by itself. The
image installs the `drive` extra, so the endpoint works rather than answering `501`. What the
image cannot carry is the consent: `python -m corrector.drive login` is interactive and cannot
run inside a container, so the token is minted on the host and mounted in. Both defaults
resolve to the image's own home (`/home/amanuense/.config/editor-agent/`), where nothing put
them, and without a token the endpoint answers `401` naming the command to run:

```bash
docker run -p 127.0.0.1:8000:8000 -e DEEPSEEK_API_KEY=... \
  -v ~/.config/editor-agent:/config \
  -e EDITOR_AGENT_GOOGLE_CLIENT_SECRETS=/config/client_secret.json \
  -e EDITOR_AGENT_GOOGLE_TOKEN=/config/token.json \
  editor-agent
```

Not `:ro`. The mount works read-only right up until the access token expires, and then stops.

Two things to settle before it faces anyone but you:

- **Nothing authenticates or rate-limits a submission (`POST /api/jobs` or the front's own
  `POST /jobs`), and every one spends money at a provider.** Publish it on `127.0.0.1` as
  above, or put something authenticating in front.
- **Jobs live in the process's memory, newest 256 kept.** One container: a restart loses what
  is in flight, and a second replica behind a round-robin answers `404` to half the polls.
  Scaling out means moving the store out of the process first.

Without a container it is the same one piece by hand:

```bash
pip install .                 # or `pip install ".[drive]"` to include Google Docs
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
