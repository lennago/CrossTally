"""The inventory types: which house article counts in which tab, and how that is changed.

Every house article belongs to exactly one type. It can be changed for the
article, for every article of its product or for a product group. Checked
here is above all that the question "only this article or the whole
product" is asked where it matters, and that the rule for a new article
takes the type the product's other articles agree on.
"""

import re
from decimal import Decimal

from django.urls import reverse

from django_project.testing import (
    AngemeldeteSeiten,
    inventur_anlegen,
    jahresinventur,
    monatsinventur,
)
from organisation.models import Haus

from .forms import HausartikelForm
from .models import (
    Basiseinheit,
    Dimension,
    Hausartikel,
    Inventurart,
    Rezepturartikel,
    Rezepturkomponente,
    Ware,
    Warengruppe,
    Zaehlumfang,
    arten_je_artikel,
    arten_je_haus,
)
from .ware_merge import zusammenfuehren


class Grundlage(AngemeldeteSeiten):
    """Artikel C in two houses, Ware D only in Kino Nord, both counted monthly."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.nord = Haus.objects.create(name="Kino Nord")
        cls.ost = Haus.objects.create(name="Kino Ost")
        cls.gruppe = Warengruppe.objects.create(name="Warengruppe G")
        cls.andere_gruppe = Warengruppe.objects.create(name="Warengruppe H")
        cls.stueck = Basiseinheit.objects.get(dimension=Dimension.STUECK)
        cls.monat = monatsinventur()
        cls.jahr = jahresinventur()
        cls.cola = Ware.objects.create(name="Artikel C 0,33", basiseinheit=cls.stueck)
        cls.ware_d = Ware.objects.create(name="Ware D", basiseinheit=cls.stueck)
        cls.cola_nord = cls.artikel(cls.cola, cls.nord, "101001")
        cls.cola_ost = cls.artikel(cls.cola, cls.ost, "101001", gruppe=cls.andere_gruppe)
        cls.d_nord = cls.artikel(cls.ware_d, cls.nord, "101002")

    @classmethod
    def artikel(cls, ware, haus, nummer, gruppe=None, **abweichend) -> Hausartikel:
        return Hausartikel.objects.create(
            haus=haus,
            ware=ware,
            warengruppe=gruppe or cls.gruppe,
            artikelnummer=nummer,
            name=ware.name,
            **abweichend,
        )

    def art_von(self, artikel: Hausartikel) -> Inventurart:
        artikel.refresh_from_db()
        return artikel.inventurart


class DieArt(Grundlage):
    def test_die_migration_legt_die_monatsinventur_an(self):
        self.assertEqual(Inventurart.objects.first(), self.monat)
        self.assertEqual(self.monat.name, "Monatsinventur")

    def test_eine_neue_art_kommt_ans_ende(self):
        becher = Inventurart.objects.create(name="Becherinventur")
        self.assertEqual(list(Inventurart.objects.all()), [self.monat, self.jahr, becher])

    def test_verschieben_nummeriert_neu(self):
        becher = Inventurart.objects.create(name="Becherinventur")
        becher.verschieben(-1)
        self.assertEqual(list(Inventurart.objects.all()), [self.monat, becher, self.jahr])
        self.assertEqual(list(Inventurart.objects.values_list("reihenfolge", flat=True)), [1, 2, 3])
        self.monat.verschieben(-1)
        self.assertEqual(Inventurart.objects.first(), self.monat)

    def test_ohne_art_entsteht_die_monatsinventur_wieder(self):
        Hausartikel.objects.all().delete()
        Inventurart.objects.all().delete()
        self.assertEqual(Inventurart.erste().name, "Monatsinventur")

    def test_loeschen_nimmt_die_artikel_mit_in_die_nachfolgerin(self):
        becher = Inventurart.objects.create(name="Becherinventur")
        self.d_nord.inventurart_setzen(becher)
        self.assertEqual(becher.loeschen(self.jahr), 1)
        self.assertEqual(self.art_von(self.d_nord), self.jahr)
        self.assertFalse(Inventurart.objects.filter(name="Becherinventur").exists())


class Vorschlag(Grundlage):
    def neu(self, ware, haus) -> Hausartikel:
        return self.artikel(ware, haus, "101099")

    def test_ein_neuer_artikel_zaehlt_wie_die_uebrigen_seiner_ware(self):
        self.cola_nord.inventurart_setzen(self.jahr, ganze_ware=True)
        sued = Haus.objects.create(name="Kino Süd")
        self.assertEqual(self.neu(self.cola, sued).inventurart, self.jahr)

    def test_sind_sie_uneins_gilt_die_erste(self):
        self.cola_nord.inventurart_setzen(self.jahr)
        sued = Haus.objects.create(name="Kino Süd")
        self.assertEqual(self.neu(self.cola, sued).inventurart, self.monat)

    def test_eine_neue_ware_kommt_in_die_erste(self):
        ware = Ware.objects.create(name="Ware N", basiseinheit=self.stueck)
        self.assertEqual(self.neu(ware, self.nord).inventurart, self.monat)


class Setzen(Grundlage):
    def test_die_ware_setzt_alle_ihre_artikel_auch_eingestellte(self):
        self.cola_ost.einstellen()
        self.assertEqual(self.cola.inventurart_setzen(self.jahr), 2)
        self.assertEqual(
            (self.art_von(self.cola_nord), self.art_von(self.cola_ost)), (self.jahr,) * 2
        )

    def test_der_artikel_allein(self):
        self.assertEqual(self.cola_nord.inventurart_setzen(self.jahr), 1)
        self.assertEqual(self.art_von(self.cola_ost), self.monat)
        self.assertTrue(self.cola.arten_gemischt)

    def test_der_artikel_mit_der_ganzen_ware(self):
        self.cola_nord.inventurart_setzen(self.jahr, ganze_ware=True)
        self.assertEqual(self.art_von(self.cola_ost), self.jahr)
        self.assertEqual(self.cola.inventurart, self.jahr)

    def test_ohne_weiteren_gefuehrten_ziehen_die_eingestellten_mit(self):
        """A dead row would otherwise make the product look mixed."""
        self.cola_ost.einstellen()
        self.cola_nord.inventurart_setzen(self.jahr)
        self.assertEqual(self.art_von(self.cola_ost), self.jahr)
        self.assertFalse(self.cola.arten_gemischt)

    def test_die_ware_nennt_ihre_arten_mit_den_haeusern(self):
        self.cola_ost.inventurart_setzen(self.jahr)
        self.assertEqual(
            [
                (art, [haus.name for haus in haeuser])
                for art, haeuser in self.cola.arten_der_artikel()
            ],
            [(self.monat, ["Kino Nord"]), (self.jahr, ["Kino Ost"])],
        )
        self.assertIsNone(self.cola.inventurart)

    def test_die_warengruppe_setzt_nur_ihre_artikel(self):
        """Kino Ost carries the product under another group. There it keeps its type."""
        self.assertEqual(self.gruppe.inventurart_setzen(self.jahr), 2)
        self.assertEqual(
            (self.art_von(self.cola_nord), self.art_von(self.d_nord), self.art_von(self.cola_ost)),
            (self.jahr, self.jahr, self.monat),
        )
        self.assertEqual(self.gruppe.arten_verteilung(), [(self.jahr, 2)])


class Umfang(Grundlage):
    def test_der_filter_ueber_den_hausartikel(self):
        self.d_nord.inventurart_setzen(self.jahr)
        jaehrlich = Zaehlumfang((self.jahr.pk,))
        self.assertEqual(list(Hausartikel.objects.filter(jaehrlich.hausartikel())), [self.d_nord])
        self.d_nord.refresh_from_db()
        self.assertTrue(jaehrlich.umfasst(self.d_nord))

    def test_der_reiter_ist_die_erste_art_der_inventur(self):
        umfang = Zaehlumfang((self.monat.pk, self.jahr.pk))
        self.assertEqual(umfang.reiter([self.jahr.pk, self.monat.pk]), self.monat.pk)
        self.assertIsNone(Zaehlumfang((self.jahr.pk,)).reiter([self.monat.pk]))

    def test_rezepturartikel_mit_den_arten_seiner_zutaten_im_haus(self):
        portion = Rezepturartikel.objects.create(name="Portion CD", basiseinheit=self.stueck)
        for ware in (self.cola, self.ware_d):
            Rezepturkomponente.objects.create(
                rezepturartikel=portion, ware=ware, menge_in_basiseinheit=Decimal("1")
            )
        self.d_nord.inventurart_setzen(self.jahr)
        self.assertEqual(
            arten_je_artikel(self.nord.pk, [portion.pk, self.d_nord.pk]),
            {portion.pk: [self.monat.pk, self.jahr.pk], self.d_nord.pk: [self.jahr.pk]},
        )
        # In Kino Ost only Artikel C is carried, and it counts monthly.
        self.assertEqual(arten_je_artikel(self.ost.pk, [portion.pk]), {portion.pk: [self.monat.pk]})

    def test_die_arten_je_haus_zaehlen_nur_aktive(self):
        self.cola_ost.inventurart_setzen(self.jahr)
        self.cola_ost.deaktivieren()
        self.assertEqual(arten_je_haus(), {self.nord.pk: {self.monat.pk}})


class SeitenDerArten(Grundlage):
    def test_die_liste_zeigt_reihenfolge_artikel_und_haeuser(self):
        antwort = self.client.get(reverse("inventurart_list"))
        self.assertEqual(list(antwort.context["inventurarten"]), [self.monat, self.jahr])
        monat = antwort.context["inventurarten"][0]
        self.assertEqual(monat.anzahl_artikel, 3)
        self.assertEqual(monat.haeuser_mit_artikeln, [("Kino Nord", 2), ("Kino Ost", 1)])
        self.assertContains(antwort, f"?inventurart={self.monat.pk}")

    def test_die_pfeile_verschieben(self):
        self.client.post(
            reverse("inventurart_verschieben", args=[self.jahr.pk]), {"richtung": "vor"}
        )
        self.assertEqual(list(Inventurart.objects.all()), [self.jahr, self.monat])

    def test_die_pfeile_tragen_eine_marke_fuer_den_fokus(self):
        """Name and value repeat in every row. The script finds the pressed button by its mark."""
        html = self.client.get(reverse("inventurart_list")).content.decode()
        for richtung in ("vor", "zurueck"):
            with self.subTest(richtung=richtung):
                self.assertIn(f'data-fokus="art-{self.jahr.pk}-{richtung}"', html)

    def test_anlegen_und_umbenennen(self):
        self.client.post(reverse("inventurart_add"), {"name": "Becherinventur"})
        becher = Inventurart.objects.get(name="Becherinventur")
        self.assertEqual(Inventurart.objects.last(), becher)
        self.client.post(reverse("inventurart_edit", args=[self.monat.pk]), {"name": "Monat"})
        self.monat.refresh_from_db()
        self.assertEqual(self.monat.name, "Monat")

    def test_loeschen_fragt_wohin_die_artikel_kommen(self):
        adresse = reverse("inventurart_delete", args=[self.monat.pk])
        self.assertContains(self.client.get(adresse), 'name="nachfolger"')
        antwort = self.client.post(adresse, {"nachfolger": self.jahr.pk}, follow=True)
        self.assertContains(antwort, "3 Artikel zählen jetzt in „Jahresinventur“")
        self.assertEqual(self.art_von(self.cola_nord), self.jahr)
        self.assertEqual(list(Inventurart.objects.all()), [self.jahr])

    def test_die_letzte_bleibt(self):
        Hausartikel.objects.update(inventurart=self.monat)
        self.jahr.delete()
        adresse = reverse("inventurart_delete", args=[self.monat.pk])
        self.assertContains(self.client.get(adresse), "Das ist die letzte Inventurart")
        self.client.post(adresse, {})
        self.assertTrue(Inventurart.objects.filter(pk=self.monat.pk).exists())

    def test_eine_art_mit_inventur_bleibt(self):
        inventur_anlegen(self.nord, self.jahr)
        antwort = self.client.post(
            reverse("inventurart_delete", args=[self.jahr.pk]),
            {"nachfolger": self.monat.pk},
            follow=True,
        )
        self.assertContains(antwort, "Es gibt Inventuren dieser Art")
        self.assertTrue(Inventurart.objects.filter(pk=self.jahr.pk).exists())


class KnoepfeInDenListen(Grundlage):
    def test_die_ware_fuer_alle_ihre_artikel(self):
        self.client.post(
            reverse("ware_inventurart", args=[self.cola.pk]), {"inventurart": self.jahr.pk}
        )
        self.assertEqual(
            (self.art_von(self.cola_nord), self.art_von(self.cola_ost)), (self.jahr,) * 2
        )

    def test_der_hausartikel_fragt_nach_der_geltung(self):
        adresse = reverse("hausartikel_inventurart", args=[self.cola_nord.pk])
        antwort = self.client.post(adresse, {"inventurart": self.jahr.pk}, follow=True)
        self.assertContains(antwort, "Gilt die neue Inventurart nur für diesen Artikel")
        self.assertEqual(self.art_von(self.cola_nord), self.monat)

        self.client.post(adresse, {"inventurart": self.jahr.pk, "geltung": "artikel"})
        self.assertEqual(
            (self.art_von(self.cola_nord), self.art_von(self.cola_ost)), (self.jahr, self.monat)
        )

        self.client.post(adresse, {"inventurart": self.monat.pk, "geltung": "ware"})
        self.client.post(
            reverse("hausartikel_inventurart", args=[self.cola_ost.pk]),
            {"inventurart": self.jahr.pk, "geltung": "ware"},
        )
        self.assertEqual(
            (self.art_von(self.cola_nord), self.art_von(self.cola_ost)), (self.jahr,) * 2
        )

    def test_ohne_weitere_artikel_keine_frage(self):
        self.client.post(
            reverse("hausartikel_inventurart", args=[self.d_nord.pk]), {"inventurart": self.jahr.pk}
        )
        self.assertEqual(self.art_von(self.d_nord), self.jahr)

    def test_die_warengruppe(self):
        self.client.post(
            reverse("warengruppe_inventurart", args=[self.gruppe.pk]), {"inventurart": self.jahr.pk}
        )
        self.assertEqual(self.art_von(self.d_nord), self.jahr)
        self.assertEqual(self.art_von(self.cola_ost), self.monat)

    def test_das_menue_des_hausartikels_bietet_beides(self):
        antwort = self.client.get(reverse("hausartikel_list", args=[self.nord.pk]))
        self.assertContains(antwort, 'name="geltung" value="ware"')
        self.assertContains(antwort, "Mit dem anderen Artikel der Ware")

    def test_eine_gemischte_ware_steht_als_gemischt_da(self):
        self.cola_ost.inventurart_setzen(self.jahr)
        antwort = self.client.get(reverse("ware_list"), {"q": "Artikel C"})
        self.assertContains(antwort, "gemischt")

    @staticmethod
    def knopf_der_art(antwort) -> str:
        """The text on the first type menu of the page: on a product's page its own."""
        treffer = re.search(r'id="art-[^"]*"[^>]*>\s*([^<]*?)\s*</button>', antwort.text)
        assert treffer is not None
        return treffer.group(1)

    def test_ohne_sichtbaren_artikel_ist_eine_ware_nicht_gemischt(self):
        """In "Not countable" no article is in sight: there is no type, and "mixed" is wrong.

        Until 10/2026 the menu said "mixed", and the filter "Mixed" did not find
        the product.
        """
        self.cola_ost.inventurart_setzen(self.jahr)
        self.assertEqual(
            self.knopf_der_art(self.client.get(self.cola.get_absolute_url())), "gemischt"
        )
        self.ware_d.ignorieren()
        self.assertEqual(self.knopf_der_art(self.client.get(self.ware_d.get_absolute_url())), "–")
        liste = self.client.get(reverse("ware_list"), {"familie": self.ware_d.warenfamilie_id})
        self.assertEqual(self.knopf_der_art(liste), "–")
        leer = Warengruppe.objects.create(name="Warengruppe L")
        self.assertEqual(self.knopf_der_art(self.client.get(leer.get_absolute_url())), "–")


