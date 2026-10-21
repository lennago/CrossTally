"""The help sidebar: where there is a text, the button appears, otherwise not.

The texts live under 'templates/help/<language>/' and find their page through
the URL name. A file without a page would never be seen by anyone. That is
why a test checks every language folder. That every text also renders is
shown by the page tests of the apps: they open every page as administrator,
and the bar is attached to every one.
"""

import re
from pathlib import Path
from types import SimpleNamespace

from django.conf import settings
from django.template.loader import render_to_string
from django.test import SimpleTestCase, override_settings
from django.urls import get_resolver, reverse

from accounts.models import CustomUser
from organisation.models import Haus

from .help import DATEINAMEN, FORMULARENDUNGEN, GRUNDSPRACHE, kandidaten
from .testing import AngemeldeteSeiten

ORDNER = Path(settings.BASE_DIR) / "templates" / "help"

#: The console backend: the mail would only end in the log.
NUR_LOG = {"default": {"BACKEND": "django.core.mail.backends.console.EmailBackend"}}
SPRACHEN = [kuerzel for kuerzel, _name in settings.LANGUAGES]


def url_namen() -> set[str]:
    return {name for name in get_resolver().reverse_dict if isinstance(name, str)}


class Dateien(SimpleTestCase):
    def test_jede_datei_gehoert_zu_einer_seite(self):
        namen = url_namen()
        erreichbar = {Path(pfad).stem for name in namen for pfad in kandidaten(name)}
        for sprache in SPRACHEN:
            for datei in (ORDNER / sprache).glob("*.html"):
                with self.subTest(sprache=sprache, datei=datei.name):
                    self.assertIn(datei.stem, erreichbar)

    def test_jede_sprache_hat_ihren_ordner(self):
        for sprache in SPRACHEN:
            with self.subTest(sprache=sprache):
                self.assertTrue((ORDNER / sprache).is_dir())

    def test_deutsch_hat_jeden_text(self):
        """The fallback needs it. What is missing in German is missing everywhere."""
        deutsch = {datei.name for datei in (ORDNER / GRUNDSPRACHE).glob("*.html")}
        for sprache in SPRACHEN:
            fremde = {datei.name for datei in (ORDNER / sprache).glob("*.html")}
            with self.subTest(sprache=sprache):
                self.assertLessEqual(fremde, deutsch)

    def test_abweichende_dateinamen_zeigen_auf_vorhandene_seiten(self):
        self.assertLessEqual(set(DATEINAMEN), url_namen())

    def test_anlegen_und_bearbeiten_teilen_sich_einen_text(self):
        for endung in FORMULARENDUNGEN:
            self.assertEqual(
                kandidaten(f"behaelter{endung}"),
                [f"help/de/behaelter{endung}.html", "help/de/behaelter_form.html"],
            )

    def test_die_fremde_sprache_kommt_vor_dem_rueckfall(self):
        self.assertEqual(
            kandidaten("behaelter_list", "en-gb"),
            ["help/en/behaelter_list.html", "help/de/behaelter_list.html"],
        )


class Seitenleiste(AngemeldeteSeiten):
    def test_seite_mit_text_zeigt_knopf_und_leiste(self):
        antwort = self.client.get(reverse("behaelter_add"))
        self.assertContains(antwort, 'data-bs-target="#hilfe"')
        self.assertContains(antwort, 'id="hilfe"')
        self.assertContains(antwort, "Leergewicht des Behälters")

    def test_der_text_kennt_den_kontext_der_seite(self):
        haus = Haus.objects.create(name="Kino Ost")
        antwort = self.client.get(reverse("hausartikel_list", args=[haus.pk]))
        self.assertContains(antwort, "Was Kino Ost führt, mit der Nummer")

    def test_seite_ohne_text_zeigt_keinen_knopf(self):
        antwort = self.client.get(reverse("password_change"))
        self.assertNotContains(antwort, 'data-bs-target="#hilfe"')
        self.assertNotContains(antwort, 'id="hilfe"')

    def test_wer_die_leiste_nicht_sieht_bekommt_auch_keine_hilfe(self):
        """Counters land on the block page. Without a bar there would be no button."""
        zaehler = CustomUser.objects.create_user(username="zaehler", email="z@example.org")
        self.client.force_login(zaehler)
        antwort = self.client.get(reverse("kein_zugriff"))
        self.assertNotContains(antwort, 'id="hilfe"')


