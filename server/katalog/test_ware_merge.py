"""Make two products one and what happens with the packs in the process.

The normal case is that both carry the same packs: it is the same product,
only imported under two designations. Contested is only where the same name
means another quantity or the same quantity carries two names.
"""

from datetime import date
from decimal import Decimal

from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.urls import reverse

from django_project.testing import (
    AngemeldeteSeiten,
    inventur_anlegen,
    skript,
    ware_zusammenfuehren,
)
from inventur.models import Zaehlposition
from organisation.models import Bereich, Haus, Ort

from .models import (
    Artikelstatus,
    Basiseinheit,
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
from .ware_merge import (
    GLEICH,
    NAMENSWAHL,
    UMBENENNEN,
    gebindepaare,
    zusammenfuehren,
)

#: The message at a new pack name that the target already carries.
VERGEBEN = "Diesen Namen trägt hier schon ein anderes Gebinde."


class Grundlagen(AngemeldeteSeiten):
    """Two products that mean the same, each carried in its house."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.stueck = Basiseinheit.objects.get(dimension=Dimension.STUECK)
        cls.kino_ost = Haus.objects.create(name="Kino Ost")
        cls.kino_sued = Haus.objects.create(name="Kino Süd")
        cls.ort = Ort.objects.create(
            name="Theke", bereich=Bereich.objects.create(name="Theke", haus=cls.kino_sued)
        )
        cls.gruppe = Warengruppe.objects.create(name="Warengruppe O")

        cls.ziel = Ware.objects.create(name="Posten S", basiseinheit=cls.stueck)
        cls.quelle = Ware.objects.create(name="Posten S alt", basiseinheit=cls.stueck)

    def gebinde(self, ware, name, faktor, sortierung=1) -> Gebinde:
        return Gebinde.objects.create(
            ware=ware,
            name=name,
            faktor_in_basiseinheit=Decimal(faktor),
            sortierung=sortierung,
        )

    def hausartikel(self, ware, haus, nummer) -> Hausartikel:
        return Hausartikel.objects.create(
            haus=haus,
            ware=ware,
            warengruppe=self.gruppe,
            artikelnummer=nummer,
            name=f"{ware.name} {nummer}",
        )

    def seite(self) -> str:
        """Where the merge is started. From the house article it is another address."""
        return reverse("ware_merge", args=[self.ziel.pk])

    def vorschau(self, **daten):
        """The first step: the page shows what would happen and writes nothing."""
        return self.client.post(self.seite(), {"quelle": self.quelle.pk, **daten})

    def bestaetigen(self, vorschau, **daten):
        """The second step: the button of the preview, with the signature it showed."""
        return self.client.post(
            self.seite(),
            {
                "quelle": self.quelle.pk,
                "bestaetigt": "1",
                "gezeigt": vorschau.context["signatur"],
                **daten,
            },
        )

    def zusammenfuehren_ueber_die_seite(self, **daten):
        """Both steps, as the page sends them. An objection of the form ends after the first."""
        return ware_zusammenfuehren(self.client, self.seite(), {"quelle": self.quelle.pk, **daten})


class WerBleibt(Grundlagen):
    """Selectable, whichever side the merge is started from."""

    def test_ohne_angabe_bleibt_der_ware_der_seite(self):
        antwort = self.zusammenfuehren_ueber_die_seite()
        self.assertRedirects(antwort, self.ziel.get_absolute_url())
        self.assertFalse(Ware.objects.filter(pk=self.quelle.pk).exists())

    def test_der_gewaehlte_kann_bleiben(self):
        """Then the page's product is absorbed into the other, including articles and packs."""
        artikel = self.hausartikel(self.ziel, self.kino_sued, "200001")
        karton = self.gebinde(self.ziel, "Karton", "12")

        antwort = self.zusammenfuehren_ueber_die_seite(bleibt="anderer")

        # The page one came from no longer exists. It goes on at the remaining one.
        self.assertRedirects(antwort, self.quelle.get_absolute_url())
        self.assertFalse(Ware.objects.filter(pk=self.ziel.pk).exists())
        artikel.refresh_from_db()
        karton.refresh_from_db()
        self.assertEqual(artikel.ware, self.quelle)
        self.assertEqual(karton.ware, self.quelle)

    def test_die_rueckfrage_zu_den_gebinden_folgt_der_wahl(self):
        """Absorbed is the page's product. Up for clarification are **its** packs."""
        self.gebinde(self.quelle, "Flasche", "1")
        eigene = self.gebinde(self.ziel, "Flasche", "2")

        antwort = self.vorschau(bleibt="anderer")

        self.assertEqual(antwort.status_code, 200)
        self.assertEqual(antwort.context["ziel"], self.quelle)
        self.assertEqual([paar.quelle for paar in antwort.context["paare"]], [eigene])
        self.assertContains(antwort, 'value="anderer" ')
        self.assertTrue(Ware.objects.filter(pk=self.ziel.pk).exists())

    def test_die_seite_bietet_die_wahl_an(self):
        antwort = self.client.get(reverse("ware_merge", args=[self.ziel.pk]))
        self.assertContains(antwort, 'name="bleibt"')
        self.assertContains(antwort, "Bleibt bestehen")
        # The first button only shows. Nothing on this page writes yet.
        self.assertContains(antwort, "Vorschau")
        self.assertNotContains(antwort, 'name="bestaetigt"')
        self.assertNotContains(antwort, "data-rueckfrage")


class ZweiSchritte(Grundlagen):
    """Always preview first, then the button: also where nothing is contested.

    Until 10/2026 the page wrote at once when no pack and no house needed a
    decision. A second enter in the search field merged two products for good,
    and the second step took the ticks of one choice for another.
    """

    def test_die_vorschau_schreibt_nichts(self):
        self.gebinde(self.ziel, "Flasche", "1")
        self.gebinde(self.quelle, "Flasche", "1")

        antwort = self.vorschau()

        self.assertEqual(antwort.status_code, 200)
        self.assertTrue(Ware.objects.filter(pk=self.quelle.pk).exists())
        self.assertContains(antwort, "Was mit den Gebinden geschieht")
        self.assertContains(antwort, 'name="bestaetigt"')
        self.assertContains(antwort, f'name="gezeigt" value="{antwort.context["signatur"]}"')
        self.assertContains(antwort, "Endgültig zusammenführen")
        self.assertContains(antwort, "data-rueckfrage")

    def test_der_knopf_der_vorschau_fuehrt_zusammen(self):
        antwort = self.bestaetigen(self.vorschau())

        self.assertRedirects(antwort, self.ziel.get_absolute_url())
        self.assertFalse(Ware.objects.filter(pk=self.quelle.pk).exists())

    def test_bestaetigt_ohne_vorschau_schreibt_nichts(self):
        """What a stale page or a script sends: 'bestaetigt' without the shown choice."""
        antwort = self.client.post(self.seite(), {"quelle": self.quelle.pk, "bestaetigt": "1"})

        self.assertEqual(antwort.status_code, 200)
        self.assertTrue(Ware.objects.filter(pk=self.quelle.pk).exists())

    def test_eine_andere_wahl_bekommt_erst_ihre_vorschau(self):
        vorschau = self.vorschau()

        antwort = self.bestaetigen(vorschau, bleibt="anderer")

        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, "Noch ist nichts geschehen")
        self.assertEqual(antwort.context["ziel"], self.quelle, "the preview of the new choice")
        self.assertTrue(Ware.objects.filter(pk=self.ziel.pk).exists())
        self.assertTrue(Ware.objects.filter(pk=self.quelle.pk).exists())

    def test_die_unterschrift_nennt_beide_und_den_faktor(self):
        vorschau = self.vorschau()
        self.assertEqual(vorschau.context["signatur"], f"{self.quelle.pk}:{self.ziel.pk}:1")

    def test_enter_in_der_wahl_sendet_nicht(self):
        """Tom Select types into a text field, and a text field submits on enter.

        The guard is in 'select.js' for every searchable list, so this page
        needs none of its own.
        """
        quelle = skript("select")
        self.assertIn('"Enter"', quelle)
        self.assertIn("preventDefault", quelle)
        self.assertIn("isOpen", quelle)

    def test_scheitert_die_umrechnung_bleibt_alles_wie_es_war(self):
        """A pack too small in the unit that stays: the button ends at the conversion field.

        The service rolls back what it had done so far, the page shows the
        form again with what the conversion said, and no message claims a
        merge.
        """
        quelle = Ware.objects.create(
            name="Ware G", basiseinheit=Basiseinheit.objects.get(kennung="GRAMM")
        )
        prise = self.gebinde(quelle, "Prise", "0.4")
        artikel = self.hausartikel(quelle, self.kino_sued, "101001")
        ziel = Ware.objects.create(
            name="Ware K", basiseinheit=Basiseinheit.objects.get(kennung="KILOGRAMM")
        )
        adresse = reverse("ware_merge", args=[ziel.pk])
        vorschau = self.client.post(adresse, {"quelle": quelle.pk})

        antwort = self.client.post(
            adresse,
            {"quelle": quelle.pk, "bestaetigt": "1", "gezeigt": vorschau.context["signatur"]},
        )

        self.assertEqual(antwort.status_code, 200)
        self.assertFormError(
            antwort.context["form"],
            "umrechnung",
            "Gebinde „Prise“ fasste danach weniger als 0,001 kg. So klein lässt sich keine "
            "Menge führen.",
        )
        self.assertNotIn("vorschau", antwort.context)
        self.assertEqual(list(get_messages(antwort.wsgi_request)), [])
        prise.refresh_from_db()
        artikel.refresh_from_db()
        self.assertEqual((prise.ware, prise.faktor_in_basiseinheit), (quelle, Decimal("0.4")))
        self.assertEqual(artikel.ware, quelle)
        self.assertEqual(list(ziel.gebinde.all()), [])
        self.assertTrue(Ware.objects.filter(pk=quelle.pk).exists())


