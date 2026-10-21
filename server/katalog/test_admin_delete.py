"""Deleting in the administration what a new row of the app hangs on: a pack or an article.

The website locks before PROTECT looks ('django_project.deletion.zum_loeschen_sperren',
on the product's page 'artikel_sperren'). The administration does the same
('VerwaltungSperrtZuerst'): its delete page, the action "Delete selected" and
the packs on the product's form. A row created first is then seen, and the
page says what holds it. One that comes later finds what it hangs on gone and
hears 400. Until 10/2026 the administration deleted without the lock, and a
row the app created in the same moment ended one of the two commits in a
server error. 'ErstGesperrt' reads the lock off the queries on SQLite,
'Gleichzeitig' forces the overlap on PostgreSQL.
"""

import itertools
import threading
from decimal import Decimal
from http import HTTPStatus
from unittest import mock, skipUnless

from django.db import connection
from django.test import Client
from django.urls import reverse
from rest_framework.test import APIClient, APITransactionTestCase

from accounts.models import CustomUser
from api.serializers import ARTIKEL_WEG, GEBINDE_WEG, zahl
from api.test_closing_race import _bis_jemand_wartet
from api.test_merge_split_race import abfragen_waehrend
from api.tests import STICHTAG, ApiGrundlagen, adresse
from django_project.testing import inventur_anlegen
from inventur.models import Inventur, Zaehlposition
from organisation.models import Bereich, Haus, Ort

from .admin import HausartikelAdmin
from .models import (
    Artikel,
    Basiseinheit,
    Erwartung,
    ErwartungGebinde,
    Gebinde,
    Hausartikel,
    Rezepturartikel,
    Ware,
    Warengruppe,
)

#: Numbers for the articles created along the way, each one new.
NUMMERN = itertools.count(901)


def geschuetzt(antwort) -> bool:
    """Whether the administration answered that PROTECT holds what it was to delete.

    The delete page and the action name it in 'protected', an inline in the
    error of its formset.
    """
    if antwort.status_code != HTTPStatus.OK:
        return False
    kontext = antwort.context
    if "protected" in kontext:
        return bool(kontext["protected"])
    return any(
        fehler.code == "deleting_protected"
        for satz in kontext["inline_admin_formsets"]
        for fehler in satz.formset.non_form_errors().as_data()
    )


def loeschseite(client, satz):
    """The delete page of the administration, confirmed."""
    meta = type(satz)._meta
    return client.post(
        reverse(f"admin:{meta.app_label}_{meta.model_name}_delete", args=[satz.pk]),
        {"post": "yes"},
    )


def alle_loeschen(client, *saetze):
    """The action "Delete selected", confirmed, as the page sends it after asking."""
    meta = type(saetze[0])._meta
    return client.post(
        reverse(f"admin:{meta.app_label}_{meta.model_name}_changelist"),
        {
            "action": "delete_selected",
            "_selected_action": [str(satz.pk) for satz in saetze],
            "post": "yes",
        },
    )


def formular_der_ware(client, ware: Ware, loeschen: set[int]):
    """The product's form in the administration, its packs in 'loeschen' ticked for deletion."""
    gebinde = list(ware.gebinde.order_by("sortierung"))
    daten = {
        "name": ware.name,
        "gebinde-TOTAL_FORMS": str(len(gebinde)),
        "gebinde-INITIAL_FORMS": str(len(gebinde)),
        "gebinde-MIN_NUM_FORMS": "0",
        "gebinde-MAX_NUM_FORMS": "1000",
    }
    if ware.warenfamilie_id is not None:
        daten["warenfamilie"] = str(ware.warenfamilie_id)
    for stelle, satz in enumerate(gebinde):
        daten |= {
            f"gebinde-{stelle}-id": str(satz.pk),
            f"gebinde-{stelle}-ware": str(ware.pk),
            f"gebinde-{stelle}-name": satz.name,
            f"gebinde-{stelle}-faktor_in_basiseinheit": zahl(satz.faktor_in_basiseinheit),
        }
        if satz.ist_einzeln:
            daten[f"gebinde-{stelle}-ist_einzeln"] = "on"
        if satz.aktiv:
            daten[f"gebinde-{stelle}-aktiv"] = "on"
        if satz.pk in loeschen:
            daten[f"gebinde-{stelle}-DELETE"] = "on"
    return client.post(reverse("admin:katalog_ware_change", args=[ware.pk]), daten)


