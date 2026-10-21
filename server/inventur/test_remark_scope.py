"""A remark only where the check list can show it: not on a product that is none, not outside.

A remark freezes a row at closing ('Inventur._ergebnisse'). Moving a product
into "Not countable" deletes the remarks of its articles in running
inventories on purpose ('katalog.hausartikel_status.aufraeumen'). An article
whose type the inventory does not count and that has no row in it is not
part of it at all. Until 10/2026 a note or a difference from the app, or
from a page loaded before, landed on either, and closing froze a row for it.
Emptying stays allowed everywhere: it freezes nothing. 'WaehrendDieWareGeht'
moves a product into "Not countable" beside a new note on PostgreSQL.
"""

from decimal import Decimal
from http import HTTPStatus
from unittest import skipUnless

from django.db import connection
from django.test import Client
from django.urls import reverse
from rest_framework.test import APIClient

from api.test_review_list import PruefGrundlagen
from api.tests import STICHTAG, adresse
from django_project.testing import fuehrungskraft, inventur_anlegen, jahresinventur
from katalog.models import Hausartikel, Ware

from .models import Inventur, Inventurnotiz, Zaehlposition
from .services import notes
from .test_merge_race import Nebeneinander
from .views.review import NICHT_GESPEICHERT_KOPF

#: What the app and the page hear for an article in "Not countable".
NICHT_ZAEHLBAR = (
    "„Ware P“ ist nicht zählbar. Eine Notiz, „Soll zählt“ oder eine Differenz gibt es dafür nicht."
)
#: And for one of a type the inventory does not count.
NICHT_DRAN = (
    "„Ware J“ ist bei dieser Inventur nicht dran. Eine Notiz, „Soll zählt“ oder eine "
    "Differenz gibt es dafür hier nicht."
)


class Grundlage(PruefGrundlagen):
    """Beside the fixture's articles a product in "Not countable" and an annual article.

    Ware P was expected at the counter and had its row before its product
    moved. Ware J belongs to the annual inventory and has no row.
    """

    def setUp(self):
        pfand = Ware.objects.create(name="Ware P", basiseinheit=self.stueck)
        self.ignoriert = Hausartikel.objects.create(
            haus=self.haus,
            ware=pfand,
            warengruppe=self.gruppe,
            artikelnummer="101005",
            name="Ware P",
        )
        Zaehlposition.objects.create(inventur=self.inventur, ort=self.theke, artikel=self.ignoriert)
        pfand.ignorieren()
        self.jahr = Hausartikel.objects.create(
            haus=self.haus,
            ware=Ware.objects.create(name="Ware J", basiseinheit=self.stueck),
            warengruppe=self.gruppe,
            artikelnummer="101006",
            name="Ware J",
            inventurart=jahresinventur(),
        )
        self.anmelden()

    def eingefroren(self) -> set[str]:
        """The numbers closing freezes a row for."""
        inventur = Inventur.objects.get(pk=self.inventur.pk)
        inventur.abschliessen()
        return set(inventur.ergebnisse.values_list("hausartikel__artikelnummer", flat=True))


class InDerApp(Grundlage):
    def test_kein_vermerk_auf_einem_nicht_zaehlbaren_artikel(self):
        self.inventur.differenz_von_hand_setzen(True)
        for daten in ({"notiz": "Rest im Lager"}, {"differenz": "-3"}):
            with self.subTest(daten):
                rsp = self.aendern(self.ignoriert, **daten)

                self.assertEqual(rsp.status_code, HTTPStatus.BAD_REQUEST, rsp.data)
                self.assertEqual(rsp.data["detail"], [NICHT_ZAEHLBAR])
        self.assertFalse(Inventurnotiz.objects.exists())
        self.assertNotIn("101005", self.eingefroren())

    def test_kein_vermerk_ausserhalb_der_arten(self):
        rsp = self.aendern(self.jahr, notiz="steht hinten")

        self.assertEqual(rsp.status_code, HTTPStatus.BAD_REQUEST, rsp.data)
        self.assertEqual(rsp.data["detail"], [NICHT_DRAN])
        self.assertFalse(Inventurnotiz.objects.exists())
        self.assertNotIn("101006", self.eingefroren())

    def test_mit_einer_zeile_gehoert_er_dazu(self):
        """Counted on top in the app, the annual article stands in the check list."""
        Zaehlposition.objects.create(
            inventur=self.inventur, ort=self.theke, artikel=self.jahr, eingabemenge=Decimal("4")
        )

        rsp = self.aendern(self.jahr, notiz="steht hinten")

        self.assertEqual(rsp.status_code, HTTPStatus.OK, rsp.data)
        self.assertEqual(rsp.data["zeile"]["notiz"], "steht hinten")

    def test_ueber_eine_rezeptur_ebenso(self):
        """A portion counted at the counter lands on its ingredient, whose type is not counted."""
        self.nachos.inventurart = jahresinventur()
        self.nachos.save(update_fields=["inventurart"])
        Zaehlposition.objects.create(
            inventur=self.inventur, ort=self.theke, artikel=self.portion, eingabemenge=Decimal("2")
        )

        rsp = self.aendern(self.nachos, notiz="zwei Portionen")

        self.assertEqual(rsp.status_code, HTTPStatus.OK, rsp.data)

    def test_ein_gespeicherter_vermerk_bleibt_aenderbar(self):
        """The check list shows a row with a remark, so its remark can change."""
        Inventurnotiz.objects.create(inventur=self.inventur, hausartikel=self.jahr, text="alt")

        rsp = self.aendern(self.jahr, notiz="neu", notiz_vorher="alt")

        self.assertEqual(rsp.status_code, HTTPStatus.OK, rsp.data)
        self.assertEqual(self.inventur.notizen.get().text, "neu")

    def test_leeren_geht_immer(self):
        """A remark from before is emptied even on an article that is none: it freezes nothing."""
        Inventurnotiz.objects.create(
            inventur=self.inventur, hausartikel=self.ignoriert, text="Rest im Lager"
        )

        rsp = self.aendern(self.ignoriert, notiz="", notiz_vorher="Rest im Lager")

        self.assertEqual(rsp.status_code, HTTPStatus.OK, rsp.data)
        self.assertFalse(Inventurnotiz.objects.exists())