class GleicheGebinde(Grundlagen):
    def test_gleicher_name_und_gleiche_menge_verschmelzen_ohne_rueckfrage(self):
        """The normal case: both carry a "Flasche", it is the same product."""
        ziel_flasche = self.gebinde(self.ziel, "Flasche", "1")
        quelle_flasche = self.gebinde(self.quelle, "Flasche", "1")

        antwort = self.zusammenfuehren_ueber_die_seite()

        self.assertRedirects(antwort, self.ziel.get_absolute_url())
        self.assertFalse(Ware.objects.filter(pk=self.quelle.pk).exists())
        self.assertFalse(Gebinde.objects.filter(pk=quelle_flasche.pk).exists())
        self.assertEqual(list(self.ziel.gebinde.all()), [ziel_flasche])

    def test_verschmolzenes_gebinde_nimmt_seine_verweise_mit(self):
        """Location defaults and count positions must not get lost in the process."""
        ziel_flasche = self.gebinde(self.ziel, "Flasche", "1")
        quelle_flasche = self.gebinde(self.quelle, "Flasche", "1")
        artikel = self.hausartikel(self.quelle, self.kino_sued, "200001")
        erwartung = Erwartung.objects.create(ort=self.ort, artikel=artikel)
        vorgabe = ErwartungGebinde.objects.create(
            erwartung=erwartung, gebinde=quelle_flasche, standard_menge=Decimal("6")
        )
        inventur = inventur_anlegen(self.kino_sued, stichtag=date(2026, 1, 31))
        position = Zaehlposition.objects.create(
            inventur=inventur, ort=self.ort, artikel=artikel, gebinde=quelle_flasche
        )

        vorher = position.zuletzt_geaendert
        self.zusammenfuehren_ueber_die_seite()

        vorgabe.refresh_from_db()
        position.refresh_from_db()
        self.assertEqual(vorgabe.gebinde, ziel_flasche)
        self.assertEqual(position.gebinde, ziel_flasche)
        # Otherwise the counting app would keep the number of a pack that no longer exists.
        self.assertGreater(position.zuletzt_geaendert, vorher)

    def test_gebinde_ohne_gegenstueck_zieht_hinten_an(self):
        """The position is unique per product. The old one does not apply in the target."""
        self.gebinde(self.ziel, "Flasche", "1", sortierung=1)
        umzug = self.gebinde(self.quelle, "Karton", "12", sortierung=1)

        self.zusammenfuehren_ueber_die_seite()

        umzug.refresh_from_db()
        self.assertEqual(umzug.ware, self.ziel)
        self.assertEqual(
            [g.name for g in self.ziel.gebinde.order_by("sortierung")], ["Flasche", "Karton"]
        )


