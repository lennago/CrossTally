"""Comma as well as point when reading, the language's separator when writing."""

from decimal import Decimal

from django import forms
from django.test import SimpleTestCase
from django.utils import translation

from katalog.models import menge_kurz, menge_mit_vorzeichen

from .decimals import dezimal_normalisieren, dezimaltrenner
from .form_fields import Dezimalfeld


class Lesen(SimpleTestCase):
    def test_der_letzte_trenner_ist_der_dezimaltrenner(self):
        for roh, erwartet in (
            ("1,5", "1.5"),
            ("1.5", "1.5"),
            ("1.234,5", "1234.5"),
            ("1,234.5", "1234.5"),
            ("1.234.567,25", "1234567.25"),
            ("1,234,567.25", "1234567.25"),
            ("-1.279,00", "-1279.00"),
            (" 12 ", "12"),
            ("", ""),
        ):
            with self.subTest(roh=roh):
                self.assertEqual(dezimal_normalisieren(roh), erwartet)

    def test_das_feld_nimmt_beides_in_jeder_sprache(self):
        feld = Dezimalfeld(max_digits=12, decimal_places=3, localize=True)
        for sprache in ("de", "en"):
            with translation.override(sprache):
                for roh, erwartet in (
                    ("1,5", "1.5"),
                    ("1.5", "1.5"),
                    ("1.234,5", "1234.5"),
                    ("1,234.5", "1234.5"),
                ):
                    with self.subTest(sprache=sprache, roh=roh):
                        self.assertEqual(feld.clean(roh), Decimal(erwartet))
                with self.assertRaises(forms.ValidationError):
                    feld.clean("viel")


class Feld(SimpleTestCase):
    """The field is a text field, also where nobody asks for 'localize'.

    Until 10/2026 such a field became '<input type="number">', and with an
    English interface Chrome refused "1,5" before the server saw it.
    """

    class Formular(forms.Form):
        menge = Dezimalfeld(max_digits=10, decimal_places=3, required=False)
        eigenes = Dezimalfeld(
            max_digits=10,
            decimal_places=3,
            required=False,
            widget=forms.TextInput(attrs={"inputmode": "numeric"}),
        )

    def test_ohne_angabe_ein_textfeld_mit_zahlentastatur(self):
        html = str(self.Formular()["menge"])
        self.assertIn('type="text"', html)
        self.assertIn('inputmode="decimal"', html)
        self.assertNotIn('type="number"', html)
        self.assertNotIn("step=", html)

    def test_ein_eigenes_inputmode_bleibt(self):
        self.assertIn('inputmode="numeric"', str(self.Formular()["eigenes"]))

    def test_ein_gespeicherter_wert_steht_kurz_in_der_sprache_des_kontos(self):
        """A stored "1,000" reads as a thousand to whoever knows the point as the separator."""
        for sprache, trenner in (("de", ","), ("en", ".")):
            for gespeichert, erwartet in (
                ("1.500", f"1{trenner}5"),
                ("1.000", "1"),
                ("250.000", "250"),
                ("0.001300000000", f"0{trenner}0013"),
            ):
                with translation.override(sprache), self.subTest(sprache=sprache, wert=gespeichert):
                    formular = self.Formular(initial={"menge": Decimal(gespeichert)})
                    self.assertIn(f'value="{erwartet}"', str(formular["menge"]))

    def test_was_getippt_wurde_bleibt_wie_es_war(self):
        formular = self.Formular({"menge": "1,500", "eigenes": "viel"})
        self.assertIn('value="1,500"', str(formular["menge"]))
        self.assertIn('value="viel"', str(formular["eigenes"]))

    def test_liest_komma_und_punkt_auch_ohne_ausdrueckliches_localize(self):
        for sprache in ("de", "en"):
            for roh in ("1,5", "1.5"):
                with translation.override(sprache), self.subTest(sprache=sprache, roh=roh):
                    formular = self.Formular({"menge": roh, "eigenes": ""})
                    self.assertTrue(formular.is_valid(), formular.errors)
                    self.assertEqual(formular.cleaned_data["menge"], Decimal("1.5"))


class Schreiben(SimpleTestCase):
    def test_die_anzeige_folgt_der_sprache(self):
        for sprache, trenner in (("de", ","), ("en", ".")):
            with translation.override(sprache), self.subTest(sprache=sprache):
                self.assertEqual(dezimaltrenner(), trenner)
                self.assertEqual(menge_kurz(Decimal("1.500")), f"1{trenner}5")
                self.assertEqual(menge_kurz(Decimal("24.000")), "24")
                self.assertEqual(menge_mit_vorzeichen(Decimal("-0.250")), f"-0{trenner}25")
                self.assertEqual(menge_mit_vorzeichen(Decimal("3")), "+3")
