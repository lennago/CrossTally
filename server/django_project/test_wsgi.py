"""The headers on what WhiteNoise serves: the test client never passes through here.

'Cache-Control', 'nosniff', 'Referrer-Policy', HSTS and the policy for
'/app/' and '/static/' are decided in 'wsgi.py', in front of Django. Until
10/2026 no test saw them, and a regression (a year of caching for the start
page, no policy on the service worker) would only have shown in operation.
"""

import tempfile
from pathlib import Path
from unittest import mock
from wsgiref.util import setup_testing_defaults

from django.conf import settings
from django.core.signals import request_finished, request_started
from django.db import close_old_connections
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.utils import translation
from django.utils.csp import build_policy

from .urls import pwa_start
from .wsgi import anwendung


def antwort(app, pfad: str) -> tuple[str, dict[str, str], bytes]:
    """One request through the WSGI application, as the application server makes it.

    Without the handler's connection housekeeping: 'close_old_connections'
    hangs on 'request_started' and 'request_finished' and closes the
    connection whose autocommit does not match, and inside a test's
    transaction that is the test's own. SQLite in memory ignores the close,
    PostgreSQL does not, and the CI runs on PostgreSQL. Django's test client
    unhooks it the same way.
    """
    environ: dict = {}
    setup_testing_defaults(environ)
    environ["PATH_INFO"] = pfad
    environ["HTTP_HOST"] = "testserver"
    status: dict[str, str] = {}
    kopfzeilen: dict[str, str] = {}

    def start_response(wert, zeilen, exc_info=None):
        status["wert"] = wert
        kopfzeilen.update(zeilen)

    request_started.disconnect(close_old_connections)
    request_finished.disconnect(close_old_connections)
    try:
        inhalt = b"".join(app(environ, start_response))
    finally:
        request_started.connect(close_old_connections)
        request_finished.connect(close_old_connections)
    return status["wert"], kopfzeilen, inhalt


class KopfzeilenDerDateien(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ordner = tempfile.TemporaryDirectory()
        wurzel = Path(cls.ordner.name)
        cls.static = wurzel / "staticfiles"
        cls.pwa = wurzel / "pwa"
        (cls.static).mkdir()
        (cls.pwa / "assets").mkdir(parents=True)
        (cls.static / "site.0123456789ab.css").write_text("body{}", encoding="utf-8")
        (cls.static / "site.css").write_text("body{}", encoding="utf-8")
        (cls.pwa / "sw.js").write_text("// worker", encoding="utf-8")
        (cls.pwa / "index.html").write_text(
            "<!doctype html><html><head><title>CrossTally</title></head></html>", encoding="utf-8"
        )
        (cls.pwa / "assets" / "index.0123456789ab.js").write_text("1", encoding="utf-8")
        cls.addClassCleanup(cls.ordner.cleanup)

    def anwendung(self):
        return anwendung(str(self.static), self.pwa)

    def test_der_service_worker_traegt_die_richtlinie_und_bleibt_kurz_im_cache(self):
        status, kopfzeilen, inhalt = antwort(self.anwendung(), "/app/sw.js")
        self.assertEqual(status, "200 OK")
        self.assertEqual(inhalt, b"// worker")
        self.assertEqual(kopfzeilen["Content-Security-Policy"], build_policy(settings.SECURE_CSP))
        self.assertEqual(kopfzeilen["X-Content-Type-Options"], "nosniff")
        self.assertEqual(kopfzeilen["Referrer-Policy"], settings.SECURE_REFERRER_POLICY)
        # A new version arrives only if the worker is fetched anew soon.
        self.assertEqual(kopfzeilen["Cache-Control"], "max-age=60, public")

    def test_dateien_mit_pruefsumme_im_namen_bleiben_ein_jahr(self):
        app = self.anwendung()
        for pfad in ("/static/site.0123456789ab.css", "/app/assets/index.0123456789ab.js"):
            with self.subTest(pfad):
                status, kopfzeilen, _inhalt = antwort(app, pfad)
                self.assertEqual(status, "200 OK")
                self.assertIn("immutable", kopfzeilen["Cache-Control"])
                self.assertIn("max-age=315360000", kopfzeilen["Cache-Control"])
                self.assertIn("Content-Security-Policy", kopfzeilen)

    def test_ohne_pruefsumme_gilt_die_minute(self):
        _status, kopfzeilen, _inhalt = antwort(self.anwendung(), "/static/site.css")
        self.assertEqual(kopfzeilen["Cache-Control"], "max-age=60, public")

    def test_die_startseite_der_app_kommt_von_django(self):
        """The built 'index.html' is not served: Django puts the name from the administration in.

        The view reads the start page from the build folder. On a fresh checkout
        and in the CI nothing is built there, so the test points it at its own.
        """
        app = self.anwendung()
        self.assertNotIn("/app/", app.files)
        self.assertNotIn("/app/index.html", app.files)
        with mock.patch("django_project.urls.PWA", self.pwa):
            status, kopfzeilen, inhalt = antwort(app, "/app/")
        self.assertEqual(status, "200 OK")
        self.assertIn(b"<html", inhalt.lower())
        self.assertIn("Content-Security-Policy", kopfzeilen)

    def test_was_es_nicht_gibt_faellt_zu_django_durch(self):
        status, _kopfzeilen, _inhalt = antwort(self.anwendung(), "/app/gibt-es-nicht.js")
        self.assertEqual(status, "404 Not Found")

    @override_settings(
        SECURE_HSTS_SECONDS=31536000, SECURE_HSTS_INCLUDE_SUBDOMAINS=True, SECURE_HSTS_PRELOAD=True
    )
    def test_hsts_wie_die_middleware_es_baut(self):
        _status, kopfzeilen, _inhalt = antwort(self.anwendung(), "/app/sw.js")
        self.assertEqual(
            kopfzeilen["Strict-Transport-Security"], "max-age=31536000; includeSubDomains; preload"
        )

    @override_settings(SECURE_HSTS_SECONDS=0)
    def test_ohne_hsts_keine_zeile(self):
        """Set here, not taken from the environment. Without 'DEBUG', as in CI, it is 300 s."""
        _status, kopfzeilen, _inhalt = antwort(self.anwendung(), "/app/sw.js")
        self.assertNotIn("Strict-Transport-Security", kopfzeilen)


class OhneBuild(SimpleTestCase):
    """Without a build '/app/' says how to make one, in the language of the request.

    A fresh checkout has no 'server/pwa/'. The hint names 'npm ci' like the
    README, which installs what the lock file names.
    """

    def setUp(self):
        leer = tempfile.TemporaryDirectory()
        self.addCleanup(leer.cleanup)
        ersatz = mock.patch("django_project.urls.PWA", Path(leer.name))
        ersatz.start()
        self.addCleanup(ersatz.stop)

    def test_der_hinweis_nennt_npm_ci(self):
        antwort = pwa_start(RequestFactory().get("/app/"))
        self.assertEqual(antwort.status_code, 404)
        self.assertIn("'npm ci'", antwort.content.decode())
        self.assertNotIn("npm install", antwort.content.decode())

    def test_auf_englisch(self):
        with translation.override("en"):
            antwort = pwa_start(RequestFactory().get("/app/"))
        self.assertEqual(antwort.status_code, 404)
        self.assertIn("has not been built yet", antwort.content.decode())
        self.assertIn("'npm ci'", antwort.content.decode())
