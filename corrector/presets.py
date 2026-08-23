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
`_deepseek_v4_flash_rate`) for every row but `fast`'s, which predates that
fix. All re-measured 2026-08-24 except `fast`; see docs/PLAN.md, "The
deadline was a bet", for how these numbers were arrived at and what they
correct in this file's own earlier version.

| preset             | F0.5  | P     | R     | s/doc | $/10k words | draws |
|--------------------|-------|-------|-------|-------|-------------|-------|
| `blocks` — shipped | 0.963 | 0.974 | 0.921 | ~74   | 0.0415      | 2     |
| `swept`            | 0.952 | 0.955 | 0.941 | ~60   | 0.0354      | **1** |
| `lean`             | 0.929 | 0.942 | 0.883 | ~68   | 0.0378      | 1     |
| `raced`            | 0.860 | 0.933 | 0.655 | 5.6   | 0.0824      | 1     |
| `fast`¹            | 0.867 | 0.904 | 0.745 | 2.4   | 0.056¹      | —     |

¹ `fast` was not re-measured this round; its row is the pre-2026-08-16 one
and its `$/10k` is not directly comparable to the others'.

`EDITOR_AGENT_SYSTEM` chooses. It is `blocks` — the row with two consistent
draws, not `swept`'s cheaper but unconfirmed one: `--repeats 3` on one draw
is not "nothing," but this document's own rule is that nothing ships off a
single measurement, and the DeepSeek key ran out of balance
(`{'error': {'message': 'Insufficient Balance', ...}}`, 2026-08-24) before a
second `swept` run could settle it. `swept` is registered, cheaper, and
believed rather than known — see its own docstring.
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
    """`blocks`, with the rule pack run first instead of after. Not shipped —
    one draw, not two; see `PRESETS`' own table for why that matters here.

    `mechanical=True` (`raced`, `fast`) asks the model about everything and
    lets a rule's edit override a clashing one afterward — the model still
    reads every mechanically-decidable span and judges it, even though the
    judgment is thrown away. This runs the rules first, applies them, and
    sends the model the swept text: nowhere left that looks like an error of
    the four types the rules own outright.

    Measured `--repeats 3`, 2026-08-24, one draw: F0.5 0.952 against
    `blocks`' 0.963 (inside the spread) for $0.0354 against $0.0415 per 10k
    words — 15% cheaper, reasoning tokens down 12% per call (8,742 against
    9,966), and unlike `lean`, recall did not pay for it (0.941 against
    0.921 — up, not down). A single small-fragment draw earlier the same day
    had suggested a much larger cut (reasoning down ~44-62%); at full-corpus
    scale that shrank to this. The DeepSeek key ran out of balance mid-run on
    the follow-up measurement, so there is no second draw yet to promote this
    past "registered, not shipped."

    See `Corrector._correct_whole_precorrected` for the offset-remapping this
    relies on and docs/PLAN.md, "The deadline was a bet", for the fuller
    account.
    """
    return Corrector(
        model=settings.MODEL,
        generate=bounded_deepseek(settings.EFFORT),
        block_words=settings.BLOCK_WORDS,
        precorrect=True,
    )


def bare():
    """`swept`, with deliberation off. Not measured, and — caught after the
    fact, not before — probably the wrong shape to measure first: see
    `swift`, below, for why. Kept rather than deleted, on the theory that a
    documented dead end is worth more than a quiet one; docs/PLAN.md,
    "Settled" already has the pattern for that.

    Every row that keeps deliberation on (`blocks`, `swept`, `lean`) tops out
    around a 15% cut past `blocks`, because the reasoning tokens deliberation
    spends are ~85-90% of the bill everywhere they were measured and none of
    them touched that share. `reasoning_effort=none` is the only switch this
    codebase has that removes it near-entirely rather than shaving it. This
    asks that question over the whole document, at once — and docs/PLAN.md's
    "Settled" section already has the relevant measurement, found after this
    function was written rather than before it: *"At `reasoning_effort=none`,
    more context is worse: the whole document beside a window scores P 0.756
    against 0.935 for ±600 words."* That is exactly this function's shape.
    `swift` asks the same underlying question — deliberation off, on text the
    rules already cleaned — through the shape that finding actually supports.
    """
    return Corrector(
        model=settings.MODEL,
        generate=bounded_deepseek("none"),
        block_words=settings.BLOCK_WORDS,
        precorrect=True,
    )


