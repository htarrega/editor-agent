"""The systems under test: the fixed baselines every change is measured against.

A system takes text and returns typed edits plus what it cost. That is the only
contract; the harness does not care how the edits were produced.
"""

import os
import re
import time

import httpx
from pydantic import BaseModel

from corrector import settings
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
from corrector.rules import mechanical_edits

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


class RulesSystem:
    """The orthotypographic rule pack on its own. No model, no calls, no cost.

    It exists to be subtracted. `corrector-fast` is a rule pass and a model
    pass in one row, and without this one there is no way to read which of them
    a gain or a regression came from — least of all on the four types where
    both of them have something to say.
    """

    name = "rules-only"
    concurrency = 1

    def correct(self, text):
        return Output(edits=mechanical_edits(text))


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
    # The endpoint allows 20 requests a minute and this system already paces
    # itself against that between chunks. Overlapping its calls would spend the
    # allowance faster, not finish sooner.
    concurrency = 1

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
        os.environ.get("EVAL_DEEPSEEK_MODEL", settings.MODEL),
        bounded_deepseek(os.environ.get("EVAL_DEEPSEEK_EFFORT", settings.EFFORT)),
    ),
    "naive-claude": lambda: NaivePromptSystem(
        "naive-claude", os.environ.get("EVAL_CLAUDE_MODEL", "claude-sonnet-5"), claude_generate
    ),
    # The row H1 closed on, kept exactly as it was measured. It is no longer
    # the system under development — `corrector-blocks` is — but its numbers are
    # quoted throughout docs/PLAN.md and cached in past reports, so nothing here
    # may change or those rows stop meaning what they say.
    "corrector-v0": lambda: CorrectorSystem(
        "corrector-v0",
        Corrector(
            os.environ.get("EVAL_DEEPSEEK_MODEL", settings.MODEL),
            bounded_deepseek(os.environ.get("EVAL_DEEPSEEK_EFFORT", settings.EFFORT)),
            # One block per line, spelled out rather than inherited: the pipeline
            # now defaults to 50 words, and this row has to keep meaning what H1
            # measured even as that default moves.
            block_words=None,
        ),
    ),
    # The corrector under development, and the default. `corrector-v0` with the
    # paragraphs re-cut into blocks the size of the ones the model already
    # handles well: same model, same effort, same prompt, only a different
    # numbering of the same characters. Over `--repeats 3` it takes F0.5 from
    # 0.926 to 0.948 and recall from 0.820 to 0.875, and it earns the default by
    # raising the floor rather than the ceiling — on the fragment with 245-word
    # paragraphs the worst draw goes from 0.455 to 0.705 (docs/PLAN.md, H5).
    "corrector-blocks": lambda: CorrectorSystem(
        "corrector-blocks",
        Corrector(
            os.environ.get("EVAL_DEEPSEEK_MODEL", settings.MODEL),
            bounded_deepseek(os.environ.get("EVAL_DEEPSEEK_EFFORT", settings.EFFORT)),
            block_words=int(os.environ.get("EVAL_BLOCK_WORDS", settings.BLOCK_WORDS)),
        ),
    ),
    # The same pass again, cut across calls instead of sent in one. `block_words`
    # decides what the model reasons over; `blocks_per_call` decides how much of
    # it travels per request, and only the first has ever been measured. Both
    # rows exist to settle that second axis: batches amortise the system prompt
    # over few calls, one-per-block pays it 56 times on `carta` but loses at most
    # one block to a bad reply instead of the whole fragment.
    "corrector-batched": lambda: CorrectorSystem(
        "corrector-batched",
        Corrector(
            os.environ.get("EVAL_DEEPSEEK_MODEL", settings.MODEL),
            bounded_deepseek(os.environ.get("EVAL_DEEPSEEK_EFFORT", settings.EFFORT)),
            block_words=int(os.environ.get("EVAL_BLOCK_WORDS", settings.BLOCK_WORDS)),
            blocks_per_call=int(os.environ.get("EVAL_BLOCKS_PER_CALL", "10")),
            concurrency=int(os.environ.get("EVAL_BLOCKS_CONCURRENCY", "1")),
        ),
    ),
    "corrector-per-block": lambda: CorrectorSystem(
        "corrector-per-block",
        Corrector(
            os.environ.get("EVAL_DEEPSEEK_MODEL", settings.MODEL),
            bounded_deepseek(os.environ.get("EVAL_DEEPSEEK_EFFORT", settings.EFFORT)),
            block_words=int(os.environ.get("EVAL_BLOCK_WORDS", settings.BLOCK_WORDS)),
            blocks_per_call=1,
            concurrency=int(os.environ.get("EVAL_BLOCKS_CONCURRENCY", "1")),
        ),
    ),
    # The same pass on the strong model. Not a baseline and not the target
    # either: it separates what the pipeline contributes from what the model
    # does, by putting our prompt and naive-claude's model in the same row.
    # Pinned to line numbering for the same reason as `corrector-v0`: it is the
    # other half of H1's prompt-against-model square, and that square was
    # measured before the blocks existed. A chunked strong model is a row nobody
    # has paid for yet, and it would need a name of its own.
    # Our prompt and protocol on a model that does not reason. `corrector-claude`
    # and `corrector-blocks` are both deliberating models; this is the cell that
    # says whether the ~90% of output tokens spent on deliberation is the task
    # asking for it or the model choosing to.
    "corrector-haiku": lambda: CorrectorSystem(
        "corrector-haiku",
        Corrector(
            os.environ.get("EVAL_HAIKU_MODEL", "claude-haiku-4-5"),
            claude_generate,
            block_words=int(os.environ.get("EVAL_BLOCK_WORDS", settings.BLOCK_WORDS)),
        ),
    ),
    # The latency row. Three changes from `corrector-blocks`, and each one is
    # the answer to a measurement in docs/PLAN.md rather than a knob turned
    # hopefully:
    #
    #  · `window_blocks` splits the calls over *responsibility* while every one
    #    of them still reads the document — which is what `corrector-batched`
    #    gave up, and what its 0.039 F0.5 bought back.
    #  · `reasoning_effort=none` is the only setting that puts a call under
    #    five seconds; at `minimal` the median call is 8.4 s and the slowest 38.
    #  · `mechanical` is what pays for the deliberation being gone. The four
    #    orthotypographic types are decidable, the model is measurably bad at
    #    them with or without deliberation, and the rule pack scores 150 of the
    #    corpus's 495 seeded errors at P 0.974 in microseconds.
    "corrector-fast": lambda: CorrectorSystem(
        "corrector-fast",
        Corrector(
            os.environ.get("EVAL_DEEPSEEK_MODEL", settings.MODEL),
            bounded_deepseek(os.environ.get("EVAL_FAST_EFFORT", "none")),
            block_words=int(os.environ.get("EVAL_BLOCK_WORDS", settings.BLOCK_WORDS)),
            window_blocks=int(os.environ.get("EVAL_WINDOW_BLOCKS", "2")),
            context_blocks=_optional_int(os.environ.get("EVAL_WINDOW_CONTEXT", "12")),
            concurrency=int(os.environ.get("EVAL_WINDOW_CONCURRENCY", "40")),
            # The model is asked only for what no rule decides. It has one
            # reading to spend and the rule pack already owns five of the
            # seventeen types outright.
            aspects=_comma_or_none(os.environ.get("EVAL_FAST_ASPECTS", "juicio")),
            mechanical=True,
        ),
    ),
    # The rule pack with no model behind it at all. Not a candidate — it can
    # only ever see four error types — but the row that says how much of
    # `corrector-fast` is the rules and how much is the calls, which is not
    # readable from the two systems' headline numbers.
    "rules-only": RulesSystem,
    "corrector-claude": lambda: CorrectorSystem(
        "corrector-claude",
        Corrector(
            os.environ.get("EVAL_CLAUDE_MODEL", "claude-sonnet-5"),
            claude_generate,
            block_words=None,
        ),
    ),
}

# The plan asks for two baselines: LanguageTool and a naive prompt to a strong
# model. `naive-deepseek` and `corrector-claude` stay registered but out of the
# default set: they are the two off-diagonal cells of the prompt × model square,
# run when the question is which of the two is doing the work, not what the
# pipeline currently scores.
DEFAULT_SYSTEMS = ["null", "languagetool", "naive-claude", "corrector-blocks"]


def _comma_or_none(value):
    return [item.strip() for item in value.split(",") if item.strip()] or None


def _optional_int(value):
    """`none` is a value here: it is what asks a window for the whole document."""
    return None if not value or value.lower() in {"none", "all"} else int(value)


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
