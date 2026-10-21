"""Website sessions that name their account ('accounts.Sitzung', 'session_store.py').

Until 10/2026 Django's own table held them, and a session said whose it was
only inside its signed content. Removing a passkey therefore decoded every
open session to find the account's ('sign_out.sitzungen_beenden'). Now the
store writes the account into a column of its own on every save, and the
account's sessions are a lookup by it.
"""

import io
import json
from datetime import timedelta
from importlib import import_module
from unittest import mock

from django.apps import apps
from django.conf import settings
from django.contrib.auth import SESSION_KEY
from django.contrib.sessions.backends.base import SessionBase
from django.contrib.sessions.backends.db import SessionStore as DjangosSpeicher
from django.contrib.sessions.models import Session
from django.core.management import call_command
from django.db import connection
from django.db.models import QuerySet
from django.test import Client, TestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from accounts.models import CustomUser, Passkey, Sitzung
from accounts.passkey import user_handle
from accounts.session_store import SessionStore
from accounts.sign_out import sitzungen_beenden
from accounts.test_passkey import Authenticator
from accounts.test_sign_out import schluessel, zaehlerin
from django_project.testing import administrator

#: The migration that moved the open sessions, for its two functions.
UMZUG = import_module("accounts.migrations.0003_sitzungen_mit_konto")


def nele() -> CustomUser:
    return CustomUser.objects.create_user(
        username="nele", email="nele@example.com", password="x", first_name="N", last_name="N"
    )


def angemeldet(konto: CustomUser) -> Client:
    browser = Client()
    browser.force_login(konto)
    return browser


def sitzungsschluessel(browser: Client) -> str:
    return browser.cookies[settings.SESSION_COOKIE_NAME].value


def zeile(browser: Client) -> Sitzung:
    return Sitzung.objects.get(session_key=sitzungsschluessel(browser))


def gespeichert(inhalt: dict, speicher: type[SessionBase] = SessionStore) -> str:
    """A session with this content, saved by the store, as a request leaves it behind."""
    sitzung = speicher()
    sitzung.update(inhalt)
    sitzung.save()
    assert sitzung.session_key is not None
    return sitzung.session_key


class DieZeileNenntIhrKonto(TestCase):
    """The column says what the content says, through every turn of a session."""

    def setUp(self):
        self.zoe = zaehlerin()

    def test_ohne_anmeldung_bleibt_sie_leer(self):
        """A passkey's question opens a session for a visitor who is not signed in."""
        self.client.post(reverse("passkey_anmelden_optionen"))

        self.assertIsNone(zeile(self.client).konto_id)

    def test_die_anmeldung_traegt_es_unter_dem_neuen_schluessel_ein(self):
        """Django signs in under a new key and keeps the content ('cycle_key')."""
        self.client.post(reverse("passkey_anmelden_optionen"))
        vorher = sitzungsschluessel(self.client)

        antwort = self.client.post(reverse("login"), {"username": "zoe", "password": "x"})

        self.assertEqual(antwort.status_code, 302)
        self.assertNotEqual(sitzungsschluessel(self.client), vorher)
        self.assertEqual(zeile(self.client).konto, self.zoe)
        self.assertFalse(Sitzung.objects.filter(session_key=vorher).exists())

    def test_ebenso_mit_dem_passkey(self):
        geraet = Authenticator()
        Passkey.objects.create(
            benutzer=self.zoe,
            kennung=geraet.kennung,
            oeffentlicher_schluessel=geraet.cose_key(),
            name="Handy",
        )
        optionen = self.client.post(reverse("passkey_anmelden_optionen")).json()
        credential = geraet.anmeldung(optionen["challenge"], user_handle(self.zoe))

        antwort = self.client.post(
            reverse("passkey_anmelden"),
            json.dumps({"credential": credential}),
            content_type="application/json",
        )

        self.assertEqual(antwort.status_code, 200, antwort.content)
        self.assertEqual(zeile(self.client).konto, self.zoe)

    def test_ebenso_ohne_formular(self):
        """The way of the tests, and of the app's 'from_session' ('force_login')."""
        self.assertEqual(zeile(angemeldet(self.zoe)).konto, self.zoe)

    def test_ein_anderes_konto_im_selben_browser(self):
        """Django empties the session of the one before ('flush'). The row names the new one."""
        self.client.force_login(self.zoe)
        vorher = sitzungsschluessel(self.client)
        andere = nele()

        self.client.post(reverse("login"), {"username": "nele", "password": "x"})

        self.assertEqual(zeile(self.client).konto, andere)
        self.assertFalse(Sitzung.objects.filter(session_key=vorher).exists())
        self.assertFalse(self.zoe.sitzungen.exists())

    def test_abmelden_nimmt_die_zeile_mit(self):
        self.client.force_login(self.zoe)
        vorher = sitzungsschluessel(self.client)

        self.client.post(reverse("logout"))

        self.assertFalse(Sitzung.objects.filter(session_key=vorher).exists())
        self.assertFalse(self.zoe.sitzungen.exists())

    def test_das_konto_nimmt_seine_sitzungen_mit(self):
        """Without the constraint in the database, Django deletes them itself."""
        angemeldet(self.zoe)
        anonym = gespeichert({"sprache": "en"})

        self.zoe.delete()

        self.assertEqual(list(Sitzung.objects.values_list("session_key", flat=True)), [anonym])

    def test_ein_inhalt_ohne_nummer_nennt_kein_konto(self):
        """Django's sign-in writes a number there. Anything else saves without an account."""
        schluessel_ohne = gespeichert({SESSION_KEY: "kein-konto"})

        self.assertIsNone(Sitzung.objects.get(session_key=schluessel_ohne).konto_id)

    async def test_ebenso_asynchron(self):
        """None of our views saves that way, Django's asynchronous ones would."""
        speicher = SessionStore()
        await speicher.aset(SESSION_KEY, str(self.zoe.pk))

        await speicher.asave()

        sitzung = await Sitzung.objects.aget(session_key=speicher.session_key)
        self.assertEqual(sitzung.konto_id, self.zoe.pk)

    def test_die_verwaltung_zeigt_beim_loeschen_keinen_schluessel(self):
        """The page before deleting an account lists its sessions. The key opens one."""
        browser = angemeldet(self.zoe)
        self.client.force_login(administrator(email="chef@example.com"))

        seite = self.client.get(reverse("admin:accounts_customuser_delete", args=[self.zoe.pk]))

        self.assertContains(seite, "Sitzung: gültig bis")
        self.assertNotContains(seite, sitzungsschluessel(browser))


