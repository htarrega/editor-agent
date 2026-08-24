"""The corrector configurations the product is allowed to ship.

A `Corrector` takes a dozen constructor arguments and only a handful of their
combinations have ever been measured. These are those combinations, by the name
the harness scores them under, so that what runs behind the API is a row from
`docs/PLAN.md` rather than a set of knobs somebody turned hopefully.

Every preset here mirrors its row in `evals/systems.py`. They are written out
twice rather than shared because the harness's rows read `EVAL_*` overrides and
must keep meaning exactly what the cached reports say they mean, while these
read the product's settings and are free to follow them. What keeps the two
from drifting apart silently is `tests/test_corrector/test_presets.py`, which
builds both and compares them argument by argument.

Numbers below are `--repeats 3` on the 8,254-word corpus, `$/10k words`
computed from each row's own input/output tokens at the rate that actually
applied when it ran — off-peak `deepseek-v4-flash` (`corrector/llm.py`,
`_deepseek_v4_flash_rate`). All re-measured 2026-08-24.

| preset            | F0.5   | P     | R     | s/doc | $/10k  | draws | vs `raced` |
|-------------------|--------|-------|-------|-------|--------|-------|------------|
| `blocks`          | .956-.963 | .971-.974 | .901-.921 | ~70-74 | .0395-.0415 | 3 | ~2.0×  |
| `swept`           | .942-.952 | .946-.955 | .925-.941 | ~60-66 | .0353-.0354 | 2 | ~2.3×  |
| `lean`            | 0.929  | 0.942 | 0.883 | ~68   | 0.0378 | 1     | ~2.2×      |
| `raced`           | 0.860  | 0.933 | 0.655 | 5.6   | 0.0824 | 1     | 1×         |
| `swift` (refuted) | 0.879  | 0.916 | 0.756 | 3.8   | 0.0881 | 1     | 0.94×      |
| `fast` (refuted)  | 0.870  | 0.910 | 0.739 | 2.3   | 0.0888 | 1     | 0.93×      |
| **`bare`, shipped** | **0.902**¹ | 0.914 | 0.853 | ~7.5 | **0.0083**¹ | **3** | **9.97×**  |

¹ Pooled across three draws — see `corrector/presets.py:bare` for each one.

`swift` and `fast` cost *more* than `blocks`, not less — the windowed shape
resends `context_blocks=12` (~1,250 words) plus the full system prompt on
every one of 549 calls, where `blocks`/`swept`/`bare` pay for context once
per document. `bare` is `swept` with `reasoning_effort=none`: the shape
`docs/PLAN.md`'s "Settled" section already found deliberation-off to fail on
(whole document, P 0.756 against 0.935 windowed) — except that finding was
measured on text the rules had not yet cleared, and on cleaned text it does
not fail. Three independent draws (different seeds), pooled: F0.5 0.902 —
above `raced`'s own 0.860 — at $0.0083 per 10k words. See
`corrector/presets.py:bare`'s own docstring for the individual draws and
docs/PLAN.md, "The deadline was a bet", for the fuller account of how this
branch got here, including the two refutations that pointed the way.

`EDITOR_AGENT_SYSTEM` chooses. It is `bare` — the row this branch was opened
to find, confirmed on three draws, not one. `swept` is the fallback for
anyone who wants the last ~9 points of F0.5 back and can spend ~4× more to
get them; both are real, shipped choices, not one believed and one known.
"""

from corrector import settings
from corrector.correct import Corrector
from corrector.llm import bounded_deepseek, deepseek_generate


def hurried(model, system, user):
    """The floor under `raced`'s deadline: the same call with deliberation off.

    Not a competitor to the deliberated attempts and never preferred to one —
    it exists so that a block whose three deliberated tickets all ran long
    still comes back with something rather than nothing.
    """
    return deepseek_generate(model, system, user, reasoning_effort="none")


