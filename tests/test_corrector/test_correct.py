import json
import re
import threading
import time
import unittest

from corrector.blocks import block_spans
from corrector.correct import ASPECTS, FOCUS, Corrector, _windows, kinds_block, parse_edits, render
from corrector.edits import apply_edits, line_spans
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


def sequential(replies):
    """A generate function that answers each call from `replies` in turn.

    Unlike `fake`, it records every call it received rather than only the
    last one: per-block mode makes several, and a test needs to see all of
    them to check what each one was actually shown.
    """
    calls = []

    def generate(model, system, user):
        calls.append({"model": model, "system": system, "user": user})
        reply = replies[len(calls) - 1]
        if isinstance(reply, Exception):
            raise reply
        return Reply(text=reply, input_tokens=100, output_tokens=20, reasoning_tokens=8)

    generate.calls = calls
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

    def test_per_block_defaults_to_off(self):
        self.assertIsNone(Corrector("deepseek-v4-flash", fake(edits_json())).blocks_per_call)


class WholeDocumentAspect(unittest.TestCase):
    """`lean`'s trade: the same one call as `blocks`, a narrower brief."""

    def test_one_aspect_is_appended_to_the_only_call(self):
        spy = {}
        Corrector("deepseek-v4-flash", fake(edits_json(), spy), aspects=["juicio"]).correct(TEXT)
        self.assertTrue(spy["user"].endswith(ASPECTS["juicio"]))

    def test_no_aspect_leaves_the_brief_unnarrowed(self):
        spy = {}
        Corrector("deepseek-v4-flash", fake(edits_json(), spy)).correct(TEXT)
        for text in ASPECTS.values():
            self.assertNotIn(text, spy["user"])

    def test_more_than_one_aspect_has_nowhere_to_go(self):
        # Two aspects and one call: the second one was never asked for.
        corrector = Corrector(
            "deepseek-v4-flash", fake(edits_json()), aspects=["juicio", "ortografía"]
        )
        with self.assertRaises(ValueError):
            corrector.correct(TEXT)


TWO_BLOCKS = "El vasu de sidra.\n—Dijistes que vendrias —dijo el vasu.\n"


