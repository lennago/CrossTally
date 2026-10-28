"""The settings of operation, checked without running operation.

The test suite runs with 'SECURE_SSL_REDIRECT=False' ('test_runner.py'),
otherwise every page would come as a 301. That the redirect is in place in
operation is therefore seen by no other test. Here 'settings.py' is loaded
once more with the values of operation and questioned. That way it shows
when a setting only looks as if it applied ('CONN_MAX_AGE' did that once).
"""

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.core import mail
from django.core.exceptions import ImproperlyConfigured
from django.core.mail.backends.smtp import EmailBackend as SmtpBackend
from django.test import SimpleTestCase, override_settings
from environs import Env, EnvError

EINSTELLUNGEN = Path(__file__).with_name("settings.py")

#: What the probe keeps from the real environment: what Python itself needs.
GEERBT = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "VIRTUAL_ENV", "PYTHONPATH", "SYSTEMROOT")


def geladen(testlauf: str = "", datei: str | Path = EINSTELLUNGEN, **umgebung):
    """'settings.py' freshly executed, with this environment on top.

    'environs' reads the '.env' only for names missing from the environment.
    What is set here therefore wins. 'testlauf' simulates a test run: "argv"
    like 'manage.py test', "pipe" like the panel of VS Code. 'datei' is
    another copy of the file, and with it another 'BASE_DIR'. It also takes
    a 'str' because callers unpack dictionaries of strings, and pyright
    checks those against every keyword.
    """
    vorgaben = {
        "SECRET_KEY": "nur-fuer-den-test",
        "BACKUP_KEY": "nur-fuer-den-test-und-lang-genug-dafuer",
        "ADMIN_URL": "verwaltung/",
        "DATABASE_URL": "sqlite:///test.sqlite3",
    }
    # From a fixed base, not from the developer's shell, and without the local
    # '.env': both would decide what the settings of operation look like, and
    # the test would pass or fail with them (until 10/2026 it did). Without the
    # markers of a test run ('settings.TESTLAUF'): here the values of operation
    # count, not the deviations of the suite.
    basis = {name: os.environ[name] for name in GEERBT if name in os.environ}
    if testlauf == "pipe":
        basis["TEST_RUN_PIPE"] = "/run/probe"
    argv = ["manage.py", "test"] if testlauf == "argv" else ["manage.py"]
    with (
        patch.dict(os.environ, basis | vorgaben | umgebung, clear=True),
        patch.object(sys, "argv", argv),
        patch.object(Env, "read_env", lambda *args, **kwargs: None),
    ):
        spec = importlib.util.spec_from_file_location("einstellungen_probe", datei)
        assert spec is not None and spec.loader is not None
        modul = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(modul)
    return modul


class ImTestlauf(SimpleTestCase):
    """The suite's deviations apply as soon as 'manage.py test' runs, whatever the runner.

    The testing panel of VS Code brings its own runner and thus bypassed the
    'Testlauf': 28 page tests failed on the manifest of the static files.
    """

    def test_manage_py_test_schaltet_die_abweichungen(self):
        suite = geladen(testlauf="argv", DEBUG="0")
        self.assertTrue(suite.TESTLAUF)
        self.assertEqual(
            suite.STORAGES["staticfiles"]["BACKEND"],
            "django.contrib.staticfiles.storage.StaticFilesStorage",
        )
        self.assertFalse(suite.SECURE_SSL_REDIRECT)
        self.assertEqual(suite.PASSWORD_HASHERS, ["django.contrib.auth.hashers.MD5PasswordHasher"])

    def test_die_pipe_der_erweiterung_genuegt(self):
        self.assertTrue(geladen(testlauf="pipe", DEBUG="0").TESTLAUF)

    def test_die_suite_spricht_deutsch_was_auch_die_umgebung_sagt(self):
        """A new account takes the server's language, and the suite asserts German texts."""
        for testlauf in ("argv", "pipe"):
            with self.subTest(testlauf):
                suite = geladen(testlauf=testlauf, DEBUG="0", LANGUAGE_CODE="en", TIME_ZONE="UTC")
                self.assertEqual((suite.LANGUAGE_CODE, suite.TIME_ZONE), ("de", "Europe/Berlin"))
        betrieb = geladen(DEBUG="0", LANGUAGE_CODE="en", TIME_ZONE="UTC")
        self.assertEqual((betrieb.LANGUAGE_CODE, betrieb.TIME_ZONE), ("en", "UTC"))

    def test_das_zugriffstoken_laeuft_nach_einer_stunde_aus(self):
        """Signing out blocks only the refresh token. The access token has to run out soon."""
        self.assertEqual(geladen(DEBUG="0").SIMPLE_JWT["ACCESS_TOKEN_LIFETIME"], timedelta(hours=1))

    def test_ohne_beides_gelten_die_werte_des_betriebs(self):
        self.assertFalse(geladen(DEBUG="0").TESTLAUF)


