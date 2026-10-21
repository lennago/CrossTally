"""Numbers from forms, parameters and addresses ('ganzzahl').

'str.isdigit' says yes to "²", and 'int' then raises a 'ValueError'. Until
10/2026 that was a server error in some thirty places, at the icons even
without signing in.
"""

from django.http import Http404
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import CustomUser
from inventur.views.sollbestand import _nummer
from katalog.formats import FORMATE
from katalog.models import nummer_sortierschluessel
from organisation.models import Bereich, Haus, Ort

from .testing import fuehrungskraft
from .validation import ganzzahl


class Ganzzahl(SimpleTestCase):
    def test_die_zehn_ziffern(self):
        self.assertEqual(ganzzahl("0"), 0)
        self.assertEqual(ganzzahl("101"), 101)
        self.assertEqual(ganzzahl(7), 7)

    def test_alles_andere_ist_keine(self):
        for wert in ("", None, "²", "1²", "١٢", "-1", " 1", "1.0", "abc", "1e3"):
            with self.subTest(wert=wert):
                self.assertIsNone(ganzzahl(wert))

    def test_eine_artikelnummer_mit_zwanzig_ziffern_ist_eine(self):
        self.assertEqual(ganzzahl("1" * 20), int("1" * 20))

    def test_eine_laengere_ist_keine(self):
        """Above 4,300 digits 'int' raised a 'ValueError' until 10/2026, a server error."""
        for wert in ("1" * 21, "1" * 4301):
            with self.subTest(laenge=len(wert)):
                self.assertIsNone(ganzzahl(wert))


class LangeZahlen(TestCase):
    """A number with thousands of digits where the page expects one: no server error."""

    @classmethod
    def setUpTestData(cls):
        cls.leitung = fuehrungskraft()

    def test_das_icon_ohne_anmeldung(self):
        self.client.raise_request_exception = False
        antwort = self.client.get(reverse("icon", kwargs={"name": "logo"}), {"von": "1" * 4301})
        self.assertEqual(antwort.status_code, 302)


class HochgestellteZiffern(TestCase):
    """A superscript digit where a number is expected: no page, no server error."""

    @classmethod
    def setUpTestData(cls):
        cls.leitung = fuehrungskraft()
        cls.ort = Ort.objects.create(
            bereich=Bereich.objects.create(haus=Haus.objects.create(name="Kino Nord"), name="B"),
            name="Theke",
        )

    def setUp(self):
        self.client.raise_request_exception = False

    def test_das_icon_ohne_anmeldung(self):
        antwort = self.client.get(reverse("icon", kwargs={"name": "logo"}), {"von": "²"})
        self.assertEqual(antwort.status_code, 302)

    def test_die_pfadleiste(self):
        self.client.force_login(self.leitung)
        antwort = self.client.get(reverse("ort_detail", args=[self.ort.pk]), {"von": "haus:²"})
        self.assertEqual(antwort.status_code, 200)

    def test_die_schnittstelle(self):
        api = APIClient()
        api.force_authenticate(CustomUser.objects.get(pk=self.leitung.pk))
        antwort = api.get("/api/v1/zaehlpositionen/", {"inventur": "²"})
        self.assertEqual(antwort.status_code, 400)

    def test_eine_zeile_der_sollklaerung(self):
        anfrage = RequestFactory().post("/", {"zeile": "²"})
        with self.assertRaises(Http404):
            _nummer(anfrage, "zeile")


class ArtikelnummernMitHochgestellterZiffer(SimpleTestCase):
    def test_das_format_nimmt_keine_nummer_die_so_beginnt(self):
        self.assertIsNone(FORMATE["winticket"].artikelnummer("²01"))

    def test_die_sortierung_stolpert_nicht(self):
        """The number "101²" got through the format and then broke the sheet of the inventory."""
        nummern = sorted(["101²", "1012", "101"], key=nummer_sortierschluessel)
        self.assertEqual(nummern, ["101", "101²", "1012"])
