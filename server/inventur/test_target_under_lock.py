"""The target stock under the inventory's lock: the upload, the target by hand, the clarification.

The pages look at the inventory before they write, whether it is still in
preparation. Under the lock of the target ('soll_darf_herein') only the
difference by hand was looked at once more, and the clarification locked
nothing at all (until 10/2026). A start of counting between the look and the
write let the file come in during the count and replace the target the apps
were already counting against. Two answers to the same row of the
clarification both added its target, and a new file in between left its
line open beside the answer or ended in a server error.

The first classes force the moment in between in one connection, on SQLite
as on PostgreSQL, and read the locks off the queries. 'KlaerungNebeneinander'
runs both sides in two connections on PostgreSQL ('test_merge_race.Nebeneinander').
"""

from decimal import Decimal
from http import HTTPStatus
from unittest import mock, skipUnless

from django.core.exceptions import ValidationError
from django.db import connection
from django.test import Client
from django.urls import reverse

from api.test_merge_split_race import abfragen_waehrend
from api.tests import STICHTAG
from django_project.testing import inventur_anlegen
from katalog.formats import STANDARD
from katalog.models import Artikel, Artikelstatus, Hausartikel, Warengruppe

from .forms import SollbestandImportForm
from .models import Inventur, Inventurstatus, Sollbestand, Sollzeile, Zaehlposition
from .services import (
    clarification,
    ohne_soll_ausscheiden,
    soll_von_hand_speichern,
    sollbestand_einlesen,
    sollzeile_aktivieren,
    sollzeile_dazurechnen,
    sollzeilen_erneut_zuordnen,
    sollzeilen_verwerfen,
    target_stock,
)
from .test_check_under_lock import meldungen
from .test_merge_race import Nebeneinander, erste_abfrage
from .test_sollbestand import Sollgrundlagen
from .test_views import InventurSeiten, datei
from .views import sollbestand as klaerungsseite
from .views.sollbestand import SollbestandKlaerungView

#: What the upload says once counting has begun, before and under the lock.
NUR_VORBEREITUNG = (
    "Der Sollbestand kommt in der Vorbereitung herein. Läuft die Zählung schon: "
    "„Zurück in die Vorbereitung“, dann die Datei. Die gezählten Mengen bleiben."
)
#: And the clarification of the target.
NUR_IN_DER_KLAERUNG = (
    "Der Sollbestand wird in der Vorbereitung geklärt. Danach halten die Zähl-Apps ihre "
    "Zeilen, und die Stammdaten sollen sich nicht mehr unter ihnen bewegen."
)


class StartZwischenBlickUndSoll(InventurSeiten):
    """The page saw the preparation. Then counting started, then the page wrote."""

    def setUp(self):
        super().setUp()
        self.vorbereitet = self.inventur()
        sollbestand_einlesen(datei("101001;10\n"), self.vorbereitet)

    def starten(self) -> None:
        Inventur.objects.get(pk=self.vorbereitet.pk).zaehlung_starten()

    def pruefe_unberuehrt(self) -> None:
        self.assertEqual(
            Inventur.objects.get(pk=self.vorbereitet.pk).status, Inventurstatus.ZAEHLUNG
        )
        self.assertEqual(Sollbestand.objects.get().menge_basiseinheit, Decimal("10"))

    def test_die_datei_kommt_nicht_mehr_herein(self):
        echt = SollbestandImportForm.is_valid

        def mit_start(form):
            self.starten()
            return echt(form)

        with mock.patch.object(SollbestandImportForm, "is_valid", mit_start):
            antwort = self.client.post(
                self.vorbereitet.get_absolute_url(),
                {"datei": datei("101001;99\n"), "format": STANDARD},
            )

        self.assertRedirects(antwort, self.vorbereitet.get_absolute_url())
        self.assertEqual(meldungen(antwort), [NUR_VORBEREITUNG])
        self.pruefe_unberuehrt()

    def test_ebenso_der_sollbestand_von_hand(self):
        """The maintenance page with the inventory as it loaded it, before the start."""
        self.starten()

        with self.assertRaisesMessage(ValidationError, NUR_VORBEREITUNG):
            soll_von_hand_speichern(self.vorbereitet, {self.artikel.pk: Decimal("3")})
        self.pruefe_unberuehrt()


