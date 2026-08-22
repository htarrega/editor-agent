"""The corrector pass: one call, minimal edits, never a rewrite.

The model is given numbered text and answers with a list of anchored edits.
It never returns prose, so there is no rewritten version to diff against and no
way for it to reword a sentence it was only asked to look at. Code resolves
each anchor to offsets and applies it; anything that does not resolve is
discarded and counted.

Unlike the naive baseline in ``evals/systems.py``, this prompt is allowed to
state the correction policy — minimal edits, hands off the voice. That policy
is the product (ARCHITECTURE §1), which is exactly why the baseline may not be
handed it.
"""

import json
import re
import time
from collections import Counter, namedtuple
from concurrent.futures import ThreadPoolExecutor

from pydantic import BaseModel, ValidationError

from corrector.blocks import DEFAULT_BLOCK_WORDS, block_spans
from corrector.edits import Edit, ProposedEdit, resolve_edits, trim
from corrector.llm import Usage, spent
from corrector.rules import mechanical_edits
from corrector.taxonomy import ERROR_TYPES

FENCE = re.compile(r"```[^\n]*\n(?P<body>.*?)```", re.DOTALL)

PROMPT = """Eres un corrector profesional de narrativa en español.

Señalas errores; no mejoras el texto. Cada intervención es una edición mínima: el
tramo más corto que arregla el error, y nada más.

CORRIGES
- Ortografía: tildes, tildes diacríticas, h inicial, b/v, homófonos (haber/a ver,
  hay/ahí, echo/hecho, haya/halla, tuvo/tubo, sino/si no).
- Gramática: concordancia de género y número, dequeísmo, queísmo, laísmo, loísmo,
  formas verbales inexistentes («dijistes» por «dijiste»).
- Ortotipografía: raya de diálogo (— y no guion ni menos), comillas latinas («»),
  signos de apertura (¿ ¡), espacio antes o después de la puntuación, mayúsculas
  y minúsculas.

NO CORRIGES
- El estilo: léxico, ritmo, longitud de las frases, repeticiones, orden de palabras,
  conectores. Aunque tú lo escribirías de otra manera, no es un error.
- La voz del autor: registro, dialectalismos, arcaísmos, palabras inventadas,
  nombres propios y topónimos que no conozcas. Si una palabra rara es coherente con
  el resto del texto, es del autor y se queda.
- Lo que ya es correcto. Ante la duda, no corriges: un falso positivo cuesta más
  que un error sin detectar.

ENTRADA
El texto llega en bloques numerados. El marcador «[N]» va en su propia línea, no
forma parte del texto y no se corrige nunca.

SALIDA
Únicamente un objeto JSON, sin comentarios, sin markdown y sin texto alrededor:

{{"edits": [
  {{"line": 7, "original": "el mesa", "replacement": "la mesa",
   "kind": "concordancia_genero", "rule": "concordancia de género", "confidence": 0.95}}
]}}

- «line»: el número del bloque donde está el error.
- «original»: copia literal del texto que se sustituye, tal como aparece en ese
  bloque, con el contexto justo para que sea inequívoco dentro del bloque. Si no
  aparece exactamente, la edición se descarta.
- «replacement»: lo que ocupa su lugar.
- «kind»: uno de estos tipos.
{kinds}
- «rule»: la norma, en pocas palabras.
- «confidence»: entre 0 y 1.

Si el texto no tiene errores, devuelves {{"edits": []}}."""

# Appended to the user message, after the numbered text, when a call owns only
# part of the document. Last rather than first so that everything above it —
# system prompt and document — stays a stable prefix across the calls of one
# pass, and because the constraint the model has to hold while it answers is
# the one it read most recently.
FOCUS = """

Corriges ÚNICAMENTE los bloques [{first}] a [{last}].

El resto del texto está aquí como contexto y no como encargo: es lo que te
permite saber si una palabra rara es del autor, si un nombre propio se escribe
así en todo el manuscrito o si un diálogo venía ya abierto. No propones
ediciones fuera de ese rango —otra llamada se ocupa de ellos— y las que
propongas dentro se descartan si caen fuera.

Antes de emitir cada edición, compruébala contra esta regla: si no puedes
nombrar la norma ortográfica, gramatical u ortotipográfica que incumple, no es
un error y no la emites. No hay ediciones de estilo, ni de léxico, ni de
registro, ni de ritmo. Una palabra que no reconoces y que el texto repite es
del autor y se queda como está. Ante la duda, no corriges."""


