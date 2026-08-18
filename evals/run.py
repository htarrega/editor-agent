"""Runs the whole harness: `python -m evals.run`.

Corpus A is the fragments with typed errors seeded in — it measures whether a
system finds and fixes real errors. Corpus B is the same fragments untouched —
every edit there is a false positive, which is the overcorrection metric.
"""

import argparse
import datetime
import json
import pathlib
import sys
from collections import Counter

from pydantic import BaseModel

from corrector.edits import apply_edits
from evals import corruptor, metrics, systems
from evals.dataset import load_fragments

RESULTS_DIR = pathlib.Path(__file__).parent / "results"
FP_SAMPLES_PER_FRAGMENT = 40


class CleanResult(BaseModel):
    """Corpus B: what a system did to text that needed nothing done to it."""

    fp: int = 0
    fp_per_1k: float = 0.0
    voice: float = 0.0
    words: int = 0
    fragments: int = 0
    unapplied: int = 0
    by_kind: dict[str, int] = {}
    skipped: list[str] = []
    samples: list[dict] = []


def main(argv=None):
    args = parse_args(argv)
    fragments = load_fragments(limit_words=args.limit_words, only=args.fragments)
    cases = build_cases(fragments, args)

    print(header(fragments, cases, args))

    report = {
        "config": vars(args) | {"fragments": [f.name for f in fragments]},
        "corpus": summarise_corpus(cases),
        "systems": {},
    }

    # The naive baselines take minutes per call, so the report is rewritten
    # after every system: an interrupted run still keeps what it measured.
    path = report_path(args.out, args.tag)
    for system in systems.build(args.systems):
        result = evaluate(system, fragments, cases, skip_clean=args.skip_clean)
        report["systems"][system.name] = result
        print(summary_row(system.name, result))
        write_report(report, path)

    print()
    for name, result in report["systems"].items():
        print(by_kind_table(name, result))

    print(f"informe: {path}")
    return 0


def parse_args(argv):
    parser = argparse.ArgumentParser(prog="evals.run", description=__doc__)
    parser.add_argument("--systems", type=comma_list, default=systems.DEFAULT_SYSTEMS)
    parser.add_argument("--fragments", type=comma_list, default=None)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--rate", type=float, default=0.02, help="errors seeded per word")
    parser.add_argument(
        "--repeats",
        type=int,
        default=1,
        help="corrupted versions per fragment, each with its own seed",
    )
    parser.add_argument("--limit-words", type=int, default=None, help="truncate fragments")
    parser.add_argument("--out", type=pathlib.Path, default=RESULTS_DIR)
    parser.add_argument("--tag", default="", help="suffix for the report filename")
    parser.add_argument("--skip-clean", action="store_true", help="skip corpus B")
    return parser.parse_args(argv)


def comma_list(value):
    return [item.strip() for item in value.split(",") if item.strip()]


def build_cases(fragments, args):
    cases = []
    for fragment in fragments:
        for repeat in range(args.repeats):
            case = corruptor.corrupt(
                fragment.text,
                rate=args.rate,
                seed=args.seed + repeat,
                name=f"{fragment.name}#{repeat}",
            )
            if not corruptor.restores_clean(case):
                raise AssertionError(f"gold edits do not restore {case.name}")
            cases.append(case)
    return cases


def evaluate(system, fragments, cases, skip_clean=False):
    usage = systems.Usage()
    score = metrics.Score()
    errors, skipped = [], 0

    for case in cases:
        out = system.correct(case.text)
        usage.add(out.usage)
        errors.extend(f"{case.name}: {e}" for e in out.errors)
        skipped += out.skipped
        score.add(metrics.score(case.text, case.gold, out.edits))

    clean = CleanResult()
    if not skip_clean:
        clean, clean_usage, clean_errors = evaluate_clean(system, fragments)
        usage.add(clean_usage)
        errors.extend(clean_errors)

    return {
        "overall": score.overall.model_dump(),
        "by_kind": {kind: t.model_dump() for kind, t in sorted(score.by_kind.items())},
        "clean": clean.model_dump(),
        "usage": usage.model_dump(),
        "unactionable": skipped,
        "errors": errors,
        # Recorded so a past run says what it was compared against; changing a
        # baseline's prompt changes what its numbers mean.
        "model": getattr(system, "model", ""),
        "prompt": getattr(system, "prompt", ""),
    }


