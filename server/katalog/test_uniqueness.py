"""House, number and designation together exist only once, in every status.

The partial indexes govern what is **carried** (per house and number only
one row that is not discontinued). This rule keeps apart, next to that,
what gathers under one number over the years. A database rule is not
possible (the designation is in the table of the article core), so all
three doors stand here: the model, the form of the site and the import.
"""

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.urls import reverse

from django_project.testing import AngemeldeteSeiten
from organisation.models import Haus

from .models import Artikelstatus, Basiseinheit, Hausartikel, Ware, Warengruppe


class Eindeutigkeit(AngemeldeteSeiten):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.haus = Haus.objects.create(name="Kino Nord")
        cls.anderes = Haus.objects.create(name="Kino Süd")
        cls.stueck = Basiseinheit.objects.get(name="Stück(e)")
        cls.gruppe = Warengruppe.objects.create(name="Warengruppe G")
        cls.cola = cls.artikel("101001", "Artikel C")

    @classmethod
    def artikel(cls, nummer, name, haus=None, ware=None, **abweichend):
        ware = (
            ware or Ware.objects.get_or_create(name=name, defaults={"basiseinheit": cls.stueck})[0]
        )
        return Hausartikel.objects.create(
            haus=haus or cls.haus,
            ware=ware,
            warengruppe=cls.gruppe,
            artikelnummer=nummer,
            name=name,
            **abweichend,
        )

    def zwilling(self, **abweichend) -> Hausartikel:
        vorgaben = {
            "haus": self.haus,
            "ware": Ware.objects.get_or_create(
                name="Artikel C neu", defaults={"basiseinheit": self.stueck}
            )[0],
            "warengruppe": self.gruppe,
            "artikelnummer": "101001",
            "name": "Artikel C",
        }
        return Hausartikel(**(vorgaben | abweichend))


class RegelAmModell(Eindeutigkeit):
    def test_eingestellter_zwilling_wird_abgewiesen(self):
        """The number is free. The same article a second time is still not."""
        self.cola.einstellen()
        with self.assertRaises(ValidationError) as abgewiesen:
            self.zwilling().full_clean()
        self.assertIn("name", abgewiesen.exception.message_dict)
        self.assertIn("eingestellt", " ".join(abgewiesen.exception.message_dict["name"]))

    def test_andere_bezeichnung_unter_derselben_nummer_geht(self):
        """The ordinary case: the number was reassigned."""
        self.cola.einstellen()
        self.zwilling(name="Artikel F").full_clean()

    def test_andere_nummer_oder_anderes_haus_geht(self):
        self.cola.einstellen()
        self.zwilling(artikelnummer="101002").full_clean()
        self.zwilling(haus=self.anderes).full_clean()

    def test_der_satz_zaehlt_nicht_gegen_sich_selbst(self):
        self.cola.full_clean()

    def test_umbenennen_auf_einen_vorhandenen_namen_wird_abgewiesen(self):
        self.cola.einstellen()
        fassbrause = self.artikel("101001", "Artikel F")
        fassbrause.name = "Artikel C"
        with self.assertRaises(ValidationError):
            fassbrause.full_clean()

    def test_weiterhin_nur_ein_gefuehrter_je_haus_und_nummer(self):
        """The old rule remains next to it, as a partial index in the database."""
        for status in (Artikelstatus.AKTIV, Artikelstatus.INAKTIV):
            with (
                self.subTest(status=status),
                self.assertRaises(IntegrityError),
                transaction.atomic(),
            ):
                self.artikel("101001", "Artikel F", status=status)
        # Discontinued, by contrast, any number, as long as they are called differently.
        self.artikel("101001", "Artikel F", status=Artikelstatus.EINGESTELLT)
        self.artikel("101001", "Artikel S", status=Artikelstatus.EINGESTELLT)


class RegelImFormular(Eindeutigkeit):
    def anlegen(self, **abweichend):
        daten = {
            "artikelnummer": "101001",
            "name": "Artikel C",
            "neuer_ware": "Artikel C neu",
            "basiseinheit": self.stueck.pk,
            "warengruppe": self.gruppe.pk,
            "gebinde-TOTAL_FORMS": "0",
            "gebinde-INITIAL_FORMS": "0",
            "gebinde-MIN_NUM_FORMS": "0",
            "gebinde-MAX_NUM_FORMS": "1000",
            "orte-TOTAL_FORMS": "0",
            "orte-INITIAL_FORMS": "0",
            "orte-MIN_NUM_FORMS": "0",
            "orte-MAX_NUM_FORMS": "1000",
        }
        return self.client.post(reverse("hausartikel_add", args=[self.haus.pk]), daten | abweichend)

    def test_anlegen_neben_eingestelltem_zwilling_wird_abgewiesen(self):
        self.cola.einstellen()
        antwort = self.anlegen()
        self.assertEqual(antwort.status_code, 200)
        self.assertIn("name", antwort.context["form"].errors)
        self.assertEqual(Hausartikel.objects.filter(artikelnummer="101001").count(), 1)

    def test_anlegen_mit_anderer_bezeichnung_geht(self):
        self.cola.einstellen()
        antwort = self.anlegen(name="Artikel C Zero", neuer_ware="Artikel C Zero")
        self.assertEqual(antwort.status_code, 302)
        self.assertEqual(Hausartikel.objects.filter(artikelnummer="101001").count(), 2)