# The second wave. One call per proposed edit, and a much easier question than
# the one that produced it: judging a candidate is not finding one.
#
# H2 was dropped because the default pass left almost nothing to verify — 0.12
# false positives per 1,000 clean words. `corrector-fast` restores the premise
# it died for: with the deliberation off, precision falls to ~0.90, and a
# verifier exists exactly to buy that back without touching what recall is made
# of. The calls are independent and tiny, so the whole wave is one round trip.
VERIFY = """Eres un corrector profesional de narrativa en español, revisando una corrección
que ha propuesto un compañero sobre un texto.

Tu compañero ya ha leído el texto y ha visto algo. Tu trabajo no es volver a
corregir: es descartar lo que se le ha colado. Por eso la respuesta por defecto
es MANTENER, y solo descartas cuando estás seguro.

DESCARTAR si el texto original, tal como está, ya es correcto:
- la corrección es de estilo, léxico, registro o ritmo, no de norma;
- la palabra es inventada, un dialectalismo, un arcaísmo o un nombre propio del
  autor —aunque no la reconozcas, es suya y se queda—;
- la corrección cambia el sentido de la frase;
- las dos formas son correctas y la elección es del autor.

MANTENER en todo lo demás, incluido cuando no estás seguro: tu compañero tenía
delante el mismo texto que tú.

Respondes únicamente MANTENER o DESCARTAR, sin explicar nada."""

PROPOSAL = """{context}

En el bloque [{block}] se propone esta corrección:

  «{original}» → «{replacement}»

¿Mantener o descartar? Responde únicamente MANTENER o DESCARTAR."""


# One narrowed brief per mechanical category, appended after FOCUS when a pass
# runs its windows once per aspect instead of once.
#
# A model that does not deliberate is answering from one reading, and one
# reading of "todo lo anterior" is worse than three readings of a third of it:
# the whole `CORRIGES` list stays in the system prompt — the voice policy with
# it — and this only says which part of it this call is answering for.
ASPECTS = {
    "ortografía": (
        "En esta pasada buscas SOLO ortografía: tildes, tildes diacríticas, h "
        "inicial, b/v, y homófonos (haber/a ver, hay/ahí, echo/hecho, haya/halla, "
        "tuvo/tubo, sino/si no). Nada de gramática ni de ortotipografía."
    ),
    "gramática": (
        "En esta pasada buscas SOLO gramática: concordancia de género y de número, "
        "dequeísmo, queísmo, laísmo, loísmo y formas verbales inexistentes "
        "(«dijistes» por «dijiste»). Nada de ortografía ni de ortotipografía."
    ),
    # What no rule decides. `corrector/rules.py` owns comillas, espaciado,
    # mayúsculas, la raya y los signos de apertura outright — 1.000 recall on
    # three of them — and a model with its deliberation off has one reading to
    # spend. This brief spends it on the types that need the sentence read:
    # every one of them is a pair of real words, or an agreement across a
    # clause, and no regular expression will ever decide them.
    "juicio": (
        "En esta pasada NO buscas ortotipografía —comillas, rayas, signos de "
        "apertura, espaciado y mayúsculas los corrige una regla aparte y ya están "
        "resueltos—. Buscas solo lo que exige leer la frase: concordancia de "
        "género y de número, dequeísmo, queísmo, laísmo, loísmo, formas verbales "
        "inexistentes («dijistes»), homófonos (haber/a ver, hay/ahí, echo/hecho, "
        "haya/halla, tuvo/tubo, vaya/valla, sino/si no) y tildes diacríticas "
        "(el/él, tu/tú, si/sí, mas/más, se/sé, de/dé, aun/aún, solo/sólo)."
    ),
    "ortotipografía": (
        "En esta pasada buscas SOLO ortotipografía: raya de diálogo (— y no guion "
        "ni menos), comillas latinas («»), signos de apertura (¿ ¡), espacio "
        "sobrante o ausente junto a la puntuación, y mayúsculas y minúsculas. "
        "Nada de ortografía ni de gramática."
    ),
}

