"""How a text is cut into the numbered blocks the model is shown.

A block is what an edit's «line» points at and what an anchor is resolved
inside. Until now a block *was* a line of the text, which tied the unit the
model reasons over to the length of the author's paragraphs — and H1 measured
what that costs: on the one fragment averaging 220 words per paragraph the
cheap model's recall fell to 0.636, against 0.926 on the three fragments whose
paragraphs run 45–55 words.

So the block is cut here rather than inherited. Without ``max_words`` a block
is a line, which is the numbering H1 measured; with it, an over-long line is
cut at its own sentence boundaries. The text itself is never touched: cutting
changes how the same characters are numbered, nothing else.
"""

import re

from corrector.edits import line_spans

# What a block holds unless a caller says otherwise. Measured over `--repeats 3`
# against one block per line: F0.5 0.948 vs 0.926, and the worst draw on the
# fragment with 245-word paragraphs rises from 0.455 to 0.705 (docs/PLAN.md, H5).
# It is the order of the paragraphs the model already handles well — and the only
# value ever measured, not the winner of a sweep.
DEFAULT_BLOCK_WORDS = 50

# A sentence ends at closing punctuation followed by space. Any closing quote
# or bracket travels with it, so «vámonos». cuts after the closing quote and
# not between the period and it.
BOUNDARY = re.compile(r"[.!?…]+[»”’\"')\]]*(?=\s)")


def block_spans(text, max_words=None):
    """``(start, end)`` of every numbered block, in order. Block *n* is ``spans[n - 1]``.

    Trailing blank lines are dropped: they carry no text to correct, and a
    marker over nothing invites the model to explain itself there.
    """
    spans = line_spans(text)
    while spans and not text[spans[-1][0] : spans[-1][1]].strip():
        spans.pop()
    if not max_words:
        return spans
    return [block for span in spans for block in _cut(text, span, max_words)]


def _cut(text, span, max_words):
    """One line into blocks of at most ``max_words``, whole sentences each.

    A sentence is never split, so a single sentence longer than the budget
    stays one over-long block: a block that stops mid-clause would show the
    model a fragment the author never wrote, which is the same reason the
    marker sits on its own line rather than inline.
    """
    start, end = span
    if len(text[start:end].split()) <= max_words:
        return [span]

    blocks, open_start, open_end, words = [], None, None, 0
    for sentence_start, sentence_end in _sentences(text, start, end):
        count = len(text[sentence_start:sentence_end].split())
        if open_start is None:
            open_start, open_end, words = sentence_start, sentence_end, count
        elif words + count <= max_words:
            open_end, words = sentence_end, words + count
        else:
            blocks.append((open_start, open_end))
            open_start, open_end, words = sentence_start, sentence_end, count
    if open_start is not None:
        blocks.append((open_start, open_end))
    # A long line with no boundary to cut at — one sentence, or dialogue with
    # no full stop in it — goes through whole rather than being guessed at.
    return blocks or [span]


def _sentences(text, start, end):
    """Absolute ``(start, end)`` of each sentence in a line.

    The space between two sentences belongs to neither: a block must not start
    with whitespace the author would not see at the head of a paragraph. That
    leaves a one-character gap between blocks, which costs nothing — an anchor
    that straddles a sentence boundary simply misses its block and falls back
    to the text-wide search, exactly as one straddling a line already does.
    """
    spans, cursor = [], start
    for match in BOUNDARY.finditer(text, start, end):
        stop = match.end()
        if text[cursor:stop].strip():
            spans.append((cursor, stop))
        cursor = stop
        while cursor < end and text[cursor].isspace():
            cursor += 1
    if text[cursor:end].strip():
        spans.append((cursor, end))
    return spans
