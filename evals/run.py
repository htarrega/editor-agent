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
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from pydantic import BaseModel

from corrector.edits import apply_edits
from corrector.llm import MAX_OUTPUT_TOKENS, MAX_RETRIES
from corrector.taxonomy import ERROR_TYPES
from evals import corruptor, metrics, reuse, systems
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

    report = {
        # The token cap goes in with the corpus because a call that ran into
        # it did not answer at all: a row measured under one cap and a row
        # measured under another are not the same measurement.
        "config": vars(args)
        | {
            "fragments": [f.name for f in fragments],
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "max_retries": MAX_RETRIES,
        },
        "corpus": summarise_corpus(cases),
        "systems": {},
    }

    # Nothing about a baseline changes between runs, and one of them costs
    # $1.32: with --reuse only the system under development is actually called.
    cached, notes = {}, []
    if args.reuse:
        # The system under development is the one whose numbers change, and
        # after its first run it also has a cache. Without --fresh a routine
        # `--reuse` would quietly publish last run's numbers as this run's.
        wanted = [name for name in args.systems if name not in args.fresh]
        cached, notes = reuse.load(args.reuse, wanted, report["config"], report["corpus"], args.out)

    print(header(fragments, cases, args, notes))

    # Unknown names fail here rather than after ten minutes of paid baseline.
    live = {s.name: s for s in systems.build([n for n in args.systems if n not in cached])}

    # The naive baselines take minutes per call, so the report is rewritten
    # after every system: an interrupted run still keeps what it measured.
    path = report_path(args.out, args.tag)
    for name in args.systems:
        if name in cached:
            result = cached[name]
        else:
            result = evaluate(
                live[name],
                fragments,
                cases,
                skip_clean=args.skip_clean,
                concurrency=args.concurrency,
            )
        report["systems"][name] = result
        print(summary_row(name, result))
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
    parser.add_argument(
        "--concurrency",
        type=int,
        default=4,
        help="calls in flight per system; the calls are independent, and a system "
        "that paces itself (languagetool) holds to its own limit regardless",
    )
    parser.add_argument(
        "--fresh",
        type=comma_list,
        default=[],
        help="systems that are always run live, never taken from the cache",
    )
    parser.add_argument(
        "--reuse",
        nargs="?",
        const="latest",
        default=None,
        metavar="INFORME",
        help="read each system's numbers from an earlier report instead of running it; "
        "bare flag scans --out newest first, or name one report. Reports built from a "
        "different corpus are skipped, never mixed in",
    )
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


def correct_all(system, texts, concurrency):
    """``system.correct`` over every text, results in input order.

    The calls are independent — that is what makes this safe at all — but what
    happens to their results is not: scores, false-positive samples and the
    per-edit record are all appended in corpus order, so the answers have to
    come back in the order they went out. Otherwise two runs of one corpus
    would write two different reports and neither would be wrong.

    A system may pin its own ceiling with a ``concurrency`` attribute.
    LanguageTool does: it paces itself against a requests-per-minute limit, and
    firing its chunks at once would defeat the pacing rather than outrun it.
    """
    limit = max(1, min(concurrency, getattr(system, "concurrency", concurrency)))
    if limit == 1:
        return [system.correct(text) for text in texts]
    with ThreadPoolExecutor(max_workers=limit) as pool:
        return list(pool.map(system.correct, texts))


def evaluate(system, fragments, cases, skip_clean=False, concurrency=1):
    started = time.monotonic()
    usage = systems.Usage()
    score = metrics.Score()
    errors, skipped = [], 0
    rejected = Counter()
    detail = []

    outputs = correct_all(system, [case.text for case in cases], concurrency)
    for case, out in zip(cases, outputs, strict=True):
        usage.add(out.usage)
        errors.extend(f"{case.name}: {e}" for e in out.errors)
        skipped += out.skipped
        rejected.update(out.rejected)
        score.add(metrics.score(case.text, case.gold, out.edits))
        detail.extend(
            {"case": case.name} | outcome.model_dump()
            for outcome in metrics.outcomes(case.text, case.gold, out.edits)
        )

    clean = CleanResult()
    if not skip_clean:
        clean, clean_usage, clean_errors, clean_rejected = evaluate_clean(
            system, fragments, concurrency
        )
        usage.add(clean_usage)
        errors.extend(clean_errors)
        rejected.update(clean_rejected)

    return {
        "overall": score.overall.model_dump(),
        "by_kind": {kind: t.model_dump() for kind, t in sorted(score.by_kind.items())},
        "clean": clean.model_dump(),
        "usage": usage.model_dump(),
        # `usage.seconds` sums each call's own duration, so it measures latency
        # per call and does not move when calls overlap. Elapsed time does, and
        # it is the only number that says whether a run got faster.
        "wall_seconds": time.monotonic() - started,
        "unactionable": skipped,
        # Why a proposal never became an edit. ARCHITECTURE §4 requires the
        # discards to be logged, not merely counted: a run where the anchors
        # stop matching looks, from F0.5 alone, like a run that got worse.
        "rejected": dict(sorted(rejected.items(), key=lambda kv: -kv[1])),
        # Every corpus-A edit on both sides, hit or missed, with where it sits.
        # The tallies say how much was missed; a rule pack is written from
        # which ones, and the position is what separates "cannot see this
        # error" from "stopped reading".
        "edits": detail,
        "errors": errors,
        # Recorded so a past run says what it was compared against; changing a
        # baseline's prompt changes what its numbers mean.
        "model": getattr(system, "model", ""),
        "prompt": getattr(system, "prompt", ""),
        # How the text was cut into the numbered blocks the model saw. Same
        # reason as the prompt: change it and the row means something else.
        "block_words": getattr(system, "block_words", None),
    }


