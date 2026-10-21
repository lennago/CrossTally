"""The catalog admin configuration.

Checked is what carries logic of its own: the locks on the rows from the
data migration, the hidden MTI core, the restricted pack selection and the
bulk actions. Pure declarations like 'list_display' stay out. Those are
already checked by Django's own system check.
"""

from decimal import Decimal
from typing import cast

from django.contrib.admin.sites import site
from django.forms import ModelChoiceField
from django.test import RequestFactory, TestCase
from django.urls import reverse

from accounts.models import CustomUser
from django_project.form_fields import auswahl
from django_project.testing import bereich
from organisation.models import Haus, Ort

from .admin import ErwartungGebindeInline
from .models import (
    Artikel,
    Artikelstatus,
    Basiseinheit,
    Behaelter,
    Dimension,
    Erwartung,
    Gebinde,
    Hausartikel,
    Rezepturartikel,
    Rezepturkomponente,
    Ware,
    Warengruppe,
)
from .test_models import hausartikel, ware

User = CustomUser


def administrator():
    return User.objects.create_superuser(
        username="admin",
        email="admin@example.com",
        password="testpass1234",
        first_name="Ada",
        last_name="Admin",
    )


def stueck() -> Basiseinheit:
    return Basiseinheit.objects.get(dimension=Dimension.STUECK)


class GesperrteStammdatenTests(TestCase):
    """Base units and "Without container" come from the data migration."""

    def setUp(self):
        self.request = RequestFactory().get("/")
        self.admin_konto = administrator()
        self.request.user = self.admin_konto

    def test_basiseinheiten_sind_unveraenderlich(self):
        admin = site._registry[Basiseinheit]
        self.assertFalse(admin.has_add_permission(self.request))
        self.assertFalse(admin.has_change_permission(self.request))
        self.assertFalse(admin.has_delete_permission(self.request))

    def test_basiseinheitenliste_bleibt_erreichbar(self):
        self.client.force_login(self.admin_konto)
        antwort = self.client.get(reverse("admin:katalog_basiseinheit_changelist"))
        self.assertContains(antwort, "kg")

    def test_ohne_behaelter_laesst_sich_nicht_loeschen(self):
        """It is available everywhere. Without it weighing would break."""
        admin = site._registry[Behaelter]
        self.assertFalse(admin.has_delete_permission(self.request, Behaelter.ohne_behaelter()))

    def test_andere_behaelter_schon(self):
        admin = site._registry[Behaelter]
        schale = Behaelter.objects.create(name="Schale", tara=Decimal("250"))
        self.assertTrue(admin.has_delete_permission(self.request, schale))


class ArtikelkernTests(TestCase):
    """The MTI core is registered only for 'autocomplete_fields'."""

    def setUp(self):
        self.admin = site._registry[Artikel]
        self.request = RequestFactory().get("/")
        self.admin_konto = administrator()
        self.request.user = self.admin_konto

    def test_taucht_nicht_im_menue_auf(self):
        self.assertEqual(self.admin.get_model_perms(self.request), {})

    def test_anlegen_ist_gesperrt(self):
        self.assertFalse(self.admin.has_add_permission(self.request))

    def test_autocomplete_findet_beide_artikelarten(self):
        """Without 'search_fields' on the article the expectation would be unusable in the admin."""
        haus = Haus.objects.create(name="Kino Nord")
        hausartikel(haus, ware("Artikel B 0,33"))
        Rezepturartikel.objects.create(name="Portion N", basiseinheit=stueck())
        self.client.force_login(self.admin_konto)

        antwort = self.client.get(
            reverse("admin:autocomplete"),
            {
                "app_label": "katalog",
                "model_name": "erwartung",
                "field_name": "artikel",
                "term": "",
            },
        )
        self.assertEqual(antwort.status_code, 200)
        namen = [eintrag["text"] for eintrag in antwort.json()["results"]]
        self.assertIn("Artikel B 0,33", namen)
        self.assertIn("Portion N", namen)


