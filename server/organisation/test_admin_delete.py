"""Deleting a location or an area in the administration while the app creates a row there.

A new row of the app locks its location ('api.serializers.counting.ort_sperren'). The
administration locks the location, or the locations of an area, before
PROTECT looks ('django_project.deletion.VerwaltungSperrtZuerst'), as the
website's delete pages do: the delete page, the action "Delete selected" and
the areas and locations on the form of their house or area. Until 10/2026 it
deleted without the lock, and a row the app created in the same moment ended
one of the two commits in a server error. 'ErstGesperrt' reads the lock off
the queries on SQLite, 'Gleichzeitig' forces the overlap on PostgreSQL.
"""

from http import HTTPStatus
from unittest import skipUnless

from django.db import connection
from django.test import Client, TestCase
from django.urls import reverse
from rest_framework.test import APITransactionTestCase

from accounts.models import CustomUser
from api.serializers import ORT_WEG
from api.test_merge_split_race import abfragen_waehrend
from api.tests import STICHTAG
from django_project.testing import inventur_anlegen
from katalog.test_admin_delete import (
    Nebeneinander,
    alle_loeschen,
    geschuetzt,
    loeschseite,
    zuerst_gesperrt,
)
from organisation.models import Bereich, Haus, Ort

from .test_delete_race import MitLager


def formular(client, eltern: Haus | Bereich, kinder, loeschen: set[int]):
    """The form of a house or an area in the administration, the rows in 'loeschen' ticked."""
    prefix = "bereiche" if isinstance(eltern, Haus) else "orte"
    saetze = list(kinder)
    daten = {
        "name": eltern.name,
        f"{prefix}-TOTAL_FORMS": str(len(saetze)),
        f"{prefix}-INITIAL_FORMS": str(len(saetze)),
        f"{prefix}-MIN_NUM_FORMS": "0",
        f"{prefix}-MAX_NUM_FORMS": "1000",
    }
    if isinstance(eltern, Bereich):
        daten["haus"] = str(eltern.haus_id)
    for stelle, satz in enumerate(saetze):
        daten |= {
            f"{prefix}-{stelle}-id": str(satz.pk),
            f"{prefix}-{stelle}-name": satz.name,
        }
        if satz.pk in loeschen:
            daten[f"{prefix}-{stelle}-DELETE"] = "on"
    meta = type(eltern)._meta
    return client.post(
        reverse(f"admin:{meta.app_label}_{meta.model_name}_change", args=[eltern.pk]), daten
    )


def formular_des_hauses(client, haus: Haus, loeschen: set[int]):
    return formular(client, haus, haus.bereiche.order_by("pk"), loeschen)


def formular_des_bereichs(client, bereich: Bereich, loeschen: set[int]):
    return formular(client, bereich, bereich.orte.order_by("pk"), loeschen)