class PerBlockCorrectorPass(unittest.TestCase):
    """Opt-in mode: one call per block instead of one call for the document.

    ``per_block=False`` is the constructor default, so every test above this
    class already pins the unchanged path; these only exercise what changes
    when a caller opts in.
    """

    def build(self, generate, **kwargs):
        return Corrector("deepseek-v4-flash", generate, blocks_per_call=1, **kwargs)

    def test_default_construction_is_unaffected(self):
        # Same fixture and assertions as `test_applies_an_anchored_edit`
        # above, just spelled out here to pin that turning per_block on for
        # one Corrector cannot be mistaken for a change to the default.
        default = Corrector("deepseek-v4-flash", fake(edits_json()))
        self.assertIsNone(default.blocks_per_call)

    def test_issues_one_call_per_block(self):
        spans = block_spans(TWO_BLOCKS)
        self.assertEqual(len(spans), 2)
        generate = sequential([edits_json(), edits_json()])
        self.build(generate).correct(TWO_BLOCKS)
        self.assertEqual(len(generate.calls), 2)

    def test_each_call_renders_its_true_global_index(self):
        # The trap: rendering `render(text, [span])` alone would label every
        # one of these `[1]`, because `render` numbers from `enumerate(spans, 1)`
        # and does not know where the lone span it was given really sits.
        generate = sequential([edits_json(), edits_json()])
        self.build(generate).correct(TWO_BLOCKS)
        self.assertTrue(generate.calls[0]["user"].startswith("[1]\n"))
        self.assertTrue(generate.calls[1]["user"].startswith("[2]\n"))

    def test_a_proposal_resolves_inside_its_true_block_not_block_one(self):
        # "vasu" exists once in block 1 and once in block 2. A model shown
        # only its own block in isolation would, left to itself, answer
        # "line": 1 regardless of which block it actually saw — this is
        # exactly that reply for the call covering block 2. If block 2's
        # proposals were not stamped with their true index before resolving,
        # this would resolve against block 1's "vasu" instead, or not at all
        # once "vasu" stops being unique in the whole text.
        spans = block_spans(TWO_BLOCKS)
        replies = [edits_json(), edits_json({"line": 1, "original": "vasu", "replacement": "vaso"})]
        result = self.build(sequential(replies)).correct(TWO_BLOCKS)
        self.assertEqual(len(result.edits), 1)
        start = result.edits[0].start
        self.assertTrue(spans[1][0] <= start < spans[1][1])
        self.assertFalse(spans[0][0] <= start < spans[0][1])

    def test_resolves_against_true_block_spans_not_line_spans(self):
        # One line cut into several blocks: block numbering and line
        # numbering disagree here, which is exactly what papers over
        # `resolve_edits` being called without the block spans — it would
        # fall back to one span per *line*, and this line is only one span.
        text = (
            "Una frase corta. Otra frase distinta que sigue aqui. "
            "Y una tercera frase final aqui mismo.\n"
        )
        spans = block_spans(text, 4)
        self.assertGreater(len(spans), 1)
        self.assertLess(len(line_spans(text)), len(spans))

        last = len(spans)
        replies = [edits_json() for _ in range(last - 1)]
        replies.append(edits_json({"line": last, "original": "frase", "replacement": "frase,"}))
        result = self.build(sequential(replies), block_words=4).correct(text)

        self.assertEqual(len(result.edits), 1)
        start = result.edits[0].start
        self.assertTrue(spans[-1][0] <= start < spans[-1][1])

    def test_one_failing_block_does_not_lose_the_others_edits(self):
        item = {"line": 2, "original": "vasu", "replacement": "vaso"}
        replies = [RuntimeError("truncado"), edits_json(item)]
        result = self.build(sequential(replies)).correct(TWO_BLOCKS)
        self.assertEqual(len(result.edits), 1)
        # trim() shrinks "vasu"→"vaso" to its one differing letter, same as
        # the whole-document path does for the identical anchor.
        self.assertEqual(result.edits[0].replacement, "o")
        self.assertEqual(result.errors, ["RuntimeError: truncado"])

    def test_every_block_failing_is_visible_as_every_call_failing(self):
        replies = [RuntimeError("truncado"), RuntimeError("truncado")]
        result = self.build(sequential(replies)).correct(TWO_BLOCKS)
        self.assertEqual(result.edits, [])
        self.assertEqual(len(result.errors), result.usage.calls)
        self.assertEqual(result.usage.calls, 2)

    def test_partial_failure_is_visible_as_fewer_errors_than_calls(self):
        replies = [RuntimeError("truncado"), edits_json()]
        result = self.build(sequential(replies)).correct(TWO_BLOCKS)
        self.assertLess(len(result.errors), result.usage.calls)

    def test_usage_aggregates_across_calls(self):
        result = self.build(sequential([edits_json(), edits_json()])).correct(TWO_BLOCKS)
        self.assertEqual(result.usage.calls, 2)
        self.assertEqual(result.usage.input_tokens, 200)
        self.assertEqual(result.usage.output_tokens, 40)
        self.assertAlmostEqual(result.usage.cost_usd, 2 * (100 * 0.14 + 20 * 0.28) / 1e6)

    def test_usage_still_costs_a_call_that_raises(self):
        result = self.build(sequential([RuntimeError("truncado"), edits_json()])).correct(
            TWO_BLOCKS
        )
        self.assertEqual(result.usage.calls, 2)

    def test_proposed_and_rejected_aggregate_across_blocks(self):
        # Block 1: one malformed item. Block 2: one anchor invented outright.
        replies = [
            edits_json({"original": "sin replacement"}),
            edits_json({"line": 2, "original": "no existe", "replacement": "x"}),
        ]
        result = self.build(sequential(replies)).correct(TWO_BLOCKS)
        self.assertEqual(result.proposed, 2)
        self.assertEqual(result.rejected, {"malformed": 1, "anchor_not_found": 1})
        self.assertEqual(result.skipped, 2)
        self.assertEqual(result.edits, [])

    def test_an_unreadable_reply_from_one_block_is_an_error_not_fatal(self):
        replies = [
            "No he encontrado errores.",
            edits_json({"line": 2, "original": "vasu", "replacement": "vaso"}),
        ]
        result = self.build(sequential(replies)).correct(TWO_BLOCKS)
        self.assertEqual(len(result.edits), 1)
        self.assertEqual(
            result.errors, ["unparseable reply: no JSON in reply: 'No he encontrado errores.'"]
        )


