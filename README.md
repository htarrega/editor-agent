# editor-agent

An autonomous text correction agent, with access to local files and Google Drive.

- Design: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- Milestones and state: [`docs/PLAN.md`](docs/PLAN.md)
- Evaluation harness: [`evals/README.md`](evals/README.md)

## Usage

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt   # ruff, for formatting

export DEEPSEEK_API_KEY=...      # the corrector's workhorse
export ANTHROPIC_API_KEY=...     # strong-model baseline

python -m evals.run              # metrics table + cost of the run
python -m evals.run --reuse      # ...reusing cached baselines, calling only what is new
python -m evals.run --reuse --fresh corrector-blocks   # ...never caching the one being built
python -m unittest discover -s evals/tests -t .

ruff format . && ruff check .    # formatting and import order
```
