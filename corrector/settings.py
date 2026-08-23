import os

# The three knobs the product and the eval harness must never read from two
# different names, or a run can be measuring one configuration while the
# product ships another without either side noticing.

MODEL = os.environ.get(
    "EDITOR_AGENT_MODEL",
    "deepseek-v4-flash",
)

EFFORT = os.environ.get(
    "EDITOR_AGENT_EFFORT",
    "minimal",
)

BLOCK_WORDS = int(
    os.environ.get(
        "EDITOR_AGENT_BLOCK_WORDS",
        "50",
    )
)

# Which measured configuration the API runs. One of `corrector/presets.py`.
#
# `blocks` and not `raced`, reversing the earlier call. Re-measured
# `--repeats 3` on 2026-08-24: `raced` alone, uncontended, scored F0.5 0.860 —
# not 0.919. Its deadline is a bet on the provider being as fast as it was the
# day it was tuned; that day it was not, and the redundancy that is supposed
# to buy the bet back ($0.0824 against `blocks`' $0.0415 per 10k words, both
# at today's rate) did not save it. `blocks` scored 0.963 the same day, at
# roughly half `raced`'s cost — cheaper *and* better, which is not a trade at
# all. Neither figure is the "9x"/"0.019" pair `docs/PLAN.md`'s older rows
# quote: those used a since-corrected DeepSeek rate and, for `raced`, what
# looks like a units mismatch never caught before now — see docs/PLAN.md,
# "The deadline was a bet", for the reconciliation.
SYSTEM = os.environ.get(
    "EDITOR_AGENT_SYSTEM",
    "blocks",
)

# Where the built front lives, when the API is the thing serving it. A missing
# directory means there is nothing to serve and the API is HTTP only, which is
# every development checkout: `web/dist` is a build artefact and git ignores it.
# The container image builds the front and points this at it.
WEB_DIST = os.environ.get(
    "EDITOR_AGENT_WEB_DIST",
    "web/dist",
)

# The largest text the API will accept. Only `api/` reads it — it lives here
# because this is the one place in the repository that reads the environment,
# and a second config module for a single value is worse than the dilution.
#
# A measured ceiling rather than a policy: 2,000 words is the size the rows in
# docs/PLAN.md were scored at, and there is no document-level pass yet (H5), so
# above it the pipeline runs where nobody has scored it.
MAX_WORDS = int(
    os.environ.get(
        "EDITOR_AGENT_MAX_WORDS",
        "2000",
    )
)
