"""A large plan goes back in one piece, and one too large is refused before it is shown.

Every row of the plan travels back with the submit, because the server keeps
nothing between the two steps ('ImportZeileForm'). The server takes only so
many fields and bytes per request ('DATA_UPLOAD_MAX_NUMBER_FIELDS',
'DATA_UPLOAD_MAX_MEMORY_SIZE'). Until 10/2026 a plan of more than about 1,450
new articles was shown, and when it came back, after someone had gone through
it, Django answered with a bare 400. Of more than 2,000 rows the formset took
back only the first 2,000.

A plan of the full 3,000 rows takes seconds to plan and as long to write, too
long for the suite. So the parts are checked: how many fields a row sends at
most, read off the rendered page, that the settings take that many rows of
them, that Django parses such a body, and the whole way at a small limit with
the same arithmetic.
"""

import re
from html.parser import HTMLParser
from unittest import mock
from urllib.parse import urlencode

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import HttpResponseBase
from django.test import RequestFactory, override_settings
from django.urls import reverse

from django_project.testing import AngemeldeteSeiten
from organisation.models import Haus

from .forms import (
    FELDER_JE_PLANZEILE,
    HOECHSTENS_PLANZEILEN,
    ImportFormSet,
    ImportZeileForm,
    plan_zu_gross,
)
from .models import Artikelstatus, Basiseinheit, Gebinde, Hausartikel, Ware, Warengruppe
from .services import TEILE

KOPF = "artikelnummer;name;warengruppe;gebinde;anzahl;einheit;haus"

#: Six rows, one of every kind. 101004 is missing from the file.
DATEI = (
    # A conflict in all three parts: product group, unit (g instead of kg) and pack.
    "101001;Artikel K;Warengruppe H;Karton;5000;g;Kino Nord",
    # The number under another designation.
    "101002;Artikel U neu;Warengruppe G;Karton;24;Stück(e);Kino Nord",
    # Discontinued, can be brought back.
    "101003;Artikel E;Warengruppe G;Karton;24;Stück(e);Kino Nord",
    # New, its product exists already: the field shows that product's packs.
    "101005;Ware P;Warengruppe G;Karton;12;Stück(e);Kino Nord",
    # New, with a new product.
    "101006;Artikel N;Warengruppe G;Karton;24;Stück(e);Kino Nord",
)


class Browser(HTMLParser):
    """What a browser sends for each form of a page: every named field, a box only ticked.

    A select sends its chosen option or its first, a textarea its text
    without the first line break and with every line break as CR LF.
    """

    def __init__(self):
        super().__init__()
        self.formulare: list[list[tuple[str, str]]] = []
        self._paare: list[tuple[str, str]] | None = None
        self._text: list[str] | None = None
        self._wahl: dict[str, str | None] | None = None

    def handle_starttag(self, tag, attrs):
        werte = dict(attrs)
        if tag == "form":
            self._paare = []
            self.formulare.append(self._paare)
        name = werte.get("name")
        if self._paare is None or "disabled" in werte:
            return
        if tag == "input" and name:
            art = werte.get("type") or "text"
            if art in ("radio", "checkbox"):
                if "checked" in werte:
                    self._paare.append((name, werte.get("value") or "on"))
            elif art not in ("submit", "button", "reset", "file"):
                self._paare.append((name, werte.get("value") or ""))
        elif tag == "textarea" and name:
            self._text = [name, ""]
        elif tag == "select" and name:
            self._wahl = {"name": name, "erste": None, "gewaehlt": None}
        elif tag == "option" and self._wahl is not None:
            wert = werte.get("value") or ""
            if self._wahl["erste"] is None:
                self._wahl["erste"] = wert
            if "selected" in werte:
                self._wahl["gewaehlt"] = wert

    def handle_endtag(self, tag):
        if self._paare is None:
            return
        if tag == "form":
            self._paare = None
        elif tag == "textarea" and self._text is not None:
            name, text = self._text
            self._paare.append((name, text.removeprefix("\n").replace("\n", "\r\n")))
            self._text = None
        elif tag == "select" and self._wahl is not None:
            wahl = self._wahl
            wert = wahl["gewaehlt"] if wahl["gewaehlt"] is not None else wahl["erste"]
            if wert is not None and wahl["name"]:
                self._paare.append((str(wahl["name"]), wert))
            self._wahl = None

    def handle_data(self, data):
        if self._text is not None:
            self._text[1] += data


