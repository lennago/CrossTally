"""The footer and the privacy page.

Both hang on the appearance: who operates the application is in the
database and not in the code. The published image knows no business, and
without an entry no empty line or a comma without a name may remain. The
privacy text is prefilled with the bundled one and is extended or replaced
there. Empty means: no page and no link.

The page itself is the part that most easily breaks unnoticed: it belongs
precisely to those who otherwise only see the block page of the web
interface. If it were behind the middleware, none of them would get there.
"""

import re

from django.conf import settings
from django.db import connection
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from accounts.models import CustomUser, Sprache
from api.serializers import IchSerializer
from django_project.testing import AngemeldeteSeiten, administrator, stylesheet
from django_project.textformat import als_html
from organisation.models import Erscheinungsbild
from organisation.privacy_text import mitgelieferter_datenschutztext


def betreiber(**felder) -> Erscheinungsbild:
    """An own, active version with what the business entered.

    The default cannot be changed. The way is that of the administration:
    copy, change, choose.
    """
    fassung = Erscheinungsbild.objects.filter(name="Eigenes").first()
    if fassung is None:
        fassung = Erscheinungsbild.standard().kopie("Eigenes")
    for name, wert in felder.items():
        setattr(fassung, name, wert)
    fassung.save()
    fassung.aktivieren()
    return fassung


class Seitenfuss(AngemeldeteSeiten):
    def test_verweist_auf_jeder_seite_auf_den_datenschutz(self):
        for name in ("haus_list", "warengruppe_list"):
            with self.subTest(name):
                antwort = self.client.get(reverse(name))
                self.assertContains(antwort, reverse("datenschutz"))

    def test_nennt_den_betreiber_samt_kontakt(self):
        betreiber(betreiber="Betrieb Beispiel", betreiber_kontakt="post@example.org")
        antwort = self.client.get(reverse("haus_list"))
        self.assertContains(antwort, "Betrieb Beispiel, post@example.org")

    def test_ohne_namen_steht_auch_der_kontakt_nicht_da(self):
        """A contact without a name would be a comma with nothing before it."""
        betreiber(betreiber="", betreiber_kontakt="post@example.org")
        antwort = self.client.get(reverse("haus_list"))
        self.assertNotContains(antwort, "post@example.org")
        self.assertContains(antwort, reverse("datenschutz"))

    def test_der_betreiber_kostet_keine_eigene_abfrage(self):
        """The same row that serves the logo. A second processor would ask twice."""
        with CaptureQueriesContext(connection) as ohne:
            self.client.get(reverse("haus_list"))
        betreiber(betreiber="Betrieb Beispiel")
        with CaptureQueriesContext(connection) as mit:
            self.client.get(reverse("haus_list"))
        self.assertEqual(len(mit), len(ohne))


class AnmeldungUndAttrappeTragenDenselbenFuss(TestCase):
    """The decoy under '/admin/' has to look exactly like the login.

    A footer carried by only one of the two would be the tell that
    'accounts/honeypot.py' is trying to avoid.
    """

    def test_beide_zeigen_denselben_fuss(self):
        betreiber(betreiber="Betrieb Beispiel")
        fuesse = []
        for pfad in (reverse("login"), reverse("honeypot:login")):
            seite = self.client.get(pfad).content.decode()
            treffer = re.search(r"<footer.*?</footer>", seite, re.S)
            self.assertIsNotNone(treffer, pfad)
            assert treffer is not None
            fuesse.append(re.sub(r"\s+", " ", treffer.group(0)))
        self.assertEqual(*fuesse)
        self.assertIn("Betrieb Beispiel", fuesse[0])


