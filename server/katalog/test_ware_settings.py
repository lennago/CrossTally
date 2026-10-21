"""The settings of the product on the page of a house article.

Whoever maintains a house corrects the product right there: its name,
whether it is weighed, its family. Checked is above all what must **not**
happen on the side: a product changed that nobody meant, a house article
that cannot be corrected because its product has an open end, and half a
save when the settings do not hold.
"""

from decimal import Decimal

from django import forms
from django.urls import reverse

from django_project.testing import AngemeldeteSeiten, monatsinventur, skript
from organisation.models import Haus

from .forms import WareEinstellungenForm
from .models import (
    Artikelstatus,
    Basiseinheit,
    Behaelter,
    Dimension,
    Hausartikel,
    Ware,
    Warenfamilie,
    Warengruppe,
)

PRAEFIX = "einstellungen"


def wie_der_browser(form: forms.BaseForm) -> dict:
    """What the browser sends back when nobody touches the form."""
    daten = {}
    for feld in form:
        wert = feld.value()
        widget = feld.field.widget
        if isinstance(widget, forms.CheckboxInput):
            if wert:
                daten[feld.html_name] = "on"
        elif isinstance(widget, forms.SelectMultiple | forms.CheckboxSelectMultiple):
            daten[feld.html_name] = [str(eintrag) for eintrag in wert or []]
        elif isinstance(widget, forms.Select):
            daten[feld.html_name] = "" if wert is None else str(wert)
        else:
            daten[feld.html_name] = widget.format_value(wert) or ""
    return daten


