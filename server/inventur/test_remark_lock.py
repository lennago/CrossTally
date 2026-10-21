"""A remark locks its inventory without the key, and a deleted inventory answers 404.

A note or a difference locks the inventory before it reads what is stored
('services.notes._gesperrt'). Until 10/2026 it took 'FOR UPDATE'. A product
saved with a new name and moved into "Not countable" touches the rows of the
inventory twice and deletes the article's remarks. At its commit PostgreSQL
checks the keys of those rows against the inventory, and that check waited for
the remark's lock. The remark itself waited for the row the product's save was
deleting, and one of the two ended in a server error. Without the key the check
goes on beside the lock, as for the closing and the app. An inventory deleted
while a remark was on its way ended in a server error as well.

'ZuerstOhneSchluessel' and 'Geloescht' run everywhere. 'NebenDerWare' forces
the overlap on PostgreSQL with two connections ('test_merge_race.Nebeneinander').
"""

from http import HTTPStatus
from unittest import mock, skipUnless

from django.db import connection
from django.test import Client, TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from api.test_merge_split_race import abfragen_waehrend
from api.tests import STICHTAG, adresse
from django_project.testing import fuehrungskraft, inventur_anlegen
from katalog.models import Artikelstatus, Hausartikel, Ware, Warenfamilie

from .models import Inventur, Inventurnotiz, Inventurstatus
from .services import notes, notizen_speichern, vermerk_aendern
from .test_merge_race import MitBestand, Nebeneinander, erste_abfrage


class Laufend(MitBestand):
    """A running inventory of the counter's crates, with a note on the article."""

    inventur: Inventur

    def laufend_anlegen(self) -> None:
        self.bestand_anlegen()
        self.inventur = inventur_anlegen(self.haus, stichtag=STICHTAG)
        self.inventur.zaehlpositionen_anlegen()
        self.inventur.zaehlung_starten()
        vermerk_aendern(self.inventur, self.artikel.pk, text="Regal hinten")


class ZuerstOhneSchluessel(Laufend, TestCase):
    """Both ways of writing a remark lock the inventory without the key, read off the queries."""

    def setUp(self):
        self.laufend_anlegen()

    def pruefe_ohne_schluessel(self, schreiben) -> None:
        abfragen = abfragen_waehrend(schreiben)
        stelle = erste_abfrage(abfragen, Inventur, gesperrt=True)
        self.assertTrue(abfragen[stelle][2], f"FOR UPDATE statt FOR NO KEY UPDATE: {stelle}")
        self.assertLess(stelle, erste_abfrage(abfragen, Inventurnotiz))

    def test_die_app(self):
        self.pruefe_ohne_schluessel(
            lambda: vermerk_aendern(self.inventur, self.artikel.pk, text="Bruch")
        )
        self.assertEqual(self.inventur.notizen.get().text, "Bruch")

    def test_die_pruefliste(self):
        self.pruefe_ohne_schluessel(
            lambda: notizen_speichern(
                self.inventur,
                {self.artikel.pk: "Bruch"},
                vorher={self.artikel.pk: ("Regal hinten", False)},
            )
        )
        self.assertEqual(self.inventur.notizen.get().text, "Bruch")

    def pruefe_schalter_ohne_schluessel(self, schalten) -> None:
        """The switches of the inventory itself lock it the same way ('_frisch_und_gesperrt')."""
        abfragen = abfragen_waehrend(schalten)
        stelle = erste_abfrage(abfragen, Inventur, gesperrt=True)
        self.assertTrue(abfragen[stelle][2], f"FOR UPDATE statt FOR NO KEY UPDATE: {stelle}")

    def test_differenz_von_hand(self):
        self.inventur.sollbestaende.all().delete()
        self.pruefe_schalter_ohne_schluessel(lambda: self.inventur.differenz_von_hand_setzen(True))
        self.assertTrue(Inventur.objects.get(pk=self.inventur.pk).differenz_von_hand)

    def test_oeffnen(self):
        self.inventur.sperre_setzen(True)
        self.pruefe_schalter_ohne_schluessel(lambda: self.inventur.sperre_setzen(False))
        self.assertFalse(Inventur.objects.get(pk=self.inventur.pk).gesperrt)

    def test_zurueck_in_die_vorbereitung(self):
        self.pruefe_schalter_ohne_schluessel(self.inventur.zurueck_in_vorbereitung)
        self.assertEqual(
            Inventur.objects.get(pk=self.inventur.pk).status, Inventurstatus.VORBEREITUNG
        )