class OhneConstraint(TransactionTestCase):
    """The column has no constraint in the database ('Sitzung.konto').

    Outside a transaction, as the middleware saves: a constraint is checked
    when the save commits. Inside the transaction of a 'TestCase' it would
    only be checked at the end of the test.
    """

    serialized_rollback = True

    def test_ein_eben_geloeschtes_konto_haelt_das_speichern_nicht_auf(self):
        """A request of the account ends after the account is gone and saves its session.

        With a constraint, Django's 'create()' took the error for a key that
        is taken and tried the next one, without end. The row signs nobody
        in: Django finds no account behind it.
        """
        zoe = zaehlerin()
        inhalt = zeile(angemeldet(zoe)).get_decoded()
        nummer = zoe.pk
        zoe.delete()
        echt = SessionStore.create_model_instance
        versuche = []

        def noch_ein_versuch(speicher, daten):
            """Called once per try, each under a key of its own."""
            versuche.append(speicher.session_key)
            if len(versuche) > 1:
                self.fail("ein Schlüssel nach dem anderen, ohne Ende")
            return echt(speicher, daten)

        with mock.patch.object(
            SessionStore, "create_model_instance", autospec=True, side_effect=noch_ein_versuch
        ):
            schluessel_danach = gespeichert(inhalt)

        self.assertEqual(versuche, [schluessel_danach])
        self.assertEqual(Sitzung.objects.get(session_key=schluessel_danach).konto_id, nummer)
        browser = Client()
        browser.cookies[settings.SESSION_COOKIE_NAME] = schluessel_danach
        self.assertNotEqual(browser.get(reverse("passkey_list")).status_code, 200)


class SitzungenBeenden(TestCase):
    """'sign_out.sitzungen_beenden': by the column, whatever else is open."""

    def setUp(self):
        self.zoe = zaehlerin()

    def test_die_des_kontos_ausser_der_genannten(self):
        hier = angemeldet(self.zoe)
        angemeldet(self.zoe)
        fremd = angemeldet(nele())
        anonym = gespeichert({"sprache": "en"})

        sitzungen_beenden(self.zoe, ausser=sitzungsschluessel(hier))

        self.assertEqual(
            set(Sitzung.objects.values_list("session_key", flat=True)),
            {sitzungsschluessel(hier), sitzungsschluessel(fremd), anonym},
        )

    def test_ohne_ausnahme_jede(self):
        angemeldet(self.zoe)
        angemeldet(self.zoe)

        sitzungen_beenden(self.zoe)

        self.assertFalse(self.zoe.sitzungen.exists())

    def beenden(self) -> tuple[list[str], int]:
        """The queries of one call for a signed-in account, and how many sessions it decoded."""
        angemeldet(self.zoe)
        echt = SessionBase.decode
        with (
            mock.patch.object(SessionBase, "decode", autospec=True, side_effect=echt) as gelesen,
            CaptureQueriesContext(connection) as abfragen,
        ):
            sitzungen_beenden(self.zoe)
        self.assertFalse(self.zoe.sitzungen.exists())
        return [abfrage["sql"] for abfrage in abfragen.captured_queries], gelesen.call_count

    def test_ohne_die_anderen_zu_entschluesseln(self):
        """Two hundred sessions of others change nothing about the query, and none is read.

        Until 10/2026 every open session was decoded here, inside the
        transaction of the removal that holds the administrators.
        """
        allein, _gelesen = self.beenden()
        andere = nele()
        for nummer in range(100):
            gespeichert({SESSION_KEY: str(andere.pk), "nummer": nummer})
            gespeichert({"sprache": "en", "nummer": nummer})

        neben_zweihundert, gelesen = self.beenden()

        self.assertEqual(gelesen, 0)
        self.assertEqual(neben_zweihundert, allein)
        self.assertEqual(len(allein), 1, allein)
        self.assertIn("konto_id", allein[0])
        self.assertEqual(Sitzung.objects.count(), 200)


