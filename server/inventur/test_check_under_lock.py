"""Counters and target stock against a closing or the switch for the difference by hand.

The pages look at the inventory before they write: the counters page whether
it still runs, the upload and the target by hand whether the difference comes
by hand. Both look again on the locked row now, as the switches do. Until
10/2026 a closing between the look and the write got its counters back,
although who counted is not kept, and a target came in beside a difference
by hand, where nobody sees it.

The upload reads its file before it takes the lock ('sollbestand_einlesen').
Until 10/2026 it read the file under the lock, and a large workbook kept
everyone who writes to the inventory waiting meanwhile.

The first classes force the moment in between in one connection, on SQLite
as on PostgreSQL, and read the locks off the queries. The last one runs both
sides in two connections on PostgreSQL ('Nebeneinander').
"""

from decimal import Decimal
from http import HTTPStatus
from unittest import mock, skipUnless

from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import Client
from django.urls import reverse

from accounts.models import CustomUser
from api.test_merge_split_race import abfragen_waehrend
from api.tests import STICHTAG
from django_project.testing import inventur_anlegen
from katalog.formats import STANDARD, Lesefehler, format_holen
from katalog.formats.winticket import WinTicket

from .closing import Entscheidungen, abschliessen
from .forms import SollbestandImportForm, ZaehlendeForm
from .models import Inventur, Inventurstatus, Sollbestand, Sollzeile
from .services import soll_von_hand_speichern, sollbestand_einlesen, target_stock
from .test_merge_race import Nebeneinander
from .test_views import InventurSeiten, datei

#: What the counters page says about a closed inventory.
NICHT_UMBESETZT = "Eine abgeschlossene Inventur wird nicht mehr umbesetzt."
#: What the upload says while the difference comes by hand.
OHNE_SOLL = (
    "Die Differenz wird bei dieser Inventur von Hand eingetragen. Ein Sollbestand kommt erst "
    "herein, wenn das auf der Seite der Inventur ausgeschaltet ist."
)


def meldungen(antwort) -> list[str]:
    return [str(meldung) for meldung in get_messages(antwort.wsgi_request)]


def erste_sperre(abfragen) -> int:
    """The place of the first query that locks the inventory, without the key."""
    for stelle, (modell, gesperrt, ohne_schluessel, *_rest) in enumerate(abfragen):
        if modell is Inventur and gesperrt:
            assert ohne_schluessel, "FOR UPDATE statt FOR NO KEY UPDATE"
            return stelle
    raise AssertionError("keine Sperre der Inventur")


class ZaehlendeNachDemAbschluss(InventurSeiten):
    """The counters page looked at a running inventory, and a closing came before the write."""

    def setUp(self):
        super().setUp()
        self.zaehlerin = CustomUser.objects.create_user(
            username="zaehlerin", password="geheim", email="zaehlerin@example.org"
        )
        self.laufend = self.inventur(status=Inventurstatus.ZAEHLUNG)
        self.laufend.zaehlpositionen.update(eingabemenge=Decimal("2"))

    def test_ein_abschluss_zwischen_blick_und_eintrag(self):
        echt = ZaehlendeForm.is_valid

        def mit_abschluss(form):
            abschliessen(Inventur.objects.get(pk=self.laufend.pk), Entscheidungen())
            return echt(form)

        with mock.patch.object(ZaehlendeForm, "is_valid", mit_abschluss):
            antwort = self.client.post(
                reverse("inventur_zaehlende", args=[self.laufend.pk]),
                {"zaehlende": [self.zaehlerin.pk]},
            )

        self.assertRedirects(antwort, self.laufend.get_absolute_url())
        self.assertEqual(meldungen(antwort), [NICHT_UMBESETZT])
        self.laufend.refresh_from_db()
        self.assertEqual(self.laufend.status, Inventurstatus.ABGESCHLOSSEN)
        self.assertFalse(self.laufend.zaehlende.exists())

    def test_eine_inzwischen_geloeschte_inventur(self):
        """Deleted between the page's look and the write: not found, not the error page."""
        echt = ZaehlendeForm.is_valid

        def mit_loeschen(form):
            Inventur.objects.filter(pk=self.laufend.pk).delete()
            return echt(form)

        with mock.patch.object(ZaehlendeForm, "is_valid", mit_loeschen):
            antwort = self.client.post(
                reverse("inventur_zaehlende", args=[self.laufend.pk]),
                {"zaehlende": [self.zaehlerin.pk]},
            )

        self.assertEqual(antwort.status_code, HTTPStatus.NOT_FOUND)

    def test_das_eintragen_sperrt_die_inventur_zuerst(self):
        """The lock comes before the assignment is read or written. A closing waits for it."""
        form = ZaehlendeForm({"zaehlende": [self.zaehlerin.pk]}, inventur=self.laufend)
        self.assertTrue(form.is_valid())

        abfragen = abfragen_waehrend(form.speichern)

        durch = Inventur.zaehlende.through
        zuordnung = next(n for n, (modell, *_rest) in enumerate(abfragen) if modell is durch)
        self.assertLess(erste_sperre(abfragen), zuordnung)
        self.assertEqual(list(self.laufend.zaehlende.all()), [self.zaehlerin])