def zuerst_gesperrt(test, abfragen, modell, *, ohne_schluessel: bool) -> None:
    """'modell' locked by number and alone, before PROTECT looks at the count positions."""
    gesperrt = [
        stelle for stelle, (art, sperre, *_rest) in enumerate(abfragen) if art is modell and sperre
    ]
    test.assertTrue(gesperrt, f"{modell.__name__} nicht gesperrt")
    _art, _sperre, ohne, reihenfolge, sql = abfragen[gesperrt[0]]
    test.assertEqual(ohne, ohne_schluessel)
    test.assertEqual(reihenfolge, ("pk",))
    test.assertNotIn("JOIN", sql.upper())
    geschaut = next(stelle for stelle, (art, *_rest) in enumerate(abfragen) if art is Zaehlposition)
    test.assertLess(gesperrt[0], geschaut)


class ErstGesperrt(ApiGrundlagen):
    """Each way of the administration to delete, read off its queries."""

    def setUp(self):
        super().setUp()
        self.verwaltung = Client()
        self.verwaltung.force_login(
            CustomUser.objects.create_superuser(
                username="chef", password="geheim", email="chef@example.org"
            )
        )

    def loeschen(self, senden):
        antworten = []
        abfragen = abfragen_waehrend(lambda: antworten.append(senden()))
        return antworten[0], abfragen

    def test_ein_gebinde_sperrt_die_artikel_seiner_ware(self):
        """A new row locks its article, not its pack: so the product's articles, without the key."""
        for wie in (loeschseite, alle_loeschen):
            with self.subTest(wie.__name__):
                flasche = Gebinde.objects.create(
                    ware=self.ware, name="Glas", faktor_in_basiseinheit=Decimal("2")
                )
                antwort, abfragen = self.loeschen(lambda: wie(self.verwaltung, flasche))

                self.assertEqual(antwort.status_code, HTTPStatus.FOUND)
                self.assertFalse(Gebinde.objects.filter(pk=flasche.pk).exists())
                zuerst_gesperrt(self, abfragen, Artikel, ohne_schluessel=True)

    def hausartikel(self) -> Hausartikel:
        """One more house article nobody expects, with a number of its own."""
        nummer = f"101{next(NUMMERN)}"
        return Hausartikel.objects.create(
            haus=self.haus,
            ware=Ware.objects.create(name=f"Ware {nummer}", basiseinheit=self.stueck),
            warengruppe=self.gruppe,
            artikelnummer=nummer,
            name=f"Ware {nummer}",
        )

    def rezepturartikel(self) -> Rezepturartikel:
        return Rezepturartikel.objects.create(
            name=f"Anteil {next(NUMMERN)}", basiseinheit=self.stueck
        )

    def test_ein_artikel_sperrt_seine_zeile_von_artikel(self):
        """The row of 'Artikel', which the app locks, with 'FOR UPDATE': it is deleted."""
        faelle = (
            ("Hausartikel, Seite", loeschseite, self.hausartikel),
            ("Hausartikel, Aktion", alle_loeschen, self.hausartikel),
            ("Rezepturartikel, Seite", loeschseite, self.rezepturartikel),
            ("Rezepturartikel, Aktion", alle_loeschen, self.rezepturartikel),
            ("Artikelkern", loeschseite, lambda: Artikel.objects.get(pk=self.hausartikel().pk)),
        )
        for name, wie, neu in faelle:
            with self.subTest(name):
                ziel = neu()
                antwort, abfragen = self.loeschen(lambda: wie(self.verwaltung, ziel))

                self.assertEqual(antwort.status_code, HTTPStatus.FOUND)
                self.assertFalse(Artikel.objects.filter(pk=ziel.pk).exists())
                zuerst_gesperrt(self, abfragen, Artikel, ohne_schluessel=False)

    def test_die_gebinde_im_formular_der_ware(self):
        """Ticked for deletion there, a pack locks the product's articles before the check."""
        antwort, abfragen = self.loeschen(
            lambda: formular_der_ware(self.verwaltung, self.ware, {self.einzeln.pk})
        )

        self.assertEqual(antwort.status_code, HTTPStatus.FOUND)
        self.assertFalse(Gebinde.objects.filter(pk=self.einzeln.pk).exists())
        zuerst_gesperrt(self, abfragen, Artikel, ohne_schluessel=True)

    def test_ohne_haken_sperrt_das_formular_nichts(self):
        antwort, abfragen = self.loeschen(
            lambda: formular_der_ware(self.verwaltung, self.ware, set())
        )

        self.assertEqual(antwort.status_code, HTTPStatus.FOUND)
        self.assertFalse([eintrag for eintrag in abfragen if eintrag[1]])

    def test_was_gezaehlt_wird_bleibt_mit_hinweis(self):
        """The crate counts at the counter: every way says so and deletes nothing."""
        for name, senden in (
            ("Seite", lambda: loeschseite(self.verwaltung, self.kasten)),
            ("Aktion", lambda: alle_loeschen(self.verwaltung, self.kasten)),
            (
                "Formular der Ware",
                lambda: formular_der_ware(self.verwaltung, self.ware, {self.kasten.pk}),
            ),
        ):
            with self.subTest(name):
                self.assertTrue(geschuetzt(senden()))
                self.assertTrue(Gebinde.objects.filter(pk=self.kasten.pk).exists())

    def test_die_rueckfrage_allein_sperrt_nichts(self):
        """Only sending the confirmation locks: a page left open holds nothing."""
        meta = Gebinde._meta
        antwort, abfragen = self.loeschen(
            lambda: self.verwaltung.get(
                reverse(f"admin:{meta.app_label}_{meta.model_name}_delete", args=[self.einzeln.pk])
            )
        )

        self.assertEqual(antwort.status_code, HTTPStatus.OK)
        self.assertFalse([eintrag for eintrag in abfragen if eintrag[1]])


