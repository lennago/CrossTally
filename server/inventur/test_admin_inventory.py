"""The inventory in the administration: what it may still change there, and how it writes.

An inventory arises on the website with its rows, types and counters
('inventuren_anlegen'), it moves through its states by its own steps, and
its target stock and remarks have pages with their rules. Until 10/2026 the
administration offered all of it past them: a new inventory without rows,
the status set to "closed" without a closing, the house switched under its
rows, types without rows, target stock in the middle of a count, and
remarks for any article. And it saved the inventory whole, as its page had
shown it: counters assigned on the website meanwhile went back.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import CustomUser
from api.test_merge_split_race import abfragen_waehrend
from django_project.form_fields import wahlmenge
from django_project.testing import inventur_anlegen, jahresinventur
from inventur.test_admin import formular
from inventur.test_merge_race import erste_abfrage
from katalog.models import Basiseinheit, Hausartikel, Ware, Warengruppe
from organisation.models import Bereich, Haus, Ort

from .models import (
    Inventur,
    Inventurnotiz,
    Inventurstatus,
    Sollbestand,
    Sollzeile,
    Zaehlposition,
)

STICHTAG = date(2026, 1, 31)

ABGESCHLOSSEN = "Die Inventur ist abgeschlossen und steht nur noch zur Ansicht."


class Grundlage(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = CustomUser.objects.create_superuser(
            username="ada", password="geheim", email="ada@example.org"
        )
        cls.haus = Haus.objects.create(name="Kino Ost")
        cls.anderes_haus = Haus.objects.create(name="Kino Süd")
        cls.ort = Ort.objects.create(
            name="Thekenfront", bereich=Bereich.objects.create(name="Theke", haus=cls.haus)
        )
        ware = Ware.objects.create(
            name="Artikel C 0,33", basiseinheit=Basiseinheit.objects.get(name="Stück(e)")
        )
        cls.artikel = Hausartikel.objects.create(
            haus=cls.haus,
            ware=ware,
            warengruppe=Warengruppe.objects.create(name="Warengruppe G"),
            artikelnummer="101001",
            name="Artikel C 0,33 Flasche",
        )
        cls.zoe, cls.nele, cls.kim = (
            CustomUser.objects.create_user(username=name, password="x", email=f"{name}@example.org")
            for name in ("zoe", "nele", "kim")
        )
        cls.ina = CustomUser.objects.create_user(
            username="ina", password="x", email="ina@example.org", is_active=False
        )
        cls.inventur = inventur_anlegen(cls.haus, stichtag=STICHTAG)
        cls.inventur.zaehlende.add(cls.zoe)
        cls.inventur.zaehlpositionen.create(
            ort=cls.ort, artikel=cls.artikel, eingabemenge=Decimal("2")
        )
        cls.soll = Sollbestand.objects.create(
            inventur=cls.inventur, hausartikel=cls.artikel, menge_basiseinheit=Decimal("30")
        )

    def setUp(self):
        self.client.force_login(self.admin)

    def adresse(self, name: str, *args) -> str:
        return reverse(f"admin:inventur_{name}", args=args)

    def seite(self) -> dict[str, list[str]]:
        antwort = self.client.get(self.adresse("inventur_change", self.inventur.pk))
        return formular(antwort, "inventur_form")

    def senden(self, daten: dict[str, list[str]], **weiter):
        return self.client.post(
            self.adresse("inventur_change", self.inventur.pk), {**daten, "_save": ["1"]}, **weiter
        )

    def frisch(self) -> Inventur:
        return Inventur.objects.get(pk=self.inventur.pk)

    def schliessen(self) -> None:
        inventur = self.frisch()
        inventur.zaehlung_starten()
        inventur.abschliessen()


class WasDieVerwaltungAnDerInventurAendert(Grundlage):
    def test_anlegen_gibt_es_hier_nicht(self):
        """Without rows, types and counters, as 'inventuren_anlegen' gives them."""
        self.assertEqual(self.client.get(self.adresse("inventur_add")).status_code, 403)

    def test_status_und_haus_stehen_nur_zur_ansicht(self):
        """Closed without a closing, or moved under its rows into another house."""
        daten = self.seite()
        daten["status"] = [Inventurstatus.ABGESCHLOSSEN]
        daten["haus"] = [str(self.anderes_haus.pk)]

        self.assertEqual(self.senden(daten).status_code, 302)

        frisch = self.frisch()
        self.assertEqual(frisch.status, Inventurstatus.VORBEREITUNG)
        self.assertEqual(frisch.haus_id, self.haus.pk)

    def test_der_stichtag_laesst_sich_berichtigen(self):
        daten = self.seite()
        daten["stichtag"] = [(STICHTAG + timedelta(days=1)).isoformat()]

        self.assertEqual(self.senden(daten).status_code, 302)

        self.assertEqual(self.frisch().stichtag, STICHTAG + timedelta(days=1))

    def test_aber_nicht_auf_einen_tag_mit_einer_anderen_inventur_des_hauses(self):
        """The house is only shown, and Django left it out of the check: a server error."""
        inventur_anlegen(self.haus, stichtag=STICHTAG + timedelta(days=1))
        daten = self.seite()
        daten["stichtag"] = [(STICHTAG + timedelta(days=1)).isoformat()]

        antwort = self.senden(daten)

        self.assertContains(antwort, "Für dieses Haus gibt es an diesem Tag bereits eine Inventur.")
        self.assertEqual(self.frisch().stichtag, STICHTAG)

    def test_die_arten_stehen_nur_zur_ansicht(self):
        """Changed here, they got no rows ('Inventur.arten_aendern' creates them)."""
        daten = self.seite()
        vorne = next(n for n in daten if n.endswith("-TOTAL_FORMS") and "inventurart" in n)
        vorne = vorne.removesuffix("-TOTAL_FORMS")
        daten[f"{vorne}-TOTAL_FORMS"] = [str(int(daten[f"{vorne}-TOTAL_FORMS"][0]) + 1)]
        neu = int(daten[f"{vorne}-TOTAL_FORMS"][0]) - 1
        daten[f"{vorne}-{neu}-inventurart"] = [str(jahresinventur().pk)]
        daten[f"{vorne}-{neu}-inventur"] = [str(self.inventur.pk)]

        self.senden(daten)

        self.assertEqual(self.frisch().inventurarten.count(), 1)

    def test_der_sollbestand_steht_im_formular_nur_zur_ansicht(self):
        """The page of the target stock and the upload carry its rules ('soll_darf_herein')."""
        daten = self.seite()
        self.assertIn("sollbestaende-0-id", daten)
        self.assertNotIn("sollbestaende-0-menge_basiseinheit", daten)
        daten["sollbestaende-0-menge_basiseinheit"] = ["99"]

        self.assertEqual(self.senden(daten).status_code, 302)

        self.assertEqual(Sollbestand.objects.get(pk=self.soll.pk).menge_basiseinheit, 30)

    def test_eine_abgeschlossene_steht_nur_zur_ansicht(self):
        """Its date and its people are part of the record its results belong to."""
        self.schliessen()
        seite = self.client.get(self.adresse("inventur_change", self.inventur.pk))
        self.assertEqual(seite.status_code, 200)

        antwort = self.client.post(
            self.adresse("inventur_change", self.inventur.pk),
            {"stichtag": "2026-02-01", "_save": "1"},
        )

        self.assertRedirects(antwort, self.adresse("inventur_change", self.inventur.pk))
        self.assertEqual(self.frisch().stichtag, STICHTAG)

    def test_inzwischen_abgeschlossen_sagt_es_die_seite(self):
        """Closed while the page was open. Until 10/2026 a bare 403 without a word."""
        daten = self.seite()
        self.schliessen()
        daten["stichtag"] = [(STICHTAG + timedelta(days=1)).isoformat()]

        antwort = self.senden(daten, follow=True)

        self.assertEqual(
            antwort.redirect_chain,
            [(self.adresse("inventur_change", self.inventur.pk), 302)],
        )
        self.assertContains(antwort, ABGESCHLOSSEN)
        self.assertEqual(self.frisch().stichtag, STICHTAG)


class SoGeschrieben(Grundlage):
    def test_zaehlende_von_danach_bleiben(self):
        """The page was open while the website assigned Nele. Saving the date took her out."""
        daten = self.seite()
        self.inventur.zaehlende.add(self.nele)
        daten["stichtag"] = [(STICHTAG + timedelta(days=1)).isoformat()]

        self.assertEqual(self.senden(daten).status_code, 302)

        frisch = self.frisch()
        self.assertEqual(frisch.stichtag, STICHTAG + timedelta(days=1))
        self.assertEqual(set(frisch.zaehlende.all()), {self.zoe, self.nele})

    def test_auch_wenn_die_seite_die_zaehlenden_aendert(self):
        """Zoe out, Kim in, Nele assigned on the website meanwhile. Until 10/2026 she went too."""
        daten = self.seite()
        self.inventur.zaehlende.add(self.nele)
        daten["zaehlende"] = [str(self.kim.pk)]

        self.assertEqual(self.senden(daten).status_code, 302)

        self.assertEqual(set(self.frisch().zaehlende.all()), {self.nele, self.kim})

    def test_zur_wahl_steht_wer_auch_auf_der_website_zur_wahl_steht(self):
        """Administrators count without an entry, and deactivated accounts not at all."""
        seite = self.client.get(self.adresse("inventur_change", self.inventur.pk))
        angeboten = wahlmenge(seite.context["adminform"].form, "zaehlende")
        self.assertEqual(set(angeboten), {self.zoe, self.nele, self.kim})
        for konto in (self.admin, self.ina):
            with self.subTest(konto=konto.username):
                daten = formular(seite, "inventur_form")
                daten["zaehlende"] = [str(self.zoe.pk), str(konto.pk)]

                antwort = self.senden(daten)

                self.assertEqual(antwort.status_code, 200)
                self.assertTrue(antwort.context["adminform"].form.has_error("zaehlende"))
                self.assertEqual(set(self.frisch().zaehlende.all()), {self.zoe})

    def test_wen_die_seite_nicht_zeigt_nimmt_sie_nicht_heraus(self):
        """Ina was entered and deactivated later. The page does not show her, so she stays."""
        self.inventur.zaehlende.add(self.ina)
        daten = self.seite()
        self.assertEqual(daten["zaehlende"], [str(self.zoe.pk)])
        daten["zaehlende"] = [str(self.zoe.pk), str(self.kim.pk)]

        self.assertEqual(self.senden(daten).status_code, 302)

        self.assertEqual(set(self.frisch().zaehlende.all()), {self.zoe, self.ina, self.kim})

    def test_nur_das_geaenderte_wird_geschrieben(self):
        daten = self.seite()
        daten["stichtag"] = [(STICHTAG + timedelta(days=1)).isoformat()]

        abfragen = abfragen_waehrend(lambda: self.senden(daten))

        (geschrieben,) = [
            sql for art, *_rest, sql in abfragen if art is Inventur and sql.startswith("UPDATE")
        ]
        self.assertIn('"stichtag"', geschrieben)
        for spalte in ('"status"', '"gesperrt"', '"differenz_von_hand"', '"haus_id"'):
            self.assertNotIn(spalte, geschrieben)

    def test_die_inventur_ist_gesperrt_bevor_sie_gelesen_wird(self):
        """Without the key, as the closing and the app lock it."""
        daten = self.seite()

        abfragen = abfragen_waehrend(lambda: self.senden(daten))

        sperre = erste_abfrage(abfragen, Inventur, gesperrt=True)
        _art, _sperre, ohne_schluessel, reihenfolge, sql = abfragen[sperre]
        self.assertTrue(ohne_schluessel, sql)
        self.assertEqual(reihenfolge, ("pk",), sql)
        gelesen = next(
            stelle
            for stelle, (art, _s, _o, _r, sql) in enumerate(abfragen)
            if art is Inventur and sql.startswith("SELECT") and '"stichtag"' in sql
        )
        self.assertLess(sperre, gelesen)

    def test_das_loeschen_sperrt_inventur_und_zeilen_bevor_django_nachsieht(self):
        """The inventory without the key, as the website's delete page, then every row.

        'FOR UPDATE' on the inventory ran into a deadlock with a merge. A weighing
        of the app locks only its row: one written between Django's look and its
        delete ended the delete at the weighing's foreign key.
        """
        adresse = self.adresse("inventur_delete", self.inventur.pk)

        abfragen = abfragen_waehrend(lambda: self.client.post(adresse, {"post": "yes"}))

        sperre = erste_abfrage(abfragen, Inventur, gesperrt=True)
        self.assertTrue(abfragen[sperre][2], abfragen[sperre][4])
        zeilen = erste_abfrage(abfragen, Zaehlposition, gesperrt=True)
        _art, _sperre, ohne_schluessel, reihenfolge, sql = abfragen[zeilen]
        self.assertFalse(ohne_schluessel, sql)
        self.assertEqual(reihenfolge, ("pk",), sql)
        self.assertNotIn("JOIN", sql.upper(), sql)
        nachgesehen = next(
            stelle
            for stelle, (art, gesperrt, *_rest) in enumerate(abfragen)
            if art is Zaehlposition and not gesperrt
        )
        self.assertLess(sperre, zeilen)
        self.assertLess(zeilen, nachgesehen)
        self.assertFalse(Inventur.objects.filter(pk=self.inventur.pk).exists())


class ArbeitsdatenNurZurAnsicht(Grundlage):
    """Target stock, open lines of its file and remarks: shown here, written on their pages."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.notiz = Inventurnotiz.objects.create(
            inventur=cls.inventur,
            hausartikel=cls.artikel,
            text="hinten im Lager",
            differenz=Decimal("2.500"),
        )
        cls.offen = Sollzeile.objects.create(
            inventur=cls.inventur,
            artikelnummer="101099",
            name="Ware N",
            menge_basiseinheit=Decimal("4"),
        )

    def test_nichts_davon_entsteht_hier(self):
        for name in ("sollbestand_add", "inventurnotiz_add", "sollzeile_add"):
            with self.subTest(name=name):
                self.assertEqual(self.client.get(self.adresse(name)).status_code, 403)

    def test_nichts_davon_wird_hier_geaendert(self):
        for name, satz, feld, wert in (
            ("sollbestand_change", self.soll, "menge_basiseinheit", "99"),
            ("inventurnotiz_change", self.notiz, "text", "anders"),
            ("sollzeile_change", self.offen, "name", "Ware M"),
        ):
            with self.subTest(name=name):
                adresse = self.adresse(name, satz.pk)
                self.assertEqual(self.client.get(adresse).status_code, 200)
                antwort = self.client.post(adresse, {feld: wert, "_save": "1"})
                self.assertEqual(antwort.status_code, 403)
        self.assertEqual(Sollbestand.objects.get(pk=self.soll.pk).menge_basiseinheit, 30)
        self.assertEqual(Inventurnotiz.objects.get(pk=self.notiz.pk).text, "hinten im Lager")
        self.assertEqual(Sollzeile.objects.get(pk=self.offen.pk).name, "Ware N")

    def test_und_nicht_einzeln_geloescht(self):
        for name, satz in (
            ("sollbestand_delete", self.soll),
            ("inventurnotiz_delete", self.notiz),
            ("sollzeile_delete", self.offen),
        ):
            with self.subTest(name=name):
                antwort = self.client.post(self.adresse(name, satz.pk), {"post": "yes"})
                self.assertEqual(antwort.status_code, 403)
        self.assertTrue(Sollbestand.objects.filter(pk=self.soll.pk).exists())
        self.assertTrue(Inventurnotiz.objects.filter(pk=self.notiz.pk).exists())
        self.assertTrue(Sollzeile.objects.filter(pk=self.offen.pk).exists())

    def test_mit_der_inventur_gehen_sie_aber(self):
        """The inventory's delete page asks for the right to delete everything it takes along."""
        adresse = self.adresse("inventur_delete", self.inventur.pk)
        self.assertEqual(self.client.get(adresse).status_code, 200)

        self.client.post(adresse, {"post": "yes"})

        self.assertFalse(Inventur.objects.filter(pk=self.inventur.pk).exists())
        self.assertFalse(Sollbestand.objects.exists())
        self.assertFalse(Inventurnotiz.objects.exists())
        self.assertFalse(Sollzeile.objects.exists())

    def test_mengen_ohne_die_nullen_der_spalte(self):
        """Read-only, Django showed every place the column stores: "30,000", read as thousands."""
        for name, args, menge in (
            ("inventur_change", [self.inventur.pk], "30"),
            ("sollbestand_change", [self.soll.pk], "30"),
            ("sollbestand_changelist", [], "30"),
            ("sollzeile_change", [self.offen.pk], "4"),
            ("sollzeile_changelist", [], "4"),
            ("inventurnotiz_change", [self.notiz.pk], "+2,5"),
            ("inventurnotiz_changelist", [], "+2,5"),
        ):
            with self.subTest(name=name):
                antwort = self.client.get(self.adresse(name, *args))

                self.assertContains(antwort, menge)
                self.assertNotContains(antwort, f"{menge},000")
                self.assertNotContains(antwort, "2,500")