class Geloescht(Laufend, TestCase):
    """The inventory is deleted after the request found it and before the remark locks it."""

    def setUp(self):
        self.laufend_anlegen()
        self.chef = fuehrungskraft()
        self.inventur.zaehlende.add(self.chef)

    def erst_geloescht(self, schreiben):
        """'schreiben' as it runs, only with the inventory deleted right before."""

        def geloescht_und_geschrieben(inventur, *args, **kwargs):
            Inventur.objects.filter(pk=inventur.pk).delete()
            return schreiben(inventur, *args, **kwargs)

        return geloescht_und_geschrieben

    def test_die_app_hoert_404(self):
        client = APIClient()
        client.force_authenticate(self.chef)

        with mock.patch("api.views.review.vermerk_aendern", self.erst_geloescht(vermerk_aendern)):
            antwort = client.patch(
                adresse("pruefliste_zeile", self.inventur.pk, self.artikel.pk),
                {"notiz": "Bruch"},
                format="json",
            )

        self.assertEqual(antwort.status_code, HTTPStatus.NOT_FOUND)
        self.assertFalse(Inventurnotiz.objects.exists())

    def test_die_pruefliste_hoert_404(self):
        """The script shows its own sentence for any refusal without one ('notes.js')."""
        client = Client()
        client.force_login(self.chef)
        pk = self.artikel.pk

        with mock.patch(
            "inventur.views.review.notizen_speichern", self.erst_geloescht(notizen_speichern)
        ):
            antwort = client.post(
                reverse("inventur_pruefen", args=[self.inventur.pk]),
                {f"notiz_{pk}": "Bruch", f"notiz_vorher_{pk}": "Regal hinten", "nebenbei": "1"},
                HTTP_X_REQUESTED_WITH="fetch",
            )

        self.assertEqual(antwort.status_code, HTTPStatus.NOT_FOUND)
        self.assertFalse(Inventurnotiz.objects.exists())


@skipUnless(connection.vendor == "postgresql", "SQLite kennt keine Sperre einzelner Zeilen")
class NebenDerWare(Laufend, Nebeneinander):
    """A remark on its way while its product gets a new name and moves into "Not countable".

    The product's save touches the rows of the inventory twice: the new name
    and the article dropping out ('katalog.hausartikel_status.aufraeumen').
    Whichever side comes first, both get through, and the remark is gone
    with the article.
    """

    def setUp(self):
        self.laufend_anlegen()
        self.chef = fuehrungskraft()
        self.inventur.zaehlende.add(self.chef)

    def umbenennen_und_ignorieren(self):
        ware = Ware.objects.get(pk=self.ware.pk)
        ware.name = "Artikel C umbenannt"
        ware.warenfamilie = Warenfamilie.nicht_zaehlbar()
        ware.save()

    def in_der_app(self):
        client = APIClient(raise_request_exception=False)
        client.force_authenticate(self.chef)
        return client.patch(
            adresse("pruefliste_zeile", self.inventur.pk, self.artikel.pk),
            {"notiz": "Bruch", "notiz_vorher": "Regal hinten"},
            format="json",
        )

    def in_der_pruefliste(self):
        client = Client(raise_request_exception=False)
        client.force_login(self.chef)
        pk = self.artikel.pk
        return client.post(
            reverse("inventur_pruefen", args=[self.inventur.pk]),
            {f"notiz_{pk}": "Bruch", f"notiz_vorher_{pk}": "Regal hinten", "nebenbei": "1"},
            HTTP_X_REQUESTED_WITH="fetch",
        )

    def pruefe_ohne_serverfehler(self, antwort) -> None:
        self.assertLess(antwort.status_code, HTTPStatus.INTERNAL_SERVER_ERROR)
        self.assertEqual(Ware.objects.get(pk=self.ware.pk).name, "Artikel C umbenannt")
        artikel = Hausartikel.objects.get(pk=self.artikel.pk)
        self.assertEqual(artikel.status, Artikelstatus.IGNORIERT)
        self.assertFalse(Inventurnotiz.objects.exists())

    def test_die_app_nach_der_ware(self):
        """Held after the article dropped out, while the product holds its rows and remarks."""
        ergebnis = self.gleichzeitig(
            self.umbenennen_und_ignorieren,
            self.in_der_app,
            (Hausartikel, "_plaetze_wiederherstellen"),
        )

        self.pruefe_ohne_serverfehler(ergebnis["danach"])

    def test_die_ware_nach_der_app(self):
        """Held once the remark has locked the inventory, before it reads what is stored."""
        ergebnis = self.gleichzeitig(
            self.in_der_app, self.umbenennen_und_ignorieren, (notes, "_gespeicherte")
        )

        self.pruefe_ohne_serverfehler(ergebnis["zuerst"])

    def test_die_pruefliste_nach_der_ware(self):
        ergebnis = self.gleichzeitig(
            self.umbenennen_und_ignorieren,
            self.in_der_pruefliste,
            (Hausartikel, "_plaetze_wiederherstellen"),
        )

        self.pruefe_ohne_serverfehler(ergebnis["danach"])
