"""The admin configuration of the inventory workflow.

Checked is what carries logic of its own: that a closed inventory goes
away with its frozen results, but a single result row does not, and that a
count position is written under its lock and only where the page changed
it. Pure declarations like 'list_display' are already checked by Django's
system check.
"""

from datetime import date
from decimal import Decimal
from html.parser import HTMLParser

from django.test import TestCase
from django.urls import reverse

from accounts.models import CustomUser
from api.test_merge_split_race import abfragen_waehrend
from django_project.testing import inventur_anlegen
from inventur.test_merge_race import erste_abfrage
from katalog.models import (
    Artikel,
    Basiseinheit,
    Gebinde,
    Hausartikel,
    Ware,
    Warengruppe,
)
from organisation.models import Bereich, Haus, Ort

from .models import Inventur, Inventurergebnis, Wiegung, Zaehlposition

STICHTAG = date(2026, 1, 31)


class InventurLoeschen(TestCase):
    """The path through the admin interface, not only through the model."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = CustomUser.objects.create_superuser(
            username="ada", password="geheim", email="ada@example.org"
        )
        haus = Haus.objects.create(name="Kino Ost")
        ort = Ort.objects.create(
            name="Thekenfront", bereich=Bereich.objects.create(name="Theke", haus=haus)
        )
        ware = Ware.objects.create(
            name="Artikel C 0,33", basiseinheit=Basiseinheit.objects.get(name="Stück(e)")
        )
        gebinde = Gebinde.objects.create(
            ware=ware, name="Kasten", faktor_in_basiseinheit=Decimal("20")
        )
        artikel = Hausartikel.objects.create(
            haus=haus,
            ware=ware,
            warengruppe=Warengruppe.objects.create(name="Warengruppe G"),
            artikelnummer="101001",
            name="Artikel C 0,33 Flasche",
        )
        cls.inventur = inventur_anlegen(haus, stichtag=STICHTAG)
        cls.inventur.zaehlpositionen.create(
            ort=ort, artikel=artikel, gebinde=gebinde, eingabemenge=Decimal("2")
        )
        cls.inventur.zaehlung_starten()
        cls.inventur.abschliessen()

    def setUp(self):
        self.client.force_login(self.admin)

    def adresse(self, modell: str, *args) -> str:
        return reverse(f"admin:inventur_{modell}", args=args)

    def test_abgeschlossene_geht_samt_ergebnissen(self):
        """The frozen rows must not block the deletion.

        When collecting the cascade the admin asks for the delete permission on
        the result too. If it was 'False' there, it aborted with "no permission",
        and the inventory stayed.
        """
        self.assertEqual(Inventurergebnis.objects.count(), 1)
        adresse = self.adresse("inventur_delete", self.inventur.pk)
        self.assertEqual(self.client.get(adresse).status_code, 200)

        self.client.post(adresse, {"post": "yes"})
        self.assertEqual(Inventur.objects.count(), 0)
        self.assertEqual(Inventurergebnis.objects.count(), 0)

    def test_auch_ueber_die_massenaktion_der_inventurliste(self):
        """The action "Delete selected inventories" collects the same cascade."""
        antwort = self.client.post(
            self.adresse("inventur_changelist"),
            {
                "action": "delete_selected",
                "_selected_action": [self.inventur.pk],
                "post": "yes",
            },
            follow=True,
        )
        self.assertEqual(antwort.status_code, 200)
        self.assertEqual(Inventur.objects.count(), 0)
        self.assertEqual(Inventurergebnis.objects.count(), 0)

    def test_einzelne_ergebniszeile_bleibt(self):
        """Otherwise gaps would open in the frozen state."""
        ergebnis = Inventurergebnis.objects.get()
        antwort = self.client.get(self.adresse("inventurergebnis_delete", ergebnis.pk))
        self.assertEqual(antwort.status_code, 403)
        self.assertEqual(Inventurergebnis.objects.count(), 1)

    def test_massenaktion_der_ergebnisliste_gibt_es_nicht(self):
        """Not via the detour "Delete selected" in the list either."""
        antwort = self.client.post(
            self.adresse("inventurergebnis_changelist"),
            {"action": "delete_selected", "_selected_action": [Inventurergebnis.objects.get().pk]},
            follow=True,
        )
        self.assertEqual(antwort.status_code, 200)
        self.assertEqual(Inventurergebnis.objects.count(), 1)


class _Formular(HTMLParser):
    """What a browser sends from the form with this id: every value of every name.

    Hidden fields too, ticked boxes only, the chosen options of a selection or
    its first one, the text of a text area. Nothing of what is disabled. No
    buttons: the test names the one it presses. A selection of several
    counters sends each of them, and so does the hidden field beside it with
    what the page showed.
    """

    def __init__(self, kennung: str):
        super().__init__()
        self.kennung = kennung
        self.drin = False
        self.daten: dict[str, list[str]] = {}
        # The open selection: its name, whether it takes several, its options.
        self.auswahl: str | None = None
        self.mehrere = False
        self.optionen: list[tuple[str, bool]] = []
        # The open text area and its text.
        self.textfeld: str | None = None
        self.text: list[str] = []

    def dazu(self, name: str, wert: str) -> None:
        self.daten.setdefault(name, []).append(wert)

    def handle_starttag(self, tag, attrs):
        werte = dict(attrs)
        if tag == "form":
            self.drin = werte.get("id") == self.kennung
            return
        if not self.drin:
            return
        if tag == "option":
            if self.auswahl and "disabled" not in werte:
                self.optionen.append((werte.get("value") or "", "selected" in werte))
            return
        if not (name := werte.get("name")) or "disabled" in werte:
            return
        if tag == "select":
            self.auswahl, self.mehrere, self.optionen = name, "multiple" in werte, []
        elif tag == "textarea":
            self.textfeld, self.text = name, []
        elif tag == "input":
            art = werte.get("type") or "text"
            if art in ("checkbox", "radio"):
                if "checked" in werte:
                    self.dazu(name, werte.get("value") or "on")
            elif art not in ("submit", "button", "reset", "image", "file"):
                self.dazu(name, werte.get("value") or "")

    def handle_data(self, data):
        if self.textfeld is not None:
            self.text.append(data)

    def handle_endtag(self, tag):
        if tag == "select" and self.auswahl is not None:
            gewaehlt = [wert for wert, ja in self.optionen if ja]
            # A single selection without a chosen option sends its first one.
            if not gewaehlt and not self.mehrere and self.optionen:
                gewaehlt = [self.optionen[0][0]]
            for wert in gewaehlt:
                self.dazu(self.auswahl, wert)
            self.auswahl = None
        elif tag == "textarea" and self.textfeld is not None:
            # The browser drops the line break right after the opening tag.
            text = "".join(self.text)
            text = text[2:] if text.startswith("\r\n") else text.removeprefix("\n")
            self.dazu(self.textfeld, text)
            self.textfeld = None
        elif tag == "form":
            self.drin = False


def formular(antwort, kennung: str) -> dict[str, list[str]]:
    """The fields of the form 'kennung' on this page, as the browser would send them."""
    leser = _Formular(kennung)
    leser.feed(antwort.content.decode())
    assert leser.daten, f"kein Formular {kennung} auf der Seite"
    return leser.daten


class ZaehlpositionInDerVerwaltung(TestCase):
    """A count position written in the administration: locked, read anew, only what changed.

    The counting app writes the same rows, and a merge or a split of a product
    hangs them on other packs. Until 10/2026 the administration saved the
    whole row as its page had shown it: a quantity the app sent in the
    meantime went back, a row the app had removed came back from the list.
    """

    @classmethod
    def setUpTestData(cls):
        cls.admin = CustomUser.objects.create_superuser(
            username="ada", password="geheim", email="ada@example.org"
        )
        haus = Haus.objects.create(name="Kino Ost")
        cls.ort = Ort.objects.create(
            name="Thekenfront", bereich=Bereich.objects.create(name="Theke", haus=haus)
        )
        ware = Ware.objects.create(
            name="Artikel C 0,33", basiseinheit=Basiseinheit.objects.get(name="Stück(e)")
        )
        cls.kasten = Gebinde.objects.create(
            ware=ware, name="Kasten", faktor_in_basiseinheit=Decimal("20")
        )
        cls.sixpack = Gebinde.objects.create(
            ware=ware, name="Sixpack", faktor_in_basiseinheit=Decimal("6")
        )
        cls.artikel = Hausartikel.objects.create(
            haus=haus,
            ware=ware,
            warengruppe=Warengruppe.objects.create(name="Warengruppe G"),
            artikelnummer="101001",
            name="Artikel C 0,33 Flasche",
        )
        cls.inventur = inventur_anlegen(haus, stichtag=STICHTAG)
        cls.kastenzeile = cls.inventur.zaehlpositionen.create(
            ort=cls.ort, artikel=cls.artikel, gebinde=cls.kasten, eingabemenge=Decimal("2")
        )
        cls.sixpackzeile = cls.inventur.zaehlpositionen.create(
            ort=cls.ort, artikel=cls.artikel, gebinde=cls.sixpack, eingabemenge=Decimal("1")
        )
        cls.inventur.zaehlung_starten()

    def setUp(self):
        self.client.force_login(self.admin)

    def adresse(self, zeile: Zaehlposition) -> str:
        return reverse("admin:inventur_zaehlposition_change", args=[zeile.pk])

    def seite(self, zeile: Zaehlposition) -> dict[str, list[str]]:
        return formular(self.client.get(self.adresse(zeile)), "zaehlposition_form")

    @staticmethod
    def wie_die_app(zeile: Zaehlposition, **felder) -> Zaehlposition:
        """Writes the way the counting app does: its own columns and the timestamp."""
        gelesen = Zaehlposition.objects.get(pk=zeile.pk)
        for name, wert in felder.items():
            setattr(gelesen, name, wert)
        gelesen.save(update_fields=[*felder, "zuletzt_geaendert"])
        return gelesen

    def test_eine_menge_der_app_von_danach_bleibt_stehen(self):
        """The page was open while the app counted. Unticking 'aktiv' wrote the old 2 back."""
        daten = self.seite(self.kastenzeile)
        vorher = self.wie_die_app(self.kastenzeile, eingabemenge=Decimal("5")).zuletzt_geaendert
        del daten["aktiv"]

        antwort = self.client.post(self.adresse(self.kastenzeile), {**daten, "_save": "1"})

        self.assertEqual(antwort.status_code, 302)
        zeile = Zaehlposition.objects.get(pk=self.kastenzeile.pk)
        self.assertEqual(zeile.eingabemenge, Decimal("5"))
        self.assertFalse(zeile.aktiv)
        # The app fetches what changed since its last state, the review sees a new state.
        self.assertGreater(zeile.zuletzt_geaendert, vorher)

    def test_was_die_seite_aendert_wird_geschrieben(self):
        daten = self.seite(self.kastenzeile)
        vorher = Zaehlposition.objects.get(pk=self.kastenzeile.pk).zuletzt_geaendert

        self.client.post(
            self.adresse(self.kastenzeile), {**daten, "eingabemenge": "4", "_save": "1"}
        )

        zeile = Zaehlposition.objects.get(pk=self.kastenzeile.pk)
        self.assertEqual((zeile.eingabemenge, zeile.aktiv), (Decimal("4"), True))
        self.assertGreater(zeile.zuletzt_geaendert, vorher)

    def test_die_liste_schreibt_nur_den_haken_der_umgelegt_wurde(self):
        """The app removed the other row meanwhile. Until 10/2026 saving the list revived it."""
        liste = reverse("admin:inventur_zaehlposition_changelist")
        daten = formular(self.client.get(liste), "changelist-form")
        zeilen = {daten[f"form-{stelle}-id"][0]: stelle for stelle in range(2)}
        self.wie_die_app(self.sixpackzeile, aktiv=False)
        del daten[f"form-{zeilen[str(self.kastenzeile.pk)]}-aktiv"]

        antwort = self.client.post(liste, {**daten, "_save": "1"})

        self.assertEqual(antwort.status_code, 302)
        self.assertFalse(Zaehlposition.objects.get(pk=self.kastenzeile.pk).aktiv)
        self.assertFalse(Zaehlposition.objects.get(pk=self.sixpackzeile.pk).aktiv)

    def pruefe_reihenfolge(self, abfragen, *modelle) -> None:
        """Each model locked after the one before it, the inventory and the rest without key."""
        stellen = [erste_abfrage(abfragen, modell, gesperrt=True) for modell in modelle]
        self.assertEqual(stellen, sorted(stellen), [modell.__name__ for modell in modelle])
        for modell, stelle in zip(modelle, stellen, strict=True):
            _art, _sperre, ohne_schluessel, reihenfolge, sql = abfragen[stelle]
            self.assertEqual(reihenfolge, ("pk",), sql)
            self.assertNotIn("JOIN", sql.upper().split(" WHERE ", 1)[0], sql)
            if modell is not Zaehlposition:
                self.assertTrue(ohne_schluessel, f"FOR UPDATE statt FOR NO KEY UPDATE: {sql}")

    def test_die_zeile_ist_gesperrt_bevor_sie_gelesen_wird(self):
        """The inventory without key, then the row, then the read. Written: the changed column."""
        daten = self.seite(self.kastenzeile)
        del daten["aktiv"]

        abfragen = abfragen_waehrend(
            lambda: self.client.post(self.adresse(self.kastenzeile), {**daten, "_save": "1"})
        )

        self.pruefe_reihenfolge(abfragen, Inventur, Zaehlposition)
        gelesen = next(
            stelle
            for stelle, (art, _s, _o, _r, sql) in enumerate(abfragen)
            if art is Zaehlposition and sql.startswith("SELECT") and '"eingabemenge"' in sql
        )
        self.assertLess(erste_abfrage(abfragen, Zaehlposition, gesperrt=True), gelesen)
        (geschrieben,) = [
            sql
            for art, *_rest, sql in abfragen
            if art is Zaehlposition and sql.startswith("UPDATE")
        ]
        self.assertIn('"aktiv"', geschrieben)
        self.assertIn('"zuletzt_geaendert"', geschrieben)
        self.assertNotIn('"eingabemenge"', geschrieben)
        self.assertNotIn('"gebinde_id"', geschrieben)

    def test_die_liste_sperrt_ihre_zeilen_bevor_sie_sie_liest(self):
        liste = reverse("admin:inventur_zaehlposition_changelist")
        daten = formular(self.client.get(liste), "changelist-form")
        zeilen = {daten[f"form-{stelle}-id"][0]: stelle for stelle in range(2)}
        del daten[f"form-{zeilen[str(self.kastenzeile.pk)]}-aktiv"]

        abfragen = abfragen_waehrend(lambda: self.client.post(liste, {**daten, "_save": "1"}))

        self.pruefe_reihenfolge(abfragen, Inventur, Zaehlposition)
        gelesen = next(
            stelle
            for stelle, (art, _s, _o, _r, sql) in enumerate(abfragen)
            if art is Zaehlposition and sql.startswith("SELECT") and '"eingabemenge"' in sql
        )
        self.assertLess(erste_abfrage(abfragen, Zaehlposition, gesperrt=True), gelesen)

    def test_eine_neue_zeile_sperrt_inventur_ort_und_artikel_zuerst(self):
        """As a new row of the app does: in this order and without key, then read and written."""
        Zaehlposition.objects.filter(pk=self.sixpackzeile.pk).delete()
        neu = reverse("admin:inventur_zaehlposition_add")
        daten = {
            **formular(self.client.get(neu), "zaehlposition_form"),
            "inventur": str(self.inventur.pk),
            "ort": str(self.ort.pk),
            "artikel": str(self.artikel.pk),
            "gebinde": str(self.sixpack.pk),
            "eingabemenge": "3",
            "_save": "1",
        }

        antworten = []
        abfragen = abfragen_waehrend(lambda: antworten.append(self.client.post(neu, daten)))

        self.assertEqual(antworten[0].status_code, 302)
        self.pruefe_reihenfolge(abfragen, Inventur, Ort, Artikel)
        eingefuegt = next(
            stelle
            for stelle, (art, _s, _o, _r, sql) in enumerate(abfragen)
            if art is Zaehlposition and sql.startswith("INSERT")
        )
        self.assertLess(erste_abfrage(abfragen, Artikel, gesperrt=True), eingefuegt)
        self.assertEqual(Zaehlposition.objects.get(gebinde=self.sixpack).eingabemenge, Decimal("3"))

    def test_das_loeschen_sperrt_inventur_und_zeile_bevor_django_nachsieht(self):
        """Before Django collects the weighings that go along ('VerwaltungSperrtZuerst')."""
        adresse = reverse("admin:inventur_zaehlposition_delete", args=[self.sixpackzeile.pk])

        abfragen = abfragen_waehrend(lambda: self.client.post(adresse, {"post": "yes"}))

        self.pruefe_reihenfolge(abfragen, Inventur, Zaehlposition)
        self.assertLess(
            erste_abfrage(abfragen, Zaehlposition, gesperrt=True), erste_abfrage(abfragen, Wiegung)
        )
        self.assertFalse(Zaehlposition.objects.filter(pk=self.sixpackzeile.pk).exists())