MECHANICAL = tuple(name for name in ASPECTS if name != "juicio")


class Correction(BaseModel):
    """What one corrector pass produced, and what it cost."""

    edits: list[Edit] = []
    usage: Usage = Usage()
    proposed: int = 0
    skipped: int = 0
    rejected: dict[str, int] = {}
    errors: list[str] = []


def kinds_block():
    """The taxonomy, grouped, straight from the source the metrics score against.

    Written out rather than hardcoded so a type cannot exist in the corruptor
    and be missing from the prompt: the corrector would keep finding the error
    and keep labelling it «otro».
    """
    grouped = {}
    for kind, category in ERROR_TYPES.items():
        grouped.setdefault(category, []).append(kind)
    return "\n".join(f"  · {category}: {', '.join(kinds)}" for category, kinds in grouped.items())


def render(text, spans=None, first=1):
    """Number the blocks, marker on its own line.

    Off to the side rather than inline because half of what this pass corrects
    is orthotypography, and a dialogue dash judged as «12| —Vamos» is a dash
    the model has been shown in a context the author never wrote.

    Where the blocks are cut is ``corrector/blocks.py``'s business; the default
    is one block per line. ``first`` is the number the first span in ``spans``
    carries: per-block inference renders one span at a time and has to carry
    its true position among the document's blocks, or every call would show
    the model a lone ``[1]`` regardless of where the block really sits.
    """
    spans = block_spans(text) if spans is None else spans
    return "\n\n".join(f"[{n}]\n{text[s:e]}" for n, (s, e) in enumerate(spans, first))


def parse_edits(raw):
    """Read the model's answer. Returns the proposals and how many were unusable.

    Raises ``ValueError`` if there is no JSON at all: a reply we cannot read is
    a failed call, not a text without errors, and scoring it as zero edits
    would credit it with a perfect false-positive rate.
    """
    payload = json.loads(_json_body(raw))
    if isinstance(payload, dict):
        payload = payload.get("edits") or payload.get("ediciones") or []
    if not isinstance(payload, list):
        raise ValueError(f"expected a list of edits, got {type(payload).__name__}")

    proposals, malformed = [], 0
    for item in payload:
        try:
            proposals.append(ProposedEdit.model_validate(item))
        except (ValidationError, TypeError):
            malformed += 1
    return proposals, malformed


