"""Deleting on the website what a new row of the app hangs on: a pack, an article, a location.

A new row locks its location and its article before it reads them and its
pack ('serializers.ort_sperren', 'gesperrter_artikel'). The website locks
the same before PROTECT looks: the product page the product's articles
before it checks the packs ticked for deletion ('WareUpdateView.form_valid'),
the pages that delete an article or a location that row
('GeschuetztesLoeschen.zuerst_sperren'). A row created first is then seen
and becomes the page's message, one that comes later finds what it hangs on
gone and hears 400 in the app's words. Until 10/2026 PROTECT missed a row
that was not committed yet, and one of the two commits ended in a server
error. Deleting a pack that count positions hang on was one even without a
second device. The overlap is forced on PostgreSQL in 'GleichzeitigGeloescht'.
"""

import threading
from decimal import Decimal
from http import HTTPStatus
from unittest import mock, skipUnless

from django.db import connection
from django.test import Client
from django.urls import reverse
from rest_framework.test import APIClient, APITransactionTestCase

from accounts.models import CustomUser
from api.serializers import ARTIKEL_WEG, GEBINDE_WEG, ORT_WEG, zahl
from api.test_closing_race import _bis_jemand_wartet
from api.test_merge_split_race import abfragen_waehrend
from api.tests import STICHTAG, ApiGrundlagen, adresse
from django_project.testing import inventur_anlegen
from inventur.models import Zaehlposition
from katalog.models import (
    Artikel,
    Basiseinheit,
    Erwartung,
    ErwartungGebinde,
    Gebinde,
    Hausartikel,
    Ware,
    Warengruppe,
)
from organisation.models import Bereich, Haus, Ort


def seite_der_ware(client, ware: Ware, loeschen: set[int]):
    """The product page sent as the browser does, with the packs in 'loeschen' ticked."""
    gebinde = list(ware.gebinde.order_by("sortierung"))
    daten = {
        "name": ware.name,
        "basiseinheit": ware.basiseinheit_id,
        "gebinde-TOTAL_FORMS": str(len(gebinde)),
        "gebinde-INITIAL_FORMS": str(len(gebinde)),
        "gebinde-MIN_NUM_FORMS": "0",
        "gebinde-MAX_NUM_FORMS": "1000",
    }
    for stelle, satz in enumerate(gebinde):
        daten |= {
            f"gebinde-{stelle}-id": str(satz.pk),
            f"gebinde-{stelle}-ware": str(ware.pk),
            f"gebinde-{stelle}-name": satz.name,
            f"gebinde-{stelle}-faktor_in_basiseinheit": zahl(satz.faktor_in_basiseinheit),
        }
        if satz.ist_einzeln:
            daten[f"gebinde-{stelle}-ist_einzeln"] = "on"
        if satz.pk in loeschen:
            daten[f"gebinde-{stelle}-DELETE"] = "on"
    return client.post(reverse("ware_edit", args=[ware.pk]), daten)


class AlsFuehrungskraft(ApiGrundlagen):
    """The website as a manager uses it: forms, not the API's JSON."""

    def setUp(self):
        super().setUp()
        self.website = Client()
        self.website.force_login(
            CustomUser.objects.create_superuser(
                username="chef", password="geheim", email="chef@example.org"
            )
        )


