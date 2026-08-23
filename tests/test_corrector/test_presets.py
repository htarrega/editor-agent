"""The shipping presets against the rows the harness scored them as.

`corrector/presets.py` writes out the same configurations `evals/systems.py`
does, because the harness's rows read `EVAL_*` overrides and must keep meaning
what the cached reports say, while the product's read the product's settings.
Two copies of a dozen constructor arguments drift, and the drift is invisible:
the API keeps answering, the harness keeps scoring, and the number on the row
stops describing the thing that ships.

So they are compared here, argument by argument. A deliberate change to a
preset breaks this and has to be made in both places, which is the point.
"""

import unittest

from corrector import presets
from evals.systems import BUILDERS

# Everything `Corrector.__init__` keeps that changes what a pass does.
# `prompt` is compared separately; `_generate` is a closure and is checked
# through the effort it was built with, which is the only thing that varies.
SHAPE = (
    "model",
    "block_words",
    "blocks_per_call",
    "window_blocks",
    "context_blocks",
    "concurrency",
    "aspects",
    "mechanical",
    "verify",
    "attempts",
    "deadline",
)


def shape(corrector):
    return {name: getattr(corrector, name) for name in SHAPE}


class PresetsMatchTheScoredRows(unittest.TestCase):
    def assert_same(self, preset_name, row_name):
        preset = presets.build(preset_name)
        row = BUILDERS[row_name]().corrector
        self.assertEqual(shape(preset), shape(row), f"{preset_name} has drifted from {row_name}")
        self.assertEqual(preset.prompt, row.prompt)
        # `fallback` is a function, so only its presence is comparable here.
        self.assertEqual(preset.fallback is None, row.fallback is None)

    def test_blocks_is_corrector_blocks(self):
        self.assert_same("blocks", "corrector-blocks")

    def test_raced_is_corrector_raced(self):
        self.assert_same("raced", "corrector-raced")

    def test_fast_is_corrector_fast(self):
        self.assert_same("fast", "corrector-fast")

    def test_lean_is_corrector_lean(self):
        self.assert_same("lean", "corrector-lean")


class Build(unittest.TestCase):
    def test_an_unknown_name_raises_rather_than_defaulting(self):
        # Falling back to the default would leave the API running a
        # configuration nobody asked for, and looking perfectly healthy.
        with self.assertRaises(ValueError) as caught:
            presets.build("corrector-raced")
        self.assertIn("raced", str(caught.exception))

    def test_every_preset_builds(self):
        for name in presets.PRESETS:
            with self.subTest(name=name):
                self.assertIsNotNone(presets.build(name))

    def test_the_default_setting_names_a_preset(self):
        from corrector import settings

        self.assertIn(settings.SYSTEM, presets.PRESETS)


if __name__ == "__main__":
    unittest.main()