class ErwartungGebindeInlineTests(TestCase):
    """The pack selection belongs restricted to the product the expectation means."""

    @classmethod
    def setUpTestData(cls):
        cls.haus = Haus.objects.create(name="Kino Nord")
        cls.ort = Ort.objects.create(name="Theke", bereich=bereich(cls.haus))
        cls.bier = ware("Artikel B 0,33")
        cls.cola = ware("Artikel C 0,33")
        cls.artikel = hausartikel(cls.haus, cls.bier)
        hausartikel(cls.haus, cls.cola, nummer="101002")

        cls.kasten = Gebinde.objects.create(
            ware=cls.bier, name="Kasten", faktor_in_basiseinheit=Decimal("20")
        )
        cls.inaktiv = Gebinde.objects.create(
            ware=cls.bier,
            name="Palette",
            faktor_in_basiseinheit=Decimal("400"),
            aktiv=False,
        )
        cls.fremdes = Gebinde.objects.create(
            ware=cls.cola, name="Kiste", faktor_in_basiseinheit=Decimal("12")
        )

    def setUp(self):
        self.inline = ErwartungGebindeInline(Erwartung, site)
        self.request = RequestFactory().get("/")
        self.admin_konto = administrator()
        self.request.user = self.admin_konto

    def auswahl(self, erwartung) -> list[int]:
        """The numbers of the packs the inline offers for this expectation."""
        self.inline.get_formset(self.request, erwartung)
        feld = self.inline.formfield_for_foreignkey(
            self.inline.model._meta.get_field("gebinde"), self.request
        )
        assert isinstance(feld, ModelChoiceField)
        return [gebinde.pk for gebinde in feld.queryset or ()]

    def test_nur_gebinde_der_erwarteten_ware(self):
        erwartung = Erwartung.objects.create(artikel=self.artikel, ort=self.ort)
        self.assertEqual(self.auswahl(erwartung), [self.kasten.pk])

    def test_inaktive_bleiben_draussen(self):
        erwartung = Erwartung.objects.create(artikel=self.artikel, ort=self.ort)
        self.assertNotIn(self.inaktiv.pk, self.auswahl(erwartung))

    def test_fremdes_gebinde_bleibt_draussen(self):
        erwartung = Erwartung.objects.create(artikel=self.artikel, ort=self.ort)
        self.assertNotIn(self.fremdes.pk, self.auswahl(erwartung))

    def test_neue_erwartung_hat_noch_keine_auswahl(self):
        """Save first, then assign packs. Before that the article is not fixed."""
        self.assertEqual(self.auswahl(None), [])

    def test_rezepturartikel_hat_keine_gebinde(self):
        portion = Rezepturartikel.objects.create(name="Portion N", basiseinheit=stueck())
        Rezepturkomponente.objects.create(
            rezepturartikel=portion, ware=self.bier, menge_in_basiseinheit=Decimal("1")
        )
        erwartung = Erwartung.objects.create(artikel=portion, ort=self.ort)
        self.assertEqual(self.auswahl(erwartung), [])

    def test_aenderungsseite_rendert(self):
        erwartung = Erwartung.objects.create(artikel=self.artikel, ort=self.ort)
        self.client.force_login(self.admin_konto)
        antwort = self.client.get(reverse("admin:katalog_erwartung_change", args=[erwartung.pk]))
        self.assertContains(antwort, "Kasten")
        self.assertNotContains(antwort, "Kiste")


