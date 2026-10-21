"""The migration that carries the bundled privacy text into older versions.

A version keeps the text it was created with. Only a text that is still
one of the bundled ones is replaced, by hash, whatever line endings the
browser form gave it. A text someone edited stays.
"""

from importlib import import_module
from unittest import mock

from django.apps import apps
from django.test import TestCase

from organisation.models import Erscheinungsbild
from organisation.privacy_text import mitgelieferter_datenschutztext

migration = import_module("organisation.migrations.0004_datenschutztext_nachgezogen")

ALT = "Alter mitgelieferter Text.\n\nMit zwei Absätzen."


class MitgelieferterTextWirdNachgezogen(TestCase):
    def nachziehen(self):
        alte = {"datenschutztext": {migration.pruefsumme(ALT)}, "datenschutztext_en": set()}
        with mock.patch.object(migration, "MITGELIEFERT", alte):
            migration.nachziehen(apps, None)

    def test_ein_alter_mitgelieferter_text_bekommt_den_neuen(self):
        fassung = Erscheinungsbild.standard().kopie("Eigene")
        # As a browser form sends it: Windows line endings.
        fassung.datenschutztext = ALT.replace("\n", "\r\n")
        fassung.datenschutztext_en = "Own English text."
        fassung.save()

        self.nachziehen()

        fassung.refresh_from_db()
        self.assertEqual(fassung.datenschutztext, mitgelieferter_datenschutztext("de"))
        self.assertEqual(fassung.datenschutztext_en, "Own English text.")

    def test_ein_eigener_text_bleibt(self):
        fassung = Erscheinungsbild.standard().kopie("Eigene")
        fassung.datenschutztext = "Unser eigener Text."
        fassung.save()

        self.nachziehen()

        fassung.refresh_from_db()
        self.assertEqual(fassung.datenschutztext, "Unser eigener Text.")

    def test_die_pruefsumme_kennt_jede_bisherige_fassung(self):
        """Five bundled texts per language since 2026-09-22, the current one is none of them."""
        for feld, sprache in migration.SPRACHE.items():
            with self.subTest(feld=feld):
                self.assertEqual(len(migration.MITGELIEFERT[feld]), 5)
                aktuell = migration.pruefsumme(mitgelieferter_datenschutztext(sprache))
                self.assertNotIn(aktuell, migration.MITGELIEFERT[feld])


nachtrag = import_module("organisation.migrations.0005_datenschutztext_backups_und_token")


class NachtragMitBackupsUndToken(TestCase):
    """'0005': the text with backups, tokens and the periods of 10/2026, '0004' text included."""

    def nachziehen(self, alte_de: set[str]):
        alte = {"datenschutztext": alte_de, "datenschutztext_en": set()}
        with mock.patch.object(nachtrag, "MITGELIEFERT", alte):
            nachtrag.nachziehen(apps, None)

    def test_der_text_aus_0004_bekommt_den_neuen(self):
        """What '0004' wrote is a bundled text too. A version that took it gets the new one."""
        fassung = Erscheinungsbild.standard().kopie("Eigene")
        fassung.datenschutztext = ALT.replace("\n", "\r\n")
        fassung.datenschutztext_en = "Own English text."
        fassung.save()

        self.nachziehen({nachtrag.pruefsumme(ALT)})

        fassung.refresh_from_db()
        self.assertEqual(fassung.datenschutztext, mitgelieferter_datenschutztext("de"))
        self.assertEqual(fassung.datenschutztext_en, "Own English text.")

    def test_ein_eigener_text_bleibt(self):
        fassung = Erscheinungsbild.standard().kopie("Eigene")
        fassung.datenschutztext = "Unser eigener Text."
        fassung.save()

        self.nachziehen({nachtrag.pruefsumme(ALT)})

        fassung.refresh_from_db()
        self.assertEqual(fassung.datenschutztext, "Unser eigener Text.")

    def test_die_pruefsumme_kennt_jede_bisherige_fassung_samt_der_aus_0004(self):
        """Every text '0004' knew, plus the one it wrote. The current one is none of them."""
        for feld, sprache in nachtrag.SPRACHE.items():
            with self.subTest(feld=feld):
                self.assertLess(migration.MITGELIEFERT[feld], nachtrag.MITGELIEFERT[feld])
                self.assertEqual(len(nachtrag.MITGELIEFERT[feld]), 6)
                aktuell = nachtrag.pruefsumme(mitgelieferter_datenschutztext(sprache))
                self.assertNotIn(aktuell, nachtrag.MITGELIEFERT[feld])

    def test_beide_rechnen_gleich(self):
        """The same hash in both, or a text from one would be a stranger to the other."""
        for text in (ALT, ALT.replace("\n", "\r\n"), f"  {ALT}\n"):
            with self.subTest(text=text):
                self.assertEqual(nachtrag.pruefsumme(text), migration.pruefsumme(text))


adresswechsel = import_module("organisation.migrations.0006_datenschutztext_adresswechsel")


class NachtragMitDemAdresswechsel(TestCase):
    """'0006': the text with the address that waits for its confirmation, '0005' text included."""

    def test_der_text_aus_0005_bekommt_den_neuen(self):
        """The text '0005' wrote is the one in operation since 2026-10-19."""
        fassung = Erscheinungsbild.standard().kopie("Eigene")
        fassung.datenschutztext = ALT.replace("\n", "\r\n")
        fassung.datenschutztext_en = ALT
        fassung.save()
        alte = {feld: {adresswechsel.pruefsumme(ALT)} for feld in adresswechsel.SPRACHE}

        with mock.patch.object(adresswechsel, "MITGELIEFERT", alte):
            adresswechsel.nachziehen(apps, None)

        fassung.refresh_from_db()
        self.assertEqual(fassung.datenschutztext, mitgelieferter_datenschutztext("de"))
        self.assertEqual(fassung.datenschutztext_en, mitgelieferter_datenschutztext("en"))

    def test_ein_eigener_text_bleibt(self):
        fassung = Erscheinungsbild.standard().kopie("Eigene")
        fassung.datenschutztext = "Unser eigener Text."
        fassung.save()

        adresswechsel.nachziehen(apps, None)

        fassung.refresh_from_db()
        self.assertEqual(fassung.datenschutztext, "Unser eigener Text.")

    def test_die_pruefsumme_kennt_jede_bisherige_fassung_samt_der_aus_0005(self):
        """Every text '0005' knew, plus the one it wrote. The current one is none of them."""
        for feld, sprache in adresswechsel.SPRACHE.items():
            with self.subTest(feld=feld):
                self.assertLess(nachtrag.MITGELIEFERT[feld], adresswechsel.MITGELIEFERT[feld])
                self.assertEqual(len(adresswechsel.MITGELIEFERT[feld]), 7)
                aktuell = adresswechsel.pruefsumme(mitgelieferter_datenschutztext(sprache))
                self.assertNotIn(aktuell, adresswechsel.MITGELIEFERT[feld])

    def test_der_text_nennt_die_wartende_adresse(self):
        self.assertIn("Bis du die neue bestätigst", mitgelieferter_datenschutztext("de"))
        self.assertIn("Until you confirm the new one", mitgelieferter_datenschutztext("en"))

    def test_alle_rechnen_gleich(self):
        """The same hash in all three, or a text from one would be a stranger to the next."""
        for text in (ALT, ALT.replace("\n", "\r\n"), f"  {ALT}\n"):
            with self.subTest(text=text):
                self.assertEqual(adresswechsel.pruefsumme(text), nachtrag.pruefsumme(text))
                self.assertEqual(adresswechsel.pruefsumme(text), migration.pruefsumme(text))