class EinEntfernterPasskey(TestCase):
    """Removing a passkey ends the account's other rows, and nobody else's."""

    def test_nur_die_eigene_zeile_des_kontos_bleibt(self):
        zoe = zaehlerin()
        passkey = schluessel(zoe)
        anderswo = angemeldet(zoe)
        fremd = angemeldet(nele())
        anonym = gespeichert({"sprache": "en"})
        self.client.force_login(zoe)

        self.client.post(reverse("passkey_delete", args=[passkey.pk]), {"passwort": "x"})

        self.assertEqual(
            list(zoe.sitzungen.values_list("session_key", flat=True)),
            [sitzungsschluessel(self.client)],
        )
        self.assertFalse(Sitzung.objects.filter(session_key=sitzungsschluessel(anderswo)).exists())
        self.assertTrue(Sitzung.objects.filter(session_key=sitzungsschluessel(fremd)).exists())
        self.assertTrue(Sitzung.objects.filter(session_key=anonym).exists())


class AlleAbmelden(TestCase):
    """'manage.py sign_out_everyone' ends the sessions of both tables."""

    def test_auch_die_eines_abbilds_aus_djangos_tabelle(self):
        """A dump from before 10/2026 holds them there. The migration would move them over."""
        zoe = zaehlerin()
        angemeldet(zoe)
        gespeichert({SESSION_KEY: str(zoe.pk)}, DjangosSpeicher)
        ausgabe = io.StringIO()

        call_command("sign_out_everyone", stdout=ausgabe)

        self.assertFalse(Sitzung.objects.exists())
        self.assertFalse(Session.objects.exists())
        self.assertIn("Beendete Sitzungen der Website: 2.", ausgabe.getvalue())


class AbbildOhneDieNeueTabelle(TransactionTestCase):
    """A dump from before 10/2026, loaded with psql: no table for 'Sitzung' yet.

    'sign_out_everyone' runs in today's image there ('DEPLOYMENT.md'). It
    failed on the missing table, and the dump's sessions applied again under
    the old version, or moved over with the migration under this one.
    """

    serialized_rollback = True

    def setUp(self):
        with connection.schema_editor() as editor:
            editor.delete_model(Sitzung)

        def wieder_anlegen():
            with connection.schema_editor() as editor:
                editor.create_model(Sitzung)

        self.addCleanup(wieder_anlegen)

    def test_jede_sitzung_des_abbilds_endet(self):
        gespeichert({SESSION_KEY: str(zaehlerin().pk)}, DjangosSpeicher)
        ausgabe = io.StringIO()

        call_command("sign_out_everyone", stdout=ausgabe)

        self.assertFalse(Session.objects.exists())
        self.assertIn("Beendete Sitzungen der Website: 1.", ausgabe.getvalue())


