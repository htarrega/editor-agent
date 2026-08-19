import hashlib
import importlib
import os
import unittest
from unittest import mock

from corrector import blocks
from corrector import settings as settings_module
from evals import systems

# The model and prompt every row in docs/PLAN.md was measured with, read back
# from the two reports those tables cite: `20260818-133524-square.json` (H1, the
# prompt × model square, all six rows) and `20260819-135324-blocks-repeats3.json`
# (H5, chunking). Reports live outside the repository (H0), so the values are
# pinned here rather than read from them.
CORRECTOR_PROMPT_SHA256 = "650c1025598ee15c4122e9d2e416356f57df8901a9974e1c7caecb763cb76f40"
NAIVE_PROMPT_SHA256 = "706990ac5152f73a736a4a9579cb1afbbeeffca8c74087fc593b363784f35631"

# A baseline has no block numbering of its own, which is not the same fact as
# `corrector-v0` asking for line numbering by name.
ABSENT = object()

MEASURED_ROWS = {
    # name: (model, block_words, prompt)
    "corrector-v0": ("deepseek-v4-flash", None, CORRECTOR_PROMPT_SHA256),
    "corrector-blocks": ("deepseek-v4-flash", 50, CORRECTOR_PROMPT_SHA256),
    "corrector-claude": ("claude-sonnet-5", None, CORRECTOR_PROMPT_SHA256),
    "naive-deepseek": ("deepseek-v4-flash", ABSENT, NAIVE_PROMPT_SHA256),
    "naive-claude": ("claude-sonnet-5", ABSENT, NAIVE_PROMPT_SHA256),
}


class Chunking(unittest.TestCase):
    """Every LanguageTool edit position is `offset + match["offset"]`, and no
    corpus fragment has ever been long enough to split. These pin the branch
    before a longer fragment reaches it."""

    TEXT = "\n\n".join(f"parrafo {i} " + "x" * 40 for i in range(20))

    def test_chunks_cover_the_text_exactly_once(self):
        chunks = systems._chunks(self.TEXT, 100)
        self.assertEqual("".join(chunk for _, chunk in chunks), self.TEXT)

    def test_offsets_map_back_to_the_original(self):
        for offset, chunk in systems._chunks(self.TEXT, 100):
            self.assertEqual(self.TEXT[offset : offset + len(chunk)], chunk)

    def test_short_text_is_one_chunk(self):
        self.assertEqual(systems._chunks("corto", 100), [(0, "corto")])

    def test_empty_text_asks_for_nothing(self):
        self.assertEqual(systems._chunks("", 100), [])


class Unfence(unittest.TestCase):
    """Markdown wrapping was 45% of a naive baseline's false positives. Keep
    the measurement protected."""

    def test_strips_a_bare_fence(self):
        self.assertEqual(systems._unfence("```\nhola\n```"), "hola")

    def test_strips_a_tagged_fence(self):
        self.assertEqual(systems._unfence("```text\nhola\n```"), "hola")

    def test_leaves_prose_alone(self):
        self.assertEqual(systems._unfence("hola `code` adios"), "hola `code` adios")

    def test_leaves_a_fence_that_is_not_the_whole_reply(self):
        reply = "Aqui tienes:\n```\nhola\n```"
        self.assertEqual(systems._unfence(reply), reply)


class Pricing(unittest.TestCase):
    def test_every_registered_model_has_a_price(self):
        for name, build in systems.BUILDERS.items():
            model = getattr(build(), "model", None)
            if model:
                self.assertIn(model, systems.PRICING, name)


class SettingsDefaults(unittest.TestCase):
    """`corrector/settings.py` is the single place MODEL, EFFORT and
    BLOCK_WORDS are defined; `evals/systems.py` only falls back to it. If the
    resolved defaults ever moved, every cached baseline row would be quoting
    numbers for a configuration the product no longer runs."""

    def setUp(self):
        # MODEL/EFFORT/BLOCK_WORDS are read once at import time, so a plain
        # os.environ patch does nothing until the module runs again.
        env = {k: v for k, v in os.environ.items() if not k.startswith("EDITOR_AGENT_")}
        patch = mock.patch.dict(os.environ, env, clear=True)
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(importlib.reload, settings_module)

    def test_defaults_are_the_values_every_cached_row_was_paid_for(self):
        importlib.reload(settings_module)
        self.assertEqual(settings_module.MODEL, "deepseek-v4-flash")
        self.assertEqual(settings_module.EFFORT, "minimal")
        self.assertEqual(settings_module.BLOCK_WORDS, 50)

    def test_editor_agent_env_vars_move_the_defaults(self):
        os.environ["EDITOR_AGENT_MODEL"] = "other-model"
        os.environ["EDITOR_AGENT_EFFORT"] = "high"
        os.environ["EDITOR_AGENT_BLOCK_WORDS"] = "77"
        importlib.reload(settings_module)
        self.assertEqual(settings_module.MODEL, "other-model")
        self.assertEqual(settings_module.EFFORT, "high")
        self.assertEqual(settings_module.BLOCK_WORDS, 77)


