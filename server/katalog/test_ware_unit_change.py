"""Change the base unit of a product. What is in it is converted.

Until 09/2026 the form only switched the field: the crate held "20 kg"
all of a sudden instead of "20 pieces". Checked here is that every
quantity keeps its meaning, that across dimensions nothing happens
without a factor, and that a change that cannot be computed cleanly
changes nothing at all.

The houses' rows are in the unit of the **house** and stay as they are.
Instead every house gets its conversion to the product.
"""

from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from django_project.testing import AngemeldeteSeiten, inventur_anlegen, ware_zusammenfuehren
from inventur.models import (
    Inventur,
    Inventurergebnis,
    Sollbestand,
    Sollgrund,
    Sollzeile,
    Wiegung,
    Zaehlposition,
)
from organisation.models import Bereich, Haus, Ort

from .models import (
    Basiseinheit,
    Behaelter,
    Dimension,
    Gebinde,
    Hausartikel,
    Rezepturartikel,
    Rezepturkomponente,
    Ware,
    Warenfamilie,
    Warengruppe,
    eingabe_zu_faktor,
)
from .ware_unit_change import einheit_wechseln, umrechnungsfaktor


def einheit(name: str) -> Basiseinheit:
    return Basiseinheit.objects.get(name=name)


class Einheitenwechsel(AngemeldeteSeiten):
    """Artikel N: carried in pieces, in the box of 20, built into a portion, counted once."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.haus = Haus.objects.create(name="Kino Ost")
        cls.ort = Ort.objects.create(
            name="Lager", bereich=Bereich.objects.create(name="Lager", haus=cls.haus)
        )

    def setUp(self):
        super().setUp()
        self.ware = Ware.objects.create(name="Artikel N", basiseinheit=einheit("Stück(e)"))
        self.karton = Gebinde.objects.create(
            ware=self.ware, name="Karton", faktor_in_basiseinheit=Decimal("20")
        )
        self.einzeln = Gebinde.objects.create(
            ware=self.ware, name="Einzeln", faktor_in_basiseinheit=Decimal("1"), ist_einzeln=True
        )
        self.artikel = Hausartikel.objects.create(
            haus=self.haus,
            ware=self.ware,
            warengruppe=Warengruppe.objects.get_or_create(name="Warengruppe S")[0],
            artikelnummer="200001",
            name="Artikel N",
        )
        portion = Rezepturartikel.objects.create(name="Portion N", basiseinheit=einheit("Stück(e)"))
        self.rezeptur = Rezepturkomponente.objects.create(
            rezepturartikel=portion, ware=self.ware, menge_in_basiseinheit=Decimal("2")
        )

    def abgeschlossene_inventur(self) -> Inventur:
        """Two boxes counted, 38 pieces target, frozen as 40 / 38 / 2."""
        inventur = inventur_anlegen(self.haus, stichtag=date(2026, 1, 31))
        inventur.zaehlpositionen.create(
            ort=self.ort, artikel=self.artikel, gebinde=self.karton, eingabemenge=Decimal("2")
        )
        Sollbestand.objects.create(
            inventur=inventur, hausartikel=self.artikel, menge_basiseinheit=Decimal("38")
        )
        inventur.zaehlung_starten()
        inventur.abschliessen()
        return inventur

    def absenden(self, **abweichend):
        daten = {
            "name": self.ware.name,
            "basiseinheit": self.ware.basiseinheit.pk,
            "umrechnung": "",
            "gebinde-TOTAL_FORMS": "0",
            "gebinde-INITIAL_FORMS": "0",
            "gebinde-MIN_NUM_FORMS": "0",
            "gebinde-MAX_NUM_FORMS": "1000",
        }
        return self.client.post(reverse("ware_edit", args=[self.ware.pk]), daten | abweichend)


class DerFaktor(Einheitenwechsel):
    def test_innerhalb_einer_dimension_rechnet_die_einheit_selbst(self):
        faelle = (
            ("g", "kg", "0.001"),
            ("kg", "g", "1000"),
            ("Liter", "Milliliter", "1000"),
            ("Milliliter", "Liter", "0.001"),
        )
        for alt, neu, erwartet in faelle:
            with self.subTest(von=alt, nach=neu):
                # An entry is skipped, it could only be wrong.
                faktor = umrechnungsfaktor(einheit(alt), einheit(neu), Decimal("7"))
                self.assertEqual(faktor, Decimal(erwartet))

    def test_ueber_dimensionen_hinweg_nur_mit_angabe(self):
        for angabe in (None, Decimal("0"), Decimal("-1")):
            with self.subTest(angabe=angabe), self.assertRaises(ValidationError):
                umrechnungsfaktor(einheit("Stück(e)"), einheit("kg"), angabe)
        self.assertEqual(
            umrechnungsfaktor(einheit("Stück(e)"), einheit("kg"), Decimal("0.125")),
            Decimal("0.125"),
        )


class WasUmgerechnetWird(Einheitenwechsel):
    def test_jede_menge_behaelt_ihre_bedeutung(self):
        """One piece weighs 125 g: the box of 20 becomes one of 2.5 kg.

        The house keeps counting in pieces. Its target and its result stay as
        they are, and the row gets the conversion 1 pc = 0.125 kg.
        """
        inventur = self.abgeschlossene_inventur()
        laufend = inventur_anlegen(self.haus, stichtag=date(2026, 2, 28))
        soll = Sollbestand.objects.create(
            inventur=laufend, hausartikel=self.artikel, menge_basiseinheit=Decimal("16")
        )

        faktor = einheit_wechseln(self.ware, einheit("kg"), Decimal("0.125"))

        self.assertEqual(faktor, Decimal("0.125"))
        self.ware.refresh_from_db()
        self.assertEqual(self.ware.basiseinheit.name, "kg")
        self.karton.refresh_from_db()
        self.einzeln.refresh_from_db()
        self.assertEqual(self.karton.faktor_in_basiseinheit, Decimal("2.5"))
        self.assertEqual(self.einzeln.faktor_in_basiseinheit, Decimal("0.125"))
        self.rezeptur.refresh_from_db()
        self.assertEqual(self.rezeptur.menge_in_basiseinheit, Decimal("0.25"))

        self.artikel.refresh_from_db()
        self.assertEqual(self.artikel.basiseinheit.name, "Stück(e)")
        self.assertEqual(self.artikel.faktor_zur_ware, Decimal("0.125"))
        soll.refresh_from_db()
        self.assertEqual(soll.menge_basiseinheit, Decimal("16"))
        ergebnis = inventur.ergebnisse.get()
        self.assertEqual(ergebnis.gesamtmenge_basiseinheit, Decimal("40"))
        self.assertEqual(ergebnis.sollbestand, Decimal("38"))
        self.assertEqual(ergebnis.differenz, Decimal("2"))

    def test_das_einzelstueck_wird_beim_verlassen_von_stueck_ein_gebinde(self):
        """The database allows the single unit only as exactly one. In kg it is 0.125.

        Until 10/2026 that ran into the check constraint, and the change of a
        piece good into kg or litres ended on the error page.
        """
        einheit_wechseln(self.ware, einheit("kg"), Decimal("0.125"))

        self.einzeln.refresh_from_db()
        self.assertFalse(self.einzeln.ist_einzeln)
        self.assertEqual(self.einzeln.faktor_in_basiseinheit, Decimal("0.125"))
        self.assertEqual(self.einzeln.name, "Einzeln", "keeps its name as an ordinary pack")
        self.einzeln.full_clean()

    def test_zurueck_in_stueck_wird_eines_wieder_das_einzelstueck(self):
        einheit_wechseln(self.ware, einheit("kg"), Decimal("0.125"))

        einheit_wechseln(self.ware, einheit("Stück(e)"), Decimal("8"))

        self.einzeln.refresh_from_db()
        self.karton.refresh_from_db()
        self.assertTrue(self.einzeln.ist_einzeln)
        self.assertEqual(self.einzeln.faktor_in_basiseinheit, Decimal("1"))
        self.assertFalse(self.karton.ist_einzeln)
        self.assertEqual(self.karton.faktor_in_basiseinheit, Decimal("20"))

    def test_ein_anderes_gebinde_mit_eins_wird_nicht_zum_einzelstueck(self):
        """A bag that happens to hold one piece keeps being a bag. Only the name says single."""
        ware = Ware.objects.create(name="Ware P roh", basiseinheit=einheit("kg"))
        tuete = Gebinde.objects.create(
            ware=ware, name="Tüte", faktor_in_basiseinheit=Decimal("0.5")
        )

        einheit_wechseln(ware, einheit("Stück(e)"), Decimal("2"))

        tuete.refresh_from_db()
        self.assertEqual((tuete.faktor_in_basiseinheit, tuete.ist_einzeln), (Decimal("1"), False))

    def test_ein_haus_mit_eigenem_faktor_bekommt_ihn_neu_gerechnet(self):
        """The house counts in litres, one litre was 4 pieces. One piece weighs 125 g: 0.5 kg."""
        self.artikel.basiseinheit = einheit("Liter")
        self.artikel.faktor_zur_ware = Decimal("4")
        self.artikel.save()
        einheit_wechseln(self.ware, einheit("kg"), Decimal("0.125"))
        self.artikel.refresh_from_db()
        self.assertEqual(self.artikel.basiseinheit.name, "Liter")
        self.assertEqual(self.artikel.faktor_zur_ware, Decimal("0.5"))

    def test_ein_haus_in_der_neuen_dimension_braucht_keinen_faktor_mehr(self):
        """The house counts in g, the product went from pieces to kg: g converts to kg by itself."""
        self.artikel.basiseinheit = einheit("g")
        self.artikel.faktor_zur_ware = Decimal("0.008")
        self.artikel.save()
        einheit_wechseln(self.ware, einheit("kg"), Decimal("0.125"))
        self.artikel.refresh_from_db()
        self.assertIsNone(self.artikel.faktor_zur_ware)
        self.assertEqual(self.artikel.faktor_zur_ware_wirksam, Decimal("0.001"))

    def test_offene_sollzeilen_bleiben_in_der_einheit_des_hauses(self):
        inventur = inventur_anlegen(self.haus, stichtag=date(2026, 2, 28))
        zeile = Sollzeile.objects.create(
            inventur=inventur,
            artikelnummer="200001",
            menge_basiseinheit=Decimal("8"),
            grund=Sollgrund.INAKTIV,
            hausartikel=self.artikel,
        )
        einheit_wechseln(self.ware, einheit("kg"), Decimal("0.125"))
        zeile.refresh_from_db()
        self.assertEqual(zeile.menge_basiseinheit, Decimal("8"))

    def test_gezaehlte_gebinde_bleiben_anzahlen(self):
        """Two boxes stay two boxes and for the house 40 pieces.

        The pack computes in kg, the house's conversion back again.
        """
        inventur = inventur_anlegen(self.haus, stichtag=date(2026, 2, 28))
        position = inventur.zaehlpositionen.create(
            ort=self.ort, artikel=self.artikel, gebinde=self.karton, eingabemenge=Decimal("2")
        )
        einheit_wechseln(self.ware, einheit("kg"), Decimal("0.125"))
        position.refresh_from_db()
        self.assertEqual(position.eingabemenge, Decimal("2"))
        self.assertEqual(position.menge_in_basiseinheit(), Decimal("40"))

    def test_gewogenes_findet_ueber_den_faktor_in_die_neue_einheit(self):
        """From kg into pieces at 8 per kilo: one piece weighs 125 g, 1250 g gross are 10 pieces."""
        becher = Ware.objects.create(name="Becher", basiseinheit=einheit("kg"), abwiegbar=True)
        einheit_wechseln(becher, einheit("Stück(e)"), Decimal("8"))
        becher.refresh_from_db()
        self.assertEqual(becher.gramm_je_einheit, Decimal("125"))
        self.assertEqual(becher.in_basiseinheit(Decimal("1250")), Decimal("10"))

        # And back into a mass unit: the unit weighs itself, and pieces are
        # the foreign dimension now, 0.008 of them per gram.
        einheit_wechseln(becher, einheit("g"), Decimal("125"))
        becher.refresh_from_db()
        self.assertEqual(becher.gramm_je_einheit, Decimal("1"))
        self.assertEqual(becher.faktoren(), {Dimension.STUECK: Decimal("0.008")})

    def test_alle_umrechnungen_wandern_mit(self):
        """A bottle of 500 ml and 520 g, carried in litres from now on: 1 l is 2 bottles."""
        flasche = Ware.objects.create(name="Flasche 0,5", basiseinheit=einheit("Stück(e)"))
        flasche.umrechnungen_setzen(
            {Dimension.VOLUMEN: Decimal("500"), Dimension.MASSE: Decimal("520")}
        )
        einheit_wechseln(flasche, einheit("Liter"), Decimal("0.5"))
        flasche.refresh_from_db()
        self.assertEqual(
            flasche.faktoren(), {Dimension.MASSE: Decimal("1040"), Dimension.STUECK: Decimal("2")}
        )
        # Within the dimension only the scale changes: 1.04 g per millilitre.
        einheit_wechseln(flasche, einheit("Milliliter"))
        flasche.refresh_from_db()
        self.assertEqual(
            flasche.faktoren(),
            {Dimension.MASSE: Decimal("1.04"), Dimension.STUECK: Decimal("0.002")},
        )

    def test_die_familie_summiert_nach_dem_wechsel_weiter(self):
        """The old dimension is foreign now, and its factor is known from the change."""
        familie = Warenfamilie.objects.create(
            name="Warenfamilie K", leiteinheit=einheit("Stück(e)")
        )
        familie.aufnehmen(self.ware)
        einheit_wechseln(self.ware, einheit("kg"), Decimal("0.125"))
        self.ware.refresh_from_db()
        self.assertEqual(self.ware.basiseinheit.name, "kg")
        self.assertEqual(self.ware.faktoren(), {Dimension.STUECK: Decimal("8")})
        self.assertEqual(familie.waren_ohne_faktor(), [])

    def test_jeder_bekannte_weg_bleibt_bekannt(self):
        """A litre product knowing its pieces per litre keeps knowing them per kilogram."""
        familie = Warenfamilie.objects.create(
            name="Warenfamilie K", leiteinheit=einheit("Stück(e)")
        )
        saft = Ware.objects.create(name="Posten S", basiseinheit=einheit("Liter"))
        saft.umrechnungen_setzen({Dimension.STUECK: Decimal("4")})
        familie.aufnehmen(saft)
        einheit_wechseln(saft, einheit("kg"), Decimal("1.25"))
        saft.refresh_from_db()
        self.assertEqual(
            saft.faktoren(), {Dimension.STUECK: Decimal("3.2"), Dimension.VOLUMEN: Decimal("800")}
        )
        self.assertEqual(familie.waren_ohne_faktor(), [])


class WasSichNichtRechnenLaesst(Einheitenwechsel):
    def test_ein_gebinde_das_zu_klein_wuerde_haelt_alles_auf(self):
        """A piece of 0.2 g would be 0.0002 in kg. The column holds three places."""
        with self.assertRaises(ValidationError) as ausnahme:
            einheit_wechseln(self.ware, einheit("kg"), Decimal("0.0002"))
        self.assertIn("Einzeln", " ".join(ausnahme.exception.messages))
        self.ware.refresh_from_db()
        self.karton.refresh_from_db()
        self.assertEqual(self.ware.basiseinheit.name, "Stück(e)")
        self.assertEqual(self.karton.faktor_in_basiseinheit, Decimal("20"))

    def test_zwei_gebinde_die_zusammenfielen_halten_alles_auf(self):
        Gebinde.objects.create(
            ware=self.ware, name="Doppelpack", faktor_in_basiseinheit=Decimal("2")
        )
        # The single unit is always exactly one. For this case it becomes a pack of 1.6.
        Gebinde.objects.filter(pk=self.einzeln.pk).update(
            faktor_in_basiseinheit=Decimal("1.6"), ist_einzeln=False
        )
        # 1.6 and 2 pieces of 0.0008 kg each: both rounded 0.001 and 0.002. That works.
        # Of 0.0006 kg each, 0.00096 and 0.0012 both become 0.001.
        with self.assertRaises(ValidationError) as ausnahme:
            einheit_wechseln(self.ware, einheit("kg"), Decimal("0.0006"))
        self.assertIn("nicht mehr unterscheiden", " ".join(ausnahme.exception.messages))

    def test_ein_gebinde_das_die_spalte_sprengte_haelt_alles_auf(self):
        """A sack of 20,000 kg would be 20,000,000 g, and the column holds seven places.

        Until 10/2026 PostgreSQL refused it with a numeric overflow, an error
        page, and SQLite stored a number it could not read back.
        """
        ware = Ware.objects.create(name="Artikel Z", basiseinheit=einheit("kg"))
        sack = Gebinde.objects.create(
            ware=ware, name="Sack", faktor_in_basiseinheit=Decimal("20000")
        )
        with self.assertRaises(ValidationError) as ausnahme:
            einheit_wechseln(ware, einheit("g"))
        self.assertEqual(
            ausnahme.exception.messages,
            ["Gebinde „Sack“ fasste danach 20000000 g. Ein Gebinde fasst höchstens 9999999,999 g."],
        )
        sack.refresh_from_db()
        self.assertEqual(sack.faktor_in_basiseinheit, Decimal("20000"))

    def test_beim_zusammenfuehren_genauso(self):
        """Merged into a product in g, the sack of the one in kg would not fit either."""
        quelle = Ware.objects.create(name="Artikel Z", basiseinheit=einheit("kg"))
        Gebinde.objects.create(ware=quelle, name="Sack", faktor_in_basiseinheit=Decimal("20000"))
        ziel = Ware.objects.create(name="Artikel Y", basiseinheit=einheit("g"))

        antwort = ware_zusammenfuehren(
            self.client, reverse("ware_merge", args=[ziel.pk]), {"quelle": quelle.pk}
        )

        self.assertContains(
            antwort,
            "Gebinde „Sack“ fasste danach 20000000 g. Ein Gebinde fasst höchstens 9999999,999 g.",
        )
        self.assertTrue(Ware.objects.filter(pk=quelle.pk).exists())

    def schwere_ware(self, einheit_der_ware: str = "kg") -> Hausartikel:
        """A container of 1,500 kg, counted in pieces at the house: one piece is 1,500 kg."""
        ware = Ware.objects.create(name="Artikel Z", basiseinheit=einheit(einheit_der_ware))
        return Hausartikel.objects.create(
            haus=self.haus,
            ware=ware,
            warengruppe=Warengruppe.objects.get_or_create(name="Warengruppe S")[0],
            artikelnummer="200009",
            name="Container Z",
            basiseinheit=einheit("Stück(e)"),
            faktor_zur_ware=Decimal("1500"),
        )

    def test_eine_umrechnung_des_hauses_die_die_spalte_sprengte_haelt_alles_auf(self):
        """1,500 kg are 1,500,000 g, and the column holds six places before the point.

        Until 10/2026 PostgreSQL refused it with a numeric overflow, an error
        page, and SQLite stored it.
        """
        container = self.schwere_ware()
        with self.assertRaises(ValidationError) as ausnahme:
            einheit_wechseln(container.ware, einheit("g"))
        self.assertEqual(
            ausnahme.exception.messages,
            [
                "„Container Z“ in Kino Ost: 1 Stk. wäre danach 1500000 g. Eine Umrechnung fasst "
                "höchstens 999999,999999999999."
            ],
        )
        container.refresh_from_db()
        self.assertEqual(container.faktor_zur_ware, Decimal("1500"))
        self.assertEqual(container.ware.basiseinheit.name, "kg")

    def test_ein_ergebnis_in_anderer_einheit_genauso(self):
        """The house counts in g now, a result was frozen in kg: its factor grows a thousandfold.

        The house's 1.5 pieces per g fit, the result's 1,500 pieces per kg
        would not after a change into millilitres of a thousand per piece.
        """
        ware = Ware.objects.create(name="Artikel Z", basiseinheit=einheit("Stück(e)"))
        artikel = Hausartikel.objects.create(
            haus=self.haus,
            ware=ware,
            warengruppe=Warengruppe.objects.get_or_create(name="Warengruppe S")[0],
            artikelnummer="200009",
            name="Ware Z",
            basiseinheit=einheit("g"),
            faktor_zur_ware=Decimal("1.5"),
        )
        inventur = inventur_anlegen(self.haus, stichtag=date(2026, 1, 31))
        Inventurergebnis.objects.create(
            inventur=inventur,
            hausartikel=artikel,
            gesamtmenge_basiseinheit=Decimal("2"),
            eingefroren_am=timezone.now(),
            einheit=einheit("kg"),
            faktor_zur_ware=Decimal("1500"),
        )

        with self.assertRaises(ValidationError) as ausnahme:
            einheit_wechseln(ware, einheit("Milliliter"), Decimal("1000"))

        self.assertEqual(
            ausnahme.exception.messages,
            [
                f"Im Ergebnis von „Ware Z“ der Inventur {inventur} wäre 1 kg danach 1500000 ml. "
                "Eine Umrechnung fasst höchstens 999999,999999999999."
            ],
        )
        self.assertEqual(
            inventur.ergebnisse.get().faktor_zur_ware.normalize(), Decimal("1500").normalize()
        )

    def test_eine_rezeptur_die_die_spalte_sprengte_haelt_alles_auf(self):
        """2,000 kg of the product per pallet are 2,000,000 g, the column holds six places."""
        ware = Ware.objects.create(name="Artikel Z", basiseinheit=einheit("kg"))
        palette = Rezepturartikel.objects.create(name="Palette Z", basiseinheit=einheit("Stück(e)"))
        zeile = Rezepturkomponente.objects.create(
            rezepturartikel=palette, ware=ware, menge_in_basiseinheit=Decimal("2000")
        )
        with self.assertRaises(ValidationError) as ausnahme:
            einheit_wechseln(ware, einheit("g"))
        self.assertEqual(
            ausnahme.exception.messages,
            [
                "In der Rezeptur von „Palette Z“ stünden von dieser Ware danach 2000000 g. Eine "
                "Rezeptur fasst höchstens 999999,999999 g."
            ],
        )
        zeile.refresh_from_db()
        self.assertEqual(zeile.menge_in_basiseinheit, Decimal("2000"))

    def test_die_umrechnung_des_hauses_auch_beim_zusammenfuehren(self):
        """Merged into a product in g, the container of the one in kg would not fit either."""
        container = self.schwere_ware()
        ziel = Ware.objects.create(name="Artikel Y", basiseinheit=einheit("g"))

        antwort = ware_zusammenfuehren(
            self.client, reverse("ware_merge", args=[ziel.pk]), {"quelle": container.ware_id}
        )

        self.assertContains(
            antwort,
            "„Container Z“ in Kino Ost: 1 Stk. wäre danach 1500000 g. Eine Umrechnung fasst "
            "höchstens 999999,999999999999.",
        )
        container.refresh_from_db()
        self.assertEqual(container.faktor_zur_ware, Decimal("1500"))
        self.assertEqual(container.ware.name, "Artikel Z")

    def test_auf_der_seite_der_ware_steht_es_am_feld(self):
        container = self.schwere_ware()
        antwort = self.client.post(
            reverse("ware_edit", args=[container.ware_id]),
            {
                "name": container.ware.name,
                "basiseinheit": einheit("g").pk,
                "umrechnung": "",
                "gebinde-TOTAL_FORMS": "0",
                "gebinde-INITIAL_FORMS": "0",
                "gebinde-MIN_NUM_FORMS": "0",
                "gebinde-MAX_NUM_FORMS": "1000",
            },
        )

        self.assertEqual(antwort.status_code, 200)
        self.assertIn(
            "„Container Z“ in Kino Ost: 1 Stk. wäre danach 1500000 g. Eine Umrechnung fasst "
            "höchstens 999999,999999999999.",
            antwort.context["form"].errors["basiseinheit"],
        )
        container.refresh_from_db()
        self.assertEqual(container.ware.basiseinheit.name, "kg")

    def leichte_ware(self) -> Ware:
        """Pieces of 1 mg with 2 ml each, without packs: those would stop the change sooner."""
        ware = Ware.objects.create(name="Artikel Z", basiseinheit=einheit("Stück(e)"))
        ware.umrechnungen_setzen({Dimension.VOLUMEN: Decimal("2")})
        return ware

    def test_eine_umrechnung_der_ware_die_die_spalte_sprengte_haelt_alles_auf(self):
        """In kg the piece of 1 mg makes 1,000,000 pieces and 2,000,000 ml per kg.

        Until 10/2026 PostgreSQL refused it with a numeric overflow, an error
        page, and SQLite stored it.
        """
        ware = self.leichte_ware()
        with self.assertRaises(ValidationError) as ausnahme:
            einheit_wechseln(ware, einheit("kg"), Decimal("0.000001"))
        self.assertEqual(
            ausnahme.exception.messages,
            [
                "Die Umrechnung der Ware wäre danach 2000000 ml je kg. Eine Umrechnung fasst "
                "höchstens 999999,999999999999.",
                "Die Umrechnung der Ware wäre danach 1000000 Stk. je kg. Eine Umrechnung fasst "
                "höchstens 999999,999999999999.",
            ],
        )
        ware.refresh_from_db()
        self.assertEqual(ware.basiseinheit.name, "Stück(e)")
        self.assertEqual(ware.faktoren(), {Dimension.VOLUMEN: Decimal("2")})

    def test_die_umrechnung_der_ware_auf_der_seite_am_feld(self):
        ware = self.leichte_ware()
        antwort = self.client.post(
            reverse("ware_edit", args=[ware.pk]),
            {
                "name": ware.name,
                "basiseinheit": einheit("kg").pk,
                "umrechnung": "0,000001",
                "gebinde-TOTAL_FORMS": "0",
                "gebinde-INITIAL_FORMS": "0",
                "gebinde-MIN_NUM_FORMS": "0",
                "gebinde-MAX_NUM_FORMS": "1000",
            },
        )

        self.assertEqual(antwort.status_code, 200)
        self.assertIn(
            "Die Umrechnung der Ware wäre danach 1000000 Stk. je kg. Eine Umrechnung fasst "
            "höchstens 999999,999999999999.",
            antwort.context["form"].errors["basiseinheit"],
        )
        ware.refresh_from_db()
        self.assertEqual(ware.basiseinheit.name, "Stück(e)")

    def test_umrechnungen_an_beiden_enden_der_spalte_halten_alles_auf(self):
        """A piece of 999999 ml, and the change takes it for 1e-12 kg: 1e18 ml per kg.

        Rounded to twelve places that needed 31 digits, and Decimal computes
        with 28. Until 10/2026 it raised 'InvalidOperation', an error page,
        before the check could say that the column does not hold it.
        """
        ware = Ware.objects.create(name="Artikel Z", basiseinheit=einheit("Stück(e)"))
        ware.umrechnungen_setzen({Dimension.VOLUMEN: Decimal("999999")})

        with self.assertRaises(ValidationError) as ausnahme:
            einheit_wechseln(ware, einheit("kg"), Decimal("0.000000000001"))

        self.assertEqual(
            ausnahme.exception.messages,
            [
                "Die Umrechnung der Ware wäre danach 999999000000000000 ml je kg. Eine "
                "Umrechnung fasst höchstens 999999,999999999999.",
                "Die Umrechnung der Ware wäre danach 1000000000000 Stk. je kg. Eine Umrechnung "
                "fasst höchstens 999999,999999999999.",
            ],
        )
        ware.refresh_from_db()
        self.assertEqual(ware.basiseinheit.name, "Stück(e)")
        self.assertEqual(ware.faktoren(), {Dimension.VOLUMEN: Decimal("999999")})

    def test_dieselben_auf_der_seite_am_feld(self):
        ware = Ware.objects.create(name="Artikel Z", basiseinheit=einheit("Stück(e)"))
        ware.umrechnungen_setzen({Dimension.VOLUMEN: Decimal("999999")})

        antwort = self.client.post(
            reverse("ware_edit", args=[ware.pk]),
            {
                "name": ware.name,
                "basiseinheit": einheit("kg").pk,
                "umrechnung": "0,000000000001",
                "gebinde-TOTAL_FORMS": "0",
                "gebinde-INITIAL_FORMS": "0",
                "gebinde-MIN_NUM_FORMS": "0",
                "gebinde-MAX_NUM_FORMS": "1000",
            },
        )

        self.assertEqual(antwort.status_code, 200)
        self.assertIn(
            "Die Umrechnung der Ware wäre danach 999999000000000000 ml je kg. Eine Umrechnung "
            "fasst höchstens 999999,999999999999.",
            antwort.context["form"].errors["basiseinheit"],
        )
        ware.refresh_from_db()
        self.assertEqual(ware.basiseinheit.name, "Stück(e)")

    def test_wachsende_mengen_stossen_unterwegs_nicht_aneinander(self):
        """1 and 1000 times a thousand: the small one would otherwise hit the large one's value."""
        ware = Ware.objects.create(name="Artikel Z", basiseinheit=einheit("kg"))
        for name, menge in (("Beutel", "1"), ("Palette", "1000")):
            Gebinde.objects.create(ware=ware, name=name, faktor_in_basiseinheit=Decimal(menge))
        einheit_wechseln(ware, einheit("g"))
        self.assertEqual(
            sorted(ware.gebinde.values_list("faktor_in_basiseinheit", flat=True)),
            [Decimal("1000"), Decimal("1000000")],
        )