class Corrector:
    """One pass over a text. The unit H5 will chunk a manuscript into.

    ``blocks_per_call`` is opt-in and defaults to ``None``: with it unset,
    ``correct`` is byte-identical to what H1 and H5 measured — every block in
    one call for the whole document — because the frozen rows in docs/PLAN.md
    depend on exactly that request shape. Set it to an integer to send that
    many blocks per call instead, sequential for now; ``1`` is one call per
    block. How the text is *numbered* (``block_words``) and how it is *split
    across calls* are separate axes, and only the first of them has been
    measured. See ``_correct_batched`` for what the second one costs.
    """

    def __init__(
        self,
        model,
        generate,
        prompt=None,
        block_words=DEFAULT_BLOCK_WORDS,
        blocks_per_call=None,
        concurrency=1,
        window_blocks=None,
        context_blocks=None,
        aspects=None,
        mechanical=False,
        verify=False,
        attempts=1,
        deadline=None,
        fallback=None,
    ):
        # `block_words=None` is one block per line, which is what H1 measured and
        # what the frozen reference rows in the harness ask for by name.
        if blocks_per_call and window_blocks:
            raise ValueError("blocks_per_call and window_blocks are two ways to cut the same pass")
        self.model = model
        self.prompt = (prompt or PROMPT).format(kinds=kinds_block())
        self.block_words = block_words
        self.blocks_per_call = blocks_per_call
        self.window_blocks = window_blocks
        self.context_blocks = context_blocks
        unknown = [name for name in aspects or () if name not in ASPECTS]
        if unknown:
            raise ValueError(f"unknown aspects {unknown}; available: {sorted(ASPECTS)}")
        self.aspects = tuple(aspects) if aspects else ()
        # Off by default: every row in docs/PLAN.md was measured without it,
        # and a pass that silently gained a second source of edits would make
        # those rows say something they were never asked.
        self.mechanical = mechanical
        # The second wave. Off by default for the same reason as `mechanical`:
        # every row above was measured without it.
        self.verify = verify
        # How many times each call is issued, and how long the whole fan-out
        # may take. Both default to the shape every row above was measured
        # with: one attempt, and no clock.
        self.attempts = attempts
        self.deadline = deadline
        # A second, faster way of answering the same call, raced alongside the
        # first. Never preferred to it — only read for the windows the
        # deliberated attempts did not deliver in time.
        self.fallback = fallback
        # Only ever reached through `blocks_per_call` or `window_blocks`; the
        # whole-document path is one call and has nothing to overlap. Defaults
        # to 1 so that turning either on does not change two things at once.
        self.concurrency = concurrency
        self._generate = generate

    def correct(self, text):
        # Rendered and resolved from the same spans, so what the model was
        # numbered and what an anchor is searched inside cannot drift apart.
        spans = block_spans(text, self.block_words)
        if self.window_blocks:
            result = self._correct_windowed(text, spans, self.window_blocks)
        elif self.blocks_per_call:
            result = self._correct_batched(text, spans, self.blocks_per_call)
        else:
            result = self._correct_whole(text, spans)
        if self.verify:
            # Before the rules go in, not after: the rule pack scores P 0.969 on
            # its eight types and there is nothing for a verifier to buy there,
            # while every call it would spend is a call in the critical path.
            result = self._verified(text, spans, result)
        return self._with_rules(text, result) if self.mechanical else result

    def _verified(self, text, spans, result):
        """Ask about each proposed edit on its own, and keep the ones confirmed.

        One call per candidate rather than one per window, because the calls
        are then independent of each other and of how many candidates a window
        happened to produce — the whole wave is one round trip whatever the
        document did, and a document with forty candidates costs the same wall
        clock as one with four.

        A verifier call that fails is a vote to **keep**. The edit already
        cleared the pass that proposed it; dropping it because a second opinion
        never arrived would let a transient 500 quietly cost recall, and that is
        the one direction this milestone cannot afford to lose silently.
        """
        if not result.edits:
            return result

        users = [
            PROPOSAL.format(
                context=render(text, *_around(spans, edit, self.context_blocks)),
                block=_block_of(spans, edit),
                original=edit.before(text),
                replacement=edit.replacement,
            )
            for edit in result.edits
        ]
        kept, rejected = [], Counter(result.rejected)
        for edit, (reply, failure, seconds) in zip(
            result.edits, self._fanout(users, VERIFY), strict=True
        ):
            if failure is not None:
                result.errors.append(f"verificador: {type(failure).__name__}: {failure}")
                result.usage.add(Usage(calls=1, seconds=seconds))
                kept.append(edit)
                continue
            result.usage.add(spent(self.model, reply, seconds))
            if _says_no(reply.text):
                rejected["unverified"] += 1
            else:
                kept.append(edit)

        result.edits = kept
        result.rejected = dict(rejected)
        result.skipped = sum(rejected.values())
        return result

    def _with_rules(self, text, result):
        """The rule pack's edits alongside the model's, rules winning any clash.

        Rules win because they are decidable and the model is not: on the four
        types this covers the pack scores P 1.000 on 8,254 words of untouched
        prose, which is a claim no model output in this repository can make.
        Where the two propose different fixes to the same characters, keeping
        both would have `apply_edits` drop one as overlapping — silently, and
        by position rather than by which is more likely right.

        A model edit that merely repeats a rule's is counted as `duplicate`
        rather than discarded quietly: it is the measure of how much of this
        the model was doing anyway, and it is what a later run would watch to
        decide whether the prompt should stop asking for it at all.
        """
        rules = mechanical_edits(text)
        if not rules:
            return result

        rejected = Counter(result.rejected)
        kept = []
        for edit in result.edits:
            clash = next(
                (rule for rule in rules if edit.start < rule.end and rule.start < edit.end), None
            )
            # An insertion is zero-width, so the interval test above never
            # catches one sitting exactly where a rule inserts.
            if clash is None and edit.start == edit.end:
                clash = next((rule for rule in rules if rule.start == edit.start), None)
            if clash is None:
                kept.append(edit)
            elif (clash.start, clash.end, clash.replacement) == (
                edit.start,
                edit.end,
                edit.replacement,
            ):
                rejected["duplicate"] += 1
            else:
                rejected["superseded_by_rule"] += 1

        result.proposed += len(rules)
        result.edits = sorted(kept + rules, key=lambda edit: (edit.start, edit.end))
        result.rejected = dict(rejected)
        result.skipped = sum(rejected.values())
        return result

    def _correct_whole(self, text, spans):
        """Today's shape: every block in one prompt, one call for the document."""
        result = Correction()
        started = time.monotonic()
        try:
            reply = self._generate(self.model, self.prompt, render(text, spans))
        except Exception as exc:
            result.errors.append(f"{type(exc).__name__}: {exc}")
            result.usage = Usage(calls=1, seconds=time.monotonic() - started)
            return result

        result.usage = spent(self.model, reply, time.monotonic() - started)
        try:
            proposals, malformed = parse_edits(reply.text)
        except ValueError as exc:
            result.errors.append(f"unparseable reply: {exc}")
            return result

        result.proposed = len(proposals) + malformed
        result.edits, result.rejected = _resolve(text, proposals, malformed, spans)
        result.skipped = sum(result.rejected.values())
        return result

    def _correct_batched(self, text, spans, size):
        """``size`` blocks per call instead of the whole document in one.

        Each batch is rendered carrying the true index of its first block
        (``render(..., first=...)``), not the ``[1]`` it would get from being
        numbered on its own — that numbering is what the prompt tells the model
        a marker means, so it has to match the document. Resolution then runs
        against the full ``spans`` list, exactly as the whole-document path
        does: passing the batch alone would resolve a block index against a
        numbering nobody was shown, and passing nothing at all would fall back
        to one span per *line*.

        A batch of one is the only case where the block a proposal belongs to
        is known without asking: there the ``line`` is overwritten outright,
        so the mapping cannot depend on the model echoing the marker back. With
        more than one block in the call the number is the model's to choose, and
        all this can do is refuse a number from outside the batch — dropping it
        to ``None`` sends the anchor to the text-wide search rather than to some
        other block's span.

        A failing batch is recorded in ``errors`` and skipped; it does not
        discard the other batches' edits. That makes ``errors`` mean something
        different here than it does for ``_correct_whole``: there, any entry
        means the pass returned nothing at all. Here it can hold anywhere from
        one bad call up to every call, with real edits sitting alongside it.
        ``len(result.errors)`` against ``result.usage.calls`` is how a caller
        tells "one of many failed" from "all of them did".
        """
        result = Correction()
        rejected = Counter()
        batches = [
            (offset + 1, spans[offset : offset + size]) for offset in range(0, len(spans), size)
        ]
        users = [render(text, batch, first=first) for first, batch in batches]
        for (first, batch), (reply, failure, seconds) in zip(
            batches, self._fanout(users), strict=True
        ):
            if failure is not None:
                result.errors.append(f"{type(failure).__name__}: {failure}")
                result.usage.add(Usage(calls=1, seconds=seconds))
                continue

            result.usage.add(spent(self.model, reply, seconds))
            try:
                proposals, malformed = parse_edits(reply.text)
            except ValueError as exc:
                result.errors.append(f"unparseable reply: {exc}")
                continue

            last = first + len(batch) - 1
            for proposal in proposals:
                if len(batch) == 1:
                    proposal.line = first
                elif proposal.line is None or not first <= proposal.line <= last:
                    proposal.line = None
            result.proposed += len(proposals) + malformed
            edits, batch_rejected = _resolve(text, proposals, malformed, spans)
            result.edits.extend(edits)
            rejected.update(batch_rejected)

        result.rejected = dict(rejected)
        result.skipped = sum(rejected.values())
        return result

    def _correct_windowed(self, text, spans, size):
        """``size`` blocks per call, and the whole document behind each of them.

        The other split — `_correct_batched` — sends a call the blocks it owns
        and nothing else, and that is measured to cost 0.039 F0.5 and ten times
        the false positives on clean text (docs/PLAN.md, H5). The reason is in
        the prompt rather than in the arithmetic: a rule that says a strange
        word *coherente con el resto del texto* belongs to the author cannot be
        applied by a call that was never shown the rest of the text. So here
        the split is over *responsibility* and not over context. Every call
        reads the same document, numbered identically, and is told which blocks
        are its own; latency falls with the number of calls and the evidence
        each one judges on does not move.

        A window owns a contiguous range of characters, and the ranges
        partition the text. Ownership is settled on the resolved offset rather
        than on the ``line`` the model reported, because the number is a hint
        it may get wrong and the offset is a fact: an edit that lands outside
        its window is dropped as ``out_of_window``, which is what keeps two
        calls that both noticed the same error from applying it twice.

        ``context_blocks`` narrows what is shown from the whole document to
        that many blocks on either side. It exists because the context is
        re-sent per call and therefore is what a windowed pass pays for; a
        pass that does not need the far end of the document should not buy it.
        """
        result = Correction()
        rejected = Counter()
        jobs, users = [], []
        for window in _windows(text, spans, size, self.context_blocks):
            shown = render(text, window.shown, first=window.shown_first)
            brief = shown + FOCUS.format(first=window.first, last=window.last)
            for aspect in self.aspects or (None,):
                jobs.append(window)
                users.append(brief if aspect is None else brief + "\n\n" + ASPECTS[aspect])

        # Two aspects of one window can reach the same error from different
        # sides — a missing opening «¿» is ortotipografía and the capital that
        # follows it is not — so the same edit can arrive twice. Kept once:
        # `apply_edits` would drop the second as overlapping anyway, but it
        # would have been counted as a proposal and scored as a false positive.
        seen = set()
        for window, (reply, failure, seconds) in zip(jobs, self._fanout(users), strict=True):
            if failure is not None:
                result.errors.append(f"{type(failure).__name__}: {failure}")
                result.usage.add(Usage(calls=1, seconds=seconds))
                continue

            result.usage.add(spent(self.model, reply, seconds))
            try:
                proposals, malformed = parse_edits(reply.text)
            except ValueError as exc:
                result.errors.append(f"unparseable reply: {exc}")
                continue

            result.proposed += len(proposals) + malformed
            # Resolved against the document's own spans, which is the numbering
            # every call was shown: `shown_first` carries the true index of the
            # first block in the window, so a «line» means the same thing in
            # every call and in the text.
            edits, window_rejected = _resolve(text, proposals, malformed, spans)
            kept, duplicate = [], 0
            for edit in edits:
                if not window.start <= edit.start < window.end:
                    continue
                signature = (edit.start, edit.end, edit.replacement)
                if signature in seen:
                    duplicate += 1
                    continue
                seen.add(signature)
                kept.append(edit)
            rejected.update(window_rejected)
            outside = len(edits) - len(kept) - duplicate
            if outside:
                rejected["out_of_window"] += outside
            if duplicate:
                rejected["duplicate"] += duplicate
            result.edits.extend(kept)

        for reply, seconds in self._drain():
            result.usage.add(spent(self.model, reply, seconds))
        result.rejected = dict(rejected)
        result.skipped = sum(rejected.values())
        return result

    def _drain(self):
        """Attempts that came back after their race was already won.

        Read once and cleared: they are paid for and have to reach the cost
        column, but their edits are the same edits and must not be counted
        twice.
        """
        spare, self._spare = getattr(self, "_spare", []), []
        return spare

    def _fanout(self, users, system=None):
        """One reply per user message, in the order they were built.

        The calls are independent, so they overlap; what is done with the
        answers is not, and the accumulation above stays sequential and in
        order. A split pass appends edits and counts rejections as it goes, so
        replies arriving out of order would have two runs of one text writing
        two different `Correction`s, neither of them wrong. This is
        `evals/run.py:correct_all`'s argument applied one level down: there the
        unit is a document, here it is one call's share of it.

        The exception is carried back rather than raised: a failing call is
        recorded and skipped, and raising here would discard every other call's
        edits along with it.
        """

        def call(user):
            started = time.monotonic()
            try:
                reply = self._generate(self.model, system or self.prompt, user)
            except Exception as exc:
                return None, exc, time.monotonic() - started
            return reply, None, time.monotonic() - started

        if self.attempts > 1 or self.deadline:
            hurried = None
            if self.fallback is not None:

                def hurried(user):
                    started = time.monotonic()
                    try:
                        reply = self.fallback(self.model, system or self.prompt, user)
                    except Exception as exc:
                        return None, exc, time.monotonic() - started
                    return reply, None, time.monotonic() - started

            return self._race(call, users, hurried)
        if self.concurrency <= 1 or len(users) <= 1:
            return [call(user) for user in users]
        with ThreadPoolExecutor(max_workers=min(self.concurrency, len(users))) as pool:
            return list(pool.map(call, users))

    def _race(self, call, users, hurried=None):
        """Every call issued ``attempts`` times at once; first answer wins, clock runs.

        This is the shape the measurements above argue for and nothing else
        does. At `reasoning_effort=minimal` one 50-word call has a **median of
        4.3 s and a maximum of 19** — the model's deliberation is a lottery, and
        a pass that waits for every ticket waits for the worst one. Wall clock
        is therefore not the average call, it is the slowest of sixty-four, and
        no amount of tuning the average touches it.

        Two things follow, and they are the whole design:

        - **Redundancy beats patience.** The same call issued k times in
          parallel finishes when the *luckiest* of them does, and the provider
          sustains 67 concurrent calls, so the copies are free in wall clock.
        - **The deadline is a promise, not a timeout.** A pass that returns
          whatever it has at five seconds is bounded *by construction*, where
          one that usually takes four is bounded by nothing. What a block that
          misses the deadline costs is recall on that block alone — the rest of
          the document is unaffected, and the report says how many.

        Losing attempts are still paid for at the provider, so the ones that
        came back are counted: a cost column that only saw the winners would
        understate the bill by nearly the redundancy factor.
        """
        started = time.monotonic()
        width = min(self.concurrency, max(1, len(users) * self.attempts))
        pool = ThreadPoolExecutor(max_workers=width)
        try:
            # Submission order is the whole trick, and it is the opposite of
            # the order preference reads in. The hurried ticket for *every*
            # window goes in first, so the floor under the deadline is bought
            # before a single token of redundancy is; only then do the
            # deliberated attempts queue up behind them, filling whatever
            # capacity and time is left. Submitted the other way round — every
            # window's three deliberated attempts before any window's floor —
            # a long document spends its whole budget on the first third of
            # itself and the rest comes back empty. That is measured: on the
            # 2,563-word fragment it took recall from 0.914 to 0.569.
            #
            # The pool is bounded well below what a long document would ask
            # for. Firing 368 calls at a provider that runs 67 does not make
            # them 368 calls in flight, it makes all of them slower.
            tickets = {index: [] for index in range(len(users))}
            if hurried is not None:
                for index, user in enumerate(users):
                    tickets[index].append(pool.submit(hurried, user))
            for _ in range(self.attempts):
                for index, user in enumerate(users):
                    tickets[index].append(pool.submit(call, user))

            won, spare = {}, []
            while len(won) < len(users):
                for index, tries in tickets.items():
                    if index in won:
                        continue
                    finished = [f for f in tries if f.done()]
                    usable = [f for f in finished if f.result()[1] is None]
                    # `tries[0]` is the hurried ticket when there is one, so a
                    # deliberated answer is anything after it — preferred
                    # whenever one exists, and the hurried one read only as the
                    # floor it was submitted to be.
                    floor = 1 if hurried is not None else 0
                    thought = [f for f in usable if tries.index(f) >= floor]
                    if thought:
                        won[index] = thought[0].result()
                    elif usable and (self.deadline is None or _out_of_time(started, self.deadline)):
                        won[index] = usable[0].result()
                    elif finished and len(finished) == len(tries):
                        won[index] = finished[0].result()  # every attempt failed
                if len(won) == len(users):
                    break
                if self.deadline and time.monotonic() - started >= self.deadline:
                    break
                time.sleep(0.02)

            # The deadline has passed, or everything is in: any window still
            # holding only its hurried answer takes it now.
            for index, tries in tickets.items():
                if index in won:
                    continue
                usable = [f for f in tries if f.done() and f.result()[1] is None]
                if usable:
                    won[index] = usable[0].result()

            elapsed = time.monotonic() - started
            for index, tries in tickets.items():
                for future in tries:
                    if not future.done():
                        continue
                    reply, failure, seconds = future.result()
                    if failure is None and won.get(index, (None,))[0] is not reply:
                        spare.append((reply, seconds))
            self._spare = spare
            missed = RuntimeError(f"sin respuesta en {self.deadline} s")
            return [won.get(index, (None, missed, elapsed)) for index in range(len(users))]
        finally:
            # Not waited on: the point of the deadline is not to wait for the
            # attempt that blew it. What is already in flight finishes into a
            # result nobody reads, which is the price of the bound.
            pool.shutdown(wait=False, cancel_futures=True)


