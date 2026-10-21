"""The house's unit on the house article and the conversion to the product.

The product is the lead unit of the report. A house almost always counts
in it, a few carry the same product differently in WinTICKET: pieces
against litres, grams against kilograms. Checked here is what the model
promises about it, the form of the house article and the merging of two
products in different units.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.urls import reverse

from django_project.testing import AngemeldeteSeiten, monatsinventur, ware_zusammenfuehren
from organisation.models import Haus

from .models import (
    Basiseinheit,
    Gebinde,
    Hausartikel,
    Rezepturartikel,
    Rezepturkomponente,
    Ware,
    Warengruppe,
    umrechnung,
)
from .ware_merge import gebindepaare, zusammenfuehren


def einheit(name: str) -> Basiseinheit:
    return Basiseinheit.objects.get(name=name)


class Grundlagen(AngemeldeteSeiten):
    """A product in kg, a house that carries it in pieces: one bag weighs 1 kg."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.haus = Haus.objects.create(name="Kino Ost")
        cls.gruppe = Warengruppe.objects.create(name="Warengruppe G")
        cls.ware = Ware.objects.create(name="Ware K", basiseinheit=einheit("kg"))
        cls.karton = Gebinde.objects.create(
            ware=cls.ware, name="Karton", faktor_in_basiseinheit=Decimal("6")
        )

    def hausartikel(self, **abweichend) -> Hausartikel:
        vorgaben = {
            "haus": self.haus,
            "ware": self.ware,
            "warengruppe": self.gruppe,
            "artikelnummer": "101001",
            "name": "Ware K 1kg",
        }
        return Hausartikel.objects.create(**(vorgaben | abweichend))


class AmModell(Grundlagen):
    def test_ohne_angabe_zaehlt_das_haus_in_der_einheit_der_ware(self):
        """The 'pre_save' receiver fills it in, as with an old backup."""
        artikel = self.hausartikel()
        artikel.refresh_from_db()
        self.assertEqual(artikel.basiseinheit, self.ware.basiseinheit)
        self.assertIsNone(artikel.faktor_zur_ware)
        self.assertFalse(artikel.weicht_ab)
        self.assertEqual(artikel.faktor_zur_ware_wirksam, Decimal("1"))

    def test_innerhalb_einer_dimension_rechnet_die_einheit(self):
        artikel = self.hausartikel(basiseinheit=einheit("g"))
        self.assertTrue(artikel.weicht_ab)
        self.assertFalse(artikel.braucht_faktor)
        self.assertEqual(artikel.faktor_zur_ware_wirksam, Decimal("0.001"))
        self.assertEqual(artikel.in_wareneinheit(Decimal("2500")), Decimal("2.5"))
        self.assertEqual(artikel.in_hauseinheit(Decimal("6")), Decimal("6000"))
        self.assertEqual(artikel.einheit_hinweis, "1 g = 0,001 kg")

    def test_ueber_dimensionen_hinweg_gilt_der_faktor(self):
        artikel = self.hausartikel(basiseinheit=einheit("Stück(e)"), faktor_zur_ware=Decimal("1"))
        self.assertTrue(artikel.braucht_faktor)
        self.assertEqual(artikel.in_hauseinheit(self.karton.faktor_in_basiseinheit), Decimal("6"))
        self.assertEqual(artikel.in_wareneinheit(Decimal("4")), Decimal("4"))

    def test_ohne_faktor_ueber_dimensionen_hinweg_weist_die_pruefung_ab(self):
        artikel = self.hausartikel(basiseinheit=einheit("Stück(e)"))
        with self.assertRaises(ValidationError) as ausnahme:
            artikel.full_clean()
        self.assertIn("faktor_zur_ware", ausnahme.exception.message_dict)
        # And it cannot be computed with either: a silent 1 would be wrong.
        with self.assertRaises(ValueError):
            artikel.faktor_zur_ware_wirksam

    def test_innerhalb_einer_dimension_darf_kein_faktor_stehen(self):
        artikel = self.hausartikel(basiseinheit=einheit("g"), faktor_zur_ware=Decimal("2"))
        with self.assertRaises(ValidationError) as ausnahme:
            artikel.full_clean()
        self.assertIn("faktor_zur_ware", ausnahme.exception.message_dict)

    def test_die_waage_findet_in_die_einheit_des_hauses(self):
        """The product is in kg, the house counts pieces of 1 kg: 2500 g gross are 2.25 pieces."""
        self.ware.abwiegbar = True
        self.ware.save(update_fields=["abwiegbar"])
        artikel = self.hausartikel(basiseinheit=einheit("Stück(e)"), faktor_zur_ware=Decimal("1"))
        from .models import Behaelter  # noqa: PLC0415

        schale = Behaelter.objects.create(name="Schale", tara=Decimal("250"))
        self.assertEqual(artikel.in_basiseinheit(Decimal("2500"), schale), Decimal("2.25"))

    def test_die_modulfunktion_sagt_dasselbe(self):
        self.assertEqual(umrechnung(einheit("g"), einheit("kg")), Decimal("0.001"))
        self.assertEqual(umrechnung(einheit("g"), einheit("kg"), Decimal("7")), Decimal("0.001"))
        self.assertIsNone(umrechnung(einheit("Stück(e)"), einheit("kg")))
        self.assertIsNone(umrechnung(einheit("Stück(e)"), einheit("kg"), Decimal("0")))
        self.assertEqual(umrechnung(einheit("Stück(e)"), einheit("kg"), Decimal("2")), Decimal("2"))