class InDerPruefliste(Grundlage):
    """A page loaded before the product moved still shows the row. The other rows are saved."""

    def senden(self, felder: dict):
        client = Client()
        client.force_login(fuehrungskraft())
        return client.post(
            reverse("inventur_pruefen", args=[self.inventur.pk]),
            felder | {"nebenbei": "1"},
            HTTP_X_REQUESTED_WITH="fetch",
        )

    def test_die_uebrigen_zeilen_sind_gespeichert(self):
        cola, ignoriert, jahr = self.cola.pk, self.ignoriert.pk, self.jahr.pk

        antwort = self.senden(
            {
                f"notiz_{cola}": "zwei Kästen hinten",
                f"notiz_vorher_{cola}": "",
                f"notiz_{ignoriert}": "Rest im Lager",
                f"notiz_vorher_{ignoriert}": "",
                f"notiz_{jahr}": "steht hinten",
                f"notiz_vorher_{jahr}": "",
            }
        )

        self.assertEqual(antwort.status_code, HTTPStatus.BAD_REQUEST)
        self.assertEqual(antwort.content.decode(), f"{NICHT_ZAEHLBAR} {NICHT_DRAN}")
        self.assertEqual(
            antwort[NICHT_GESPEICHERT_KOPF].split(),
            [f"notiz_{ignoriert}", f"differenz_{ignoriert}", f"notiz_{jahr}", f"differenz_{jahr}"],
        )
        self.assertEqual(
            dict(Inventurnotiz.objects.values_list("hausartikel_id", "text")),
            {cola: "zwei Kästen hinten"},
        )
        self.assertEqual(self.eingefroren(), {"101001"})

    def test_ohne_inhalt_nimmt_sie_alles(self):
        Inventurnotiz.objects.create(
            inventur=self.inventur, hausartikel=self.ignoriert, text="Rest im Lager"
        )
        pk = self.ignoriert.pk

        antwort = self.senden({f"notiz_{pk}": "", f"notiz_vorher_{pk}": "Rest im Lager"})

        self.assertEqual(antwort.status_code, HTTPStatus.NO_CONTENT)
        self.assertFalse(Inventurnotiz.objects.exists())

    def test_ein_fremder_artikel_bleibt_wie_bisher(self):
        """Of another house: the page drops it without a word, the app hears 400 as before."""
        fremd = Hausartikel.objects.create(
            haus=self.fremdes_haus,
            ware=self.ware,
            warengruppe=self.gruppe,
            artikelnummer="101001",
            name="Artikel C fremd",
        )

        antwort = self.senden({f"notiz_{fremd.pk}": "Bruch", f"notiz_vorher_{fremd.pk}": ""})

        self.assertEqual(antwort.status_code, HTTPStatus.NO_CONTENT)
        self.assertFalse(Inventurnotiz.objects.exists())
        self.assertEqual(
            self.client.patch(
                adresse("pruefliste_zeile", self.inventur.pk, fremd.pk),
                {"notiz": "Bruch"},
                format="json",
            ).status_code,
            HTTPStatus.BAD_REQUEST,
        )


@skipUnless(connection.vendor == "postgresql", "SQLite kennt keine Sperre einzelner Zeilen")
class WaehrendDieWareGeht(Nebeneinander):
    """The product moves into "Not countable" while a new note is on its way, in both orders.

    The note locks the article after the inventory and looks at it under
    that lock. Whoever comes second waits: the note then hears that the
    article is none, or the product's save deletes the note it waited for.
    """

    def setUp(self):
        super().setUp()
        self.inventur = inventur_anlegen(self.haus, stichtag=STICHTAG)
        self.inventur.zaehlpositionen_anlegen()
        self.inventur.zaehlung_starten()
        self.inventur.zaehlende.add(self.chef)

    def ignorieren(self):
        Ware.objects.get(pk=self.ware.pk).ignorieren()

    def notiz(self):
        client = APIClient(raise_request_exception=False)
        client.force_authenticate(self.chef)
        return client.patch(
            adresse("pruefliste_zeile", self.inventur.pk, self.artikel.pk),
            {"notiz": "Bruch"},
            format="json",
        )

    def test_die_notiz_nach_der_ware(self):
        """Held after the article dropped out, while the product's save holds the article."""
        ergebnis = self.gleichzeitig(
            self.ignorieren, self.notiz, (Hausartikel, "_plaetze_wiederherstellen")
        )

        self.assertEqual(ergebnis["danach"].status_code, HTTPStatus.BAD_REQUEST)
        self.assertIn("ist nicht zählbar", str(ergebnis["danach"].data["detail"]))
        self.assertFalse(Inventurnotiz.objects.exists())

    def test_die_ware_nach_der_notiz(self):
        """Held once the note has locked inventory and article, before it reads."""
        ergebnis = self.gleichzeitig(self.notiz, self.ignorieren, (notes, "_gespeicherte"))

        self.assertEqual(ergebnis["zuerst"].status_code, HTTPStatus.OK)
        self.assertFalse(Inventurnotiz.objects.exists())