def blocks():
    """One call for the whole document, and the best F0.5 measured with more
    than one draw behind it — and, re-measured 2026-08-24, cheaper than
    `raced` too, not just better.

    ~74 s on 2,000 words, most of it the model deliberating. That used to be
    priced as the cost of the recall, against `raced`'s clock; it no longer
    reads that way, because `raced`'s own clock only holds on a good hour.
    See `corrector/settings.py` and docs/PLAN.md, "The deadline was a bet".

    What the harness scores by default, what the cached reports quote, and
    — again, as of this reversal — what the API ships.
    """
    return Corrector(
        model=settings.MODEL,
        generate=bounded_deepseek(settings.EFFORT),
        block_words=settings.BLOCK_WORDS,
    )


def raced():
    """Under five seconds without giving up the deliberation — when the
    provider cooperates. No longer what the API ships; see `blocks`.

    One block per call at the default effort scores well on its own but takes
    up to 19 s, and all of that is the tail. So each call is issued three
    times at once and the first answer wins, under a hard 4.3 s deadline, with
    a `hurried` ticket queued first for every block so nothing comes back
    empty.

    That bought a quality difference against `blocks` inside this harness's
    own run-to-run spread — on the day it was measured. Re-measured
    2026-08-24, alone and uncontended: F0.5 0.860 against `blocks`' 0.963, a
    gap four times the spread that used to say the two were the same. The
    deadline did not move; the provider's pace under it did, so more blocks
    missed their deliberated attempts and fell back to `hurried`, which finds
    less by design. The redundancy is what makes this row cost roughly twice
    `blocks` ($0.0824 against $0.0415 per 10k words, both at today's rate) —
    and this time it bought a worse row, not merely a faster one.
    See docs/PLAN.md, "The deadline was a bet".
    """
    return Corrector(
        model=settings.MODEL,
        generate=bounded_deepseek(settings.EFFORT),
        block_words=settings.BLOCK_WORDS,
        window_blocks=1,
        context_blocks=12,
        # Bounded well below what a long document would ask for: firing 368
        # calls at a provider that runs 67 does not make them 368 in flight.
        concurrency=96,
        attempts=3,
        deadline=4.3,
        fallback=hurried,
        mechanical=True,
    )


def lean():
    """`blocks`, asked only what no rule decides. Tried as a cost row; refuted
    as one, and registered so the refutation reproduces.

    Same shape as `blocks` — one call, the whole document, deliberation on —
    except the brief is narrowed to `juicio`: concordance, dequeísmo/queísmo,
    laísmo/loísmo, dead verb forms, homophones, diacritic tildes. The four
    orthotypographic types the rule pack already owns outright and the three
    dictionary-decided spelling types are never put to the model at all, on
    the theory that a shorter brief is a shorter deliberation.

    Measured `--repeats 3`, 2026-08-24: F0.5 0.929 against `blocks`' 0.963 —
    real recall lost (0.883 against 0.921), not noise — for $0.0378 against
    $0.0415 per 10k words. Nine percent cheaper for a quality drop outside
    the spread is not a trade worth making; the model's reasoning tokens
    barely moved (9,318 against 9,966 per call), so a narrower brief does not
    mean a shorter read, it means a less careful one. Consistent with
    "narrowing the brief" already being a wash on precision (docs/PLAN.md,
    "buying quality back cheaply") — spent here on cost instead, and lost.
    Compare `swept`, which cuts the same reasoning by removing spans rather
    than by removing categories, and actually moves the number.
    """
    return Corrector(
        model=settings.MODEL,
        generate=bounded_deepseek(settings.EFFORT),
        block_words=settings.BLOCK_WORDS,
        aspects=["juicio"],
        mechanical=True,
    )


