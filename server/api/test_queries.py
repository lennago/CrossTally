"""The answers of the API must not query per row, like the pages of the website.

The same pattern as 'inventur/test_queries.py': equally many queries with
few and with many rows. The list of inventories is what every device fetches
every twenty seconds, and until 10/2026 it grew by two queries per inventory
('hat_sollbestand').
"""

from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.db import connection
from django.test.utils import CaptureQueriesContext

from django_project.testing import inventur_anlegen
from inventur.models import Inventur, Inventurnotiz, Zaehlposition
from katalog.models import Gebinde, Hausartikel, Ware

from .serializers import GebindeSerializer
from .tests import STICHTAG, ApiGrundlagen, adresse


class AbfragenWachsenNichtMitDenZeilen(ApiGrundlagen):
    def abfragen(self, url: str) -> int:
        with CaptureQueriesContext(connection) as gezaehlt:
            rsp = self.client.get(url)
            self.assertEqual(rsp.status_code, 200, getattr(rsp, "data", rsp.content))
        return len(gezaehlt)

    def weitere_inventur(self, tage: int) -> Inventur:
        inventur = inventur_anlegen(self.haus, stichtag=STICHTAG + timedelta(days=tage))
        inventur.zaehlpositionen_anlegen()
        inventur.zaehlende.add(self.zaehler)
        inventur.zaehlung_starten()
        return inventur

    def artikel_mit_zeile(self, nummer: int) -> Hausartikel:
        """Counted unexpectedly, with a note: that is a row of the check list."""
        ware = Ware.objects.create(name=f"Ware {nummer}", basiseinheit=self.stueck)
        karton = Gebinde.objects.create(
            ware=ware, name="Karton", faktor_in_basiseinheit=Decimal("10")
        )
        artikel = Hausartikel.objects.create(
            haus=self.haus,
            ware=ware,
            warengruppe=self.gruppe,
            artikelnummer=str(nummer),
            name=ware.name,
        )
        Zaehlposition.objects.create(
            inventur=self.inventur,
            ort=self.lager,
            artikel=artikel,
            gebinde=karton,
            eingabemenge=Decimal("3"),
            unerwartet=True,
        )
        Inventurnotiz.objects.create(inventur=self.inventur, hausartikel=artikel, text="x")
        return artikel

    def test_das_gebinde_der_zeilen_baut_seine_felder_einmal(self):
        """Not a query, but the same pattern: the cost must not grow with the rows."""
        self.anmelden()
        url = adresse("inventur-detail", self.inventur.pk)

        def felder_gebaut() -> int:
            with mock.patch.object(
                GebindeSerializer,
                "get_fields",
                autospec=True,
                side_effect=GebindeSerializer.get_fields,
            ) as gebaut:
                self.assertEqual(self.client.get(url).status_code, 200)
            return gebaut.call_count

        for nummer in range(101201, 101203):
            self.artikel_mit_zeile(nummer)
        wenige = felder_gebaut()
        for nummer in range(101203, 101209):
            self.artikel_mit_zeile(nummer)
        self.assertEqual(felder_gebaut(), wenige)

    def test_inventurliste(self):
        self.anmelden()
        self.weitere_inventur(1)
        wenige = self.abfragen(adresse("inventur-list"))
        for tage in range(2, 6):
            self.weitere_inventur(tage)
        self.assertEqual(self.abfragen(adresse("inventur-list")), wenige)

    def test_pruefliste(self):
        self.anmelden()
        for nummer in range(101101, 101103):
            self.artikel_mit_zeile(nummer)
        url = adresse("inventur-pruefliste", self.inventur.pk)
        wenige = self.abfragen(url)
        for nummer in range(101103, 101109):
            self.artikel_mit_zeile(nummer)
        self.assertEqual(self.abfragen(url), wenige)

    def test_eine_zeile_der_pruefliste_liest_nur_ihren_artikel(self):
        """A note from the app computes its row alone. Until 10/2026 it computed every row.

        The query count hardly differs from the list's: a dozen fixed queries
        either way. What differs is what they fetch. The positions query
        names the article, and only one article is loaded.
        """
        self.anmelden()
        artikel = self.artikel_mit_zeile(101101)
        for nummer in range(101102, 101109):
            self.artikel_mit_zeile(nummer)

        def notiz(text: str) -> list[str]:
            with CaptureQueriesContext(connection) as gezaehlt:
                rsp = self.client.patch(
                    adresse("pruefliste_zeile", self.inventur.pk, artikel.pk),
                    {"notiz": text},
                    format="json",
                )
                self.assertEqual(rsp.status_code, 200, rsp.data)
                self.assertEqual(rsp.data["zeile"]["ist"], "30")
            return [eintrag["sql"] for eintrag in gezaehlt]

        erste = notiz("eins")
        positionen = [sql for sql in erste if '"inventur_zaehlposition"' in sql and "SELECT" in sql]
        self.assertTrue(positionen)
        for sql in positionen:
            self.assertIn(str(artikel.pk), sql, sql)
        artikel_abfragen = [
            sql for sql in erste if 'FROM "katalog_hausartikel"' in sql and "SELECT" in sql
        ]
        self.assertTrue(artikel_abfragen)
        self.assertTrue(all(str(artikel.pk) in sql for sql in artikel_abfragen))

        for nummer in range(101109, 101115):
            self.artikel_mit_zeile(nummer)
        self.assertEqual(len(notiz("zwei")), len(erste))

    def test_ich(self):
        """Three fields read the appearance, and that was three queries until 10/2026."""
        self.anmelden()
        with CaptureQueriesContext(connection) as gezaehlt:
            self.assertEqual(self.client.get(adresse("ich")).status_code, 200)
        erscheinungsbild = [
            abfrage for abfrage in gezaehlt if "organisation_erscheinungsbild" in abfrage["sql"]
        ]
        self.assertEqual(len(erscheinungsbild), 1)