class StrittigeGebinde(Grundlagen):
    def test_gleicher_name_andere_menge_fragt_nach(self):
        """A "Flasche" means 1 here and 2 there. They must not merge."""
        self.gebinde(self.ziel, "Flasche", "1")
        self.gebinde(self.quelle, "Flasche", "2")

        antwort = self.vorschau()

        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, "Gebinde klären")
        # Until then nothing is written.
        self.assertTrue(Ware.objects.filter(pk=self.quelle.pk).exists())

    def test_umbenannt_bleiben_beide_erhalten(self):
        ziel_flasche = self.gebinde(self.ziel, "Flasche", "1")
        quelle_flasche = self.gebinde(self.quelle, "Flasche", "2")

        antwort = self.zusammenfuehren_ueber_die_seite(
            **{f"neu-{quelle_flasche.pk}": "Flasche groß"}
        )

        self.assertRedirects(antwort, self.ziel.get_absolute_url())
        quelle_flasche.refresh_from_db()
        self.assertEqual(quelle_flasche.ware, self.ziel)
        self.assertEqual(
            sorted(g.name for g in self.ziel.gebinde.all()), ["Flasche", "Flasche groß"]
        )
        self.assertEqual(ziel_flasche.faktor_in_basiseinheit, Decimal("1.000"))

    def test_ein_umbenanntes_gebinde_des_ziels_erfaehrt_die_app(self):
        """The target's crate takes the source's name. Its rows in a running inventory follow.

        Until 10/2026 they kept their timestamp, and the app's next sync did
        not fetch them: it showed the old name.
        """
        kiste = self.gebinde(self.ziel, "Kiste", "24")
        kasten = self.gebinde(self.quelle, "Kasten", "24")
        artikel = self.hausartikel(self.ziel, self.kino_sued, "101001")
        inventur = inventur_anlegen(self.kino_sued, stichtag=date(2026, 1, 31))
        position = Zaehlposition.objects.create(
            inventur=inventur, ort=self.ort, artikel=artikel, gebinde=kiste
        )
        vorher = position.zuletzt_geaendert

        self.zusammenfuehren_ueber_die_seite(**{f"name-{kasten.pk}": "quelle"})

        kiste.refresh_from_db()
        position.refresh_from_db()
        self.assertEqual(kiste.name, "Kasten")
        self.assertGreater(position.zuletzt_geaendert, vorher)

    def test_der_neue_name_darf_nicht_schon_vergeben_sein(self):
        self.gebinde(self.ziel, "Flasche", "1")
        quelle_flasche = self.gebinde(self.quelle, "Flasche", "2")

        antwort = self.zusammenfuehren_ueber_die_seite(**{f"neu-{quelle_flasche.pk}": "flasche"})

        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, "trägt hier schon ein anderes Gebinde")
        self.assertTrue(Ware.objects.filter(pk=self.quelle.pk).exists())

    def namensfehler(self, **daten) -> dict:
        """Both steps by hand: the second one stops at the packs, nothing is written."""
        antwort = self.bestaetigen(self.vorschau(), **daten)
        self.assertEqual(antwort.status_code, 200)
        self.assertTrue(Ware.objects.filter(pk=self.quelle.pk).exists())
        return antwort.context["aufloesen"].errors

    def test_der_neue_name_darf_kein_anderes_gebinde_des_ziels_treffen(self):
        """Taken is every name in the target, not only that of the partner.

        Until 10/2026 this ended as an error page at the unique index.
        """
        self.gebinde(self.ziel, "Kasten", "20", sortierung=1)
        self.gebinde(self.ziel, "Pack", "6", sortierung=2)
        quelle_kasten = self.gebinde(self.quelle, "Kasten", "24")

        fehler = self.namensfehler(**{f"neu-{quelle_kasten.pk}": "Pack"})

        self.assertEqual(fehler, {f"neu-{quelle_kasten.pk}": [VERGEBEN]})

    def test_der_neue_name_darf_kein_umziehendes_gebinde_treffen(self):
        """A pack without a partner moves under its own name, and that one is taken too."""
        self.gebinde(self.ziel, "Kasten", "20")
        quelle_kasten = self.gebinde(self.quelle, "Kasten", "24", sortierung=1)
        self.gebinde(self.quelle, "Kiste", "12", sortierung=2)

        fehler = self.namensfehler(**{f"neu-{quelle_kasten.pk}": "Kiste"})

        self.assertEqual(fehler, {f"neu-{quelle_kasten.pk}": [VERGEBEN]})

    def test_die_namenswahl_darf_kein_anderes_gebinde_des_ziels_treffen(self):
        """The source's name for the target's box, but another target pack carries it already.

        Until 10/2026 this ended as an error page at the unique index.
        """
        self.gebinde(self.ziel, "Kasten", "20", sortierung=1)
        self.gebinde(self.ziel, "Pack", "6", sortierung=2)
        quelle_pack = self.gebinde(self.quelle, "Pack", "20")
        self.assertEqual([paar.art for paar in gebindepaare(self.quelle, self.ziel)], [NAMENSWAHL])

        fehler = self.namensfehler(**{f"name-{quelle_pack.pk}": "quelle"})

        self.assertEqual(fehler, {f"name-{quelle_pack.pk}": [VERGEBEN]})

    def test_namenswahl_und_neuer_name_duerfen_nicht_zusammenfallen(self):
        """Both name a pack of the target. Two packs under one name do not fit there."""
        self.gebinde(self.ziel, "Kasten", "20", sortierung=1)
        self.gebinde(self.ziel, "Pack", "6", sortierung=2)
        quelle_kasten = self.gebinde(self.quelle, "Kasten", "24", sortierung=1)
        quelle_tray = self.gebinde(self.quelle, "Tray", "6", sortierung=2)

        fehler = self.namensfehler(
            **{f"neu-{quelle_kasten.pk}": "Tray", f"name-{quelle_tray.pk}": "quelle"}
        )

        # The later of the two in the order of the source gets the message.
        self.assertEqual(fehler, {f"name-{quelle_tray.pk}": [VERGEBEN]})

    def test_gleiche_menge_anderer_name_laesst_den_namen_waehlen(self):
        """Two names for the same quantity. One of the two stays."""
        ziel_dose = self.gebinde(self.ziel, "Dose", "1")
        quelle_sprueh = self.gebinde(self.quelle, "Sprühdose", "1")

        antwort = self.zusammenfuehren_ueber_die_seite(**{f"name-{quelle_sprueh.pk}": "quelle"})

        self.assertRedirects(antwort, self.ziel.get_absolute_url())
        ziel_dose.refresh_from_db()
        self.assertEqual(ziel_dose.name, "Sprühdose")
        self.assertEqual(list(self.ziel.gebinde.all()), [ziel_dose])

    def test_namenswahl_behaelt_auf_wunsch_den_alten(self):
        ziel_dose = self.gebinde(self.ziel, "Dose", "1")
        quelle_sprueh = self.gebinde(self.quelle, "Sprühdose", "1")

        self.zusammenfuehren_ueber_die_seite(**{f"name-{quelle_sprueh.pk}": "ziel"})

        ziel_dose.refresh_from_db()
        self.assertEqual(ziel_dose.name, "Dose")


