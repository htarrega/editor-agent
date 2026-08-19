# Evaluation harness

```bash
python -m evals.run                       # 4 systems, corpora A and B
python -m evals.run --systems null,languagetool
python -m evals.run --repeats 4           # N corrupted versions per fragment
python -m evals.run --concurrency 6       # calls in flight per system (default 4)
python -m evals.run --limit-words 300     # cheap smoke run
python -m evals.run --reuse               # only what has no cached numbers is called
python -m evals.run --reuse --fresh corrector-blocks   # ...but never cache the one being built
python -m unittest discover -s evals/tests -t .
```

It prints a table and writes `evals/results/<timestamp>.json` with the full detail,
cost included.

## Reusing the baselines

The baselines do not change between runs and `naive-claude` costs $1.32 a run, so
there is no reason to pay for them again to measure something else. `--reuse` reads
each system's numbers from earlier reports and calls only the systems that have none
— in practice, the one under development.

```bash
python -m evals.run --reuse                       # scan --out, newest report first
python -m evals.run --reuse evals/results/20260817-230537-claude-final.json
```

## Running the calls concurrently

The calls a system makes are independent, so `--concurrency` puts several in flight at once.
Results are collected **in input order** — the scores, the false-positive samples and the
per-edit record are all appended in corpus order, so two runs of one corpus have to write the
same report. A system that paces itself against a rate limit pins its own ceiling with a
`concurrency` attribute, as `languagetool` does.

The `seg` column keeps summing each call's own duration, so it measures latency per call and
does not move when calls overlap; `wall_seconds` in the report is the elapsed time and is the
number that shows a speedup.

This is what makes `--repeats` affordable, and `--repeats` is what makes a comparison
between two systems mean anything — a single run of this harness is a draw, not a
measurement.

Reused rows are marked `↺ caché` in the table and carry `reused_from` in the report.
Their cost and seconds columns are what the original run paid, not this one.

`--fresh` names the systems that are always called live. The system under development
has a cache too from its previous run, and reusing *that* is how a run comes to
publish last week's numbers as this week's.

Numbers only travel between runs built from the same corpus. A report is skipped
unless its fragments, seed, rate, repeats, truncation and `--skip-clean` match and
its corpus fingerprint —a hash of the exact clean and corrupted text— is identical.
The config alone is not enough: `seed 0, rate 0.02` over these same four fragments
seeded different errors before and after a corruptor change, and the two reports are
indistinguishable by their config. Reports older than the fingerprint fall back to
comparing the seeded-error counts. A report named explicitly that turns out not to
match aborts the run instead of being skipped.

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
| `reuse.py` | takes a system's numbers from an earlier report, if it is comparable |
| `run.py` | CLI, table and report |

The systems themselves: `null` and `languagetool` and the naive prompts are baselines
and live in `systems.py`; `corrector-blocks` is the pipeline, and lives in `corrector/`
(`correct.py` for the pass, `llm.py` for the providers and the prices).
`corrector-claude` is the same pass on the strong model — not a baseline, a way of
telling apart what the prompt contributes from what the model does.

The corruptor's invariant, checked on every run and in the tests:
`apply_edits(result.text, result.gold) == result.clean`. If it fails, the run aborts.

Environment variables: `EVAL_CLAUDE_MODEL`, `EVAL_DEEPSEEK_MODEL`,
`EVAL_DEEPSEEK_EFFORT` (the corrector's `reasoning_effort`), `LANGUAGETOOL_URL`.

Design decisions and measured results: `docs/PLAN.md` (H0).
