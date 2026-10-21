"""Load every page once, through the URL resolver, not through a list of names.

The page tests of the apps check what a page *does*. That it exists at all
and that it loads was checked by nobody for some: "Add user" in the
administration threw a 500 until someone opened it in the browser, and the
lists of the inventory raw data were loaded by no test until 09/2026. Here
the list of pages comes from 'get_resolver()': **a new address is included by
itself**, and one without test data turns the test red instead of silently
missing.

Checked per address: no error status, and no more queries than 'HOECHSTENS'.
The second catches the gross N+1. Whether a list grows *with the rows* is
measured by 'katalog/test_views_queries.py' and 'inventur/test_queries.py'.
Plus the sticky bars every page should have (see 'leisten_pruefen').
"""

import re
from datetime import date
from decimal import Decimal
from http import HTTPStatus

from django.conf import settings
from django.contrib import admin
from django.contrib.auth.tokens import default_token_generator
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import URLPattern, URLResolver, get_resolver, reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from accounts.email_change import token as email_token
from accounts.models import CustomUser, Passkey
from django_project.middleware import freie_praefixe
from django_project.testing import inventur_anlegen, monatsinventur
from inventur.models import Wiegung
from katalog.models import (
    Basiseinheit,
    Behaelter,
    Erwartung,
    ErwartungGebinde,
    Gebinde,
    Hausartikel,
    Inventurart,
    Rezepturartikel,
    Rezepturkomponente,
    Ware,
    Warenfamilie,
    Warengruppe,
)
from organisation.models import Bereich, Erscheinungsbild, Haus, Ort

from .testing import AngemeldeteSeiten

#: Reference point from the measurement of 2026-09-18: the pages average
#: seven queries, the most expensive honest one 39.
HOECHSTENS = 40

#: 200 the page, 302 an intended redirect (start page, PDF of a running
#: inventory), 405 a button that only accepts POST. Everything else is a finding.
IN_ORDNUNG = {200, 302, 405}

#: The heading of a page. Login and block pages carry smaller ones and need
#: no sticky bar, because they are short.
TITEL = re.compile(r'<h[12] class="h3[ "]')

#: The button by which one recognises a form that can also be left.
ABBRECHEN = re.compile(r">\s*Abbrechen\s*</a>")

#: A decimal as '<input type="number">'. Django gives a number field 'step'
#: only for decimals, so the page number and other whole numbers stay allowed.
DEZIMALZAHL_ALS_ZAHLENFELD = re.compile(r'<input\b(?=[^>]*\btype="number")(?=[^>]*\bstep=)[^>]*>')

#: Deliberately not loaded, with a reason.
AUSGELASSEN = {
    "logout": "meldet ab",
    "admin:logout": "meldet ab",
    "pwa": "liefert die gebaute Zähl-App aus, siehe 'ZaehlAppAusliefern'",
    "pwa_manifest": "braucht den Build der Zähl-App, siehe 'ZaehlAppAusliefern'",
    "admin:view_on_site": "leitet auf fremde Modelle um",
    "admin:backup_einspielen": "braucht eine Datei im Backup-Ordner, siehe 'test_admin_site'",
    "admin:autocomplete": "antwortet ohne Angabe des Feldes mit 403",
    "admin:login": "ist für Angemeldete gesperrt",
}


#: What a visitor without an account may open: signing in and its ways back,
#: the invitation and the confirmation of a new address, the texts every person
#: may read, the icons, the decoy under '/admin/' and the sign-in of the app.
#: The POST-only ones answer a GET with 405, which still counts as open here.
OFFEN = {
    "login",
    "password_reset",
    "password_reset_done",
    "password_reset_confirm",
    "password_reset_complete",
    "passkey_anmelden",
    "passkey_anmelden_optionen",
    "einladung",
    "einladung_fertig",
    "email_bestaetigen",
    "email_bestaetigt",
    "datenschutz",
    "impressum",
    "sprachwahl",
    "icon",
    "honeypot:index",
    "honeypot:login",
    "honeypot:passkey_anmelden",
    "honeypot:passkey_optionen",
    "token_holen",
    "token_erneuern",
    "token_pruefen",
    # Blocks a refresh token by its id, without a sign-in ('abmelden_nachgeholt').
    "abmelden_nachgeholt_api",
    "passkey_anmelden_api",
    "passkey_anmelden_optionen_api",
}


