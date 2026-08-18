# Evaluation harness

```bash
python -m evals.run                       # 4 systems, corpora A and B
python -m evals.run --systems null,languagetool
python -m evals.run --repeats 4           # N corrupted versions per fragment
python -m evals.run --limit-words 300     # cheap smoke run
python -m unittest discover -s evals/tests -t .
```

It prints a table and writes `evals/results/<timestamp>.json` with the full detail,
cost included.

## Corpus

`evals/corpus/*.txt`. **Outside the repository** (gitignore), same as
`evals/results/`. Reproducing a run requires having the files in place.

Both corpora come out of the same files:

- **A**: the fragments with typed errors seeded into them → precision, recall, F0.5.
- **B**: the same fragments untouched → false positives and stylometric distance.
  On clean text every edit is a false positive.

A new fragment must go in without typos of its own: a pre-existing typo counts as a
false positive when a system correctly spots it.

## Pieces

| file | what it does |
|---|---|
| `corruptor.py` | seeds the 17 error types from `corrector/taxonomy.py` |
| `metrics.py` | scoring by group, false positives, stylometry |
| `systems.py` | the systems under test and their prices |
| `dataset.py` | loads and normalizes the fragments |
| `run.py` | CLI, table and report |

The corruptor's invariant, checked on every run and in the tests:
`apply_edits(result.text, result.gold) == result.clean`. If it fails, the run aborts.

Environment variables: `EVAL_CLAUDE_MODEL`, `EVAL_DEEPSEEK_MODEL`,
`LANGUAGETOOL_URL`.

Design decisions and measured results: `docs/PLAN.md` (H0).