class SollNebenDerDifferenzVonHand(InventurSeiten):
    """The page looked at an inventory without the difference by hand. Then came the switch."""

    def test_ein_schalter_zwischen_blick_und_upload(self):
        inventur = self.inventur()
        echt = SollbestandImportForm.is_valid

        def mit_schalter(form):
            Inventur.objects.get(pk=inventur.pk).differenz_von_hand_setzen(True)
            return echt(form)

        with mock.patch.object(SollbestandImportForm, "is_valid", mit_schalter):
            antwort = self.client.post(
                inventur.get_absolute_url(),
                {"datei": datei("101001;30\n"), "format": STANDARD},
            )

        self.assertRedirects(antwort, inventur.get_absolute_url())
        self.assertEqual(meldungen(antwort), [OHNE_SOLL])
        inventur.refresh_from_db()
        self.assertTrue(inventur.differenz_von_hand)
        self.assertFalse(Sollbestand.objects.exists() or Sollzeile.objects.exists())

    def test_ebenso_der_sollbestand_von_hand(self):
        """The maintenance page with an inventory loaded before the switch."""
        inventur = self.inventur()
        Inventur.objects.get(pk=inventur.pk).differenz_von_hand_setzen(True)

        with self.assertRaisesMessage(ValidationError, OHNE_SOLL):
            soll_von_hand_speichern(inventur, {self.artikel.pk: Decimal("3")})
        self.assertFalse(Sollbestand.objects.exists())

    def test_beide_sperren_die_inventur_zuerst(self):
        """Before the old target goes and before a new one is written."""
        inventur = self.inventur()
        for name, schreiben in (
            ("Datei", lambda: sollbestand_einlesen(datei("101001;30\n"), inventur)),
            (
                "von Hand",
                lambda: soll_von_hand_speichern(inventur, {self.artikel.pk: Decimal("30")}),
            ),
        ):
            with self.subTest(name):
                abfragen = abfragen_waehrend(schreiben)
                soll = next(
                    stelle
                    for stelle, (modell, *_rest) in enumerate(abfragen)
                    if modell in (Sollbestand, Sollzeile)
                )
                self.assertLess(erste_sperre(abfragen), soll)
                self.assertEqual(Sollbestand.objects.get().menge_basiseinheit, Decimal("30"))
                Sollbestand.objects.all().delete()


class DieDateiVorDerSperre(InventurSeiten):
    """The file is read with no lock held. Whatever changes meanwhile is seen under the lock."""

    def setUp(self):
        super().setUp()
        self.inv = self.inventur()

    def einlesen(self):
        return sollbestand_einlesen(datei("101001;30\n"), self.inv, format_holen(STANDARD))

    def beim_lesen(self, tun):
        """'tun' happens while the format reads the file."""
        echt = WinTicket.sollbestand_lesen

        def lesen_und_tun(format, datei):
            tun()
            return echt(format, datei)

        return mock.patch.object(
            WinTicket, "sollbestand_lesen", autospec=True, side_effect=lesen_und_tun
        )

    def test_erst_lesen_dann_sperren(self):
        folge = []
        echt = target_stock.soll_darf_herein

        def sperren(inventur):
            folge.append("sperren")
            return echt(inventur)

        with (
            self.beim_lesen(lambda: folge.append("lesen")),
            mock.patch.object(target_stock, "soll_darf_herein", side_effect=sperren),
        ):
            self.einlesen()

        self.assertEqual(folge, ["lesen", "sperren"])
        self.assertEqual(Sollbestand.objects.get().menge_basiseinheit, Decimal("30"))

    def test_lesen_ohne_eigene_transaktion(self):
        """Not even a transaction of its own is open while the file is read."""
        tiefe = []
        vorher = len(connection.atomic_blocks)

        with self.beim_lesen(lambda: tiefe.append(len(connection.atomic_blocks))):
            self.einlesen()

        self.assertEqual(tiefe, [vorher])

    def test_ein_schalter_waehrend_des_lesens(self):
        with (
            self.beim_lesen(
                lambda: Inventur.objects.get(pk=self.inv.pk).differenz_von_hand_setzen(True)
            ),
            self.assertRaisesMessage(ValidationError, OHNE_SOLL),
        ):
            self.einlesen()

        self.assertFalse(Sollbestand.objects.exists() or Sollzeile.objects.exists())

    def test_ein_start_der_zaehlung_waehrend_des_lesens(self):
        """The apps count against the target already: it does not change under them."""
        with (
            self.beim_lesen(lambda: Inventur.objects.get(pk=self.inv.pk).zaehlung_starten()),
            self.assertRaisesMessage(ValidationError, str(target_stock.NUR_IN_DER_VORBEREITUNG)),
        ):
            self.einlesen()

        self.assertFalse(Sollbestand.objects.exists() or Sollzeile.objects.exists())

    def test_eine_unlesbare_datei_sperrt_nichts(self):
        with (
            mock.patch.object(WinTicket, "sollbestand_lesen", side_effect=Lesefehler("kaputt")),
            mock.patch.object(target_stock, "soll_darf_herein") as sperren,
            self.assertRaises(Lesefehler),
        ):
            self.einlesen()

        sperren.assert_not_called()


