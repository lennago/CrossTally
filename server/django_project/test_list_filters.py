"""The skeleton of the filter forms, independent of a list."""

from django import forms
from django.db.models import F, Q
from django.test import SimpleTestCase
from django.utils.translation import gettext_lazy as _

from .columns import Spaltensortierung
from .list_filters import ListenFilterForm


class Filterform(ListenFilterForm):
    AUSWAHLFELDER = ("status",)
    GLIEDERUNGEN = (("familie", _("Nach Familie"), ("familie",)),)

    q = forms.CharField(required=False, label=_("Suche"))
    status = forms.ChoiceField(
        choices=(("", _("Jeder Status")), ("aktiv", _("Aktiv"))), required=False
    )


class Auswahlfelder(SimpleTestCase):
    def test_nur_das_suchfeld_nimmt_eingaben(self):
        """The select lists of a filter only choose. 'select.js' would otherwise put a
        text search on each of them."""
        formular = Filterform()
        for feld in formular.auswahlfelder:
            self.assertIn("data-ohne-suche", feld.field.widget.attrs, feld.name)
        self.assertNotIn("data-ohne-suche", formular["q"].field.widget.attrs)
        self.assertIn('data-ohne-suche=""', str(formular["status"]))

    def test_die_gliederung_zaehlt_zu_den_auswahlfeldern(self):
        self.assertEqual(
            [feld.name for feld in Filterform().auswahlfelder], ["status", "gliederung"]
        )
        self.assertEqual(
            [feld.name for feld in Filterform(gliederbar=False).auswahlfelder], ["status"]
        )


class Spaltenform(ListenFilterForm):
    """A list with columns whose search knows tiers."""

    SPALTEN = {"nummer": (_("Nr."), ("nummer",)), "name": (_("Name"), ("name",))}
    STANDARD_SPALTE = "nummer"
    GLIEDERUNGEN = (("familie", _("Nach Familie"), ("familie",)),)

    q = forms.CharField(required=False, label=_("Suche"))

    def relevanz(self, begriff):
        return (Q(name__istartswith=begriff),)


class RelevanzDerSuche(SimpleTestCase):
    """When the hits of a search come by how well they match, and when by column."""

    def test_nur_beim_suchen_und_ohne_gewaehlte_spalte(self):
        faelle = (
            ("ohne Suche", {}, False),
            ("nur Leerzeichen sind keine Suche", {"q": "   "}, False),
            ("mit Suche", {"q": "limo"}, True),
            ("mit gewählter Spalte", {"q": "limo", "sortierung": "name"}, False),
            ("auch absteigend gewählt", {"q": "limo", "sortierung": "-nummer"}, False),
            ("eine unbekannte Spalte ist keine Wahl", {"q": "limo", "sortierung": "x"}, True),
            ("gegliedert", {"q": "limo", "gliederung": "familie"}, False),
        )
        for fall, daten, erwartet in faelle:
            with self.subTest(fall=fall):
                self.assertIs(Spaltenform(daten).nach_relevanz, erwartet)

    def test_eine_liste_ohne_stufen_sortiert_wie_immer(self):
        self.assertFalse(Filterform({"q": "limo"}).nach_relevanz)

    def test_der_rang_steht_vor_der_spalte(self):
        erste, *rest = Spaltenform({"q": "limo"}).reihenfolge()
        self.assertIn("CASE", str(erste.expression).upper())
        # The primary key closes every order ('Spaltensortierung.order_by').
        self.assertEqual([teil.expression.name for teil in rest], ["nummer", "pk"])

    def test_kein_kopf_zeigt_sich_als_sortiert(self):
        koepfe = Spaltenform({"q": "limo"}).spalten.kopf()
        self.assertEqual([kopf.aria_sort for kopf in koepfe.values()], ["none", "none"])
        # The first click sorts ascending, also on what would be the default.
        self.assertEqual(koepfe["nummer"].naechster, "nummer")

    def test_ohne_suche_zeigt_sich_die_vorgabe(self):
        koepfe = Spaltenform({}).spalten.kopf()
        self.assertEqual(koepfe["nummer"].aria_sort, "ascending")
        self.assertEqual(koepfe["nummer"].naechster, "-nummer")


class Zweitschluessel(SimpleTestCase):
    """Ties must not wander between pages."""

    SPALTEN = {"nummer": ("Nummer", ("artikelnummer",)), "name": ("Name", ("name",))}

    def test_der_primaerschluessel_schliesst_jede_ordnung(self):
        sortierung = Spaltensortierung("", self.SPALTEN, "nummer", ("artikelnummer",))
        letzte = sortierung.order_by()[-1]
        self.assertEqual(str(letzte.expression), str(F("pk")))
        self.assertFalse(letzte.descending)

    def test_auch_absteigend_und_nach_dem_zweitschluessel(self):
        sortierung = Spaltensortierung("-name", self.SPALTEN, "nummer", ("artikelnummer",))
        felder = [str(ausdruck.expression) for ausdruck in sortierung.order_by()]
        self.assertEqual(felder, [str(F("name")), str(F("artikelnummer")), str(F("pk"))])