class RegelImImport(Eindeutigkeit):
    def datei(self, *zeilen) -> SimpleUploadedFile:
        kopf = "artikelnummer;name;warengruppe;gebinde;anzahl;einheit;haus"
        inhalt = "\n".join([kopf, *zeilen])
        return SimpleUploadedFile("export.csv", inhalt.encode("utf-8"), "text/csv")

    def plan(self, *zeilen):
        return self.client.post(
            reverse("hausartikel_import", args=[self.haus.pk]),
            {"datei": self.datei(*zeilen), "format": "winticket"},
        )

    def absenden(self, antwort, **abweichend):
        formset = antwort.context["formset"]
        daten = {
            "schritt": "uebernehmen",
            "plan-TOTAL_FORMS": str(len(formset.forms)),
            "plan-INITIAL_FORMS": str(len(formset.forms)),
            "plan-MIN_NUM_FORMS": "0",
            "plan-MAX_NUM_FORMS": "5000",
        }
        for stelle, form in enumerate(formset.forms):
            for feld, wert in form.initial.items():
                daten[f"plan-{stelle}-{feld}"] = "" if wert is None else wert
        return self.client.post(
            reverse("hausartikel_import", args=[self.haus.pk]), daten | abweichend
        )

    def test_eingestellter_steht_zum_zurueckholen_im_plan(self):
        """Number and designation match. Offered is the existing row, no twin."""
        self.cola.einstellen()
        antwort = self.plan("101001;Artikel C;Warengruppe G;Kasten;24;Stück(e);Kino Nord")
        zeile = antwort.context["formset"].forms[0]
        self.assertEqual(zeile.initial["art"], "eingestellt")
        self.assertEqual(zeile.initial["vorhanden_pk"], self.cola.pk)
        self.assertEqual(
            [wert for wert, _ in zeile.fields["vorgehen"].choices], ["uebergehen", "zurueckholen"]
        )

    def test_eingestellten_als_neu_ausgeben_wird_abgewiesen(self):
        """'art' comes from the form. It is measured against the stock, not believed."""
        self.cola.einstellen()
        antwort = self.absenden(
            self.plan("101001;Artikel C;Warengruppe G;Kasten;24;Stück(e);Kino Nord"),
            **{"plan-0-art": "neu", "plan-0-vorhanden_pk": "", "plan-0-vorgehen": "aktiv"},
        )
        self.assertEqual(antwort.status_code, 200)
        self.assertIn("passt nicht mehr zu dem, was Kino Nord", antwort.content.decode())
        self.assertEqual(Hausartikel.objects.filter(artikelnummer="101001").count(), 1)

    def test_zurueckholen_macht_den_eingestellten_wieder_aktiv(self):
        self.cola.einstellen()
        antwort = self.absenden(
            self.plan("101001;Artikel C;Warengruppe G;Kasten;24;Stück(e);Kino Nord"),
            **{"plan-0-vorgehen": "zurueckholen"},
        )
        self.assertEqual(antwort.status_code, 302)
        self.cola.refresh_from_db()
        self.assertEqual(self.cola.status, Artikelstatus.AKTIV)
        self.assertEqual(Hausartikel.objects.filter(artikelnummer="101001").count(), 1)

    def test_zurueckholen_stellt_ein_wer_die_nummer_inzwischen_fuehrt(self):
        """101001 is now called Artikel F. The file brings Artikel C again, that still exists."""
        self.cola.einstellen()
        fassbrause = self.artikel("101001", "Artikel F")
        plan = self.plan("101001;Artikel C;Warengruppe G;Kasten;24;Stück(e);Kino Nord")
        zeile = plan.context["formset"].forms[0]
        self.assertEqual(zeile.initial["art"], "eingestellt")
        self.assertIn("führt zurzeit „Artikel F“", zeile.initial["hinweise"])

        antwort = self.absenden(plan, **{"plan-0-vorgehen": "zurueckholen"})
        self.assertEqual(antwort.status_code, 302)
        self.cola.refresh_from_db()
        fassbrause.refresh_from_db()
        self.assertEqual(self.cola.status, Artikelstatus.AKTIV)
        self.assertEqual(fassbrause.status, Artikelstatus.EINGESTELLT)

    def test_zurueckholen_geht_nicht_wenn_die_ware_vergeben_ist(self):
        """The partial index on the product, otherwise it would end when writing."""
        self.cola.einstellen()
        self.artikel("101002", "Artikel C 0,33", ware=self.cola.ware)
        antwort = self.absenden(
            self.plan("101001;Artikel C;Warengruppe G;Kasten;24;Stück(e);Kino Nord"),
            **{"plan-0-vorgehen": "zurueckholen"},
        )
        self.assertEqual(antwort.status_code, 200)
        self.assertIn("führt diese Ware schon unter 101002", antwort.content.decode())
        self.cola.refresh_from_db()
        self.assertEqual(self.cola.status, Artikelstatus.EINGESTELLT)

    def test_aendern_ohne_neue_bezeichnung_bleibt_moeglich(self):
        """The button "Change" hits the existing row. It is not in its own way."""
        antwort = self.absenden(
            self.plan("101001;Artikel C;Warengruppe G;Kasten;24;Stück(e);Kino Nord"),
            **{"plan-0-vorgehen": "aktualisieren"},
        )
        self.assertEqual(antwort.status_code, 302)

    def test_am_formular_vorbei_meldet_der_bericht(self):
        from .services import AKTIV, ausfuehren  # noqa: PLC0415

        self.cola.einstellen()
        bericht = ausfuehren(
            [
                {
                    "vorgehen": AKTIV,
                    "artikelnummer": "101001",
                    "name": "Artikel C",
                    "warengruppe": "Warengruppe G",
                    "einheit": "Stück(e)",
                    "ware": None,
                    "neuer_ware": "Artikel C neu",
                    "inventurart": None,
                    "gebinde": {},
                    "vorhanden_pk": None,
                }
            ],
            self.haus,
        )
        self.assertEqual(len(bericht.fehler), 1)
        self.assertEqual(Hausartikel.objects.filter(artikelnummer="101001").count(), 1)
