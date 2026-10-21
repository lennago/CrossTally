"""The forms for areas and locations: duplicate names and the choice of area."""

from django.test import TestCase

from django_project.form_fields import wahlmenge
from django_project.testing import bereich

from .forms import BereichForm, OrtForm
from .models import Bereich, Haus, Ort


class OrtFormTests(TestCase):
    def setUp(self):
        self.haus = Haus.objects.create(name="Kino Nord")
        self.lager = bereich(self.haus, "Lager")
        Ort.objects.create(name="Trockenlager", bereich=self.lager)

    def daten(self, name, ziel=None):
        return {"name": name, "bereich": (ziel or self.lager).pk}

    def test_doppelter_name_wird_zum_formularfehler(self):
        form = OrtForm(self.daten("Trockenlager"), haus=self.haus)
        self.assertFalse(form.is_valid())
        self.assertIn("name", form.errors)

    def test_doppelter_name_auch_bei_anderer_schreibweise(self):
        self.assertFalse(OrtForm(self.daten("trockenlager"), haus=self.haus).is_valid())

    def test_name_in_anderem_bereich_ist_frei(self):
        theke = bereich(self.haus, "Theke")
        self.assertTrue(OrtForm(self.daten("Trockenlager", theke), haus=self.haus).is_valid())

    def test_eigener_name_blockiert_das_bearbeiten_nicht(self):
        ort = Ort.objects.get(name="Trockenlager")
        form = OrtForm(self.daten("Trockenlager"), instance=ort, haus=self.haus)
        self.assertTrue(form.is_valid(), form.errors)

    def test_nur_bereiche_des_eigenen_hauses_stehen_zur_wahl(self):
        """Otherwise a location could be hung into a foreign house."""
        fremd = bereich(Haus.objects.create(name="Kino Süd"), "Theke")
        form = OrtForm(haus=self.haus)
        self.assertIn(self.lager, wahlmenge(form, "bereich"))
        self.assertNotIn(fremd, wahlmenge(form, "bereich"))


class BereichFormTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.haus = Haus.objects.create(name="Kino Nord")
        Bereich.objects.create(name="Theke", haus=cls.haus)

    def test_doppelter_name_wird_zum_formularfehler(self):
        form = BereichForm(data={"name": "theke"}, haus=self.haus)
        self.assertFalse(form.is_valid())
        self.assertIn("name", form.errors)

    def test_name_in_anderem_haus_ist_frei(self):
        form = BereichForm(data={"name": "Theke"}, haus=Haus.objects.create(name="Kino Süd"))
        self.assertTrue(form.is_valid())
