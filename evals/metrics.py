"""Scores a system's edits against the gold edits, and measures voice drift.

Matching is cluster-based rather than edit-to-edit: gold and predicted edits
that touch the same span are grouped, and the group counts as correct only if
applying either set produces the same text. That way a system is not punished
for splitting one correction into two, or for merging two into one, but it is
punished for getting the combined result wrong.

Clusters chain transitively: one predicted edit spanning two gold edits pulls
all three into a single all-or-nothing group, so a wide overcorrection costs
the correct fixes it reaches. That is deliberate — both cannot be applied — but
it means a chatty system is penalised twice over.
"""

import re
from collections import Counter

from pydantic import BaseModel, computed_field

from corrector.taxonomy import ERROR_TYPES

WORD = re.compile(r"[^\W\d_]+")
SENTENCE_END = re.compile(r"[.!?…]+")
PUNCTUATION = re.compile(r"[,;:.!?…—«»\"'()¿¡-]")


class Tally(BaseModel):
    """Counts for one error type, or for a whole run.

    Gold and predicted hits are counted separately on purpose: under cluster
    matching a system can split one correction into two or merge two into one,
    so recall is measured on the gold side and precision on the predicted one.
    Reporting a single "TP" would make neither formula check out.
    """

    tp_gold: int = 0
    tp_pred: int = 0
    n_gold: int = 0
    n_pred: int = 0

    @computed_field
    @property
    def fn(self) -> int:
        return self.n_gold - self.tp_gold

    @computed_field
    @property
    def fp(self) -> int:
        return self.n_pred - self.tp_pred

    @computed_field
    @property
    def precision(self) -> float:
        return self.tp_pred / self.n_pred if self.n_pred else 0.0

    @computed_field
    @property
    def recall(self) -> float:
        return self.tp_gold / self.n_gold if self.n_gold else 0.0

    @computed_field
    @property
    def f05(self) -> float:
        return self.f_beta(0.5)

    def f_beta(self, beta=0.5):
        p, r = self.precision, self.recall
        if not p or not r:
            return 0.0
        b2 = beta * beta
        return (1 + b2) * p * r / (b2 * p + r)

    def add(self, other):
        self.tp_gold += other.tp_gold
        self.tp_pred += other.tp_pred
        self.n_gold += other.n_gold
        self.n_pred += other.n_pred


class Score(BaseModel):
    overall: Tally = Tally()
    by_kind: dict[str, Tally] = {}

    def tally(self, kind):
        return self.by_kind.setdefault(kind, Tally())

    def add(self, other):
        self.overall.add(other.overall)
        for kind, tally in other.by_kind.items():
            self.tally(kind).add(tally)


def score(text, gold, predicted):
    """Compare predicted edits against gold edits on the corrupted text."""
    result = Score()
    result.overall.n_gold = len(gold)
    result.overall.n_pred = len(predicted)
    for edit in gold:
        result.tally(edit.kind).n_gold += 1
    for edit in predicted:
        result.tally(_normalise_kind(edit.kind)).n_pred += 1

    for cluster in _clusters(gold, predicted):
        golds = [e for source, e in cluster if source == "gold"]
        preds = [e for source, e in cluster if source == "pred"]
        if not golds or not preds or not _agree(text, cluster, golds, preds):
            continue
        result.overall.tp_gold += len(golds)
        result.overall.tp_pred += len(preds)
        for edit in golds:
            result.tally(edit.kind).tp_gold += 1
        # Attribute the predicted hit to the gold type, so per-type precision
        # stays meaningful for systems that do not use our taxonomy.
        target = golds[0].kind
        for edit in preds:
            result.tally(_normalise_kind(edit.kind)).n_pred -= 1
            result.tally(target).n_pred += 1
            result.tally(target).tp_pred += 1

    # Reattribution can empty a type out entirely; an all-zero row is noise.
    result.by_kind = {k: t for k, t in result.by_kind.items() if t.n_gold or t.n_pred}
    return result


def false_positives(predicted):
    """On clean text every edit is a false positive, by definition."""
    return Counter(_normalise_kind(edit.kind) for edit in predicted)


# --- stylometry -----------------------------------------------------------


def features(text):
    words = WORD.findall(text)
    sentences = [s for s in SENTENCE_END.split(text) if s.strip()]
    if not words:
        return {"sentence_len": 0.0, "word_len": 0.0, "mattr": 0.0, "punct": 0.0, "comma": 0.0}
    return {
        "sentence_len": len(words) / max(len(sentences), 1),
        "word_len": sum(len(w) for w in words) / len(words),
        "mattr": _mattr(words),
        "punct": 100 * len(PUNCTUATION.findall(text)) / len(words),
        "comma": 100 * text.count(",") / len(words),
    }


def voice_distance(before, after):
    """Mean relative change across the style features. 0 = untouched voice."""
    a, b = features(before), features(after)
    diffs = [
        abs(a[key] - b[key]) / max(abs(a[key]), 1e-9)
        for key in a
        if not (a[key] == 0 and b[key] == 0)
    ]
    return sum(diffs) / len(diffs) if diffs else 0.0


def _mattr(words, window=100):
    """Moving-average type-token ratio: lexical richness, length-independent."""
    lowered = [w.lower() for w in words]
    if len(lowered) <= window:
        return len(set(lowered)) / len(lowered)
    ratios = [
        len(set(lowered[i : i + window])) / window
        for i in range(0, len(lowered) - window + 1, max(window // 4, 1))
    ]
    return sum(ratios) / len(ratios)


# --- internals --------------------------------------------------------------


def _clusters(gold, predicted):
    items = [("gold", e) for e in gold] + [("pred", e) for e in predicted]
    items.sort(key=lambda item: (item[1].start, item[1].end))

    clusters, current, reach = [], [], None
    for source, edit in items:
        if current and edit.start > reach:
            clusters.append(current)
            current, reach = [], None
        current.append((source, edit))
        reach = edit.end if reach is None else max(reach, edit.end)
    if current:
        clusters.append(current)
    return clusters


def _agree(text, cluster, golds, preds):
    lo = min(e.start for _, e in cluster)
    hi = max(e.end for _, e in cluster)
    rendered_gold = _render(text, lo, hi, golds)
    rendered_pred = _render(text, lo, hi, preds)
    return rendered_gold is not None and rendered_gold == rendered_pred


def _render(text, lo, hi, edits):
    pieces, cursor = [], lo
    for edit in sorted(edits, key=lambda e: (e.start, e.end)):
        if edit.start < cursor:
            return None
        pieces.append(text[cursor : edit.start])
        pieces.append(edit.replacement)
        cursor = edit.end
    pieces.append(text[cursor:hi])
    return "".join(pieces)


def _normalise_kind(kind):
    return kind if kind in ERROR_TYPES else "otro"