class GebindeAufDerSeiteDerWareLoeschen(AlsFuehrungskraft):
    """A pack count positions hang on stays, and the page says which inventory holds it."""

    def test_ein_gezaehltes_gebinde_bleibt_und_die_seite_nennt_die_inventur(self):
        """Until 10/2026 a server error, the row at the counter holds the crate."""
        antwort = seite_der_ware(self.website, self.ware, {self.kasten.pk})

        self.assertEqual(antwort.status_code, HTTPStatus.OK)
        fehler = " ".join(antwort.context["formsets"]["gebinde"].non_form_errors())
        self.assertIn("An „Kasten“ hängen noch Zählpositionen von", fehler)
        self.assertIn(str(self.inventur), fehler)
        self.assertTrue(Gebinde.objects.filter(pk=self.kasten.pk).exists())

    def test_ein_ungezaehltes_gebinde_geht(self):
        antwort = seite_der_ware(self.website, self.ware, {self.einzeln.pk})

        self.assertEqual(antwort.status_code, HTTPStatus.FOUND)
        self.assertFalse(Gebinde.objects.filter(pk=self.einzeln.pk).exists())

    def test_die_seite_sperrt_die_artikel_bevor_sie_die_gebinde_prueft(self):
        abfragen = abfragen_waehrend(
            lambda: seite_der_ware(self.website, self.ware, {self.einzeln.pk})
        )

        sperre = next(
            stelle
            for stelle, (modell, gesperrt, *_rest) in enumerate(abfragen)
            if modell is Artikel and gesperrt
        )
        pruefung = next(
            stelle for stelle, (modell, *_rest) in enumerate(abfragen) if modell is Zaehlposition
        )
        self.assertLess(sperre, pruefung)
        self.assertTrue(abfragen[sperre][2], "die Artikel mit FOR UPDATE statt FOR NO KEY UPDATE")


class LoeschenSperrtZuerst(AlsFuehrungskraft):
    """Deleting an article or a location locks its row before PROTECT looks for count positions."""

    def test_artikel_und_ort(self):
        """An article on the row of 'Artikel', which the app locks, and alone, without joins."""
        for name, satz, gesperrt_auf in (
            ("hausartikel_delete", self.nachos, Artikel),
            ("rezepturartikel_delete", self.portion, Artikel),
            ("ort_delete", self.lager, Ort),
        ):
            with self.subTest(name):
                antworten = []
                abfragen = abfragen_waehrend(
                    lambda: antworten.append(self.website.post(reverse(name, args=[satz.pk])))
                )
                self.assertEqual(antworten[0].status_code, HTTPStatus.FOUND)
                self.assertFalse(type(satz).objects.filter(pk=satz.pk).exists())
                gesperrt = [
                    stelle
                    for stelle, (art, sperre, *_rest) in enumerate(abfragen)
                    if art is gesperrt_auf and sperre
                ]
                self.assertTrue(gesperrt, "die Zeile nicht gesperrt")
                schutz = next(
                    stelle for stelle, (art, *_rest) in enumerate(abfragen) if art is Zaehlposition
                )
                self.assertLess(gesperrt[0], schutz)
                self.assertNotIn("JOIN", abfragen[gesperrt[0]][4].upper())


