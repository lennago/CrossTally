"""Weighing while another device deletes the bowl, brings its row back or merges its product.

A correction reads its weighing once more under the lock of its row
('serializers.counting.WiegungSerializer.save'). Deleting a weighing, and a
new bowl that revives the row, hold the row while they delete. Until
10/2026 the correction saved the weighing as the view had loaded it:
PostgreSQL found nothing to update, and Django inserted it again under its
number, after a revival next to the bowl that replaced it. A new bowl asks
once more under the row's lock whether the row still weighs and offers its
container. A merge of the product locks the row first. Until 10/2026 the
bowl checked before the merge landed after it, on a row that no longer
weighed. Forced here with two connections. What another device does in
between is done by hand in 'tests.BerichtigteWiegungUnterDerSperre' and
'test_row_lock_checks', on SQLite as well.
"""

from decimal import Decimal
from http import HTTPStatus
from unittest import skipUnless

from django.db import connection

from inventur.models import Wiegung, Zaehlposition
from inventur.test_merge_race import ZUSAMMENFUEHREN_HAELT, Nebeneinander
from katalog.models import Basiseinheit, Ware
from katalog.test_ignore_race import MitZaehlung
from katalog.ware_merge import zusammenfuehren

from .serializers import counting
from .tests import adresse


@skipUnless(connection.vendor == "postgresql", "SQLite kennt keine Sperre einzelner Zeilen")
class BerichtigenNebenher(MitZaehlung, Nebeneinander):
    def setUp(self):
        self.zaehlung_anlegen()
        self.alt = Wiegung.objects.create(
            zaehlposition=self.waage, brutto_gramm=Decimal("1250"), behaelter=self.schale
        )

    def berichtigen(self):
        return self.client_der_app().patch(
            adresse("wiegung-detail", self.alt.pk), {"brutto_gramm": "1500"}, format="json"
        )

    def test_die_berichtigung_wartet_auf_das_loeschen(self):
        def loeschen():
            return self.client_der_app().delete(adresse("wiegung-detail", self.alt.pk))

        ergebnis = self.gleichzeitig(loeschen, self.berichtigen, (Wiegung, "delete"))

        self.assertEqual(ergebnis["zuerst"].status_code, HTTPStatus.NO_CONTENT)
        self.assertEqual(ergebnis["danach"].status_code, HTTPStatus.NOT_FOUND)
        self.assertFalse(Wiegung.objects.filter(pk=self.alt.pk).exists())

    def test_die_berichtigung_wartet_auf_die_wiederbelebung(self):
        """The new bowl takes the old ones along. The correction finds its bowl gone."""
        Zaehlposition.objects.filter(pk=self.waage.pk).update(aktiv=False)

        ergebnis = self.gleichzeitig(
            self.wiegung, self.berichtigen, (counting, "behaelter_sperren")
        )

        self.assertEqual(ergebnis["zuerst"].status_code, HTTPStatus.CREATED)
        self.assertEqual(ergebnis["danach"].status_code, HTTPStatus.NOT_FOUND)
        self.assertEqual(
            list(self.waage.wiegungen.values_list("brutto_gramm", flat=True)), [Decimal("1250")]
        )
        self.assertFalse(Wiegung.objects.filter(pk=self.alt.pk).exists())


@skipUnless(connection.vendor == "postgresql", "SQLite kennt keine Sperre einzelner Zeilen")
class WiegenWaehrendDesZusammenfuehrens(MitZaehlung, Nebeneinander):
    """Ware N merges into another product while a bowl is on its way.

    The merge holds the rows once it has re-hung them. The bowl was checked
    before, against Ware N, and waits for its row.
    """

    def setUp(self):
        self.zaehlung_anlegen()

    def zusammenfuehren_in(self, **ziel):
        ziel = Ware.objects.create(
            name="Ware N neu", basiseinheit=Basiseinheit.objects.get(name="g"), **ziel
        )
        return lambda: zusammenfuehren(
            Ware.objects.get(pk=self.lose.pk), Ware.objects.get(pk=ziel.pk)
        )

    def test_in_eine_ware_die_nicht_gewogen_wird(self):
        ergebnis = self.gleichzeitig(self.zusammenfuehren_in(), self.wiegung, ZUSAMMENFUEHREN_HAELT)

        antwort = ergebnis["danach"]
        self.assertEqual(antwort.status_code, HTTPStatus.BAD_REQUEST, antwort.data)
        self.assertEqual(antwort.data, {"zaehlposition": ["„Ware N“ wird nicht gewogen."]})
        self.assertFalse(Wiegung.objects.exists())

    def test_in_eine_gewogene_ware_ohne_die_schale(self):
        ergebnis = self.gleichzeitig(
            self.zusammenfuehren_in(abwiegbar=True), self.wiegung, ZUSAMMENFUEHREN_HAELT
        )

        antwort = ergebnis["danach"]
        self.assertEqual(antwort.status_code, HTTPStatus.BAD_REQUEST, antwort.data)
        self.assertEqual(list(antwort.data), ["behaelter"])
        self.assertFalse(Wiegung.objects.exists())
