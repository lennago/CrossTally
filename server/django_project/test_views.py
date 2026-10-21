"""The common base of the views, without a particular app behind it.

The buttons themselves are checked by the apps on their pages. Here stands
what applies to all: the return accepts no foreign targets, and a
'ValidationError' becomes a message instead of an error page.
"""

from django.contrib import messages
from django.contrib.messages import get_messages
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.backends.signed_cookies import SessionStore
from django.core.exceptions import ValidationError
from django.test import RequestFactory, SimpleTestCase

from .views import Zeilenaktion, zurueck_zu


def anfrage(**daten):
    request = RequestFactory().post("/irgendwo/", daten)
    request.session = SessionStore()
    # What the 'MessageMiddleware' otherwise attaches. The stubs do not know the attribute.
    setattr(request, "_messages", FallbackStorage(request))
    return request


class Ruecksprung(SimpleTestCase):
    def test_eigene_adresse_samt_filtern_wird_uebernommen(self):
        self.assertEqual(
            zurueck_zu(anfrage(weiter="/katalog/?q=artikel+c&page=3"), "/standard/"),
            "/katalog/?q=artikel+c&page=3",
        )

    def test_ohne_angabe_gilt_der_standard(self):
        self.assertEqual(zurueck_zu(anfrage(), "/standard/"), "/standard/")

    def test_fremde_ziele_werden_verworfen(self):
        """Otherwise every row button would be an open redirect."""
        for ziel in ("https://example.com/", "//example.com/", "javascript:alert(1)"):
            with self.subTest(ziel=ziel):
                self.assertEqual(zurueck_zu(anfrage(weiter=ziel), "/standard/"), "/standard/")


class _Satz:
    def get_absolute_url(self):
        return "/satz/"


class _Knopf(Zeilenaktion):
    def get_object(self, queryset=None):
        return _Satz()

    def ausfuehren(self, satz):
        if self.request.POST.get("kaputt"):
            raise ValidationError(["Geht nicht.", "Und das auch nicht."])
        messages.success(self.request, "Erledigt.")


class Knopfdruck(SimpleTestCase):
    def druecken(self, **daten):
        request = anfrage(**daten)
        antwort = _Knopf.as_view()(request)
        return antwort, [str(meldung) for meldung in get_messages(request)]

    def test_zurueck_zum_satz_wenn_die_zeile_nichts_mitschickt(self):
        antwort, meldungen = self.druecken()
        self.assertEqual(antwort["Location"], "/satz/")
        self.assertEqual(meldungen, ["Erledigt."])

    def test_zurueck_in_die_liste(self):
        antwort, _ = self.druecken(weiter="/liste/?page=2")
        self.assertEqual(antwort["Location"], "/liste/?page=2")

    def test_was_nicht_geht_wird_zur_meldung(self):
        antwort, meldungen = self.druecken(kaputt="1", weiter="/liste/")
        self.assertEqual(antwort.status_code, 302)
        self.assertEqual(antwort["Location"], "/liste/")
        self.assertEqual(meldungen, ["Geht nicht.", "Und das auch nicht."])
