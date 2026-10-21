"""Split a product ('katalog/ware_split.py').

The delicate part is not re-attaching the house articles but the packs:
they belong to the product, and location defaults as well as counted
quantities point to them. After splitting each of these references has to
point to a pack of the product its house article now belongs to.
"""

from datetime import date
from decimal import Decimal
from unittest import mock

from django.urls import reverse
from django.utils import timezone

from api.master_data import ware_name
from django_project.testing import AngemeldeteSeiten, inventur_anlegen, jahresinventur
from inventur.models import Zaehlposition
from organisation.models import Bereich, Haus, Ort

from . import ware_split
from .models import (
    Artikelstatus,
    Basiseinheit,
    Behaelter,
    Dimension,
    Erwartung,
    ErwartungGebinde,
    Gebinde,
    Hausartikel,
    Rezepturartikel,
    Rezepturkomponente,
    Ware,
    Warenfamilie,
    Warengruppe,
)
from .ware_split import aufteilen


class Grundlagen(AngemeldeteSeiten):
    """A product two houses carry. One of them means something different."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.stueck = Basiseinheit.objects.get(dimension=Dimension.STUECK)
        cls.kino_nord = Haus.objects.create(name="Kino Nord")
        cls.kino_ost = Haus.objects.create(name="Kino Ost")
        cls.ort = Ort.objects.create(
            name="Theke", bereich=Bereich.objects.create(name="Theke", haus=cls.kino_ost)
        )
        cls.gruppe = Warengruppe.objects.create(name="Warengruppe R")

        cls.ware = Ware.objects.create(name="Artikel C", basiseinheit=cls.stueck)
        cls.flasche = cls.gebinde(cls.ware, "Flasche", "1", sortierung=1)
        cls.kasten = cls.gebinde(cls.ware, "Kasten", "20", sortierung=2)
        cls.nord = cls.hausartikel(cls.ware, cls.kino_nord, "101001")
        cls.ost = cls.hausartikel(cls.ware, cls.kino_ost, "101002")

    @classmethod
    def gebinde(cls, ware, name, faktor, sortierung=1, aktiv=True) -> Gebinde:
        return Gebinde.objects.create(
            ware=ware,
            name=name,
            faktor_in_basiseinheit=Decimal(faktor),
            sortierung=sortierung,
            aktiv=aktiv,
        )

    @classmethod
    def hausartikel(cls, ware, haus, nummer) -> Hausartikel:
        return Hausartikel.objects.create(
            haus=haus,
            ware=ware,
            warengruppe=cls.gruppe,
            artikelnummer=nummer,
            name=f"{ware.name} {nummer}",
        )

    def aufteilen_ueber_die_seite(self, folgen=False, **daten):
        return self.client.post(
            reverse("ware_aufteilen", args=[self.ware.pk]),
            {"name": "Artikel C", "neuer_name": "Artikel C Ost", **daten},
            follow=folgen,
        )


class Aufteilen(Grundlagen):
    def test_hausartikel_wandern_und_beide_namen_gelten(self):
        bericht = aufteilen(self.ware, "Artikel C klein", "Artikel C groß", [self.ost])

        self.assertEqual(bericht.gewandert, 1)
        self.assertEqual(bericht.bleibt.name, "Artikel C klein")
        self.assertEqual(
            list(bericht.neu.hausartikel.values_list("artikelnummer", flat=True)), ["101002"]
        )
        self.assertEqual(
            list(bericht.bleibt.hausartikel.values_list("artikelnummer", flat=True)), ["101001"]
        )

    def test_die_artikel_behalten_ihre_art(self):
        """The type hangs on the house article. Splitting moves the article, not the type."""
        self.ost.inventurart_setzen(jahresinventur())
        aufteilen(self.ware, "Artikel C", "Artikel C Ost", [self.ost])
        self.ost.refresh_from_db()
        self.assertEqual(self.ost.inventurart, jahresinventur())

    def test_die_neue_ware_erbt_alles_was_die_ware_ausmacht(self):
        becher = Behaelter.objects.create(name="Behälter R", tara=Decimal("15"))
        familie = Warenfamilie.objects.create(name="Warenfamilie R", leiteinheit=self.stueck)
        self.ware.warenfamilie = familie
        self.ware.abwiegbar = True
        self.ware.save()
        self.ware.umrechnungen_setzen(
            {Dimension.MASSE: Decimal("330"), Dimension.VOLUMEN: Decimal("300")}
        )
        self.ware.behaelter.add(becher)

        neu = aufteilen(self.ware, "Artikel C", "Artikel C Ost", [self.ost]).neu

        self.assertEqual(neu.basiseinheit, self.stueck)
        self.assertEqual(neu.warenfamilie, familie)
        self.assertTrue(neu.abwiegbar)
        self.assertEqual(
            neu.faktoren(), {Dimension.MASSE: Decimal("330"), Dimension.VOLUMEN: Decimal("300")}
        )
        self.assertEqual(list(neu.behaelter.all()), [becher])

    def test_die_gebinde_werden_abgebildet_samt_reihenfolge(self):
        """The inactive ones too, because old counts hang on them."""
        alt = self.gebinde(self.ware, "Tüte", "6", sortierung=3, aktiv=False)

        bericht = aufteilen(self.ware, "Artikel C", "Artikel C Ost", [self.ost])

        self.assertEqual(bericht.gebinde, 3)
        abbilder = list(bericht.neu.gebinde.order_by("sortierung"))
        self.assertEqual([g.name for g in abbilder], ["Flasche", "Kasten", "Tüte"])
        self.assertEqual(
            [g.faktor_in_basiseinheit for g in abbilder],
            [g.faktor_in_basiseinheit for g in (self.flasche, self.kasten, alt)],
        )
        self.assertEqual([g.aktiv for g in abbilder], [True, True, False])
        # The previous product keeps its own.
        self.assertEqual(self.ware.gebinde.count(), 3)

    def test_fremde_hausartikel_werden_uebergangen(self):
        andere = Ware.objects.create(name="Ware N", basiseinheit=self.stueck)
        fremd = self.hausartikel(andere, self.kino_nord, "101003")

        bericht = aufteilen(self.ware, "Artikel C", "Artikel C Ost", [fremd])

        self.assertEqual(bericht.gewandert, 0)
        fremd.refresh_from_db()
        self.assertEqual(fremd.ware, andere)

    def test_ohne_hausartikel_bleibt_die_neue_ware_leer(self):
        bericht = aufteilen(self.ware, "Artikel C", "Artikel C Ost", [])

        self.assertEqual(bericht.gewandert, 0)
        self.assertEqual(bericht.neu.hausartikel.count(), 0)
        self.assertEqual(bericht.neu.gebinde.count(), 2)


class Verweise(Grundlagen):
    """What hangs on the migrated rows has to fit the new product."""

    def setUp(self):
        super().setUp()
        self.erwartung = Erwartung.objects.create(ort=self.ort, artikel=self.ost)
        self.vorgabe = ErwartungGebinde.objects.create(
            erwartung=self.erwartung, gebinde=self.kasten, standard_menge=Decimal("6")
        )
        self.inventur = inventur_anlegen(self.kino_ost, stichtag=date(2026, 1, 31))
        self.position = Zaehlposition.objects.create(
            inventur=self.inventur, ort=self.ort, artikel=self.ost, gebinde=self.kasten
        )

    def test_ortsvorgabe_und_zaehlposition_zeigen_auf_das_abbild(self):
        vorher = self.position.zuletzt_geaendert

        bericht = aufteilen(self.ware, "Artikel C", "Artikel C Ost", [self.ost])

        abbild = bericht.neu.gebinde.get(name="Kasten")
        self.vorgabe.refresh_from_db()
        self.position.refresh_from_db()
        self.assertEqual(self.vorgabe.gebinde, abbild)
        self.assertEqual(self.vorgabe.standard_menge, Decimal("6.000"))
        self.assertEqual(self.position.gebinde, abbild)
        self.assertEqual(bericht.zaehlpositionen, 1)
        # Otherwise the counting app would keep the number of a pack that no
        # longer belongs to this product.
        self.assertGreater(self.position.zuletzt_geaendert, vorher)

    def test_die_vorgabe_eines_bleibenden_satzes_ruehrt_sich_nicht(self):
        erwartung = Erwartung.objects.create(
            ort=Ort.objects.create(
                name="Lager", bereich=Bereich.objects.create(name="Lager", haus=self.kino_nord)
            ),
            artikel=self.nord,
        )
        vorgabe = ErwartungGebinde.objects.create(erwartung=erwartung, gebinde=self.kasten)

        aufteilen(self.ware, "Artikel C", "Artikel C Ost", [self.ost])

        vorgabe.refresh_from_db()
        self.assertEqual(vorgabe.gebinde, self.kasten)

    def test_die_umgehaengten_verweise_halten_der_eigenen_pruefung_stand(self):
        """Pack and article have to belong to the same product afterwards."""
        aufteilen(self.ware, "Artikel C", "Artikel C Ost", [self.ost])

        self.vorgabe.refresh_from_db()
        self.position.refresh_from_db()
        self.vorgabe.full_clean()
        self.position.full_clean()


class DerNameDerBleibenden(Grundlagen):
    """The product that stays may get a new name, and the app shows it on every row."""

    def setUp(self):
        super().setUp()
        lager = Ort.objects.create(
            name="Lager", bereich=Bereich.objects.create(name="Lager", haus=self.kino_nord)
        )
        inventur = inventur_anlegen(self.kino_nord, stichtag=date(2026, 1, 31))
        self.position = Zaehlposition.objects.create(
            inventur=inventur, ort=lager, artikel=self.nord, gebinde=self.kasten
        )
        self.vorher = self.position.zuletzt_geaendert

    def test_ihre_zeilen_kommen_mit_dem_naechsten_abgleich(self):
        """Until 10/2026 they kept their timestamp, and the app the old name."""
        aufteilen(self.ware, "Artikel C klein", "Artikel C Ost", [self.ost])

        self.position.refresh_from_db()
        self.assertGreater(self.position.zuletzt_geaendert, self.vorher)
        self.assertEqual(ware_name(self.position.artikel)[0], "Artikel C klein")

    def test_mit_den_wandernden_in_einer_folge_gesperrt(self):
        """Both articles in one call, by number, before the rows: the order of every path."""
        with mock.patch.object(
            ware_split, "artikel_sperren", wraps=ware_split.artikel_sperren
        ) as gesperrt:
            aufteilen(self.ware, "Artikel C klein", "Artikel C Ost", [self.ost])

        self.assertEqual(set(gesperrt.call_args_list[0].args[0]), {self.nord.pk, self.ost.pk})

    def test_bleibt_der_name_ruehren_sich_ihre_zeilen_nicht(self):
        """Nothing changes for the app there: a sync would only fetch them for nothing."""
        aufteilen(self.ware, "Artikel C", "Artikel C Ost", [self.ost])

        self.position.refresh_from_db()
        self.assertEqual(self.position.zuletzt_geaendert, self.vorher)


class UeberDieSeite(Grundlagen):
    def test_aufteilen_und_meldung(self):
        antwort = self.aufteilen_ueber_die_seite(wandern=[self.ost.pk], folgen=True)

        self.assertRedirects(antwort, self.ware.get_absolute_url())
        neu = Ware.objects.get(name="Artikel C Ost")
        self.assertEqual(list(neu.hausartikel.all()), [self.ost])
        self.assertContains(antwort, "1 Hausartikel")

    def test_die_seite_zeigt_die_hausartikel_beider_haeuser(self):
        antwort = self.client.get(reverse("ware_aufteilen", args=[self.ware.pk]))

        self.assertContains(antwort, "Kino Nord")
        self.assertContains(antwort, "Kino Ost")
        self.assertContains(antwort, 'name="wandern"')

    def test_namen_muessen_frei_und_verschieden_sein(self):
        Ware.objects.create(name="Ware N", basiseinheit=self.stueck)

        antwort = self.aufteilen_ueber_die_seite(neuer_name="Ware N")
        self.assertContains(antwort, "gibt es schon")

        antwort = self.aufteilen_ueber_die_seite(neuer_name="Artikel C")
        self.assertContains(antwort, "verschiedene Namen")
        self.assertEqual(Ware.objects.filter(name="Artikel C").count(), 1)

    def test_rezepturen_bleiben_und_werden_gemeldet(self):
        rezepturartikel = Rezepturartikel.objects.create(
            name="Mischung R", basiseinheit=Basiseinheit.objects.get(name="g")
        )
        Rezepturkomponente.objects.create(
            rezepturartikel=rezepturartikel, ware=self.ware, menge_in_basiseinheit=Decimal("2")
        )

        antwort = self.aufteilen_ueber_die_seite(wandern=[self.ost.pk], folgen=True)

        self.assertContains(antwort, "Mischung R")
        self.assertEqual(self.ware.verwendet_in.count(), 1)
        self.assertEqual(Ware.objects.get(name="Artikel C Ost").verwendet_in.count(), 0)


class NichtZaehlbar(Grundlagen):
    """A product from "Not countable" stays so in both halves."""

    def test_beide_haelften_bleiben_ignoriert(self):
        self.ware.ignorieren()

        bericht = aufteilen(self.ware, "Artikel C", "Artikel C Ost", [self.ost])

        self.assertTrue(bericht.neu.ist_nicht_zaehlbar)
        self.ost.refresh_from_db()
        self.nord.refresh_from_db()
        self.assertEqual(self.ost.status, Artikelstatus.IGNORIERT)
        self.assertEqual(self.nord.status, Artikelstatus.IGNORIERT)


class Dubletten(Grundlagen):
    """A duplicate counts as another row of the same product. The split may separate them."""

    def dublette(self) -> Hausartikel:
        return Hausartikel.objects.create(
            haus=self.kino_nord,
            ware=self.ware,
            warengruppe=self.gruppe,
            artikelnummer="101009",
            name="Artikel C alt",
            status=Artikelstatus.EINGESTELLT,
            gueltig_bis=timezone.now(),
            zaehlt_als=self.nord,
        )

    def test_wandert_die_dublette_allein_verliert_sie_ihren_verweis(self):
        """Until 10/2026 'zaehlt_als' pointed across the product border afterwards."""
        dublette = self.dublette()
        aufteilen(self.ware, "Artikel C", "Artikel C alt", [dublette])
        dublette.refresh_from_db()
        self.assertEqual(dublette.ware.name, "Artikel C alt")
        self.assertIsNone(dublette.zaehlt_als)

    def test_wandert_ihr_ziel_allein_ebenso(self):
        dublette = self.dublette()
        aufteilen(self.ware, "Artikel C", "Artikel C neu", [self.nord])
        dublette.refresh_from_db()
        self.assertEqual(dublette.ware, self.ware)
        self.assertIsNone(dublette.zaehlt_als)

    def test_wandern_beide_bleibt_der_verweis(self):
        dublette = self.dublette()
        aufteilen(self.ware, "Artikel C", "Artikel C neu", [self.nord, dublette])
        dublette.refresh_from_db()
        self.assertEqual(dublette.zaehlt_als, self.nord)
        self.nord.refresh_from_db()
        self.assertEqual(dublette.ware_id, self.nord.ware_id)
