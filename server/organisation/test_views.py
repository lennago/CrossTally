"""The pages of houses, areas and locations, including access protection and staff."""

from decimal import Decimal

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import NoReverseMatch, reverse

from accounts.models import CustomUser
from django_project.testing import AngemeldeteSeiten, bereich, jahresinventur, monatsinventur
from katalog.models import (
    Basiseinheit,
    Dimension,
    Erwartung,
    ErwartungGebinde,
    Gebinde,
    Hausartikel,
    Inventurart,
    Rezepturartikel,
    Ware,
    Warengruppe,
)

from .models import Bereich, Haus, Ort

User = CustomUser


def fuehrungskraft(**kwargs):
    """Signing in is required everywhere. StaffOnlyMiddleware protects all non-API paths."""
    vorgaben = {
        "username": "chefin",
        "email": "chefin@example.com",
        "password": "testpass1234",
        "first_name": "Carla",
        "last_name": "Chefin",
        "is_staff": True,
    }
    return User.objects.create_user(**(vorgaben | kwargs))


class ZugriffsschutzTests(TestCase):
    """StaffOnlyMiddleware: only managers get to the web interface."""

    @classmethod
    def setUpTestData(cls):
        cls.haus = Haus.objects.create(name="Kino Nord")

    def test_anonym_wird_zum_login_geschickt(self):
        antwort = self.client.get(reverse("haus_list"))
        self.assertEqual(antwort.status_code, 302)
        self.assertIn("/accounts/login/", antwort["Location"])

    def test_zaehler_landet_auf_der_sperrseite(self):
        self.client.force_login(fuehrungskraft(username="zaehler", is_staff=False))
        antwort = self.client.get(reverse("haus_list"))
        self.assertRedirects(antwort, reverse("kein_zugriff"))


def hausartikel(haus, nummer="000001", name="Artikel B", warengruppe=None) -> Hausartikel:
    """A house article including its product.

    The identity hangs on the product, the house on the house article.
    For the tests here both are extras.
    """
    return Hausartikel.objects.create(
        haus=haus,
        name=name,
        artikelnummer=nummer,
        ware=Ware.objects.create(
            name=name, basiseinheit=Basiseinheit.objects.get(dimension=Dimension.STUECK)
        ),
        warengruppe=warengruppe or Warengruppe.objects.get_or_create(name="Warengruppe G")[0],
    )


class HausViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.haus = Haus.objects.create(name="Kino Nord")
        cls.vorne = bereich(cls.haus, "Theke")
        cls.hinten = bereich(cls.haus, "Lager")
        cls.theke = Ort.objects.create(name="Theke A", bereich=cls.vorne)
        cls.lager = Ort.objects.create(name="Lager", bereich=cls.hinten)

        for nummer, name in (("000001", "Artikel B"), ("000002", "Artikel C")):
            artikel = hausartikel(cls.haus, nummer=nummer, name=name)
            Erwartung.objects.create(ort=cls.theke, artikel=artikel)

    def setUp(self):
        self.client.force_login(fuehrungskraft())

    def test_liste_zeigt_orte_und_erwartungszahl(self):
        antwort = self.client.get(reverse("haus_list"))
        self.assertEqual(antwort.status_code, 200)
        self.assertTemplateUsed(antwort, "organisation/haus_list.html")

        orte = {
            ort.name: ort.anzahl_erwartungen
            for gliederung in antwort.context["haeuser"][0].bereiche.all()
            for ort in gliederung.orte.all()
        }
        self.assertEqual(orte, {"Theke A": 2, "Lager": 0})
        self.assertContains(antwort, "Theke A")

    def test_detail_zeigt_bereiche_mit_ihren_orten(self):
        antwort = self.client.get(self.haus.get_absolute_url())
        self.assertEqual(antwort.status_code, 200)
        gliederung = {
            b.name: {ort.name: ort.anzahl_erwartungen for ort in b.orte.all()}
            for b in antwort.context["haus"].bereiche.all()
        }
        self.assertEqual(gliederung, {"Theke": {"Theke A": 2}, "Lager": {"Lager": 0}})

    def test_detail_legt_neuen_bereich_an(self):
        """Areas arise on the house, the locations below them on the area's page."""
        antwort = self.client.post(self.haus.get_absolute_url(), {"name": "Kühlung"})
        self.assertRedirects(antwort, self.haus.get_absolute_url())
        self.assertTrue(Bereich.objects.filter(haus=self.haus, name="Kühlung").exists())

    def test_detail_weist_doppelten_bereichsnamen_ab(self):
        antwort = self.client.post(self.haus.get_absolute_url(), {"name": "Lager"})
        self.assertEqual(antwort.status_code, 200)
        self.assertFormError(
            antwort.context["form"], "name", ["„Lager“ gibt es in Kino Nord bereits."]
        )
        self.assertEqual(Bereich.objects.filter(haus=self.haus, name="Lager").count(), 1)

    def test_bereichsseite_legt_orte_an(self):
        antwort = self.client.post(
            self.vorne.get_absolute_url(), {"name": "Gefrierfach", "bereich": self.vorne.pk}
        )
        self.assertRedirects(antwort, self.vorne.get_absolute_url())
        self.assertTrue(Ort.objects.filter(bereich=self.vorne, name="Gefrierfach").exists())

    def test_bereich_umbenennen_laesst_die_orte_stehen(self):
        antwort = self.client.post(
            reverse("bereich_edit", args=[self.vorne.pk]), {"name": "Verkaufstheke"}
        )
        self.assertRedirects(antwort, self.vorne.get_absolute_url())
        self.vorne.refresh_from_db()
        self.assertEqual(self.vorne.name, "Verkaufstheke")
        self.assertEqual(list(self.vorne.orte.values_list("name", flat=True)), ["Theke A"])

    def test_bereich_loeschen_nimmt_die_orte_mit(self):
        antwort = self.client.post(reverse("bereich_delete", args=[self.vorne.pk]))
        self.assertRedirects(antwort, self.haus.get_absolute_url())
        self.assertFalse(Ort.objects.filter(name="Theke A").exists())

    def test_haus_anlegen(self):
        antwort = self.client.post(reverse("haus_add"), {"name": "Kino Ost"})
        neues_haus = Haus.objects.get(name="Kino Ost")
        self.assertRedirects(antwort, neues_haus.get_absolute_url())

    def test_haus_umbenennen(self):
        antwort = self.client.post(
            reverse("haus_edit", args=[self.haus.pk]), {"name": "Kino Mitte"}
        )
        self.assertRedirects(antwort, self.haus.get_absolute_url())
        self.haus.refresh_from_db()
        self.assertEqual(self.haus.name, "Kino Mitte")

    def test_leeres_haus_loeschen(self):
        leer = Haus.objects.create(name="Kino Süd")
        antwort = self.client.post(reverse("haus_delete", args=[leer.pk]))
        self.assertRedirects(antwort, reverse("haus_list"))
        self.assertFalse(Haus.objects.filter(pk=leer.pk).exists())

    def test_haus_mit_hausartikeln_ist_geschuetzt(self):
        """The article history must not vanish with the house."""
        antwort = self.client.post(reverse("haus_delete", args=[self.haus.pk]), follow=True)
        self.assertContains(antwort, "lässt sich nicht löschen")
        self.assertContains(antwort, "2 Hausartikel")
        self.assertTrue(Haus.objects.filter(pk=self.haus.pk).exists())


class TemplateSmokeTests(TestCase):
    """Open every page once. That catches typos in templates and URL names."""

    @classmethod
    def setUpTestData(cls):
        cls.haus = Haus.objects.create(name="Kino Nord")
        cls.ort = Ort.objects.create(name="Theke A", bereich=bereich(cls.haus))
        cls.artikel = hausartikel(cls.haus)
        cls.erwartung = Erwartung.objects.create(ort=cls.ort, artikel=cls.artikel)

    def setUp(self):
        self.client.force_login(fuehrungskraft())

    def test_alle_seiten_rendern(self):
        seiten = [
            reverse("haus_list"),
            reverse("haus_add"),
            reverse("haus_detail", args=[self.haus.pk]),
            reverse("haus_edit", args=[self.haus.pk]),
            reverse("haus_delete", args=[self.haus.pk]),
            reverse("ort_detail", args=[self.ort.pk]),
            reverse("ort_edit", args=[self.ort.pk]),
            reverse("ort_delete", args=[self.ort.pk]),
            reverse("zaehlliste", args=[self.ort.pk]),
        ]
        for seite in seiten:
            with self.subTest(seite=seite):
                self.assertEqual(self.client.get(seite).status_code, 200)


class OrtViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.haus = Haus.objects.create(name="Kino Nord")
        cls.ort = Ort.objects.create(name="Theke A", bereich=bereich(cls.haus))
        cls.artikel = hausartikel(cls.haus)

    def setUp(self):
        self.client.force_login(fuehrungskraft())

    def test_orte_haben_keine_listenansicht(self):
        with self.assertRaises(NoReverseMatch):
            reverse("ort_list")

    def test_detail_listet_erwartete_artikel(self):
        Erwartung.objects.create(ort=self.ort, artikel=self.artikel)
        antwort = self.client.get(self.ort.get_absolute_url())
        self.assertEqual(antwort.status_code, 200)
        self.assertTemplateUsed(antwort, "organisation/ort_detail.html")
        self.assertContains(antwort, "Artikel B")

    def test_detail_ohne_erwartungen(self):
        antwort = self.client.get(self.ort.get_absolute_url())
        self.assertContains(antwort, "noch kein Artikel erwartet")

    def test_detail_nennt_die_vorgabe_eines_inaktiven_gebindes_inaktiv(self):
        """It gets no row in new inventories, as the counting list shows too."""
        erwartung = Erwartung.objects.create(ort=self.ort, artikel=self.artikel)
        for name, menge, aktiv in (("Kasten", "20", True), ("Kiste", "12", False)):
            gebinde = Gebinde.objects.create(
                ware=self.artikel.ware,
                name=name,
                faktor_in_basiseinheit=Decimal(menge),
                aktiv=aktiv,
            )
            ErwartungGebinde.objects.create(erwartung=erwartung, gebinde=gebinde)
        antwort = self.client.get(self.ort.get_absolute_url())
        self.assertEqual(antwort.content.decode().count("· inaktiv"), 1)

    def test_ort_umbenennen(self):
        antwort = self.client.post(
            reverse("ort_edit", args=[self.ort.pk]),
            {"name": "Theke B", "bereich": self.ort.bereich_id},
        )
        self.assertRedirects(antwort, self.ort.get_absolute_url())
        self.ort.refresh_from_db()
        self.assertEqual(self.ort.name, "Theke B")

    def test_ort_in_anderen_bereich_verschieben(self):
        """The expectations stay, only the classification changes."""
        Erwartung.objects.create(ort=self.ort, artikel=self.artikel)
        kuehlung = bereich(self.haus, "Kühlung")
        self.client.post(
            reverse("ort_edit", args=[self.ort.pk]),
            # The same form also maintains the article selection. Without sending
            # it along here, it would be emptied.
            {"name": self.ort.name, "bereich": kuehlung.pk, "artikel": [self.artikel.pk]},
        )
        self.ort.refresh_from_db()
        self.assertEqual(self.ort.bereich, kuehlung)
        self.assertEqual(self.ort.erwartungen.count(), 1)

    def test_ort_loeschen_fuehrt_zurueck_zum_bereich(self):
        bereichsseite = self.ort.bereich.get_absolute_url()
        antwort = self.client.post(reverse("ort_delete", args=[self.ort.pk]))
        self.assertRedirects(antwort, bereichsseite)
        self.assertFalse(Ort.objects.filter(pk=self.ort.pk).exists())

    def test_ort_loeschen_nimmt_erwartungen_mit(self):
        Erwartung.objects.create(ort=self.ort, artikel=self.artikel)
        self.client.post(reverse("ort_delete", args=[self.ort.pk]))
        self.assertEqual(Erwartung.objects.count(), 0)


class RezepturartikelErwartungTests(TestCase):
    """Recipe articles may be expected at a location just like house articles."""

    def setUp(self):
        self.haus = Haus.objects.create(name="Kino Nord")
        self.ort = Ort.objects.create(name="Theke A", bereich=bereich(self.haus))
        self.client.force_login(fuehrungskraft())

    def test_detail_markiert_rezepturartikel(self):
        rezepturartikel = Rezepturartikel.objects.create(
            name="Portion N",
            basiseinheit=Basiseinheit.objects.get(dimension=Dimension.STUECK),
        )
        Erwartung.objects.create(ort=self.ort, artikel=rezepturartikel)
        antwort = self.client.get(self.ort.get_absolute_url())
        self.assertContains(antwort, "Portion N")
        self.assertContains(antwort, "Rezepturartikel")


