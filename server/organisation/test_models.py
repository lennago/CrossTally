"""Houses, areas and locations: what the model itself promises."""

from django.db import IntegrityError, transaction
from django.test import TestCase

from django_project.testing import bereich

from .models import Bereich, Haus, Ort


class HausModelTests(TestCase):
    def test_str_ist_der_name(self):
        haus = Haus.objects.create(name="Kino Nord")
        self.assertEqual(str(haus), "Kino Nord")

    def test_get_absolute_url(self):
        haus = Haus.objects.create(name="Kino Nord")
        self.assertEqual(haus.get_absolute_url(), f"/haeuser/{haus.pk}/")

    def test_ordering_nach_name(self):
        Haus.objects.create(name="Kino West")
        Haus.objects.create(name="Kino Nord")
        self.assertEqual([h.name for h in Haus.objects.all()], ["Kino Nord", "Kino West"])


class OrtModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.haus = Haus.objects.create(name="Kino Nord")

    def test_str_nennt_haus_und_bereich(self):
        ort = Ort.objects.create(name="Theke A", bereich=bereich(self.haus, "Theke"))
        self.assertEqual(str(ort), "Kino Nord - Theke - Theke A")

    def test_haus_kommt_ueber_den_bereich(self):
        """'Ort.haus' is a property, not a second field, otherwise they would drift apart."""
        ort = Ort.objects.create(name="Theke A", bereich=bereich(self.haus))
        self.assertEqual(ort.haus, self.haus)
        self.assertFalse(any(feld.name == "haus" for feld in Ort._meta.get_fields()))

    def test_get_absolute_url(self):
        ort = Ort.objects.create(name="Theke A", bereich=bereich(self.haus))
        self.assertEqual(ort.get_absolute_url(), f"/haeuser/orte/{ort.pk}/")

    def test_ortsname_je_bereich_eindeutig(self):
        Ort.objects.create(name="Lager", bereich=bereich(self.haus))
        with self.assertRaises(IntegrityError), transaction.atomic():
            Ort.objects.create(name="Lager", bereich=bereich(self.haus))

    def test_gleicher_ortsname_in_anderem_bereich_erlaubt(self):
        """A "Freezer" may exist at the counter and in storage."""
        Ort.objects.create(name="Gefrierfach", bereich=bereich(self.haus, "Theke"))
        Ort.objects.create(name="Gefrierfach", bereich=bereich(self.haus, "Lager"))
        self.assertEqual(Ort.objects.filter(name="Gefrierfach").count(), 2)

    def test_gleicher_ortsname_in_anderem_haus_erlaubt(self):
        anderes_haus = Haus.objects.create(name="Kino Süd")
        Ort.objects.create(name="Lager", bereich=bereich(self.haus))
        Ort.objects.create(name="Lager", bereich=bereich(anderes_haus))
        self.assertEqual(Ort.objects.filter(name="Lager").count(), 2)

    def test_haus_loeschen_nimmt_bereiche_und_orte_mit(self):
        Ort.objects.create(name="Lager", bereich=bereich(self.haus))
        self.haus.delete()
        self.assertEqual(Ort.objects.count(), 0)
        self.assertEqual(Bereich.objects.count(), 0)

    def test_bereich_loeschen_nimmt_seine_orte_mit(self):
        theke = bereich(self.haus, "Theke")
        Ort.objects.create(name="Thekenfront", bereich=theke)
        Ort.objects.create(name="Gefrierfach", bereich=theke)
        theke.delete()
        self.assertEqual(Ort.objects.count(), 0)


class BereichModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.haus = Haus.objects.create(name="Kino Nord")

    def test_str_nennt_das_haus(self):
        self.assertEqual(str(bereich(self.haus, "Theke")), "Kino Nord - Theke")

    def test_name_je_haus_eindeutig(self):
        Bereich.objects.create(name="Theke", haus=self.haus)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Bereich.objects.create(name="Theke", haus=self.haus)

    def test_gleicher_name_in_anderem_haus_erlaubt(self):
        Bereich.objects.create(name="Theke", haus=self.haus)
        Bereich.objects.create(name="Theke", haus=Haus.objects.create(name="Kino Süd"))
        self.assertEqual(Bereich.objects.filter(name="Theke").count(), 2)

    def test_alle_orte_des_hauses_ueber_die_bereiche(self):
        Ort.objects.create(name="Thekenfront", bereich=bereich(self.haus, "Theke"))
        Ort.objects.create(name="Trockenlager", bereich=bereich(self.haus, "Lager"))
        self.assertEqual(
            sorted(self.haus.alle_orte().values_list("name", flat=True)),
            ["Thekenfront", "Trockenlager"],
        )
