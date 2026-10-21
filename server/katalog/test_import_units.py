"""The import writes a product only in a unit a product may be carried in.

The plan's unit field is hidden. A changed one made a product in portions
until 10/2026, although every form refuses that.
"""

from decimal import Decimal

from django.test import TestCase

from organisation.models import Haus

from . import services
from .models import Einheitenkennung, Hausartikel, Ware


class PortionImPlan(TestCase):
    def test_wird_uebersprungen_und_gemeldet(self):
        zeile = {
            "artikelnummer": "101501",
            "warengruppe": "Warengruppe S",
            "einheit": Einheitenkennung.PORTION,
            "art": services.NEU,
            "vorhanden_pk": None,
            "name": "Ware P",
            "ware": None,
            "neuer_ware": "",
            "inventurart": None,
            "gebinde": {"Karton": Decimal("10")},
            "vorgehen": services.AKTIV,
            "status_neu": services.AKTIV,
            "uebernehmen": [],
            "faktor_zur_ware": None,
        }
        bericht = services.ausfuehren([zeile], Haus.objects.create(name="Kino West"))
        self.assertEqual(len(bericht.fehler), 1)
        self.assertIn("unbekannte Einheit", bericht.fehler[0])
        self.assertFalse(Ware.objects.filter(name="Ware P").exists())
        self.assertFalse(Hausartikel.objects.filter(artikelnummer="101501").exists())