class HausMitarbeitendeTests(TestCase):
    """Counters are assigned on the house. Managers count everywhere."""

    def setUp(self):
        self.haus = Haus.objects.create(name="Kino Nord")
        self.chefin = fuehrungskraft()
        self.zaehlerin = fuehrungskraft(
            username="zoe",
            email="zoe@example.com",
            first_name="Zoe",
            last_name="Zählerin",
            is_staff=False,
        )
        self.client.force_login(self.chefin)

    def test_formular_zeigt_die_bestehende_zuordnung(self):
        self.haus.mitarbeitende.add(self.zaehlerin)
        antwort = self.client.get(reverse("haus_edit", args=[self.haus.pk]))
        self.assertCountEqual(
            antwort.context["form"].fields["mitarbeitende"].initial, [self.zaehlerin]
        )

    def test_fuehrungskraefte_stehen_nicht_zur_auswahl(self):
        """They count everywhere anyway. An assignment would have no effect."""
        antwort = self.client.get(reverse("haus_edit", args=[self.haus.pk]))
        auswahl = antwort.context["form"].fields["mitarbeitende"].queryset
        self.assertIn(self.zaehlerin, auswahl)
        self.assertNotIn(self.chefin, auswahl)

    def test_zuordnung_setzen(self):
        antwort = self.client.post(
            reverse("haus_edit", args=[self.haus.pk]),
            {"name": "Kino Nord", "mitarbeitende": [self.zaehlerin.pk]},
        )
        self.assertRedirects(antwort, self.haus.get_absolute_url())
        self.assertCountEqual(self.haus.mitarbeitende.all(), [self.zaehlerin])

    def test_zuordnung_entfernen(self):
        self.haus.mitarbeitende.set([self.zaehlerin])
        self.client.post(
            reverse("haus_edit", args=[self.haus.pk]), {"name": "Kino Nord", "mitarbeitende": []}
        )
        self.assertEqual(self.haus.mitarbeitende.count(), 0)

    def test_zuordnung_einer_fuehrungskraft_ueberlebt_das_speichern(self):
        """It is not in the form. 'set' would have removed it silently."""
        self.haus.mitarbeitende.add(self.chefin)
        self.client.post(
            reverse("haus_edit", args=[self.haus.pk]),
            {"name": "Kino Nord", "mitarbeitende": [self.zaehlerin.pk]},
        )
        self.assertCountEqual(self.haus.mitarbeitende.all(), [self.chefin, self.zaehlerin])

    def test_haus_ohne_zaehler_bleibt_speicherbar(self):
        self.haus.mitarbeitende.add(self.zaehlerin)
        self.client.post(reverse("haus_edit", args=[self.haus.pk]), {"name": "Kino Nord"})
        self.assertEqual(self.haus.mitarbeitende.count(), 0)

    def test_neues_haus_direkt_mit_zaehlern(self):
        self.client.post(
            reverse("haus_add"), {"name": "Kino Ost", "mitarbeitende": [self.zaehlerin.pk]}
        )
        self.assertCountEqual(
            Haus.objects.get(name="Kino Ost").mitarbeitende.all(), [self.zaehlerin]
        )

    def test_detailseite_listet_zaehler_und_fuehrungskraefte(self):
        self.haus.mitarbeitende.add(self.zaehlerin)
        antwort = self.client.get(self.haus.get_absolute_url())
        self.assertContains(antwort, "Zoe Zählerin")
        self.assertContains(antwort, "Carla Chefin")

    def test_fuehrungskraft_ist_an_kein_haus_gebunden(self):
        """That says nothing about counting. For that the assignment on the inventory counts."""
        self.assertTrue(self.chefin.gilt_fuer_alle_haeuser)
        self.assertFalse(self.zaehlerin.gilt_fuer_alle_haeuser)
        self.assertIn(self.chefin, self.haus.zugehoerige_benutzer())
        self.assertNotIn(self.zaehlerin, self.haus.zugehoerige_benutzer())

    def test_doppelter_hausname_wird_abgewiesen(self):
        antwort = self.client.post(reverse("haus_add"), {"name": "Kino Nord"})
        self.assertEqual(antwort.status_code, 200)
        self.assertIn("name", antwort.context["form"].errors)
        self.assertEqual(Haus.objects.filter(name="Kino Nord").count(), 1)