def muster(resolver: URLResolver | None = None, vorsatz: str = "", raum: str = ""):
    """Every named pattern as (name, path pattern)."""
    for eintrag in (resolver or get_resolver()).url_patterns:
        if isinstance(eintrag, URLResolver):
            tiefer = f"{raum}{eintrag.namespace}:" if eintrag.namespace else raum
            yield from muster(eintrag, vorsatz + str(eintrag.pattern), tiefer)
        elif isinstance(eintrag, URLPattern) and eintrag.name:
            yield f"{raum}{eintrag.name}", vorsatz + str(eintrag.pattern)


class JedeSeiteLaedt(AngemeldeteSeiten):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.passkey = Passkey.objects.create(
            benutzer=cls.user,
            kennung="pruefkennung",
            oeffentlicher_schluessel=b"\x00",
            name="Laptop",
        )
        # The legal notice exists only with a text. The shipped version has none.
        fassung = Erscheinungsbild.standard().kopie("Mit Impressum")
        fassung.impressum = "Kino Nord GmbH, Musterweg 1, 10101 Musterstadt"
        fassung.save()
        fassung.aktivieren()
        stueck = Basiseinheit.objects.get(name="Stück(e)")
        cls.haus = Haus.objects.create(name="Kino Ost")
        cls.bereich = Bereich.objects.create(name="Theke", haus=cls.haus)
        cls.ort = Ort.objects.create(name="Thekenfront", bereich=cls.bereich)
        cls.warengruppe = Warengruppe.objects.create(name="Warengruppe G")
        cls.warenfamilie = Warenfamilie.objects.create(name="Warenfamilie M", leiteinheit=stueck)
        cls.behaelter = Behaelter.objects.create(name="Schale", tara=Decimal("250"))
        cls.inventurart = Inventurart.objects.create(name="Wochenzählung Theke")
        cls.ware = Ware.objects.create(
            name="Artikel C 0,33", basiseinheit=stueck, warenfamilie=cls.warenfamilie
        )
        cls.gebinde = Gebinde.objects.create(
            ware=cls.ware, name="Kasten", faktor_in_basiseinheit=Decimal("20")
        )
        cls.hausartikel = Hausartikel.objects.create(
            haus=cls.haus,
            ware=cls.ware,
            warengruppe=cls.warengruppe,
            artikelnummer="101001",
            name="Artikel C 0,33 Flasche",
            # In a type of its own, so the pages also show an inventory of two types.
            inventurart=cls.inventurart,
        )
        cls.rezepturartikel = Rezepturartikel.objects.create(
            name="Mischung", basiseinheit=Basiseinheit.objects.get(name="g")
        )
        Rezepturkomponente.objects.create(
            rezepturartikel=cls.rezepturartikel,
            ware=cls.ware,
            menge_in_basiseinheit=Decimal("1"),
        )
        erwartung = Erwartung.objects.create(ort=cls.ort, artikel=cls.hausartikel)
        ErwartungGebinde.objects.create(erwartung=erwartung, gebinde=cls.gebinde)
        Erwartung.objects.create(ort=cls.ort, artikel=cls.rezepturartikel)

        cls.inventur = inventur_anlegen(
            cls.haus, monatsinventur(), cls.inventurart, stichtag=date(2026, 1, 31)
        )
        cls.inventur.zaehlpositionen_anlegen()
        cls.inventur.zaehlung_starten()
        cls.zaehlposition = cls.inventur.zaehlpositionen.get(artikel=cls.rezepturartikel)
        cls.wiegung = Wiegung.objects.create(
            zaehlposition=cls.zaehlposition,
            brutto_gramm=Decimal("900"),
            behaelter=Behaelter.ohne_behaelter(),
        )

    #: Whose number a '<pk>' means is said by the start of the URL name.
    ABWEICHEND = {
        "zaehlliste": "ort",
        "erwartung_umschalten": "ort",
        "gebinde_sortieren": "ware",
        "inventur-detail": "inventur",
        "inventur-stammdaten": "inventur",
        "pruefliste_zeile": "inventur",
        "differenz_von_hand_api": "inventur",
        "sperre_api": "inventur",
        "zaehlposition-detail": "zaehlposition",
        "wiegung-detail": "wiegung",
    }

    #: A list that answers only with a filter, and whose number the filter
    #: takes. Without one it answers 400 ('api.views.ZaehlpositionViewSet.list',
    #: 'WiegungViewSet.list').
    FILTER = {
        "zaehlposition-list": ("inventur", "inventur"),
        "wiegung-list": ("zaehlposition", "zaehlposition"),
    }

    def adresse(self, name: str, pfad: str) -> str | None:
        """The address for the pattern, or None if there is no test data for it."""
        parameter = re.findall(r"<(?:\w+:)?(\w+)>|\(\?P<(\w+)>", pfad)
        namen = [a or b for a, b in parameter]
        angaben: dict[str, object] = {}
        for parameter_name in namen:
            if parameter_name == "version":
                angaben["version"] = "v1"
            elif parameter_name == "uidb64":
                angaben["uidb64"] = urlsafe_base64_encode(force_bytes(self.user.pk))
            elif parameter_name == "token" and name == "email_bestaetigen":
                # The link of a new address carries the address itself.
                angaben["token"] = email_token(self.user, "neu@example.org")
            elif parameter_name == "token":
                angaben["token"] = default_token_generator.make_token(self.user)
            elif parameter_name == "name":
                # The icons: without an own logo they point to the placeholder.
                angaben["name"] = "logo"
            elif parameter_name == "hausartikel":
                # A row of the app's check list: the article within the inventory.
                angaben["hausartikel"] = self.hausartikel.pk
            elif parameter_name == "stichtag":
                # The run: the cut-off date of the one inventory, without a number.
                angaben["stichtag"] = self.inventur.stichtag.isoformat()
            elif parameter_name in ("pk", "haus_pk", "ort_pk"):
                art = self.ABWEICHEND.get(name) or (
                    parameter_name.removesuffix("_pk")
                    if parameter_name != "pk"
                    else re.split(r"[_-]", name)[0]
                )
                satz = getattr(self, art, None)
                if satz is None:
                    return None
                angaben[parameter_name] = satz.pk
            else:
                return None
        adresse = reverse(name, kwargs=angaben)
        if (filter_ := self.FILTER.get(name)) is not None:
            feld, art = filter_
            adresse = f"{adresse}?{feld}={getattr(self, art).pk}"
        return adresse

    def laden(self, adresse: str):
        with CaptureQueriesContext(connection) as gezaehlt:
            antwort = self.client.get(adresse)
        # Django 6.1 relabels the empty choice of a selection list, and the German
        # language pack does not know the text yet. It stood in English on every
        # page with a selection list. 'VerwaltungsAdminConfig.ready' sets it in German.
        if antwort.status_code == HTTPStatus.OK and "html" in antwort.headers.get(
            "Content-Type", ""
        ):
            seite = antwort.content.decode()
            self.assertNotIn("Select an option", seite, adresse)
            # Decimals are text fields ('Dezimalfeld'). In a number field the
            # browser decides about the comma: Chrome with an English
            # interface refused "1,5" before the server ever saw it.
            self.assertEqual(DEZIMALZAHL_ALS_ZAHLENFELD.findall(seite), [], adresse)
        return antwort.status_code, len(gezaehlt)

    def leisten_pruefen(self, adresse: str):
        """Title and buttons stay put when scrolling, not only on the lists.

        On a long detail page, heading and "Edit" had otherwise vanished at the
        top and "Save" was only reachable after a long scroll.
        """
        antwort = self.client.get(adresse)
        if antwort.status_code != HTTPStatus.OK or "html" not in antwort.headers.get(
            "Content-Type", ""
        ):
            return
        seite = antwort.content.decode()
        if TITEL.search(seite):
            self.assertIn('class="kopfleiste ', seite, f"{adresse}: Kopf klebt nicht")
        if ABBRECHEN.search(seite):
            self.assertIn('class="fussleiste ', seite, f"{adresse}: Knöpfe kleben nicht")

    def test_jede_eigene_seite(self):
        ohne_daten = []
        geladen = 0
        for name, pfad in muster():
            if name in AUSGELASSEN or name.startswith("admin") or "format" in pfad:
                continue
            adresse = self.adresse(name, pfad)
            if adresse is None:
                ohne_daten.append(name)
                continue
            geladen += 1
            with self.subTest(seite=name):
                status, abfragen = self.laden(adresse)
                self.assertIn(status, IN_ORDNUNG, adresse)
                self.assertLessEqual(abfragen, HOECHSTENS, adresse)
                self.leisten_pruefen(adresse)
        # If the resolver suddenly found nothing, the test would be green and worthless.
        self.assertGreaterEqual(geladen, 90, "Der Resolver liefert kaum noch Seiten.")
        self.assertEqual(
            ohne_daten,
            [],
            "Für diese Muster kennt der Test keine Daten. In 'setUpTestData' oder "
            "'ABWEICHEND' ergänzen, oder mit Grund in 'AUSGELASSEN' eintragen.",
        )

    def geschuetzte_adressen(self) -> list[tuple[str, str]]:
        """Every own page the block closes: everything outside 'freie_praefixe'."""
        frei = freie_praefixe()
        adressen = []
        for name, pfad in muster():
            if name in AUSGELASSEN or name.startswith("admin") or "format" in pfad:
                continue
            adresse = self.adresse(name, pfad)
            if adresse is not None and not adresse.startswith(frei):
                adressen.append((name, adresse))
        # If the list ran empty, both tests below would be green and worthless.
        self.assertGreaterEqual(len(adressen), 80, "Kaum noch geschützte Seiten gefunden.")
        return adressen

    def test_ohne_anmeldung_ist_nur_offen_was_offen_sein_soll(self):
        """Without an account only 'OFFEN' answers, every other page wants a sign-in.

        The block lets through only 'freie_praefixe', and below them every page
        guards itself. A new page that forgets that, or a prefix that grows too
        broad, would open it to everybody. Until 10/2026 only an audit noticed
        that. A page that is meant to be open goes into 'OFFEN', on purpose.
        """
        self.client.logout()
        offen = set()
        for name, pfad in muster():
            if name in AUSGELASSEN or name.startswith("admin") or "format" in pfad:
                continue
            adresse = self.adresse(name, pfad)
            if adresse is None:
                continue
            antwort = self.client.get(adresse)
            zur_anmeldung = antwort.status_code == HTTPStatus.FOUND and antwort[
                "Location"
            ].startswith(str(settings.LOGIN_URL))
            abgewiesen = adresse.startswith("/api/") and antwort.status_code in (
                HTTPStatus.UNAUTHORIZED,
                HTTPStatus.FORBIDDEN,
            )
            if not (zur_anmeldung or abgewiesen):
                offen.add(name)
        self.assertEqual(offen, OFFEN)
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, HTTPStatus.NOT_FOUND)

    def test_zaehlende_landen_auf_jeder_geschuetzten_seite_auf_der_sperrseite(self):
        """A counter is signed in, but the web interface is not theirs."""
        adressen = self.geschuetzte_adressen()
        zaehler = CustomUser.objects.create_user("zaehlt", "zaehlt@example.org", "x-geheim-1")
        self.client.force_login(zaehler)
        sperrseite = reverse("kein_zugriff")
        for name, adresse in adressen:
            with self.subTest(seite=name):
                antwort = self.client.get(adresse)
                self.assertEqual(antwort.status_code, HTTPStatus.FOUND, adresse)
                self.assertEqual(antwort["Location"], sperrseite, adresse)
        verwaltung = self.client.get(reverse("admin:index"))
        self.assertEqual(verwaltung["Location"], sperrseite)

    def test_jede_seite_der_verwaltung(self):
        """List, create and change per registered model, a 403 where it is locked."""
        self.client.get(reverse("admin:index"))  # fills the content types cache
        self.assertGreaterEqual(len(admin.site._registry), 15)
        for modell in admin.site._registry:
            kennung = f"{modell._meta.app_label}_{modell._meta.model_name}"
            seiten = [reverse(f"admin:{kennung}_changelist"), reverse(f"admin:{kennung}_add")]
            erster = modell._default_manager.order_by("pk").first()
            if erster is not None:
                seiten += [
                    reverse(f"admin:{kennung}_{art}", args=[erster.pk])
                    for art in ("change", "history", "delete")
                ]
            for adresse in seiten:
                with self.subTest(seite=adresse):
                    status, abfragen = self.laden(adresse)
                    self.assertIn(status, IN_ORDNUNG | {403}, adresse)
                    self.assertLessEqual(abfragen, HOECHSTENS, adresse)

    def test_startseite_und_anwendungen_der_verwaltung(self):
        for adresse in (
            reverse("admin:index"),
            reverse("admin:app_list", kwargs={"app_label": "inventur"}),
            reverse("admin:password_change"),
            reverse("admin:jsi18n"),
        ):
            with self.subTest(seite=adresse):
                self.assertEqual(self.client.get(adresse).status_code, 200)

    def test_konten_kommen_in_den_daten_vor(self):
        """Otherwise 'user_detail' and relatives would run into a foreign account and fail."""
        self.assertTrue(CustomUser.objects.filter(pk=self.user.pk).exists())