class BatchedCorrectorPass(unittest.TestCase):
    """`blocks_per_call` greater than one: several blocks per call, still numbered
    from where they really sit in the document."""

    def corrector(self, size, replies):
        self.seen = []
        answers = iter(replies)

        def generate(model, system, user):
            self.seen.append(user)
            return Reply(text=next(answers))

        return Corrector("deepseek-v4-flash", generate, block_words=None, blocks_per_call=size)

    def test_batches_the_blocks_into_calls_of_the_given_size(self):
        text = "\n".join(f"linea {n}." for n in range(1, 8))  # 7 blocks
        self.corrector(3, [edits_json()] * 3).correct(text)
        self.assertEqual(len(self.seen), 3)
        markers = [re.findall(r"^\[(\d+)\]$", call, re.M) for call in self.seen]
        self.assertEqual(markers, [["1", "2", "3"], ["4", "5", "6"], ["7"]])

    def test_a_line_from_outside_the_batch_is_not_trusted(self):
        # Four blocks, two per call, and the second call answers with `line: 1`
        # — a block it was never shown. The anchor sits in block 1 *and* block 4,
        # so honouring that number would resolve the edit inside block 1, which
        # the call had nothing to say about. Refusing it sends the anchor to the
        # text-wide search, where two hits make it ambiguous and it is dropped.
        text = "la casa roja.\notra linea.\ntercera linea.\nla casa azul."
        item = {"line": 1, "original": "casa", "replacement": "casona"}
        corrector = self.corrector(2, [edits_json(), edits_json(item)])
        result = corrector.correct(text)
        self.assertEqual(result.edits, [])
        self.assertEqual(result.rejected, {"anchor_ambiguous": 1})

    def test_a_single_block_batch_still_stamps_the_line(self):
        text = "el gatto duerme.\nla casa roja."
        item = {"line": 99, "original": "casa", "replacement": "casona"}
        corrector = self.corrector(1, [edits_json(), edits_json(item)])
        result = corrector.correct(text)
        self.assertEqual(len(result.edits), 1)
        applied, _ = apply_edits(text, result.edits)
        self.assertEqual(applied, "el gatto duerme.\nla casona roja.")


class Windows(unittest.TestCase):
    """The ranges a windowed pass hands out. Every character belongs to exactly
    one window, which is what lets an edit be attributed by offset alone."""

    TEXT = "\n".join(f"linea {n} aqui." for n in range(1, 8))  # 7 blocks

    def test_the_owned_ranges_partition_the_text(self):
        windows = _windows(self.TEXT, block_spans(self.TEXT), 3)
        self.assertEqual(windows[0].start, 0)
        self.assertEqual(windows[-1].end, len(self.TEXT))
        for earlier, later in zip(windows, windows[1:]):
            self.assertEqual(earlier.end, later.start)

    def test_the_gap_between_two_blocks_belongs_to_the_later_window(self):
        # The newline between block 3 and block 4 is inside no block at all.
        # It has to fall on one side or the other, or an edit landing on it
        # would be dropped by both windows.
        spans = block_spans(self.TEXT)
        windows = _windows(self.TEXT, spans, 3)
        gap = spans[2][1]  # end of block 3, before the newline
        self.assertTrue(windows[0].start <= gap < windows[0].end)
        self.assertEqual(windows[1].start, spans[3][0])

    def test_without_context_every_window_is_shown_the_whole_document(self):
        spans = block_spans(self.TEXT)
        for window in _windows(self.TEXT, spans, 2):
            self.assertEqual(window.shown, spans)
            self.assertEqual(window.shown_first, 1)

    def test_context_narrows_what_is_shown_but_not_what_is_owned(self):
        spans = block_spans(self.TEXT)
        windows = _windows(self.TEXT, spans, 1, context=1)
        middle = windows[3]  # owns block 4 alone
        self.assertEqual((middle.first, middle.last), (4, 4))
        # Blocks 3, 4 and 5 are shown, numbered from 3.
        self.assertEqual(middle.shown, spans[2:5])
        self.assertEqual(middle.shown_first, 3)

    def test_context_does_not_run_off_either_end(self):
        spans = block_spans(self.TEXT)
        windows = _windows(self.TEXT, spans, 1, context=99)
        self.assertEqual(windows[0].shown_first, 1)
        self.assertEqual(windows[-1].shown, spans)