class MassenaktionenTests(TestCase):
    """The actions of the list views."""

    @classmethod
    def setUpTestData(cls):
        cls.haus = Haus.objects.create(name="Kino Nord")
        cls.gruppe = Warengruppe.objects.create(name="Warengruppe U")
        cls.eine = ware("Ware G")
        cls.andere = ware("Artikel L")
        cls.artikel = hausartikel(cls.haus, cls.eine, warengruppe=cls.gruppe)
        hausartikel(cls.haus, cls.andere, nummer="101002", warengruppe=cls.gruppe)

    def setUp(self):
        self.client.force_login(administrator())

    def ausfuehren(self, modell: str, aktion: str, pks: list[int]):
        return self.client.post(
            reverse(f"admin:katalog_{modell}_changelist"),
            {"action": aktion, "_selected_action": [str(pk) for pk in pks]},
            follow=True,
        )

    def test_hausartikel_inaktiv_setzen(self):
        self.ausfuehren("hausartikel", "deaktivieren", [self.artikel.pk])
        self.artikel.refresh_from_db()
        self.assertEqual(self.artikel.status, Artikelstatus.INAKTIV)
        self.assertIsNotNone(self.artikel.gueltig_bis)

    def test_hausartikel_einstellen(self):
        """Saved individually: discontinued drops out of the counting lists."""
        ort = Ort.objects.create(name="Lager", bereich=bereich(self.artikel.haus))
        Erwartung.objects.create(ort=ort, artikel=self.artikel)
        self.ausfuehren("hausartikel", "einstellen", [self.artikel.pk])
        self.artikel.refresh_from_db()
        self.assertEqual(self.artikel.status, Artikelstatus.EINGESTELLT)
        self.assertFalse(Erwartung.objects.filter(artikel=self.artikel).exists())

    def test_warengruppe_inaktiv_setzen(self):
        self.ausfuehren("warengruppe", "deaktivieren", [self.gruppe.pk])
        self.assertFalse(Warengruppe.objects.get(pk=self.gruppe.pk).aktiv)

    def test_warengruppe_einstellen(self):
        self.ausfuehren("warengruppe", "einstellen", [self.gruppe.pk])
        self.artikel.refresh_from_db()
        self.assertEqual(self.artikel.status, Artikelstatus.EINGESTELLT)

    def test_ware_inaktiv_setzen(self):
        self.ausfuehren("ware", "deaktivieren", [self.eine.pk])
        self.artikel.refresh_from_db()
        self.assertFalse(self.artikel.aktiv)

    def test_ware_einstellen(self):
        self.ausfuehren("ware", "einstellen", [self.eine.pk])
        self.artikel.refresh_from_db()
        self.assertEqual(self.artikel.status, Artikelstatus.EINGESTELLT)


class HausartikelStatusImAdmin(TestCase):
    """The status "Not countable" follows the product's family. In the admin it is not available."""

    def setUp(self):
        self.request = RequestFactory().get("/")
        self.admin_konto = administrator()
        self.request.user = self.admin_konto
        self.admin = site._registry[Hausartikel]
        self.haus = Haus.objects.create(name="Kino")

    def test_nicht_zaehlbar_steht_nicht_zur_wahl(self):
        form = self.admin.get_form(self.request)()
        wahl = cast("list[tuple[str, str]]", auswahl(form, "status").choices)
        werte = {wert for wert, _ in wahl}
        self.assertNotIn(Artikelstatus.IGNORIERT, werte)
        self.assertIn(Artikelstatus.EINGESTELLT, werte)

    def test_ein_ignorierter_zeigt_seinen_status_nur_an(self):
        pfand = hausartikel(self.haus, ware("Artikel P"))
        pfand.ignorieren()
        pfand.refresh_from_db()
        self.assertIn("status", self.admin.get_readonly_fields(self.request, pfand))
        self.assertNotIn(
            "status",
            self.admin.get_readonly_fields(
                self.request, hausartikel(self.haus, ware("Artikel C"), "101002")
            ),
        )