def swept():
    """`blocks`, with the rule pack run first instead of after. Shipped —
    confirmed with two independent `--repeats 3` draws, not one.

    `mechanical=True` (`raced`, `fast`) asks the model about everything and
    lets a rule's edit override a clashing one afterward — the model still
    reads every mechanically-decidable span and judges it, even though the
    judgment is thrown away. This runs the rules first, applies them, and
    sends the model the swept text: nowhere left that looks like an error of
    the four types the rules own outright.

    Measured `--repeats 3` twice, 2026-08-24: F0.5 0.952 and 0.942, both
    inside `blocks`' own 0.956-0.963 range and each other's — the quality
    difference is noise, not a result, by this document's own 0.043 bar. The
    cost difference is not noise: $0.0354 and $0.0353 per 10k words, both
    draws, a consistent 12-15% under every `blocks` draw measured. Reasoning
    tokens run ~12% lower per call than `blocks`', and unlike `lean`, recall
    does not pay for it — both draws' recall (0.941, 0.925) sit at or above
    `blocks`'. A single small-fragment smoke draw earlier the same day had
    suggested a much larger cut (reasoning down ~44-62%); at full-corpus
    scale, twice, that reproduces as this — a real number, not the
    over-optimistic one, but a real one.

    See `Corrector._correct_whole_precorrected` for the offset-remapping this
    relies on and docs/PLAN.md, "The deadline was a bet", for the fuller
    account, including two windowed variants (`fast`, `swift`) that looked
    like they should cost less and, measured, cost more — this shape does
    not share their flaw because it makes 16 calls, not ~549.
    """
    return Corrector(
        model=settings.MODEL,
        generate=bounded_deepseek(settings.EFFORT),
        block_words=settings.BLOCK_WORDS,
        precorrect=True,
    )


def bare():
    """`swept`, with deliberation off. Shipped — confirmed with three
    independent `--repeats 3` draws (different seeds), pooled: **F0.5 0.902,
    $0.0083 per 10k words — 9.97× `raced`'s $0.0824, at a quality this
    document's own pipeline has never delivered `raced` at (0.860).**

    This was expected to fail the way `fast` does — deliberation off, asked
    of a model that has to judge everything at once, which is exactly the
    shape docs/PLAN.md's "Settled" section already measured as worse (whole
    document beside a window scores P 0.756 against 0.935 at
    `reasoning_effort=none`). It did not fail. The difference is what "asked
    to judge everything" means here: `swept`'s rule pack has already removed
    the four orthotypographic and three dictionary-decided types before the
    model reads a single character, so a model with no deliberation to spend
    is spending its one reading on `juicio` alone, in effect — the same
    narrowing `lean` tried explicitly and could not make work with
    deliberation *on*. Off, on pre-cleaned text, it works. Nobody had
    isolated "no reasoning" from "reasoning about spans a rule already
    decided" before this measurement; every prior test of
    `reasoning_effort=none` in this codebase (`fast`, and the four failures
    logged under "Settled") ran on text the rules had not yet touched.

    Individual draws: $0.0072/10k (F0.5 0.908, 0 errors), $0.0109/10k (F0.5
    0.892, 2 unparseable replies of 16 calls), $0.0075/10k (F0.5 0.903, 0
    errors) — 11.4×, 7.6×, 11.0× `raced` respectively. The spread is real:
    a `reasoning_effort=none` reply occasionally returns malformed JSON
    (`_json_body`'s closing-bracket heuristic catching trailing content), at
    roughly 4% of calls across the three draws. A failed call never touches
    the document — it is recorded and that block keeps whatever it had — so
    the failure mode is missed edits on one block, not corrupted output, and
    it is already inside the pooled number above, not hidden by it.

    `swept` stays the row to reach for when the 10% quality gap to it
    (0.902 against 0.94-0.96) matters more than the last 3× of cost; `bare`
    is the row that actually answers what this branch was opened to answer.
    """
    return Corrector(
        model=settings.MODEL,
        generate=bounded_deepseek("none"),
        block_words=settings.BLOCK_WORDS,
        precorrect=True,
    )


