"""Talking to the providers, and what each call cost.

It lives in ``corrector/`` and not in ``evals/`` because the pipeline is the
thing that makes the calls; the harness only measures them. Every client
returns the same ``Reply``, so a pass does not know which provider answered it.
"""

import os
import threading
import time
from datetime import datetime, timezone

from pydantic import BaseModel

# USD per million tokens. `deepseek-v4-flash`'s pair is the off-peak rate —
# see `_deepseek_v4_flash_rate` for the peak surcharge `price()` applies.
PRICING = {
    "deepseek-v4-flash": (0.22, 0.66),
    "deepseek-reasoner": (0.55, 2.19),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-haiku-4-5": (1.00, 5.00),
    # Google's published rate for 2.5 Flash at the time of measuring. Worth
    # re-checking before a cost claim leans on it: unlike the two above, no run
    # in this repository has ever been reconciled against a provider invoice.
    "gemini-2.5-flash": (0.30, 2.50),
}

# DeepSeek moved `deepseek-v4-flash` off its flat rate to peak/off-peak
# billing on 2026-08-16 — peak is UTC 01:00–04:00 and 06:00–10:00, at exactly
# double PRICING's off-peak pair. Every cost claim in docs/PLAN.md computed
# before that date, and any run of this harness before this fix landed, used
# the old flat $0.14/$0.28 — cheaper than even today's off-peak rate, so
# every one of them understated what the call actually billed.
_DEEPSEEK_PEAK_HOURS_UTC = ((1, 4), (6, 10))


def _deepseek_v4_flash_rate(now=None):
    """The rate that applies right now — double `PRICING`'s pair during peak.

    ``now`` is injectable so a test can pin a peak or an off-peak hour
    without depending on when it happens to run.
    """
    hour = (now or datetime.now(timezone.utc)).hour
    peak = any(start <= hour < end for start, end in _DEEPSEEK_PEAK_HOURS_UTC)
    rate = PRICING["deepseek-v4-flash"]
    return (rate[0] * 2, rate[1] * 2) if peak else rate


# What a call may spend, deliberation included. Raised for one run with
# `EVAL_MAX_OUTPUT_TOKENS`: the cap is what a truncated call ran into, so a run
# that moves it is measuring something else, and `evals.run` records the value
# it used alongside the corpus.
MAX_OUTPUT_TOKENS = int(os.environ.get("EVAL_MAX_OUTPUT_TOKENS", "32000"))

# Both SDKs retry 429s and 5xx on their own, twice, with backoff. Pinned here
# rather than left to the default because a failed call is not a bad score, it
# is a fragment dropped from the false-positive rate — so how many transient
# failures survive is part of what a report means, and it should not change
# quietly under an SDK upgrade.
MAX_RETRIES = int(os.environ.get("EVAL_MAX_RETRIES", "3"))


# One client per provider, shared by every thread.
#
# Not a micro-optimisation: a client built per call opens its own TCP
# connection and negotiates its own TLS, and doing that from sixteen threads at
# once is what a windowed pass spends its wall clock on. Measured on sixteen
# identical calls — 8.4 s and 5.9x effective parallelism with a client each,
# 2.4 s and 13.0x sharing one. Both SDKs document their clients as thread-safe
# and pool connections internally; the lock is only so two threads do not build
# the first one twice.
_clients = {}
_clients_lock = threading.Lock()


def _client(name, build):
    client = _clients.get(name)
    if client is None:
        with _clients_lock:
            client = _clients.get(name)
            if client is None:
                client = _clients[name] = build()
    return client


class Reply(BaseModel):
    """One model answer and what it spent.

    ``reasoning_tokens`` is a slice of ``output_tokens``, not an addition to
    it: a reasoning model bills its deliberation as completion tokens, which is
    also what ``max_tokens`` caps. Kept apart because the interesting number is
    how much of the budget went to thinking rather than to answering.
    """

    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0


class Usage(BaseModel):
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: float = 0.0
    seconds: float = 0.0

    def add(self, other):
        self.calls += other.calls
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.reasoning_tokens += other.reasoning_tokens
        self.cost_usd += other.cost_usd
        self.seconds += other.seconds


def price(model, input_tokens, output_tokens):
    # A model missing from PRICING is a config error, not a free run. Cost per
    # run is what the cost claim rests on, so a silent $0.00 is worse than a crash —
    # and `systems.build` catches it before a single paid call goes out.
    rates = _deepseek_v4_flash_rate() if model == "deepseek-v4-flash" else PRICING[model]
    return (input_tokens * rates[0] + output_tokens * rates[1]) / 1_000_000


def spent(model, reply, seconds):
    """The usage of a single completed call."""
    return Usage(
        calls=1,
        input_tokens=reply.input_tokens,
        output_tokens=reply.output_tokens,
        reasoning_tokens=reply.reasoning_tokens,
        cost_usd=price(model, reply.input_tokens, reply.output_tokens),
        seconds=seconds,
    )


def deepseek_generate(model, system, user, reasoning_effort=None):
    def build():
        # Imported here so the offline systems run without the provider SDKs.
        from openai import OpenAI

        return OpenAI(
            api_key=os.environ["DEEPSEEK_API_KEY"],
            base_url="https://api.deepseek.com",
            max_retries=MAX_RETRIES,
        )

    client = _client("deepseek", build)
    # Omitted rather than defaulted: the naive baseline's numbers are cached
    # and reused, and they were paid for by a request without this field.
    effort = {"reasoning_effort": reasoning_effort} if reasoning_effort else {}
    response = client.chat.completions.create(
        model=model,
        max_tokens=MAX_OUTPUT_TOKENS,
        messages=_messages(system, user),
        **effort,
    )
    choice = response.choices[0]
    if choice.finish_reason == "length":
        raise RuntimeError("response truncated by max_tokens")
    usage = response.usage
    return Reply(
        text=choice.message.content or "",
        input_tokens=usage.prompt_tokens,
        output_tokens=usage.completion_tokens,
        reasoning_tokens=_reasoning_tokens(usage),
    )


