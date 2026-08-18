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

export DEEPSEEK_API_KEY=...      # cheap baseline
export ANTHROPIC_API_KEY=...     # strong-model baseline

python -m evals.run              # metrics table + cost of the run
python -m unittest discover -s evals/tests -t .
```