class Klaerungsfall(Sollgrundlagen):
    """Kino Ost in preparation with its file read in, a question of every kind still open.

    101009 is the discontinued old number of Artikel C, whose target can go
    onto 101001. Artikel F is inactive and has a target, Artikel N is active
    and has none, 999999 the house does not know.
    """

    def setUp(self):
        super().setUp()
        self.alt = self.dublette(zaehlt_als=None)
        self.inaktiv = self.zweiter_artikel("Artikel F", "101002", status=Artikelstatus.INAKTIV)
        self.ohne = self.zweiter_artikel("Artikel N", "101003")
        self.inv = self.inventur()
        self.einlesen(self.inv, "101001;10\n101009;4;Artikel C Dose\n101002;3\n999999;1\n")
        self.adresse = reverse("inventur_sollbestand", args=[self.inv.pk])

    def zeile(self, nummer: str) -> Sollzeile:
        return Sollzeile.objects.select_related("hausartikel").get(artikelnummer=nummer)

    def soll(self) -> dict[str, Decimal]:
        return dict(
            Sollbestand.objects.values_list("hausartikel__artikelnummer", "menge_basiseinheit")
        )

    def antworten(self) -> dict[str, dict]:
        """Every answer of the page, as its form sends it."""
        return {
            "dazurechnen": {"aktion": "dazurechnen", "zeile": self.zeile("101009").pk},
            "aktivieren": {"aktion": "aktivieren", "zeile": self.zeile("101002").pk},
            "verwerfen": {"aktion": "verwerfen", "zeile": self.zeile("999999").pk},
            "alle_verwerfen": {"aktion": "alle_verwerfen"},
            "erneut": {"aktion": "erneut"},
            "inaktiv": {"aktion": "inaktiv", "artikel": self.ohne.pk},
        }


class KlaerungZuerstGesperrt(Klaerungsfall):
    """Each answer locks the inventory without the key before it reads or writes."""

    def pruefe(self, schreiben, geschrieben=(Sollzeile, Sollbestand)) -> list:
        abfragen = abfragen_waehrend(schreiben)
        sperre = erste_abfrage(abfragen, Inventur, gesperrt=True)
        self.assertTrue(abfragen[sperre][2], "FOR UPDATE statt FOR NO KEY UPDATE")
        for modell in geschrieben:
            self.assertLess(sperre, erste_abfrage(abfragen, modell), modell.__name__)
        return abfragen

    def test_jede_antwort(self):
        """Discarding deletes without reading first, so there only the lock is looked at."""
        alt, inaktiv = self.zeile("101009"), self.zeile("101002")
        for name, schreiben, geschrieben in (
            ("dazurechnen", lambda: sollzeile_dazurechnen(alt), (Sollzeile, Sollbestand)),
            ("aktivieren", lambda: sollzeile_aktivieren(inaktiv), (Sollzeile, Sollbestand)),
            ("erneut", lambda: sollzeilen_erneut_zuordnen(self.inv), (Sollzeile,)),
            ("alle_verwerfen", lambda: sollzeilen_verwerfen(self.inv), ()),
            (
                "inaktiv",
                lambda: ohne_soll_ausscheiden(self.inv, self.ohne, Artikelstatus.INAKTIV),
                (Hausartikel,),
            ),
        ):
            with self.subTest(name):
                self.pruefe(schreiben, geschrieben)

    def test_ohne_soll_ausscheiden_sperrt_die_zeilen_vor_dem_aendern(self):
        """The article's empty rows are marked by number, after its lock, as everywhere.

        In preparation the app writes nothing. A status change of the article
        reaches the same rows, and it locks them first as well.
        """
        Zaehlposition.objects.create(inventur=self.inv, ort=self.ort, artikel=self.ohne)

        abfragen = abfragen_waehrend(
            lambda: ohne_soll_ausscheiden(self.inv, self.ohne, Artikelstatus.INAKTIV)
        )

        artikel = erste_abfrage(abfragen, Artikel, gesperrt=True)
        zeilen = erste_abfrage(abfragen, Zaehlposition, gesperrt=True)
        self.assertEqual(abfragen[zeilen][3], ("pk",))
        self.assertLess(artikel, zeilen)
        self.assertLess(artikel, erste_abfrage(abfragen, Artikel, beginnt="UPDATE"))
        self.assertLess(zeilen, erste_abfrage(abfragen, Zaehlposition, beginnt="UPDATE"))
        self.assertFalse(self.inv.zaehlpositionen.filter(artikel=self.ohne, aktiv=True).exists())


