"""The systems under test: the fixed baselines every change is measured against.

A system takes text and returns typed edits plus what it cost. That is the only
contract; the harness does not care how the edits were produced.
"""

import os
import re
import time

import httpx
from pydantic import BaseModel

from corrector.edits import Edit, diff_edits, trim

# USD per million tokens.
PRICING = {
    "deepseek-v4-flash": (0.14, 0.28),
    "deepseek-reasoner": (0.55, 2.19),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-haiku-4-5": (1.00, 5.00),
}

# The line this prompt must not cross: it may state the output contract, never
# the correction policy. "Return only the corrected text" is what any writer
# adds after reading one reply. "Minimal edits", "keep the author's voice" or
# "only spelling and grammar" would hand the baseline our own thesis, and we
# would end up measuring the pipeline against the pipeline minus the code.
NAIVE_PROMPT = (
    "Corrige este texto. Devuelve únicamente el texto corregido, "
    "con los mismos saltos de línea y sin comentarios ni explicaciones.\n\n"
)

MAX_OUTPUT_TOKENS = 32000

FENCE = re.compile(r"\A\s*```[^\n]*\n(?P<body>.*)\n```\s*\Z", re.DOTALL)


class Usage(BaseModel):
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    seconds: float = 0.0

    def add(self, other):
        self.calls += other.calls
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cost_usd += other.cost_usd
        self.seconds += other.seconds


class Output(BaseModel):
    edits: list[Edit] = []
    usage: Usage = Usage()
    skipped: int = 0
    errors: list[str] = []


def price(model, input_tokens, output_tokens):
    # A model missing from PRICING is a config error, not a free run. Cost per
    # run is what H7 is measured on, so a silent $0.00 is worse than a crash —
    # and `build` catches it before a single paid call goes out.
    rates = PRICING[model]
    return (input_tokens * rates[0] + output_tokens * rates[1]) / 1_000_000


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
            corrected, input_tokens, output_tokens = self._generate(self.model, self.prompt + text)
        except Exception as exc:
            out.errors.append(f"{type(exc).__name__}: {exc}")
            out.usage.calls += 1
            out.usage.seconds += time.monotonic() - started
            return out

        out.usage = Usage(
            calls=1,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=price(self.model, input_tokens, output_tokens),
            seconds=time.monotonic() - started,
        )
        out.edits = diff_edits(text, _unfence(corrected))
        return out


def _unfence(text):
    """Undo markdown fencing. Without it the diff measures formatting, not language."""
    match = FENCE.match(text)
    return match.group("body") if match else text.strip("\n")


def deepseek_generate(model, prompt):
    # Imported here so the offline systems run without the provider SDKs.
    from openai import OpenAI

    client = OpenAI(api_key=os.environ["DEEPSEEK_API_KEY"], base_url="https://api.deepseek.com")
    response = client.chat.completions.create(
        model=model,
        max_tokens=MAX_OUTPUT_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    choice = response.choices[0]
    if choice.finish_reason == "length":
        raise RuntimeError("response truncated by max_tokens")
    usage = response.usage
    return choice.message.content or "", usage.prompt_tokens, usage.completion_tokens


def claude_generate(model, prompt):
    import anthropic

    client = anthropic.Anthropic()
    with client.messages.stream(
        model=model,
        max_tokens=MAX_OUTPUT_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        response = stream.get_final_message()
    if response.stop_reason == "refusal":
        raise RuntimeError(f"refusal: {response.stop_details}")
    text = "".join(block.text for block in response.content if block.type == "text")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("response truncated by max_tokens")
    return text, response.usage.input_tokens, response.usage.output_tokens


# --- registry ---------------------------------------------------------------

BUILDERS = {
    "null": NullSystem,
    "languagetool": LanguageToolSystem,
    "naive-deepseek": lambda: NaivePromptSystem(
        "naive-deepseek",
        os.environ.get("EVAL_DEEPSEEK_MODEL", "deepseek-v4-flash"),
        deepseek_generate,
    ),
    "naive-claude": lambda: NaivePromptSystem(
        "naive-claude", os.environ.get("EVAL_CLAUDE_MODEL", "claude-sonnet-5"), claude_generate
    ),
}

# The plan asks for two baselines: LanguageTool and a naive prompt to a strong
# model. naive-deepseek stays registered for ad-hoc checks but out of the
# default set: it is a reasoning model and does not finish a literary fragment
# within MAX_OUTPUT_TOKENS (see docs/PLAN.md, H0).
DEFAULT_SYSTEMS = ["null", "languagetool", "naive-claude"]


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
