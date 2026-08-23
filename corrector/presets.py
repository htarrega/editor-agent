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

Numbers below are `--repeats 3` on the 8,254-word corpus, per document,
re-measured 2026-08-24 (the model's own deliberation moved between this run
and the one `docs/PLAN.md`'s older rows quote — `raced`'s cost held, `blocks`'
did not — so this table stands on its own rather than mixing days):

| preset             | F0.5  | P     | s/doc | $/10k words |
|--------------------|-------|-------|-------|-------------|
| `blocks` — shipped | 0.963 | 0.974 | ~74   | 0.061       |
| `lean`             | 0.929 | 0.942 | ~68   | 0.056       |
| `fast`             | 0.867 | 0.904 | 2.4   | 0.056¹      |
| `raced`            | 0.860 | 0.933 | 5.6   | 0.171       |

¹ `fast` was not re-measured this round; its row is the older one.

`EDITOR_AGENT_SYSTEM` chooses. It is `blocks`, reversing the earlier call —
see `corrector/settings.py` for why, and `docs/PLAN.md`, "The deadline was a
bet", for the numbers. `lean` is registered and not shipped: see its own
docstring for what it cost to find that out.
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
    """One call for the whole document, and the best F0.5 measured — and,
    re-measured 2026-08-24, the cheapest of the deliberating rows too.

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
    less by design. The cost held almost exactly (0.1706 against the old
    0.171) — redundancy is what is being paid for here, and it was paid for
    in full either way. What changed is what the redundancy bought this time.
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
    real recall lost (0.883 against 0.921), not noise — for $0.056 against
    $0.061 per 10k words. Six percent cheaper for a quality drop outside the
    spread is not a trade worth making; the model's reasoning tokens barely
    moved (9,318 against 9,966), so a narrower brief does not mean a shorter
    read, it means a less careful one. Consistent with "narrowing the brief"
    already being a wash on precision (docs/PLAN.md, "buying quality back
    cheaply") — spent here on cost instead, and lost.
    """
    return Corrector(
        model=settings.MODEL,
        generate=bounded_deepseek(settings.EFFORT),
        block_words=settings.BLOCK_WORDS,
        aspects=["juicio"],
        mechanical=True,
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


PRESETS = {
    "blocks": blocks,
    "raced": raced,
    "fast": fast,
    "lean": lean,
}


def build(name):
    """The preset `name`, or a `ValueError` naming the ones that exist.

    A typo in `EDITOR_AGENT_SYSTEM` must not fall back to the default: the run
    would look fine and be measuring something nobody asked for.
    """
    if name not in PRESETS:
        raise ValueError(f"unknown system {name!r}; available: {sorted(PRESETS)}")
    return PRESETS[name]()
