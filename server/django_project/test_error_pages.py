"""The error pages wear the application's dress.

Until 10/2026 only the 404 did. A server error, a refused request, a missing
permission and a failed CSRF check answered with Django's bare English
pages, also for a German account. The three views below provoke the cases.
"""

from django.core.exceptions import PermissionDenied, SuspiciousOperation
from django.test import Client, TestCase, override_settings
from django.urls import include, path, reverse

from django_project.testing import fuehrungskraft


def stolpert(request):
    raise RuntimeError("Probe")


def verboten(request):
    raise PermissionDenied


def verdaechtig(request):
    raise SuspiciousOperation("Probe")


urlpatterns = [
    path("probe/500/", stolpert),
    path("probe/403/", verboten),
    path("probe/400/", verdaechtig),
    path("", include("django_project.urls")),
]


@override_settings(ROOT_URLCONF="django_project.test_error_pages")
class Fehlerseiten(TestCase):
    def setUp(self):
        self.client.force_login(fuehrungskraft())
        # The exception is to arrive as a response, not in the test.
        self.client.raise_request_exception = False

    def test_der_serverfehler_traegt_das_kleid_der_anwendung(self):
        """Rendered without request and context processors, so 'base.html' must cope."""
        with self.assertLogs("django.request", level="ERROR"):
            antwort = self.client.get("/probe/500/")
        self.assertEqual(antwort.status_code, 500)
        self.assertContains(antwort, "Fehler 500", status_code=500)
        self.assertContains(antwort, "site.css", status_code=500)
        self.assertContains(antwort, reverse("login"), status_code=500)

    def test_kein_zugriff(self):
        antwort = self.client.get("/probe/403/")
        self.assertContains(antwort, "Fehler 403", status_code=403)
        self.assertContains(antwort, "fehlt die Berechtigung", status_code=403)
        self.assertContains(antwort, reverse("inventur_list"), status_code=403)

    def test_ungueltige_anfrage(self):
        with self.assertLogs("django.security.SuspiciousOperation", level="ERROR"):
            antwort = self.client.get("/probe/400/")
        self.assertContains(antwort, "Fehler 400", status_code=400)
        self.assertContains(antwort, "site.css", status_code=400)

    def test_abgelaufenes_formular(self):
        """Django finds '403_csrf.html' by its name. The text is ours and German."""
        streng = Client(enforce_csrf_checks=True)
        antwort = streng.post(reverse("login"), {"username": "x", "password": "y"})
        self.assertContains(antwort, "Das Formular ist abgelaufen", status_code=403)
        self.assertNotContains(antwort, "CSRF verification failed", status_code=403)
        self.assertContains(antwort, "site.css", status_code=403)
