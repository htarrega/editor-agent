"""The systems under test: the fixed baselines every change is measured against.

A system takes text and returns typed edits plus what it cost. That is the only
contract; the harness does not care how the edits were produced.
"""

import os
import re
import time

import httpx
from pydantic import BaseModel

from corrector.correct import Corrector
from corrector.edits import Edit, diff_edits, trim
from corrector.llm import (
    MAX_OUTPUT_TOKENS,
    PRICING,
    Usage,
    bounded_deepseek,
    claude_generate,
    price,
    spent,
)

# Re-exported: the harness talks about cost and usage in its own report, and
# the modules around it read these off `systems`.
__all__ = ["MAX_OUTPUT_TOKENS", "PRICING", "Usage", "price"]

# The line this prompt must not cross: it may state the output contract, never
# the correction policy. "Return only the corrected text" is what any writer
# adds after reading one reply. "Minimal edits", "keep the author's voice" or
# "only spelling and grammar" would hand the baseline our own thesis, and we
# would end up measuring the pipeline against the pipeline minus the code.
NAIVE_PROMPT = (
    "Corrige este texto. Devuelve únicamente el texto corregido, "
    "con los mismos saltos de línea y sin comentarios ni explicaciones.\n\n"
)

FENCE = re.compile(r"\A\s*```[^\n]*\n(?P<body>.*)\n```\s*\Z", re.DOTALL)


class Output(BaseModel):
    """What the harness asks of every system.

    ``skipped`` is anything the system wanted to say but could not turn into an
    applicable edit; ``rejected`` breaks that number down by reason, for the
    systems that know why.
    """

    edits: list[Edit] = []
    usage: Usage = Usage()
    skipped: int = 0
    rejected: dict[str, int] = {}
    errors: list[str] = []


class NullSystem:
    """Corrects nothing. The floor: zero recall, zero false positives."""

    name = "null"

    def correct(self, text):
        return Output()


# --- baseline: LanguageTool -------------------------------------------------

LT_CHUNK_CHARS = 15000  # the public endpoint's per-request limit is 20000
LT_PAUSE = 3.5  # its rate limit is 20 requests per minute

# Ordered: the first substring hit wins.
LT_KINDS = [
    ("DEQUEISMO", "dequeismo"),
    ("QUEISMO", "queismo"),
    ("LAISMO", "laismo"),
    ("LOISMO", "loismo"),
    # Leísmo is a different error, is not in the taxonomy, and is the most
    # common of the three: bucket it as "otro" rather than inflate loismo.
    ("LEISMO", "otro"),
    ("DIACRIT", "tilde_diacritica"),
    ("TILDE", "tilde"),
    ("ACENTU", "tilde"),
    ("CONCORDANCIA", "concordancia_genero"),
    ("AGREEMENT", "concordancia_genero"),
    ("MAYUSCUL", "mayuscula"),
    ("UPPERCASE", "mayuscula"),
    ("COMILLAS", "comillas"),
    ("QUOTE", "comillas"),
    ("INTERROGACION", "signo_apertura"),
    ("EXCLAMACION", "signo_apertura"),
    ("GUION", "raya_dialogo"),
    ("DASH", "raya_dialogo"),
    ("WHITESPACE", "espaciado"),
    ("SPACE", "espaciado"),
]

LT_ISSUE_TYPES = {
    "misspelling": "otro",
    "typographical": "espaciado",
    "whitespace": "espaciado",
    "grammar": "otro",
}


class LanguageToolSystem:
    """Rule-based baseline. Not an LLM, and that is the point."""

    name = "languagetool"

    def __init__(self, endpoint=None):
        self.endpoint = endpoint or os.environ.get(
            "LANGUAGETOOL_URL", "https://api.languagetool.org"
        )

    def correct(self, text):
        out = Output()
        for offset, chunk in _chunks(text, LT_CHUNK_CHARS):
            if offset:
                time.sleep(LT_PAUSE)  # between chunks only, never after the last
            try:
                matches, seconds, calls = self._check(chunk)
            except Exception as exc:
                out.errors.append(f"{type(exc).__name__}: {exc}")
                out.usage.calls += 1
                continue
            out.usage.calls += calls
            # Request time only: the rate-limit backoff below is not latency
            # the system would pay against a self-hosted endpoint.
            out.usage.seconds += seconds

            for match in matches:
                replacements = match.get("replacements") or []
                if not replacements:
                    out.skipped += 1
                    continue
                start = offset + match["offset"]
                edit = Edit(
                    start=start,
                    end=start + match["length"],
                    replacement=replacements[0]["value"],
                    kind=_lt_kind(match),
                    rule=match.get("rule", {}).get("id", ""),
                )
                out.edits.append(trim(text, edit))
            time.sleep(LT_PAUSE)
        return out

    def _check(self, chunk):
        """Returns the matches, the time spent in requests, and how many ran."""
        seconds = 0.0
        for attempt in range(4):
            started = time.monotonic()
            response = httpx.post(
                f"{self.endpoint}/v2/check",
                data={"text": chunk, "language": "es", "level": "default"},
                timeout=90,
            )
            seconds += time.monotonic() - started
            if response.status_code == 429:
                time.sleep(10 * (attempt + 1))
                continue
            response.raise_for_status()
            return response.json().get("matches", []), seconds, attempt + 1
        raise RuntimeError("languagetool rate limit not cleared after 4 attempts")


def _lt_kind(match):
    rule = match.get("rule", {})
    haystack = f"{rule.get('id', '')} {rule.get('category', {}).get('id', '')}".upper()
    for needle, kind in LT_KINDS:
        if needle in haystack:
            return kind
    return LT_ISSUE_TYPES.get(rule.get("issueType", ""), "otro")