class WindowedCorrectorPass(unittest.TestCase):
    """`window_blocks`: the calls split the *responsibility* and share the context.

    The point of the mode is that every call still sees the whole document, so
    the tests here are mostly about what stops two calls that read the same
    text from both acting on it.
    """

    TEXT = "\n".join(f"linea {n} aqui." for n in range(1, 8))

    def corrector(self, size, replies, **kwargs):
        self.seen = []
        answers = iter(replies)

        def generate(model, system, user):
            self.seen.append(user)
            return Reply(text=next(answers))

        return Corrector(
            "deepseek-v4-flash", generate, block_words=None, window_blocks=size, **kwargs
        )

    def test_default_construction_is_unaffected(self):
        self.assertIsNone(Corrector("deepseek-v4-flash", fake(edits_json())).window_blocks)

    def test_one_call_per_window(self):
        self.corrector(3, [edits_json()] * 3).correct(self.TEXT)
        self.assertEqual(len(self.seen), 3)

    def test_every_call_carries_the_whole_document(self):
        # This is the whole difference from `blocks_per_call`, which shows a
        # call its own blocks and nothing else — and pays 0.039 F0.5 for it.
        self.corrector(3, [edits_json()] * 3).correct(self.TEXT)
        for call in self.seen:
            markers = re.findall(r"^\[(\d+)\]$", call, re.M)
            self.assertEqual(markers, [str(n) for n in range(1, 8)])

    def test_each_call_is_told_which_blocks_are_its_own(self):
        self.corrector(3, [edits_json()] * 3).correct(self.TEXT)
        wanted = [
            FOCUS.format(first=1, last=3),
            FOCUS.format(first=4, last=6),
            FOCUS.format(first=7, last=7),
        ]
        self.assertEqual([call[-len(w) :] for call, w in zip(self.seen, wanted)], wanted)

    def test_an_edit_outside_its_window_is_dropped(self):
        # Every call reads the whole text, so every call can see the error in
        # block 1. Only the window that owns block 1 may act on it; without
        # that the edit would be proposed three times and applied twice.
        item = {"line": 1, "original": "linea 1", "replacement": "línea 1"}
        result = self.corrector(3, [edits_json(item)] * 3).correct(self.TEXT)
        self.assertEqual(len(result.edits), 1)
        self.assertEqual(result.rejected["out_of_window"], 2)

    def test_ownership_is_decided_on_the_offset_not_on_the_reported_line(self):
        # The model puts the right anchor under a line number from another
        # window. The anchor is what resolves, so the edit belongs to whoever
        # owns where it landed — here the second call, which claimed it.
        item = {"line": 1, "original": "linea 5", "replacement": "línea 5"}
        result = self.corrector(3, [edits_json(), edits_json(item), edits_json()]).correct(
            self.TEXT
        )
        self.assertEqual(len(result.edits), 1)
        applied, _ = apply_edits(self.TEXT, result.edits)
        self.assertIn("línea 5", applied)

    def test_one_failing_window_does_not_lose_the_others_edits(self):
        item = {"line": 5, "original": "linea 5", "replacement": "línea 5"}
        replies = [edits_json(), edits_json(item), edits_json()]
        self.seen = []
        answers = iter(replies)

        def generate(model, system, user):
            self.seen.append(user)
            reply = next(answers)
            if len(self.seen) == 1:
                raise RuntimeError("truncado")
            return Reply(text=reply)

        corrector = Corrector("deepseek-v4-flash", generate, block_words=None, window_blocks=3)
        result = corrector.correct(self.TEXT)
        self.assertEqual(len(result.edits), 1)
        self.assertEqual(result.errors, ["RuntimeError: truncado"])
        self.assertLess(len(result.errors), result.usage.calls)

    def test_context_blocks_shows_a_neighbourhood_instead_of_the_document(self):
        self.corrector(1, [edits_json()] * 7, context_blocks=1).correct(self.TEXT)
        markers = re.findall(r"^\[(\d+)\]$", self.seen[3], re.M)
        self.assertEqual(markers, ["3", "4", "5"])

    def test_a_windowed_pass_cannot_also_be_a_batched_one(self):
        with self.assertRaises(ValueError):
            Corrector("deepseek-v4-flash", fake(edits_json()), blocks_per_call=2, window_blocks=2)