class EinstellungenDerWare(AngemeldeteSeiten):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.nord = Haus.objects.create(name="Kino Nord")
        cls.ost = Haus.objects.create(name="Kino Ost")
        cls.gruppe = Warengruppe.objects.create(name="Warengruppe G")
        cls.stueck = Basiseinheit.objects.get(dimension=Dimension.STUECK)
        cls.schale = Behaelter.objects.create(name="Schale", tara=Decimal("250"))

        cls.ware = Ware.objects.create(name="Artikel C 0,33", basiseinheit=cls.stueck)
        cls.andere = Ware.objects.create(name="Ware N", basiseinheit=cls.stueck)
        cls.artikel = Hausartikel.objects.create(
            haus=cls.nord,
            ware=cls.ware,
            warengruppe=cls.gruppe,
            artikelnummer="101001",
            name="Artikel C klein",
        )
        cls.im_osten = Hausartikel.objects.create(
            haus=cls.ost,
            ware=cls.ware,
            warengruppe=cls.gruppe,
            artikelnummer="102001",
            name="Artikel C Flasche",
        )

    def adresse(self) -> str:
        return reverse("hausartikel_edit", args=[self.artikel.pk])

    def hausartikel(self, **abweichend) -> dict:
        daten = {
            "artikelnummer": self.artikel.artikelnummer,
            "name": self.artikel.name,
            "ware": self.ware.pk,
            "neuer_ware": "",
            "basiseinheit": "",
            "inventurart": monatsinventur().pk,
            "warengruppe": self.gruppe.pk,
            "neue_warengruppe": "",
            "ean": "",
            "gebinde-TOTAL_FORMS": "0",
            "gebinde-INITIAL_FORMS": "0",
            "gebinde-MIN_NUM_FORMS": "0",
            "gebinde-MAX_NUM_FORMS": "1000",
        }
        return daten | abweichend

    def einstellungen(self, **abweichend) -> dict:
        """The settings as the page shows them, with what the test changes."""
        seite = self.client.get(self.adresse())
        daten = wie_der_browser(seite.context["einstellungen"])
        for name, wert in abweichend.items():
            schluessel = f"{PRAEFIX}-{name}"
            if wert is None:
                daten.pop(schluessel, None)
            else:
                daten[schluessel] = wert
        return daten

    def speichern(self, hausartikel: dict | None = None, **einstellungen):
        return self.client.post(
            self.adresse(),
            self.hausartikel(**(hausartikel or {})) | self.einstellungen(**einstellungen),
            follow=True,
        )

    # -- What the page shows -----------------------------------------------

    def test_seite_zeigt_die_einstellungen_mit_hinweis(self):
        antwort = self.client.get(self.adresse())
        self.assertContains(antwort, "Einstellungen der Ware")
        self.assertContains(antwort, "Gilt in jedem Haus.")
        self.assertContains(antwort, "Neben Kino Nord führen sie: Kino Ost.")
        self.assertContains(antwort, f'name="{PRAEFIX}-name"')
        self.assertContains(antwort, f'name="{PRAEFIX}-abwiegbar"')
        self.assertContains(antwort, self.ware.get_absolute_url())

    def test_fuehrt_sie_nur_dieses_haus_sagt_der_hinweis_das(self):
        self.im_osten.einstellen()
        antwort = self.client.get(self.adresse())
        self.assertContains(antwort, "Bisher führt sie nur Kino Nord.")
        self.assertNotContains(antwort, "Kino Ost")

    def test_beim_anlegen_gibt_es_keine_einstellungen(self):
        """There the product is chosen or arises. Nothing of it is there to be set yet."""
        antwort = self.client.get(reverse("hausartikel_add", args=[self.nord.pk]))
        self.assertNotContains(antwort, "Einstellungen der Ware")
        self.assertNotContains(antwort, "js/ware_settings.js")

    def test_die_einheit_bleibt_auf_der_seite_der_ware(self):
        form = WareEinstellungenForm(instance=self.ware, prefix=PRAEFIX)
        self.assertNotIn("basiseinheit", form.fields)
        self.assertNotIn("umrechnung", form.fields)
        # Pieces compute themselves. For them there is nothing to enter.
        self.assertNotIn("faktor_STUECK", form.fields)
        self.assertEqual(
            list(form.fields),
            [
                "name",
                "faktor_MASSE",
                "faktor_VOLUMEN",
                "faktor_PORTION",
                "warenfamilie",
                "abwiegbar",
                "behaelter",
            ],
        )

    def test_die_umrechnungen_heissen_nach_der_einheit(self):
        """On the product's page the script names them. Here the unit is fixed."""
        antwort = self.client.get(self.adresse())
        self.assertContains(antwort, "g je Stk.")
        self.assertContains(antwort, "ml je Stk.")

    def test_die_skripte_finden_die_felder_unter_dem_praefix(self):
        antwort = self.client.get(self.adresse())
        self.assertContains(antwort, "js/ware_form.js")
        self.assertContains(antwort, f'data-praefix="{PRAEFIX}"')
        self.assertIn("TEXT.praefix", skript("ware_form"))

        self.assertContains(antwort, "js/ware_settings.js")
        self.assertContains(antwort, f'data-einstellungen-der-ware="{self.ware.pk}"')
        self.assertIn("[data-einstellungen-der-ware]", skript("ware_settings"))
        self.assertIn('getElementById("id_ware")', skript("ware_settings"))
        self.assertContains(antwort, 'id="id_ware"')

    def test_die_seite_der_ware_kommt_ohne_praefix_aus(self):
        antwort = self.client.get(reverse("ware_edit", args=[self.ware.pk]))
        self.assertContains(antwort, "js/ware_form.js")
        self.assertNotContains(antwort, "data-praefix")

    # -- Saving ------------------------------------------------------------

    def test_name_und_wiegen_aendern_sich_an_der_ware(self):
        antwort = self.speichern(
            name="Artikel C 0,33 l",
            abwiegbar="on",
            faktor_MASSE="345,5",
            behaelter=[str(self.schale.pk)],
        )
        self.assertRedirects(antwort, self.artikel.get_absolute_url())
        self.assertContains(antwort, "Die Einstellungen der Ware „Artikel C 0,33 l“ sind geändert.")
        self.ware.refresh_from_db()
        self.assertEqual(self.ware.name, "Artikel C 0,33 l")
        self.assertTrue(self.ware.abwiegbar)
        self.assertEqual(self.ware.gramm_je_einheit, Decimal("345.5"))
        self.assertEqual(list(self.ware.behaelter.all()), [self.schale])
        # The other house carries the same product and sees the same.
        self.im_osten.refresh_from_db()
        self.assertEqual(self.im_osten.ware.name, "Artikel C 0,33 l")

    def test_der_hausartikel_wird_im_selben_zug_gespeichert(self):
        self.speichern({"name": "Artikel C ganz klein"}, name="Artikel C 0,33 l")
        self.artikel.refresh_from_db()
        self.assertEqual(self.artikel.name, "Artikel C ganz klein")
        self.assertEqual(self.artikel.ware.name, "Artikel C 0,33 l")

    def test_unveraendert_bleibt_die_ware_unberuehrt(self):
        """Also with a stored conversion: the page shows it with a comma."""
        self.ware.umrechnungen_setzen({Dimension.MASSE: Decimal("345.5")})
        self.ware.abwiegbar = True
        self.ware.save()
        self.ware.behaelter.add(self.schale)

        antwort = self.speichern({"ean": "4000000000017"})
        self.assertRedirects(antwort, self.artikel.get_absolute_url())
        self.assertNotContains(antwort, "Die Einstellungen der Ware")
        self.artikel.refresh_from_db()
        self.assertEqual(self.artikel.ean, "4000000000017")
        self.ware.refresh_from_db()
        self.assertTrue(self.ware.abwiegbar)
        self.assertEqual(self.ware.gramm_je_einheit, Decimal("345.5"))
        self.assertEqual(list(self.ware.behaelter.all()), [self.schale])

    def test_eine_ware_mit_offenem_ende_steht_der_korrektur_nicht_im_weg(self):
        """Weighing can be switched on through the button before the weight is known."""
        Ware.objects.filter(pk=self.ware.pk).update(abwiegbar=True)
        antwort = self.speichern({"ean": "4000000000017"})
        self.assertRedirects(antwort, self.artikel.get_absolute_url())
        self.artikel.refresh_from_db()
        self.assertEqual(self.artikel.ean, "4000000000017")

    def test_ungueltige_einstellung_nimmt_auch_den_hausartikel_zurueck(self):
        antwort = self.speichern({"name": "Artikel C ganz klein"}, abwiegbar="on")
        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, "Zum Wiegen braucht die Ware „g je Stk.“")
        # What was typed stays in the fields.
        self.assertContains(antwort, 'value="Artikel C ganz klein"')
        self.artikel.refresh_from_db()
        self.assertEqual(self.artikel.name, "Artikel C klein")
        self.ware.refresh_from_db()
        self.assertFalse(self.ware.abwiegbar)

    def test_vergebener_name_wird_abgewiesen(self):
        antwort = self.speichern(name=self.andere.name)
        self.assertEqual(antwort.status_code, 200)
        self.assertTrue(antwort.context["einstellungen"].has_error("name"))
        self.ware.refresh_from_db()
        self.assertEqual(self.ware.name, "Artikel C 0,33")

    def test_behaelter_ohne_wiegen_werden_abgewiesen(self):
        antwort = self.speichern(behaelter=[str(self.schale.pk)])
        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, "Behälter gehören zum Wiegen.")
        self.assertFalse(self.ware.behaelter.exists())

    # -- When the settings do not count --------------------------------------

    def test_wer_eine_andere_ware_waehlt_aendert_keine(self):
        """The settings shown belonged to the previous product. The script hides them."""
        antwort = self.speichern({"ware": self.andere.pk}, name="Ganz anders", abwiegbar="on")
        self.assertRedirects(antwort, self.artikel.get_absolute_url())
        self.assertNotContains(antwort, "Die Einstellungen der Ware")
        self.artikel.refresh_from_db()
        self.assertEqual(self.artikel.ware, self.andere)
        for ware, name in ((self.ware, "Artikel C 0,33"), (self.andere, "Ware N")):
            ware.refresh_from_db()
            self.assertEqual(ware.name, name)
            self.assertFalse(ware.abwiegbar)

    def test_eine_neue_ware_nimmt_die_einstellungen_nicht_mit(self):
        antwort = self.speichern(
            {"ware": "", "neuer_ware": "Ware Z", "basiseinheit": self.stueck.pk},
            name="Ganz anders",
        )
        self.assertRedirects(antwort, self.artikel.get_absolute_url())
        self.artikel.refresh_from_db()
        self.assertEqual(self.artikel.ware.name, "Ware Z")
        self.ware.refresh_from_db()
        self.assertEqual(self.ware.name, "Artikel C 0,33")

    def test_ein_aufruf_an_der_seite_vorbei_laesst_die_ware_stehen(self):
        """An absent tick would otherwise switch off the weighing."""
        self.ware.umrechnungen_setzen({Dimension.MASSE: Decimal("345.5")})
        self.ware.abwiegbar = True
        self.ware.save()
        self.ware.behaelter.add(self.schale)

        antwort = self.client.post(self.adresse(), self.hausartikel(ean="4000000000017"))
        self.assertEqual(antwort.status_code, 302)
        self.ware.refresh_from_db()
        self.assertTrue(self.ware.abwiegbar)
        self.assertEqual(self.ware.gramm_je_einheit, Decimal("345.5"))
        self.assertEqual(list(self.ware.behaelter.all()), [self.schale])

    # -- The family "Not countable" ------------------------------------------

    def test_in_nicht_zaehlbar_zieht_jedes_haus_nach(self):
        familie = Warenfamilie.nicht_zaehlbar()
        antwort = self.speichern(warenfamilie=str(familie.pk))
        self.assertRedirects(antwort, self.artikel.get_absolute_url())
        for artikel in (self.artikel, self.im_osten):
            artikel.refresh_from_db()
            self.assertEqual(artikel.status, Artikelstatus.IGNORIERT)

    def test_in_nicht_zaehlbar_entsteht_kein_gebinde_mehr(self):
        """What is no product has no packs, even if the row was filled in."""
        familie = Warenfamilie.nicht_zaehlbar()
        self.speichern(
            {
                "gebinde-TOTAL_FORMS": "1",
                "gebinde-0-name": "Kasten",
                "gebinde-0-faktor_in_basiseinheit": "20",
            },
            warenfamilie=str(familie.pk),
        )
        self.assertFalse(self.ware.gebinde.exists())

    def test_zurueck_aus_nicht_zaehlbar_scheitert_am_belegten_platz(self):
        """The rule of the product looks at the house article as it is saved now."""
        self.ware.ignorieren()
        Hausartikel.objects.create(
            haus=self.nord,
            ware=self.andere,
            warengruppe=self.gruppe,
            artikelnummer="101002",
            name="Ware N",
        )
        # The number typed here belongs to another article of the house. While
        # the article is ignored that is no obstacle. Active it would be one.
        antwort = self.speichern({"artikelnummer": "101002"}, warenfamilie="")
        self.assertEqual(antwort.status_code, 200)
        self.assertTrue(antwort.context["einstellungen"].has_error("warenfamilie"))
        self.artikel.refresh_from_db()
        self.assertEqual(self.artikel.artikelnummer, "101001")
        self.assertEqual(self.artikel.status, Artikelstatus.IGNORIERT)
        self.ware.refresh_from_db()
        self.assertTrue(self.ware.ist_nicht_zaehlbar)
