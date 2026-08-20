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

A FastAPI wrapper over the pipeline. It needs `DEEPSEEK_API_KEY`; it never writes the file.

```bash
uvicorn api.main:app
curl -X POST localhost:8000/correct-file \
  -H 'Content-Type: application/json' \
  -d '{"file_path": "evals/corpus/carta.txt"}'
```

The body carries the corrected text and what was proposed, applied and rejected. A pass
whose every call failed answers `502` instead of handing the original text back with a
`200` that would be indistinguishable from "no errors found"; a pass that lost only some of
its calls returns what the rest produced, with the failures in `errors`.

**`file_path` is not restricted to any root.** Any file the server process can read is
readable through the endpoint, so keep it on `127.0.0.1` until that is settled.

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