class InaktivesGebindeDerBleibenden(Grundlagen):
    """An active pack of the absorbed product would merge into an inactive one.

    Inactive packs get no row in new inventories. Until 10/2026 the preview
    said "same pack, merges", and the next inventory had no row at the
    location where the absorbed product was expected in the pack. Now the
    preview highlights the pack and the person decides (2026-10-20).
    """

    def setUp(self):
        super().setUp()
        self.kasten = self.gebinde(self.ziel, "Kasten", "24")
        self.kasten.aktiv_setzen(False)
        self.alter_kasten = self.gebinde(self.quelle, "Kasten", "24")
        self.artikel = self.hausartikel(self.quelle, self.kino_sued, "101001")
        Erwartung.anlegen(self.ort, self.artikel, vorgaben=[(self.alter_kasten.pk, None)])

    def antwort(self, wahl: str, gebinde: Gebinde | None = None) -> dict:
        return {f"aktiv-{self.alter_kasten.pk}": f"{wahl}:{(gebinde or self.kasten).pk}"}

    def neue_inventur(self):
        inventur = inventur_anlegen(self.kino_sued, stichtag=date(2026, 2, 28))
        inventur.zaehlpositionen_anlegen()
        return inventur.zaehlpositionen.filter(artikel=self.artikel, ort=self.ort)

    def test_die_vorschau_hebt_es_hervor_und_fragt(self):
        vorschau = self.vorschau()

        self.assertContains(vorschau, "„Kasten“ ist hier inaktiv")
        self.assertContains(vorschau, "„Kasten“ gibt es bei „Posten S“ schon, aber inaktiv")
        self.assertContains(vorschau, "Wieder aktivieren")

    def test_ohne_antwort_wird_nicht_geschrieben(self):
        antwort = self.bestaetigen(self.vorschau())

        self.assertContains(antwort, "Bitte wählen: wieder aktivieren oder inaktiv lassen.")
        self.assertTrue(Ware.objects.filter(pk=self.quelle.pk).exists())

    def test_wieder_aktivieren(self):
        antwort = self.bestaetigen(self.vorschau(), **self.antwort("aktivieren"))

        self.assertRedirects(antwort, self.ziel.get_absolute_url(), fetch_redirect_response=False)
        self.kasten.refresh_from_db()
        self.assertTrue(self.kasten.aktiv)
        self.assertEqual([zeile.gebinde for zeile in self.neue_inventur()], [self.kasten])

    def test_inaktiv_lassen(self):
        """The defaults rest on the inactive pack, and the message says so."""
        antwort = self.bestaetigen(self.vorschau(), **self.antwort("inaktiv"))

        self.assertRedirects(antwort, self.ziel.get_absolute_url(), fetch_redirect_response=False)
        self.kasten.refresh_from_db()
        self.assertFalse(self.kasten.aktiv)
        self.assertEqual(
            [str(meldung) for meldung in get_messages(antwort.wsgi_request)][-1],
            "„Kasten“ bleibt inaktiv. Seine Vorgaben gelten erst, wenn es wieder aktiv ist.",
        )
        self.assertFalse(self.neue_inventur().exists())

    def test_eine_antwort_fuer_ein_anderes_gebinde_fragt_neu(self):
        """A stale page cannot switch on another pack."""
        anderes = self.gebinde(self.ziel, "Karton", "12")

        antwort = self.bestaetigen(self.vorschau(), **self.antwort("aktivieren", anderes))

        self.assertContains(antwort, "Das Gebinde hat sich inzwischen geändert. Bitte neu wählen.")
        self.assertTrue(Ware.objects.filter(pk=self.quelle.pk).exists())

    def test_ein_inaktives_gebinde_der_aufgeloesten_fragt_nicht(self):
        """Inactive into inactive merges as before, nothing comes back."""
        self.alter_kasten.aktiv_setzen(False)

        self.assertNotContains(self.vorschau(), "ist hier inaktiv")
        self.zusammenfuehren_ueber_die_seite()
        self.kasten.refresh_from_db()
        self.assertFalse(self.kasten.aktiv)


