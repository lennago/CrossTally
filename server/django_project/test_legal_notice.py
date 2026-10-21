"""The legal notice: written whole in the administration, a page only where there is a text.

The shipped application has none. An internal tool usually needs none
('DEPLOYMENT.md'), and the published image knows no business it could name.
Whoever needs one writes it on the appearance, in the five rules of the
privacy text, and it appears without a frame around it.
"""

from django.test import TestCase
from django.urls import reverse

from accounts.models import CustomUser, Sprache
from api.serializers import IchSerializer
from django_project.testing import administrator
from organisation.models import Erscheinungsbild

from .test_privacy import betreiber

TEXT = "## Angaben\n\nBeispiel GmbH\n\n- Musterweg 1, 10101 Musterstadt\n- <post@example.org>"


class OhneText(TestCase):
    def test_der_standard_hat_keins(self):
        standard = Erscheinungsbild.standard()
        self.assertEqual((standard.impressum, standard.impressum_en), ("", ""))
        self.assertFalse(standard.hat_impressum)

    def test_keine_seite_und_kein_verweis(self):
        self.assertEqual(self.client.get(reverse("impressum")).status_code, 404)
        self.assertNotContains(self.client.get(reverse("login")), reverse("impressum"))
        self.client.force_login(administrator())
        self.assertNotContains(self.client.get(reverse("haus_list")), reverse("impressum"))
        zoe = administrator("zoe", email="zoe@example.org")
        self.assertFalse(IchSerializer(zoe).data["impressum"])


class MitText(TestCase):
    def setUp(self):
        betreiber(impressum=TEXT)

    def test_ohne_anmeldung_zu_lesen(self):
        antwort = self.client.get(reverse("impressum"))
        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, '<h2 class="h5 mt-4">Angaben</h2>', html=True)
        self.assertContains(antwort, "<li>Musterweg 1, 10101 Musterstadt</li>", html=True)

    def test_html_im_text_bleibt_text(self):
        self.assertContains(self.client.get(reverse("impressum")), "&lt;post@example.org&gt;")

    def test_ohne_rahmen(self):
        """What a legal notice says is up to the business. The page adds nothing of its own."""
        antwort = self.client.get(reverse("impressum"))
        self.assertNotContains(antwort, "Wer verantwortlich ist")
        self.assertNotContains(antwort, "Führungskraft")

    def test_zaehlende_landen_nicht_auf_der_sperrseite(self):
        self.client.force_login(CustomUser.objects.create_user(username="zoe", password="x"))
        self.assertEqual(self.client.get(reverse("impressum")).status_code, 200)

    def test_der_fuss_verweist_darauf_auch_auf_der_anmeldung(self):
        self.assertContains(self.client.get(reverse("login")), reverse("impressum"))
        self.client.force_login(administrator())
        antwort = self.client.get(reverse("haus_list"))
        self.assertContains(antwort, reverse("impressum"))
        self.assertContains(antwort, reverse("datenschutz"))

    def test_die_sprache_des_kontos_waehlt_den_text(self):
        betreiber(impressum="Deutscher Text.", impressum_en="English text.")
        self.client.force_login(administrator(sprache=Sprache.ENGLISCH))
        self.assertContains(self.client.get(reverse("impressum")), "English text.")

    def test_eine_sprache_genuegt(self):
        betreiber(impressum="", impressum_en="English text.")
        self.client.force_login(administrator(sprache=Sprache.DEUTSCH))
        self.assertContains(self.client.get(reverse("impressum")), "English text.")

    def test_die_zaehl_app_erfaehrt_es(self):
        daten = IchSerializer(administrator()).data
        self.assertTrue(daten["impressum"])
        self.assertEqual(daten["impressum_adresse"], reverse("impressum"))

    def test_eine_kopie_nimmt_es_mit(self):
        fassung = Erscheinungsbild.aktuell()
        kopie = fassung.kopie(fassung.kopiename())
        self.assertEqual(kopie.impressum, TEXT)


class InDerVerwaltung(TestCase):
    def test_die_felder_stehen_im_formular(self):
        self.client.force_login(administrator())
        antwort = self.client.get(reverse("admin:organisation_erscheinungsbild_add"))
        for feld in ("impressum", "impressum_en"):
            with self.subTest(feld=feld):
                self.assertContains(antwort, f'name="{feld}"')
