"""Talking to the providers, and what each call cost.

It lives in ``corrector/`` and not in ``evals/`` because the pipeline is the
thing that makes the calls; the harness only measures them. Every client
returns the same ``Reply``, so a pass does not know which provider answered it.
"""

import os

from pydantic import BaseModel

# USD per million tokens.
PRICING = {
    "deepseek-v4-flash": (0.14, 0.28),
    "deepseek-reasoner": (0.55, 2.19),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-haiku-4-5": (1.00, 5.00),
}

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
    # run is what H7 is measured on, so a silent $0.00 is worse than a crash —
    # and `systems.build` catches it before a single paid call goes out.
    rates = PRICING[model]
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
    # Imported here so the offline systems run without the provider SDKs.
    from openai import OpenAI

    client = OpenAI(
        api_key=os.environ["DEEPSEEK_API_KEY"],
        base_url="https://api.deepseek.com",
        max_retries=MAX_RETRIES,
    )
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


def claude_generate(model, system, user):
    import anthropic

    client = anthropic.Anthropic(max_retries=MAX_RETRIES)
    extra = {"system": system} if system else {}
    with client.messages.stream(
        model=model,
        max_tokens=MAX_OUTPUT_TOKENS,
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