class Nebeneinander:
    """Two threads: the app's new row and the administration, the first one held at its lock.

    Each side waits until the other one waits for a lock or is done
    ('test_closing_race._bis_jemand_wartet'), as 'api.test_deletion_race' does
    for the website. The new row is held right before it is written
    ('Zaehlposition.save'), the administration where 'halt' names.
    """

    konto: CustomUser
    chef: CustomUser
    inventur: Inventur

    def neue_zeile(self, ort: Ort, artikel: Hausartikel, gebinde: Gebinde):
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

    def in_der_verwaltung(self, senden):
        def schicken():
            client = Client(raise_request_exception=False)
            client.force_login(self.chef)
            return senden(client)

        return schicken

    def gleichzeitig(self, app, verwaltung, halt, *, app_zuerst: bool) -> dict:
        ziel, name = (Zaehlposition, "save") if app_zuerst else halt
        gehalten = threading.Event()
        fertig = {"app": threading.Event(), "verwaltung": threading.Event()}
        ergebnis: dict = {}
        echt = getattr(ziel, name)
        warten_auf = fertig["verwaltung"] if app_zuerst else fertig["app"]

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
                threading.Thread(target=laufen, args=("verwaltung", verwaltung, not app_zuerst)),
            ]
            for faden in faeden:
                faden.start()
            for faden in faeden:
                faden.join()
        return ergebnis


