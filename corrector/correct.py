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


def render(text):
    """Number the lines, marker on its own line.

    Off to the side rather than inline because half of what this pass corrects
    is orthotypography, and a dialogue dash judged as «12| —Vamos» is a dash
    the model has been shown in a context the author never wrote.
    """
    lines = text.split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n\n".join(f"[{n}]\n{line}" for n, line in enumerate(lines, 1))


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
    """One pass over a text. The unit H2 will wrap in a verifier."""

    def __init__(self, model, generate, prompt=None):
        self.model = model
        self.prompt = (prompt or PROMPT).format(kinds=kinds_block())
        self._generate = generate

    def correct(self, text):
        result = Correction()
        started = time.monotonic()
        try:
            reply = self._generate(self.model, self.prompt, render(text))
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
        result.edits, result.rejected = _resolve(text, proposals, malformed)
        result.skipped = sum(result.rejected.values())
        return result


def _resolve(text, proposals, malformed):
    """Anchors to offsets. Every proposal that does not survive is counted."""
    rejected = Counter()
    if malformed:
        rejected["malformed"] = malformed

    edits, discarded = resolve_edits(text, proposals)
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
