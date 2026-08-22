"""Orthotypography by rule, because the model will not do it.

Every latency result in docs/PLAN.md ends at the same wall: what buys recall on
this model is deliberation, and deliberation is what the wall clock is made of.
This module is the one class of error where that trade does not have to be
made. Quotation marks, dialogue dashes, opening signs and spacing around
punctuation are *decidable*: the norm names the character that belongs there,
so a regular expression is not an approximation of the model's judgement, it is
strictly better than it — and it runs in microseconds.

That the model is bad at exactly these is measured rather than assumed. With
its deliberation turned off it scores 0/2 on «comillas» and 0/3 on
«signo_apertura», and narrowing a call to ortotipografía alone does not move
either (docs/PLAN.md, H4). It is not short of attention; it is the wrong tool.

The rules answer to the norm and not to the corruptor. That distinction is the
whole validity of the exercise: a rule written to invert `evals/corruptor.py`
would score well on corpus A and mean nothing, so each one below is a rule a
copy editor would state — a straight quote is not a Spanish quotation mark, a
question mark closes something that has to have been opened — and the check
that it is not overfitted is corpus B, which nobody corrupted.
"""

import re

from corrector.edits import Edit

# A straight double quote has no legitimate use in Spanish literary prose: the
# norm is the angular pair, with the English pair as a nested second level.
# Which of the two a given mark becomes is decided by counting, so an unpaired
# one is left alone rather than guessed at.
STRAIGHT_QUOTE = re.compile(r'"')

# The dialogue dash is a raya (—), not a hyphen and not a minus. Restricted to
# the positions where a hyphen cannot be a hyphen: opening a line, or standing
# with whitespace on at least one side. `físico-químico` and `1939-1945` have a
# letter or a digit on both sides and are never touched.
HYPHEN_AS_DASH = re.compile(
    r"(?:(?<=^)|(?<=\n))[-–]{1,2}(?=[^\s-])"  # opening a line of dialogue
    r"|(?<=\s)[-–]{1,2}(?=[^\s-])"  # opening an incise
    r"|(?<=[^\s-])[-–]{1,2}(?=\s)"  # closing one
)

# A space before the mark, or none after it. Both are typing slips rather than
# choices; neither has a reading in which it is correct.
SPACE_BEFORE_MARK = re.compile(r"\s+(?=[,;:!?](?:\s|$))")
MISSING_SPACE_AFTER = re.compile(r"(?<=[,;:])(?=[^\s\d,;:.)»\"'\]])")

# Where a question or exclamation may have begun. The opening sign goes at the
# head of the clause, and in Spanish that is the head of the sentence unless a
# vocative or a connector has been split off with a comma — which is why the
# comma is not a boundary here: «¿Qué haces, Juan?» would take the sign to the
# wrong side of it.
CLAUSE_HEAD = re.compile(r"(?:^|\n|(?<=[.!?…])\s|(?<=—)|(?<=«)|(?<=:)\s)")

# Skipped over when placing an opening sign: they belong outside it.
LEADING = " \t«\"'—-¿¡"

PAIRED = {"?": "¿", "!": "¡"}

# Words Spanish never capitalises mid-sentence. Capitalising them is a real
# error and a common one — it is interference from English, not a house style —
# whereas a capital in the middle of an ordinary word is a slip nobody makes,
# so only this closed list is touched.
ALWAYS_LOWERCASE = re.compile(
    r"(?<=[a-záéíóúüñ] )("
    r"[EF]nero|[Ff]ebrero|[Mm]arzo|[Aa]bril|[Mm]ayo|[Jj]unio|[Jj]ulio|[Aa]gosto|"
    r"[Ss]eptiembre|[Oo]ctubre|[Nn]oviembre|[Dd]iciembre|"
    r"[Ll]unes|[Mm]artes|[Mm]iércoles|[Jj]ueves|[Vv]iernes|[Ss]ábado|[Dd]omingo|"
    r"[Ee]spañol|[Ee]spañola|[Ff]rancés|[Ff]rancesa|[Ii]nglés|[Ii]nglesa|"
    r"[Cc]astellano|[Cc]astellana|[Aa]sturiano|[Aa]sturiana"
    r")\b"
)

# A sentence opens with a capital. The full stop has to be a full stop, so an
# abbreviation is not one: after «etc.» or «Sr.» the sentence has not ended and
# the lowercase letter is right. The ellipsis is excluded for the same reason —
# it suspends a sentence as often as it closes one.
SENTENCE_OPENER = re.compile(r"(?<=[.!?])\s+([a-záéíóúüñ])(?=[a-záéíóúüñ])")