class ErstGesperrt(MitLager, TestCase):
    def setUp(self):
        self.lager_anlegen()
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

    def test_ein_ort_sperrt_sich(self):
        """'FOR UPDATE': the location is deleted."""
        for name, wie in (("Seite", loeschseite), ("Aktion", alle_loeschen)):
            with self.subTest(name):
                vitrine = Ort.objects.create(name=f"Vitrine {name}", bereich=self.lager)
                antwort, abfragen = self.loeschen(lambda: wie(self.verwaltung, vitrine))

                self.assertEqual(antwort.status_code, HTTPStatus.FOUND)
                self.assertFalse(Ort.objects.filter(pk=vitrine.pk).exists())
                zuerst_gesperrt(self, abfragen, Ort, ohne_schluessel=False)

    def test_ein_bereich_sperrt_seine_orte(self):
        """With the area its locations go, and the app locks a location, not the area."""
        for name, wie in (("Seite", loeschseite), ("Aktion", alle_loeschen)):
            with self.subTest(name):
                keller = Bereich.objects.create(name=f"Keller {name}", haus=self.haus)
                Ort.objects.create(name="Regal", bereich=keller)
                antwort, abfragen = self.loeschen(lambda: wie(self.verwaltung, keller))

                self.assertEqual(antwort.status_code, HTTPStatus.FOUND)
                self.assertFalse(Bereich.objects.filter(pk=keller.pk).exists())
                zuerst_gesperrt(self, abfragen, Ort, ohne_schluessel=False)

    def test_die_bereiche_im_formular_des_hauses(self):
        """Ticked for deletion there, an area locks its locations before the check."""
        antwort, abfragen = self.loeschen(
            lambda: formular_des_hauses(self.verwaltung, self.haus, {self.lager.pk})
        )

        self.assertEqual(antwort.status_code, HTTPStatus.FOUND)
        self.assertFalse(Bereich.objects.filter(pk=self.lager.pk).exists())
        zuerst_gesperrt(self, abfragen, Ort, ohne_schluessel=False)

    def test_die_orte_im_formular_des_bereichs(self):
        antwort, abfragen = self.loeschen(
            lambda: formular_des_bereichs(self.verwaltung, self.lager, {self.kuehlraum.pk})
        )

        self.assertEqual(antwort.status_code, HTTPStatus.FOUND)
        self.assertFalse(Ort.objects.filter(pk=self.kuehlraum.pk).exists())
        zuerst_gesperrt(self, abfragen, Ort, ohne_schluessel=False)

    def test_ohne_haken_sperren_die_formulare_nichts(self):
        for name, senden in (
            ("Haus", lambda: formular_des_hauses(self.verwaltung, self.haus, set())),
            ("Bereich", lambda: formular_des_bereichs(self.verwaltung, self.lager, set())),
        ):
            with self.subTest(name):
                antwort, abfragen = self.loeschen(senden)

                self.assertEqual(antwort.status_code, HTTPStatus.FOUND)
                self.assertFalse([eintrag for eintrag in abfragen if eintrag[1]])

    def test_was_gezaehlt_wird_bleibt_mit_hinweis(self):
        """A row in the cold room holds it, its area and the area's place on the house's form."""
        inventur = inventur_anlegen(self.haus, stichtag=STICHTAG)
        inventur.zaehlpositionen_anlegen()
        inventur.zaehlpositionen.create(
            ort=self.kuehlraum, artikel=self.artikel, gebinde=self.kasten
        )
        for name, senden in (
            ("Ort, Seite", lambda: loeschseite(self.verwaltung, self.kuehlraum)),
            ("Ort, Aktion", lambda: alle_loeschen(self.verwaltung, self.kuehlraum)),
            ("Bereich, Seite", lambda: loeschseite(self.verwaltung, self.lager)),
            ("Bereich, Aktion", lambda: alle_loeschen(self.verwaltung, self.lager)),
            (
                "Formular des Bereichs",
                lambda: formular_des_bereichs(self.verwaltung, self.lager, {self.kuehlraum.pk}),
            ),
            (
                "Formular des Hauses",
                lambda: formular_des_hauses(self.verwaltung, self.haus, {self.lager.pk}),
            ),
        ):
            with self.subTest(name):
                self.assertTrue(geschuetzt(senden()))
                self.assertTrue(Ort.objects.filter(pk=self.kuehlraum.pk).exists())


@skipUnless(connection.vendor == "postgresql", "SQLite kennt keine Sperre einzelner Zeilen")
class Gleichzeitig(MitLager, Nebeneinander, APITransactionTestCase):
    """A new row in the cold room and the administration deleting it or its area, both orders.

    Runs in the CI, which tests against PostgreSQL.
    """

    serialized_rollback = True

    def setUp(self):
        self.lager_anlegen()
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

    def app(self):
        return self.neue_zeile(self.kuehlraum, self.artikel, self.kasten)

    def test_die_neue_zeile_wartet_aufs_loeschen_des_orts(self):
        """Until 10/2026 the delete ended in 'ProtectedError' after the app's row."""
        ergebnis = self.gleichzeitig(
            self.app(),
            self.in_der_verwaltung(lambda client: loeschseite(client, self.kuehlraum)),
            (Ort, "delete"),
            app_zuerst=False,
        )

        self.assertEqual(ergebnis["verwaltung"].status_code, HTTPStatus.FOUND)
        self.assertEqual(ergebnis["app"].status_code, HTTPStatus.BAD_REQUEST)
        self.assertEqual([str(text) for text in ergebnis["app"].data["ort"]], [str(ORT_WEG)])
        self.assertFalse(Ort.objects.filter(pk=self.kuehlraum.pk).exists())

    def test_das_loeschen_des_bereichs_im_formular_des_hauses_wartet(self):
        """Until 10/2026 the commit of the house's form failed on the foreign key of the row."""
        ergebnis = self.gleichzeitig(
            self.app(),
            self.in_der_verwaltung(
                lambda client: formular_des_hauses(client, self.haus, {self.lager.pk})
            ),
            (Bereich, "delete"),
            app_zuerst=True,
        )

        self.assertEqual(
            ergebnis["app"].status_code, HTTPStatus.CREATED, getattr(ergebnis["app"], "data", "")
        )
        self.assertTrue(geschuetzt(ergebnis["verwaltung"]))
        self.assertTrue(Bereich.objects.filter(pk=self.lager.pk).exists())
