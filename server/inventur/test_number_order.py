"""Article numbers of different length sort as numbers, as in the counting app.

As text, "1012" stood between "101002" and "101003" on the sheet and in the
check list (until 10/2026), while the app and the inventory management
system show it before them.
"""

from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from katalog.models import Hausartikel, Ware, Warengruppe, nummer_sortierschluessel

from .evaluation import warengruppen_einer_inventur
from .models import Inventurergebnis, Inventurstatus, Zaehlposition
from .services import ergebniszeilen
from .test_views import InventurSeiten

NUMMERN = ("1012", "101002", "101003-1", "9", "101003")
ERWARTET = ["9", "1012", "101002", "101003", "101003-1"]


class NummernWieInDerApp(InventurSeiten):
    def eingefroren(self):
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)
        inventur.abschliessen()
        gruppe = Warengruppe.objects.get(name="Warengruppe G")
        for nummer in NUMMERN:
            ware = Ware.objects.create(name=f"Ware {nummer}", basiseinheit=self.stueck)
            artikel = Hausartikel.objects.create(
                haus=self.haus, ware=ware, warengruppe=gruppe, artikelnummer=nummer, name=ware.name
            )
            Inventurergebnis.objects.create(
                inventur=inventur,
                hausartikel=artikel,
                gesamtmenge_basiseinheit=Decimal("1"),
                eingefroren_am=timezone.now(),
            )
        return inventur

    def test_der_schluessel(self):
        self.assertEqual(sorted(NUMMERN, key=nummer_sortierschluessel), ERWARTET)
        self.assertLess(nummer_sortierschluessel("101003"), nummer_sortierschluessel("101003-1"))

    def test_blatt_und_ergebnistabelle(self):
        inventur = self.eingefroren()
        bloecke = warengruppen_einer_inventur(inventur)
        self.assertEqual([zeile.nummer for block in bloecke for zeile in block.zeilen], ERWARTET)
        self.assertEqual([zeile.artikelnummer for zeile in ergebniszeilen(inventur)], ERWARTET)

    def test_die_pruefliste_sortiert_ueber_die_spalte(self):
        """The column sorts over attributes of the rows, not over SQL."""
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)
        gruppe = Warengruppe.objects.get(name="Warengruppe G")
        for nummer in NUMMERN:
            ware = Ware.objects.create(name=f"Ware {nummer}", basiseinheit=self.stueck)
            artikel = Hausartikel.objects.create(
                haus=self.haus, ware=ware, warengruppe=gruppe, artikelnummer=nummer, name=ware.name
            )
            Zaehlposition.objects.create(
                inventur=inventur,
                ort=self.ort,
                artikel=artikel,
                eingabemenge=Decimal("1"),
                unerwartet=True,
            )
        for sortierung, erwartet in (("nummer", ERWARTET), ("-nummer", ERWARTET[::-1])):
            with self.subTest(sortierung=sortierung):
                seite = self.client.get(
                    reverse("inventur_pruefen", args=[inventur.pk]) + f"?sortierung={sortierung}"
                )
                inhalt = seite.content.decode()
                positionen = [inhalt.index(f'title="Ware {nummer}"') for nummer in erwartet]
                self.assertEqual(positionen, sorted(positionen))