class Einteilung(Grundlagen):
    def test_paare_sagen_was_geschieht(self):
        self.gebinde(self.ziel, "Flasche", "1", sortierung=1)
        self.gebinde(self.ziel, "Dose", "2", sortierung=2)
        self.gebinde(self.quelle, "Flasche", "1", sortierung=1)
        self.gebinde(self.quelle, "Sprühdose", "2", sortierung=2)
        self.gebinde(self.quelle, "Karton", "12", sortierung=3)

        arten = {paar.quelle.name: paar.art for paar in gebindepaare(self.quelle, self.ziel)}

        self.assertEqual(arten["Flasche"], GLEICH)
        self.assertEqual(arten["Sprühdose"], NAMENSWAHL)
        self.assertEqual(arten["Karton"], "")

    def test_gleicher_name_andere_menge_heisst_umbenennen(self):
        self.gebinde(self.ziel, "Flasche", "1")
        self.gebinde(self.quelle, "Flasche", "2")

        paar = gebindepaare(self.quelle, self.ziel)[0]

        self.assertEqual(paar.art, UMBENENNEN)
        self.assertEqual(paar.vorschlag, "Flasche (2)")


class Dubletten(Grundlagen):
    """A house that carries both: the absorbed product's row is discontinued, counting as the other.

    Nothing changes about the staying row.
    """

    def setUp(self):
        super().setUp()
        self.kasten_ziel = self.gebinde(self.ziel, "Kasten", "20")
        self.kasten_quelle = self.gebinde(self.quelle, "Kasten", "20")
        self.flasche = self.gebinde(self.quelle, "Flasche", "1", sortierung=2)
        self.bleibt = self.hausartikel(self.ziel, self.kino_sued, "101001")
        self.geht = self.hausartikel(self.quelle, self.kino_sued, "101002")
        self.lager = Ort.objects.create(name="Lager", bereich=self.ort.bereich)

    def erwartung(self, artikel, ort, *vorgaben, wiegen=False) -> Erwartung:
        erwartung = Erwartung.objects.create(ort=ort, artikel=artikel, wiegen_erwartet=wiegen)
        for gebinde, menge in vorgaben:
            ErwartungGebinde.objects.create(
                erwartung=erwartung,
                gebinde=gebinde,
                standard_menge=None if menge is None else Decimal(menge),
            )
        return erwartung

    def test_der_erste_schritt_fragt_je_haus_und_schreibt_nichts(self):
        antwort = self.vorschau()
        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, "Häuser, die beide führen")
        self.assertContains(antwort, "101002 „Posten S alt 101002“ wird eingestellt")
        self.assertTrue(Ware.objects.filter(pk=self.quelle.pk).exists())
        self.geht.refresh_from_db()
        self.assertEqual(self.geht.status, Artikelstatus.AKTIV)

    def test_der_artikel_der_aufgeloesten_ware_wird_eingestellt(self):
        bericht = zusammenfuehren(self.quelle, self.ziel)
        self.assertEqual(bericht.eingestellt, 1)
        self.geht.refresh_from_db()
        self.assertEqual(self.geht.status, Artikelstatus.EINGESTELLT)
        self.assertEqual(self.geht.zaehlt_als, self.bleibt)
        self.assertEqual(self.geht.ware, self.ziel, "unter derselben Ware")
        self.bleibt.refresh_from_db()
        self.assertTrue(self.bleibt.aktiv)

    def test_am_bleibenden_aendert_sich_nichts(self):
        """Inactive it stays inactive, even if the discontinued one was active."""
        self.bleibt.deaktivieren()
        zusammenfuehren(self.quelle, self.ziel)
        self.bleibt.refresh_from_db()
        self.geht.refresh_from_db()
        self.assertEqual(self.bleibt.status, Artikelstatus.INAKTIV)
        self.assertEqual(self.geht.status, Artikelstatus.EINGESTELLT)
        self.assertEqual(self.geht.zaehlt_als, self.bleibt)

    def test_welche_ware_bleibt_entscheidet_auch_im_haus(self):
        """The other product stays: then this page's row is the discontinued one."""
        nur_hier = self.erwartung(self.bleibt, self.lager, (self.kasten_ziel, "1"))
        antwort = self.zusammenfuehren_ueber_die_seite(
            bleibt="anderer", **{f"erwartung-{nur_hier.pk}": "on"}
        )
        self.assertRedirects(antwort, self.quelle.get_absolute_url(), fetch_redirect_response=False)
        self.bleibt.refresh_from_db()
        self.assertEqual(self.bleibt.status, Artikelstatus.EINGESTELLT)
        self.assertEqual(self.bleibt.zaehlt_als, self.geht)
        nur_hier.refresh_from_db()
        self.assertEqual(nur_hier.artikel_id, self.geht.pk, "übernommen")

    def test_eine_geaenderte_wahl_nimmt_die_haken_der_alten_nicht(self):
        """The ticks belong to the previewed choice. For the other one, they mean other rows.

        Until 10/2026 the second step wrote for the new choice with the old
        ticks: its form has no required field, so an empty one was valid, and
        the expectations of the now discontinued row fell without a question.
        """
        nur_hier = self.erwartung(self.bleibt, self.lager, (self.kasten_ziel, "1"))
        vorschau = self.vorschau()

        antwort = self.bestaetigen(vorschau, bleibt="anderer")

        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, "Noch ist nichts geschehen")
        self.assertContains(antwort, f'name="erwartung-{nur_hier.pk}"', msg_prefix="its own tick")
        self.assertTrue(Erwartung.objects.filter(pk=nur_hier.pk).exists())
        self.bleibt.refresh_from_db()
        self.assertEqual(self.bleibt.status, Artikelstatus.AKTIV)
        self.assertTrue(Ware.objects.filter(pk=self.ziel.pk).exists())

    def test_erwartung_ohne_haken_geht_mit_dem_einstellen(self):
        weg = self.erwartung(self.geht, self.lager, (self.kasten_quelle, None))
        zusammenfuehren(self.quelle, self.ziel, uebernehmen={str(self.kino_sued.pk): frozenset()})
        self.assertFalse(Erwartung.objects.filter(pk=weg.pk).exists())

    def test_eine_erwartung_mit_dem_einzelstueck_bricht_die_seite_nicht(self):
        """The loose single unit carries a lazy name. Joined into the label it broke the page."""
        Gebinde.objects.filter(pk=self.flasche.pk).delete()
        einzeln = Gebinde.objects.create(
            ware=self.quelle,
            name="Einzeln",
            ist_einzeln=True,
            faktor_in_basiseinheit=Decimal("1"),
            sortierung=3,
        )
        mit_einzeln = self.erwartung(
            self.geht, self.lager, (self.kasten_quelle, None), (einzeln, "4")
        )
        antwort = self.vorschau()
        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, f'name="erwartung-{mit_einzeln.pk}"')
        antwort = self.bestaetigen(antwort, **{f"erwartung-{mit_einzeln.pk}": "on"})
        self.assertRedirects(antwort, self.ziel.get_absolute_url(), fetch_redirect_response=False)
        mit_einzeln.refresh_from_db()
        self.assertEqual(mit_einzeln.artikel_id, self.bleibt.pk)

    def test_uebernommen_an_einem_neuen_ort_mit_position_und_gebinde(self):
        rueber = self.erwartung(self.geht, self.lager, (self.flasche, "6"))
        sortierung = rueber.sortierung
        zusammenfuehren(self.quelle, self.ziel)
        rueber.refresh_from_db()
        self.assertEqual((rueber.artikel_id, rueber.sortierung), (self.bleibt.pk, sortierung))
        vorgabe = rueber.gebinde_vorgaben.get()
        self.assertEqual((vorgabe.gebinde_id, vorgabe.standard_menge), (self.flasche.pk, 6))
        self.assertEqual(vorgabe.gebinde.ware, self.ziel)

    def test_am_selben_ort_wird_zusammengezaehlt(self):
        """The same pack twice is the same thing twice. Another pack comes along."""
        bleibende = self.erwartung(self.bleibt, self.ort, (self.kasten_ziel, "2"))
        self.erwartung(
            self.geht, self.ort, (self.kasten_quelle, "3"), (self.flasche, "4"), wiegen=True
        )
        bericht = zusammenfuehren(self.quelle, self.ziel)
        self.assertEqual(bericht.erwartungen, 1)
        mengen = dict(bleibende.gebinde_vorgaben.values_list("gebinde__name", "standard_menge"))
        self.assertEqual(mengen, {"Kasten": Decimal("5"), "Flasche": Decimal("4")})
        bleibende.refresh_from_db()
        self.assertTrue(bleibende.wiegen_erwartet)
        self.assertEqual(Erwartung.objects.filter(ort=self.ort).count(), 1)

    def test_eine_leere_menge_zaehlt_nichts_dazu(self):
        bleibende = self.erwartung(self.bleibt, self.ort, (self.kasten_ziel, None))
        self.erwartung(self.geht, self.ort, (self.kasten_quelle, "3"))
        zusammenfuehren(self.quelle, self.ziel)
        self.assertEqual(bleibende.gebinde_vorgaben.get().standard_menge, Decimal("3"))

    def test_wieder_aktiviert_zaehlt_er_als_er_selbst(self):
        zusammenfuehren(self.quelle, self.ziel)
        self.bleibt.refresh_from_db()
        self.bleibt.einstellen()
        self.geht.refresh_from_db()
        self.geht.aktivieren()
        self.geht.refresh_from_db()
        self.assertIsNone(self.geht.zaehlt_als)

    def test_nur_ein_eingestellter_zaehlt_als_ein_anderer(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Hausartikel.objects.filter(pk=self.geht.pk).update(zaehlt_als=self.bleibt)

    def test_das_paar_ueberlebt_nicht_zaehlbar_und_zurueck(self):
        """Into "Not countable" and out again: the duplicate stays what it was.

        Until 10/2026 the way in cleared the mark, and the way out set both
        rows active: the unique index of the house refused, an error page.
        """
        zusammenfuehren(self.quelle, self.ziel)

        self.ziel.ignorieren()
        self.geht.refresh_from_db()
        self.assertEqual(
            (self.geht.status, self.geht.zaehlt_als_id), (Artikelstatus.IGNORIERT, self.bleibt.pk)
        )

        self.ziel.wieder_aufnehmen()

        self.bleibt.refresh_from_db()
        self.geht.refresh_from_db()
        self.assertEqual(self.bleibt.status, Artikelstatus.AKTIV)
        self.assertEqual(
            (self.geht.status, self.geht.zaehlt_als_id), (Artikelstatus.EINGESTELLT, self.bleibt.pk)
        )

    def test_zwei_ignorierte_ohne_marke_kommen_als_paar_zurueck(self):
        """Rows from before 10/2026 lost the mark and remember nothing.

        The one carried last comes back active.
        """
        zusammenfuehren(self.quelle, self.ziel)
        self.ziel.ignorieren()
        Hausartikel.objects.filter(pk=self.geht.pk).update(zaehlt_als=None)
        Hausartikel.objects.filter(ware=self.ziel).update(status_vorher="")

        self.ziel.wieder_aufnehmen()

        self.bleibt.refresh_from_db()
        self.geht.refresh_from_db()
        self.assertEqual(self.bleibt.status, Artikelstatus.AKTIV, "left the carried state last")
        self.assertEqual(
            (self.geht.status, self.geht.zaehlt_als_id), (Artikelstatus.EINGESTELLT, self.bleibt.pk)
        )

    def test_wieder_aufnehmen_ueber_die_seite_mit_dublettenpaar(self):
        zusammenfuehren(self.quelle, self.ziel)
        self.ziel.ignorieren()

        antwort = self.client.post(
            reverse("ware_status", args=[self.ziel.pk]), {"status": "WIEDER_AUFNEHMEN"}
        )

        self.assertRedirects(antwort, self.ziel.get_absolute_url(), fetch_redirect_response=False)
        self.assertEqual(
            sorted(self.ziel.hausartikel.values_list("status", flat=True)),
            sorted([Artikelstatus.AKTIV, Artikelstatus.EINGESTELLT]),
        )

    def test_nur_im_selben_haus_und_derselben_ware(self):
        fremd = self.hausartikel(self.ziel, self.kino_ost, "102001")
        zusammenfuehren(self.quelle, self.ziel)
        self.geht.refresh_from_db()
        self.geht.zaehlt_als = fremd
        with self.assertRaises(ValidationError):
            self.geht.full_clean()


class Ketten(Grundlagen):
    """Merged twice: the duplicate of the first merge must count as the row of the second."""

    def test_eine_dublette_zaehlt_nach_dem_zweiten_zusammenfuehren_als_die_bleibende(self):
        """Until 10/2026 A pointed at B and B at C. The target stock then asked about A again."""
        dritte = Ware.objects.create(name="Posten S neu", basiseinheit=self.stueck)
        a = self.hausartikel(self.quelle, self.kino_sued, "101001")
        b = self.hausartikel(self.ziel, self.kino_sued, "101002")
        c = self.hausartikel(dritte, self.kino_sued, "101003")

        zusammenfuehren(self.quelle, self.ziel)
        zusammenfuehren(self.ziel, dritte)

        a.refresh_from_db()
        b.refresh_from_db()
        self.assertEqual(
            (a.status, a.zaehlt_als_id, a.ware_id), (Artikelstatus.EINGESTELLT, c.pk, dritte.pk)
        )
        self.assertEqual((b.status, b.zaehlt_als_id), (Artikelstatus.EINGESTELLT, c.pk))
        a.full_clean()


class VomHausartikel(Grundlagen):
    """The merge started from a house article: the other one is one of the house's articles."""

    def setUp(self):
        super().setUp()
        self.gebinde(self.ziel, "Kasten", "20")
        self.gebinde(self.quelle, "Kasten", "20")
        self.bleibt = self.hausartikel(self.ziel, self.kino_sued, "101001")
        self.geht = self.hausartikel(self.quelle, self.kino_sued, "101002")
        self.adresse = reverse("hausartikel_zusammenfuehren", args=[self.bleibt.pk])

    def seite(self) -> str:
        return self.adresse

    def test_die_auswahl_nennt_die_artikel_des_hauses(self):
        anderswo = Ware.objects.create(name="Ware N", basiseinheit=self.stueck)
        self.hausartikel(anderswo, self.kino_ost, "102001")
        seite = self.client.get(self.adresse)
        self.assertEqual(seite.status_code, 200)
        wahl = dict(seite.context["form"].fields["quelle"].choices)
        beschriftungen = [str(text) for text in wahl.values()]
        self.assertIn("101002 Posten S alt 101002", beschriftungen)
        self.assertNotIn("102001 Ware N 102001", beschriftungen, "anderes Haus")
        self.assertEqual(len(beschriftungen), 2, "leere Wahl und der eine andere Artikel")

    def test_zusammenfuehren_und_zurueck_zum_artikel(self):
        vorschau = self.vorschau()
        self.assertEqual(vorschau.status_code, 200)
        self.assertContains(vorschau, "Häuser, die beide führen")
        antwort = self.bestaetigen(vorschau)
        self.assertRedirects(antwort, self.bleibt.get_absolute_url(), fetch_redirect_response=False)
        self.assertFalse(Ware.objects.filter(pk=self.quelle.pk).exists())
        self.geht.refresh_from_db()
        self.assertEqual(
            (self.geht.status, self.geht.zaehlt_als), (Artikelstatus.EINGESTELLT, self.bleibt)
        )

    def test_der_knopf_steht_auf_der_seite_des_artikels(self):
        seite = self.client.get(self.bleibt.get_absolute_url())
        self.assertContains(seite, self.adresse)


class DirektAufgerufen(Grundlagen):
    """The business logic without interface. It is in 'ware_merge.py'."""

    def test_bericht_zaehlt_mit(self):
        self.gebinde(self.ziel, "Flasche", "1")
        self.gebinde(self.quelle, "Flasche", "1")
        self.gebinde(self.quelle, "Karton", "12", sortierung=2)
        self.hausartikel(self.quelle, self.kino_sued, "200001")

        bericht = zusammenfuehren(self.quelle, self.ziel)

        self.assertEqual(bericht.hausartikel, 1)
        self.assertEqual(bericht.verschmolzen, 1)
        self.assertEqual(bericht.umgezogen, 1)


class NichtZaehlbarBeimZusammenfuehren(Grundlagen):
    def test_ware_und_keine_ware_lassen_sich_nicht_zusammenfuehren(self):
        self.quelle.warenfamilie = Warenfamilie.nicht_zaehlbar()
        self.quelle.save()
        antwort = self.zusammenfuehren_ueber_die_seite()
        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, "Nur eine der beiden gehört zu „Nicht zählbar“")
        self.assertTrue(Ware.objects.filter(pk=self.quelle.pk).exists())

    def test_zwei_ignorierte_bleiben_ignoriert(self):
        kein = Warenfamilie.nicht_zaehlbar()
        for ware in (self.ziel, self.quelle):
            ware.warenfamilie = kein
            ware.save()
        artikel = self.hausartikel(self.quelle, self.kino_ost, "901001")
        zusammenfuehren(self.quelle, self.ziel)
        artikel.refresh_from_db()
        self.assertEqual(artikel.ware, self.ziel)
        self.assertEqual(artikel.status, Artikelstatus.IGNORIERT)