class EvalVarsOverrideSettings(unittest.TestCase):
    """The harness caches paid baseline rows under the `EVAL_*` names, so
    those names and their precedence over `corrector/settings.py` must stay
    exactly what they were before the two were unified: an `EVAL_*` override
    wins no matter what the product-side setting says."""

    def setUp(self):
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith("EVAL_") and not k.startswith("EDITOR_AGENT_")
        }
        patch = mock.patch.dict(os.environ, env, clear=True)
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(importlib.reload, settings_module)
        importlib.reload(settings_module)

    def test_settings_default_flows_through_when_no_eval_var_is_set(self):
        self.assertEqual(systems.BUILDERS["naive-deepseek"]().model, "deepseek-v4-flash")
        self.assertEqual(systems.BUILDERS["corrector-blocks"]().block_words, 50)

    def test_eval_var_wins_even_when_the_settings_default_disagrees(self):
        # Move the settings-side default first, so a pass here can't be
        # explained by the two happening to already agree.
        os.environ["EDITOR_AGENT_MODEL"] = "settings-model"
        os.environ["EDITOR_AGENT_EFFORT"] = "settings-effort"
        os.environ["EDITOR_AGENT_BLOCK_WORDS"] = "9"
        importlib.reload(settings_module)

        os.environ["EVAL_DEEPSEEK_MODEL"] = "eval-model"
        os.environ["EVAL_BLOCK_WORDS"] = "17"
        built = systems.BUILDERS["corrector-blocks"]()
        self.assertEqual(built.model, "eval-model")
        self.assertEqual(built.block_words, 17)


class FrozenRows(unittest.TestCase):
    """Rows whose numbers are quoted in docs/PLAN.md must not move when the
    pipeline's own defaults do. They ask for their numbering by name."""

    def setUp(self):
        """Build the systems as the code defines them, not as a shell configured them.

        The `EVAL_*` overrides change what a system does at runtime, which is the
        point of them — `EVAL_BLOCK_WORDS=17` in the environment is a developer
        measuring something. It is not this class's question, which is whether the
        *code's* frozen defaults have moved.
        """
        kept = {k: v for k, v in os.environ.items() if not k.startswith("EVAL_")}
        patch = mock.patch.dict(os.environ, kept, clear=True)
        patch.start()
        self.addCleanup(patch.stop)

    def test_the_pipeline_defaults_to_the_measured_block(self):
        self.assertEqual(
            systems.BUILDERS["corrector-blocks"]().block_words, blocks.DEFAULT_BLOCK_WORDS
        )

    def test_h1_reference_rows_stay_on_line_numbering(self):
        for name in ("corrector-v0", "corrector-claude"):
            with self.subTest(name):
                self.assertIsNone(systems.BUILDERS[name]().block_words)

    def test_the_default_row_is_the_one_that_won(self):
        self.assertIn("corrector-blocks", systems.DEFAULT_SYSTEMS)
        self.assertNotIn("corrector-v0", systems.DEFAULT_SYSTEMS)

    def test_the_measured_prompt_and_model_have_not_moved(self):
        """The three fields that decide what a cached row means.

        Every report records the `prompt`, the `model` and the `block_words` a
        system ran with, because changing any of them changes what its numbers
        say. `reuse.incompatible` does not compare any of the three — it checks
        the corpus and the six keys in `COMPARED_CONFIG` — so a reworded prompt
        is reused in silence and the table looks unchanged.

        Both sides are pinned, not only the pipeline: `naive-claude` is the
        0.899 bar H1 had to beat, and `evals/systems.py` spends a paragraph on
        why its prompt may not gain a single policy word. Moving any of these is
        a decision, not a refactor: re-measure, then update the constant.
        """
        for name, (model, block_words, prompt_sha) in MEASURED_ROWS.items():
            with self.subTest(name):
                built = systems.BUILDERS[name]()
                self.assertEqual(built.model, model)
                self.assertEqual(getattr(built, "block_words", ABSENT), block_words)
                digest = hashlib.sha256(built.prompt.encode("utf-8")).hexdigest()
                self.assertEqual(digest, prompt_sha)


if __name__ == "__main__":
    unittest.main()
