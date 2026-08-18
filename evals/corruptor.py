"""Seeds typed errors into clean text.

This is the scientific core of the harness: corrupting a clean fragment gives
exact references for free. Every error we inject is one we know how to score,
and the gold edits are, by construction, whatever restores the original.

Invariant: ``apply_edits(result.text, result.gold)[0] == result.clean``.
"""

import random
import re
from collections import Counter

from pydantic import BaseModel, Field

from corrector.edits import Edit, apply_edits, trim

MIN_GAP = 12

RULES = {}


class Candidate(BaseModel):
    """A site where a rule could inject its error. Only some become
    corruptions; ``kind`` is stamped by ``corrupt`` from the registry key."""

    start: int = Field(ge=0)
    end: int = Field(ge=0)
    replacement: str
    kind: str = ""


class CorruptedText(BaseModel):
    name: str = ""
    clean: str
    text: str
    gold: list[Edit]

    def counts_by_kind(self):
        return Counter(edit.kind for edit in self.gold)


def rule(kind):
    """Register a rule for one error type.

    A rule takes the clean text and returns every site where it could inject
    its error, as spans of that text. Sites may overlap each other and may be
    no-ops: ``_far_enough`` and ``_changes_text`` filter both, so a rule never
    needs to deduplicate its own output. Leave ``kind`` unset — it is stamped
    from the key here, so the two can never drift apart.

    Only the span can be wrong. The gold edit is derived as the exact inverse
    of the change, so a rule cannot produce an unrecoverable corruption.
    """

    def register(fn):
        RULES[kind] = fn
        return fn

    return register


def sites(text, pattern, replacement, group=0, flags=0):
    """One candidate per match, replacing ``group``.

    ``replacement`` is either a literal string or a callable taking the match.
    """
    return [
        Candidate(
            start=match.start(group),
            end=match.end(group),
            replacement=replacement(match) if callable(replacement) else replacement,
        )
        for match in re.finditer(pattern, text, flags)
    ]


def corrupt(clean, rate=0.02, seed=0, kinds=None, name=""):
    """Inject roughly ``rate`` errors per word, spread evenly across types."""
    rng = random.Random(seed)
    active = [k for k in RULES if kinds is None or k in kinds]

    buckets = {}
    for kind in active:
        found = [c for c in RULES[kind](clean) if _changes_text(clean, c)]
        for candidate in found:
            candidate.kind = kind
        rng.shuffle(found)
        if found:
            buckets[kind] = found

    # Types that compete for the same sites (laísmo and loísmo both want
    # "le + verbo de habla") would starve each other under a fixed order.
    order = sorted(buckets)
    rng.shuffle(order)

    target = max(1, round(len(clean.split()) * rate))
    chosen = _round_robin(buckets, target, order)
    return _apply_and_invert(clean, chosen, name)


def _round_robin(buckets, target, order):
    """Take one candidate per type in turn so no type dominates the corpus."""
    chosen = []
    while len(chosen) < target:
        progressed = False
        for kind in order:
            if len(chosen) >= target:
                break
            while buckets[kind]:
                candidate = buckets[kind].pop()
                if _far_enough(candidate, chosen):
                    chosen.append(candidate)
                    progressed = True
                    break
        if not progressed:
            break
    return sorted(chosen, key=lambda c: c.start)


def _far_enough(candidate, chosen):
    return all(
        candidate.end + MIN_GAP <= other.start or other.end + MIN_GAP <= candidate.start
        for other in chosen
    )


def _changes_text(clean, candidate):
    return clean[candidate.start : candidate.end] != candidate.replacement


def _apply_and_invert(clean, chosen, name):
    pieces, gold = [], []
    cursor, shift = 0, 0
    for candidate in chosen:
        pieces.append(clean[cursor : candidate.start])
        pieces.append(candidate.replacement)
        start = candidate.start + shift
        gold.append(
            Edit(
                start=start,
                end=start + len(candidate.replacement),
                replacement=clean[candidate.start : candidate.end],
                kind=candidate.kind,
            )
        )
        shift += len(candidate.replacement) - (candidate.end - candidate.start)
        cursor = candidate.end
    pieces.append(clean[cursor:])

    text = "".join(pieces)
    return CorruptedText(name=name, clean=clean, text=text, gold=[trim(text, e) for e in gold])


def restores_clean(result):
    """The round trip that makes the gold trustworthy."""
    restored, rejected = apply_edits(result.text, result.gold)
    return restored == result.clean and not rejected


# --- ortografía -------------------------------------------------------------

STRIP_ACCENT = str.maketrans("áéíóúÁÉÍÓÚ", "aeiouAEIOU")
WORD = re.compile(r"[^\W\d_]+")

