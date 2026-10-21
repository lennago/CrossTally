"""A count position saved in the administration while its inventory is closed.

The administration locks the inventory without the key and then the row
before Django reads it ('inventur.admin.ZaehlpositionAdmin'), as the app
does. The closing locks the inventory and every row before it compares
('Inventur.zum_abschluss_sperren'). Whichever comes second waits. Until
10/2026 the administration locked nothing: a change that came in while the
closing froze the rows put its row back into the closed inventory, and the
result went without it.

SQLite locks nothing. The order of the locks is read off the queries in
'test_admin.ZaehlpositionInDerVerwaltung', the overlap is forced here on
PostgreSQL with two connections, in both orders ('test_merge_race.Nebeneinander').
"""

from decimal import Decimal
from unittest import skipUnless

from django.db import connection
from django.test import Client
from django.urls import reverse

from api.tests import STICHTAG
from django_project.testing import administrator, inventur_anlegen
from inventur.admin import ZaehlpositionAdmin
from inventur.models import Inventur, Inventurergebnis, Inventurstatus, Zaehlposition
from inventur.test_admin import formular
from inventur.test_merge_race import Nebeneinander


@skipUnless(connection.vendor == "postgresql", "SQLite kennt keine Sperre einzelner Zeilen")
class VerwaltungNebenDemAbschluss(Nebeneinander):
    """Two crates counted, three typed in the administration, and the closing beside it."""

    def setUp(self):
        super().setUp()
        self.inventur = inventur_anlegen(self.haus, stichtag=STICHTAG)
        self.inventur.zaehlpositionen_anlegen()
        self.inventur.zaehlung_starten()
        zeile = self.inventur.zaehlpositionen.get()
        zeile.eingabemenge = Decimal("2")
        zeile.save(update_fields=["eingabemenge", "zuletzt_geaendert"])
        self.admin = administrator(email="chef@example.org")
        self.adresse = reverse("admin:inventur_zaehlposition_change", args=[zeile.pk])
        client = Client()
        client.force_login(self.admin)
        seite = formular(client.get(self.adresse), "zaehlposition_form")
        self.daten = {**seite, "eingabemenge": "3", "_save": "1"}

    def in_der_verwaltung(self):
        def schicken():
            client = Client(raise_request_exception=False)
            client.force_login(self.admin)
            return client.post(self.adresse, self.daten)

        return schicken

    def abschluss(self):
        return lambda: Inventur.objects.get(pk=self.inventur.pk).abschliessen()

    def pruefe_eingefroren(self, menge: Decimal) -> None:
        """Closed, no row left behind, and the result as the one who came first left it."""
        self.assertEqual(
            Inventur.objects.get(pk=self.inventur.pk).status, Inventurstatus.ABGESCHLOSSEN
        )
        self.assertFalse(Zaehlposition.objects.exists())
        (ergebnis,) = Inventurergebnis.objects.all()
        self.assertEqual(ergebnis.gesamtmenge_basiseinheit, menge)

    def test_der_abschluss_wartet_auf_die_verwaltung(self):
        """The change is written first and frozen: three crates of 20."""
        ergebnis = self.gleichzeitig(
            self.in_der_verwaltung(),
            self.abschluss(),
            (ZaehlpositionAdmin, "get_object"),
            nach_dem_aufruf=True,
        )

        self.assertEqual(ergebnis["zuerst"].status_code, 302)
        self.pruefe_eingefroren(Decimal("60"))

    def test_die_verwaltung_wartet_auf_den_abschluss(self):
        """The closing froze two crates and cleared the row. The change finds it gone."""
        ergebnis = self.gleichzeitig(
            self.abschluss(), self.in_der_verwaltung(), (Inventur, "mengen_je_hausartikel")
        )

        self.pruefe_eingefroren(Decimal("40"))
        # Django's answer for a row that no longer exists: back to the start page.
        self.assertRedirects(
            ergebnis["danach"], reverse("admin:index"), fetch_redirect_response=False
        )