class UeberDasFormular(Grundlagen):
    def daten(self, **abweichend) -> dict:
        return {
            "artikelnummer": "101001",
            "name": "Ware K 1kg",
            "ean": "",
            "ware": self.ware.pk,
            "neuer_ware": "",
            "basiseinheit": "",
            "faktor_zur_ware": "",
            "inventurart": monatsinventur().pk,
            "warengruppe": self.gruppe.pk,
            "neue_warengruppe": "",
            "gebinde-TOTAL_FORMS": "0",
            "gebinde-INITIAL_FORMS": "0",
            "gebinde-MIN_NUM_FORMS": "0",
            "gebinde-MAX_NUM_FORMS": "1000",
            "orte-TOTAL_FORMS": "0",
            "orte-INITIAL_FORMS": "0",
            "orte-MIN_NUM_FORMS": "0",
            "orte-MAX_NUM_FORMS": "1000",
        } | abweichend

    def anlegen(self, **abweichend):
        return self.client.post(
            reverse("hausartikel_add", args=[self.haus.pk]), self.daten(**abweichend)
        )

    def test_leer_heisst_wie_die_ware(self):
        antwort = self.anlegen()
        self.assertEqual(antwort.status_code, 302)
        artikel = Hausartikel.objects.get(artikelnummer="101001")
        self.assertEqual(artikel.basiseinheit.name, "kg")
        self.assertIsNone(artikel.faktor_zur_ware)

    def test_andere_dimension_braucht_den_faktor(self):
        antwort = self.anlegen(basiseinheit=einheit("Stück(e)").pk)
        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, "weiß nur, wer die Ware kennt")

        antwort = self.anlegen(basiseinheit=einheit("Stück(e)").pk, faktor_zur_ware="1,5")
        self.assertEqual(antwort.status_code, 302)
        artikel = Hausartikel.objects.get(artikelnummer="101001")
        self.assertEqual(artikel.basiseinheit.name, "Stück(e)")
        self.assertEqual(artikel.faktor_zur_ware, Decimal("1.5"))

    def test_ein_faktor_mit_zwoelf_stellen_bleibt_wie_er_ist(self):
        """The house counts in ml, the product in pieces of 750 ml: 0.001333333333 pieces per ml.

        With six places the field refused it, and 0.001333 made 750 ml into
        0.99975 pieces. Editing shows the factor in a text field: a number
        field drops a value written with a comma.
        """
        flaschen = Ware.objects.create(name="Ware F", basiseinheit=einheit("Stück(e)"))
        antwort = self.anlegen(
            ware=flaschen.pk,
            name="Ware F",
            basiseinheit=einheit("Milliliter").pk,
            faktor_zur_ware="0,001333333333",
        )
        self.assertEqual(antwort.status_code, 302)
        artikel = Hausartikel.objects.get(artikelnummer="101001")
        self.assertEqual(artikel.faktor_zur_ware, Decimal("0.001333333333"))
        self.assertEqual(
            artikel.in_wareneinheit(Decimal("750")).quantize(Decimal("0.001")), Decimal("1")
        )
        self.assertEqual(artikel.einheit_hinweis, "1 ml = 0,001333333333 Stk.")
        antwort = self.client.get(reverse("hausartikel_edit", args=[artikel.pk]))
        self.assertContains(
            antwort, '<input type="text" name="faktor_zur_ware" value="0,001333333333"'
        )
        self.assertContains(antwort, 'inputmode="decimal"')

    def test_null_ist_keine_umrechnung(self):
        antwort = self.anlegen(basiseinheit=einheit("Stück(e)").pk, faktor_zur_ware="0")
        self.assertContains(antwort, "Eine Umrechnung muss größer als null sein.")
        self.assertNotContains(antwort, "1E-12")

    def test_innerhalb_der_dimension_faellt_der_faktor_still_weg(self):
        """Without JavaScript the field stays visible. A remainder in it means nothing."""
        antwort = self.anlegen(basiseinheit=einheit("g").pk, faktor_zur_ware="3")
        self.assertEqual(antwort.status_code, 302)
        artikel = Hausartikel.objects.get(artikelnummer="101001")
        self.assertEqual(artikel.basiseinheit.name, "g")
        self.assertIsNone(artikel.faktor_zur_ware)

    def test_eine_neue_ware_entsteht_in_der_einheit_des_hauses(self):
        antwort = self.anlegen(ware="", neuer_ware="Ware L", basiseinheit=einheit("Liter").pk)
        self.assertEqual(antwort.status_code, 302)
        artikel = Hausartikel.objects.get(artikelnummer="101001")
        self.assertEqual(artikel.ware.name, "Ware L")
        self.assertEqual(artikel.ware.basiseinheit.name, "Liter")
        self.assertEqual(artikel.basiseinheit.name, "Liter")

    def test_beim_wechsel_der_ware_zaehlt_die_neue(self):
        """Editing: from the kg product to a piece product, the house stays with pieces."""
        artikel = self.hausartikel(basiseinheit=einheit("Stück(e)"), faktor_zur_ware=Decimal("1"))
        stueckware = Ware.objects.create(name="Ware S", basiseinheit=einheit("Stück(e)"))
        antwort = self.client.post(
            reverse("hausartikel_edit", args=[artikel.pk]),
            self.daten(ware=stueckware.pk, basiseinheit=einheit("Stück(e)").pk),
        )
        self.assertEqual(antwort.status_code, 302)
        artikel.refresh_from_db()
        self.assertEqual(artikel.ware, stueckware)
        self.assertIsNone(artikel.faktor_zur_ware)

    def test_die_seite_bringt_das_skript_und_die_dimensionen_mit(self):
        antwort = self.client.get(reverse("hausartikel_add", args=[self.haus.pk]))
        self.assertContains(antwort, "js/house_unit.js")
        self.assertContains(antwort, 'data-dimension="MASSE" data-einheit="kg"')
        self.assertContains(antwort, 'name="faktor_zur_ware"')
        # The label says in which direction the factor applies: 1000 for a
        # pack of 1 kg of a product carried in g, not 0.001.
        self.assertContains(antwort, 'data-wert="1 {haus} = wie viele {ware}?"')
        self.assertContains(antwort, "Dann steht hier 1000.")

    def test_die_detailseite_nennt_die_abweichung(self):
        artikel = self.hausartikel(basiseinheit=einheit("Stück(e)"), faktor_zur_ware=Decimal("1"))
        antwort = self.client.get(artikel.get_absolute_url())
        self.assertContains(antwort, "Die Ware wird in kg geführt.")
        self.assertContains(antwort, "1 Stk. = 1 kg")


