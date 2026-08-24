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
# `bare`, and — a deliberate choice, not merely a default — the only one:
# unlike every other name here, this is not read from the environment. The
# API and any exterior deployment run `bare` and nothing else. Three
# independent `--repeats 3` draws (different seeds), pooled: **F0.5 0.902 at
# $0.0083 per 10k words — 9.97x `raced`'s $0.0824, at a quality `raced` has
# never itself delivered (0.860).**
#
# The chain that got here, all 2026-08-24: `raced`, re-measured alone and
# uncontended, scored 0.860 rather than the 0.919 it shipped on — its
# deadline is a bet on the provider's pace, lost that day. `blocks` replaced
# it, cheaper and better at once. `swept` replaced `blocks` — the same rule
# pack, run before the call instead of after, ~12-15% cheaper with quality
# inside noise, confirmed on two draws. `swift` and `fast` were the reasoned
# prediction for where the next order of magnitude would come from —
# windowed, `reasoning_effort=none` — and both measured *more* expensive
# than `blocks`: 549 calls each re-sending `context_blocks=12` costs more in
# input tokens than a near-empty output saves. `bare` is what that refutation
# pointed at instead: `swept`'s whole-document shape (16 calls, not 549)
# with deliberation off. Every prior test of `reasoning_effort=none` in this
# codebase ran on text the rule pack had not yet cleared; this is the first
# one that did not, and it is the one that worked. See docs/PLAN.md, "The
# deadline was a bet", and `corrector/presets.py:bare` for the individual
# draws — one of three saw 2 of 16 calls return unparseable JSON, which
# `Corrector` already treats as a missed block, not a corrupted one.
#
# `swept`, `blocks` and the rest stay registered in `corrector/presets.py`
# and reachable from the harness (`python -m evals.run --systems
# corrector-swept`) for measurement and comparison — that door is deliberately
# not closed. What is closed is running any of them from this API: there is
# no environment variable that changes what a request here gets served by.
# Whoever wants a different trade-off edits this line and re-deploys, which
# is the point — a choice this consequential should leave a commit, not an
# unreviewable env var flip in someone's deploy config.
SYSTEM = "bare"

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

# Where the Google Docs integration keeps its two files (`corrector/drive.py`).
#
# The client secrets are the OAuth application — downloaded once from Google
# Cloud Console, type «Desktop app». The token is the author's consent to it,
# written after the first `python -m corrector.drive login` and refreshed from
# then on. Neither belongs in the repository, so both default outside it.
GOOGLE_CLIENT_SECRETS = os.environ.get(
    "EDITOR_AGENT_GOOGLE_CLIENT_SECRETS",
    "~/.config/editor-agent/client_secret.json",
)

GOOGLE_TOKEN = os.environ.get(
    "EDITOR_AGENT_GOOGLE_TOKEN",
    "~/.config/editor-agent/token.json",
)