class UeberDieSeite(Einheitenwechsel):
    def test_das_feld_gibt_es_nur_beim_bearbeiten(self):
        self.assertContains(
            self.client.get(reverse("ware_edit", args=[self.ware.pk])), 'name="umrechnung"'
        )
        self.assertNotContains(self.client.get(reverse("ware_add")), 'name="umrechnung"')

    def test_ohne_faktor_bleibt_bei_dimensionswechsel_alles_wie_es_war(self):
        antwort = self.absenden(basiseinheit=einheit("kg").pk)
        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, "wechselt die Dimension")
        self.ware.refresh_from_db()
        self.assertEqual(self.ware.basiseinheit.name, "Stück(e)")

    def test_mit_faktor_wird_umgerechnet_und_gemeldet(self):
        inventur = self.abgeschlossene_inventur()
        antwort = self.absenden(basiseinheit=einheit("kg").pk, umrechnung="0,125")
        self.assertEqual(antwort.status_code, 302)
        self.karton.refresh_from_db()
        self.assertEqual(self.karton.faktor_in_basiseinheit, Decimal("2.5"))
        # The house's result is in pieces and stays.
        self.assertEqual(inventur.ergebnisse.get().gesamtmenge_basiseinheit, Decimal("40"))
        self.artikel.refresh_from_db()
        self.assertEqual(self.artikel.faktor_zur_ware, Decimal("0.125"))
        meldung = self.client.get(self.ware.get_absolute_url()).content.decode()
        self.assertIn("1 Stk. = 0,125 kg", meldung)
        self.assertIn("Ein loses Einzelstück gibt es nur bei Stück.", meldung)
        self.assertIn("„Einzeln“ mit 0,125 kg", meldung)

    def test_innerhalb_der_dimension_braucht_es_keine_angabe(self):
        ware = Ware.objects.create(name="Ware P roh", basiseinheit=einheit("kg"))
        sack = Gebinde.objects.create(
            ware=ware, name="Sack", faktor_in_basiseinheit=Decimal("22.68")
        )
        self.ware = ware
        antwort = self.absenden(basiseinheit=einheit("g").pk)
        self.assertEqual(antwort.status_code, 302)
        sack.refresh_from_db()
        self.assertEqual(sack.faktor_in_basiseinheit, Decimal("22680"))

    def test_gebinde_der_seite_gelten_in_der_bisherigen_einheit(self):
        """What is sent along stood in pieces, a newly typed pack too."""
        antwort = self.absenden(
            basiseinheit=einheit("kg").pk,
            umrechnung="0,125",
            **{
                "gebinde-TOTAL_FORMS": "1",
                "gebinde-0-name": "Palette",
                "gebinde-0-faktor_in_basiseinheit": "400",
            },
        )
        self.assertEqual(antwort.status_code, 302)
        palette = self.ware.gebinde.get(name="Palette")
        self.assertEqual(palette.faktor_in_basiseinheit, Decimal("50"))

    def test_was_sich_nicht_rechnen_laesst_nimmt_auch_die_ware_zurueck(self):
        antwort = self.absenden(basiseinheit=einheit("kg").pk, umrechnung="0,0002")
        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, "So klein lässt sich keine Menge führen")
        self.ware.refresh_from_db()
        self.einzeln.refresh_from_db()
        self.assertEqual(self.ware.basiseinheit.name, "Stück(e)")
        self.assertEqual(self.einzeln.faktor_in_basiseinheit, Decimal("1"))

    def test_abgewogene_ware_bekommt_ihr_gewicht_aus_dem_faktor(self):
        ware = Ware.objects.create(name="Becher", basiseinheit=einheit("kg"), abwiegbar=True)
        ware.behaelter.add(Behaelter.objects.create(name="Schale", tara=Decimal("250")))
        self.ware = ware
        antwort = self.absenden(
            basiseinheit=einheit("Stück(e)").pk,
            umrechnung="8",
            abwiegbar="on",
            behaelter=[ware.behaelter.get().pk],
            gramm_je_referenz="1",
        )
        self.assertEqual(antwort.status_code, 302)
        ware.refresh_from_db()
        self.assertEqual(ware.gramm_je_einheit, Decimal("125"))


