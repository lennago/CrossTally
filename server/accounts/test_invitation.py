"""A new account invited by mail: the tick, the mail, the link and the state in between."""

from http import HTTPStatus
from smtplib import SMTPException
from unittest import mock

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import CustomUser, Passkey
from django_project.mail import mail_moeglich
from django_project.testing import administrator, fuehrungskraft
from organisation.models import Haus

from .invitation import einladungs_token, einladungslink
from .test_notification import formular, verwaltungsformular

PASSWORT = "ziemlich-geheim-2026"

#: The console backend: the mail would only end in the log.
NUR_LOG = {"default": {"BACKEND": "django.core.mail.backends.console.EmailBackend"}}


def angaben(**abweichend) -> dict:
    return {
        "username": "nele",
        "email": "nele@example.com",
        "first_name": "Nele",
        "last_name": "Nachtigall",
        "password1": "",
        "password2": "",
    } | abweichend


class EinladungBeimAnlegen(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.chefin = fuehrungskraft(email="chefin@example.com", first_name="Carla", last_name="C")
        cls.haus = Haus.objects.create(name="Kino Nord")

    def setUp(self):
        self.client.force_login(self.chefin)

    def anlegen(self, **abweichend):
        return self.client.post(reverse("user_add"), angaben(**abweichend))

    def test_ohne_passwort_entsteht_ein_eingeladenes_konto(self):
        antwort = self.anlegen(haeuser=[self.haus.pk])
        self.assertRedirects(antwort, reverse("user_list"))
        konto = CustomUser.objects.get(username="nele")
        self.assertFalse(konto.is_active)
        self.assertFalse(konto.has_usable_password())
        self.assertTrue(konto.ist_eingeladen)
        self.assertEqual(list(konto.haeuser.all()), [self.haus])

    def test_die_mail_traegt_den_link_und_den_benutzernamen(self):
        self.anlegen()
        self.assertEqual(len(mail.outbox), 1)
        nachricht = mail.outbox[0]
        self.assertEqual(nachricht.to, ["nele@example.com"])
        self.assertIn("Dein Zugang zu", nachricht.subject)
        self.assertIn("Hallo Nele,", nachricht.body)
        self.assertIn("Dein Benutzername: nele", nachricht.body)
        self.assertIn("Carla C hat dir ein Konto", nachricht.body)
        self.assertIn("/accounts/einladung/", nachricht.body)
        self.assertIn("Der Link gilt 3 Tage.", nachricht.body)
        self.assertIn("http://testserver/app/", nachricht.body)

    def test_die_mail_spricht_die_sprache_des_kontos(self):
        with override_settings(LANGUAGE_CODE="en"):
            self.anlegen()
        self.assertEqual(CustomUser.objects.get(username="nele").sprache, "en")
        self.assertIn("Hello Nele,", mail.outbox[0].body)
        self.assertIn("Your access to", mail.outbox[0].subject)

    def test_die_meldung_sagt_dass_die_einladung_unterwegs_ist(self):
        antwort = self.anlegen()
        antwort = self.client.get(antwort["Location"])
        self.assertContains(antwort, "Die Einladung ist an nele@example.com unterwegs.")
        self.assertContains(antwort, "eingeladen")

    def test_mit_haken_und_passwort_ohne_einladung(self):
        self.anlegen(passwort_setzen="on", password1=PASSWORT, password2=PASSWORT)
        konto = CustomUser.objects.get(username="nele")
        self.assertTrue(konto.is_active)
        self.assertTrue(konto.check_password(PASSWORT))
        self.assertFalse(konto.ist_eingeladen)
        self.assertEqual(mail.outbox, [])

    def test_mit_haken_braucht_es_beide_felder(self):
        for felder in ({}, {"password1": PASSWORT}, {"password2": PASSWORT}):
            with self.subTest(felder=felder):
                antwort = self.anlegen(passwort_setzen="on", **felder)
                self.assertEqual(antwort.status_code, 200)
                self.assertContains(antwort, "Dieses Feld ist zwingend erforderlich.")
                self.assertFalse(CustomUser.objects.filter(username="nele").exists())
        self.assertEqual(mail.outbox, [])

    def test_ein_passwort_ohne_haken_wird_abgewiesen_statt_geraten(self):
        """Without JavaScript the fields stand there. Whoever fills them forgot the tick."""
        for felder in (
            {"password1": PASSWORT, "password2": PASSWORT},
            {"password1": PASSWORT},
            {"password2": PASSWORT},
        ):
            with self.subTest(felder=felder):
                antwort = self.anlegen(**felder)
                self.assertEqual(antwort.status_code, 200)
                self.assertContains(antwort, "Für ein eigenes Passwort den Haken setzen.")
                self.assertFalse(CustomUser.objects.filter(username="nele").exists())
        self.assertEqual(mail.outbox, [])

    def test_ein_schwaches_passwort_wird_weiter_abgewiesen(self):
        antwort = self.anlegen(passwort_setzen="on", password1="12345678", password2="12345678")
        self.assertEqual(antwort.status_code, 200)
        self.assertFalse(CustomUser.objects.filter(username="nele").exists())

    def test_ein_stummer_mailserver_laesst_das_konto_stehen(self):
        """The account exists and is wanted. The page says so and offers the button."""
        with (
            mock.patch("accounts.invitation.send_mail", side_effect=SMTPException("nein")),
            self.assertLogs("accounts.invitation", level="ERROR"),
        ):
            antwort = self.client.post(reverse("user_add"), angaben(), follow=True)
        konto = CustomUser.objects.get(username="nele")
        self.assertRedirects(antwort, konto.get_absolute_url())
        self.assertContains(antwort, "E-Mail-Server antwortet nicht")
        self.assertContains(antwort, "Einladung erneut senden")
        self.assertTrue(konto.ist_eingeladen)

    @override_settings(MAILERS=NUR_LOG, DEBUG=False)
    def test_ohne_mailserver_ist_ein_passwort_pflicht(self):
        """Like any required field, and nothing on the page talks of an invitation."""
        self.assertFalse(mail_moeglich())
        antwort = self.anlegen()
        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, "Dieses Feld ist zwingend erforderlich.")
        self.assertNotContains(antwort, "Einladung")
        self.assertFalse(CustomUser.objects.filter(username="nele").exists())

    @override_settings(MAILERS=NUR_LOG, DEBUG=False)
    def test_ohne_mailserver_geht_es_mit_passwort_ohne_haken(self):
        self.anlegen(password1=PASSWORT, password2=PASSWORT)
        konto = CustomUser.objects.get(username="nele")
        self.assertTrue(konto.is_active)
        self.assertTrue(konto.check_password(PASSWORT))
        self.assertEqual(mail.outbox, [])

    @override_settings(MAILERS=NUR_LOG, DEBUG=False)
    def test_ohne_mailserver_steht_auf_der_seite_kein_haken(self):
        antwort = self.client.get(reverse("user_add"))
        self.assertNotContains(antwort, "passwort_setzen")
        self.assertNotContains(antwort, "Einladung")
        self.assertContains(antwort, "Die Sprache der Oberfläche.")
        self.assertContains(antwort, 'name="password1"')

    @override_settings(MAILERS=NUR_LOG, DEBUG=True)
    def test_auf_dem_entwicklungsrechner_zaehlt_das_log_als_gesendet(self):
        self.assertTrue(mail_moeglich())

    def test_die_seite_bietet_den_haken_an(self):
        antwort = self.client.get(reverse("user_add"))
        self.assertContains(antwort, "Passwort selbst vergeben")
        self.assertContains(antwort, "Sonst bekommt die Person eine E-Mail")
        self.assertContains(antwort, "Der Link gilt 3 Tage.")
        self.assertContains(antwort, "js/password_choice.js")
        self.assertContains(antwort, "Die Sprache der Einladung und der Oberfläche.")

    def test_der_haken_steht_vor_den_passwortfeldern(self):
        inhalt = self.client.get(reverse("user_add")).content.decode()
        self.assertLess(inhalt.index('name="passwort_setzen"'), inhalt.index('name="password1"'))