class FilterUndSortierung(Grundlage):
    def namen(self, antwort, liste="ware") -> list[str]:
        return [satz.name for satz in antwort.context[liste]]

    def test_die_warenliste_nach_art(self):
        self.d_nord.inventurart_setzen(self.jahr)
        antwort = self.client.get(reverse("ware_list"), {"inventurart": self.jahr.pk})
        self.assertEqual(self.namen(antwort), ["Ware D"])

    def test_eine_gemischte_ware_steht_unter_jeder_ihrer_arten(self):
        self.cola_ost.inventurart_setzen(self.jahr)
        for art in (self.monat, self.jahr):
            with self.subTest(art=art.name):
                antwort = self.client.get(reverse("ware_list"), {"inventurart": art.pk})
                self.assertIn("Artikel C 0,33", self.namen(antwort))
        antwort = self.client.get(reverse("ware_list"), {"inventurart": "gemischt"})
        self.assertEqual(self.namen(antwort), ["Artikel C 0,33"])

    def test_die_artikelliste_des_hauses_nach_art(self):
        self.d_nord.inventurart_setzen(self.jahr)
        adresse = reverse("hausartikel_list", args=[self.nord.pk])
        antwort = self.client.get(adresse, {"inventurart": self.jahr.pk})
        self.assertEqual(self.namen(antwort, "hausartikel"), ["Ware D"])

    def test_sortiert_nach_der_reihenfolge_der_arten(self):
        self.cola.inventurart_setzen(self.jahr)
        antwort = self.client.get(reverse("ware_list"), {"sortierung": "art"})
        self.assertEqual(self.namen(antwort), ["Ware D", "Artikel C 0,33"])
        antwort = self.client.get(reverse("ware_list"), {"sortierung": "-art"})
        self.assertEqual(self.namen(antwort), ["Artikel C 0,33", "Ware D"])