class DatenschutzseiteIstFuerAlleDa(TestCase):
    def test_ohne_anmeldung_zu_lesen(self):
        """The link also stands in the footer of the login form."""
        antwort = self.client.get(reverse("datenschutz"))
        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, "Dein Passwort niemals im Klartext")

    def test_zaehlende_landen_nicht_auf_der_sperrseite(self):
        """They are the reason for the page and otherwise never see the interface."""
        zaehlende = CustomUser.objects.create_user(username="zoe", password="geheim")
        self.client.force_login(zaehlende)
        antwort = self.client.get(reverse("datenschutz"))
        self.assertEqual(antwort.status_code, 200)

    def test_nennt_verantwortlichen_und_anlaufstelle(self):
        betreiber(betreiber="Betrieb Beispiel", datenschutz_kontakt="datenschutz@example.org")
        antwort = self.client.get(reverse("datenschutz"))
        self.assertContains(antwort, "Betrieb Beispiel")
        self.assertContains(antwort, "datenschutz@example.org")

    def test_ohne_eigene_anlaufstelle_antwortet_der_betreiber(self):
        betreiber(betreiber_kontakt="post@example.org", datenschutz_kontakt="")
        self.assertContains(self.client.get(reverse("datenschutz")), "post@example.org")

    def test_ohne_leiste_klebt_die_kopfleiste_ganz_oben(self):
        """The only page with a header bar that is open even without the manager role.

        The header bar otherwise attaches itself below the navigation bar. That
        does not exist here, and its height would be a gap through which the text
        scrolls away upwards. Exactly that was visible until 09/2026.
        """
        self.client.force_login(CustomUser.objects.create_user(username="zoe"))
        seite = self.client.get(reverse("datenschutz")).content.decode()
        self.assertIn("kopfleiste", seite)
        self.assertNotIn('class="navbar', seite)
        self.assertIn("body:not(:has(.navbar))", stylesheet("site.css"))

    def test_ohne_jede_angabe_bleibt_die_seite_lesbar(self):
        """The published image knows no business. Still no empty paragraph."""
        antwort = self.client.get(reverse("datenschutz"))
        self.assertContains(antwort, "sagt dir deine Führungskraft")
        self.assertContains(antwort, "an deine Führungskraft oder an die Leitung")


class DerDatenschutztext(TestCase):
    """Prefilled with the bundled text, editable, and empty means: no page."""

    TEXT = "## Verantwortlich\n\nDie Beispiel GmbH.\n\n- Fragen an <datenschutz@example.org>."

    def test_der_mitgelieferte_text_kennt_passkeys_mails_und_den_koeder(self):
        """What the application stores has to stand in it. Three things were missing (10/2026)."""
        for sprache, woerter in (
            ("de", ("Passkeys", "/admin/", "Mails an dich", "täglichen Bereinigung")),
            ("en", ("passkeys", "/admin/", "Mails to you", "daily clean-up")),
        ):
            text = (settings.BASE_DIR / "templates" / "privacy" / f"{sprache}.txt").read_text(
                encoding="utf-8"
            )
            for wort in woerter:
                with self.subTest(sprache=sprache, wort=wort):
                    self.assertIn(wort, text)

    def test_der_mitgelieferte_text_kennt_backups_token_und_fristen(self):
        """Backups, the tokens of the app and the periods were missing or wrong (audit 10/2026)."""
        for sprache, woerter, weg in (
            (
                "de",
                ("**Backups:**", "Anmeldetoken", "nach einem Jahr", "die gewählte Sprache"),
                ("Das betrifft nur die Administratoren", "einige Tage im Protokoll"),
            ),
            (
                "en",
                ("**Backups:**", "sign-in token", "after one year", "the language you chose"),
                ("This concerns the administrators only", "logged in", "Logging out"),
            ),
        ):
            text = (settings.BASE_DIR / "templates" / "privacy" / f"{sprache}.txt").read_text(
                encoding="utf-8"
            )
            for wort in woerter:
                with self.subTest(sprache=sprache, wort=wort):
                    self.assertIn(wort, text)
            for wort in weg:
                with self.subTest(sprache=sprache, nicht=wort):
                    self.assertNotIn(wort, text)

    def test_die_zeile_beginnt_mit_dem_mitgelieferten_text(self):
        zeile = Erscheinungsbild.objects.create(name="Eigenes")
        self.assertIn("Dein Passwort niemals im Klartext", zeile.datenschutztext)
        self.assertIn("Your password never in plain text", zeile.datenschutztext_en)
        self.assertNotIn("<", zeile.datenschutztext, "Text, keine Vorlage")

    def test_die_seite_zeigt_den_text_der_zeile(self):
        betreiber(datenschutztext=self.TEXT)
        antwort = self.client.get(reverse("datenschutz"))
        self.assertContains(antwort, '<h2 class="h5 mt-4">Verantwortlich</h2>', html=True)
        self.assertContains(antwort, "<p>Die Beispiel GmbH.</p>", html=True)
        self.assertNotContains(antwort, "Dein Passwort niemals im Klartext")

    def test_html_im_text_bleibt_text(self):
        """Plain text, no template: nothing from outside reaches the page as markup."""
        betreiber(datenschutztext=self.TEXT)
        antwort = self.client.get(reverse("datenschutz"))
        self.assertContains(antwort, "&lt;datenschutz@example.org&gt;")

    def test_die_sprache_des_kontos_waehlt_den_text(self):
        betreiber(datenschutztext="Deutscher Text.", datenschutztext_en="English text.")
        self.client.force_login(administrator(sprache=Sprache.ENGLISCH))
        self.assertContains(self.client.get(reverse("datenschutz")), "English text.")
        self.client.force_login(
            administrator("zoe", email="zoe@example.org", sprache=Sprache.DEUTSCH)
        )
        self.assertContains(self.client.get(reverse("datenschutz")), "Deutscher Text.")

    def test_eine_sprache_genuegt(self):
        """Whoever fills in only one means it for all."""
        betreiber(datenschutztext="", datenschutztext_en="English text.")
        self.client.force_login(administrator(sprache=Sprache.DEUTSCH))
        self.assertContains(self.client.get(reverse("datenschutz")), "English text.")

    def test_leer_heisst_keine_seite_und_kein_verweis(self):
        betreiber(datenschutztext="", datenschutztext_en="")
        self.assertEqual(self.client.get(reverse("datenschutz")).status_code, 404)
        self.client.force_login(administrator())
        self.assertNotContains(self.client.get(reverse("haus_list")), reverse("datenschutz"))
        self.assertNotContains(self.client.get(reverse("login")), reverse("datenschutz"))

    def test_die_zaehl_app_erfaehrt_es(self):
        """Their account menu would otherwise point into nothing."""
        konto = administrator()
        self.assertTrue(IchSerializer(konto).data["datenschutz"])
        betreiber(datenschutztext="", datenschutztext_en="")
        self.assertFalse(IchSerializer(konto).data["datenschutz"])

    def test_die_verwaltung_zeigt_die_felder_vorbelegt(self):
        """It is edited there, so the form has to be present (lesson from 09/2026)."""
        self.client.force_login(administrator())
        antwort = self.client.get(reverse("admin:organisation_erscheinungsbild_add"))
        for feld in ("betreiber", "betreiber_kontakt", "datenschutz_kontakt", "datenschutztext"):
            with self.subTest(feld=feld):
                self.assertContains(antwort, f'name="{feld}"')
        self.assertContains(antwort, "Dein Passwort niemals im Klartext")