class StartWaehrendDerKlaerung(Klaerungsfall):
    """The page saw the preparation, then counting started, then the answer came.

    The answer goes nowhere, and the page says what it says for an inventory
    in the count.
    """

    def nach_dem_start(self, name: str) -> None:
        echt = SollbestandKlaerungView._ausfuehren

        def gestartet(view, request, inventur, aktion):
            Inventur.objects.get(pk=inventur.pk).zaehlung_starten()
            return echt(view, request, inventur, aktion)

        vorher = (self.soll(), Sollzeile.objects.count(), self.ohne.status)
        with mock.patch.object(SollbestandKlaerungView, "_ausfuehren", gestartet):
            antwort = self.client.post(self.adresse, self.antworten()[name])

        self.assertRedirects(antwort, self.inv.get_absolute_url())
        self.assertEqual(meldungen(antwort), [NUR_IN_DER_KLAERUNG])
        jetzt = Hausartikel.objects.get(pk=self.ohne.pk).status
        self.assertEqual((self.soll(), Sollzeile.objects.count(), jetzt), vorher)

    def test_dazurechnen(self):
        self.nach_dem_start("dazurechnen")

    def test_aktivieren(self):
        self.nach_dem_start("aktivieren")

    def test_verwerfen(self):
        self.nach_dem_start("verwerfen")

    def test_alle_verwerfen(self):
        self.nach_dem_start("alle_verwerfen")

    def test_erneut_zuordnen(self):
        self.nach_dem_start("erneut")

    def test_inaktiv_setzen(self):
        self.nach_dem_start("inaktiv")


class SchonBeantwortet(Klaerungsfall):
    """A second window sent the same answer, and it went through between lookup and write."""

    def zweimal(self, name: str, daten: dict):
        """Posts 'daten'. Right before the page's service runs, the other window's goes through."""
        echt = getattr(klaerungsseite, name)

        def nach_dem_anderen(zeile, *args):
            echt(Sollzeile.objects.get(pk=zeile.pk), *args)
            return echt(zeile, *args)

        with mock.patch.object(klaerungsseite, name, nach_dem_anderen):
            return self.client.post(self.adresse, daten)

    def test_das_soll_kommt_nur_einmal_dazu(self):
        zeile = self.zeile("101009")

        antwort = self.zweimal(
            "sollzeile_dazurechnen", {"aktion": "dazurechnen", "zeile": zeile.pk}
        )

        self.assertRedirects(antwort, self.adresse)
        self.assertEqual(meldungen(antwort), [BEANTWORTET])
        self.assertEqual(self.soll()["101001"], Decimal("14"))

    def test_ebenso_das_aktivieren(self):
        zeile = self.zeile("101002")

        antwort = self.zweimal("sollzeile_aktivieren", {"aktion": "aktivieren", "zeile": zeile.pk})

        self.assertIn("101002 ist inzwischen beantwortet", meldungen(antwort)[0])
        self.assertEqual(self.soll()["101002"], Decimal("3"))

    def test_ebenso_das_verwerfen(self):
        """Gone after the page found it: the other window discarded it."""
        zeile = self.zeile("999999")
        echt = klaerungsseite.get_object_or_404

        def gefunden_und_weg(*args, **kwargs):
            gefunden = echt(*args, **kwargs)
            Sollzeile.objects.filter(pk=gefunden.pk).delete()
            return gefunden

        with mock.patch.object(klaerungsseite, "get_object_or_404", gefunden_und_weg):
            antwort = self.client.post(self.adresse, {"aktion": "verwerfen", "zeile": zeile.pk})

        self.assertIn("999999 ist inzwischen beantwortet", meldungen(antwort)[0])


