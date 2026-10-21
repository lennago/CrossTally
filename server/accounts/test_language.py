"""Stage 1 of bilingualism: the language hangs on the account and applies everywhere."""

from django.conf import settings
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import translation

from accounts.models import CustomUser, Sitzung, Sprache
from django_project.testing import administrator, inventur_anlegen


class SprachwahlAmKonto(TestCase):
    """The choice is on the account, not on the device."""

    def setUp(self):
        self.user = administrator()
        self.client.force_login(self.user)

    def test_voreingestellt_ist_deutsch(self):
        self.assertEqual(self.user.sprache, Sprache.DEUTSCH)

    def test_der_knopf_setzt_sie_und_kehrt_zurueck(self):
        ziel = reverse("haus_list")
        antwort = self.client.post(reverse("sprachwahl"), {"sprache": "en", "weiter": ziel})
        self.assertRedirects(antwort, ziel)
        self.user.refresh_from_db()
        self.assertEqual(self.user.sprache, Sprache.ENGLISCH)

    def test_ein_unbekanntes_kuerzel_aendert_nichts(self):
        self.client.post(reverse("sprachwahl"), {"sprache": "kl"})
        self.user.refresh_from_db()
        self.assertEqual(self.user.sprache, Sprache.DEUTSCH)

    def test_ein_fremdes_ziel_wird_verworfen(self):
        """'weiter' is user input. Otherwise the button would be an open redirect."""
        antwort = self.client.post(
            reverse("sprachwahl"), {"sprache": "en", "weiter": "https://example.org/"}
        )
        self.assertEqual(antwort["Location"], reverse("home"))

    def test_beide_leisten_bieten_die_wahl_an(self):
        for adresse in (reverse("inventur_list"), reverse("admin:index")):
            with self.subTest(adresse=adresse):
                antwort = self.client.get(adresse)
                self.assertContains(antwort, 'data-sprache="de"')
                self.assertContains(antwort, 'data-sprache="en"')
                self.assertContains(antwort, reverse("sprachwahl"))

    def test_die_gewaehlte_traegt_den_haken(self):
        self.user.sprache = Sprache.ENGLISCH
        self.user.save(update_fields=["sprache"])
        seite = self.client.get(reverse("inventur_list")).content.decode()
        self.assertIn('data-sprache="en"\n            aria-current="true"', seite)


class SpracheGiltFuerDieAnfrage(TestCase):
    """The middleware sets it, and only for this one request."""

    def test_eine_englische_wahl_kommt_in_der_antwort_an(self):
        user = administrator(sprache=Sprache.ENGLISCH)
        self.client.force_login(user)
        antwort = self.client.get(reverse("haus_list"))
        self.assertEqual(antwort.headers["Content-Language"], "en")

    def test_deutsch_bleibt_deutsch(self):
        self.client.force_login(administrator())
        antwort = self.client.get(reverse("haus_list"))
        self.assertEqual(antwort.headers["Content-Language"], "de")

    def test_wer_nicht_angemeldet_ist_bekommt_die_vorgabe(self):
        antwort = self.client.get(reverse("login"))
        self.assertEqual(antwort.headers["Content-Language"], "de")

    def test_danach_ist_die_sprache_wieder_die_alte(self):
        """The worker would otherwise serve the next request in the previous one's language."""
        self.client.force_login(administrator(sprache=Sprache.ENGLISCH))
        self.client.get(reverse("haus_list"))
        self.assertEqual(translation.get_language(), "de")


class MeldungenSprechenDieSpracheDesKontos(TestCase):
    """A lazy message only becomes text when it goes into the session.

    That happens in the response, so the language choice must still apply. With
    the middleware behind the messages, "Der Sollbestand wird in der
    Vorbereitung geklärt" arrived in German on an English page (09/2026).
    """

    def test_die_meldung_kommt_in_der_sprache_des_kontos_an(self):
        from datetime import date  # noqa: PLC0415

        from organisation.models import Haus  # noqa: PLC0415

        self.client.force_login(administrator(sprache=Sprache.ENGLISCH))
        inventur = inventur_anlegen(
            Haus.objects.create(name="Kino Ost"), stichtag=date(2026, 1, 31)
        )
        inventur.zaehlung_starten()
        antwort = self.client.get(reverse("inventur_sollbestand", args=[inventur.pk]), follow=True)
        self.assertContains(antwort, "The target stock is clarified during preparation")
        self.assertNotContains(antwort, "Der Sollbestand wird")


class SpracheAusDerAdresse(TestCase):
    """Where no account can be asked, the parameter says it.

    The counting app links to the privacy page but identifies itself with a
    token. It has no Django session, so for Django the call is anonymous.
    """

    def test_der_parameter_setzt_die_sprache(self):
        antwort = self.client.get(reverse("datenschutz"), {"sprache": "en"})
        self.assertEqual(antwort.headers["Content-Language"], "en")
        self.assertContains(antwort, "Privacy")

    def test_ein_unbekanntes_kuerzel_aendert_nichts(self):
        antwort = self.client.get(reverse("datenschutz"), {"sprache": "kl"})
        self.assertEqual(antwort.headers["Content-Language"], "de")

    def test_das_konto_sticht_den_parameter(self):
        """Whoever is signed in has made their choice. It applies on every device."""
        self.client.force_login(administrator(sprache=Sprache.ENGLISCH))
        antwort = self.client.get(reverse("datenschutz"), {"sprache": "de"})
        self.assertEqual(antwort.headers["Content-Language"], "en")


