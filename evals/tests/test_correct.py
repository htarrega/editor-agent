import json
import re
import unittest

from corrector.correct import Corrector, kinds_block, parse_edits, render
from corrector.edits import line_spans
from corrector.llm import Reply
from corrector.taxonomy import ERROR_TYPES

TEXT = "El vasu de sidra.\n\n—Dijistes que vendrias —dijo el vasu.\n"


def fake(reply, spy=None):
    """A generate function that answers with `reply` and records what it was asked."""

    def generate(model, system, user):
        if spy is not None:
            spy.update({"model": model, "system": system, "user": user})
        if isinstance(reply, Exception):
            raise reply
        return Reply(text=reply, input_tokens=100, output_tokens=20, reasoning_tokens=8)

    return generate


def edits_json(*items):
    return json.dumps({"edits": list(items)})


class Render(unittest.TestCase):
    """The numbering the prompt shows and the numbering an anchor resolves
    against have to be the same numbering."""

    def test_block_n_is_line_n(self):
        rendered = render(TEXT)
        for number, (start, end) in enumerate(line_spans(TEXT), 1):
            marker = f"[{number}]\n"
            if not TEXT[start:end] and number == len(line_spans(TEXT)):
                continue  # the trailing newline's empty line carries no marker
            self.assertIn(marker + TEXT[start:end], rendered)

    def test_every_line_gets_exactly_one_marker(self):
        markers = re.findall(r"^\[(\d+)\]$", render(TEXT), re.MULTILINE)
        self.assertEqual(markers, [str(n) for n in range(1, len(TEXT.split("\n")))])

    def test_marker_sits_on_its_own_line(self):
        self.assertTrue(render("—Vamos.").startswith("[1]\n—"))

    def test_trailing_blank_lines_get_no_marker(self):
        self.assertEqual(render("hola\n"), "[1]\nhola")


class KindsBlock(unittest.TestCase):
    def test_every_taxonomy_type_reaches_the_prompt(self):
        block = kinds_block()
        for kind in ERROR_TYPES:
            self.assertIn(kind, block)


class ParseEdits(unittest.TestCase):
    ITEM = {"line": 1, "original": "vasu", "replacement": "vaso", "kind": "ortografia_bv"}

    def test_plain_object(self):
        proposals, malformed = parse_edits(edits_json(self.ITEM))
        self.assertEqual(malformed, 0)
        self.assertEqual(proposals[0].replacement, "vaso")
        self.assertEqual(proposals[0].line, 1)

    def test_fenced_json(self):
        proposals, _ = parse_edits(f"```json\n{edits_json(self.ITEM)}\n```")
        self.assertEqual(len(proposals), 1)

    def test_json_wrapped_in_prose(self):
        proposals, _ = parse_edits(f"Aqui tienes:\n{edits_json(self.ITEM)}\nEspero que sirva.")
        self.assertEqual(len(proposals), 1)

    def test_bare_list(self):
        proposals, _ = parse_edits(json.dumps([self.ITEM]))
        self.assertEqual(len(proposals), 1)

    def test_spanish_key(self):
        proposals, _ = parse_edits(json.dumps({"ediciones": [self.ITEM]}))
        self.assertEqual(len(proposals), 1)

    def test_no_edits_is_not_a_failure(self):
        self.assertEqual(parse_edits('{"edits": []}'), ([], 0))

    def test_unusable_entries_are_counted_not_fatal(self):
        proposals, malformed = parse_edits(edits_json(self.ITEM, {"original": "sin replacement"}))
        self.assertEqual((len(proposals), malformed), (1, 1))

    def test_a_reply_without_json_raises(self):
        # Scoring this as "no edits" would hand a failed call a perfect
        # false-positive rate on corpus B.
        with self.assertRaises(ValueError):
            parse_edits("El texto me parece correcto, no he encontrado errores.")


class CorrectorPass(unittest.TestCase):
    def build(self, reply, spy=None):
        return Corrector("deepseek-v4-flash", fake(reply, spy))

    def test_applies_an_anchored_edit(self):
        item = {"line": 1, "original": "vasu de", "replacement": "vaso de", "kind": "ortografia_bv"}
        result = self.build(edits_json(item)).correct(TEXT)
        self.assertEqual(result.errors, [])
        self.assertEqual([e.replacement for e in result.edits], ["o"])
        self.assertEqual(result.edits[0].kind, "ortografia_bv")

    def test_line_disambiguates_a_repeated_anchor(self):
        # "vasu" appears in both lines; only the line number separates them.
        item = {"line": 3, "original": "vasu", "replacement": "vaso"}
        result = self.build(edits_json(item)).correct(TEXT)
        self.assertEqual(len(result.edits), 1)
        self.assertGreater(result.edits[0].start, TEXT.index("\n\n"))

    def test_an_ambiguous_anchor_without_a_line_is_discarded(self):
        item = {"original": "vasu", "replacement": "vaso"}
        result = self.build(edits_json(item)).correct(TEXT)
        self.assertEqual(result.edits, [])
        self.assertEqual(result.rejected, {"anchor_ambiguous": 1})
        self.assertEqual(result.skipped, 1)

    def test_an_invented_anchor_is_discarded_and_logged(self):
        item = {"line": 1, "original": "copa", "replacement": "vaso"}
        result = self.build(edits_json(item)).correct(TEXT)
        self.assertEqual(result.edits, [])
        self.assertEqual(result.rejected, {"anchor_not_found": 1})

    def test_an_edit_that_changes_nothing_is_not_an_edit(self):
        item = {"line": 1, "original": "sidra", "replacement": "sidra"}
        result = self.build(edits_json(item)).correct(TEXT)
        self.assertEqual(result.edits, [])
        self.assertEqual(result.rejected, {"no_change": 1})

    def test_the_marker_is_never_part_of_the_text(self):
        # A model that quotes the marker back gets its edit dropped rather
        # than a numbering artefact spliced into the manuscript.
        item = {"line": 1, "original": "[1]\nEl vasu", "replacement": "[1]\nEl vaso"}
        result = self.build(edits_json(item)).correct(TEXT)
        self.assertEqual(result.edits, [])
        self.assertEqual(result.rejected, {"anchor_not_found": 1})

    def test_usage_is_priced_and_reasoning_kept_apart(self):
        result = self.build(edits_json()).correct(TEXT)
        self.assertEqual(result.usage.calls, 1)
        self.assertEqual(result.usage.reasoning_tokens, 8)
        self.assertAlmostEqual(result.usage.cost_usd, (100 * 0.14 + 20 * 0.28) / 1e6)

    def test_a_failed_call_is_an_error_not_an_empty_correction(self):
        result = self.build(RuntimeError("truncado")).correct(TEXT)
        self.assertEqual(result.edits, [])
        self.assertEqual(result.usage.calls, 1)
        self.assertIn("RuntimeError: truncado", result.errors)

    def test_an_unreadable_reply_is_an_error_but_still_costs(self):
        result = self.build("No he encontrado errores.").correct(TEXT)
        self.assertEqual(len(result.errors), 1)
        self.assertEqual(result.usage.calls, 1)
        self.assertGreater(result.usage.cost_usd, 0)

    def test_the_policy_travels_in_the_system_prompt(self):
        spy = {}
        self.build(edits_json(), spy).correct(TEXT)
        self.assertIn("edición mínima", spy["system"])
        self.assertTrue(spy["user"].startswith("[1]\n"))


if __name__ == "__main__":
    unittest.main()