# What one windowed call is responsible for and what it is shown. `start`/`end`
# are the characters it owns, `first`/`last` the block numbers it is told to
# correct, and `shown`/`shown_first` the spans that go into its prompt — the
# whole document unless `context_blocks` narrows it.
Window = namedtuple("Window", "first last start end shown shown_first")


def _out_of_time(started, deadline):
    return time.monotonic() - started >= deadline


def _says_no(reply):
    """Whether the verifier discarded it. Anything unreadable is a vote to keep.

    Same direction as a failed call, and for the same reason: the edit already
    survived the pass that proposed it, so silence must not be able to delete
    it. Only an explicit «descartar» does.
    """
    head = reply.strip().lstrip("«\"'*").upper()
    return head.startswith("DESCARTAR")


def _around(spans, edit, context):
    """The blocks to show a verifier, and the number the first of them carries."""
    index = _block_of(spans, edit) - 1
    if context is None:
        return spans, 1
    low = max(0, index - context)
    return spans[low : min(len(spans), index + context + 1)], low + 1


def _block_of(spans, edit):
    """The 1-based block the edit falls in; the last one if it sits past the end."""
    for number, (start, end) in enumerate(spans, 1):
        if start <= edit.start < end:
            return number
    return next(
        (number for number, (start, _) in enumerate(spans, 1) if edit.start < start), len(spans)
    )