class EinladungInDerVerwaltung(TestCase):
    """The administration uses the same form, so the mail goes out there too."""

    def setUp(self):
        self.client.force_login(
            administrator(email="chef@example.com", first_name="C", last_name="D")
        )

    def test_ohne_passwort_geht_die_mail_hinaus(self):
        # The mail leaves after the commit ('test_mail_after_commit').
        with self.captureOnCommitCallbacks(execute=True):
            antwort = self.client.post(reverse("admin:accounts_customuser_add"), angaben())
        self.assertEqual(antwort.status_code, 302)
        konto = CustomUser.objects.get(username="nele")
        self.assertTrue(konto.ist_eingeladen)
        self.assertFalse(konto.is_active)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("/accounts/einladung/", mail.outbox[0].body)

    def test_der_haken_steht_vor_den_passwortfeldern(self):
        antwort = self.client.get(reverse("admin:accounts_customuser_add"))
        inhalt = antwort.content.decode()
        self.assertLess(inhalt.index('name="passwort_setzen"'), inhalt.index('name="password1"'))
        self.assertContains(antwort, "js/password_choice.js")

    def test_mit_haken_und_passwort_ohne_einladung(self):
        antwort = self.client.post(
            reverse("admin:accounts_customuser_add"),
            angaben(passwort_setzen="on", password1=PASSWORT, password2=PASSWORT),
        )
        self.assertEqual(antwort.status_code, 302)
        self.assertTrue(CustomUser.objects.get(username="nele").check_password(PASSWORT))
        self.assertEqual(mail.outbox, [])

    @override_settings(MAILERS=NUR_LOG, DEBUG=False)
    def test_ohne_mailserver_ohne_haken_und_mit_pflicht(self):
        antwort = self.client.get(reverse("admin:accounts_customuser_add"))
        self.assertNotContains(antwort, "passwort_setzen")
        antwort = self.client.post(reverse("admin:accounts_customuser_add"), angaben())
        self.assertEqual(antwort.status_code, 200)
        self.assertFalse(CustomUser.objects.filter(username="nele").exists())