class DasTextformat(SimpleTestCase):
    """Five rules: paragraph, line break, heading, list, bold. Everything else is text."""

    def test_die_fuenf_regeln(self):
        html = str(als_html("## Titel\n\nEin **Satz**.\nNoch einer.\n\n- eins\n- **zwei**"))
        self.assertEqual(
            html,
            '<h2 class="h5 mt-4">Titel</h2>\n<p>Ein <strong>Satz</strong>.<br>Noch einer.</p>\n'
            "<ul><li>eins</li><li><strong>zwei</strong></li></ul>",
        )

    def test_eine_anschrift_behaelt_ihre_zeilen(self):
        """The reason for the line break: until 10/2026 an address ran into one line."""
        html = str(als_html("Beispiel GmbH\nMusterweg 1  \n10101 Musterstadt"))
        self.assertEqual(html, "<p>Beispiel GmbH<br>Musterweg 1<br>10101 Musterstadt</p>")

    def test_maskiert_vor_dem_umbau(self):
        self.assertEqual(str(als_html("<b>x</b> & y")), "<p>&lt;b&gt;x&lt;/b&gt; &amp; y</p>")

    def test_der_mitgelieferte_text_setzt_sich_ohne_rest(self):
        """Every line of the template matches one of the five rules or is a paragraph."""
        for sprache in ("de", "en"):
            with self.subTest(sprache=sprache):
                html = str(als_html(mitgelieferter_datenschutztext(sprache)))
                self.assertNotIn("**", html)
                for rest in ("<p>## ", "<li>## ", "<p>- ", "<li>- "):
                    self.assertNotIn(rest, html)


class DasErscheinungsbildEinmalJeSeite(TestCase):
    """The text pages read the active version once. Until 10/2026 name and footer read it again."""

    def test_datenschutz_und_impressum(self):
        betreiber(betreiber="Betrieb Beispiel", impressum="Kino Nord, Hauptstraße 1")
        for name in ("datenschutz", "impressum"):
            with self.subTest(seite=name):
                with CaptureQueriesContext(connection) as erfasst:
                    antwort = self.client.get(reverse(name))
                self.assertEqual(antwort.status_code, 200)
                gelesen = sum(
                    '"organisation_erscheinungsbild"' in abfrage["sql"]
                    for abfrage in erfasst.captured_queries
                )
                self.assertEqual(gelesen, 1)
