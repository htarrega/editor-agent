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