DIACRITICS = {
    "él": "el",
    "tú": "tu",
    "mí": "mi",
    "sí": "si",
    "sé": "se",
    "té": "te",
    "dé": "de",
    "más": "mas",
    "qué": "que",
    "cómo": "como",
    "cuándo": "cuando",
    "dónde": "donde",
    "quién": "quien",
    "cuál": "cual",
    "cuánto": "cuanto",
    "aún": "aun",
}
# Adding an accent to these is not reliably an error, so only strip them.
NEVER_ADD = {"mas", "aun"}
UNACCENTED = {v: k for k, v in DIACRITICS.items() if v not in NEVER_ADD}


@rule("tilde")
def _tilde(text):
    # Only polysyllables: the monosyllables live in tilde_diacritica.
    return [
        Candidate(start=m.start(), end=m.end(), replacement=m.group().translate(STRIP_ACCENT))
        for m in WORD.finditer(text)
        if len(m.group()) >= 4 and m.group().lower() not in DIACRITICS
    ]


@rule("tilde_diacritica")
def _tilde_diacritica(text):
    def swapped(match):
        word = match.group()
        return _match_case(word, DIACRITICS.get(word.lower()) or UNACCENTED[word.lower()])

    pairs = set(DIACRITICS) | set(UNACCENTED)
    return [
        Candidate(start=m.start(), end=m.end(), replacement=swapped(m))
        for m in WORD.finditer(text)
        if m.group().lower() in pairs
    ]


@rule("ortografia_h")
def _ortografia_h(text):
    return [
        Candidate(start=m.start(), end=m.start() + 1, replacement="")
        for m in WORD.finditer(text)
        if len(m.group()) >= 4 and m.group()[0] in "hH"
    ]


BV_SWAP = {"b": "v", "v": "b", "B": "V", "V": "B"}


@rule("ortografia_bv")
def _ortografia_bv(text):
    # The first b or v of each word: the two letters are homophones in Spanish.
    out = []
    for match in WORD.finditer(text):
        word = match.group()
        offset = next((i for i, char in enumerate(word) if char in BV_SWAP), None)
        if len(word) >= 3 and offset is not None:
            out.append(
                Candidate(
                    start=match.start() + offset,
                    end=match.start() + offset + 1,
                    replacement=BV_SWAP[word[offset]],
                )
            )
    return out


HOMOPHONES = {
    "haber": "a ver",
    "a ver": "haber",
    "hay": "ahí",
    "ahí": "hay",
    "hecho": "echo",
    "echo": "hecho",
    "haya": "halla",
    "halla": "haya",
    "vaya": "valla",
    "valla": "vaya",
    "tuvo": "tubo",
    "tubo": "tuvo",
    "sino": "si no",
    "si no": "sino",
    "porque": "por que",
    "por qué": "porque",
    "vez": "ves",
    "ves": "vez",
}


@rule("homofono")
def _homofono(text):
    return [
        candidate
        for source, target in HOMOPHONES.items()
        for candidate in sites(
            text,
            rf"\b{re.escape(source)}\b",
            lambda m, t=target: _match_case(m.group(), t),
            flags=re.IGNORECASE,
        )
    ]


# --- gramática --------------------------------------------------------------

# fmt: off
# A word list reads as a list; one entry per line reads as code.
MASCULINE = {
    "el": "la", "un": "una", "este": "esta", "ese": "esa",
    "los": "las", "unos": "unas", "estos": "estas",
}
FEMININE = {v: k for k, v in MASCULINE.items()}
PLURALISE = {
    "el": "los", "la": "las", "un": "unos",
    "una": "unas", "este": "estos", "esta": "estas",
}
SINGULARISE = {v: k for k, v in PLURALISE.items()}
# fmt: on

DETERMINER = re.compile(
    r"\b(el|la|los|las|un|una|unos|unas|este|esta|estos|estas|ese|esa)\s+([^\W\d_]+)\b",
    re.IGNORECASE,
)


def _determiner_sites(text, tables):
    """Swap the determiner only when the noun's ending confirms the mismatch.

    Each entry pairs a swap table with the noun endings that make the swap a
    certain error, so no part-of-speech tagger is needed.
    """
    out = []
    for table, endings in tables:
        for match in DETERMINER.finditer(text):
            determiner, noun = match.group(1), match.group(2)
            swap = table.get(determiner.lower())
            if not swap or len(noun) < 4 or not noun.lower().endswith(endings):
                continue
            out.append(
                Candidate(
                    start=match.start(1),
                    end=match.end(1),
                    replacement=_match_case(determiner, swap),
                )
            )
    return out


@rule("concordancia_genero")
def _concordancia_genero(text):
    return _determiner_sites(text, ((MASCULINE, ("o", "os")), (FEMININE, ("a", "as"))))


@rule("concordancia_numero")
def _concordancia_numero(text):
    return _determiner_sites(text, ((PLURALISE, ("o", "a")), (SINGULARISE, ("os", "as"))))