class MitDerUmrechnungDerWare(Einheitenwechsel):
    """Ware N in ml knows its piece: "750 ml je Stk.". The change into pieces asks for nothing.

    Until 10/2026 the column held 1/750 to six places, and the form showed
    750.188. Nor could the change be typed in: it needs 0.0013333…, and the
    field took six places.
    """

    def setUp(self):
        super().setUp()
        # 'absenden' sends the form of this product.
        self.ware = Ware.objects.create(name="Ware N", basiseinheit=einheit("Milliliter"))
        self.ware.umrechnungen_setzen(
            {
                Dimension.STUECK: eingabe_zu_faktor(
                    Dimension.VOLUMEN, Dimension.STUECK, Decimal("750")
                )
            }
        )
        self.flasche = Gebinde.objects.create(
            ware=self.ware, name="Flasche", faktor_in_basiseinheit=Decimal("750")
        )
        self.klein = Gebinde.objects.create(
            ware=self.ware, name="Fläschchen", faktor_in_basiseinheit=Decimal("200")
        )

    def test_ohne_angabe_rechnet_der_wechsel_mit_der_umrechnung_der_ware(self):
        """750 and 200 ml become 1 and 0.267 pieces, and a piece stays 750 ml."""
        familie = Warenfamilie.objects.create(name="Warenfamilie R", leiteinheit=einheit("Liter"))
        familie.aufnehmen(self.ware)

        faktor = einheit_wechseln(self.ware, einheit("Stück(e)"))

        # Read back as the 750 the form shows, not as the column's 0.001333333333.
        self.assertEqual(faktor, Decimal(1) / Decimal(750))
        self.flasche.refresh_from_db()
        self.klein.refresh_from_db()
        self.assertEqual(self.flasche.faktor_in_basiseinheit, Decimal("1"))
        self.assertEqual(self.klein.faktor_in_basiseinheit, Decimal("0.267"))
        self.ware.refresh_from_db()
        self.assertEqual(self.ware.basiseinheit.name, "Stück(e)")
        self.assertEqual(self.ware.faktoren(), {Dimension.VOLUMEN: Decimal("750")})
        self.assertEqual(self.ware.umrechnungen.get().anzeigetext, "1 Stk. = 750 ml")
        # The family sums in litres and still finds its way there.
        self.assertEqual(familie.waren_ohne_faktor(), [])
        self.assertEqual(self.ware.faktor_nach(Dimension.VOLUMEN), Decimal("750"))

    def test_auch_aus_litern_bleibt_ein_stueck_750_ml(self):
        """Entered as "0,75 l je Stk.", stored as 1.333333333333 pieces per litre."""
        ware = Ware.objects.create(name="Ware L", basiseinheit=einheit("Liter"))
        ware.umrechnungen_setzen(
            {
                Dimension.STUECK: eingabe_zu_faktor(
                    Dimension.VOLUMEN, Dimension.STUECK, Decimal("0.75")
                )
            }
        )
        karton = Gebinde.objects.create(
            ware=ware, name="Karton", faktor_in_basiseinheit=Decimal("4.5")
        )

        einheit_wechseln(ware, einheit("Stück(e)"))

        karton.refresh_from_db()
        self.assertEqual(karton.faktor_in_basiseinheit, Decimal("6"))
        ware.refresh_from_db()
        self.assertEqual(ware.umrechnungen.get().anzeigetext, "1 Stk. = 750 ml")

    def test_ein_haus_in_litern_bekommt_seinen_faktor_auf_zwoelf_stellen(self):
        artikel = Hausartikel.objects.create(
            haus=self.haus,
            ware=self.ware,
            warengruppe=Warengruppe.objects.get_or_create(name="Warengruppe S")[0],
            artikelnummer="101001",
            name="Ware N",
            basiseinheit=einheit("Liter"),
        )
        einheit_wechseln(self.ware, einheit("Stück(e)"))
        artikel.refresh_from_db()
        self.assertEqual(artikel.faktor_zur_ware, Decimal("1.333333333333"))
        self.assertEqual(artikel.einheit_hinweis, "1 l = 1,333333333333 Stk.")

    def test_eine_angabe_geht_der_umrechnung_der_ware_vor(self):
        """Typed is 0.002: from now on a piece is 500 ml, whatever the product said."""
        self.assertEqual(
            umrechnungsfaktor(
                einheit("Milliliter"), einheit("Stück(e)"), Decimal("0.002"), self.ware
            ),
            Decimal("0.002"),
        )
        antwort = self.absenden(basiseinheit=einheit("Stück(e)").pk, umrechnung="0,002")
        self.assertEqual(antwort.status_code, 302)
        self.flasche.refresh_from_db()
        self.assertEqual(self.flasche.faktor_in_basiseinheit, Decimal("1.5"))
        self.ware.refresh_from_db()
        self.assertEqual(self.ware.faktoren(), {Dimension.VOLUMEN: Decimal("500")})

    def test_die_seite_wechselt_ohne_angabe_und_nennt_den_faktor(self):
        antwort = self.absenden(basiseinheit=einheit("Stück(e)").pk)
        self.assertEqual(antwort.status_code, 302)
        self.flasche.refresh_from_db()
        self.assertEqual(self.flasche.faktor_in_basiseinheit, Decimal("1"))
        seite = self.client.get(self.ware.get_absolute_url()).content.decode()
        # The message names the factor as the columns hold it, not in full precision.
        self.assertIn("(1 ml = 0,001333333333 Stk.)", seite)
        self.assertIn("1 Stk. = 750 ml", seite)

    def test_eine_angabe_mit_zwoelf_stellen_wird_angenommen(self):
        antwort = self.absenden(basiseinheit=einheit("Stück(e)").pk, umrechnung="0,001333333333")
        self.assertEqual(antwort.status_code, 302)
        self.flasche.refresh_from_db()
        self.assertEqual(self.flasche.faktor_in_basiseinheit, Decimal("1"))

    def test_ohne_umrechnung_in_die_neue_dimension_bleibt_die_frage(self):
        """Ware N knows what a millilitre weighs, but not what a piece holds."""
        self.ware.umrechnungen_setzen({Dimension.MASSE: Decimal("1.03")})
        with self.assertRaises(ValidationError) as ausnahme:
            einheit_wechseln(self.ware, einheit("Stück(e)"))
        meldung = " ".join(ausnahme.exception.messages)
        self.assertIn("wechselt die Dimension", meldung)
        # Two ways out, and nothing about results: those stay in the house's unit.
        self.assertIn("bei den Umrechnungen der Ware", meldung)
        self.assertNotIn("Ergebnisse", meldung)

        antwort = self.absenden(basiseinheit=einheit("Stück(e)").pk)
        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, "wechselt die Dimension")
        self.ware.refresh_from_db()
        self.flasche.refresh_from_db()
        self.assertEqual(self.ware.basiseinheit.name, "Milliliter")
        self.assertEqual(self.flasche.faktor_in_basiseinheit, Decimal("750"))