class HaeuserlisteZahlen(AngemeldeteSeiten):
    """The house list says how much work an inventory would be there."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.haus = Haus.objects.create(name="Kino Ost")
        gruppe = Warengruppe.objects.create(name="Warengruppe G")
        stueck = Basiseinheit.objects.get(name="Stück(e)")

        def ware(name):
            return Ware.objects.create(name=name, basiseinheit=stueck)

        cls.monatlich = Hausartikel.objects.create(
            haus=cls.haus,
            ware=ware("Artikel C"),
            warengruppe=gruppe,
            artikelnummer="101001",
            name="Artikel C",
        )
        Hausartikel.objects.create(
            haus=cls.haus,
            ware=ware("Ware D"),
            warengruppe=gruppe,
            artikelnummer="101002",
            name="Ware D",
            inventurart=jahresinventur(),
        )
        inaktiv = Hausartikel.objects.create(
            haus=cls.haus,
            ware=ware("Alte Ware"),
            warengruppe=gruppe,
            artikelnummer="101003",
            name="Alte Ware",
        )
        inaktiv.deaktivieren()
        eingestellt = Hausartikel.objects.create(
            haus=cls.haus,
            ware=ware("Ausgelistet"),
            warengruppe=gruppe,
            artikelnummer="101004",
            name="Ausgelistet",
        )
        eingestellt.einstellen()

    def test_eingestellte_und_inaktive_stehen_fuer_sich(self):
        super().setUp()
        antwort = self.client.get(reverse("haus_list"))
        haus = antwort.context["haeuser"][0]
        self.assertEqual(haus.anzahl_inaktiv, 1)
        self.assertEqual(haus.anzahl_eingestellt, 1)
        self.assertContains(antwort, "eingestellt")

    def test_hausseite_nennt_inaktive_und_eingestellte(self):
        self.client.force_login(self.user)
        antwort = self.client.get(self.haus.get_absolute_url())
        self.assertEqual(antwort.context["anzahl_inaktiv"], 1)
        self.assertEqual(antwort.context["anzahl_eingestellt"], 1)
        self.assertContains(antwort, "1 eingestellt")

    def test_gesamt_aktiv_und_je_art(self):
        self.client.force_login(self.user)
        antwort = self.client.get(reverse("haus_list"))
        haus = antwort.context["haeuser"][0]
        # The discontinued one counts towards the total, but not towards the active ones.
        self.assertEqual(haus.anzahl_artikel, 4)
        self.assertEqual(haus.anzahl_aktiv, 2)
        # Per type only the active ones, the inactive monthly one does not count.
        self.assertEqual(haus.arten_zahlen, [(monatsinventur(), 1), (jahresinventur(), 1)])
        self.assertContains(antwort, "Jahresinventur")

    def test_art_ohne_aktive_artikel_fehlt(self):
        """A type the house carries nothing of would only be a zero in the list."""
        Inventurart.objects.create(name="Becherinventur")
        self.client.force_login(self.user)
        haus = self.client.get(reverse("haus_list")).context["haeuser"][0]
        self.assertNotIn("Becherinventur", [art.name for art, _ in haus.arten_zahlen])


class AuswahlDerBereicheFragtNichtJeBereich(AngemeldeteSeiten):
    """An area names its house ('Bereich.__str__'), and until 10/2026 every choice asked again.

    The same in the administration, where every row of an inline names its
    location with house and area.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.haus = Haus.objects.create(name="Kino Ost")
        cls.theke = Bereich.objects.create(name="Theke", haus=cls.haus)
        cls.ort = Ort.objects.create(name="Thekenfront", bereich=cls.theke)

    def abfragen(self, adresse: str) -> int:
        with CaptureQueriesContext(connection) as erfasst:
            self.assertEqual(self.client.get(adresse).status_code, 200)
        return len(erfasst)

    def mehr(self, anzahl: int) -> None:
        """More areas in the house and more locations at the counter."""
        start = Bereich.objects.filter(haus=self.haus).count()
        for i in range(start, start + anzahl):
            Bereich.objects.create(name=f"Lager {i}", haus=self.haus)
            Ort.objects.create(name=f"Regal {i}", bereich=self.theke)

    def test_seiten_und_verwaltung(self):
        adressen = {
            "bereich_detail": reverse("bereich_detail", args=[self.theke.pk]),
            "ort_edit": reverse("ort_edit", args=[self.ort.pk]),
            "verwaltung_bereich": reverse(
                "admin:organisation_bereich_change", args=[self.theke.pk]
            ),
            "verwaltung_haus": reverse("admin:organisation_haus_change", args=[self.haus.pk]),
        }
        self.mehr(1)
        for adresse in adressen.values():
            self.client.get(adresse)  # fills Django's cache of the content types
        wenige = {name: self.abfragen(adresse) for name, adresse in adressen.items()}
        self.mehr(4)
        for name, adresse in adressen.items():
            with self.subTest(seite=name):
                self.assertEqual(self.abfragen(adresse), wenige[name])