#: What the second answer to a row hears once the first went through.
BEANTWORTET = (
    "Zeile 101009 ist inzwischen beantwortet oder mit einer neuen Datei ersetzt. Hier steht, "
    "was jetzt offen ist."
)


@skipUnless(connection.vendor == "postgresql", "SQLite kennt keine Sperre einzelner Zeilen")
class KlaerungNebeneinander(Nebeneinander):
    """Two answers to the old number, or an answer and a new file, in two connections.

    Each holds the inventory from its first step on. Whoever comes second
    waits, then finds the row gone: answered, or replaced by the new file.
    Until 10/2026 the target counted twice, the new file's line stayed open
    beside an answer to the old one, or the answer ended in a server error.
    """

    def setUp(self):
        super().setUp()
        Hausartikel.objects.create(
            haus=self.haus,
            ware=self.ware,
            warengruppe=Warengruppe.objects.get(),
            artikelnummer="101009",
            name="Artikel C Dose",
            status=Artikelstatus.EINGESTELLT,
        )
        self.inventur = inventur_anlegen(self.haus, stichtag=STICHTAG)
        self.inventur.zaehlpositionen_anlegen()
        sollbestand_einlesen(datei("101001;10\n101009;4;Artikel C Dose\n"), self.inventur)
        self.zeile = Sollzeile.objects.get()

    def client_der_leitung(self) -> Client:
        client = Client(raise_request_exception=False)
        client.force_login(self.chef)
        return client

    def dazurechnen(self):
        return self.client_der_leitung().post(
            reverse("inventur_sollbestand", args=[self.inventur.pk]),
            {"aktion": "dazurechnen", "zeile": self.zeile.pk},
        )

    def hochladen(self):
        return self.client_der_leitung().post(
            self.inventur.get_absolute_url(),
            {"datei": datei("101001;10\n101009;6;Artikel C Dose\n"), "format": STANDARD},
        )

    def stand(self) -> tuple[dict, list]:
        soll = dict(
            Sollbestand.objects.values_list("hausartikel__artikelnummer", "menge_basiseinheit")
        )
        return soll, list(Sollzeile.objects.values_list("artikelnummer", "menge_basiseinheit"))

    def test_zweimal_dazurechnen(self):
        """Held once the first holds the inventory and has read the row."""
        ergebnis = self.gleichzeitig(
            self.dazurechnen, self.dazurechnen, (clarification, "_nachfolger")
        )

        self.assertEqual(self.stand(), ({"101001": Decimal("14")}, []))
        self.assertEqual(meldungen(ergebnis["danach"]), [BEANTWORTET])

    def test_die_datei_nach_dem_dazurechnen(self):
        """The new file waits, then adds the old number's stock by itself ('zaehlt_als')."""
        ergebnis = self.gleichzeitig(
            self.dazurechnen, self.hochladen, (clarification, "_nachfolger")
        )

        self.assertEqual(ergebnis["danach"].status_code, HTTPStatus.FOUND)
        self.assertEqual(self.stand(), ({"101001": Decimal("16")}, []))

    def test_das_dazurechnen_nach_der_datei(self):
        """Held after the file took the old target away. The answer finds its line replaced.

        Held where it writes the new one: the file itself is read before the
        lock ('sollbestand_einlesen'), and the answer would not wait for that.
        """
        ergebnis = self.gleichzeitig(
            self.hochladen, self.dazurechnen, (target_stock.Zuordner, "schreiben")
        )

        self.assertEqual(ergebnis["danach"].status_code, HTTPStatus.FOUND)
        self.assertEqual(meldungen(ergebnis["danach"]), [BEANTWORTET])
        self.assertEqual(self.stand(), ({"101001": Decimal("10")}, [("101009", Decimal("6"))]))
