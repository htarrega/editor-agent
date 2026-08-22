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

Numbers below are `--repeats 3` on the 8,254-word corpus, per document:

| preset            | F0.5  | P     | s/doc | $/10k words |
|-------------------|-------|-------|-------|-------------|
| `blocks`          | 0.947 | 0.960 | ~88   | 0.019       |
| `raced` — shipped | 0.919 | 0.936 | 4.35  | 0.171       |
| `fast`            | 0.867 | 0.904 | 2.4   | 0.056       |

`EDITOR_AGENT_SYSTEM` chooses. It is `raced` by the author's decision, which is
not the row with the best F0.5 — see `corrector/settings.py` for why.
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
    """One call for the whole document, and the best F0.5 measured.

    ~88 s on 2,000 words, ~87% of it the model deliberating. That is the cost of
    the recall: every row that took the clock down took quality with it.

    Still what the harness scores by default and what the cached reports quote,
    but no longer what the API ships — see `raced`.
    """
    return Corrector(
        model=settings.MODEL,
        generate=bounded_deepseek(settings.EFFORT),
        block_words=settings.BLOCK_WORDS,
    )


def raced():
    """What the API ships. Under five seconds without giving up the deliberation.

    One block per call at the default effort scores 0.948 on its own — the
    default's number — but takes 19 s, and all 19 are the tail: the median call
    is 4.3 s. So each call is issued three times at once and the first answer
    wins, under a hard 4.3 s deadline, with a `hurried` ticket queued first for
    every block so nothing can come back empty. All 32 measured documents
    finished under five seconds.

    The quality difference against `blocks` is 0.036, and this harness's
    run-to-run spread is 0.043 — the gap is smaller than the instrument. What
    it does cost is real: 9× the money, because most of those calls are thrown
    away by the one that answered first.
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
}


def build(name):
    """The preset `name`, or a `ValueError` naming the ones that exist.

    A typo in `EDITOR_AGENT_SYSTEM` must not fall back to the default: the run
    would look fine and be measuring something nobody asked for.
    """
    if name not in PRESETS:
        raise ValueError(f"unknown system {name!r}; available: {sorted(PRESETS)}")
    return PRESETS[name]()
