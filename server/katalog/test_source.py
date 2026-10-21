"""The source of a house article: which system carries it, and whether it needs a number.

A house without an inventory management system carries only own articles.
Those have no number, and a house may share the empty number any number of
times. Everything else that hangs on the number (target stock, free slot,
import) has to cope with that.
"""

import pandas as pd
from django.core.exceptions import ValidationError
from django.db import models
from django.test import TestCase
from django.urls import reverse

from accounts.models import CustomUser
from api.master_data import _mit_freiem_platz
from django_project.testing import AngemeldeteSeiten, inventur_anlegen, monatsinventur
from inventur.services import sollbestand_einlesen, sollpflege
from inventur.test_views import STICHTAG, datei
from organisation.models import Haus

from .formats import STANDARD
from .forms import HausartikelForm
from .models import (
    QUELLE_EIGEN,
    Artikelstatus,
    Basiseinheit,
    Hausartikel,
    Ware,
    Warengruppe,
    quellenwahl,
)
from .services import FEHLT, als_daten, ausfuehren, planen
from .test_services import SPALTEN, zeile


class Grundlage(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.haus = Haus.objects.create(name="Restaurant Süd")
        cls.kino = Haus.objects.create(name="Kino Nord")
        cls.stueck = Basiseinheit.objects.get(name="Stück(e)")
        cls.gruppe = Warengruppe.objects.create(name="Warengruppe G")
        cls.sahne = Ware.objects.create(name="Ware S", basiseinheit=cls.stueck)
        cls.zucker = Ware.objects.create(name="Ware Z", basiseinheit=cls.stueck)

    def artikel(self, ware, quelle=QUELLE_EIGEN, nummer="", haus=None, **abweichend):
        satz = Hausartikel(
            haus=haus or self.haus,
            ware=ware,
            warengruppe=self.gruppe,
            artikelnummer=nummer,
            quelle=quelle,
            name=abweichend.pop("name", ware.name),
            **abweichend,
        )
        satz.full_clean()
        satz.save()
        return satz


class Modell(Grundlage):
    def test_die_wahl_kennt_jedes_format_und_eigen(self):
        kennungen = [kennung for kennung, _name in quellenwahl()]
        self.assertEqual(kennungen, ["winticket", "eigen"])

    def test_eigen_ohne_nummer(self):
        satz = self.artikel(self.sahne)
        self.assertEqual((satz.quelle, satz.artikelnummer), ("eigen", ""))
        self.assertEqual(str(satz), "Restaurant Süd: Ware S")

    def test_eigen_mit_nummer_wird_abgewiesen(self):
        with self.assertRaises(ValidationError) as fang:
            self.artikel(self.sahne, nummer="101001")
        self.assertIn("artikelnummer", fang.exception.message_dict)

    def test_winticket_ohne_nummer_wird_abgewiesen(self):
        with self.assertRaises(ValidationError) as fang:
            self.artikel(self.sahne, quelle="winticket")
        self.assertIn("beginnt mit einer Ziffer", str(fang.exception.message_dict["artikelnummer"]))

    def test_eine_unbekannte_quelle_wird_abgewiesen(self):
        with self.assertRaises(ValidationError) as fang:
            self.artikel(self.sahne, quelle="xyz", nummer="101001")
        self.assertIn("quelle", fang.exception.message_dict)

    def test_die_leere_nummer_teilen_sich_beliebig_viele(self):
        self.artikel(self.sahne)
        self.artikel(self.zucker)
        self.assertEqual(Hausartikel.objects.filter(haus=self.haus).count(), 2)

    def test_dieselbe_ware_fuehrt_das_haus_trotzdem_nur_einmal(self):
        self.artikel(self.sahne)
        with self.assertRaises(ValidationError):
            self.artikel(self.sahne, name="Ware S anders")

    def test_blockiert_wird_nur_ueber_die_ware(self):
        erster = self.artikel(self.sahne)
        zweiter = Hausartikel(
            haus=self.haus, ware=self.zucker, warengruppe=self.gruppe, artikelnummer="", name="Z"
        )
        self.assertIsNone(zweiter.blockiert_durch())
        dritter = Hausartikel(
            haus=self.haus, ware=self.sahne, warengruppe=self.gruppe, artikelnummer="", name="S"
        )
        self.assertEqual(dritter.blockiert_durch(), erster)

    def test_eingestellte_eigene_artikel_sind_beide_frei(self):
        """The free slot for the app: the empty number occupies nothing."""
        a = self.artikel(self.sahne, status=Artikelstatus.EINGESTELLT)
        b = self.artikel(self.zucker, status=Artikelstatus.EINGESTELLT)
        self.assertEqual(set(_mit_freiem_platz(self.haus, [a, b])), {a, b})


class Sollbestand(Grundlage):
    def setUp(self):
        self.eigen = self.artikel(self.sahne)
        self.inventur = inventur_anlegen(self.haus, stichtag=STICHTAG)

    def test_die_datei_kennt_eigene_artikel_nicht(self):
        bericht = sollbestand_einlesen(datei("000000;5\n"), self.inventur)
        self.assertEqual((bericht.zugeordnet, bericht.offen), (0, 1))
        self.assertFalse(self.inventur.sollbestaende.exists())

    def test_von_hand_bekommen_sie_ihr_soll(self):
        self.assertEqual([z.hausartikel for z in sollpflege(self.inventur).ohne], [self.eigen])

    def test_ein_haus_ohne_nummern_bekommt_den_hinweis(self):
        bericht = sollbestand_einlesen(datei("000000;5\n"), self.inventur)
        self.assertTrue(bericht.ohne_nummern)
        self.artikel(self.zucker, quelle="winticket", nummer="101002")
        self.assertFalse(sollbestand_einlesen(datei("000000;5\n"), self.inventur).ohne_nummern)


class Listen(Grundlage):
    """Own articles show "–" instead of a number and sort by the product name."""

    def test_ohne_nummer_nach_dem_warennamen_hinter_den_nummerierten(self):
        z = self.artikel(self.zucker)
        s = self.artikel(self.sahne)
        n = self.artikel(self.zucker, quelle="winticket", nummer="101002", haus=self.kino)
        self.assertEqual(
            list(Hausartikel.objects.filter(haus__in=[self.haus, self.kino])), [n, s, z]
        )

    def test_die_liste_zeigt_einen_strich(self):
        self.artikel(self.sahne)
        self.client.force_login(
            CustomUser.objects.create_superuser("probe", "p@example.invalid", "x")
        )
        antwort = self.client.get(reverse("hausartikel_list", args=[self.haus.pk]))
        self.assertContains(antwort, "–")


class Formular(Grundlage):
    def daten(self, **abweichend):
        basis = {
            "quelle": QUELLE_EIGEN,
            "artikelnummer": "",
            "name": "Ware S",
            "ware": self.sahne.pk,
            "neuer_ware": "",
            "basiseinheit": "",
            "inventurart": monatsinventur().pk,
            "warengruppe": self.gruppe.pk,
            "neue_warengruppe": "",
            "ean": "",
        }
        return basis | abweichend

    def test_eigen_ohne_nummer_geht_durch(self):
        form = HausartikelForm(self.daten(), haus=self.haus)
        self.assertTrue(form.is_valid(), form.errors)
        satz = form.save()
        self.assertEqual((satz.quelle, satz.artikelnummer), ("eigen", ""))

    def test_winticket_braucht_die_nummer(self):
        form = HausartikelForm(self.daten(quelle="winticket"), haus=self.haus)
        self.assertFalse(form.is_valid())
        self.assertIn("artikelnummer", form.errors)

    def test_ohne_angabe_gilt_die_uebliche_quelle_des_hauses(self):
        self.artikel(self.zucker)
        form = HausartikelForm(self.daten(quelle=""), haus=self.haus)
        self.assertEqual(form.fields["quelle"].initial, "eigen")
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().quelle, "eigen")

    def test_ein_leeres_haus_bekommt_die_vorgabe(self):
        form = HausartikelForm(haus=self.kino)
        self.assertEqual(form.fields["quelle"].initial, "winticket")


