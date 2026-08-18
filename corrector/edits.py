"""Typed edits: the only channel through which text is ever modified.

A model never rewrites text. It emits anchored proposals; this module resolves
them to offsets, discards the ones that do not match, and applies the rest
deterministically. Every discard is recorded.
"""

import difflib
import re

from pydantic import BaseModel, Field

TOKEN = re.compile(r"\s+|\w+|[^\w\s]")


class ProposedEdit(BaseModel):
    """An edit as a model emits it, anchored on the text it replaces."""

    original: str = Field(description="Exact text to replace; must appear exactly once")
    replacement: str = Field(description="Text to put in its place")
    kind: str = Field(default="otro", description="Error type from the taxonomy")
    rule: str = Field(default="", description="Rule or norm that justifies the edit")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class Edit(BaseModel):
    """A proposal resolved against a concrete text."""

    start: int = Field(ge=0)
    end: int = Field(ge=0)
    replacement: str
    kind: str = "otro"
    rule: str = ""
    confidence: float = 1.0

    def before(self, text):
        return text[self.start : self.end]


class Rejection(BaseModel):
    reason: str
    detail: str


def resolve_edits(text, proposals):
    """Turn anchored proposals into offset edits.

    An anchor that is missing or appears more than once is ambiguous, so the
    edit is dropped rather than guessed at.
    """
    edits, rejected = [], []
    for proposal in proposals:
        hits = _find_all(text, proposal.original)
        if not hits:
            rejected.append(Rejection(reason="anchor_not_found", detail=proposal.original))
            continue
        if len(hits) > 1:
            rejected.append(Rejection(reason="anchor_ambiguous", detail=proposal.original))
            continue
        start = hits[0]
        edits.append(
            Edit(
                start=start,
                end=start + len(proposal.original),
                replacement=proposal.replacement,
                kind=proposal.kind,
                rule=proposal.rule,
                confidence=proposal.confidence,
            )
        )
    return edits, rejected


def apply_edits(text, edits):
    """Apply non-overlapping edits left to right. Overlaps are dropped."""
    pieces, rejected = [], []
    cursor = 0
    for edit in sorted(edits, key=lambda e: (e.start, e.end)):
        if edit.end > len(text) or edit.start > edit.end:
            rejected.append(Rejection(reason="out_of_bounds", detail=repr(edit)))
            continue
        if edit.start < cursor:
            rejected.append(Rejection(reason="overlapping", detail=repr(edit)))
            continue
        pieces.append(text[cursor : edit.start])
        pieces.append(edit.replacement)
        cursor = edit.end
    pieces.append(text[cursor:])
    return "".join(pieces), rejected


def diff_edits(source, target):
    """Derive minimal edits from a rewritten text.

    Needed for baselines that hand back a whole corrected text instead of a
    list of edits. Diffing on tokens rather than characters keeps the edits at
    word granularity, which is what a corrector would have emitted.
    """
    src = _tokens(source)
    tgt = _tokens(target)
    matcher = difflib.SequenceMatcher(a=[t[0] for t in src], b=[t[0] for t in tgt], autojunk=False)

    edits = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        start = src[i1][1] if i1 < len(src) else len(source)
        end = src[i2 - 1][2] if i2 > i1 else start
        replacement = target[tgt[j1][1] : tgt[j2 - 1][2]] if j2 > j1 else ""
        start, end, replacement = _trim(source, start, end, replacement)
        if start == end and not replacement:
            continue
        edits.append(Edit(start=start, end=end, replacement=replacement))
    return edits


def trim(text, edit):
    """Shrink an edit to the span that actually changes."""
    start, end, replacement = _trim(text, edit.start, edit.end, edit.replacement)
    return edit.model_copy(update={"start": start, "end": end, "replacement": replacement})


def _trim(text, start, end, replacement):
    before = text[start:end]
    head = 0
    while head < len(before) and head < len(replacement) and before[head] == replacement[head]:
        head += 1
    tail = 0
    while (
        tail < len(before) - head
        and tail < len(replacement) - head
        and before[len(before) - 1 - tail] == replacement[len(replacement) - 1 - tail]
    ):
        tail += 1
    return start + head, end - tail, replacement[head : len(replacement) - tail]


def _tokens(text):
    return [(m.group(), m.start(), m.end()) for m in TOKEN.finditer(text)]


def _find_all(text, needle):
    if not needle:
        return []
    hits, start = [], text.find(needle)
    while start != -1:
        hits.append(start)
        start = text.find(needle, start + 1)
    return hits