class Backupschluessel(SimpleTestCase):
    """In operation the application does not start without the key of the backups.

    Refusing only the backup would be quieter. A nightly run that fails is
    noticed on the day the backup is needed.
    """

    def test_ohne_schluessel_startet_der_betrieb_nicht(self):
        with self.assertRaisesMessage(ImproperlyConfigured, "BACKUP_KEY"):
            geladen(DEBUG="0", BACKUP_KEY="")

    def test_ein_kurzer_schluessel_gilt_als_keiner(self):
        """PBKDF2 slows guessing down, it does not make a short key long."""
        with self.assertRaisesMessage(ImproperlyConfigured, "shorter than 32"):
            geladen(DEBUG="0", BACKUP_KEY="geheim")

    def test_mit_schluessel_startet_er(self):
        schluessel = "ein-schluessel-der-lang-genug-ist-0123"
        self.assertEqual(geladen(DEBUG="0", BACKUP_KEY=schluessel).BACKUP_KEY, schluessel)

    def test_beim_entwickeln_ist_er_freiwillig(self):
        self.assertEqual(geladen(DEBUG="1", BACKUP_KEY="").BACKUP_KEY, "")


class Verwaltungspfad(SimpleTestCase):
    """A path that does not hide the admin, or that breaks it, stops the start.

    Empty, the admin took over the start page. Without the closing slash its
    sign-in page was '/verwaltung-x7login/'. The placeholder of the templates
    stands in a public repository, and 'admin/' would push the decoy aside.
    """

    def test_ein_eigener_pfad_gilt(self):
        self.assertEqual(geladen(ADMIN_URL="verwaltung-x7/").ADMIN_URL, "verwaltung-x7/")

    def test_leer_startet_es_nicht(self):
        with self.assertRaisesMessage(ImproperlyConfigured, "ADMIN_URL has to be a path"):
            geladen(ADMIN_URL="")

    def test_ohne_schraegstrich_am_ende_startet_es_nicht(self):
        with self.assertRaisesMessage(ImproperlyConfigured, "'verwaltung-x7'"):
            geladen(ADMIN_URL="verwaltung-x7")

    def test_der_platzhalter_der_vorlage_startet_es_nicht(self):
        with self.assertRaisesMessage(ImproperlyConfigured, "placeholder"):
            geladen(ADMIN_URL="a-path-of-your-own/")

    def test_der_koeder_und_die_wurzel_bleiben_frei(self):
        for wert in ("admin/", "/admin/", "/"):
            with self.subTest(wert=wert), self.assertRaises(ImproperlyConfigured):
                geladen(ADMIN_URL=wert)

    def test_mit_schraegstrich_am_anfang_startet_es_nicht(self):
        """The admin answered only at '//verwaltung-x7/', without its hiding."""
        with self.assertRaisesMessage(ImproperlyConfigured, "does not start with it"):
            geladen(ADMIN_URL="/verwaltung-x7/")

    def test_unter_einem_freien_pfad_startet_es_nicht(self):
        """The middleware lets these through before it hides the admin.

        Under '/admin/x/' the real admin showed its sign-in page to everyone,
        under '/accounts/x/' its catch-all covered the site's own login.
        """
        for wert, frei in (
            ("admin/geheim-x7/", "/admin/"),
            ("accounts/x7/", "/accounts/"),
            ("api/x7/", "/api/"),
            ("app/x7/", "/app/"),
            ("icons/x7/", "/icons/"),
            ("static/x7/", "/static/"),
            ("datenschutz/x7/", "/datenschutz/"),
            ("privacy/x7/", "/privacy/"),
            ("no-access/x7/", "/no-access/"),
        ):
            with self.subTest(wert=wert), self.assertRaisesMessage(ImproperlyConfigured, frei):
                geladen(ADMIN_URL=wert)

    def test_ueber_einem_freien_pfad_startet_es_nicht(self):
        """Below 'users/' lies the language choice of the login form."""
        with self.assertRaisesMessage(ImproperlyConfigured, "/users/sprache/"):
            geladen(ADMIN_URL="users/")

    def test_ein_aehnlicher_name_gilt(self):
        for wert in ("adminbereich-x7/", "apix7/", "accounts-x7/"):
            with self.subTest(wert=wert):
                self.assertEqual(geladen(ADMIN_URL=wert).ADMIN_URL, wert)

    def test_auch_beim_entwickeln(self):
        """The development template carries the same placeholder."""
        with self.assertRaises(ImproperlyConfigured):
            geladen(DEBUG="1", ADMIN_URL="a-path-of-your-own/")

    def test_beide_vorlagen_tragen_den_platzhalter(self):
        """If a template changes its value, the check has to learn the new one."""
        platzhalter = geladen().ADMIN_URL_PLATZHALTER
        wurzel = EINSTELLUNGEN.parent.parent.parent
        for vorlage in (wurzel / ".env.example", wurzel / "server" / ".env.example"):
            with self.subTest(vorlage=vorlage.relative_to(wurzel)):
                inhalt = vorlage.read_text(encoding="utf-8").splitlines()
                self.assertIn(f"ADMIN_URL={platzhalter}", inhalt)