class AlleZeilenWieDerBrowser(AngemeldeteSeiten):
    """Artikel N in pieces, with a box and the single unit, changed into kg over the page.

    The browser posts every existing pack row along. 'Einheitenwechsel.absenden'
    posts none, which is why the refusal of the single unit went unseen.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.haus = Haus.objects.create(name="Kino Ost")

    def setUp(self):
        super().setUp()
        self.ware = Ware.objects.create(name="Artikel N", basiseinheit=einheit("Stück(e)"))
        self.karton = Gebinde.objects.create(
            ware=self.ware, name="Karton", faktor_in_basiseinheit=Decimal("20")
        )
        self.einzeln = Gebinde.objects.create(
            ware=self.ware, name="Einzeln", faktor_in_basiseinheit=Decimal("1"), ist_einzeln=True
        )
        Hausartikel.objects.create(
            haus=self.haus,
            ware=self.ware,
            warengruppe=Warengruppe.objects.get_or_create(name="Warengruppe S")[0],
            artikelnummer="200001",
            name="Artikel N",
        )

    def post_wie_der_browser(self, **abweichend):
        """Every existing pack row rides along, the single unit with its tick."""
        daten = {
            "name": self.ware.name,
            "basiseinheit": einheit("kg").pk,
            "umrechnung": "0,125",
            "gebinde-TOTAL_FORMS": "3",
            "gebinde-INITIAL_FORMS": "2",
            "gebinde-MIN_NUM_FORMS": "0",
            "gebinde-MAX_NUM_FORMS": "1000",
            "gebinde-0-id": str(self.karton.pk),
            "gebinde-0-ware": str(self.ware.pk),
            "gebinde-0-name": "Karton",
            "gebinde-0-faktor_in_basiseinheit": "20",
            "gebinde-1-id": str(self.einzeln.pk),
            "gebinde-1-ware": str(self.ware.pk),
            "gebinde-1-name": "Einzeln",
            "gebinde-1-ist_einzeln": "on",
            "gebinde-1-faktor_in_basiseinheit": "1",
            "gebinde-2-id": "",
            "gebinde-2-ware": str(self.ware.pk),
            "gebinde-2-name": "",
            "gebinde-2-faktor_in_basiseinheit": "",
        }
        return self.client.post(reverse("ware_edit", args=[self.ware.pk]), daten | abweichend)

    def test_mit_allen_zeilen_wie_der_browser(self):
        """The packs are checked against the previous unit, then converted.

        Until 10/2026 the formset saw the product already in kg and refused
        the single unit, so the change failed for every product with one.
        """
        antwort = self.post_wie_der_browser()
        self.assertEqual(antwort.status_code, 302, antwort.content[:500])
        self.ware.refresh_from_db()
        self.karton.refresh_from_db()
        self.einzeln.refresh_from_db()
        self.assertEqual(self.ware.basiseinheit.name, "kg")
        self.assertEqual(self.karton.faktor_in_basiseinheit, Decimal("2.5"))
        self.assertEqual(self.einzeln.faktor_in_basiseinheit, Decimal("0.125"))
        self.assertFalse(self.einzeln.ist_einzeln)

    def test_eine_neue_zeile_zaehlt_in_der_bisherigen_einheit(self):
        """A pack typed on the same page was meant in pieces, like the rest."""
        antwort = self.post_wie_der_browser(
            **{
                "gebinde-2-name": "Palette",
                "gebinde-2-faktor_in_basiseinheit": "200",
            }
        )
        self.assertEqual(antwort.status_code, 302, antwort.content[:500])
        palette = self.ware.gebinde.get(name="Palette")
        self.assertEqual(palette.faktor_in_basiseinheit, Decimal("25"))


class AnteileInProzent(AngemeldeteSeiten):
    """A weighed mix states its recipe as shares in percent. They are no quantity in a unit.

    The way from the gram into the product goes through its weight per unit
    ('Rezepturkomponente.menge_je_einheit'), and that follows a new unit by
    itself. Until 10/2026 the shares were converted as well: 60 % became
    0.06 % from g to kg, and the mix distributed a thousandth.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.haus = Haus.objects.create(name="Kino Ost")
        cls.ort = Ort.objects.create(
            name="Theke", bereich=Bereich.objects.create(name="Theke", haus=cls.haus)
        )
        cls.gruppe = Warengruppe.objects.create(name="Warengruppe S")

    def setUp(self):
        super().setUp()
        self.ware = Ware.objects.create(name="Ware A", basiseinheit=einheit("g"))
        self.andere = Ware.objects.create(name="Ware B", basiseinheit=einheit("g"))
        self.mischung = Rezepturartikel.objects.create(
            name="Mischung M", basiseinheit=einheit("g"), anteilig=True
        )
        self.anteil = Rezepturkomponente.objects.create(
            rezepturartikel=self.mischung, ware=self.ware, menge_in_basiseinheit=Decimal("60")
        )
        Rezepturkomponente.objects.create(
            rezepturartikel=self.mischung, ware=self.andere, menge_in_basiseinheit=Decimal("40")
        )

    def verteilt(self) -> Decimal:
        """What 1000 g of the mix hold of the product, in its unit of the moment."""
        mischung = Rezepturartikel.objects.get(pk=self.mischung.pk)
        return mischung.verteilen(Decimal("1000"))[self.ware]

    def test_von_g_nach_kg_bleibt_der_anteil(self):
        self.assertEqual(self.verteilt(), Decimal("600"))

        einheit_wechseln(self.ware, einheit("kg"))

        self.anteil.refresh_from_db()
        self.assertEqual(self.anteil.menge_in_basiseinheit, Decimal("60"))
        self.assertEqual(self.verteilt(), Decimal("0.6"))

    def test_hin_und_zurueck_bleibt_der_anteil_auf_jedem_schritt(self):
        """There and back the old error cancelled out. In between it did not."""
        for neu, erwartet in (("kg", Decimal("0.6")), ("g", Decimal("600"))):
            with self.subTest(nach=neu):
                einheit_wechseln(Ware.objects.get(pk=self.ware.pk), einheit(neu))
                self.anteil.refresh_from_db()
                self.assertEqual(self.anteil.menge_in_basiseinheit, Decimal("60"))
                self.assertEqual(self.verteilt(), erwartet)

    def test_von_liter_nach_kg_ueber_das_gewicht_der_ware(self):
        """A syrup of 1250 g per litre: 600 g of it are 0.48 l before and 0.6 kg after."""
        sirup = Ware.objects.create(name="Sirup S", basiseinheit=einheit("Liter"))
        sirup.umrechnungen_setzen({Dimension.MASSE: Decimal("1250")})
        Rezepturkomponente.objects.filter(pk=self.anteil.pk).update(ware=sirup)
        self.ware = sirup
        self.assertEqual(self.verteilt(), Decimal("0.48"))

        einheit_wechseln(sirup, einheit("kg"))

        self.anteil.refresh_from_db()
        self.assertEqual(self.anteil.menge_in_basiseinheit, Decimal("60"))
        self.assertEqual(self.verteilt(), Decimal("0.6"))

    def test_eine_menge_je_einheit_daneben_wird_weiter_umgerechnet(self):
        """The same product in a counted portion: there the number is a quantity in g."""
        portion = Rezepturartikel.objects.create(name="Portion P", basiseinheit=einheit("Stück(e)"))
        menge = Rezepturkomponente.objects.create(
            rezepturartikel=portion, ware=self.ware, menge_in_basiseinheit=Decimal("140")
        )

        einheit_wechseln(self.ware, einheit("kg"))

        menge.refresh_from_db()
        self.anteil.refresh_from_db()
        self.assertEqual(menge.menge_in_basiseinheit, Decimal("0.14"))
        self.assertEqual(self.anteil.menge_in_basiseinheit, Decimal("60"))

    def test_ueber_die_seite_rechnet_die_laufende_inventur_gleich(self):
        """2000 g of the mix weighed, the house counts in g: 1200 g of Ware A before and after."""
        artikel = {}
        for nummer, ware in (("101001", self.ware), ("101002", self.andere)):
            artikel[ware] = Hausartikel.objects.create(
                haus=self.haus,
                ware=ware,
                warengruppe=self.gruppe,
                artikelnummer=nummer,
                name=ware.name,
            )
        inventur = inventur_anlegen(self.haus)
        position = Zaehlposition.objects.create(
            inventur=inventur, ort=self.ort, artikel=self.mischung
        )
        Wiegung.objects.create(
            zaehlposition=position,
            brutto_gramm=Decimal("2000"),
            behaelter=Behaelter.ohne_behaelter(),
        )
        vorher = inventur.mengen_je_hausartikel()[artikel[self.ware].pk]

        antwort = self.client.post(
            reverse("ware_edit", args=[self.ware.pk]),
            {
                "name": self.ware.name,
                "basiseinheit": einheit("kg").pk,
                "umrechnung": "",
                "gebinde-TOTAL_FORMS": "0",
                "gebinde-INITIAL_FORMS": "0",
                "gebinde-MIN_NUM_FORMS": "0",
                "gebinde-MAX_NUM_FORMS": "1000",
            },
        )

        self.assertEqual(antwort.status_code, 302)
        self.ware.refresh_from_db()
        self.assertEqual(self.ware.basiseinheit, einheit("kg"))
        self.assertEqual(vorher, Decimal("1200"))
        self.assertEqual(inventur.mengen_je_hausartikel()[artikel[self.ware].pk], vorher)
