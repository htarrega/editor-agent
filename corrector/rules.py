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
# Not «space, mark, space»: a mark can be missing its following space at the
# same time as carrying a spurious leading one, and the two are separate slips
# that have to be fixable together.
SPACE_BEFORE_MARK = re.compile(r"[^\S\n]+(?=[,;:!?.])")
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


# The vowels an accent can land on, and the letters that sound the same. These
# three are the classes of Spanish misspelling that turn a real word into a
# non-word — which is exactly the property that makes them decidable without
# context, and exactly what separates them from `tilde_diacritica` (`esta` and
# `está` are both words) or `homofono` (`tuvo` and `tubo` are both words).
# Those two stay with the model, where they belong.
ACCENTS = {"a": "á", "e": "é", "i": "í", "o": "ó", "u": "ú"}
UNACCENT = {accented: plain for plain, accented in ACCENTS.items()}
BV = {"b": "v", "v": "b"}

WORD = re.compile(r"[^\W\d_]+", re.UNICODE)

# Below this a minimal repair is not minimal: `de`, `da` and `dé` are three
# words apart by one accent, and none of them is a misspelling of another.
MIN_WORD = 4


def _known(word):
    """Whether ``word`` is a form of Spanish. Loaded on first use, not on import.

    `simplemma` reads a few megabytes of language data the first time it is
    asked, and a process that never corrects anything — the offline test suite,
    a `--systems null` run — should not pay for it.
    """
    import simplemma

    return simplemma.is_known(word, lang="es")


def mechanical_edits(text):
    """Every edit the language decides on its own, in text order.

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
        *_verb_2sg(text),
    ]
    return _reconcile(sorted(edits, key=lambda edit: (edit.start, edit.end)), _spelling(text))


def _reconcile(edits, spellings):
    """Fold the spelling repairs in, letting a repaired word keep its capital.

    A word can be both misspelled and sentence-initial — «Tambien» after a full
    stop wants an accent *and* a capital — and the two edits cover overlapping
    characters, so `apply_edits` would drop one of them by position. The repair
    spans the whole word, so it is the one that can carry both: it takes the
    capital and the capitals rule stands down.
    """
    out = list(edits)
    for repair in spellings:
        clash = [
            edit
            for edit in out
            if edit.start < repair.end and repair.start < edit.end and edit.kind == "mayuscula"
        ]
        if any(
            edit.kind != "mayuscula"
            for edit in out
            if edit.start < repair.end and repair.start < edit.end
        ):
            continue
        for edit in clash:
            out.remove(edit)
        if clash:
            repair = repair.model_copy(
                update={"replacement": repair.replacement[:1].upper() + repair.replacement[1:]}
            )
        out.append(repair)
    return sorted(out, key=lambda edit: (edit.start, edit.end))


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


def _spelling(text):
    """Words that are not Spanish, and are one minimal repair away from being.

    This is a spellchecker's argument, narrowed to the point where it cannot
    argue with the author. It fires only when the word as written is not a form
    of Spanish *and* exactly one of the three repairs produces one that is. So
    `vasu`, `pumarada`, `fíos`, `gomitar` and `merequetengue` — every invented
    or dialectal word in this corpus — are left alone, because they are not in
    the dictionary either and no accent, no `h` and no `b` puts them there.
    Being unknown is never on its own a reason to touch a word; the repair is.

    «Exactly one» is the other half. A word two known repairs away is a word we
    cannot choose between without reading the sentence, and reading the
    sentence is the model's job.
    """
    edits = []
    for match in WORD.finditer(text):
        word = match.group()
        if len(word) < MIN_WORD or not word.islower() and not _sentence_initial(text, match):
            # A capital inside a sentence is a name, and a name is not a word
            # this dictionary has an opinion about.
            continue
        lowered = word.lower()
        if _known(lowered) or _enclitic(lowered):
            continue
        repairs = {pair for pair in _repairs(lowered) if _known(pair[0])}
        if len(repairs) != 1:
            continue
        repaired, kind = repairs.pop()
        edits.append(
            _edit(
                match.start(),
                match.end(),
                _recase(word, repaired),
                kind,
                "palabra inexistente; una enmienda mínima la corrige",
            )
        )
    return edits


def _repairs(word):
    """Every word one accent, one «h» or one b/v away from ``word``, with which.

    The accent is only ever *added*. Taking one away is a claim about the
    author's own writing rather than about the language, and the dictionary is
    not good enough to make it: it does not carry `ojalá` or `jamás`, but it
    does carry `ojala` and `jamas` as forms of `ojalar` and `jamar`, so the
    removal direction proposes stripping the accent off two perfectly correct
    adverbs. Adding one has no symmetric failure — a word that gains an accent
    and becomes a different word is a word the accent belonged to.
    """
    out = set()
    for index, char in enumerate(word):
        if char in ACCENTS:
            out.add((word[:index] + ACCENTS[char] + word[index + 1 :], "tilde"))
        if char in BV:
            out.add((word[:index] + BV[char] + word[index + 1 :], "ortografia_bv"))
    out.add(("h" + word, "ortografia_h"))
    if word.startswith("h"):
        out.add((word[1:], "ortografia_h"))
    return {(candidate, kind) for candidate, kind in out if candidate != word}


# What can hang off the end of an infinitive or a gerund. The dictionary does
# not hold these forms — `irme`, `decirle`, `contarlo` are all «unknown» — so
# without this guard every one of them is a word looking for a repair, and it
# finds them: `hirme` is in the dictionary and `irme` is not.
ENCLITICS = (
    "melo",
    "mela",
    "selo",
    "sela",
    "selos",
    "selas",
    "telo",
    "tela",
    "me",
    "te",
    "se",
    "lo",
    "la",
    "le",
    "nos",
    "os",
    "los",
    "las",
    "les",
)


def _enclitic(word):
    """Whether ``word`` is a verb with pronouns stuck to it.

    Checked by taking them off: if what is left is a form of Spanish, the word
    was never misspelled, it was inflected in a way the dictionary does not
    list.
    """
    for suffix in ENCLITICS:
        if word.endswith(suffix) and len(word) > len(suffix) + 1:
            stem = word[: -len(suffix)]
            if _known(stem) or any(_known(stem + vowel) for vowel in ("", "e")):
                return True
    return False


def _recase(original, repaired):
    return repaired[:1].upper() + repaired[1:] if original[:1].isupper() else repaired


def _sentence_initial(text, match):
    """Whether the word opens a sentence, where a capital says nothing about it."""
    before = text[: match.start()].rstrip()
    return not before or before[-1] in ".!?…:—«\n"


# Spanish second person singular preterite ends in «-ste», never «-stes». The
# form with the s is analogical from every other second person and is the most
# frequent non-standard verb form in the language; it is also decidable on
# sight, which no other agreement error is.
VERB_2SG = re.compile(r"\b([^\W\d_]{3,}ste)s\b", re.IGNORECASE)


def _verb_2sg(text):
    return [
        _edit(
            m.end(1), m.end(0), "", "verbo_2sg", "segunda persona del singular: «-ste», no «-stes»"
        )
        for m in VERB_2SG.finditer(text)
        if _known(m.group(1).lower()) and not _known(m.group(0).lower())
    ]


def _clause_start(text, at):
    """The boundary the sentence holding ``at`` opens at, as written."""
    starts = [m.end() for m in CLAUSE_HEAD.finditer(text, 0, at)]
    return starts[-1] if starts else None