class Import(AngemeldeteSeiten):
    """New rows from a file carry the format they came from."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.haus = Haus.objects.create(name="Kino Nord")
        cls.stueck = Basiseinheit.objects.get(name="Stück(e)")
        cls.sahne = Ware.objects.create(name="Ware S", basiseinheit=cls.stueck)
        cls.zucker = Ware.objects.create(name="Ware Z", basiseinheit=cls.stueck)

    def test_die_vorgabe_des_modells_ist_das_standardformat(self):
        feld = Hausartikel._meta.get_field("quelle")
        assert isinstance(feld, models.CharField)
        self.assertEqual(feld.default, STANDARD)

    def test_fehlt_in_der_datei_gilt_nur_fuer_dieselbe_quelle(self):
        """An own article is in no file. It must not appear there as "missing"."""
        gruppe = Warengruppe.objects.create(name="Warengruppe G")
        Hausartikel.objects.create(
            haus=self.haus,
            ware=self.sahne,
            warengruppe=gruppe,
            artikelnummer="101001",
            name="Artikel C",
        )
        Hausartikel.objects.create(
            haus=self.haus,
            ware=self.zucker,
            warengruppe=gruppe,
            artikelnummer="",
            name="Ware Z",
            quelle=QUELLE_EIGEN,
        )
        tabelle = pd.DataFrame([zeile("101003", "Artikel D", "Kasten", 24.0)], columns=SPALTEN)
        plan, _uebergangen = planen(tabelle, self.haus)
        self.assertEqual([satz.name for satz in plan if satz.art == FEHLT], ["Artikel C"])

    def test_ausfuehren_schreibt_die_quelle(self):
        tabelle = pd.DataFrame([zeile("101001", "Artikel C", "Kasten", 24.0)], columns=SPALTEN)
        plan, _uebergangen = planen(tabelle, self.haus)
        ausfuehren([als_daten(satz) for satz in plan], self.haus, "winticket")
        self.assertEqual(Hausartikel.objects.get().quelle, "winticket")

    def test_der_plan_traegt_das_format_als_verstecktes_feld(self):
        kopf = "artikelnummer;name;warengruppe;gebinde;anzahl;einheit;haus"
        inhalt = "\n".join([kopf, "101001;Artikel C;Warengruppe G;Kasten;24;Stück(e);Kino Nord"])
        antwort = self.client.post(
            reverse("hausartikel_import", args=[self.haus.pk]),
            {"datei": datei(inhalt, name="export.csv"), "format": "winticket"},
        )
        self.assertContains(antwort, 'name="format" value="winticket"')