# --- baseline: naive prompt to a strong model -------------------------------


class NaivePromptSystem:
    """One prompt, no pipeline. Deliberately unengineered but not crippled.

    The model returns a whole rewritten text, so the edits are recovered by
    diffing. That is exactly the fluency-edit behaviour we want to beat.
    """

    def __init__(self, name, model, generate, prompt=NAIVE_PROMPT):
        self.name = name
        self.model = model
        self.prompt = prompt
        self._generate = generate

    def correct(self, text):
        out = Output()
        started = time.monotonic()
        try:
            # No system prompt: the request that produced the cached baseline
            # numbers had none, and those numbers are still in the table.
            reply = self._generate(self.model, "", self.prompt + text)
        except Exception as exc:
            out.errors.append(f"{type(exc).__name__}: {exc}")
            out.usage.calls += 1
            out.usage.seconds += time.monotonic() - started
            return out

        out.usage = spent(self.model, reply, time.monotonic() - started)
        out.edits = diff_edits(text, _unfence(reply.text))
        return out


def _unfence(text):
    """Undo markdown fencing. Without it the diff measures formatting, not language."""
    match = FENCE.match(text)
    return match.group("body") if match else text.strip("\n")


# --- the pipeline under development -----------------------------------------


class CorrectorSystem:
    """Puts ``corrector.correct.Corrector`` on the harness's table.

    A thin adapter and not a shared base class: the pipeline is the product and
    must not import the thing that measures it, so the two result types stay
    separate and this is where they meet.
    """

    def __init__(self, name, corrector):
        self.name = name
        self.corrector = corrector

    @property
    def model(self):
        return self.corrector.model

    @property
    def prompt(self):
        return self.corrector.prompt

    @property
    def block_words(self):
        return self.corrector.block_words

    def correct(self, text):
        result = self.corrector.correct(text)
        return Output(
            edits=result.edits,
            usage=result.usage,
            skipped=result.skipped,
            rejected=result.rejected,
            errors=result.errors,
        )


# --- registry ---------------------------------------------------------------

BUILDERS = {
    "null": NullSystem,
    "languagetool": LanguageToolSystem,
    # Bounded like the corrector, and for the same reason: unbounded it does
    # not finish a literary fragment at all (H0), so there is no baseline to
    # read. The cap is part of what this row means. Holding the model and the
    # effort fixed and changing only the prompt is what separates what our
    # prompt contributes from what the model does — the other diagonal of the
    # same square as `corrector-claude`.
    "naive-deepseek": lambda: NaivePromptSystem(
        "naive-deepseek",
        os.environ.get("EVAL_DEEPSEEK_MODEL", "deepseek-v4-flash"),
        bounded_deepseek(os.environ.get("EVAL_DEEPSEEK_EFFORT", "minimal")),
    ),
    "naive-claude": lambda: NaivePromptSystem(
        "naive-claude", os.environ.get("EVAL_CLAUDE_MODEL", "claude-sonnet-5"), claude_generate
    ),
    "corrector-v0": lambda: CorrectorSystem(
        "corrector-v0",
        Corrector(
            os.environ.get("EVAL_DEEPSEEK_MODEL", "deepseek-v4-flash"),
            bounded_deepseek(os.environ.get("EVAL_DEEPSEEK_EFFORT", "minimal")),
        ),
    ),
    # `corrector-v0` with the paragraphs re-cut into blocks the size of the
    # ones the model already handles well. H1 found its whole recall deficit in
    # the single fragment averaging 220 words per paragraph (0.636 against
    # 0.926 on the rest), so this row holds the model, the effort and the
    # prompt fixed and changes only how the same characters are numbered.
    "corrector-blocks": lambda: CorrectorSystem(
        "corrector-blocks",
        Corrector(
            os.environ.get("EVAL_DEEPSEEK_MODEL", "deepseek-v4-flash"),
            bounded_deepseek(os.environ.get("EVAL_DEEPSEEK_EFFORT", "minimal")),
            block_words=int(os.environ.get("EVAL_BLOCK_WORDS", "50")),
        ),
    ),
    # The same pass on the strong model. Not a baseline and not the target
    # either: it separates what the pipeline contributes from what the model
    # does, by putting our prompt and naive-claude's model in the same row.
    "corrector-claude": lambda: CorrectorSystem(
        "corrector-claude",
        Corrector(os.environ.get("EVAL_CLAUDE_MODEL", "claude-sonnet-5"), claude_generate),
    ),
}

# The plan asks for two baselines: LanguageTool and a naive prompt to a strong
# model. `naive-deepseek` and `corrector-claude` stay registered but out of the
# default set: they are the two off-diagonal cells of the prompt × model square,
# run when the question is which of the two is doing the work, not what the
# pipeline currently scores.
DEFAULT_SYSTEMS = ["null", "languagetool", "naive-claude", "corrector-v0"]


def build(names):
    unknown = [name for name in names if name not in BUILDERS]
    if unknown:
        raise KeyError(f"unknown systems {unknown}; available: {sorted(BUILDERS)}")

    built = [BUILDERS[name]() for name in names]
    # Fail now rather than after a run whose cost column reads $0.00.
    for system in built:
        model = getattr(system, "model", "")
        if model and model not in PRICING:
            raise KeyError(f"no price for model {model!r}; add it to PRICING")
    return built


def _chunks(text, limit):
    """Split on blank lines so no request exceeds the endpoint's size limit."""
    out, start = [], 0
    while start < len(text):
        end = min(start + limit, len(text))
        if end < len(text):
            split = text.rfind("\n\n", start, end)
            if split > start:
                end = split
        out.append((start, text[start:end]))
        start = end
    return out