@skipUnless(connection.vendor == "postgresql", "SQLite kennt keine Sperre einzelner Zeilen")
class GleichzeitigGeloescht(APITransactionTestCase):
    """A new row of the app and the website deleting its pack, article or location, both orders.

    The side that holds the lock first finishes. Each side waits until the
    other one waits for a lock ('test_closing_race._bis_jemand_wartet'). Runs
    in the CI, which tests against PostgreSQL.
    """

    serialized_rollback = True

    def setUp(self):
        self.haus = Haus.objects.create(name="Kino Ost")
        bereich = Bereich.objects.create(name="Theke", haus=self.haus)
        theke = Ort.objects.create(name="Thekenfront", bereich=bereich)
        self.lager = Ort.objects.create(name="Trockenlager", bereich=bereich)
        self.vitrine = Ort.objects.create(name="Vitrine", bereich=bereich)
        stueck = Basiseinheit.objects.get(name="Stück(e)")
        gruppe = Warengruppe.objects.create(name="Warengruppe G")
        self.ware = Ware.objects.create(name="Artikel C", basiseinheit=stueck)
        kasten = Gebinde.objects.create(
            ware=self.ware, name="Kasten", faktor_in_basiseinheit=Decimal("20")
        )
        # Nobody counted on the pallet yet: the product page may delete it.
        self.palette = Gebinde.objects.create(
            ware=self.ware, name="Palette", faktor_in_basiseinheit=Decimal("480")
        )
        self.artikel = Hausartikel.objects.create(
            haus=self.haus,
            ware=self.ware,
            warengruppe=gruppe,
            artikelnummer="101001",
            name="Artikel C",
        )
        ErwartungGebinde.objects.create(
            erwartung=Erwartung.objects.create(ort=theke, artikel=self.artikel), gebinde=kasten
        )
        # Expected nowhere: its page may delete it.
        ware_n = Ware.objects.create(name="Ware N", basiseinheit=stueck)
        self.karton = Gebinde.objects.create(
            ware=ware_n, name="Karton", faktor_in_basiseinheit=Decimal("12")
        )
        self.ware_n = Hausartikel.objects.create(
            haus=self.haus, ware=ware_n, warengruppe=gruppe, artikelnummer="101002", name="Ware N"
        )
        self.inventur = inventur_anlegen(self.haus, stichtag=STICHTAG)
        self.inventur.zaehlpositionen_anlegen()
        self.konto = CustomUser.objects.create_user(
            username="erste", password="geheim", email="erste@example.org"
        )
        self.konto.haeuser.add(self.haus)
        self.inventur.zaehlende.add(self.konto)
        self.inventur.zaehlung_starten()
        self.chef = CustomUser.objects.create_superuser(
            username="chef", password="geheim", email="chef@example.org"
        )

    def anlegen(self, ort: Ort, artikel: Hausartikel, gebinde: Gebinde):
        """What the app sends for a row nobody expected."""

        def senden():
            client = APIClient(raise_request_exception=False)
            client.force_authenticate(self.konto)
            return client.post(
                adresse("zaehlposition-list"),
                {
                    "inventur": self.inventur.pk,
                    "ort": ort.pk,
                    "artikel": artikel.pk,
                    "gebinde": gebinde.pk,
                    "eingabemenge": "2",
                },
                format="json",
            )

        return senden

    def auf_der_website(self, senden):
        def schicken():
            client = Client(raise_request_exception=False)
            client.force_login(self.chef)
            return senden(client)

        return schicken

    def gleichzeitig(self, app, website, halt, *, app_zuerst: bool) -> dict:
        """Runs both in two threads, the first one held while it holds its lock.

        The new row is held right before it is written ('Zaehlposition.save'),
        the website where 'halt' names, before it deletes.
        """
        ziel, name = (Zaehlposition, "save") if app_zuerst else halt
        gehalten = threading.Event()
        fertig = {"app": threading.Event(), "website": threading.Event()}
        ergebnis: dict = {}
        echt = getattr(ziel, name)
        warten_auf = fertig["website"] if app_zuerst else fertig["app"]

        def halten(*args, **kwargs):
            if not gehalten.is_set():
                gehalten.set()
                _bis_jemand_wartet(warten_auf)
            return echt(*args, **kwargs)

        def laufen(seite, tun, zuerst):
            if not zuerst:
                gehalten.wait(timeout=10)
            try:
                ergebnis[seite] = tun()
            finally:
                fertig[seite].set()
                connection.close()

        with mock.patch.object(ziel, name, autospec=True, side_effect=halten):
            faeden = [
                threading.Thread(target=laufen, args=("app", app, app_zuerst)),
                threading.Thread(target=laufen, args=("website", website, not app_zuerst)),
            ]
            for faden in faeden:
                faden.start()
            for faden in faeden:
                faden.join()
        return ergebnis

    def pruefe_abgelehnt(self, antwort, feld: str, meldung):
        self.assertEqual(antwort.status_code, HTTPStatus.BAD_REQUEST)
        self.assertEqual([str(text) for text in antwort.data[feld]], [str(meldung)])

    def pruefe_angelegt(self, antwort, **wo):
        self.assertEqual(antwort.status_code, HTTPStatus.CREATED, antwort.data)
        self.assertEqual(Zaehlposition.objects.get(pk=antwort.data["id"], **wo).eingabemenge, 2)

    # -- A pack on the product page ------------------------------------------

    def gebinde_loeschen(self):
        return self.auf_der_website(
            lambda client: seite_der_ware(client, self.ware, {self.palette.pk})
        )

    def test_die_neue_zeile_wartet_aufs_loeschen_des_gebindes(self):
        ergebnis = self.gleichzeitig(
            self.anlegen(self.lager, self.artikel, self.palette),
            self.gebinde_loeschen(),
            (Gebinde, "delete"),
            app_zuerst=False,
        )

        self.assertEqual(ergebnis["website"].status_code, HTTPStatus.FOUND)
        self.pruefe_abgelehnt(ergebnis["app"], "gebinde", GEBINDE_WEG)
        self.assertFalse(Gebinde.objects.filter(pk=self.palette.pk).exists())

    def test_das_loeschen_des_gebindes_wartet_auf_die_neue_zeile(self):
        ergebnis = self.gleichzeitig(
            self.anlegen(self.lager, self.artikel, self.palette),
            self.gebinde_loeschen(),
            (Gebinde, "delete"),
            app_zuerst=True,
        )

        self.pruefe_angelegt(ergebnis["app"], gebinde=self.palette)
        seite = ergebnis["website"]
        self.assertEqual(seite.status_code, HTTPStatus.OK)
        self.assertIn(
            "Zählpositionen", " ".join(seite.context["formsets"]["gebinde"].non_form_errors())
        )

    # -- An article on its delete page -----------------------------------------

    def artikel_loeschen(self):
        return self.auf_der_website(
            lambda client: client.post(reverse("hausartikel_delete", args=[self.ware_n.pk]))
        )

    def test_die_neue_zeile_wartet_aufs_loeschen_des_artikels(self):
        ergebnis = self.gleichzeitig(
            self.anlegen(self.lager, self.ware_n, self.karton),
            self.artikel_loeschen(),
            (Hausartikel, "delete"),
            app_zuerst=False,
        )

        self.assertEqual(ergebnis["website"].status_code, HTTPStatus.FOUND)
        self.pruefe_abgelehnt(ergebnis["app"], "artikel", ARTIKEL_WEG)
        self.assertFalse(Hausartikel.objects.filter(pk=self.ware_n.pk).exists())

    def test_das_loeschen_des_artikels_wartet_auf_die_neue_zeile(self):
        ergebnis = self.gleichzeitig(
            self.anlegen(self.lager, self.ware_n, self.karton),
            self.artikel_loeschen(),
            (Hausartikel, "delete"),
            app_zuerst=True,
        )

        self.pruefe_angelegt(ergebnis["app"], artikel=self.ware_n)
        # Back to the article's page with the message what holds it.
        self.assertEqual(ergebnis["website"].status_code, HTTPStatus.FOUND)
        self.assertTrue(Hausartikel.objects.filter(pk=self.ware_n.pk).exists())

    # -- A location on its delete page -------------------------------------------

    def ort_loeschen(self):
        return self.auf_der_website(
            lambda client: client.post(reverse("ort_delete", args=[self.vitrine.pk]))
        )

    def test_die_neue_zeile_wartet_aufs_loeschen_des_orts(self):
        ergebnis = self.gleichzeitig(
            self.anlegen(self.vitrine, self.artikel, self.palette),
            self.ort_loeschen(),
            (Ort, "delete"),
            app_zuerst=False,
        )

        self.assertEqual(ergebnis["website"].status_code, HTTPStatus.FOUND)
        self.pruefe_abgelehnt(ergebnis["app"], "ort", ORT_WEG)
        self.assertFalse(Ort.objects.filter(pk=self.vitrine.pk).exists())

    def test_das_loeschen_des_orts_wartet_auf_die_neue_zeile(self):
        ergebnis = self.gleichzeitig(
            self.anlegen(self.vitrine, self.artikel, self.palette),
            self.ort_loeschen(),
            (Ort, "delete"),
            app_zuerst=True,
        )

        self.pruefe_angelegt(ergebnis["app"], ort=self.vitrine)
        self.assertEqual(ergebnis["website"].status_code, HTTPStatus.FOUND)
        self.assertTrue(Ort.objects.filter(pk=self.vitrine.pk).exists())