class DerLinkAusDerMail(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.konto = CustomUser.objects.create_user(
            username="nele",
            email="nele@example.com",
            first_name="Nele",
            last_name="Nachtigall",
            password=None,
            is_active=False,
        )

    def link(self, konto=None) -> str:
        chefin = fuehrungskraft(email="chefin@example.com", first_name="C", last_name="D")
        self.client.force_login(chefin)
        antwort = self.client.get(reverse("user_list"))
        adresse = einladungslink(konto or self.konto, antwort.wsgi_request)
        self.client.logout()
        return adresse

    def test_der_link_ist_ohne_anmeldung_erreichbar(self):
        """The person has no account to sign in with yet. The middleware must let it through."""
        antwort = self.client.get(self.link())
        self.assertEqual(antwort.status_code, 302)
        self.assertIn("/set-password/", antwort["Location"])
        seite = self.client.get(antwort["Location"])
        self.assertContains(seite, "Willkommen")
        self.assertContains(seite, "<strong>nele</strong>")

    def test_das_passwort_schaltet_das_konto_frei(self):
        antwort = self.client.get(self.link())
        antwort = self.client.post(
            antwort["Location"], {"new_password1": PASSWORT, "new_password2": PASSWORT}
        )
        self.assertRedirects(antwort, reverse("einladung_fertig"))
        self.konto.refresh_from_db()
        self.assertTrue(self.konto.is_active)
        self.assertTrue(self.konto.check_password(PASSWORT))
        self.assertFalse(self.konto.ist_eingeladen)
        fertig = self.client.get(reverse("einladung_fertig"))
        self.assertContains(fertig, "Konto freigeschaltet")
        self.assertContains(fertig, reverse("pwa"))

    def test_danach_ist_der_link_tot(self):
        adresse = self.link()
        antwort = self.client.get(adresse)
        neu = {"new_password1": PASSWORT, "new_password2": PASSWORT}
        self.client.post(antwort["Location"], neu)
        self.assertContains(self.client.get(adresse), "Link ungültig")

    def test_ein_konto_mit_passwort_oeffnet_der_link_nicht(self):
        """Whoever has a password and forgot it takes "Forgot your password"."""
        self.konto.set_password(PASSWORT)
        self.konto.save()
        self.assertContains(self.client.get(self.link()), "Link ungültig")

    def test_ein_fremder_token_oeffnet_nichts(self):
        adresse = self.link().rsplit("/", 2)[0] + "/abc-def/"
        self.assertContains(self.client.get(adresse), "Link ungültig")

    def test_ein_link_zum_zuruecksetzen_oeffnet_die_einladung_nicht(self):
        """Two salts: the one token does not pass for the other."""
        from django.contrib.auth.tokens import default_token_generator  # noqa: PLC0415

        self.assertFalse(
            einladungs_token.check_token(self.konto, default_token_generator.make_token(self.konto))
        )


class EinladungErneutSenden(TestCase):
    def setUp(self):
        self.client.force_login(
            fuehrungskraft(email="chefin@example.com", first_name="C", last_name="D")
        )
        self.konto = CustomUser.objects.create_user(
            username="nele",
            email="nele@example.com",
            first_name="Nele",
            last_name="Nachtigall",
            password=None,
            is_active=False,
        )

    def test_die_seite_zeigt_den_knopf_und_das_abzeichen(self):
        antwort = self.client.get(self.konto.get_absolute_url())
        self.assertContains(antwort, "Einladung erneut senden")
        self.assertContains(antwort, "eingeladen")
        self.assertNotContains(antwort, "deaktiviert")
        liste = self.client.get(reverse("user_list"))
        self.assertContains(liste, "eingeladen")

    def test_der_knopf_schickt_die_mail_noch_einmal(self):
        antwort = self.client.post(reverse("user_einladen", args=[self.konto.pk]))
        self.assertRedirects(antwort, self.konto.get_absolute_url())
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("/accounts/einladung/", mail.outbox[0].body)

    def test_der_knopf_meldet_einen_stummen_mailserver(self):
        with (
            mock.patch("accounts.invitation.send_mail", side_effect=OSError("zu")),
            self.assertLogs("accounts.invitation", level="ERROR"),
        ):
            antwort = self.client.post(reverse("user_einladen", args=[self.konto.pk]), follow=True)
        self.assertContains(antwort, "E-Mail-Server antwortet nicht")
        self.assertEqual(mail.outbox, [])

    def test_ein_konto_mit_passwort_bekommt_keine(self):
        self.konto.set_password(PASSWORT)
        self.konto.is_active = True
        self.konto.save()
        self.client.post(reverse("user_einladen", args=[self.konto.pk]))
        self.assertEqual(mail.outbox, [])
        antwort = self.client.get(self.konto.get_absolute_url())
        self.assertNotContains(antwort, "Einladung erneut senden")

    def test_ein_von_hand_deaktiviertes_konto_heisst_deaktiviert(self):
        self.konto.set_password(PASSWORT)
        self.konto.save()
        antwort = self.client.get(self.konto.get_absolute_url())
        self.assertContains(antwort, "deaktiviert")
        self.assertNotContains(antwort, "eingeladen")

    @override_settings(MAILERS=NUR_LOG, DEBUG=False)
    def test_ohne_mailserver_zeigt_die_seite_den_knopf_nicht(self):
        """The state stays visible, the promise of a mail does not."""
        antwort = self.client.get(self.konto.get_absolute_url())
        self.assertContains(antwort, "eingeladen")
        self.assertNotContains(antwort, "Einladung erneut senden")

    @override_settings(MAILERS=NUR_LOG, DEBUG=False)
    def test_ohne_mailserver_sagt_der_knopf_es(self):
        antwort = self.client.post(reverse("user_einladen", args=[self.konto.pk]), follow=True)
        self.assertContains(antwort, "Ohne E-Mail-Server geht keine Einladung hinaus.")
        self.assertEqual(mail.outbox, [])


def angemeldet(konto: CustomUser) -> CustomUser:
    """The account signed in once, as every passkey of it did."""
    CustomUser.objects.filter(pk=konto.pk).update(last_login=timezone.now())
    konto.refresh_from_db()
    return konto


def nur_mit_passkey(username: str, **abweichend) -> CustomUser:
    """An account without a password that signs in with a passkey, and has done so."""
    konto = CustomUser.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        first_name=username.capitalize(),
        last_name="Passkey",
        password=None,
        **abweichend,
    )
    Passkey.objects.create(
        benutzer=konto, kennung=f"k-{username}", oeffentlicher_schluessel=b"x", name="Handy"
    )
    return angemeldet(konto)