class ArtenDerInventurImText(SimpleTestCase):
    """The types an inventory counts never stand where the text speaks of those it does not.

    'arten_anzeige' names the counted ones. Behind "… this inventory does not count" it
    read as the opposite: "does not count (Cup inventory)" for a cup inventory (until 10/2026).
    """

    TEXTE = ("inventur_closing", "sollbestand", "inventur_detail")
    #: What a placeholder must not follow.
    VERKEHRT = re.compile(r"(nicht zählt|does not count|mehrere Arten|several types)\s*\(")

    def gerendert(self, sprache: str, name: str) -> str:
        haus = SimpleNamespace(name="Kino Nord")
        inventur = SimpleNamespace(haus=haus, arten_anzeige="Becherinventur")
        text = render_to_string(f"help/{sprache}/{name}.html", {"inventur": inventur})
        return " ".join(text.split())

    def test_die_gezaehlten_arten_heissen_gezaehlt(self):
        for sprache in SPRACHEN:
            for name in self.TEXTE:
                with self.subTest(sprache=sprache, text=name):
                    text = self.gerendert(sprache, name)
                    self.assertIn("Becherinventur", text)
                    self.assertIsNone(self.VERKEHRT.search(text))

    def test_der_abschluss_nennt_sie_als_die_arten_der_inventur(self):
        self.assertIn(
            "zu keiner der Arten, die diese Inventur zählt (Becherinventur)",
            self.gerendert("de", "inventur_closing"),
        )
        self.assertIn(
            "none of the types this inventory counts (Becherinventur)",
            self.gerendert("en", "inventur_closing"),
        )


class SucheDerPruefliste(SimpleTestCase):
    """The review list searches neither the EAN nor the family ('PrueflisteFilterForm').

    The shared paragraph of the other lists promised both (until 10/2026).
    """

    def test_die_hilfe_verspricht_nur_was_die_suche_kann(self):
        for sprache, gruppe in (("de", "Warengruppe"), ("en", "article group")):
            with self.subTest(sprache=sprache):
                text = render_to_string(f"help/{sprache}/inventur_review.html", {})
                self.assertIn(gruppe, text)
                self.assertNotIn("EAN", text)


class OhneMailserverKeinWortVonMails(AngemeldeteSeiten):
    """Whoever reads the help should not look for a button the page does not show."""

    def hilfe(self, name: str) -> str:
        """Only the sidebar: the form itself rightly asks for an e-mail address."""
        seite = self.client.get(reverse(name)).content.decode()
        treffer = re.search(r'<div class="offcanvas-body hilfe">(.*?)</div>\s*</div>', seite, re.S)
        assert treffer is not None, name
        return treffer.group(1)

    def test_mit_mailserver_erklaert_die_hilfe_einladung_und_benachrichtigung(self):
        text = self.hilfe("user_add")
        self.assertIn("Einladung oder Passwort", text)
        self.assertIn("Passwort selbst vergeben", text)
        self.assertIn("Mailtexte", text)
        self.assertIn(
            "Jeder neue und jeder entfernte Passkey kommt als E-Mail.", self.hilfe("passkey_list")
        )

    @override_settings(MAILERS=NUR_LOG, DEBUG=False)
    def test_ohne_mailserver_schweigt_sie_davon(self):
        text = self.hilfe("user_add")
        self.assertIn("Erstes Passwort", text)
        for satz in ("Einladung", "E-Mail", "Passwort vergessen?", "Benachrichtigung"):
            with self.subTest(satz=satz):
                self.assertNotIn(satz, text)
        self.assertNotIn("als E-Mail", self.hilfe("passkey_list"))