class Umzug(TestCase):
    """The migration that moved the open sessions into the new table ('accounts/0003')."""

    def setUp(self):
        self.zoe = zaehlerin()

    def test_wer_angemeldet_war_bleibt_es(self):
        """The same key and content in Django's table before, the browser holds its cookie."""
        browser = angemeldet(self.zoe)
        neu = zeile(browser)
        Session.objects.create(
            session_key=neu.session_key,
            session_data=DjangosSpeicher().encode(neu.get_decoded()),
            expire_date=neu.expire_date,
        )
        neu.delete()

        UMZUG.umziehen(apps, None)

        self.assertFalse(Session.objects.exists())
        self.assertEqual(zeile(browser).konto, self.zoe)
        self.assertEqual(browser.get(reverse("passkey_list")).status_code, 200)

    def test_mit_dem_inhalt_wie_er_war(self):
        angemeldet_bei_django = gespeichert(
            {SESSION_KEY: str(self.zoe.pk), "sprache": "en"}, DjangosSpeicher
        )
        anonym = gespeichert(
            {"passkey_challenge": {"challenge": "x", "gefragt": 1}}, DjangosSpeicher
        )

        UMZUG.umziehen(apps, None)

        self.assertEqual(
            SessionStore(angemeldet_bei_django).load(),
            {SESSION_KEY: str(self.zoe.pk), "sprache": "en"},
        )
        self.assertEqual(Sitzung.objects.get(session_key=angemeldet_bei_django).konto, self.zoe)
        self.assertIsNone(Sitzung.objects.get(session_key=anonym).konto_id)

    def test_was_niemanden_haelt_bleibt_zurueck(self):
        """Expired, or under another key: Django reads neither as a sign-in."""
        abgelaufen = gespeichert({SESSION_KEY: str(self.zoe.pk)}, DjangosSpeicher)
        Session.objects.filter(session_key=abgelaufen).update(
            expire_date=timezone.now() - timedelta(seconds=1)
        )
        with self.settings(SECRET_KEY="ein-anderer-schluessel-nur-fuer-diesen-test"):
            gespeichert({SESSION_KEY: str(self.zoe.pk)}, DjangosSpeicher)

        UMZUG.umziehen(apps, None)

        self.assertFalse(Sitzung.objects.exists())
        self.assertFalse(Session.objects.exists())

    def test_ein_verschwundenes_konto_bleibt_leer(self):
        """The content names an account deleted since. Django's table kept such sessions."""
        verschwunden = gespeichert({SESSION_KEY: "999999"}, DjangosSpeicher)

        UMZUG.umziehen(apps, None)

        self.assertIsNone(Sitzung.objects.get(session_key=verschwunden).konto_id)

    def test_zurueck_in_djangos_tabelle(self):
        """Backwards the code of before reads them again, under the same key."""
        schluessel_vorher = sitzungsschluessel(angemeldet(self.zoe))
        anonym = gespeichert({"sprache": "en"})

        UMZUG.zurueckziehen(apps, None)

        self.assertFalse(Sitzung.objects.exists())
        self.assertEqual(
            set(Session.objects.values_list("session_key", flat=True)), {schluessel_vorher, anonym}
        )
        self.assertEqual(DjangosSpeicher(schluessel_vorher).load()[SESSION_KEY], str(self.zoe.pk))
        self.assertEqual(DjangosSpeicher(anonym).load(), {"sprache": "en"})


class UmzugInBloecken(TestCase):
    """The migration writes a block while it reads, both ways, instead of one list of all.

    Collected before the first write, every open session stood in memory at
    once, during the deployment. 'BLOCK' is two here, so five sessions take
    three blocks.
    """

    def setUp(self):
        self.zoe = zaehlerin()
        bloecke = mock.patch.object(UMZUG, "BLOCK", 2)
        bloecke.start()
        self.addCleanup(bloecke.stop)

    def geschrieben(self, tun) -> dict[type, list[int]]:
        """How many rows each write held, by the model it wrote."""
        original = QuerySet.bulk_create
        groessen: dict[type, list[int]] = {}

        def mitzaehlen(menge, objekte, *args, **kwargs):
            objekte = list(objekte)
            groessen.setdefault(menge.model, []).append(len(objekte))
            return original(menge, objekte, *args, **kwargs)

        with mock.patch.object(QuerySet, "bulk_create", autospec=True, side_effect=mitzaehlen):
            tun(apps, None)
        return groessen

    def test_hin(self):
        schluessel_hin = {gespeichert({SESSION_KEY: str(self.zoe.pk)}, DjangosSpeicher)}
        schluessel_hin |= {gespeichert({"sprache": "en"}, DjangosSpeicher) for _ in range(4)}
        with self.settings(SECRET_KEY="ein-anderer-schluessel-nur-fuer-diesen-test"):
            gespeichert({SESSION_KEY: str(self.zoe.pk)}, DjangosSpeicher)

        groessen = self.geschrieben(UMZUG.umziehen)

        self.assertEqual(groessen[Sitzung], [2, 2, 1])
        self.assertEqual(set(Sitzung.objects.values_list("session_key", flat=True)), schluessel_hin)
        self.assertEqual(Sitzung.objects.filter(konto=self.zoe).count(), 1)
        self.assertFalse(Session.objects.exists())

    def test_zurueck(self):
        schluessel_zurueck = {gespeichert({"sprache": "en"}) for _ in range(5)}
        abgelaufen = gespeichert({"sprache": "de"})
        Sitzung.objects.filter(session_key=abgelaufen).update(
            expire_date=timezone.now() - timedelta(seconds=1)
        )

        groessen = self.geschrieben(UMZUG.zurueckziehen)

        self.assertEqual(groessen[Session], [2, 2, 1])
        self.assertEqual(
            set(Session.objects.values_list("session_key", flat=True)), schluessel_zurueck
        )
        self.assertFalse(Sitzung.objects.exists())
