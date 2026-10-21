"""The addresses of the web interface follow the server's language.

On a German server it is '/haeuser/', on an English one '/sites/'. The names
of the addresses stay the same, and what the counting app or Django know for
certain stays the same in every language. The whole piece is checked:
patterns, 'reverse', 'resolve', the middleware's block and the answer to the
app.
"""

import importlib
from contextlib import contextmanager

from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import Resolver404, clear_url_caches, resolve, reverse

from accounts.models import CustomUser
from api.serializers import IchSerializer
from django_project.testing import administrator

from . import routes
from .middleware import freie_praefixe

#: In this order: the root includes the apps, the anonymous version the
#: root.
URL_MODULE = (
    "accounts.urls",
    "organisation.urls",
    "katalog.urls",
    "inventur.urls",
    "django_project.urls",
    "django_project.urls_anonymous",
)


def adressen_neu_laden() -> None:
    """The patterns are created at load time. Another language needs a fresh load."""
    for name in URL_MODULE:
        importlib.reload(importlib.import_module(name))
    clear_url_caches()


@contextmanager
def serversprache(sprache: str):
    """The addresses as a server with this 'LANGUAGE_CODE' would have them."""
    with override_settings(LANGUAGE_CODE=sprache):
        adressen_neu_laden()
        try:
            yield
        finally:
            pass
    adressen_neu_laden()


class DieUebersetzungDerMuster(SimpleTestCase):
    def test_deutsch_bleibt_wie_es_ist(self):
        self.assertEqual(
            routes.uebersetzt("haeuser/<int:pk>/edit/", "de"), "haeuser/<int:pk>/edit/"
        )

    def test_englisch_uebersetzt_segment_fuer_segment(self):
        self.assertEqual(routes.uebersetzt("haeuser/<int:pk>/edit/", "en"), "sites/<int:pk>/edit/")
        self.assertEqual(routes.uebersetzt("datenschutz/", "en"), "privacy/")
        self.assertEqual(
            routes.uebersetzt("inventuren/lauf/<str:stichtag>/pdf/", "en"),
            "inventories/run/<str:stichtag>/pdf/",
        )

    def test_ein_leeres_muster_bleibt_leer(self):
        self.assertEqual(routes.uebersetzt("", "en"), "")

    def test_ein_unbekanntes_segment_bricht_beim_laden(self):
        """In every language, so it does not stay behind on the German server."""
        for sprache in ("de", "en"):
            with self.subTest(sprache=sprache), self.assertRaises(LookupError):
                routes.uebersetzt("unbekannt/<int:pk>/", sprache)

    def test_kein_englisches_wort_steht_fuer_zwei_deutsche(self):
        """Two identical targets would be the same address on the English server."""
        englisch = list(routes.SEGMENTE.values())
        self.assertEqual(len(englisch), len(set(englisch)))

    def test_die_englischen_segmente_sind_adressen(self):
        for wort in routes.SEGMENTE.values():
            with self.subTest(wort=wort):
                self.assertRegex(wort, r"^[a-z]+(-[a-z]+)*$")


class DerEnglischeServer(TestCase):
    def test_die_adressen_heissen_englisch(self):
        with serversprache("en"):
            self.assertEqual(reverse("haus_list"), "/sites/")
            self.assertEqual(reverse("datenschutz"), "/privacy/")
            self.assertEqual(reverse("kein_zugriff"), "/no-access/")
            self.assertEqual(reverse("sprachwahl"), "/users/language/")
            self.assertEqual(
                reverse("hausartikel_list", kwargs={"haus_pk": 3}),
                "/catalogue/sites/3/articles/",
            )
            self.assertEqual(
                reverse("inventur_zaehlung_starten", kwargs={"pk": 5}),
                "/inventories/5/start-counting/",
            )

    def test_die_namen_bleiben_und_die_deutsche_adresse_gibt_es_nicht(self):
        """Templates and help texts only know the name."""
        with serversprache("en"):
            self.assertEqual(resolve("/sites/").url_name, "haus_list")
            self.assertEqual(resolve("/privacy/").url_name, "datenschutz")
            with self.assertRaises(Resolver404):
                resolve("/haeuser/")

    def test_danach_ist_wieder_deutsch(self):
        with serversprache("en"):
            pass
        self.assertEqual(reverse("haus_list"), "/haeuser/")
        self.assertEqual(resolve("/datenschutz/").url_name, "datenschutz")

    def test_app_schnittstelle_und_django_behalten_ihre_adressen(self):
        """The counting app is built with '/api/' and knows nothing of the language."""
        with serversprache("en"):
            self.assertEqual(reverse("ich", args=["v1"]), "/api/v1/ich/")
            self.assertEqual(reverse("pwa"), "/app/")
            self.assertEqual(reverse("login"), "/accounts/login/")
            self.assertEqual(reverse("icon", kwargs={"name": "logo"}), "/icons/logo.png")

    def test_die_sperre_laesst_die_freien_seiten_in_jeder_sprache_durch(self):
        zaehler = CustomUser.objects.create_user(username="zaehler", password="geheim")
        self.client.force_login(zaehler)
        self.assertEqual(self.client.get("/datenschutz/").status_code, 200)
        with serversprache("en"):
            self.assertIn("/privacy/", freie_praefixe())
            self.assertIn("/no-access/", freie_praefixe())
            self.assertIn("/users/language/", freie_praefixe())
            self.assertNotIn("/datenschutz/", freie_praefixe())
            self.assertEqual(self.client.get("/privacy/").status_code, 200)
            self.assertEqual(self.client.get("/no-access/").status_code, 200)
            # The web interface stays closed for counters, even under a new name.
            self.assertRedirects(self.client.get("/sites/"), "/no-access/")

    def test_die_seiten_verlinken_in_der_sprache_des_servers(self):
        """A German account on the English server gets English addresses."""
        self.client.force_login(administrator(sprache="de"))
        with serversprache("en"):
            antwort = self.client.get("/sites/")
            self.assertEqual(antwort.status_code, 200)
            self.assertContains(antwort, 'href="/privacy/"')
            self.assertContains(antwort, 'href="/inventories/"')
            self.assertEqual(antwort.headers["Content-Language"], "de")

    def test_die_zaehl_app_erfaehrt_die_adresse_des_datenschutzes(self):
        """It is built with the German path and cannot know the server's language."""
        konto = administrator()
        self.assertEqual(IchSerializer(konto).data["datenschutz_adresse"], "/datenschutz/")
        with serversprache("en"):
            self.assertEqual(IchSerializer(konto).data["datenschutz_adresse"], "/privacy/")