DEQUEISMO_VERBS = (
    "pienso|piensa|pensaba|pensó|creo|cree|creía|creyó|dijo|dije|dicen|decía|resulta|opino|opina|"
    "considero|considera|sé|sabe|sabía|supongo|supone|recuerdo|recuerda|imagino|imagina|temo|teme|"
    "siento|siente|parece|parecía|noto|nota|entiendo|entiende|afirma|asegura|comprende|sospecho|sospecha"
)

QUEISMO_HEADS = (
    "cuenta|seguro|segura|seguros|seguras|alegro|alegra|alegré|acuerdo|acuerda|olvidé|olvidó|"
    "convencido|convencida|consciente|capaz|hecho|caso|miedo|ganas|idea|impresión|sensación|certeza|"
    "temor|esperanza|duda|posibilidad|costumbre"
)


@rule("dequeismo")
def _dequeismo(text):
    return sites(
        text,
        rf"\b({DEQUEISMO_VERBS})(\s+)que\b",
        lambda m: m.group(2) + "de ",
        group=2,
        flags=re.IGNORECASE,
    )


@rule("queismo")
def _queismo(text):
    return sites(
        text,
        rf"\b({QUEISMO_HEADS})(\s+de\s+)que\b",
        " ",
        group=2,
        flags=re.IGNORECASE,
    )


DATIVE_VERBS = (
    "dijo|dije|dijeron|preguntó|pregunté|contestó|respondió|explicó|explicaba|pidió|pedía|"
    "ofreció|entregó|dio|daba|escribió|contó|contaba|mandó|envió|prometió|advirtió"
)


def _clitic_sites(text, replacement):
    def swapped(match):
        clitic = match.group(1)
        target = replacement + ("s" if clitic.lower() == "les" else "")
        return _match_case(clitic, target)

    return sites(
        text,
        rf"\b(le|les)(\s+(?:{DATIVE_VERBS}))\b",
        swapped,
        group=1,
        flags=re.IGNORECASE,
    )


@rule("laismo")
def _laismo(text):
    return _clitic_sites(text, "la")


@rule("loismo")
def _loismo(text):
    return _clitic_sites(text, "lo")


# fmt: off
NOT_PRETERITE = {
    "chiste", "triste", "existe", "insiste", "asiste", "resiste", "persiste", "consiste",
    "embiste", "reviste", "desiste", "viste", "batiste", "aliste", "conquiste", "moleste",
}
# fmt: on


@rule("verbo_2sg")
def _verbo_2sg(text):
    # The spurious -s of "dijistes". Zero-width: nothing is replaced, only added.
    return [
        Candidate(start=m.end(), end=m.end(), replacement="s")
        for m in re.finditer(r"\b[^\W\d_]{3,}(?:aste|iste)\b", text, re.IGNORECASE)
        if m.group().lower() not in NOT_PRETERITE
    ]


# --- ortotipografía ---------------------------------------------------------


@rule("raya_dialogo")
def _raya_dialogo(text):
    return sites(text, r"^—", "-", flags=re.MULTILINE) + sites(
        text, r"(?<=\S)\s—|—(?=\S)", lambda m: m.group().replace("—", "-")
    )


@rule("comillas")
def _comillas(text):
    return sites(text, r"[«»“”]", '"')


@rule("signo_apertura")
def _signo_apertura(text):
    return sites(text, r"[¿¡]", "")


@rule("espaciado")
def _espaciado(text):
    # A space before the mark, and the space after it removed.
    return sites(text, r"(?<=[^\s])([,;:])(?= )", lambda m: " " + m.group(1), group=1) + sites(
        text, r"([,;:]) (?=\S)", lambda m: m.group(1)
    )


# Capitalising these mid-sentence is a real error in Spanish (interference
# from English), unlike a random internal capital, which nobody writes.
ALWAYS_LOWERCASE = (
    "enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre|"
    "lunes|martes|miércoles|jueves|viernes|sábado|domingo|"
    "español|española|francés|francesa|inglés|inglesa|castellano|castellana|asturiano|asturiana"
)


@rule("mayuscula")
def _mayuscula(text):
    # Lowercase the letter that opens a sentence, and capitalise a word that
    # Spanish always keeps lowercase.
    return sites(
        text,
        r"(?<=[.!?…]\s)([^\W\d_])(?=[^\W\d_])",
        lambda m: m.group(1).lower(),
        group=1,
    ) + sites(
        text,
        rf"(?<=[a-záéíóúñ] )({ALWAYS_LOWERCASE})\b",
        lambda m: m.group(1).capitalize(),
        group=1,
    )


def _match_case(source, target):
    if source.isupper() and len(source) > 1:
        return target.upper()
    if source[:1].isupper():
        return target[:1].upper() + target[1:]
    return target
