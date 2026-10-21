"""The Content-Security-Policy and what it presupposes.

The policy does without a nonce because no script is in a page any more.
That is a property of the templates that nobody has in view while writing a
new one: a single inline script would silently run into nothing in the
browser, and no page test would see it. That is why the search here goes
over **all** templates instead of individual pages.
"""

import re
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils.csp import CSP, build_policy

from accounts.models import CustomUser

from .testing import administrator

KOPFZEILE = str(CSP.HEADER_ENFORCE)

#: A <script> without 'src'. Data blocks ('application/json') are not executed
#: by the browser. The policy does not block them.
INLINE_SKRIPT = re.compile(r"<script\b(?![^>]*\bsrc=)(?![^>]*application/json)[^>]*>", re.I)
#: 'onclick=' and relatives. 'script-src' without 'unsafe-inline' blocks them too.
EREIGNISATTRIBUT = re.compile(r"<[^>]*\son[a-z]+\s*=", re.I)
#: A <style> in the page. The PDF is exempt: WeasyPrint reads that, not a browser.
INLINE_STIL = re.compile(r"<style\b", re.I)
#: A 'style' attribute on any element.
STIL_AM_ELEMENT = re.compile(r"<[a-zA-Z][^<>]*\sstyle\s*=", re.I)


def eigene_vorlagen() -> list[Path]:
    """All templates of the project, the shared ones and those of our own apps."""
    ordner = [Path(pfad) for pfad in settings.TEMPLATES[0]["DIRS"]]
    ordner += [
        Path(app.path) / "templates"
        for app in apps.get_app_configs()
        if Path(app.path).is_relative_to(settings.BASE_DIR) and ".venv" not in Path(app.path).parts
    ]
    return sorted(datei for pfad in ordner if pfad.is_dir() for datei in pfad.rglob("*.html"))


class Richtlinie(SimpleTestCase):
    def test_skripte_nur_von_hier(self):
        """Whoever relaxes here switches off the protection. A test should turn that red."""
        self.assertEqual(settings.SECURE_CSP["script-src"], [CSP.SELF])
        self.assertEqual(settings.SECURE_CSP["default-src"], [CSP.SELF])
        self.assertEqual(settings.SECURE_CSP["object-src"], [CSP.NONE])

    def test_stile_in_der_seite_bleiben_gesperrt(self):
        """Neither <style> nor 'style' attributes. The former relaxation is gone."""
        self.assertEqual(settings.SECURE_CSP["style-src"], [CSP.SELF])
        self.assertNotIn("style-src-attr", settings.SECURE_CSP)
        self.assertNotIn("unsafe-inline", build_policy(settings.SECURE_CSP))

    def test_ohne_nonce(self):
        """WhiteNoise serves the counting app past the middleware. Nobody set it there."""
        for direktive, werte in settings.SECURE_CSP.items():
            with self.subTest(direktive=direktive):
                self.assertNotIn(CSP.NONCE, werte)


class Vorlagen(SimpleTestCase):
    def test_es_gibt_sie(self):
        """Otherwise the search below would find nothing and always be green."""
        self.assertGreater(len(eigene_vorlagen()), 100)

    def test_kein_skript_steht_in_der_seite(self):
        for vorlage in eigene_vorlagen():
            text = vorlage.read_text(encoding="utf-8")
            with self.subTest(vorlage=str(vorlage.relative_to(settings.BASE_DIR))):
                self.assertIsNone(INLINE_SKRIPT.search(text), "Skripte gehören nach static/js/.")
                self.assertIsNone(
                    EREIGNISATTRIBUT.search(text),
                    "Ereignisse hängt ein Skript an, nicht ein Attribut (siehe confirm.js).",
                )

    def test_kein_stylesheet_steht_in_der_seite(self):
        for vorlage in eigene_vorlagen():
            if "pdf" in vorlage.parts:
                continue
            with self.subTest(vorlage=str(vorlage.relative_to(settings.BASE_DIR))):
                self.assertIsNone(
                    INLINE_STIL.search(vorlage.read_text(encoding="utf-8")),
                    "Stile gehören nach static/site.css.",
                )

    def test_kein_stil_steht_am_element(self):
        """A 'style' attribute is blocked by the policy too.

        That happens silently, the column would then just be narrow.
        """
        for vorlage in eigene_vorlagen():
            if "pdf" in vorlage.parts:
                continue
            with self.subTest(vorlage=str(vorlage.relative_to(settings.BASE_DIR))):
                self.assertIsNone(
                    STIL_AM_ELEMENT.search(vorlage.read_text(encoding="utf-8")),
                    "Breiten sind Klassen in static/site.css ('breite-7', 'max-breite-30').",
                )

    def test_jede_benutzte_breite_hat_ihre_klasse(self):
        """A class without a rule goes unnoticed. The column is then simply a different width."""
        stylesheet = (settings.BASE_DIR / "static" / "site.css").read_text(encoding="utf-8")
        definiert = set(
            re.findall(r"^\.((?:max-|min-)?(?:breite|hoehe)-[0-9-]+) \{", stylesheet, re.M)
        )
        benutzt: set[str] = set()
        for vorlage in eigene_vorlagen():
            text = vorlage.read_text(encoding="utf-8")
            benutzt |= set(re.findall(r"\b((?:max-|min-)?(?:breite|hoehe)-[0-9][0-9-]*)\b", text))
            # Column headers name their width as a value: breite="8rem" becomes 'breite-8'.
            benutzt |= {f"breite-{wert}" for wert in re.findall(r'breite="([0-9]+)rem"', text)}
        self.assertGreater(len(benutzt), 30)
        self.assertEqual(benutzt - definiert, set())

    def test_die_startseite_der_zaehl_app_ebenso(self):
        startseite = settings.BASE_DIR.parent / "app" / "index.html"
        if not startseite.exists():
            self.skipTest("Ohne 'app/' (im Image) gibt es nichts zu prüfen.")
        # Comments there explain why no script is in the page.
        text = re.sub(r"<!--.*?-->", "", startseite.read_text(encoding="utf-8"), flags=re.S)
        self.assertIsNone(INLINE_SKRIPT.search(text))