class RacedCorrectorPass(unittest.TestCase):
    """`attempts` and `deadline`: the same call issued several times, first wins.

    The model's deliberation is a lottery — median 4.3 s, maximum 19 — so the
    wall clock of a pass is its slowest call and not its average one
    (docs/PLAN.md). These pin the three properties that turn that into a bound:
    redundancy, a clock, and a floor that is queued before either.
    """

    TEXT = "\n".join(f"linea {n} aqui." for n in range(1, 5))

    def corrector(self, answer, **kwargs):
        """`answer(index, attempt)` decides what each ticket does."""
        self.calls = []
        lock = threading.Lock()

        def generate(model, system, user):
            with lock:
                attempt = sum(1 for seen in self.calls if seen == user)
                self.calls.append(user)
            return answer(user, attempt)

        return Corrector(
            "deepseek-v4-flash",
            generate,
            block_words=None,
            window_blocks=1,
            concurrency=16,
            **kwargs,
        )

    def test_the_first_answer_wins_and_the_slow_ones_are_not_waited_for(self):
        edit = {"line": 1, "original": "linea 1", "replacement": "línea 1"}

        def answer(user, attempt):
            if attempt == 0:
                time.sleep(5)  # never collected: the deadline fires first
            return Reply(text=edits_json(edit))

        started = time.monotonic()
        result = self.corrector(answer, attempts=2, deadline=1.0).correct(self.TEXT)
        self.assertLess(time.monotonic() - started, 4)
        self.assertEqual(result.errors, [])

    def test_a_window_with_no_answer_by_the_deadline_is_an_error_not_a_silence(self):
        # The confusion `parse_edits` refuses to make, one level down: a block
        # nobody read must not look like a block with nothing wrong in it.
        def answer(user, attempt):
            time.sleep(5)
            return Reply(text=edits_json())

        result = self.corrector(answer, attempts=1, deadline=0.5).correct(self.TEXT)
        self.assertEqual(result.edits, [])
        self.assertEqual(len(result.errors), result.usage.calls)
        self.assertTrue(all("sin respuesta" in e for e in result.errors))

    def test_the_hurried_ticket_is_the_floor_and_never_the_preference(self):
        # Both come back; the deliberated one has to be the one that is read.
        def hurried(model, system, user):
            return Reply(text=edits_json({"line": 1, "original": "aqui", "replacement": "AQUI"}))

        def answer(user, attempt):
            return Reply(text=edits_json({"line": 1, "original": "aqui", "replacement": "aquí"}))

        result = self.corrector(answer, attempts=1, deadline=2.0, fallback=hurried).correct(
            self.TEXT
        )
        self.assertTrue(result.edits)
        self.assertTrue(all(edit.replacement == "í" for edit in result.edits))

    def test_the_hurried_ticket_is_read_when_the_deliberated_ones_run_long(self):
        def hurried(model, system, user):
            return Reply(text=edits_json({"line": 1, "original": "aqui", "replacement": "aquí"}))

        def answer(user, attempt):
            time.sleep(5)
            return Reply(text=edits_json())

        result = self.corrector(answer, attempts=1, deadline=0.8, fallback=hurried).correct(
            self.TEXT
        )
        self.assertTrue(result.edits)
        self.assertEqual(result.errors, [])

    def test_results_stay_in_window_order(self):
        # Racing resolves out of order by construction; what is written from it
        # must not. Same argument as `evals/run.py:correct_all`.
        def answer(user, attempt):
            number = re.search(r"bloques \[(\d+)\]", user).group(1)
            time.sleep(0.05 * (4 - int(number)))
            return Reply(text=edits_json({"original": f"linea {number}", "replacement": "L"}))

        result = self.corrector(answer, attempts=1, deadline=3.0).correct(self.TEXT)
        starts = [edit.start for edit in result.edits]
        self.assertEqual(starts, sorted(starts))


if __name__ == "__main__":
    unittest.main()