def _windows(text, spans, size, context=None):
    """Cut the blocks into windows whose owned character ranges partition the text.

    A window starts where its first block starts and ends where the next
    window's first block starts, so the whitespace between two blocks — and
    anything before the first block or after the last — belongs to exactly one
    window rather than to none. An edit can therefore be attributed by offset
    alone, which is what lets two calls that both spotted the same error be
    reconciled without asking either of them where it was.
    """
    bounds = [(offset + 1, min(offset + size, len(spans))) for offset in range(0, len(spans), size)]
    windows = []
    for index, (first, last) in enumerate(bounds):
        start = spans[first - 1][0] if index else 0
        end = spans[bounds[index + 1][0] - 1][0] if index + 1 < len(bounds) else len(text)
        if context is None:
            shown, shown_first = spans, 1
        else:
            low = max(0, first - 1 - context)
            shown, shown_first = spans[low : min(len(spans), last + context)], low + 1
        windows.append(Window(first, last, start, end, shown, shown_first))
    return windows


def _resolve(text, proposals, malformed, spans=None):
    """Anchors to offsets. Every proposal that does not survive is counted."""
    rejected = Counter()
    if malformed:
        rejected["malformed"] = malformed

    edits, discarded = resolve_edits(text, proposals, spans)
    rejected.update(rejection.reason for rejection in discarded)

    kept = []
    for edit in edits:
        shrunk = trim(text, edit)
        # An anchor that resolves but changes nothing: the model quoted the
        # text back at itself. Not an edit, and it would score as a false
        # positive on clean text.
        if shrunk.start == shrunk.end and not shrunk.replacement:
            rejected["no_change"] += 1
            continue
        kept.append(shrunk)
    return kept, dict(rejected)


def _json_body(raw):
    """The JSON inside whatever the model wrapped it in."""
    text = raw.strip()
    fenced = FENCE.search(text)
    if fenced:
        text = fenced.group("body").strip()
    if text.startswith(("{", "[")):
        return text
    opening = min((i for i in (text.find("{"), text.find("[")) if i != -1), default=-1)
    closing = max(text.rfind("}"), text.rfind("]"))
    if opening == -1 or closing <= opening:
        raise ValueError(f"no JSON in reply: {text[:120]!r}")
    return text[opening : closing + 1]