class WerSichAngemeldetHatIstNichtEingeladen(TestCase):
    """Invited is who has no password and never signed in, not every account without one.

    Until 10/2026 an account that signs in with a passkey only counted as
    invited. The list said so, its page offered the invitation again, a
    manager could send it for an administrator, and the link set a password
    and switched a deactivated account back on, administrators included.
    """

    #: The badges as the list shows them. The help beside it names both words too.
    EINGELADEN = 'ms-1">eingeladen</span>'
    DEAKTIVIERT = 'ms-1">deaktiviert</span>'

    def setUp(self):
        self.leitung = fuehrungskraft(email="leitung@example.com", first_name="L", last_name="L")
        self.client.force_login(self.leitung)

    def pruefe_seiten(self, konto: CustomUser, *, deaktiviert: bool) -> None:
        seite = self.client.get(konto.get_absolute_url())
        self.assertNotContains(seite, reverse("user_einladen", args=[konto.pk]))
        self.assertNotContains(seite, "text-bg-info-subtle")
        self.assertEqual("text-bg-warning-subtle" in seite.content.decode(), deaktiviert)
        liste = self.client.get(reverse("user_list"))
        self.assertNotContains(liste, self.EINGELADEN)
        self.assertEqual(self.DEAKTIVIERT in liste.content.decode(), deaktiviert)

    def test_ein_konto_mit_passkey_ist_nicht_eingeladen(self):
        zoe = nur_mit_passkey("zoe")
        self.assertFalse(zoe.ist_eingeladen)
        self.pruefe_seiten(zoe, deaktiviert=False)

    def test_deaktiviert_heisst_es_deaktiviert(self):
        self.pruefe_seiten(nur_mit_passkey("zoe", is_active=False), deaktiviert=True)

    def test_ein_eingeladenes_konto_traegt_weiter_seine_marke(self):
        nele = CustomUser.objects.create_user(
            username="nele",
            email="nele@example.com",
            first_name="Nele",
            last_name="Nachtigall",
            password=None,
            is_active=False,
        )
        self.assertContains(self.client.get(nele.get_absolute_url()), "text-bg-info-subtle")
        self.assertContains(self.client.get(reverse("user_list")), self.EINGELADEN)

    def test_der_knopf_schickt_ihm_keine_einladung(self):
        """Not even to an address a stranger set without being asked for a password."""
        for aktiv in (True, False):
            with self.subTest(aktiv=aktiv):
                zoe = nur_mit_passkey(f"zoe{int(aktiv)}", is_active=aktiv)
                antwort = self.client.post(reverse("user_einladen", args=[zoe.pk]), follow=True)
                self.assertContains(antwort, "Dieses Konto hat sich schon angemeldet")
                self.assertEqual(mail.outbox, [])
                zoe.refresh_from_db()
                self.assertEqual(zoe.is_active, aktiv)
                self.assertFalse(zoe.has_usable_password())

    def test_einen_administrator_laedt_keine_fuehrungskraft_ein(self):
        """As for editing and deleting: only an administrator changes an administrator."""
        boss = administrator("boss", email="boss@example.com", password=None, is_active=False)
        self.assertTrue(boss.ist_eingeladen)
        antwort = self.client.post(reverse("user_einladen", args=[boss.pk]))
        self.assertEqual(antwort.status_code, 403)
        self.assertEqual(mail.outbox, [])

        chef = administrator(email="chef@example.com", first_name="C", last_name="C")
        self.client.force_login(chef)
        antwort = self.client.post(reverse("user_einladen", args=[boss.pk]))
        self.assertRedirects(antwort, boss.get_absolute_url())
        self.assertEqual([nachricht.to for nachricht in mail.outbox], [["boss@example.com"]])

    def test_ein_deaktivierter_administrator_mit_passkey_bleibt_aus(self):
        """The case of the report: a manager invites, the link switches the account back on."""
        boss = nur_mit_passkey("boss", is_staff=True, is_superuser=True, is_active=False)
        antwort = self.client.post(reverse("user_einladen", args=[boss.pk]))
        self.assertEqual(antwort.status_code, 403)
        self.assertEqual(mail.outbox, [])
        boss.refresh_from_db()
        self.assertFalse(boss.is_active)

    def test_ein_frueherer_link_stirbt_mit_der_ersten_anmeldung(self):
        """The token carries the last sign-in, and the page asks for the state as well."""
        nele = CustomUser.objects.create_user(
            username="nele",
            email="nele@example.com",
            first_name="Nele",
            last_name="Nachtigall",
            password=None,
            is_active=False,
        )
        adresse = einladungslink(nele, self.client.get(reverse("user_list")).wsgi_request)
        token = adresse.rstrip("/").rsplit("/", 1)[1]
        self.assertTrue(einladungs_token.check_token(nele, token))

        nele = angemeldet(nele)

        self.assertFalse(einladungs_token.check_token(nele, token))
        self.client.logout()
        self.assertContains(self.client.get(adresse), "Link ungültig")
        nele.refresh_from_db()
        self.assertFalse(nele.has_usable_password())