class Antworten(TestCase):
    """The header is attached to everything Django answers, not only to pages."""

    @classmethod
    def setUpTestData(cls):
        cls.chef = administrator(email="chef@example.com")
        cls.zaehler = CustomUser.objects.create_user(
            username="zaehler", password="geheim", email="zaehler@example.com"
        )

    def test_seiten_anmeldung_verwaltung_und_api(self):
        self.client.force_login(self.chef)
        adressen = {
            "Seite": reverse("inventur_list"),
            "Verwaltung": reverse("admin:index"),
            "Köder": "/admin/",
            "API": reverse("ich", args=["v1"]),
            "gibt es nicht": "/gibt-es-nicht/",
        }
        for name, adresse in adressen.items():
            with self.subTest(name):
                antwort = self.client.get(adresse, follow=True)
                self.assertEqual(antwort[KOPFZEILE], build_policy(settings.SECURE_CSP))

    def test_auch_abgemeldet(self):
        antwort = self.client.get(reverse("login"))
        self.assertEqual(antwort[KOPFZEILE], build_policy(settings.SECURE_CSP))

    def test_auch_an_der_umleitung_zur_sperrseite(self):
        """'StaffOnlyMiddleware' gives that itself. The policy therefore sits above it."""
        self.client.force_login(self.zaehler)
        antwort = self.client.get(reverse("inventur_list"))
        self.assertEqual(antwort.status_code, 302)
        self.assertIn(KOPFZEILE, antwort)


class Dateien(SimpleTestCase):
    def test_whitenoise_haengt_dieselbe_richtlinie_an(self):
        """The middleware never sees its responses, not even the start page of the counting app."""
        # Builds the whole application at load time.
        from .wsgi import mit_richtlinie  # noqa: PLC0415

        kopfzeilen: dict[str, str] = {}
        mit_richtlinie(kopfzeilen, "/irgendwo/pwa/index.html", "/app/")
        self.assertEqual(kopfzeilen[KOPFZEILE], build_policy(settings.SECURE_CSP))

    def test_und_was_sonst_die_securitymiddleware_setzt(self):
        """'/app/' and '/static/' bypass it. Without this 'nosniff' would be missing there."""
        from .wsgi import mit_richtlinie  # noqa: PLC0415

        kopfzeilen: dict[str, str] = {}
        mit_richtlinie(kopfzeilen, "/irgendwo/pwa/index.html", "/app/")
        self.assertEqual(kopfzeilen["X-Content-Type-Options"], "nosniff")
        self.assertEqual(kopfzeilen["Referrer-Policy"], settings.SECURE_REFERRER_POLICY)

    def test_nur_dateien_mit_pruefsumme_im_namen_gelten_als_unveraenderlich(self):
        """Otherwise the browser asks for everything anew every minute, or keeps 'sw.js' forever."""
        from .wsgi import ist_unveraenderlich  # noqa: PLC0415

        faelle = {
            "/static/site.2223d855119a.css": True,
            "/static/vendor/bootstrap/bootstrap.min.0a1b2c3d4e5f.css": True,
            "/app/assets/index-Cl_9qjiS.js": True,
            "/static/site.css": False,
            "/app/": False,
            "/app/sw.js": False,
            "/app/index.html": False,
            "/app/manifest.webmanifest": False,
            "/app/theme.js": False,
        }
        for adresse, erwartet in faelle.items():
            with self.subTest(adresse=adresse):
                self.assertEqual(ist_unveraenderlich("/egal", adresse), erwartet)