class AdminSeitenSmokeTests(TestCase):
    """Open every registered list and the change pages once."""

    @classmethod
    def setUpTestData(cls):
        haus = Haus.objects.create(name="Kino Nord")
        ort = Ort.objects.create(name="Theke", bereich=bereich(haus))
        cls.bier = ware("Artikel B 0,33")
        cls.nachos = ware("Artikel N", "kg")
        cls.artikel = hausartikel(haus, cls.bier)
        Gebinde.objects.create(ware=cls.bier, name="Kasten", faktor_in_basiseinheit=Decimal("20"))
        cls.nachos.abwiegbar = True
        cls.nachos.save(update_fields=["abwiegbar"])

        cls.portion = Rezepturartikel.objects.create(name="Portion N", basiseinheit=stueck())
        Rezepturkomponente.objects.create(
            rezepturartikel=cls.portion, ware=cls.bier, menge_in_basiseinheit=Decimal("1")
        )
        Erwartung.objects.create(artikel=cls.artikel, ort=ort)

    def setUp(self):
        self.client.force_login(administrator())

    def test_alle_listen_rendern(self):
        modelle = [
            "warengruppe",
            "basiseinheit",
            "ware",
            "hausartikel",
            "rezepturartikel",
            "gebinde",
            "behaelter",
            "erwartung",
        ]
        for modell in modelle:
            with self.subTest(modell=modell):
                pfad = reverse(f"admin:katalog_{modell}_changelist")
                self.assertEqual(self.client.get(pfad).status_code, 200)

    def test_gebindeliste_nennt_die_umrechnung(self):
        antwort = self.client.get(reverse("admin:katalog_gebinde_changelist"))
        self.assertContains(antwort, "Kasten")
        self.assertContains(antwort, "1 × 20")

    def test_wareliste_zeigt_haeuser_und_gebinde(self):
        antwort = self.client.get(reverse("admin:katalog_ware_changelist"))
        self.assertContains(antwort, "Kino Nord")
        self.assertContains(antwort, "Kasten")

    def test_rezepturartikelliste_zeigt_die_rezeptur(self):
        antwort = self.client.get(reverse("admin:katalog_rezepturartikel_changelist"))
        self.assertContains(antwort, "1 × Artikel B 0,33")

    def test_erwartungsliste_zeigt_das_haus(self):
        antwort = self.client.get(reverse("admin:katalog_erwartung_changelist"))
        self.assertContains(antwort, "Kino Nord")

    def test_warengruppenliste_zaehlt_die_artikel(self):
        antwort = self.client.get(reverse("admin:katalog_warengruppe_changelist"))
        self.assertEqual(antwort.status_code, 200)

    def test_aenderungsseiten_rendern(self):
        seiten = {
            "hausartikel": self.artikel.pk,
            "ware": self.bier.pk,
            "rezepturartikel": self.portion.pk,
        }
        for modell, pk in seiten.items():
            with self.subTest(modell=modell):
                pfad = reverse(f"admin:katalog_{modell}_change", args=[pk])
                self.assertEqual(self.client.get(pfad).status_code, 200)

    def test_wareseite_traegt_das_abwiegen(self):
        """Weighing is a tick on the product, no longer a pack of its own."""
        pfad = reverse("admin:katalog_ware_change", args=[self.nachos.pk])
        antwort = self.client.get(pfad)
        self.assertContains(antwort, "Wird abgewogen")
        self.assertContains(antwort, "Übliche Behälter")

    def test_organisation_und_konten_rendern_ebenfalls(self):
        for pfad in ("admin:organisation_haus_changelist", "admin:organisation_ort_changelist"):
            with self.subTest(pfad=pfad):
                self.assertEqual(self.client.get(reverse(pfad)).status_code, 200)


class DimensionTests(TestCase):
    """Make sure the test data above hits the units correctly."""

    def test_kg_ist_masse(self):
        self.assertEqual(Basiseinheit.objects.get(name="kg").dimension, Dimension.MASSE)


class NurUeberDieSeiteAenderbar(TestCase):
    """Unit and inventory type change through the pages, with their rules. The admin shows them.

    Until 10/2026 an administrator could change both here as well, past the
    conversion of the packs and past the order of the counting list.
    """

    def setUp(self):
        self.request = RequestFactory().get("/")
        self.request.user = administrator()

    def test_die_einheit_der_ware_ist_nur_zu_lesen(self):
        satz = ware("Artikel C")
        verwaltung = site._registry[Ware]
        self.assertIn("basiseinheit", verwaltung.get_readonly_fields(self.request, satz))
        # Creating still needs it: there is nothing to convert yet.
        self.assertNotIn("basiseinheit", verwaltung.get_readonly_fields(self.request))

    def test_die_inventurart_des_hausartikels_ebenso(self):
        satz = hausartikel(Haus.objects.create(name="Kino Nord"), ware("Artikel C"))
        verwaltung = site._registry[Hausartikel]
        self.assertIn("inventurart", verwaltung.get_readonly_fields(self.request, satz))
        self.assertNotIn("inventurart", verwaltung.get_readonly_fields(self.request))