class ZusammenfuehrenUeberEinheiten(Grundlagen):
    """The milk: one house carries it in pieces, the other in litres, each as its own product."""

    def setUp(self):
        super().setUp()
        self.stueckware = Ware.objects.create(name="Ware M 1l", basiseinheit=einheit("Stück(e)"))
        self.kiste = Gebinde.objects.create(
            ware=self.stueckware, name="Kiste", faktor_in_basiseinheit=Decimal("12")
        )
        self.literware = Ware.objects.create(name="Ware M", basiseinheit=einheit("Liter"))
        self.karton_l = Gebinde.objects.create(
            ware=self.literware, name="Karton", faktor_in_basiseinheit=Decimal("1")
        )
        self.kino_nord = self.hausartikel(ware=self.stueckware, artikelnummer="101450")
        self.kino_ost = Hausartikel.objects.create(
            haus=Haus.objects.create(name="Kino West"),
            ware=self.literware,
            warengruppe=self.gruppe,
            artikelnummer="101450",
            name="Ware M",
        )

    def einzelstueck(self) -> Gebinde:
        return Gebinde.objects.create(
            ware=self.stueckware,
            name="Einzeln",
            faktor_in_basiseinheit=Decimal("1"),
            ist_einzeln=True,
        )

    def test_das_einzelstueck_der_quelle_wird_im_ziel_ein_gebinde(self):
        """One piece is half a litre. Litres know no single unit, so it moves as a pack."""
        einzeln = self.einzelstueck()

        zusammenfuehren(self.stueckware, self.literware, faktor=Decimal("0.5"))

        einzeln.refresh_from_db()
        self.assertEqual(einzeln.ware, self.literware)
        self.assertEqual(
            (einzeln.faktor_in_basiseinheit, einzeln.ist_einzeln), (Decimal("0.5"), False)
        )
        self.assertEqual(einzeln.name, "Einzeln")

    def test_auch_mit_faktor_eins_bleibt_kein_einzelstueck_in_litern(self):
        """1 piece = 1 litre: the quantities stay, the single unit still has to go."""
        einzeln = self.einzelstueck()
        # The target's own pack of one litre: the two merge, so the mark would survive on it.
        self.karton_l.delete()

        zusammenfuehren(self.stueckware, self.literware, faktor=Decimal("1"))

        einzeln.refresh_from_db()
        self.assertEqual((einzeln.ware, einzeln.ist_einzeln), (self.literware, False))
        einzeln.full_clean()

    def test_die_vorschau_sagt_dass_das_einzelstueck_ein_gebinde_wird(self):
        self.einzelstueck()
        antwort = self.client.post(
            reverse("ware_merge", args=[self.literware.pk]),
            {"quelle": self.stueckware.pk, "umrechnung": "0,5"},
        )
        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, "wird ein gewöhnliches Gebinde")

    def test_die_paare_rechnen_in_der_einheit_des_ziels(self):
        paare = gebindepaare(self.stueckware, self.literware, Decimal("1"))
        self.assertEqual([(p.quelle.name, p.menge) for p in paare], [("Kiste", Decimal("12.000"))])

    def test_die_haeuser_zaehlen_weiter_in_ihrer_einheit(self):
        portion = Rezepturartikel.objects.create(name="Portion M", basiseinheit=einheit("Stück(e)"))
        rezeptur = Rezepturkomponente.objects.create(
            rezepturartikel=portion, ware=self.stueckware, menge_in_basiseinheit=Decimal("2")
        )
        bericht = zusammenfuehren(self.stueckware, self.literware, faktor=Decimal("1"))
        self.assertEqual(bericht.hausartikel, 1)

        self.kino_nord.refresh_from_db()
        self.assertEqual(self.kino_nord.ware, self.literware)
        self.assertEqual(self.kino_nord.basiseinheit.name, "Stück(e)")
        self.assertEqual(self.kino_nord.faktor_zur_ware, Decimal("1"))
        self.kino_ost.refresh_from_db()
        self.assertIsNone(self.kino_ost.faktor_zur_ware)
        self.kiste.refresh_from_db()
        self.assertEqual(self.kiste.ware, self.literware)
        self.assertEqual(self.kiste.faktor_in_basiseinheit, Decimal("12"))
        rezeptur.refresh_from_db()
        self.assertEqual(rezeptur.ware, self.literware)
        self.assertEqual(rezeptur.menge_in_basiseinheit, Decimal("2"))

    def test_ein_haus_mit_faktor_bekommt_ihn_neu_gerechnet(self):
        """The house counts in kg, two pieces per kg. One piece is one litre: 1 kg = 2 l."""
        Hausartikel.objects.filter(pk=self.kino_nord.pk).update(
            basiseinheit=einheit("kg"), faktor_zur_ware=Decimal("2")
        )
        zusammenfuehren(self.stueckware, self.literware, faktor=Decimal("1"))
        self.kino_nord.refresh_from_db()
        self.assertEqual(self.kino_nord.faktor_zur_ware, Decimal("2"))

    def test_die_seite_fragt_ueber_dimensionen_hinweg_nach(self):
        adresse = reverse("ware_merge", args=[self.literware.pk])
        antwort = self.client.get(adresse)
        self.assertContains(antwort, "js/ware_merge.js")
        self.assertContains(antwort, 'name="umrechnung"')

        antwort = self.client.post(adresse, {"quelle": self.stueckware.pk})
        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, "wechselt die Dimension")
        self.assertTrue(Ware.objects.filter(pk=self.stueckware.pk).exists())

        antwort = ware_zusammenfuehren(
            self.client, adresse, {"quelle": self.stueckware.pk, "umrechnung": "1"}
        )
        self.assertRedirects(antwort, self.literware.get_absolute_url())
        self.assertFalse(Ware.objects.filter(pk=self.stueckware.pk).exists())
        self.kino_nord.refresh_from_db()
        self.assertEqual(self.kino_nord.faktor_zur_ware, Decimal("1"))

    def test_innerhalb_einer_dimension_rechnet_die_seite_selbst(self):
        """A box of the g product at 6000 is the same as the box of the kg product at 6."""
        grammware = Ware.objects.create(name="Ware K roh", basiseinheit=einheit("g"))
        Gebinde.objects.create(
            ware=grammware, name="Karton", faktor_in_basiseinheit=Decimal("6000")
        )
        artikel = Hausartikel.objects.create(
            haus=Haus.objects.create(name="Kino Süd"),
            ware=grammware,
            warengruppe=self.gruppe,
            artikelnummer="101390",
            name="Ware K roh",
        )
        paare = gebindepaare(grammware, self.ware, Decimal("0.001"))
        self.assertEqual(paare[0].art, "gleich")
        antwort = ware_zusammenfuehren(
            self.client, reverse("ware_merge", args=[self.ware.pk]), {"quelle": grammware.pk}
        )
        self.assertRedirects(antwort, self.ware.get_absolute_url())
        artikel.refresh_from_db()
        self.assertEqual(artikel.ware, self.ware)
        self.assertEqual(artikel.basiseinheit.name, "g")
        self.assertIsNone(artikel.faktor_zur_ware)
        self.assertEqual(self.ware.gebinde.count(), 1)
