"""A note from the app's check list while a second tab takes the inventory back into preparation.

The app writes remarks only in the count, and that is asked once more under
the inventory's lock ('inventur.services.notes.vermerk_aendern', 'pruefen').
Until 10/2026 only the view asked, before the lock: a note checked a moment
before "Back into preparation" waited for the lock and then landed in the
preparation, with 200. Forced here with two connections. What happens in
between is done by hand in 'test_review_list.ZurueckInDieVorbereitung', on
SQLite as well.
"""

from http import HTTPStatus
from unittest import skipUnless

from django.db import connection

from inventur.models import Inventur, Inventurnotiz, Inventurstatus
from inventur.services import notes
from inventur.test_merge_race import Nebeneinander
from katalog.test_ignore_race import MitZaehlung

from .tests import adresse


@skipUnless(connection.vendor == "postgresql", "SQLite kennt keine Sperre einzelner Zeilen")
class NotizNebenDemZuruecknehmen(MitZaehlung, Nebeneinander):
    def setUp(self):
        self.zaehlung_anlegen()

    def notiz(self):
        return self.client_der_app().patch(
            adresse("pruefliste_zeile", self.inventur.pk, self.artikel.pk),
            {"notiz": "später"},
            format="json",
        )

    def zuruecknehmen(self) -> None:
        Inventur.objects.get(pk=self.inventur.pk).zurueck_in_vorbereitung()

    def test_die_notiz_wartet_und_landet_nicht_in_der_vorbereitung(self):
        """Held while the inventory is locked: the view of the note still sees the count."""
        ergebnis = self.gleichzeitig(self.zuruecknehmen, self.notiz, (Inventur, "save"))

        antwort = ergebnis["danach"]
        self.assertEqual(antwort.status_code, HTTPStatus.CONFLICT, antwort.data)
        self.assertEqual(antwort.data["status"], Inventurstatus.VORBEREITUNG)
        self.assertTrue(antwort.data["vorlaeufig"])
        self.assertFalse(Inventurnotiz.objects.filter(inventur=self.inventur).exists())

    def test_nach_der_notiz_steht_sie_noch_in_der_zaehlung(self):
        """The note holds the lock first: it lands, and the inventory goes back afterwards."""
        ergebnis = self.gleichzeitig(self.notiz, self.zuruecknehmen, (notes, "_gespeicherte"))

        self.assertEqual(ergebnis["zuerst"].status_code, HTTPStatus.OK, ergebnis["zuerst"].data)
        self.assertEqual(Inventurnotiz.objects.get(inventur=self.inventur).text, "später")
        self.assertEqual(
            Inventur.objects.get(pk=self.inventur.pk).status, Inventurstatus.VORBEREITUNG
        )