def fast():
    """The cheapest way to the clock, and the one that pays for it in quality.

    `reasoning_effort=none` is the only setting that puts a call under five
    seconds on its own, and taking the deliberation away is what drops F0.5 to
    0.867 and precision to 0.904. The rule pack (`mechanical`) is what pays
    part of it back: the four orthotypographic types are decidable, the model
    is measurably bad at them with or without deliberation, and the rules score
    150 of the corpus's 495 seeded errors at P 0.974 in microseconds. The model
    is then asked only for `juicio` — what no rule decides.

    Precision 0.904 means about one edit in ten is wrong. On someone's own
    prose that is the failure that matters, which is why this is not what the
    API ships by default.
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
    """`fast`, with the rule pack run first instead of after. Not measured —
    prepared, and the strongest of the unmeasured candidates, but still a
    hypothesis; do not ship or quote a number for this without running it.

    One line different from `fast`: `mechanical=True` becomes
    `precorrect=True`, everything else — windowed, `context_blocks=12`,
    `reasoning_effort=none` — held exactly fixed, so a measurement of this
    reads as "what did swapping post-hoc rules for pre-applied ones do to
    `fast`'s own number," not a new pass with three things different at once.

    This is the corrected version of `bare`: same underlying question
    (deliberation off, on text the rules already cleaned), asked through the
    shape docs/PLAN.md's own "Settled" section already found works better at
    `reasoning_effort=none` — a window with context, not the whole document.
    `fast`'s measured 0.867 F0.5 is the nearest thing to a prior this has:
    if pre-applying the rules helps here the way it helped `blocks` become
    `swept` (recall *up*, not just cost down), this is the one candidate
    that could plausibly land near both the cost and the quality bar at
    once. If it does not, that is a real answer too — and a cheaper one to
    get than `bare`'s, since a failing window fails alone rather than taking
    the whole document's call down with it.

    The cost side of that "could plausibly" is not actually in doubt:
    computed from `swept`'s own already-measured non-reasoning output share
    (9.7% of its output, at today's off-peak rate) as the floor for a
    zero-reasoning pass over the same cleaned text, this shape prices at
    ~$0.0074/10k words — 11× `raced`'s $0.0824, comfortably past an order of
    magnitude. What is genuinely open is only whether precision and recall
    survive `reasoning_effort=none` once the rules have already cleared the
    easy 40%+ of the taxonomy — a narrower, previously-untested question
    than "does the model need to deliberate at all," which `fast` and four
    other measurements already answered.
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
}

# `bare` and `swift` are deliberately not in `PRESETS`: `EDITOR_AGENT_SYSTEM`
# selecting either would put something in production that has never been run
# once, let alone `--repeats 3`. Reachable from the harness as
# `corrector-bare` / `corrector-swift` (`evals/systems.py`) for exactly that
# measurement, and from here as `corrector.presets.bare` / `.swift` for
# anyone reading this file top to bottom. Move either into this dict,
# alongside a row in the table above, only after it has numbers — the same
# rule that governs everything else in this file. Measure `swift` first: see
# its own docstring for why it is the better-grounded of the two.


def build(name):
    """The preset `name`, or a `ValueError` naming the ones that exist.

    A typo in `EDITOR_AGENT_SYSTEM` must not fall back to the default: the run
    would look fine and be measuring something nobody asked for.
    """
    if name not in PRESETS:
        raise ValueError(f"unknown system {name!r}; available: {sorted(PRESETS)}")
    return PRESETS[name]()