class Betriebswerte(SimpleTestCase):
    def test_ohne_debug_wird_auf_https_umgeleitet(self):
        betrieb = geladen(DEBUG="0")
        self.assertTrue(betrieb.SECURE_SSL_REDIRECT)
        self.assertTrue(betrieb.SESSION_COOKIE_SECURE)
        self.assertTrue(betrieb.CSRF_COOKIE_SECURE)

    def test_mit_debug_keine_umleitung(self):
        self.assertFalse(hasattr(geladen(DEBUG="1"), "SECURE_SSL_REDIRECT"))

    def test_skripte_lesen_das_csrf_token_aus_dem_cookie(self):
        """The scripts that send take it from there ('shared.js'): Django renews it on signing in.

        The page keeps the old one in its forms. A cookie only the server
        reads, or the token in the session, would leave the notes of an older
        page unsaved after signing in anew in another tab, and its row
        buttons, its order and its passkeys refused. In operation as in
        development, and the defaults of Django are what applies.
        """
        for debug in ("0", "1"):
            with self.subTest(DEBUG=debug):
                einstellungen = geladen(DEBUG=debug)
                self.assertEqual(
                    getattr(einstellungen, "CSRF_COOKIE_NAME", "csrftoken"), "csrftoken"
                )
                self.assertFalse(getattr(einstellungen, "CSRF_COOKIE_HTTPONLY", False))
                self.assertFalse(getattr(einstellungen, "CSRF_USE_SESSIONS", False))

    def test_passwoerter_behalten_djangos_hasher(self):
        """The fast hasher belongs to the test run alone ('test_runner.py')."""
        self.assertFalse(hasattr(geladen(DEBUG="0"), "PASSWORD_HASHERS"))

    def test_die_durchklickbare_schnittstelle_gibt_es_nur_beim_entwickeln(self):
        """In operation it showed every counter the structure of the API."""
        oberflaeche = "rest_framework.renderers.BrowsableAPIRenderer"
        for debug, erwartet in (("0", False), ("1", True)):
            with self.subTest(DEBUG=debug):
                klassen = geladen(DEBUG=debug).REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"]
                self.assertEqual(oberflaeche in klassen, erwartet)
                self.assertIn("rest_framework.renderers.JSONRenderer", klassen)

    def test_in_compose_geht_das_passwort_wie_es_ist(self):
        """Put into an address, "/", "#", "?" and "%" broke it (until 10/2026)."""
        passwort = "Ab3d/Ef+gH=#x?y%41z"
        datenbank = geladen(
            POSTGRES_HOST="db",
            POSTGRES_USER="crosstally",
            POSTGRES_DB="crosstally",
            POSTGRES_PASSWORD=passwort,
            # What an older 'compose.yaml' still puts beside it, broken by the password.
            DATABASE_URL=f"postgres://crosstally:{passwort}@db:5432/crosstally",
        ).DATABASES["default"]
        self.assertEqual(datenbank["ENGINE"], "django.db.backends.postgresql")
        self.assertEqual(
            (datenbank["HOST"], datenbank["PORT"], datenbank["NAME"], datenbank["USER"]),
            ("db", 5432, "crosstally", "crosstally"),
        )
        self.assertEqual(datenbank["PASSWORD"], passwort)
        self.assertEqual(datenbank["CONN_MAX_AGE"], 60)

    def test_ohne_postgres_host_gilt_die_adresse(self):
        datenbank = geladen(DATABASE_URL="sqlite:///probe.sqlite3").DATABASES["default"]
        self.assertEqual(datenbank["ENGINE"], "django.db.backends.sqlite3")

    def test_nur_sqlite_schreibt_von_anfang_an(self):
        """Why SQLite needs it: 'test_sqlite_writers.py'. psycopg would refuse the option."""
        sqlite = geladen(DATABASE_URL="sqlite:///probe.sqlite3").DATABASES["default"]
        self.assertEqual(sqlite["OPTIONS"]["transaction_mode"], "IMMEDIATE")
        for postgres in (
            geladen(DATABASE_URL="postgres://crosstally:geheim@db:5432/crosstally"),
            geladen(POSTGRES_HOST="db", POSTGRES_PASSWORD="geheim"),
        ):
            self.assertNotIn("transaction_mode", postgres.DATABASES["default"].get("OPTIONS", {}))

    def test_verbindungen_bleiben_offen(self):
        """'dj_db_url' sets the value to 0 itself. A 'setdefault' would never apply."""
        self.assertEqual(geladen().DATABASES["default"]["CONN_MAX_AGE"], 60)
        self.assertEqual(geladen(CONN_MAX_AGE="0").DATABASES["default"]["CONN_MAX_AGE"], 0)

    def test_hinter_dem_proxy_zaehlt_die_weitergereichte_adresse(self):
        """Otherwise 'axes' would only see Caddy and lock the name alone, for everyone."""
        betrieb = geladen(BEHIND_TLS_PROXY="true")
        self.assertEqual(
            betrieb.AXES_IPWARE_META_PRECEDENCE_ORDER, ("HTTP_X_FORWARDED_FOR", "REMOTE_ADDR")
        )
        # Caddy writes the header itself and is not in it.
        self.assertEqual(betrieb.AXES_IPWARE_PROXY_COUNT, 0)
        self.assertEqual(betrieb.SECURE_PROXY_SSL_HEADER, ("HTTP_X_FORWARDED_PROTO", "https"))

    def test_ohne_proxy_zaehlt_remote_addr(self):
        entwicklung = geladen(BEHIND_TLS_PROXY="false")
        self.assertEqual(entwicklung.AXES_IPWARE_META_PRECEDENCE_ORDER, ("REMOTE_ADDR",))
        self.assertIsNone(entwicklung.AXES_IPWARE_PROXY_COUNT)


