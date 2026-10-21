"""The colours of the difference hang on the account, like appearance and language.

Colour vision is personal. Red and green are not distinct for everyone, and
in a check list the zero is the most frequent row. Switching off happens
through a class on the '<body>', not by the row losing its class: the code
that computes the difference should stay free of the question who is
looking.
"""

import json
from datetime import date
from unittest.mock import patch

from django.conf import settings
from django.template.loader import render_to_string
from django.test import TestCase
from django.urls import reverse

from accounts.models import CustomUser, Differenzfarben
from django_project.testing import administrator, inventur_anlegen, stylesheet
from organisation.models import Haus


class FarbwahlAmKonto(TestCase):
    def setUp(self):
        self.user = administrator()
        self.client.force_login(self.user)

    def test_voreingestellt_sind_alle_farben(self):
        self.assertEqual(self.user.differenzfarben, Differenzfarben.ALLE)

    def test_der_knopf_setzt_sie_und_kehrt_zurueck(self):
        ziel = reverse("haus_list")

        antwort = self.client.post(reverse("farbwahl"), {"farben": "aus", "weiter": ziel})

        self.assertRedirects(antwort, ziel)
        self.user.refresh_from_db()
        self.assertEqual(self.user.differenzfarben, Differenzfarben.AUS)

    def test_ein_unbekannter_wert_aendert_nichts(self):
        self.client.post(reverse("farbwahl"), {"farben": "bunt"})

        self.user.refresh_from_db()
        self.assertEqual(self.user.differenzfarben, Differenzfarben.ALLE)

    def test_ein_fremdes_ziel_wird_verworfen(self):
        """'weiter' is user input. Otherwise the button would be an open redirect."""
        antwort = self.client.post(
            reverse("farbwahl"), {"farben": "aus", "weiter": "https://example.org/"}
        )

        self.assertEqual(antwort["Location"], reverse("home"))

    def test_beide_leisten_bieten_die_wahl_an(self):
        for adresse in (reverse("inventur_list"), reverse("admin:index")):
            with self.subTest(adresse=adresse):
                antwort = self.client.get(adresse)
                self.assertContains(antwort, 'data-farben="alle"')
                self.assertContains(antwort, 'data-farben="abweichungen"')
                self.assertContains(antwort, 'data-farben="aus"')

    def test_die_gewaehlte_traegt_den_haken(self):
        """The template sets the tick through 'aria-current', in both bars."""
        self.user.differenzfarben = Differenzfarben.AUS
        self.user.save(update_fields=["differenzfarben"])

        for adresse in (reverse("inventur_list"), reverse("admin:index")):
            with self.subTest(adresse=adresse):
                seite = self.client.get(adresse).content.decode()

                self.assertRegex(seite, r'data-farben="aus"\s+aria-current="true"')
                self.assertRegex(seite, r'data-farben="alle"\s+aria-current="false"')

    def test_die_wahl_steht_als_klasse_in_der_seite(self):
        for wert in Differenzfarben.values:
            with self.subTest(wert=wert):
                self.user.differenzfarben = wert
                self.user.save(update_fields=["differenzfarben"])

                seite = self.client.get(reverse("inventur_list")).content.decode()

                self.assertIn(f"farben-{wert}", seite)

    def test_ohne_anmeldung_steht_dort_nichts(self):
        """The login page shows no differences. A class would claim a choice."""
        self.client.logout()

        seite = self.client.get(reverse("login")).content.decode()

        self.assertNotIn("farben-", seite)


class AufDemBlatt(TestCase):
    """Printing is for whoever presses the button."""

    def setUp(self):
        self.user = administrator()
        self.client.force_login(self.user)
        haus = Haus.objects.create(name="Kino Ost")
        self.inventur = inventur_anlegen(haus, stichtag=date(2026, 1, 31))
        self.inventur.zaehlung_starten()
        self.inventur.abschliessen()

    def adresse(self) -> str:
        return reverse("inventur_pdf", args=[self.inventur.pk])

    def test_die_wahl_des_konts_geht_in_die_vorlage(self):
        """Intercepted before WeasyPrint: the finished PDF contains only colour."""
        self.user.differenzfarben = Differenzfarben.AUS
        self.user.save(update_fields=["differenzfarben"])

        with patch("inventur.pdf.render_to_string", return_value="<html></html>") as gerendert:
            self.client.get(self.adresse())

        self.assertEqual(gerendert.call_args.args[1]["farben"], "aus")

    def test_die_vorlage_setzt_die_klasse(self):
        seite = render_to_string(
            "pdf/inventur.html", {"bloecke": [], "anzahl_waren": 0, "farben": "abweichungen"}
        )

        self.assertIn('<body class="farben-abweichungen">', seite)

    def test_das_blatt_entsteht_auch_ohne_farben(self):
        self.user.differenzfarben = Differenzfarben.AUS
        self.user.save(update_fields=["differenzfarben"])

        antwort = self.client.get(self.adresse())

        self.assertEqual(antwort["Content-Type"], "application/pdf")
        self.assertTrue(antwort.content.startswith(b"%PDF"))


class DieRegelnStehenInBeidenStylesheets(TestCase):
    """A class without a rule switches nothing off, and nobody would notice."""

    def test_der_haken_hat_seine_regel(self):
        """Without them the chosen colour carries 'aria-current' but nothing to see."""
        for name in ("site.css", "admin_site.css"):
            with self.subTest(stylesheet=name):
                self.assertIn('[data-farben][aria-current="true"]::before', stylesheet(name))

    def test_jede_abschaltung_hat_ihre_regel(self):
        for name, pfad in {
            "site.css": settings.BASE_DIR / "static" / "site.css",
            "pdf/base.html": settings.BASE_DIR / "templates" / "pdf" / "base.html",
        }.items():
            text = pfad.read_text(encoding="utf-8")
            for wert in (Differenzfarben.ABWEICHUNGEN, Differenzfarben.AUS):
                with self.subTest(stylesheet=name, wert=wert):
                    self.assertIn(f".farben-{wert.value} ", text)


class NurFuerDieOberflaeche(TestCase):
    """Counters do not see the choice: the counting app shows no differences."""

    def test_die_schnittstelle_nennt_sie_nicht(self):
        zaehler = CustomUser.objects.create_user(
            username="zaehler", email="zaehler@example.org", password="testpass1234"
        )
        self.client.force_login(zaehler)

        antwort = self.client.get("/api/v1/ich/")

        self.assertNotIn("differenzfarben", json.loads(antwort.content))
