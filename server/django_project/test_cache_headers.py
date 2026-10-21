"""What a browser may keep: nothing of a signed-in person, nothing of the API.

Until 10/2026 the pages of the website carried only 'Vary: Cookie', and
"Back" after signing out on a shared machine could show them from the cache.
"""

from django.test import TestCase
from django.urls import reverse

from .testing import fuehrungskraft


class KeinCacheFuerPrivates(TestCase):
    def test_angemeldete_seiten(self):
        self.client.force_login(fuehrungskraft())
        for adresse in (reverse("inventur_list"), reverse("user_list"), reverse("passkey_list")):
            with self.subTest(adresse=adresse):
                antwort = self.client.get(adresse)
                self.assertEqual(antwort.status_code, 200)
                self.assertIn("no-store", antwort["Cache-Control"])

    def test_die_icons_bleiben_im_cache(self):
        """Without an own logo the address points to the bundled file: a redirect, no 'no-store'."""
        self.client.force_login(fuehrungskraft())
        antwort = self.client.get("/icons/favicon-32.png")
        self.assertIn(antwort.status_code, (200, 302))
        self.assertNotIn("no-store", antwort.get("Cache-Control", ""))

    def test_die_anmeldeseite_wie_bisher(self):
        self.assertIn("no-store", self.client.get(reverse("login"))["Cache-Control"])

    def test_eine_oeffentliche_seite_ohne_anmeldung_bleibt_frei(self):
        antwort = self.client.get(reverse("datenschutz"))
        self.assertEqual(antwort.status_code, 200)
        self.assertNotIn("no-store", antwort.get("Cache-Control", ""))


class NurSichereVerbenFuerDieApp(TestCase):
    def test_put_und_delete_werden_abgewiesen(self):
        for adresse in (reverse("pwa"), reverse("pwa_manifest")):
            for aufruf in (self.client.put, self.client.delete, self.client.post):
                with self.subTest(adresse=adresse, verb=aufruf.__name__):
                    self.assertEqual(aufruf(adresse).status_code, 405)
