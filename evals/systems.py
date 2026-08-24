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
    bounded_gemini,
    claude_generate,
    deepseek_generate,
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
    # Cost candidate, refuted: `corrector-blocks`'s shape with the brief
    # narrowed to `juicio`, on the theory that less to search for is less to
    # deliberate about. Measured `--repeats 3` 2026-08-24 against `blocks`:
    # 9% cheaper, real recall lost, reasoning tokens barely moved. Registered
    # so the refutation reproduces — see `corrector/presets.py:lean`.
    "corrector-lean": lambda: CorrectorSystem(
        "corrector-lean",
        Corrector(
            os.environ.get("EVAL_DEEPSEEK_MODEL", settings.MODEL),
            bounded_deepseek(os.environ.get("EVAL_DEEPSEEK_EFFORT", settings.EFFORT)),
            block_words=int(os.environ.get("EVAL_BLOCK_WORDS", settings.BLOCK_WORDS)),
            aspects=_comma_or_none(os.environ.get("EVAL_LEAN_ASPECTS", "juicio")),
            mechanical=True,
        ),
    ),
    # Cost candidate, promising but one draw: `corrector-blocks`'s shape,
    # rule pack run *before* the call instead of after — the model reads a
    # text with nowhere left that looks like the four orthotypographic error
    # types, rather than being asked to ignore them. Unlike `corrector-lean`,
    # this does not narrow what the model is asked; it narrows what there is
    # to look at, and unlike `corrector-lean` it worked: `--repeats 3`
    # 2026-08-24, F0.5 0.952 against `blocks`' 0.963 (inside the spread) at
    # 15% less cost, recall up rather than down. Not shipped: the DeepSeek key
    # ran out of balance before a second draw could confirm it — see
    # `corrector/presets.py:swept`.
    "corrector-swept": lambda: CorrectorSystem(
        "corrector-swept",
        Corrector(
            os.environ.get("EVAL_DEEPSEEK_MODEL", settings.MODEL),
            bounded_deepseek(os.environ.get("EVAL_DEEPSEEK_EFFORT", settings.EFFORT)),
            block_words=int(os.environ.get("EVAL_BLOCK_WORDS", settings.BLOCK_WORDS)),
            precorrect=True,
        ),
    ),
    # The row that met the goal. `swept` with deliberation off, over the
    # whole document — the shape docs/PLAN.md's "Settled" section already
    # measured as worse at `reasoning_effort=none` (whole document beside a
    # window: P 0.756 against 0.935). That measurement was on text the rules
    # had not cleared; on `swept`'s cleaned text it is a different question,
    # and this is the answer: three independent `--repeats 3` draws
    # (different seeds), pooled, F0.5 0.902 at $0.0083/10k words — 9.97x
    # `raced`'s $0.0824, at higher quality than `raced` itself (0.860). See
    # `corrector/presets.py:bare` for the individual draws and what shipping
    # this required correcting first (`corrector-swift`, refuted, below).
    "corrector-bare": lambda: CorrectorSystem(
        "corrector-bare",
        Corrector(
            os.environ.get("EVAL_DEEPSEEK_MODEL", settings.MODEL),
            bounded_deepseek(os.environ.get("EVAL_BARE_EFFORT", "none")),
            block_words=int(os.environ.get("EVAL_BLOCK_WORDS", settings.BLOCK_WORDS)),
            precorrect=True,
        ),
    ),
    # The latency row, not the cost one — re-measured 2026-08-24 alongside
    # `corrector-swift`: F0.5 0.870 at $0.0888/10k words, *more* than
    # `corrector-blocks`' $0.0395-0.0415. `window_blocks` buys the clock by
    # splitting calls over *responsibility* while every one of them still
    # reads the document (unlike `corrector-batched`, which gave that up for
    # 0.039 F0.5) — but re-sending that context on 549 calls instead of 16
    # costs more in input tokens than the tiny no-reasoning output saves.
    # `reasoning_effort=none` is still the only setting that puts a call
    # under five seconds, and `mechanical` still pays back some of what
    # turning deliberation off costs in recall. Cheap in wall clock, not in
    # dollars — see `corrector/presets.py:swift` for the full accounting.
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
    # Measured 2026-08-24 and refuted, on the axis that was never actually in
    # doubt — see `corrector/presets.py:swift` for the full account. F0.5
    # 0.879, close to `corrector-fast`'s 0.870 (deliberation off still costs
    # recall, pre-applied rules or not), at $0.0881/10k words — *more* than
    # `corrector-blocks`, not the ~11x less this row's docstring predicted
    # from `swept`'s output-token share alone. The gap was on the input
    # side: 549 calls each re-sending `context_blocks=12` costs more than
    # `blocks`' 16 calls save by not deliberating on the mechanically-easy
    # spans. Kept registered as the second corrected mistake in this file,
    # after `corrector-bare`.
    "corrector-swift": lambda: CorrectorSystem(
        "corrector-swift",
        Corrector(
            os.environ.get("EVAL_DEEPSEEK_MODEL", settings.MODEL),
            bounded_deepseek(os.environ.get("EVAL_SWIFT_EFFORT", "none")),
            block_words=int(os.environ.get("EVAL_BLOCK_WORDS", settings.BLOCK_WORDS)),
            window_blocks=int(os.environ.get("EVAL_WINDOW_BLOCKS", "2")),
            context_blocks=_optional_int(os.environ.get("EVAL_WINDOW_CONTEXT", "12")),
            concurrency=int(os.environ.get("EVAL_WINDOW_CONCURRENCY", "40")),
            aspects=_comma_or_none(os.environ.get("EVAL_SWIFT_ASPECTS", "juicio")),
            precorrect=True,
        ),
    ),
    # Tried with a paid key 2026-08-24 and refuted as a candidate: F0.5 0.934
    # against `corrector-blocks`' 0.963, at over double the cost ($0.1066
    # against $0.0415 per 10k words) — Gemini's per-token rate runs roughly 8x
    # DeepSeek's on output, and needing fewer reasoning tokens per call was not
    # enough to close that gap. The free-tier draws this row was registered
    # for (one fragment, one call, F0.5 0.994) were a single small-sample draw
    # and did not hold at `--repeats 3` on the full corpus. It is *not*
    # windowed: Gemini loses recall when the calls are split (0.971 to 0.657
    # on `sidra`), which is the opposite of what Sonnet did. See docs/PLAN.md.
    "corrector-gemini": lambda: CorrectorSystem(
        "corrector-gemini",
        Corrector(
            os.environ.get("EVAL_GEMINI_MODEL", "gemini-2.5-flash"),
            bounded_gemini(_optional_int(os.environ.get("EVAL_GEMINI_THINKING", "none"))),
            block_words=int(os.environ.get("EVAL_BLOCK_WORDS", settings.BLOCK_WORDS)),
            mechanical=True,
        ),
    ),
    # Bought the clock with redundancy and kept the deliberation, which is the
    # only thing that ever bought recall — the row that met the latency goal,
    # and no longer what ships (`corrector/settings.py`, `corrector-blocks`):
    # re-measured 2026-08-24, uncontended, its own deadline cost it recall the
    # day the provider ran slower than the day it was tuned. See docs/PLAN.md,
    # "The deadline was a bet".
    #
    # One block per call at `reasoning_effort=minimal` scores well on its own
    # but takes up to 19 s, all of it the slowest of sixty-four calls. So each
    # call is issued three times at once and the first answer wins, under a
    # hard 4.3 s deadline; a fast no-reasoning ticket goes in first for every
    # block so that nothing can come back empty.
    "corrector-raced": lambda: CorrectorSystem(
        "corrector-raced",
        Corrector(
            os.environ.get("EVAL_DEEPSEEK_MODEL", settings.MODEL),
            bounded_deepseek(os.environ.get("EVAL_RACE_EFFORT", settings.EFFORT)),
            block_words=int(os.environ.get("EVAL_BLOCK_WORDS", settings.BLOCK_WORDS)),
            window_blocks=1,
            context_blocks=_optional_int(os.environ.get("EVAL_WINDOW_CONTEXT", "12")),
            # Bounded well below what a long document would ask for: firing 368
            # calls at a provider that runs 67 does not make them 368 in flight.
            concurrency=int(os.environ.get("EVAL_RACE_CONCURRENCY", "96")),
            attempts=int(os.environ.get("EVAL_RACE_ATTEMPTS", "3")),
            deadline=float(os.environ.get("EVAL_RACE_DEADLINE", "4.3")),
            fallback=_hurried,
            # The knob that answers whether the rule pack earns its place in
            # the row that ships, rather than only in the one that does not
            # deliberate. `EVAL_RACE_RULES=0` is the control.
            mechanical=os.environ.get("EVAL_RACE_RULES", "1") != "0",
        ),
    ),
    # `corrector-fast` with the second wave switched on. Registered so the
    # refutation is reproducible, not because it is a candidate: it buys
    # precision and pays more recall for it, and costs 2.5 s doing so.
    "corrector-verified": lambda: CorrectorSystem(
        "corrector-verified",
        Corrector(
            os.environ.get("EVAL_DEEPSEEK_MODEL", settings.MODEL),
            bounded_deepseek(os.environ.get("EVAL_FAST_EFFORT", "none")),
            block_words=int(os.environ.get("EVAL_BLOCK_WORDS", settings.BLOCK_WORDS)),
            window_blocks=int(os.environ.get("EVAL_WINDOW_BLOCKS", "4")),
            context_blocks=_optional_int(os.environ.get("EVAL_WINDOW_CONTEXT", "12")),
            concurrency=int(os.environ.get("EVAL_WINDOW_CONCURRENCY", "40")),
            mechanical=True,
            verify=True,
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
#
# `corrector-blocks` is the reference row — the shape every other row here is
# measured against, and no longer what ships. `corrector-bare` is what ships
# (`corrector/settings.py:SYSTEM` — hardcoded, not an environment variable,
# since 2026-08-24): both stay in the default set for the same reason
# `corrector-raced` did while it shipped and
# `corrector-blocks` was still only the reference — leaving the shipped one
# out is how a deployment quietly stops being measured. `raced` itself
# dropped out the way `fast` and `verified` already were: still registered,
# still buildable by name, but not what a plain run scores, because it is
# not what a plain deployment runs. Its own row is worth repeating on request
# more than most — it issues each call three times and keeps whichever
# answers first, so which edits survive is not fixed from run to run, and its
# quality reading depends on how fast the provider was that hour
# (docs/PLAN.md, "The deadline was a bet") in a way none of the other rows do.
# `corrector-bare` inherits a milder version of the same caution: one draw in
# three saw 2 of 16 calls return unparseable JSON, so its own number moves
# more between runs than `corrector-blocks`' does.
DEFAULT_SYSTEMS = [
    "null",
    "languagetool",
    "naive-claude",
    "corrector-blocks",
    "corrector-bare",
]


def _hurried(model, system, user):
    """The floor under the deadline: the same call with the deliberation off.

    Not a competitor to the deliberated attempts and never preferred to one —
    it exists so that a block whose three deliberated tickets all ran long
    still comes back with something rather than nothing.
    """
    return deepseek_generate(model, system, user, reasoning_effort="none")


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
