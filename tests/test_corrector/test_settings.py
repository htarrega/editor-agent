import importlib
import os
import unittest
from unittest import mock

from corrector import settings


class System(unittest.TestCase):
    """`SYSTEM` is the one knob in this module that is not read from the
    environment — a deliberate choice (`corrector/settings.py`'s own
    comment), not an oversight, so it gets its own test rather than relying
    on the absence of an `os.environ.get` call to speak for itself.
    """

    def test_system_is_bare(self):
        self.assertEqual(settings.SYSTEM, "bare")

    def test_the_environment_variable_has_no_effect(self):
        # Every other setting in this module would change here. This one
        # must not: the API and any exterior deployment run `bare` and
        # nothing else, regardless of what a deploy config sets.
        with mock.patch.dict(os.environ, {"EDITOR_AGENT_SYSTEM": "raced"}):
            importlib.reload(settings)
        self.addCleanup(importlib.reload, settings)

        self.assertEqual(settings.SYSTEM, "bare")


if __name__ == "__main__":
    unittest.main()