def evaluate_clean(system, fragments):
    """Run the systems over untouched text. Returns the result, its cost and
    its errors, so the caller decides what to do with each."""
    usage, errors = systems.Usage(), []
    result = CleanResult()
    distances, by_kind = [], Counter()

    for fragment in fragments:
        out = system.correct(fragment.text)
        usage.add(out.usage)
        errors.extend(f"{fragment.name} (limpio): {e}" for e in out.errors)

        # A failed call produces no edits. Counting its words anyway would
        # silently halve the false-positive rate, so drop the fragment.
        if out.errors:
            result.skipped.append(fragment.name)
            continue

        result.fp += len(out.edits)
        result.words += fragment.words
        by_kind.update(metrics.false_positives(out.edits))

        # Which words a system wants to "fix" is what drives the rule pack, so
        # keep the actual text rather than only the counts.
        for edit in out.edits[:FP_SAMPLES_PER_FRAGMENT]:
            result.samples.append(
                {
                    "kind": edit.kind,
                    "rule": edit.rule,
                    "before": edit.before(fragment.text),
                    "after": edit.replacement,
                }
            )

        corrected, dropped = apply_edits(fragment.text, out.edits)
        # `fp` counts every edit proposed; the text measured below is missing
        # the overlapping ones, so record the gap rather than hide it.
        result.unapplied += len(dropped)
        distances.append(metrics.voice_distance(fragment.text, corrected))

    result.fragments = len(fragments) - len(result.skipped)
    result.by_kind = dict(sorted(by_kind.items(), key=lambda kv: -kv[1]))
    result.fp_per_1k = 1000 * result.fp / result.words if result.words else 0.0
    result.voice = sum(distances) / len(distances) if distances else 0.0
    return result, usage, errors


# --- rendering --------------------------------------------------------------

SUMMARY_HEADER = (
    f"{'sistema':<16} {'P':>6} {'R':>6} {'F0.5':>6} "
    f"{'FP/1k':>7} {'voz':>6} {'coste$':>8} {'seg':>6}"
)


def header(fragments, cases, args):
    seeded = sum(len(c.gold) for c in cases)
    words = sum(f.words for f in fragments)
    lines = [
        f"corpus: {len(fragments)} fragmentos, {words} palabras",
        f"corpus A: {len(cases)} versiones corrompidas, {seeded} errores sembrados "
        f"(rate={args.rate}, seed={args.seed}, repeats={args.repeats})",
        f"corpus B: {len(fragments)} fragmentos intactos, {words} palabras",
        "",
        SUMMARY_HEADER,
        "-" * len(SUMMARY_HEADER),
    ]
    return "\n".join(lines)


def summary_row(name, result):
    o, c, u = result["overall"], result["clean"], result["usage"]
    # A run with failed calls is not comparable to a complete one; say so on
    # the row itself rather than only in the detail below.
    warning = f"  ⚠ {len(result['errors'])} llamadas fallidas" if result["errors"] else ""
    return (
        f"{name:<16} {o['precision']:>6.3f} {o['recall']:>6.3f} {o['f05']:>6.3f} "
        f"{c['fp_per_1k']:>7.2f} {c['voice']:>6.3f} {u['cost_usd']:>8.4f} {u['seconds']:>6.1f}"
        f"{warning}"
    )


def by_kind_table(name, result):
    # TPg/FN are the recall pair, TPp/FP the precision pair. They differ when a
    # system splits or merges corrections, so printing a single TP would make
    # neither formula check out against the columns beside it.
    rows = [
        f"  {kind:<22} {v['tp_gold']:>4} {v['fn']:>4} {v['tp_pred']:>4} {v['fp']:>4} "
        f"{v['precision']:>6.3f} {v['recall']:>6.3f} {v['f05']:>6.3f}"
        for kind, v in result["by_kind"].items()
        if v["tp_gold"] or v["fp"] or v["fn"]
    ]
    lines = [
        f"{name} — por tipo de error",
        f"  {'tipo':<22} {'TPg':>4} {'FN':>4} {'TPp':>4} {'FP':>4} {'P':>6} {'R':>6} {'F0.5':>6}",
        *rows,
    ]
    clean = result["clean"]["by_kind"]
    if clean:
        top = ", ".join(f"{k}={v}" for k, v in list(clean.items())[:6])
        lines.append(f"  falsos positivos en texto limpio: {top}")
    if result["errors"]:
        lines.append(f"  errores: {len(result['errors'])} — {result['errors'][0]}")
    if result["unactionable"]:
        lines.append(f"  avisos sin sugerencia (descartados): {result['unactionable']}")
    return "\n".join(lines) + "\n"


def summarise_corpus(cases):
    counts = {}
    for case in cases:
        for kind, n in case.counts_by_kind().items():
            counts[kind] = counts.get(kind, 0) + n
    return {"cases": len(cases), "seeded_by_kind": dict(sorted(counts.items()))}


def report_path(out_dir, tag):
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    return out_dir / (f"{stamp}-{tag}.json" if tag else f"{stamp}.json")


def write_report(report, path):
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
