"""The breadcrumb bar from 'von': every origin leads back to its page, a gone one nowhere."""

from django.test import TestCase
from django.urls import reverse

from katalog.models import (
    Basiseinheit,
    Dimension,
    Hausartikel,
    Rezepturartikel,
    Ware,
    Warengruppe,
)
from organisation.models import Haus, Ort

from .navigation import HERKUENFTE, stationen
from .testing import bereich


class JedeHerkunft(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.haus = Haus.objects.create(name="Kino Nord")
        cls.bereich = bereich(cls.haus)
        cls.ort = Ort.objects.create(name="Theke", bereich=cls.bereich)
        stueck = Basiseinheit.objects.get(dimension=Dimension.STUECK)
        cls.gruppe = Warengruppe.objects.create(name="Warengruppe R")
        cls.ware = Ware.objects.create(name="Ware N", basiseinheit=stueck)
        cls.rezeptur = Rezepturartikel.objects.create(name="Portion N", basiseinheit=stueck)
        cls.artikel = Hausartikel.objects.create(
            haus=cls.haus,
            ware=cls.ware,
            warengruppe=cls.gruppe,
            artikelnummer="101001",
            name="Artikel C",
        )

    def ziele(self) -> dict[str, tuple[int, str]]:
        """Key in 'von' -> (primary key, address of the last station)."""
        return {
            "haus": (self.haus.pk, self.haus.get_absolute_url()),
            "haus_artikel": (self.haus.pk, reverse("hausartikel_list", args=[self.haus.pk])),
            "bereich": (self.bereich.pk, self.bereich.get_absolute_url()),
            "ort": (self.ort.pk, self.ort.get_absolute_url()),
            "warengruppe": (self.gruppe.pk, self.gruppe.get_absolute_url()),
            "ware": (self.ware.pk, self.ware.get_absolute_url()),
            "rezepturartikel": (self.rezeptur.pk, self.rezeptur.get_absolute_url()),
            "hausartikel": (self.artikel.pk, self.artikel.get_absolute_url()),
        }

    def test_jede_herkunft_ist_hier_geprueft(self):
        self.assertEqual(set(self.ziele()), set(HERKUENFTE))

    def test_jede_fuehrt_zurueck_zu_ihrer_seite(self):
        for schluessel, (pk, adresse) in self.ziele().items():
            with self.subTest(schluessel=schluessel):
                self.assertEqual(str(stationen(f"{schluessel}:{pk}")[-1].url), adresse)

    def test_eine_geloeschte_fuehrt_nirgends_hin(self):
        """A link from an old tab: the page falls back to its own path."""
        for schluessel in HERKUENFTE:
            with self.subTest(schluessel=schluessel):
                self.assertEqual(stationen(f"{schluessel}:999999"), [])
