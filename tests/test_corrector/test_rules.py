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


class Spelling(unittest.TestCase):
    """The dictionary rule. It fires only where a word is not Spanish *and* one
    minimal repair makes it Spanish — which is what keeps it off the author."""

    def test_a_missing_accent_is_restored(self):
        self.assertEqual(corrected("Ella corrio hasta alli"), "Ella corrió hasta allí")

    def test_a_missing_initial_h_is_restored(self):
        self.assertEqual(corrected("era un ombre viejo"), "era un hombre viejo")

    def test_a_b_for_a_v_is_swapped_back(self):
        self.assertEqual(corrected("no huvo manera"), "no hubo manera")

    def test_an_invented_word_is_left_alone(self):
        # The whole point. These are the author's, they are not in the
        # dictionary, and no accent, no h and no b puts them there — so being
        # unknown is never on its own a reason to touch a word.
        for word in ("vasu", "pumarada", "merequetengue", "gomitar"):
            with self.subTest(word=word):
                self.assertEqual(mechanical_edits(f"tomó el {word} entero"), [])

    def test_an_accent_the_author_wrote_is_never_stripped(self):
        # `ojalá` and `jamás` are not in the dictionary and `ojala` and `jamas`
        # are, as forms of `ojalar` and `jamar`. Removing an accent is a claim
        # about the author's writing and the dictionary cannot make it.
        self.assertEqual(mechanical_edits("ojalá vuelva"), [])
        self.assertEqual(mechanical_edits("jamás volvió"), [])

    def test_a_verb_with_pronouns_stuck_to_it_is_not_a_misspelling(self):
        # `irme` is not in the dictionary and `hirme` is. Without the enclitic
        # guard every attached-pronoun form in the corpus is a word looking for
        # a repair, and it finds one.
        for word in ("irme", "decirle", "contarlo", "dárselo"):
            with self.subTest(word=word):
                self.assertEqual(mechanical_edits(f"quiso {word} despacio"), [])

    def test_a_word_two_repairs_from_two_words_is_left_to_the_model(self):
        # Choosing between them needs the sentence, and reading the sentence is
        # not something a regular expression does.
        edits = mechanical_edits("vino el sabado")
        self.assertTrue(all(edit.kind != "tilde" or edit.replacement == "sábado" for edit in edits))

    def test_a_name_mid_sentence_is_not_a_word_the_dictionary_judges(self):
        self.assertEqual(mechanical_edits("se lo dijo a Olaya Noriega"), [])

    def test_a_repaired_word_keeps_the_capital_it_needs(self):
        # Both rules want the same characters: the word is misspelled *and*
        # opens a sentence. The repair spans the whole word, so it carries both.
        self.assertEqual(corrected("Se fue. tambien lloró."), "Se fue. También lloró.")


class SecondPersonPreterite(unittest.TestCase):
    """Spanish has no «-stes». Complete rather than heuristic: there is no verb
    for which the form is right, so the rule cannot be incomplete."""

    def test_the_analogical_s_goes(self):
        self.assertEqual(corrected("¿Ya dijistes que sí?"), "¿Ya dijiste que sí?")

    def test_a_real_word_ending_in_stes_is_left_alone(self):
        # `restes` is the subjunctive of `restar` and a word in its own right.
        self.assertEqual(mechanical_edits("no restes puntos"), [])


class GeneralisesBeyondTheCorpus(unittest.TestCase):
    """The rules have to hold on prose that is nothing like the eval corpus.

    The corpus is a sample of one author, four fragments and 8,254 words, and
    every number in docs/PLAN.md is measured on it. A rule tuned until that
    sample is clean has learned the sample. So this passage is deliberately
    unlike it — a different register, different subject matter, and one of
    every construction the rules could plausibly misfire on — and the bar is
    the same as corpus B's: **nothing to correct**.

    Anything that fails here is a rule that would damage a real manuscript, and
    it fails before a paid run ever happens.
    """

    UNLIKE_THE_CORPUS = (
        "El Consejo aprobó el reglamento el 3 de octubre de 2019 por 27 votos a 4. "
        "La comisión —presidida entonces por la doctora Alarcón— publicó un informe "
        "de 412 páginas: «la evidencia es concluyente», concluía.\n"
        "¿Qué decía el texto? Que el sistema de pesaje ítalo-suizo, vigente desde "
        "1994-1998, no cumplía la norma UNE 21-302. ¡Y que nadie lo había "
        "comprobado!\n"
        "El agua, el alma y el hacha llevan artículo masculino; la mano y la foto "
        "no. Ni el problema ni el mapa son femeninos, aunque lo parezcan.\n"
        "Ojalá se resuelva. Jamás se resolvió. Quiso irme a buscar, decirle algo, "
        "contarlo todo; no supo cómo. Etc. no es una frase.\n"
        "El pediatra y la pediatra discreparon: 3,50 puntos de diferencia, ni más "
        "ni menos.\n"
    )

    def test_nothing_to_correct(self):
        found = mechanical_edits(self.UNLIKE_THE_CORPUS)
        self.assertEqual(
            [(e.kind, e.before(self.UNLIKE_THE_CORPUS), e.replacement) for e in found], []
        )

    def test_it_still_corrects_real_errors_in_the_same_register(self):
        # The other half of the bar: conservative is not the same as inert.
        broken = 'El Consejo aprobo el reglamento ,y dijo "basta" . Cuando llegara?'
        # `aprobo` stays: the dictionary does not hold `aprobó`, and an
        # incomplete dictionary costs recall rather than precision. `Cuando`
        # stays too, on purpose — the interrogative accent is `tilde_diacritica`,
        # both spellings are words, and no rule decides it.
        self.assertEqual(
            corrected(broken),
            "El Consejo aprobo el reglamento, y dijo «basta». ¿Cuando llegara?",
        )


if __name__ == "__main__":
    unittest.main()