class Mailversand(SimpleTestCase):
    SMTP = "django.core.mail.backends.smtp.EmailBackend"
    KONSOLE = "django.core.mail.backends.console.EmailBackend"
    SERVER = {"EMAIL_HOST": "smtp.example.de", "DEFAULT_FROM_EMAIL": "absender@example.de"}

    def test_im_betrieb_mit_server_geht_es_ueber_smtp(self):
        mailer = geladen(DEBUG="0", **self.SERVER).MAILERS["default"]
        self.assertEqual(mailer["BACKEND"], self.SMTP)
        self.assertEqual(mailer["OPTIONS"]["host"], "smtp.example.de")
        self.assertEqual(mailer["OPTIONS"]["port"], 587)
        self.assertTrue(mailer["OPTIONS"]["use_tls"])
        self.assertFalse(mailer["OPTIONS"]["use_ssl"])

    def test_django_nimmt_die_einstellung_an(self):
        """Next to 'MAILERS' Django tolerates none of the old names ('EMAIL_HOST' …).

        That would otherwise only show up at startup, not when loading 'settings.py'.
        """
        betrieb = geladen(DEBUG="0", **self.SERVER)
        verboten = [name for name in vars(betrieb) if name.startswith("EMAIL_")]
        self.assertEqual(verboten, [])
        with override_settings(MAILERS=betrieb.MAILERS):
            verbindung = mail.mailers["default"]
        assert isinstance(verbindung, SmtpBackend)
        self.assertEqual((verbindung.host, verbindung.timeout), ("smtp.example.de", 10))

    def test_zugangsdaten_und_absender_kommen_aus_der_umgebung(self):
        betrieb = geladen(
            DEBUG="0",
            EMAIL_HOST_USER="konto",
            EMAIL_HOST_PASSWORD="geheim",  # gitleaks:allow
            **self.SERVER,
        )
        optionen = betrieb.MAILERS["default"]["OPTIONS"]
        self.assertEqual((optionen["username"], optionen["password"]), ("konto", "geheim"))
        self.assertEqual(betrieb.DEFAULT_FROM_EMAIL, "absender@example.de")
        self.assertEqual(betrieb.SERVER_EMAIL, "absender@example.de")

    def test_ssl_schaltet_starttls_ab_und_nimmt_port_465(self):
        """Django rejects both at once. Whoever chooses SSL should not have to switch twice."""
        betrieb = geladen(DEBUG="0", EMAIL_USE_SSL="true", **self.SERVER)
        optionen = betrieb.MAILERS["default"]["OPTIONS"]
        self.assertTrue(optionen["use_ssl"])
        self.assertFalse(optionen["use_tls"])
        self.assertEqual(optionen["port"], 465)

    def test_mit_debug_bleibt_es_bei_der_konsole(self):
        """Even with a configured server. During development nothing goes out."""
        self.assertEqual(
            geladen(DEBUG="1", **self.SERVER).MAILERS["default"]["BACKEND"], self.KONSOLE
        )

    def test_ohne_server_bleibt_es_bei_der_konsole(self):
        """That way operation without 'DEBUG' can be reproduced on the developer's machine."""
        betrieb = geladen(DEBUG="0", EMAIL_HOST="")
        self.assertEqual(betrieb.MAILERS["default"]["BACKEND"], self.KONSOLE)

    def test_ein_server_ohne_absender_scheitert_beim_start(self):
        """'webmaster@localhost' would hardly be accepted by any server.

        That would otherwise only show up at the first mail, long after setup.
        """
        with patch.dict(os.environ):
            os.environ.pop("DEFAULT_FROM_EMAIL", None)
            with self.assertRaises(EnvError):
                geladen(DEBUG="0", EMAIL_HOST="smtp.example.de")


