"""Catalogue writes at the same moment as another: taking in twice, half a place, a split.

"Hinzufügen" looks whether the article stands at the location already and
creates the place if not. Two clicks at the same moment (a double click, a
second tab) both found nothing until 10/2026, and the second ended at the
unique index on the error page. Now the location is locked before the look.

A place and its pack defaults arise together or not at all
('Erwartung.anlegen'). Called outside a transaction, a failing default left
the place behind without packs until 10/2026.
"""

from decimal import Decimal
from http import HTTPStatus
from unittest import skipUnless

from django.db import IntegrityError, connection
from django.test import Client
from django.urls import reverse

from inventur.test_merge_race import MitBestand, Nebeneinander

from . import ware_split
from .models import Erwartung, ErwartungGebinde, Hausartikel, Ware
from .views import zaehlliste
from .views.zaehlliste import AUFNEHMEN
from .ware_split import aufteilen


class ZweiterArtikel(MitBestand):
    """'Artikel D', carried in the house, expected nowhere yet."""

    def zweiter_artikel(self) -> Hausartikel:
        ware = Ware.objects.create(name="Artikel D", basiseinheit=self.ware.basiseinheit)
        return Hausartikel.objects.create(
            haus=self.haus,
            ware=ware,
            warengruppe=self.artikel.warengruppe,
            artikelnummer="101002",
            name="Artikel D",
        )


@skipUnless(connection.vendor == "postgresql", "SQLite kennt keine Sperre einzelner Zeilen")
class ZweimalHinzufuegen(ZweiterArtikel, Nebeneinander):
    """Two clicks on "Hinzufügen", the first held after it has looked."""

    def test_der_zweite_klick_findet_den_ersten(self):
        neu = self.zweiter_artikel()
        adresse = reverse("erwartung_umschalten", args=[self.ort.pk])

        def klicken():
            client = Client(raise_request_exception=False)
            client.force_login(self.chef)
            return client.post(adresse, {"artikel": neu.pk, "aktion": AUFNEHMEN})

        ergebnis = self.gleichzeitig(
            klicken, klicken, (zaehlliste, "waehlbare_artikel"), nach_dem_aufruf=True
        )

        self.assertEqual(
            [ergebnis["zuerst"].status_code, ergebnis["danach"].status_code],
            [HTTPStatus.FOUND, HTTPStatus.FOUND],
        )
        self.assertEqual(Erwartung.objects.filter(ort=self.ort, artikel=neu).count(), 1)


class GanzOderGarNicht(ZweiterArtikel, Nebeneinander):
    """Outside a transaction, as the counting list's button calls it."""

    def test_eine_vorgabe_die_scheitert_nimmt_den_platz_mit(self):
        neu = self.zweiter_artikel()

        with self.assertRaises(IntegrityError):
            Erwartung.anlegen(self.ort, neu, vorgaben=[(999_999, Decimal("2"))])

        self.assertFalse(Erwartung.objects.filter(artikel=neu).exists())
        self.assertFalse(ErwartungGebinde.objects.filter(gebinde_id=999_999).exists())


class OhneZweitenFaden(ZweiterArtikel, Nebeneinander):
    """The button itself, on both databases."""

    def test_ein_klick_nimmt_auf_und_der_zweite_sagt_es(self):
        neu = self.zweiter_artikel()
        client = Client()
        client.force_login(self.chef)
        adresse = reverse("erwartung_umschalten", args=[self.ort.pk])

        client.post(adresse, {"artikel": neu.pk, "aktion": AUFNEHMEN})
        antwort = client.post(adresse, {"artikel": neu.pk, "aktion": AUFNEHMEN}, follow=True)

        self.assertContains(antwort, "steht schon in der Zählliste")
        self.assertEqual(Erwartung.objects.filter(ort=self.ort, artikel=neu).count(), 1)


@skipUnless(connection.vendor == "postgresql", "SQLite kennt keine Sperre einzelner Zeilen")
class AufteilenWaehrendDerArtikelSeinerWareWechselt(Nebeneinander):
    """A product is split while someone moves one of its articles to another product.

    The split locks the articles before it reads which of them still belong
    to the product ('ware_split.aufteilen'). Until 10/2026 it read first:
    the article moved in between came back onto the new product, and the
    change was gone without a word.
    """

    def test_der_wechsel_bleibt(self):
        def aufteilen_jetzt():
            aufteilen(
                Ware.objects.get(pk=self.ware.pk), "Artikel C", "Artikel C Ost", [self.artikel]
            )

        def wechseln():
            artikel = Hausartikel.objects.get(pk=self.artikel.pk)
            artikel.ware = Ware.objects.get(pk=self.ziel.pk)
            artikel.save()

        self.gleichzeitig(aufteilen_jetzt, wechseln, (ware_split, "zaehlpositionen_sperren"))

        self.assertEqual(Hausartikel.objects.get(pk=self.artikel.pk).ware_id, self.ziel.pk)