def bounded_deepseek(effort):
    """A DeepSeek client that caps how long the model may deliberate.

    Left to itself the cheap model reasons without bound, and it does so worst
    on the text that needs no correcting: on a clean 250-word fragment it spent
    all 32,000 output tokens weighing dialogue punctuation and never emitted an
    answer at all (docs/PLAN.md, H1). Reasoning is billed as output and capped
    by ``max_tokens``, so an unbounded pass does not degrade — it truncates,
    and a truncated call is a fragment dropped from the false-positive rate.
    """

    def generate(model, system, user):
        return deepseek_generate(model, system, user, reasoning_effort=effort)

    return generate


def claude_generate(model, system, user, effort=None, thinking=True, max_tokens=None):
    """One Claude call. ``effort`` is omitted unless a caller asks for it.

    Sonnet 5 deliberates by default: leaving ``thinking`` unset runs it
    adaptively, which is why a call that emits four edits spends a thousand
    output tokens. ``output_config.effort`` is the dial on how much of that it
    does, and the default is ``high``. It is passed only when set, because the
    cached rows in the harness — `naive-claude`, `corrector-claude` — were paid
    for by a request that did not carry the field, and a request that carries
    it is not the same request.
    """

    def build():
        import anthropic

        return anthropic.Anthropic(max_retries=MAX_RETRIES)

    client = _client("anthropic", build)
    extra = {"system": system} if system else {}
    if effort:
        extra["output_config"] = {"effort": effort}
    if not thinking:
        extra["thinking"] = {"type": "disabled"}
    with client.messages.stream(
        model=model,
        max_tokens=max_tokens or MAX_OUTPUT_TOKENS,
        messages=[{"role": "user", "content": user}],
        **extra,
    ) as stream:
        response = stream.get_final_message()
    if response.stop_reason == "refusal":
        raise RuntimeError(f"refusal: {response.stop_details}")
    text = "".join(block.text for block in response.content if block.type == "text")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("response truncated by max_tokens")
    return Reply(
        text=text,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
    )


def tuned_claude(effort=None, thinking=True, max_tokens=None):
    """A Claude client that says how hard the model may think, and for how long.

    The counterpart of ``bounded_deepseek``: both exist because the model the
    pass runs on deliberates by default and the deliberation is what the wall
    clock is made of. What each dial is worth is a measurement, not a guess —
    see docs/PLAN.md.

    ``max_tokens`` is a ceiling and not a target, but it is worth setting on a
    windowed pass: ``MAX_OUTPUT_TOKENS`` is sized for a call that answers for a
    whole document, and a call that answers for two hundred words that runs
    away has to be stopped an order of magnitude sooner.
    """

    def generate(model, system, user):
        return claude_generate(
            model, system, user, effort=effort, thinking=thinking, max_tokens=max_tokens
        )

    return generate


def gemini_generate(model, system, user, thinking_budget=None):
    """One Gemini call. ``thinking_budget`` is the dial the others call effort.

    0 turns deliberation off outright, a positive number caps it, and ``None``
    leaves the model to decide. Same axis as DeepSeek's ``reasoning_effort`` and
    Claude's ``output_config.effort``, and measured the same way: the thinking
    is billed as output and is most of what a call's wall clock is.
    """

    def build():
        from google import genai

        return genai.Client(api_key=os.environ["GOOGLE_API_KEY"])

    from google.genai import types

    client = _client("gemini", build)
    config = types.GenerateContentConfig(
        max_output_tokens=MAX_OUTPUT_TOKENS,
        system_instruction=system or None,
        # Only sent when asked for: a request that carries the field is not the
        # request that produced a cached row without it.
        thinking_config=(
            None
            if thinking_budget is None
            else types.ThinkingConfig(thinking_budget=thinking_budget)
        ),
    )
    # The SDK does not retry 429s and the free tier hands them out in bursts.
    # Same posture as MAX_RETRIES on the other two clients: how many transient
    # failures survive is part of what a report means.
    for attempt in range(MAX_RETRIES):
        try:
            response = client.models.generate_content(model=model, contents=user, config=config)
            break
        except Exception as exc:
            if "RESOURCE_EXHAUSTED" not in str(exc) or attempt == MAX_RETRIES - 1:
                raise
            time.sleep(2 * (attempt + 1))
    candidate = (response.candidates or [None])[0]
    reason = getattr(candidate, "finish_reason", None)
    if reason is not None and str(reason).endswith("MAX_TOKENS"):
        raise RuntimeError("response truncated by max_tokens")
    usage = response.usage_metadata
    return Reply(
        text=response.text or "",
        input_tokens=usage.prompt_token_count or 0,
        output_tokens=(usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0),
        reasoning_tokens=usage.thoughts_token_count or 0,
    )


def bounded_gemini(thinking_budget):
    """A Gemini client with its deliberation pinned. See ``bounded_deepseek``."""

    def generate(model, system, user):
        return gemini_generate(model, system, user, thinking_budget=thinking_budget)

    return generate


def _messages(system, user):
    """An empty system prompt sends no system message at all.

    The naive baseline has no system prompt and its numbers are cached and
    reused across runs; the request it sends must stay the one that produced
    them.
    """
    head = [{"role": "system", "content": system}] if system else []
    return head + [{"role": "user", "content": user}]


def _reasoning_tokens(usage):
    details = getattr(usage, "completion_tokens_details", None)
    return getattr(details, "reasoning_tokens", 0) or 0