class SpracheOhneKonto(TestCase):
    """Without an account the browser remembers the choice.

    Until 09/2026 the parameter applied only to the one page: whoever opened
    the login form in English got "Forgot your password" and the block page in
    German again. Until 10/2026 the session remembered it, and every anonymous
    call with '?sprache=' wrote one into the database.
    """

    def test_der_parameter_legt_keine_sitzung_an(self):
        for _ in range(3):
            Client().get(reverse("login"), {"sprache": "en"})
        self.assertFalse(Sitzung.objects.exists())

    def test_das_cookie_traegt_nur_die_wahl(self):
        antwort = self.client.get(reverse("login"), {"sprache": "en"})
        cookie = antwort.cookies[settings.LANGUAGE_COOKIE_NAME]
        self.assertEqual(cookie.value, "en")
        self.assertTrue(cookie["httponly"])
        self.assertEqual(cookie["samesite"], "Lax")
        self.assertIn("Cookie", antwort.headers["Vary"])

    def test_ein_gemerktes_cookie_wird_nicht_neu_gesetzt(self):
        self.client.get(reverse("login"), {"sprache": "en"})
        antwort = self.client.get(reverse("login"), {"sprache": "en"})
        self.assertNotIn(settings.LANGUAGE_COOKIE_NAME, antwort.cookies)

    def test_der_parameter_haelt_ueber_die_naechste_seite(self):
        self.client.get(reverse("login"), {"sprache": "en"})
        antwort = self.client.get(reverse("password_reset"))
        self.assertEqual(antwort.headers["Content-Language"], "en")
        self.assertContains(antwort, "Forgot your password")

    def test_die_anmeldemaske_bietet_die_wahl_an(self):
        antwort = self.client.get(reverse("login"))
        self.assertContains(antwort, 'data-sprache="en"')
        self.assertContains(antwort, reverse("sprachwahl"))

    def test_der_knopf_setzt_die_sprache_ohne_konto(self):
        antwort = self.client.post(
            reverse("sprachwahl"), {"sprache": "en", "weiter": reverse("login")}
        )
        self.assertRedirects(antwort, reverse("login"), fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse("login")).headers["Content-Language"], "en")

    def test_die_sperrseite_spricht_die_gewaehlte_sprache(self):
        self.client.get(reverse("login"), {"sprache": "en"})
        for _ in range(settings.AXES_FAILURE_LIMIT + 1):
            antwort = self.client.post(
                reverse("login"), {"username": "niemand", "password": "falsch"}
            )
        self.assertEqual(antwort.status_code, 429)
        self.assertContains(antwort, "Too many failed attempts", status_code=429)

    def test_nach_der_anmeldung_gilt_das_konto(self):
        self.client.get(reverse("login"), {"sprache": "en"})
        self.client.force_login(administrator())
        self.assertEqual(self.client.get(reverse("haus_list")).headers["Content-Language"], "de")


class SpracheUeberDieSchnittstelle(TestCase):
    """The counting app learns the choice and can set it."""

    def setUp(self):
        self.user = administrator()
        self.client.force_login(self.user)

    def test_ich_nennt_die_sprache(self):
        antwort = self.client.get(reverse("ich", args=["v1"]))
        self.assertEqual(antwort.json()["sprache"], "de")

    def test_die_app_kann_sie_aendern(self):
        antwort = self.client.patch(
            reverse("ich", args=["v1"]),
            {"sprache": "en"},
            content_type="application/json",
        )
        self.assertEqual(antwort.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.sprache, Sprache.ENGLISCH)

    def test_sonst_bleibt_alles_gesperrt(self):
        """Only appearance and language, not the role."""
        self.client.patch(
            reverse("ich", args=["v1"]),
            {"is_superuser": True, "sprache": "en"},
            content_type="application/json",
        )
        self.user.refresh_from_db()
        self.assertEqual(self.user.sprache, Sprache.ENGLISCH)
        self.assertTrue(CustomUser.objects.get(pk=self.user.pk).is_superuser)


class ServersprachePraegtDenAnfang(TestCase):
    """'LANGUAGE_CODE' from the '.env' applies to the not signed in and to new accounts."""

    @override_settings(LANGUAGE_CODE="en")
    def test_ein_neues_konto_bekommt_die_sprache_des_servers(self):
        konto = CustomUser.objects.create_user(username="neu", password="geheim")
        self.assertEqual(konto.sprache, Sprache.ENGLISCH)

    def test_ohne_umstellung_ist_es_deutsch(self):
        konto = CustomUser.objects.create_user(username="neu", password="geheim")
        self.assertEqual(konto.sprache, Sprache.DEUTSCH)

    @override_settings(LANGUAGE_CODE="en")
    def test_bestehende_konten_behalten_ihre_wahl(self):
        """A choice is not overwritten remotely."""
        konto = administrator(sprache=Sprache.DEUTSCH)
        konto.refresh_from_db()
        self.assertEqual(konto.sprache, Sprache.DEUTSCH)

    @override_settings(LANGUAGE_CODE="en")
    def test_die_anmeldemaske_spricht_die_sprache_des_servers(self):
        antwort = self.client.get(reverse("login"))
        self.assertEqual(antwort.headers["Content-Language"], "en")

    @override_settings(LANGUAGE_CODE="fr")
    def test_eine_unbekannte_sprache_faellt_auf_deutsch_zurueck(self):
        """Django reports it at startup. Until then no account should break."""
        konto = CustomUser.objects.create_user(username="neu", password="geheim")
        self.assertEqual(konto.sprache, Sprache.DEUTSCH)


class SprachattributDerSeite(TestCase):
    """The 'lang' in the HTML follows the choice, otherwise the screen reader reads wrongly."""

    def test_deutsch(self):
        self.client.force_login(administrator())
        self.assertContains(self.client.get(reverse("haus_list")), '<html lang="de"')

    def test_englisch(self):
        self.client.force_login(administrator(sprache=Sprache.ENGLISCH))
        self.assertContains(self.client.get(reverse("haus_list")), '<html lang="en"')