ABBREVIATIONS = (
    "etc",
    "ej",
    "p",
    "pp",
    "vol",
    "cap",
    "núm",
    "sr",
    "sra",
    "srta",
    "dr",
    "dra",
    "d",
    "dña",
    "ud",
    "vd",
    "s",
    "a",
    "c",
)


def mechanical_edits(text):
    """Every orthotypographic edit the norm decides on its own, in text order.

    Returned as ``Edit`` and not applied here: the pipeline has exactly one
    channel through which text changes (ARCHITECTURE §4), and a correction that
    skipped it would be the only one in the run without an anchor, a type and a
    reason in the report.
    """
    edits = [
        *_quotes(text),
        *_dashes(text),
        *_spacing(text),
        *_opening_signs(text),
        *_capitals(text),
    ]
    return sorted(edits, key=lambda edit: (edit.start, edit.end))


def _edit(start, end, replacement, kind, rule):
    return Edit(start=start, end=end, replacement=replacement, kind=kind, rule=rule)


def _quotes(text):
    """Straight quotes to angular ones, opening and closing by count.

    An odd number of marks in the text means one of them is not part of a pair,
    and there is no way to tell which. The whole text is then left alone: a
    guess would put a closing mark where a quotation opens.
    """
    marks = [match.start() for match in STRAIGHT_QUOTE.finditer(text)]
    if not marks or len(marks) % 2:
        return []
    return [
        _edit(at, at + 1, "«" if index % 2 == 0 else "»", "comillas", "comillas latinas")
        for index, at in enumerate(marks)
    ]


def _dashes(text):
    return [
        _edit(m.start(), m.end(), "—", "raya_dialogo", "raya de diálogo, no guion")
        for m in HYPHEN_AS_DASH.finditer(text)
    ]


def _spacing(text):
    edits = [
        _edit(m.start(), m.end(), "", "espaciado", "sin espacio antes del signo")
        for m in SPACE_BEFORE_MARK.finditer(text)
        # A mark opening a line is not a mark with a space in front of it.
        if not text[: m.start()].endswith("\n") and "\n" not in m.group()
    ]
    edits += [
        _edit(m.start(), m.start(), " ", "espaciado", "espacio tras el signo")
        for m in MISSING_SPACE_AFTER.finditer(text)
    ]
    return edits


def _opening_signs(text):
    """Restore the ¿ or ¡ that a closing sign says must be there.

    Spanish is the language that opens these, so a lone «?» is an error the
    norm names outright. The head of the clause is found by walking back to the
    nearest boundary; when the stretch between that boundary and the closing
    sign already carries an opening one, there is nothing to add.
    """
    edits = []
    for match in re.finditer(r"[?!]", text):
        opening = PAIRED[match.group()]
        boundary = _clause_start(text, match.start())
        if boundary is None:
            continue
        # Asked of the stretch as written, before anything is skipped over:
        # the sign already standing there is itself one of the characters an
        # insertion point steps past, so looking after the skip is looking
        # exactly where it cannot be. Either sign counts — «¡Pero qué dices?»
        # is a mixed pair the norm allows, and it must not be doubled.
        if "¿" in text[boundary : match.start()] or "¡" in text[boundary : match.start()]:
            continue
        head = boundary
        while head < match.start() and text[head] in LEADING:
            head += 1
        # A closing sign with nothing in front of it is not a question.
        if head >= match.start() or not text[head : match.start()].strip():
            continue
        edits.append(_edit(head, head, opening, "signo_apertura", "signo de apertura"))
    return edits


def _capitals(text):
    """The capital a sentence opens with, and the one a month never carries."""
    edits = [
        _edit(m.start(1), m.end(1), m.group(1).upper(), "mayuscula", "mayúscula inicial")
        for m in SENTENCE_OPENER.finditer(text)
        if not _abbreviated(text, m.start())
    ]
    edits += [
        _edit(m.start(1), m.end(1), m.group(1).lower(), "mayuscula", "minúscula obligatoria")
        for m in ALWAYS_LOWERCASE.finditer(text)
        if m.group(1)[:1].isupper()
    ]
    return edits


def _abbreviated(text, at):
    """Whether the stop before ``at`` closes an abbreviation rather than a sentence.

    ``at`` is where the whitespace after the stop begins, so the stop is the
    last character of ``text[:at]`` and the word carrying it is what precedes.
    """
    word = re.search(r"([^\W\d_]+)\.$", text[:at])
    return bool(word) and word.group(1).lower() in ABBREVIATIONS


def _clause_start(text, at):
    """The boundary the sentence holding ``at`` opens at, as written."""
    starts = [m.end() for m in CLAUSE_HEAD.finditer(text, 0, at)]
    return starts[-1] if starts else None
