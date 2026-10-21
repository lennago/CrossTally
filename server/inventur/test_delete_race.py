"""Deleting a running inventory while the app weighs on one of its rows.

A weighing locks only its row ('api.serializers.counting.gesperrte_zeile').
Every delete of an inventory, on the website, for its run and in the
administration, locks the inventory without the key and then every row by
number before Django collects ('zeilen_der_inventuren_sperren'). A weighing
that holds its row first is waited for, and Django collects it along. One
that comes later finds its row gone and hears 404. Until 10/2026 only the
inventory was locked: Django collected, the weighing wrote between that and
the delete of its row, and the delete ended at the weighing's foreign key,
a server error.

SQLite locks nothing. The order of the locks is read off the queries in
'test_admin_inventory' and 'test_merge_race.ErstGesperrt', the overlap is
forced here on PostgreSQL with two connections ('test_merge_race.Nebeneinander').
"""

from http import HTTPStatus
from unittest import skipUnless

from django.db import connection
from django.test import Client
from django.urls import reverse

from api.serializers import counting
from api.tests import STICHTAG
from django_project.testing import administrator, fuehrungskraft
from inventur.admin import InventurAdmin
from inventur.models import Inventur, Wiegung, Zaehlposition
from inventur.test_merge_race import Nebeneinander
from katalog.test_ignore_race import MitZaehlung


@skipUnless(connection.vendor == "postgresql", "SQLite kennt keine Sperre einzelner Zeilen")
class LoeschenNebenDerWiegung(MitZaehlung, Nebeneinander):
    """Ware N on the scale in a running count, and its inventory deleted beside it."""

    def setUp(self):
        self.zaehlung_anlegen()
        self.chef = fuehrungskraft()
        self.admin = administrator(email="chef@example.org")

    def loeschen(self, adresse: str, konto, daten: dict | None = None):
        def schicken():
            client = Client(raise_request_exception=False)
            client.force_login(konto)
            return client.post(adresse, daten or {})

        return schicken

    def in_der_verwaltung(self):
        adresse = reverse("admin:inventur_inventur_delete", args=[self.inventur.pk])
        return self.loeschen(adresse, self.admin, {"post": "yes"})

    def auf_der_website(self, adresse: str):
        return self.loeschen(adresse, self.chef)

    def pruefe_geloescht(self, antwort) -> None:
        self.assertEqual(antwort.status_code, HTTPStatus.FOUND)
        self.assertFalse(Inventur.objects.exists())
        self.assertFalse(Zaehlposition.objects.exists())
        self.assertFalse(Wiegung.objects.exists())

    def zuerst_gewogen(self, loeschen) -> None:
        """The weighing holds its row, the delete waits, then collects the weighing along."""
        ergebnis = self.gleichzeitig(
            self.wiegung, loeschen, (counting, "gesperrte_zeile"), nach_dem_aufruf=True
        )

        self.assertEqual(ergebnis["zuerst"].status_code, HTTPStatus.CREATED)
        self.pruefe_geloescht(ergebnis["danach"])

    def test_die_verwaltung_wartet_auf_die_wiegung(self):
        self.zuerst_gewogen(self.in_der_verwaltung())

    def test_die_seite_der_inventur_wartet_auf_die_wiegung(self):
        self.zuerst_gewogen(
            self.auf_der_website(reverse("inventur_delete", args=[self.inventur.pk]))
        )

    def test_das_loeschen_des_laufs_wartet_auf_die_wiegung(self):
        adresse = reverse("inventur_lauf_delete", args=[STICHTAG.isoformat()])
        self.zuerst_gewogen(self.auf_der_website(adresse))

    def test_eine_wiegung_nach_dem_loeschen_findet_ihre_zeile_nicht_mehr(self):
        """Held once Django has looked, under the locks: the weighing waits and hears 404."""
        ergebnis = self.gleichzeitig(
            self.in_der_verwaltung(), self.wiegung, (InventurAdmin, "delete_model")
        )

        self.pruefe_geloescht(ergebnis["zuerst"])
        self.assertEqual(ergebnis["danach"].status_code, HTTPStatus.NOT_FOUND)
