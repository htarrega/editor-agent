"""Takes a system's numbers from an earlier report instead of paying for them twice.

A baseline that has not changed returns the numbers it returned last week, and
`naive-claude` costs $1.32 a run. So a run can measure only the system under
development and read the rest off disk.

What makes that honest is the compatibility check. A report is either built from
the same corpus as this run — same fragments, same seeding, same truncation — or
it is skipped. Numbers from two different corpora sharing a table look like a
comparison and are not one.
"""

import hashlib
import json
import pathlib

# What has to match for two runs to be comparable. `systems`, `out`, `tag` and
# `reuse` say how a run was invoked, not what it measured, so they are excluded.
COMPARED_CONFIG = ("fragments", "seed", "rate", "repeats", "limit_words", "skip_clean")


def fingerprint(cases):
    """Hash of the exact text the systems are fed, clean and corrupted.

    The config does not pin this down: `seed 0, rate 0.02` over the same four
    fragments seeded different errors before and after a corruptor change, and
    the two reports are indistinguishable by their config alone. Editing a
    fragment does the same to corpus B.
    """
    digest = hashlib.sha256()
    for case in cases:
        for part in (case.name, case.clean, case.text):
            digest.update(part.encode("utf-8"))
            digest.update(b"\0")
    return digest.hexdigest()[:16]


def load(spec, wanted, config, corpus, results_dir):
    """Finds cached results for `wanted`, newest report first.

    Returns `{name: result}` with `reused_from` stamped on each, and the lines
    to print about what was taken, what was not, and why.
    """
    paths, explicit = _candidates(spec, results_dir)
    found, notes, rejected = {}, [], []

    for path in paths:
        if len(found) == len(wanted):
            break
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            rejected.append(f"{path.name}: {type(exc).__name__}")
            continue
        reasons = incompatible(report, config, corpus)
        if reasons:
            rejected.append(f"{path.name}: {'; '.join(reasons)}")
            continue
        for name in wanted:
            if name in found or name not in report.get("systems", {}):
                continue
            found[name] = report["systems"][name] | {"reused_from": path.name}
            notes.append(f"reutilizado: {name:<16} ← {path.name}")

    # A named report that turns out to be incomparable is a mistake worth
    # stopping for; when scanning a directory, skipping it is the whole point.
    if explicit and not found and rejected:
        raise ValueError(f"informe no comparable — {rejected[0]}")

    missing = [name for name in wanted if name not in found]
    if missing:
        notes.append(f"sin caché: {', '.join(missing)} — se ejecutan en vivo")
    if rejected:
        notes.append(f"informes descartados: {len(rejected)} — {rejected[0]}")
    return found, notes


def incompatible(report, config, corpus):
    """Reasons this report cannot sit in this run's table. Empty means it can."""
    reasons = []
    cached = report.get("config", {})
    for key in COMPARED_CONFIG:
        if cached.get(key) != config[key]:
            reasons.append(f"{key} {cached.get(key)!r} ≠ {config[key]!r}")

    cached_corpus = report.get("corpus", {})
    if "fingerprint" in cached_corpus:
        if cached_corpus["fingerprint"] != corpus["fingerprint"]:
            reasons.append("corpus distinto")
    # Reports written before the fingerprint existed: the seeded counts are the
    # only evidence left that neither the fragments nor the corruptor moved.
    elif cached_corpus.get("seeded_by_kind") != corpus["seeded_by_kind"]:
        reasons.append("errores sembrados distintos")
    return reasons


def _candidates(spec, results_dir):
    """Report paths newest first, and whether the user named one report.

    Filenames are timestamps, so sorting them backwards is newest first.
    """
    if spec in ("latest", "", True):
        # --out need not exist yet; having nothing to reuse is not an error.
        reports = sorted(results_dir.glob("*.json"), reverse=True) if results_dir.is_dir() else []
        return reports, False
    path = pathlib.Path(spec)
    if path.is_dir():
        return sorted(path.glob("*.json"), reverse=True), False
    if not path.is_file():
        raise FileNotFoundError(f"no such report: {path}")
    return [path], True