class Formulare(Grundlage):
    def daten(self, artikel: Hausartikel, **abweichend) -> dict:
        return {
            "quelle": artikel.quelle,
            "artikelnummer": artikel.artikelnummer,
            "name": artikel.name,
            "ware": artikel.ware_id,
            "neuer_ware": "",
            "basiseinheit": "",
            "warengruppe": artikel.warengruppe_id,
            "neue_warengruppe": "",
            "ean": "",
            "inventurart": artikel.inventurart_id,
        } | abweichend

    def test_eine_neue_art_fragt_nach_der_geltung(self):
        form = HausartikelForm(
            self.daten(self.cola_nord, inventurart=self.jahr.pk), instance=self.cola_nord
        )
        self.assertFalse(form.is_valid())
        self.assertIn("geltung", form.errors)

    def test_nur_fuer_diesen_artikel(self):
        form = HausartikelForm(
            self.daten(self.cola_nord, inventurart=self.jahr.pk, geltung="artikel"),
            instance=self.cola_nord,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assertEqual(
            (self.art_von(self.cola_nord), self.art_von(self.cola_ost)), (self.jahr, self.monat)
        )

    def test_fuer_die_ganze_ware(self):
        form = HausartikelForm(
            self.daten(self.cola_nord, inventurart=self.jahr.pk, geltung="ware"),
            instance=self.cola_nord,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assertEqual(self.art_von(self.cola_ost), self.jahr)

    def test_unveraendert_fragt_niemand(self):
        form = HausartikelForm(self.daten(self.cola_nord), instance=self.cola_nord)
        self.assertTrue(form.is_valid(), form.errors)

    def test_die_frage_nennt_die_anderen_haeuser(self):
        form = HausartikelForm(instance=self.cola_nord)
        self.assertIn("Kino Ost", str(form["geltung"]))

    def test_beim_anlegen_gilt_ohne_wahl_die_art_der_ware(self):
        self.cola.inventurart_setzen(self.jahr)
        sued = Haus.objects.create(name="Kino Süd")
        daten = self.daten(self.cola_nord, inventurart="", artikelnummer="301001")
        form = HausartikelForm(daten, haus=sued)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().inventurart, self.jahr)

    def test_das_warenformular_setzt_alle_artikel(self):
        antwort = self.client.post(
            reverse("ware_edit", args=[self.cola.pk]),
            {
                "name": self.cola.name,
                "basiseinheit": self.stueck.pk,
                "inventurart": self.jahr.pk,
                "gebinde-TOTAL_FORMS": "0",
                "gebinde-INITIAL_FORMS": "0",
            },
        )
        self.assertEqual(antwort.status_code, 302, antwort.content.decode()[:2000])
        self.assertEqual(
            (self.art_von(self.cola_nord), self.art_von(self.cola_ost)), (self.jahr,) * 2
        )

    def test_bei_gemischter_ware_bleibt_ohne_wahl_alles(self):
        self.cola_ost.inventurart_setzen(self.jahr)
        seite = self.client.get(reverse("ware_edit", args=[self.cola.pk]))
        self.assertContains(seite, "Die Artikel stehen in verschiedenen Arten")
        self.client.post(
            reverse("ware_edit", args=[self.cola.pk]),
            {
                "name": self.cola.name,
                "basiseinheit": self.stueck.pk,
                "inventurart": "",
                "gebinde-TOTAL_FORMS": "0",
                "gebinde-INITIAL_FORMS": "0",
            },
        )
        self.assertTrue(self.cola.arten_gemischt)


class Zusammenfuehren(Grundlage):
    def test_die_artikel_der_aufgeloesten_ware_nehmen_die_art_der_bleibenden(self):
        self.ware_d.inventurart_setzen(self.jahr)
        andere = Ware.objects.create(name="Ware D2", basiseinheit=self.stueck)
        d_ost = self.artikel(andere, self.ost, "101002")
        zusammenfuehren(andere, self.ware_d)
        self.assertEqual(self.art_von(d_ost), self.jahr)

    def test_ist_die_bleibende_gemischt_behalten_sie_ihre(self):
        self.cola_ost.inventurart_setzen(self.jahr)
        andere = Ware.objects.create(name="Artikel C2", basiseinheit=self.stueck)
        becher = Inventurart.objects.create(name="Becherinventur")
        c2 = self.artikel(
            andere, Haus.objects.create(name="Kino Süd"), "101005", inventurart=becher
        )
        zusammenfuehren(andere, self.cola)
        self.assertEqual(self.art_von(c2), becher)