def evaluate_clean(system, fragments, concurrency=1):
    """Run the systems over untouched text. Returns the result, its cost, its
    errors and its discards, so the caller decides what to do with each."""
    usage, errors = systems.Usage(), []
    result = CleanResult()
    distances, by_kind, rejected = [], Counter(), Counter()

    outputs = correct_all(system, [fragment.text for fragment in fragments], concurrency)
    for fragment, out in zip(fragments, outputs, strict=True):
        usage.add(out.usage)
        errors.extend(f"{fragment.name} (limpio): {e}" for e in out.errors)
        rejected.update(out.rejected)

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
    return result, usage, errors, rejected


# --- rendering --------------------------------------------------------------

SUMMARY_HEADER = (
    f"{'sistema':<16} {'P':>6} {'R':>6} {'F0.5':>6} "
    f"{'FP/1k':>7} {'voz':>6} {'coste$':>8} {'seg':>6}"
)


def header(fragments, cases, args, notes=()):
    seeded = sum(len(c.gold) for c in cases)
    words = sum(f.words for f in fragments)
    lines = [
        f"corpus: {len(fragments)} fragmentos, {words} palabras",
        f"corpus A: {len(cases)} versiones corrompidas, {seeded} errores sembrados "
        f"(rate={args.rate}, seed={args.seed}, repeats={args.repeats})",
        f"corpus B: {len(fragments)} fragmentos intactos, {words} palabras",
        *notes,
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
    # Cost and seconds of a reused row were paid by an earlier run; the row says
    # so, because otherwise this run looks like it spent them.
    cache = "  ↺ caché" if result.get("reused_from") else ""
    return (
        f"{name:<16} {o['precision']:>6.3f} {o['recall']:>6.3f} {o['f05']:>6.3f} "
        f"{c['fp_per_1k']:>7.2f} {c['voice']:>6.3f} {u['cost_usd']:>8.4f} {u['seconds']:>6.1f}"
        f"{cache}{warning}"
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
    # Older reports predate both keys; a reused row must still render.
    discarded = result.get("rejected") or {}
    if discarded:
        reasons = ", ".join(f"{k}={v}" for k, v in discarded.items())
        lines.append(f"  propuestas descartadas: {reasons}")
    for extra in (coverage_line(result.get("edits")), offschema_line(result.get("edits"))):
        if extra:
            lines.append(f"  {extra}")
    if result["usage"]["calls"]:
        lines.append(f"  {usage_line(result['usage'])}")
    if result["errors"]:
        lines.append(f"  errores: {len(result['errors'])} — {result['errors'][0]}")
    if result["unactionable"]:
        lines.append(f"  avisos sin sugerencia (descartados): {result['unactionable']}")
    return "\n".join(lines) + "\n"


def coverage_line(detail):
    """Recall over the first half of each fragment against the second.

    A system that cannot see an error type misses it everywhere; one that runs
    out of budget misses the back half. The two call for different fixes — a
    rule pack against chunking — and the per-type table cannot tell them apart.
    """
    if not detail:
        return None
    halves = {"1ª mitad": [0, 0], "2ª mitad": [0, 0]}
    for record in detail:
        if record["side"] != "gold":
            continue
        bucket = halves["1ª mitad" if record["at"] < 0.5 else "2ª mitad"]
        bucket[0] += bool(record["hit"])
        bucket[1] += 1
    parts = [
        f"{name} {hit}/{total} ({hit / total:.3f})"
        for name, (hit, total) in halves.items()
        if total
    ]
    return "cobertura (recall por posición): " + ", ".join(parts) if parts else None


def offschema_line(detail):
    """Proposals labelled with something that is not a taxonomy type.

    Worth a line of its own because a label the schema did not offer is free to
    read and may predict a bad edit — a filter that costs no model call.
    """
    if not detail:
        return None
    predicted = [r for r in detail if r["side"] == "pred"]
    off = [r for r in predicted if r["kind"] not in ERROR_TYPES]
    if not off:
        return None
    wrong = sum(1 for r in off if not r["hit"])
    return (
        f"etiquetas fuera de taxonomía: {len(off)} de {len(predicted)} propuestas, "
        f"{wrong} de ellas falsas"
    )


def usage_line(usage):
    """Tokens and latency per call.

    H1 asks for these from the first run: the cheap model is a reasoning model
    whose deliberation is billed as output and capped by `max_tokens`, so
    «output tokens» alone does not say whether a call is close to truncating.
    """
    calls = usage["calls"]
    reasoning = usage.get("reasoning_tokens", 0)
    thinking = f", de ellos {reasoning / calls:,.0f} razonando" if reasoning else ""
    return (
        f"uso: {calls} llamadas, {usage['input_tokens'] / calls:,.0f} tok entrada, "
        f"{usage['output_tokens'] / calls:,.0f} salida{thinking}, "
        f"{usage['seconds'] / calls:.1f} s por llamada"
    )


def summarise_corpus(cases):
    counts = {}
    for case in cases:
        for kind, n in case.counts_by_kind().items():
            counts[kind] = counts.get(kind, 0) + n
    return {
        "cases": len(cases),
        # Identifies the corpus a later run would have to match to reuse these
        # numbers; the config alone does not (see reuse.fingerprint).
        "fingerprint": reuse.fingerprint(cases),
        "seeded_by_kind": dict(sorted(counts.items())),
    }


def report_path(out_dir, tag):
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    return out_dir / (f"{stamp}-{tag}.json" if tag else f"{stamp}.json")


def write_report(report, path):
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
