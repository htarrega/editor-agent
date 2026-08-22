import unittest

from corrector.edits import apply_edits
from corrector.rules import mechanical_edits


def corrected(text):
    applied, rejected = apply_edits(text, mechanical_edits(text))
    assert not rejected, rejected
    return applied


def kinds(text):
    return sorted({edit.kind for edit in mechanical_edits(text)})


class Quotes(unittest.TestCase):
    def test_a_pair_becomes_angular(self):
        self.assertEqual(corrected('Dijo "hola" y se fue.'), "Dijo «hola» y se fue.")

    def test_two_pairs_alternate(self):
        self.assertEqual(corrected('"uno" y "dos"'), "«uno» y «dos»")

    def test_an_odd_number_is_left_alone(self):
        # There is no way to tell which of three marks is the unpaired one, and
        # a guess puts a closing mark where a quotation opens.
        self.assertEqual(corrected('"uno" y "dos'), '"uno" y "dos')

    def test_angular_quotes_are_already_right(self):
        self.assertEqual(mechanical_edits("Dijo «hola»."), [])


class Dashes(unittest.TestCase):
    def test_a_line_opening_with_a_hyphen_is_dialogue(self):
        self.assertEqual(corrected("-Vamos."), "—Vamos.")

    def test_an_incise_takes_the_dash_on_both_sides(self):
        self.assertEqual(corrected("—Vamos -dijo ella- y salió."), "—Vamos —dijo ella— y salió.")

    def test_a_compound_word_keeps_its_hyphen(self):
        # The whole reason the rule is written on whitespace rather than on the
        # character: `físico-químico` is a hyphen doing its own job.
        self.assertEqual(mechanical_edits("un estudio físico-químico"), [])

    def test_a_range_of_years_keeps_its_hyphen(self):
        self.assertEqual(mechanical_edits("la guerra de 1939-1945"), [])


class Spacing(unittest.TestCase):
    def test_a_space_before_the_mark_goes(self):
        self.assertEqual(corrected("Vino ella , y se fue."), "Vino ella, y se fue.")

    def test_a_missing_space_after_the_mark_arrives(self):
        self.assertEqual(corrected("Vino ella,y se fue."), "Vino ella, y se fue.")

    def test_a_decimal_is_not_a_comma_missing_a_space(self):
        self.assertEqual(mechanical_edits("costaba 3,50 euros"), [])


class OpeningSigns(unittest.TestCase):
    def test_a_question_gets_its_opening_sign(self):
        self.assertEqual(corrected("Puedes venir?"), "¿Puedes venir?")

    def test_an_exclamation_gets_its_own(self):
        self.assertEqual(corrected("Qué frío!"), "¡Qué frío!")

    def test_one_already_opened_is_left_alone(self):
        # The regression that made this rule worth writing twice: the opening
        # sign is itself skipped over when looking for where to insert, so
        # asking after the skip is asking exactly where it cannot be.
        self.assertEqual(mechanical_edits("¿Puedes venir?"), [])
        self.assertEqual(mechanical_edits("¡Qué frío!"), [])

    def test_a_mixed_pair_is_left_alone(self):
        self.assertEqual(mechanical_edits("¡Pero qué dices?"), [])

    def test_the_sign_opens_the_sentence_not_the_paragraph(self):
        self.assertEqual(corrected("Llegó tarde. Dónde estabas?"), "Llegó tarde. ¿Dónde estabas?")

    def test_a_dialogue_dash_stays_outside_the_sign(self):
        self.assertEqual(corrected("—Otra vez?"), "—¿Otra vez?")


class Capitals(unittest.TestCase):
    def test_a_sentence_opens_with_a_capital(self):
        self.assertEqual(corrected("Llegó tarde. no dijo nada."), "Llegó tarde. No dijo nada.")

    def test_an_abbreviation_does_not_close_a_sentence(self):
        self.assertEqual(mechanical_edits("naranjas, peras, etc. luego se fue"), [])

    def test_a_month_is_lowercase_mid_sentence(self):
        self.assertEqual(corrected("volvió en Marzo del año"), "volvió en marzo del año")

    def test_a_month_opening_a_sentence_keeps_its_capital(self):
        self.assertEqual(mechanical_edits("Marzo fue lluvioso."), [])


class NoFalsePositivesOnCleanProse(unittest.TestCase):
    """The check that the rules answer to the norm and not to the corruptor.

    Corpus B is the author's own untouched prose and the rules score 0 false
    positives on its 8,254 words (docs/PLAN.md, H4). That corpus is outside the
    repository, so what is pinned here is a passage carrying one of every
    construction the rules could misfire on.
    """

    CLEAN = (
        "—¿Puedes odiar a una mujer cuyo nombre no sabes pronunciar? —dijo ella—. "
        "Yo no.\n"
        "Volvió en marzo, con un estudio físico-químico bajo el brazo y 3,50 euros. "
        "La guerra de 1939-1945 le había enseñado algo: peras, manzanas, etc. no "
        "alimentan a nadie.\n"
        "«No es más que sidra», pensó. ¡Y qué sidra!\n"
    )

    def test_nothing_to_correct(self):
        self.assertEqual(mechanical_edits(self.CLEAN), [])


class EveryEditIsTyped(unittest.TestCase):
    def test_kinds_come_from_the_taxonomy(self):
        from corrector.taxonomy import ERROR_TYPES

        text = '-Vamos , dijo. "hola" Puedes venir? volvió en Marzo del año'
        edits = mechanical_edits(text)
        self.assertTrue(edits)
        for edit in edits:
            self.assertIn(edit.kind, ERROR_TYPES)
            self.assertTrue(edit.rule)

    def test_edits_come_back_in_text_order(self):
        text = '-Vamos , dijo. "hola" Puedes venir?'
        starts = [edit.start for edit in mechanical_edits(text)]
        self.assertEqual(starts, sorted(starts))


if __name__ == "__main__":
    unittest.main()
