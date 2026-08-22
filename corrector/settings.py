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
# `raced` and not `blocks`, by the author's decision. It is the trade the
# harness could describe but never make: 0.036 F0.5 against a run-to-run spread
# of 0.043 — smaller than the instrument — for 88 s down to 4.35, at 9× the
# money. `blocks` remains what the harness scores by default and what every
# cached report quotes, so the two names now mean different things: the
# reference row, and what ships.
SYSTEM = os.environ.get(
    "EDITOR_AGENT_SYSTEM",
    "raced",
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