def versuchen(tun):
    """What 'tun' returns, or the refusal it raises: in a thread an exception would be lost."""
    try:
        return tun()
    except ValidationError as fehler:
        return fehler


@skipUnless(connection.vendor == "postgresql", "SQLite kennt keine Sperre einzelner Zeilen")
class InZweiVerbindungen(Nebeneinander):
    """Both sides at the same moment, each in a connection of its own."""

    def setUp(self):
        super().setUp()
        self.inventur = inventur_anlegen(self.haus, stichtag=STICHTAG)
        self.inventur.zaehlpositionen_anlegen()
        self.zaehlerin = CustomUser.objects.create_user(
            username="zaehlerin", password="geheim", email="zaehlerin@example.org"
        )

    def zaehlende_eintragen(self):
        client = Client()
        client.force_login(self.chef)
        return client.post(
            reverse("inventur_zaehlende", args=[self.inventur.pk]),
            {"zaehlende": [self.zaehlerin.pk]},
        )

    def abschliessen(self):
        return abschliessen(Inventur.objects.get(pk=self.inventur.pk), Entscheidungen())

    def hochladen(self):
        return versuchen(
            lambda: sollbestand_einlesen(
                datei("101001;30\n"),
                Inventur.objects.get(pk=self.inventur.pk),
                format_holen(STANDARD),
            )
        )

    def von_hand(self):
        return versuchen(
            lambda: Inventur.objects.get(pk=self.inventur.pk).differenz_von_hand_setzen(True)
        )

    def test_der_abschluss_zwischen_blick_und_eintrag_der_zaehlenden(self):
        """The counters page looked, the closing runs through, then the page writes."""
        self.inventur.zaehlung_starten()

        ergebnis = self.gleichzeitig(
            self.zaehlende_eintragen, self.abschliessen, (ZaehlendeForm, "speichern")
        )

        self.assertRedirects(
            ergebnis["zuerst"], self.inventur.get_absolute_url(), fetch_redirect_response=False
        )
        inventur = Inventur.objects.get(pk=self.inventur.pk)
        self.assertEqual(inventur.status, Inventurstatus.ABGESCHLOSSEN)
        self.assertFalse(inventur.zaehlende.exists())

    def test_der_upload_wartet_auf_den_schalter(self):
        """The switch holds the inventory. The upload waits, then sees the difference by hand."""
        ergebnis = self.gleichzeitig(self.von_hand, self.hochladen, (Inventur, "save"))

        self.assertIsInstance(ergebnis["danach"], ValidationError)
        self.assertTrue(Inventur.objects.get(pk=self.inventur.pk).differenz_von_hand)
        self.assertFalse(Sollbestand.objects.exists() or Sollzeile.objects.exists())

    def test_der_schalter_wartet_nicht_auf_das_lesen(self):
        """No lock while the file is read: the switch goes through, the upload sees it after."""
        ergebnis = self.gleichzeitig(
            self.hochladen, self.von_hand, (WinTicket, "sollbestand_lesen")
        )

        self.assertIsInstance(ergebnis["zuerst"], ValidationError)
        self.assertEqual(ergebnis["zuerst"].messages, [OHNE_SOLL])
        self.assertIsNone(ergebnis["danach"])
        self.assertTrue(Inventur.objects.get(pk=self.inventur.pk).differenz_von_hand)
        self.assertFalse(Sollbestand.objects.exists() or Sollzeile.objects.exists())

    def test_der_schalter_wartet_auf_das_schreiben(self):
        """The upload holds the inventory while it writes. The switch then finds a target."""
        ergebnis = self.gleichzeitig(
            self.hochladen, self.von_hand, (target_stock.Zuordner, "schreiben")
        )

        self.assertIsInstance(ergebnis["danach"], ValidationError)
        self.assertFalse(Inventur.objects.get(pk=self.inventur.pk).differenz_von_hand)
        self.assertTrue(Sollbestand.objects.exists())
