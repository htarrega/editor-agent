# editor-agent

An autonomous text correction agent, with access to local files and Google Drive.

- Design: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- Milestones and state: [`docs/PLAN.md`](docs/PLAN.md)
- Evaluation harness: [`evals/README.md`](evals/README.md)

## Usage

```bash
python3 -m venv venv
source venv/bin/activate
pip install -e ".[dev]"          # the pipeline, the harness and ruff

export DEEPSEEK_API_KEY=...      # the corrector's workhorse
export ANTHROPIC_API_KEY=...     # strong-model baseline

python -m evals.run              # metrics table + cost of the run
python -m evals.run --reuse      # ...reusing cached baselines, calling only what is new
python -m evals.run --reuse --fresh corrector-blocks   # ...never caching the one being built
python -m unittest discover -s tests -t .

ruff format . && ruff check .    # formatting and import order
```

## Layout

| package | what it is |
|---|---|
| `corrector/` | the pipeline — the product. Imports nothing from `evals/` |
| `evals/` | the harness that measures it |
| `tests/` | `tests/test_corrector/` and `tests/test_evals/`, mirroring the two |

Installing the project is what makes `corrector` importable from outside the repository
root. The test directories carry a `test_` prefix so that neither can ever shadow the
package it tests: a directory named `corrector/` under `tests/` is a second, empty
`corrector` on the import path the moment a runner puts `tests/` on it.

The tests run offline — the corpus lives outside the repository (`docs/PLAN.md`, H0), and
the one test that wants it skips when it is absent.