class Versionsangabe(SimpleTestCase):
    def test_die_umgebung_gewinnt(self):
        """Where no image brought a file: on the developer's machine."""
        self.assertEqual(geladen(APP_VERSION="0.3.0").APP_VERSION, "0.3.0")

    def test_das_image_bringt_sie_in_einer_datei_mit(self):
        """In a copy of the folder. Until 10/2026 the file lay in the tree.

        A run that broke off left it there, and every 'runserver' afterwards
        called itself "lokal-1a2b3c4".
        """
        ordner = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, ordner)
        kopie = ordner / "django_project" / "settings.py"
        kopie.parent.mkdir()
        shutil.copyfile(EINSTELLUNGEN, kopie)
        (ordner / "VERSION").write_text("lokal-1a2b3c4\n", encoding="utf-8")
        self.assertEqual(geladen(datei=kopie).APP_VERSION, "lokal-1a2b3c4")
        # An empty entry in the '.env' does not hide the file.
        self.assertEqual(geladen(datei=kopie, APP_VERSION="").APP_VERSION, "lokal-1a2b3c4")
        self.assertFalse((EINSTELLUNGEN.parent.parent / "VERSION").exists())

    def test_die_datei_des_image_gewinnt_gegen_die_umgebung(self):
        """A value left in the '.env' from a build on the machine covers no published number.

        Until 10/2026 the environment won, and 'compose.yaml' emptied it for
        that. An image from before then knows only the environment, and gone
        back to, it showed no version at all.
        """
        ordner = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, ordner)
        kopie = ordner / "django_project" / "settings.py"
        kopie.parent.mkdir()
        shutil.copyfile(EINSTELLUNGEN, kopie)
        (ordner / "VERSION").write_text("1.4.0\n", encoding="utf-8")
        self.assertEqual(geladen(datei=kopie, APP_VERSION="lokal-1a2b3c4").APP_VERSION, "1.4.0")

    def test_ohne_angabe_ist_es_der_eigene_rechner(self):
        with patch.dict(os.environ):
            os.environ.pop("APP_VERSION", None)
            self.assertEqual(geladen().APP_VERSION, "lokal")


