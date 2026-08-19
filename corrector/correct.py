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
from collections import Counter

from pydantic import BaseModel, ValidationError

from corrector.blocks import DEFAULT_BLOCK_WORDS, block_spans
from corrector.edits import Edit, ProposedEdit, resolve_edits, trim
from corrector.llm import Usage, spent
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
    ):
        # `block_words=None` is one block per line, which is what H1 measured and
        # what the frozen reference rows in the harness ask for by name.
        self.model = model
        self.prompt = (prompt or PROMPT).format(kinds=kinds_block())
        self.block_words = block_words
        self.blocks_per_call = blocks_per_call
        self._generate = generate

    def correct(self, text):
        # Rendered and resolved from the same spans, so what the model was
        # numbered and what an anchor is searched inside cannot drift apart.
        spans = block_spans(text, self.block_words)
        if self.blocks_per_call:
            return self._correct_batched(text, spans, self.blocks_per_call)
        return self._correct_whole(text, spans)

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
        for offset in range(0, len(spans), size):
            batch = spans[offset : offset + size]
            first = offset + 1
            started = time.monotonic()
            try:
                reply = self._generate(self.model, self.prompt, render(text, batch, first=first))
            except Exception as exc:
                result.errors.append(f"{type(exc).__name__}: {exc}")
                result.usage.add(Usage(calls=1, seconds=time.monotonic() - started))
                continue

            result.usage.add(spent(self.model, reply, time.monotonic() - started))
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