@skipUnless(connection.vendor == "postgresql", "SQLite kennt keine Sperre einzelner Zeilen")
class Gleichzeitig(Nebeneinander, APITransactionTestCase):
    """A new row of the app and the administration deleting its pack or article, both orders.

    Runs in the CI, which tests against PostgreSQL.
    """

    serialized_rollback = True

    def setUp(self):
        self.haus = Haus.objects.create(name="Kino Ost")
        bereich = Bereich.objects.create(name="Theke", haus=self.haus)
        theke = Ort.objects.create(name="Thekenfront", bereich=bereich)
        self.lager = Ort.objects.create(name="Trockenlager", bereich=bereich)
        stueck = Basiseinheit.objects.get(name="Stück(e)")
        gruppe = Warengruppe.objects.create(name="Warengruppe G")
        self.ware = Ware.objects.create(name="Artikel C", basiseinheit=stueck)
        kasten = Gebinde.objects.create(
            ware=self.ware, name="Kasten", faktor_in_basiseinheit=Decimal("20")
        )
        # Nobody counted on the pallet yet: the administration may delete it.
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
        # Expected nowhere: the administration may delete it.
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

    def pruefe_abgelehnt(self, antwort, feld: str, meldung):
        self.assertEqual(antwort.status_code, HTTPStatus.BAD_REQUEST)
        self.assertEqual([str(text) for text in antwort.data[feld]], [str(meldung)])

    def pruefe_angelegt(self, antwort, **wo):
        self.assertEqual(antwort.status_code, HTTPStatus.CREATED, getattr(antwort, "data", ""))
        self.assertEqual(Zaehlposition.objects.get(pk=antwort.data["id"], **wo).eingabemenge, 2)

    # -- A pack on its delete page ---------------------------------------------

    def test_die_neue_zeile_wartet_aufs_loeschen_des_gebindes(self):
        """Until 10/2026 the delete ended in 'ProtectedError' after the app's row."""
        ergebnis = self.gleichzeitig(
            self.neue_zeile(self.lager, self.artikel, self.palette),
            self.in_der_verwaltung(lambda client: loeschseite(client, self.palette)),
            (Gebinde, "delete"),
            app_zuerst=False,
        )

        self.assertEqual(ergebnis["verwaltung"].status_code, HTTPStatus.FOUND)
        self.pruefe_abgelehnt(ergebnis["app"], "gebinde", GEBINDE_WEG)
        self.assertFalse(Gebinde.objects.filter(pk=self.palette.pk).exists())

    def test_das_loeschen_des_gebindes_wartet_auf_die_neue_zeile(self):
        """Until 10/2026 the pack went first, and the app's commit failed on the foreign key."""
        ergebnis = self.gleichzeitig(
            self.neue_zeile(self.lager, self.artikel, self.palette),
            self.in_der_verwaltung(lambda client: loeschseite(client, self.palette)),
            (Gebinde, "delete"),
            app_zuerst=True,
        )

        self.pruefe_angelegt(ergebnis["app"], gebinde=self.palette)
        self.assertTrue(geschuetzt(ergebnis["verwaltung"]))
        self.assertTrue(Gebinde.objects.filter(pk=self.palette.pk).exists())

    # -- An article through "Delete selected" ------------------------------------

    def test_die_neue_zeile_wartet_aufs_loeschen_des_artikels(self):
        ergebnis = self.gleichzeitig(
            self.neue_zeile(self.lager, self.ware_n, self.karton),
            self.in_der_verwaltung(lambda client: alle_loeschen(client, self.ware_n)),
            (HausartikelAdmin, "delete_queryset"),
            app_zuerst=False,
        )

        self.assertEqual(ergebnis["verwaltung"].status_code, HTTPStatus.FOUND)
        self.pruefe_abgelehnt(ergebnis["app"], "artikel", ARTIKEL_WEG)
        self.assertFalse(Hausartikel.objects.filter(pk=self.ware_n.pk).exists())

    def test_das_loeschen_des_artikels_wartet_auf_die_neue_zeile(self):
        """Until 10/2026 the delete's commit failed on the foreign key of the app's row."""
        ergebnis = self.gleichzeitig(
            self.neue_zeile(self.lager, self.ware_n, self.karton),
            self.in_der_verwaltung(lambda client: alle_loeschen(client, self.ware_n)),
            (HausartikelAdmin, "delete_queryset"),
            app_zuerst=True,
        )

        self.pruefe_angelegt(ergebnis["app"], artikel=self.ware_n)
        self.assertTrue(geschuetzt(ergebnis["verwaltung"]))
        self.assertTrue(Hausartikel.objects.filter(pk=self.ware_n.pk).exists())
