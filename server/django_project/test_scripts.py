"""The website's scripts ask the server in the background through one helper.

'imHintergrund' in 'static/js/shared.js' marks such a request as a script's
('X-Requested-With: fetch'), and the server answers it with a status where it
would otherwise lead to the sign-in ('im_hintergrund'). A script that called
'fetch' itself could forget the mark: its save would then count as done after
the session had expired, because the sign-in page answers 200. That is why
the search here goes over all scripts, as 'test_csp.py' does over the
templates.
"""

import re

from django.conf import settings
from django.test import SimpleTestCase
from django.urls import reverse

from .testing import AngemeldeteSeiten, skript

#: A call of 'fetch'. A feature check ('window.fetch') calls nothing.
AUFRUF = re.compile(r"\bfetch\s*\(")

#: Scripts that still ask by themselves.
SELBST = {
    # The helpers themselves.
    "shared.js",
    # In the head, before 'shared.js', and in the administration, which has
    # no 'shared.js'. It asks the API, and that knows no sign-in page.
    "theme.js",
}


def ohne_kommentare(quelle: str) -> str:
    """The code of a script: a word in a comment calls nothing."""
    ohne_block = re.sub(r"/\*.*?\*/", "", quelle, flags=re.S)
    return re.sub(r"^\s*//.*$", "", ohne_block, flags=re.M)


def skripte():
    return sorted((settings.BASE_DIR / "static" / "js").glob("*.js"))


class AnfragenImHintergrund(SimpleTestCase):
    def test_kein_skript_fragt_an_den_hilfen_vorbei(self):
        gefunden = [
            pfad.name
            for pfad in skripte()
            if pfad.name not in SELBST
            and AUFRUF.search(ohne_kommentare(pfad.read_text(encoding="utf-8")))
        ]
        self.assertEqual(
            gefunden, [], "Anfragen im Hintergrund gehen über 'imHintergrund' aus shared.js."
        )

    def test_der_test_erkennt_einen_rueckfall(self):
        """Otherwise it would go unnoticed if the expression no longer caught anything."""
        self.assertTrue(AUFRUF.search(skript("shared")))
        self.assertTrue(AUFRUF.search("const antwort = await fetch(adresse, {"))
        self.assertFalse(AUFRUF.search("if (!window.fetch || !window.DOMParser) return;"))
        self.assertFalse(AUFRUF.search(ohne_kommentare("  // A 'fetch(' in a comment.")))

    def test_die_ausnahmen_gibt_es(self):
        """An exception for a script that is gone or asks no more would only hide the next one."""
        vorhanden = {pfad.name: pfad.read_text(encoding="utf-8") for pfad in skripte()}
        for name in SELBST:
            with self.subTest(name=name):
                self.assertIn(name, vorhanden)
                self.assertTrue(AUFRUF.search(ohne_kommentare(vorhanden[name])))


class GemeinsameHilfenZuerst(AngemeldeteSeiten):
    """'shared.js' comes before every script that uses it.

    Classic scripts run in the order of the page. One that came earlier would
    find no 'imHintergrund' and no 'csrfMarke' and fail on its first request.
    """

    #: What a script that uses the helpers calls.
    HILFE = re.compile(r"\b(?:imHintergrund|csrfMarke)\b")

    def nutzer(self) -> list[str]:
        return [
            pfad.stem
            for pfad in skripte()
            if pfad.name != "shared.js"
            and self.HILFE.search(ohne_kommentare(pfad.read_text(encoding="utf-8")))
        ]

    def test_vor_den_skripten_jeder_seite(self):
        nutzer = self.nutzer()
        self.assertIn("instant_actions", nutzer)
        # Signed in the list with its filter, signed out the sign-in with its passkey.
        for angemeldet, adresse in ((True, reverse("ware_list")), (False, reverse("login"))):
            if not angemeldet:
                self.client.logout()
            html = self.client.get(adresse).content.decode()
            eingebunden = [name for name in nutzer if f"js/{name}.js" in html]
            with self.subTest(adresse=adresse):
                self.assertGreaterEqual(len(eingebunden), 2)
                for name in eingebunden:
                    self.assertLess(html.index("js/shared.js"), html.index(f"js/{name}.js"), name)