class AnteileInProzent(AngemeldeteSeiten):
    """A share in percent of the absorbed product stays a share, whatever the units.

    It goes through the weight per unit of the product that stays
    ('Rezepturkomponente.menge_je_einheit'). Until 10/2026 it was converted
    along: merged from g into kg, 60 % became 0.06 %. And a product that
    stays without a weight per unit would silently count a gram per unit,
    so that is an obstacle now, depending on who stays.
    """

    def setUp(self):
        super().setUp()
        self.gramm = Basiseinheit.objects.get(name="g")
        self.stueck = Basiseinheit.objects.get(dimension=Dimension.STUECK)
        self.quelle = Ware.objects.create(name="Ware Q", basiseinheit=self.gramm)
        andere = Ware.objects.create(name="Ware B", basiseinheit=self.gramm)
        self.mischung = Rezepturartikel.objects.create(
            name="Mischung M", basiseinheit=self.gramm, anteilig=True
        )
        self.anteil = Rezepturkomponente.objects.create(
            rezepturartikel=self.mischung, ware=self.quelle, menge_in_basiseinheit=Decimal("60")
        )
        Rezepturkomponente.objects.create(
            rezepturartikel=self.mischung, ware=andere, menge_in_basiseinheit=Decimal("40")
        )

    def verteilt(self, ware: Ware) -> Decimal:
        """What 1000 g of the mix hold of this product, in its unit."""
        mischung = Rezepturartikel.objects.get(pk=self.mischung.pk)
        return mischung.verteilen(Decimal("1000"))[ware]

    def ueber_die_seite(self, ziel: Ware, **daten):
        """Merged from the page of 'ziel', with Ware Q as the other one."""
        return ware_zusammenfuehren(
            self.client, reverse("ware_merge", args=[ziel.pk]), {"quelle": self.quelle.pk, **daten}
        )

    def test_von_g_in_kg_bleibt_der_anteil(self):
        ziel = Ware.objects.create(name="Ware P", basiseinheit=Basiseinheit.objects.get(name="kg"))

        zusammenfuehren(self.quelle, ziel, faktor=Decimal("0.001"))

        self.anteil.refresh_from_db()
        self.assertEqual(self.anteil.ware, ziel)
        self.assertEqual(self.anteil.menge_in_basiseinheit, Decimal("60"))
        self.assertEqual(self.verteilt(ziel), Decimal("0.6"))

    def test_ohne_gewicht_der_bleibenden_haelt_es_auf(self):
        ziel = Ware.objects.create(name="Ware P", basiseinheit=self.stueck)

        antwort = self.ueber_die_seite(ziel, umrechnung="0,008")

        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, "kennt keines")
        self.assertContains(antwort, "Mischung M")
        self.assertTrue(Ware.objects.filter(pk=self.quelle.pk).exists())
        self.anteil.refresh_from_db()
        self.assertEqual(self.anteil.ware, self.quelle)

    def test_mit_gewicht_der_bleibenden_geht_es(self):
        """125 g per piece: 600 g of Ware Q in the mix are 4.8 pieces of Ware P."""
        ziel = Ware.objects.create(name="Ware P", basiseinheit=self.stueck)
        ziel.umrechnungen_setzen({Dimension.MASSE: Decimal("125")})

        antwort = self.ueber_die_seite(ziel, umrechnung="0,008")

        self.assertRedirects(antwort, ziel.get_absolute_url(), fetch_redirect_response=False)
        self.anteil.refresh_from_db()
        self.assertEqual(self.anteil.ware, ziel)
        self.assertEqual(self.anteil.menge_in_basiseinheit, Decimal("60"))
        self.assertEqual(self.verteilt(ziel), Decimal("4.8"))

    def test_bleibt_die_gewogene_gibt_es_kein_hindernis(self):
        """From the page of the piece good, but Ware Q stays: its weight is in its unit."""
        stueckware = Ware.objects.create(name="Ware P", basiseinheit=self.stueck)

        antwort = self.ueber_die_seite(stueckware, bleibt="anderer", umrechnung="125")

        self.assertRedirects(antwort, self.quelle.get_absolute_url(), fetch_redirect_response=False)
        self.assertFalse(Ware.objects.filter(pk=stueckware.pk).exists())
        self.anteil.refresh_from_db()
        self.assertEqual(self.anteil.ware, self.quelle)
        self.assertEqual(self.anteil.menge_in_basiseinheit, Decimal("60"))