def gesendet(antwort: HttpResponseBase) -> list[tuple[str, str]]:
    """The plan as the browser sends it back, every open conflict decided with "Leave"."""
    browser = Browser()
    browser.feed(getattr(antwort, "content", b"").decode())
    # The page has other forms too: language, colours, signing out.
    paare = next(
        formular
        for formular in browser.formulare
        if any(name == "plan-TOTAL_FORMS" for name, _wert in formular)
    )
    entschieden = {name for name, _wert in paare if name.endswith("-vorgehen")}
    for name, wert in list(paare):
        vorgehen = name.removesuffix("-art") + "-vorgehen"
        if name.endswith("-art") and wert == "konflikt" and vorgehen not in entschieden:
            paare.append((vorgehen, "uebergehen"))
    return paare


def stelle(name: str) -> int | None:
    """The row a field belongs to, None for the page's own ('plan-TOTAL_FORMS', the token)."""
    treffer = re.fullmatch(r"plan-(\d+)-.+", name)
    return int(treffer.group(1)) if treffer else None


def je_zeile(paare: list[tuple[str, str]]) -> dict[int, list[tuple[str, str]]]:
    zeilen: dict[int, list[tuple[str, str]]] = {}
    for name, wert in paare:
        if (nummer := stelle(name)) is not None:
            zeilen.setdefault(nummer, []).append((name.split("-", 2)[2], wert))
    return zeilen


def seitenfelder(paare: list[tuple[str, str]]) -> list[tuple[str, str]]:
    return [(name, wert) for name, wert in paare if stelle(name) is None]


def vervielfacht(paare: list[tuple[str, str]], zeile: list[tuple[str, str]], anzahl: int) -> str:
    """The page's own fields and 'zeile' 'anzahl' times, numbered like a plan of that many rows."""
    eigene = [
        (name, str(anzahl) if name.endswith(("TOTAL_FORMS", "INITIAL_FORMS")) else wert)
        for name, wert in seitenfelder(paare)
    ]
    zeilen = [(f"plan-{nummer}-{feld}", wert) for nummer in range(anzahl) for feld, wert in zeile]
    return urlencode(eigene + zeilen)


def gelesen(rumpf: str):
    """Django's own reading of a submit, with the limits of the settings."""
    anfrage = RequestFactory().post("/", rumpf, content_type="application/x-www-form-urlencoded")
    return anfrage.POST