class DeaktivierenEntwertetDieEinladung(TestCase):
    """A manager switches an invited account on and off again to block it: its old link dies.

    The link does not ask 'is_active' ('CustomUser.ist_eingeladen' says why),
    but its token carries the password field. Switched off without a usable
    password, the account gets a fresh unusable one, so every link sent
    before opens nothing, and the account still counts as invited. Until
    10/2026 the old link switched it back on for its three days, with its
    role and its houses.
    """

    def setUp(self):
        self.leitung = fuehrungskraft(email="leitung@example.com", first_name="L", last_name="L")
        self.chef = administrator(email="chef@example.com", first_name="C", last_name="C")
        self.nele = CustomUser.objects.create_user(
            username="nele",
            email="nele@example.com",
            first_name="Nele",
            last_name="Nachtigall",
            password=None,
            is_active=False,
        )
        self.nele.haeuser.add(Haus.objects.create(name="Kino Nord"))

    def link(self) -> str:
        browser = self.client_class()
        browser.force_login(self.leitung)
        return einladungslink(self.nele, browser.get(reverse("user_list")).wsgi_request)

    def auf_der_website(self, aktiv: bool) -> None:
        browser = self.client_class()
        browser.force_login(self.leitung)
        konto = CustomUser.objects.get(pk=self.nele.pk)
        antwort = browser.post(
            reverse("user_edit", args=[konto.pk]), formular(konto, is_active=aktiv)
        )
        self.assertEqual(antwort.status_code, 302)

    def in_der_verwaltung(self, aktiv: bool) -> None:
        browser = self.client_class()
        browser.force_login(self.chef)
        konto = CustomUser.objects.get(pk=self.nele.pk)
        with self.captureOnCommitCallbacks(execute=True):
            antwort = browser.post(
                reverse("admin:accounts_customuser_change", args=[konto.pk]),
                verwaltungsformular(konto, is_active=aktiv),
            )
        self.assertEqual(antwort.status_code, 302)

    def passwort_setzen(self, link: str):
        gast = self.client_class()
        schritt = gast.get(link)
        if schritt.status_code != HTTPStatus.FOUND:
            return schritt
        neu = {"new_password1": PASSWORT, "new_password2": PASSWORT}
        return gast.post(schritt["Location"], neu)

    def pruefe_gesperrt(self, link: str) -> None:
        self.assertContains(self.passwort_setzen(link), "Link ungültig")
        konto = CustomUser.objects.get(pk=self.nele.pk)
        self.assertFalse(konto.is_active)
        self.assertFalse(konto.has_usable_password())
        self.assertTrue(konto.ist_eingeladen)

    def test_auf_der_website(self):
        alt = self.link()

        self.auf_der_website(True)
        self.auf_der_website(False)

        self.pruefe_gesperrt(alt)

    def test_in_der_verwaltung(self):
        alt = self.link()

        self.in_der_verwaltung(True)
        self.in_der_verwaltung(False)

        self.pruefe_gesperrt(alt)

    def test_eine_neue_einladung_geht_wieder(self):
        alt = self.link()
        self.auf_der_website(True)
        self.auf_der_website(False)
        browser = self.client_class()
        browser.force_login(self.leitung)

        browser.post(reverse("user_einladen", args=[self.nele.pk]))

        neu = next(
            zeile for zeile in mail.outbox[-1].body.splitlines() if "/accounts/einladung/" in zeile
        )
        self.assertNotEqual(neu, alt)
        self.assertRedirects(self.passwort_setzen(neu), reverse("einladung_fertig"))
        konto = CustomUser.objects.get(pk=self.nele.pk)
        self.assertTrue(konto.is_active)
        self.assertTrue(konto.check_password(PASSWORT))

    def test_ein_eingeschaltetes_konto_behaelt_seinen_link(self):
        """Switched on by hand, the link still works. That is why it is not bound to the tick."""
        alt = self.link()

        self.auf_der_website(True)

        self.assertRedirects(self.passwort_setzen(alt), reverse("einladung_fertig"))

    def test_ein_konto_mit_passwort_behaelt_es(self):
        CustomUser.objects.filter(pk=self.nele.pk).update(is_active=True)
        self.nele.set_password(PASSWORT)
        self.nele.save(update_fields=["password"])

        self.auf_der_website(False)

        self.assertTrue(CustomUser.objects.get(pk=self.nele.pk).check_password(PASSWORT))
