import unittest

from evals import systems


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


if __name__ == "__main__":
    unittest.main()
