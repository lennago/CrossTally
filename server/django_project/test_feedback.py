"""The mail behind "Report a problem or suggestion" in the account menu."""

import html
import re
from urllib.parse import parse_qs, urlsplit

from django.conf import settings
from django.core.exceptions import ValidationError
from django.test import override_settings
from django.urls import reverse

from accounts.email_change import token as email_token
from django_project.feedback import BETREFF_MARKE
from django_project.testing import AngemeldeteSeiten
from organisation.forms import ErscheinungsbildForm
from organisation.models import Erscheinungsbild

ADRESSE = "rueckmeldung@example.org"


def mit_adresse(adresse: str = ADRESSE, **weitere) -> Erscheinungsbild:
    fassung = Erscheinungsbild.standard().kopie("Eigene")
    fassung.rueckmeldeadresse = adresse
    for feld, wert in weitere.items():
        setattr(fassung, feld, wert)
    fassung.save()
    fassung.aktivieren()
    return fassung


def mail_aus(seite: str) -> tuple[str, str, str]:
    """Recipient, subject and text of the one 'mailto:' link on the page."""
    (link,) = re.findall(r'href="(mailto:[^"]*)"', seite)
    teile = urlsplit(html.unescape(link))
    felder = parse_qs(teile.query)
    return teile.path, felder["subject"][0], felder["body"][0]


class ImKontomenue(AngemeldeteSeiten):
    def test_ohne_adresse_kein_eintrag(self):
        """The shipped application names nobody to write to."""
        antwort = self.client.get(reverse("haus_list"))
        self.assertNotContains(antwort, "Problem oder Wunsch melden")
        self.assertNotContains(antwort, "mailto:")

    def test_mit_adresse_steht_er_ueber_der_version(self):
        mit_adresse()
        seite = self.client.get(reverse("haus_list")).content.decode()

        self.assertIn("Problem oder Wunsch melden", seite)
        self.assertLess(seite.index("Problem oder Wunsch melden"), seite.index("Version "))
        self.assertEqual(mail_aus(seite)[0], ADRESSE)

    def test_auch_in_der_verwaltung(self):
        mit_adresse()
        seite = self.client.get(reverse("admin:index")).content.decode()
        self.assertIn("Problem oder Wunsch melden", seite)
        self.assertEqual(mail_aus(seite)[0], ADRESSE)

    def test_die_anmeldung_kennt_ihn_nicht(self):
        """Without an account there is no menu, and the address stays off the open page."""
        mit_adresse()
        self.client.logout()
        self.assertNotContains(self.client.get(reverse("login")), ADRESSE)

    def test_er_wechselt_mit_der_fassung(self):
        mit_adresse()
        Erscheinungsbild.standard().aktivieren()
        self.assertNotContains(self.client.get(reverse("haus_list")), "mailto:")


class DieMail(AngemeldeteSeiten):
    @override_settings(APP_VERSION="9.8.7")
    def test_sie_bringt_mit_was_eine_meldung_braucht(self):
        mit_adresse()
        seite = self.client.get(
            reverse("haus_list"), {"suche": "Ware N"}, HTTP_USER_AGENT="Testbrowser/1.0"
        )
        _, _, text = mail_aus(seite.content.decode())
        zeilen = text.split("\r\n")

        self.assertEqual(zeilen[0], "Was ist passiert, oder was wünschst du dir?")
        self.assertIn("Technische Angaben, bitte stehen lassen:", zeilen)
        self.assertIn("Version: 9.8.7", zeilen)
        # Without the query: a search term says nothing about the problem.
        self.assertIn(f"Seite: http://testserver{reverse('haus_list')}", zeilen)
        self.assertIn(f"Konto: {self.user.username}", zeilen)
        self.assertIn("Browser: Testbrowser/1.0", zeilen)
        self.assertTrue(any(zeile.startswith("Zeit: ") for zeile in zeilen))

    def test_die_bestaetigungsseite_gibt_ihr_token_nicht_weiter(self):
        """The link from the mail is alive while the page is open. The mail names only the page."""
        mit_adresse()
        token = email_token(self.user, "neu@example.org")

        seite = self.client.get(reverse("email_bestaetigen", args=[token]))
        _, _, text = mail_aus(seite.content.decode())

        self.assertIn("Seite: email_bestaetigen", text.split("\r\n"))
        self.assertNotIn(token, text)

    def test_die_verwaltung_gibt_ihren_pfad_nicht_weiter(self):
        """The path of the administration is meant to stay unknown. The page below it is enough."""
        mit_adresse()
        adresse = reverse("admin:accounts_customuser_change", args=[self.user.pk])

        _, _, text = mail_aus(self.client.get(adresse).content.decode())

        self.assertNotIn(settings.ADMIN_URL, text)
        self.assertIn(f"Seite: Verwaltung: /accounts/customuser/{self.user.pk}/change/", text)

    def test_der_betreff_beginnt_in_jeder_sprache_gleich(self):
        """The recipient filters by the start. It stays, whatever the account speaks."""
        mit_adresse()
        _, betreff, _ = mail_aus(self.client.get(reverse("haus_list")).content.decode())
        self.assertEqual(betreff, f"{BETREFF_MARKE} Rückmeldung von der Website")

        self.user.sprache = "en"
        self.user.save()
        _, betreff, text = mail_aus(self.client.get(reverse("haus_list")).content.decode())
        self.assertEqual(betreff, f"{BETREFF_MARKE} Feedback from the website")
        self.assertIn("Technical details, please leave them in:", text)

    def test_der_betreff_folgt_nicht_dem_namen_der_anwendung(self):
        """Renaming the application must not break the filter that holds on to the start."""
        mit_adresse(anwendungsname="Lager & Co")
        _, betreff, _ = mail_aus(self.client.get(reverse("haus_list")).content.decode())
        self.assertTrue(betreff.startswith("[CrossTally] "))

    def test_kein_zeichen_bleibt_unkodiert(self):
        """RFC 6068: a line break is '%0D%0A', and a slash in the text is encoded too."""
        mit_adresse()
        seite = self.client.get(reverse("haus_list")).content.decode()
        (link,) = re.findall(r'href="(mailto:[^"]*)"', seite)
        abfrage = html.unescape(link).partition("?")[2]

        self.assertIn("%0D%0A", abfrage)
        self.assertNotIn("/", abfrage)
        self.assertNotIn(" ", abfrage)


class DasFeld(AngemeldeteSeiten):
    def test_nur_eine_mailadresse_geht(self):
        daten = {"name": "Eigene", "hintergrund": "#ffffff", "rueckmeldeadresse": "Kino Nord"}
        formular = ErscheinungsbildForm(daten)
        self.assertFalse(formular.is_valid())
        self.assertIn("rueckmeldeadresse", formular.errors)

    def test_eine_kopie_nimmt_die_adresse_mit(self):
        eigene = mit_adresse()
        self.assertEqual(eigene.kopie(eigene.kopiename()).rueckmeldeadresse, ADRESSE)

    def test_der_standard_bleibt_ohne(self):
        standard = Erscheinungsbild.standard()
        standard.rueckmeldeadresse = ADRESSE
        with self.assertRaises(ValidationError):
            standard.save()

    def test_die_verwaltung_bietet_das_feld_an(self):
        antwort = self.client.get(reverse("admin:organisation_erscheinungsbild_add"))
        self.assertContains(antwort, 'name="rueckmeldeadresse"')
        self.assertContains(antwort, "Rückmeldungen")