class Grundlage(AngemeldeteSeiten):
    """Kino Nord carries four articles. The file brings a row of every kind for them."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.haus = Haus.objects.create(name="Kino Nord")
        stueck = Basiseinheit.objects.get(name="Stück(e)")
        gruppe = Warengruppe.objects.create(name="Warengruppe G")
        kilo = Basiseinheit.objects.get(name="kg")
        ware_k = Ware.objects.create(name="Ware K", basiseinheit=kilo)
        Gebinde.objects.create(ware=ware_k, name="Sack", faktor_in_basiseinheit=25)
        for nummer, name, ware, status in (
            ("101001", "Artikel K", ware_k, Artikelstatus.AKTIV),
            ("101002", "Artikel U", None, Artikelstatus.AKTIV),
            ("101003", "Artikel E", None, Artikelstatus.EINGESTELLT),
            ("101004", "Artikel F", None, Artikelstatus.AKTIV),
        ):
            Hausartikel.objects.create(
                haus=cls.haus,
                ware=ware or Ware.objects.create(name=name, basiseinheit=stueck),
                warengruppe=gruppe,
                artikelnummer=nummer,
                name=name,
                status=status,
            )
        ware_p = Ware.objects.create(name="Ware P", basiseinheit=stueck)
        Gebinde.objects.create(ware=ware_p, name="Kasten", faktor_in_basiseinheit=24)

    @property
    def adresse(self) -> str:
        return reverse("hausartikel_import", args=[self.haus.pk])

    def hochladen(self, *zeilen: str):
        datei = SimpleUploadedFile(
            "export.csv", "\n".join([KOPF, *zeilen]).encode("utf-8"), "text/csv"
        )
        return self.client.post(self.adresse, {"datei": datei, "format": "winticket"})

    def plan(self):
        antwort = self.hochladen(*DATEI)
        self.assertTemplateUsed(antwort, "katalog/import_plan.html")
        return antwort


class FelderJeZeile(Grundlage):
    def test_die_obergrenze_folgt_aus_dem_formular(self):
        """The hidden ones, the ones to fill in, the procedure, and the most a row chooses besides.

        A field more in the form means a field more per row, and the settings
        have to grow with it ('DATA_UPLOAD_MAX_NUMBER_FIELDS').
        """
        waehlbar = ("vorgehen", "status_neu", "uebernehmen")
        auszufuellen = [
            name
            for name in ImportZeileForm().fields
            if name not in ImportZeileForm.FESTE_FELDER and name not in waehlbar
        ]
        self.assertEqual(
            FELDER_JE_PLANZEILE,
            len(ImportZeileForm.FESTE_FELDER) + len(auszufuellen) + 1 + max(1, len(TEILE)),
        )

    def test_keine_zeile_der_seite_sendet_mehr(self):
        zeilen = je_zeile(gesendet(self.plan()))

        self.assertEqual(len(zeilen), 6)
        for felder in zeilen.values():
            with self.subTest(felder[0]):
                self.assertLessEqual(len(felder), FELDER_JE_PLANZEILE)
        # The conflict sends all three of its parts, the replacing row its status.
        namen = {nummer: [name for name, _wert in felder] for nummer, felder in zeilen.items()}
        self.assertEqual(max(felder.count("uebernehmen") for felder in namen.values()), 3)
        self.assertTrue(any("status_neu" in felder for felder in namen.values()))

    def test_ein_leeres_verstecktes_feld_bleibt_von_der_seite(self):
        """A new article with a new product: twelve fields instead of seventeen (10/2026)."""
        neu = next(
            felder
            for felder in je_zeile(gesendet(self.plan())).values()
            if ("artikelnummer", "101006") in felder
        )
        namen = [name for name, _wert in neu]

        self.assertEqual(len(neu), 12)
        for leer in ("vorhanden_pk", "abweichungen", "gebinde_von", "gebinde_faktor"):
            self.assertNotIn(leer, namen)


class DerGroesstePlan(Grundlage):
    def test_die_einstellungen_fassen_ihn(self):
        """As many rows as a plan may have, each with as many fields as a row sends at most."""
        eigene = seitenfelder(gesendet(self.plan()))

        self.assertGreaterEqual(
            settings.DATA_UPLOAD_MAX_NUMBER_FIELDS,
            HOECHSTENS_PLANZEILEN * FELDER_JE_PLANZEILE + len(eigene),
        )

    def test_django_liest_ihn_aus_der_reichsten_zeile_der_seite(self):
        """The conflict with its hints, as often as a plan may have rows: fields and bytes fit."""
        paare = gesendet(self.plan())
        reichste = max(je_zeile(paare).values(), key=lambda felder: len(urlencode(felder)))

        daten = gelesen(vervielfacht(paare, reichste, HOECHSTENS_PLANZEILEN))

        self.assertEqual(len(daten.getlist(f"plan-{HOECHSTENS_PLANZEILEN - 1}-art")), 1)

    def test_ein_plan_von_1600_neuen_artikeln_kommt_durch(self):
        """Until 10/2026 Django refused it: 17 fields per row, 27,207 against 25,000."""
        paare = gesendet(self.plan())
        neu = next(felder for felder in je_zeile(paare).values() if ("art", "neu") in felder)

        daten = gelesen(vervielfacht(paare, neu, 1600))

        self.assertEqual(daten.get("plan-1599-art"), "neu")

    def test_das_formset_nimmt_so_viele_zeilen_zurueck(self):
        """Django's default took back 2,000 and showed only those again."""
        formset = ImportFormSet(
            {
                "plan-TOTAL_FORMS": str(HOECHSTENS_PLANZEILEN),
                "plan-INITIAL_FORMS": str(HOECHSTENS_PLANZEILEN),
                "plan-MIN_NUM_FORMS": "0",
                "plan-MAX_NUM_FORMS": str(HOECHSTENS_PLANZEILEN),
            },
            prefix="plan",
        )

        self.assertEqual(formset.total_form_count(), HOECHSTENS_PLANZEILEN)

    @mock.patch("katalog.forms.imports.HOECHSTENS_PLANZEILEN", 6)
    def test_bis_zur_grenze_geht_er_durch_und_wird_geschrieben(self):
        """At a limit of six rows and with the settings computed as for the real one."""
        paare = gesendet(self.plan())
        grenze = 6 * FELDER_JE_PLANZEILE + len(seitenfelder(paare))

        with override_settings(DATA_UPLOAD_MAX_NUMBER_FIELDS=grenze):
            antwort = self.client.post(
                self.adresse, urlencode(paare), content_type="application/x-www-form-urlencoded"
            )

        self.assertRedirects(antwort, self.haus.get_absolute_url())
        self.assertTrue(Hausartikel.objects.filter(artikelnummer="101006").exists())
        self.assertEqual(Hausartikel.objects.get(artikelnummer="101005").ware.name, "Ware P")

    @mock.patch("katalog.forms.imports.HOECHSTENS_PLANZEILEN", 5)
    def test_eine_zeile_mehr_wird_vor_dem_zeigen_abgewiesen(self):
        antwort = self.hochladen(*DATEI)

        self.assertTemplateUsed(antwort, "katalog/import.html")
        self.assertTemplateNotUsed(antwort, "katalog/import_plan.html")
        self.assertContains(
            antwort,
            "Der Plan hätte 6 Zeilen. In einem Zug lassen sich höchstens 5 übernehmen. "
            "Verteile die Warengruppen bitte auf mehrere Exporte und lies sie nacheinander "
            "ein. Eine Warengruppe, die in einer Datei fehlt, bleibt dabei unberührt.",
        )
        self.assertFalse(Hausartikel.objects.filter(artikelnummer="101006").exists())

    def test_mit_der_echten_grenze(self):
        zeile = {"artikelnummer": "101001", "name": "Ware N", "art": "neu"}

        self.assertIsNone(plan_zu_gross([zeile] * HOECHSTENS_PLANZEILEN))
        self.assertEqual(
            plan_zu_gross([zeile] * (HOECHSTENS_PLANZEILEN + 1)),
            f"Der Plan hätte {HOECHSTENS_PLANZEILEN + 1} Zeilen. In einem Zug lassen sich "
            f"höchstens {HOECHSTENS_PLANZEILEN} übernehmen. Verteile die Warengruppen bitte auf "
            "mehrere Exporte und lies sie nacheinander ein. Eine Warengruppe, die in einer Datei "
            "fehlt, bleibt dabei unberührt.",
        )

    def test_was_der_server_nicht_laese_wird_vor_dem_zeigen_abgewiesen(self):
        """One byte less than the browser would send for the unchanged plan, and it is refused."""
        rumpf = urlencode(gesendet(self.plan()))

        with override_settings(DATA_UPLOAD_MAX_MEMORY_SIZE=len(rumpf) - 1):
            antwort = self.hochladen(*DATEI)

        self.assertTemplateNotUsed(antwort, "katalog/import_plan.html")
        self.assertContains(antwort, "Der Plan wäre zu groß, um ihn in einem Zug zu übernehmen.")

    def test_die_hilfe_nennt_die_grenze(self):
        antwort = self.client.get(self.adresse)

        self.assertContains(
            antwort, f"Ein Plan hat höchstens <strong>{HOECHSTENS_PLANZEILEN} Zeilen"
        )