SKRIPT = EINSTELLUNGEN.parent.parent / "bin" / "version.sh"
HASH = "1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b"


class Versionsskript(SimpleTestCase):
    """What a self-built image calls itself: the build argument, else the checkout's state."""

    def git(self, head: str, **dateien: str) -> Path:
        ordner = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, ordner)
        (ordner / "HEAD").write_text(head + "\n", encoding="utf-8")
        for name, inhalt in dateien.items():
            pfad = ordner / name.replace("__", "/")
            pfad.parent.mkdir(parents=True, exist_ok=True)
            pfad.write_text(inhalt, encoding="utf-8")
        return ordner

    def version(self, argument: str, git: Path) -> str:
        lauf = subprocess.run(
            ["sh", str(SKRIPT), argument, str(git)], capture_output=True, text=True, check=True
        )
        return lauf.stdout.strip()

    def test_das_argument_gewinnt(self):
        git = self.git("ref: refs/heads/main", refs__heads__main=HASH)
        self.assertEqual(self.version("0.3.0", git), "0.3.0")

    def test_der_zweig_aus_seiner_datei(self):
        git = self.git("ref: refs/heads/feat/thema", refs__heads__feat__thema=HASH + "\n")
        self.assertEqual(self.version("", git), "lokal-1a2b3c4")

    def test_der_zweig_aus_den_gepackten_verweisen(self):
        git = self.git(
            "ref: refs/heads/main",
            **{
                "packed-refs": "# pack-refs with: peeled fully-peeled sorted\n"
                f"{'f' * 40} refs/heads/main-alt\n"
                f"{HASH} refs/heads/main\n"
                f"{'e' * 40} refs/tags/v1\n"
            },
        )
        self.assertEqual(self.version("", git), "lokal-1a2b3c4")

    def test_ein_losgeloester_kopf_ist_der_commit_selbst(self):
        self.assertEqual(self.version("", self.git(HASH)), "lokal-1a2b3c4")

    def test_ohne_git_bleibt_es_unbekannt(self):
        ordner = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, ordner)
        self.assertEqual(self.version("", ordner), "unknown")
        self.assertEqual(self.version("", self.git("ref: refs/heads/main")), "unknown")

    def test_eine_git_datei_bleibt_unbekannt(self):
        """A worktree's '.git' is a file. Copied beside the script, it names no commit."""
        ordner = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, ordner)
        (ordner / ".git").write_text("gitdir: /anderswo/.git/worktrees/wt\n", encoding="utf-8")
        self.assertEqual(self.version("", ordner), "unknown")

    def test_der_build_verlangt_kein_git_verzeichnis(self):
        """Until 10/2026 'COPY .git' stopped each build from an archive, worktree or submodule."""
        wurzel = SKRIPT.parent.parent.parent
        if not (wurzel / "Dockerfile").exists():
            # In the image the repository root is not there.
            self.skipTest("Dockerfile liegt hier nicht")
        dockerfile = (wurzel / "Dockerfile").read_text(encoding="utf-8").splitlines()
        quellen = [z.split()[1:-1] for z in dockerfile if z.startswith("COPY ")]
        self.assertNotIn(".git", [quelle for zeile in quellen for quelle in zeile])
        self.assertIn(["server/bin/version.sh", ".gi[t]"], quellen)
        ignoriert = (wurzel / ".dockerignore").read_text(encoding="utf-8").splitlines()
        self.assertIn(".git/*", ignoriert)
        self.assertFalse({".git", ".git/"} & set(ignoriert))

    def test_was_neben_den_daten_liegt_kommt_nicht_ins_image(self):
        """Until 10/2026 a journal of SQLite, a copy of the '.env' and a log went along."""
        wurzel = SKRIPT.parent.parent.parent
        if not (wurzel / ".dockerignore").exists():
            self.skipTest(".dockerignore liegt hier nicht")
        ignoriert = (wurzel / ".dockerignore").read_text(encoding="utf-8").splitlines()
        for muster in ("**/*.sqlite3-*", "**/.env.*", "**/*.log"):
            with self.subTest(muster=muster):
                self.assertIn(muster, ignoriert)