def fast():
    """The cheapest way to the clock — re-measured 2026-08-24 alongside
    `swift` and no longer the cheapest way to the bill.

    `reasoning_effort=none` is the only setting that puts a call under five
    seconds on its own, and taking the deliberation away is what drops F0.5 to
    0.870 (was 0.867, pre-2026-08-16) and precision to 0.910. The rule pack
    (`mechanical`) is what pays part of it back: the four orthotypographic
    types are decidable, the model is measurably bad at them with or without
    deliberation, and the rules score 150 of the corpus's 495 seeded errors at
    P 0.974 in microseconds. The model is then asked only for `juicio` — what
    no rule decides.

    What the old row never priced: $0.0888 per 10k words, *more* than
    `blocks`' $0.0395-0.0415, because 549 windowed calls each re-sending
    `context_blocks=12` costs more in input tokens than the tiny per-call
    output saves. `bare` gets the clock's cheap no-reasoning call *and* a real
    cost win, by keeping `blocks`' one-call shape and cleaning the text first
    instead of narrowing the window. See `corrector/presets.py:bare`.
    """
    return Corrector(
        model=settings.MODEL,
        generate=bounded_deepseek("none"),
        block_words=settings.BLOCK_WORDS,
        window_blocks=2,
        context_blocks=12,
        concurrency=40,
        aspects=["juicio"],
        mechanical=True,
    )


def swift():
    """`fast`, with the rule pack run first instead of after. Measured
    2026-08-24, `--repeats 3`, and refuted — not on the axis this docstring
    used to predict.

    F0.5 0.879 against `blocks`' 0.956 — a real gap, consistent with `fast`'s
    own 0.870 and the four-times-refuted "Settled" finding that deliberation
    is what buys recall. That part of the prediction held. What did not: the
    *cost* side was never in question, or so this docstring argued from
    `swept`'s non-reasoning output share extrapolated to a zero-reasoning
    pass — $0.0074/10k words, 11× `raced`. Measured: **$0.0881/10k, more
    expensive than `blocks`' $0.0395, not less.**

    The arithmetic error: it costed the *output* side of a windowed pass and
    never costed the *input* side of running 549 calls instead of 16. Every
    one of those 549 carries `context_blocks=12` — roughly 1,250 words of
    surrounding text — plus the full system prompt, so a pass that emits
    almost nothing (34 output tokens/call, next to nothing to reason about)
    still spends 1,265,404 input tokens getting there. `blocks` spends 66,619
    input tokens total for the same corpus, one context per document instead
    of one per window. This is the same failure mode `corrector-raced` was
    built to hide behind a five-second wall clock, not the one this session
    thought it had found a way around: windowing was never free, and nothing
    about `precorrect` changes what a window costs to keep re-sending.
    `corrector-fast` at $0.0888/10k, measured the same run, confirms it is
    the shape and not the rule-pack ordering — swapping `mechanical` for
    `precorrect` moved the number by nothing worth naming.

    Kept registered, not deleted, as the second entry in this file's list of
    corrected mistakes — `bare` was the first. See docs/PLAN.md, "The
    deadline was a bet", for both.
    """
    return Corrector(
        model=settings.MODEL,
        generate=bounded_deepseek("none"),
        block_words=settings.BLOCK_WORDS,
        window_blocks=2,
        context_blocks=12,
        concurrency=40,
        aspects=["juicio"],
        precorrect=True,
    )


PRESETS = {
    "blocks": blocks,
    "raced": raced,
    "fast": fast,
    "lean": lean,
    "swept": swept,
    "bare": bare,
}

# `swift` is deliberately not in `PRESETS`: it was the reasoned prediction
# for where a 10x cut would come from, and it was wrong — measured, refuted,
# more expensive than `blocks`. `bare` is what actually got there, arrived at
# after `swift`'s refutation pointed at the whole-document shape instead of
# the windowed one. `EDITOR_AGENT_SYSTEM=swift` would put a refuted
# configuration in production; `corrector-swift` (`evals/systems.py`) is
# still reachable for whoever wants to reproduce the refutation.


def build(name):
    """The preset `name`, or a `ValueError` naming the ones that exist.

    A typo in `EDITOR_AGENT_SYSTEM` must not fall back to the default: the run
    would look fine and be measuring something nobody asked for.
    """
    if name not in PRESETS:
        raise ValueError(f"unknown system {name!r}; available: {sorted(PRESETS)}")
    return PRESETS[name]()
