import os
import sys
from datetime import timedelta
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from django.utils.csp import CSP
from environs import Env

from django_project.routes import freie_praefixe

env = Env()
env.read_env()

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = env.str("SECRET_KEY")

DEBUG = env.bool("DEBUG", default=False)

#: The value both templates ship ('.env.example'). It stands in a public
#: repository, so it hides nothing.
ADMIN_URL_PLATZHALTER = "a-path-of-your-own/"

#: The path of the real admin, mounted as it is ('urls.py'). Anonymous
#: visitors get a 404 there ('middleware.py').
ADMIN_URL = env.str("ADMIN_URL")

# Checked at start, like the key of the backups below. Empty, the admin took
# over the start page. Without the closing slash its sign-in page was called
# '/pathlogin/'. With a leading one it answered only at '//path/', without
# its hiding. The placeholder is known to everyone who read the template.
# And under 'admin/' waits the decoy, which the real admin would push aside.
if (
    not ADMIN_URL.endswith("/")
    or ADMIN_URL.startswith("/")
    or ADMIN_URL.strip("/") in ("", "admin")
    or ADMIN_URL == ADMIN_URL_PLATZHALTER
):
    raise ImproperlyConfigured(
        "ADMIN_URL has to be a path of your own that ends with '/' and does not start "
        "with it, for example 'verwaltung-x7/'. Not empty, not 'admin/' and not the "
        f"placeholder {ADMIN_URL_PLATZHALTER!r} from the template. Now it is {ADMIN_URL!r}."
    )

# Nor next to a path that opens without signing in, in either language
# ('routes.freie_praefixe'). The middleware lets those through before it hides
# the admin: under one of them the real admin showed its sign-in page to
# everyone (until 10/2026), above one it covered that page.
_admin_pfad = "/" + ADMIN_URL
if _frei := next(
    (
        praefix
        for sprache in ("de", "en")
        for praefix in freie_praefixe(sprache)
        if _admin_pfad.startswith(praefix) or praefix.startswith(_admin_pfad)
    ),
    None,
):
    raise ImproperlyConfigured(
        f"ADMIN_URL {ADMIN_URL!r} meets {_frei!r}, which opens without signing in. "
        "Under it the admin would show itself to everyone, above it it would cover that "
        "path. Choose a path of your own, for example 'verwaltung-x7/'."
    )


def _version_des_image() -> str:
    datei = BASE_DIR / "VERSION"
    return datei.read_text(encoding="utf-8").strip() if datei.is_file() else ""


# Which version is running. It stands in the account menu so that a question
# from the cinema makes clear what is being talked about. The image writes it
# into 'VERSION' when it is built ('Dockerfile', 'bin/version.sh'): the version
# number of a release or branch and hash from 'image.yml', the state of the
# checkout when built on the machine itself. The file wins over the
# environment, so a value left in the '.env' from such a build does not cover
# the number of a published image. Until 10/2026 the environment won, and
# 'compose.yaml' emptied it for that. An older image, which knows only the
# environment, then showed no version. The environment counts where there is
# no file: on the developer's machine. Without either it is "lokal".
APP_VERSION = _version_des_image() or env.str("APP_VERSION", default="") or "lokal"

#: Where the administration writes its backups. In operation this points to
#: the folder 'bin/backup.sh' fills too (compose mounts it). Locally it lies
#: next to the dumps from 'bin/full-dumpdata.sh'.
BACKUP_DIR = Path(env.str("BACKUP_DIR", default=str(BASE_DIR / "data" / "backups" / "complete")))

#: Shorter than this a key can be guessed faster than PBKDF2 can slow it down.
BACKUP_KEY_MINDESTLAENGE = 32

#: The key every backup is encrypted with ('encryption.py'). In operation
#: the application does not start without it. Refusing only the backup would
#: be quieter: a nightly run that fails is noticed on the day the backup is
#: needed. During development the key is optional. Without it the states are
#: written as before, and the scripts say so.
BACKUP_KEY = env.str("BACKUP_KEY", default="") if DEBUG else env.str("BACKUP_KEY")

if not DEBUG and len(BACKUP_KEY) < BACKUP_KEY_MINDESTLAENGE:
    raise ImproperlyConfigured(
        f"BACKUP_KEY is missing or shorter than {BACKUP_KEY_MINDESTLAENGE} characters. "
        "Create one with 'openssl rand -base64 48' and keep a copy away from this "
        "machine: without the key no backup can be read."
    )

ALLOWED_HOSTS = ["localhost", "127.0.0.1", *env.list("EXTRA_ALLOWED_HOSTS", default=[])]


CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])


HINTER_PROXY = env.bool("BEHIND_TLS_PROXY", default=False)

if HINTER_PROXY:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

INSTALLED_APPS = [
    # Instead of "django.contrib.admin": our version, see 'admin_site.py'.
    "django_project.apps.VerwaltungsAdminConfig",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Registers the trigram lookups of pg_trgm. Harmless under SQLite: the
    # search only uses them where the connection is PostgreSQL.
    "django.contrib.postgres",
    # 3rd party
    "crispy_forms",
    "crispy_bootstrap5",
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "axes",
    # Local
    "accounts",
    "organisation",
    "katalog",
    "inventur",
    "api",
    # Builds the demo stock ('manage.py demo'). Its one model is the marker
    # ('Demokennzeichen'). In operation the table stays empty.
    "demo",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # Far up, so the policy is also attached to the responses a middleware below
    # gives itself (redirect to the block page, rejection by 'axes').
    "django.middleware.csp.ContentSecurityPolicyMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # After authentication, because it asks the account ('CustomUser.sprache').
    # Before the messages: a lazy message ('gettext_lazy') only becomes text when
    # the messages go into the session, and that has to happen in the chosen
    # language. The other way round, "Der Sollbestand wird in der Vorbereitung
    # geklärt" arrived in German on an English page.
    "django_project.middleware.SprachwahlMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_project.middleware.KeinCacheFuerPrivates",
    "django_project.middleware.StaffOnlyMiddleware",
    # Very last: it catches the lockout message of 'axes' and turns it into a
    # response. Before it, the exception would stand uncaught in the room.
    "axes.middleware.AxesMiddleware",
]

#: 'axes' first: it lets a login through only as long as the account is not
#: locked, and then hands it on to Django's own check. The **standalone**
#: version is the right one because it does not authenticate itself.
#: That stays with the 'ModelBackend'.
AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
]

ROOT_URLCONF = "django_project.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "django_project.help.hilfe",
                "django_project.context_processors.version",
                "django_project.context_processors.demo",
                "django_project.context_processors.sprachen",
                "django_project.context_processors.farbwahl",
                "django_project.context_processors.erscheinungsbild",
                "django_project.context_processors.mail",
            ],
        },
    },
]

WSGI_APPLICATION = "django_project.wsgi.application"

# Two pages post a set of fields per row. The import plan carries every value
# of every row between its two steps, because the server keeps no state in
# between, and the check list sends note, tick and what the row showed, per
# article (six at most). A plan has at most 3,000 rows, a row sends at most 20
# fields, the page seven of its own ('HOECHSTENS_PLANZEILEN' and
# 'FELDER_JE_PLANZEILE' in 'katalog/forms/imports.py'): 60,007, rounded up.
# The bytes, measured on such plans: about 0.4 kB per new article, 1.1 kB for
# a conflict with its hints. 3,000 conflicts with what someone types while
# checking come to about 4 MB. Django's default of 2.5 MB is too little, 5 MB
# leave room. The plan refuses beyond both before it is shown
# ('plan_zu_gross'). Until 10/2026 the limit was 25,000 fields, and a plan of
# more than about 1,450 new articles was shown, gone through and then refused
# with a bare 400.
#
# Both limits apply to every request, a sign-in without an account too. They
# bound what reading a body costs, but only its fields and its bytes that are
# not a file. A file part of a multipart body counts against neither. Django
# writes it to a temporary file ('TemporaryFileUploadHandler'), as many and
# as large as the body brings. Two more bounds cover that. The proxy caps the
# whole body, file parts included ('request_body' in the 'Caddyfile'). And the
# API reads JSON only ('DEFAULT_PARSER_CLASSES'), so no endpoint parses a
# multipart body and the anonymous doors answer 415 before a part is read. On
# the website a multipart body still reaches 'CsrfViewMiddleware', which reads
# the fields, so there the proxy is the bound. The upload forms read a file on
# purpose, under their own limit: the import and the target stock up to
# 'MAX_UPLOAD_MB' ('validation.HoechstensGross'), the logo less
# ('organisation.images.HOECHSTGROESSE'). A backup is never uploaded, it is
# restored from a snapshot already on disk.
#
# What the two limits cost, measured on the development machine (10/2026):
# 61,000 empty fields take 50 ms, 5 MB in 61,000 fields 240 ms. The password
# hash of a single sign-in attempt takes 130 ms (PBKDF2 with Django's 1.5
# million rounds), and the decoy admin runs it as well ('accounts/honeypot.py').
# Whoever wants to keep the server busy with fields gets no further with the
# largest body than with two wrong passwords. Before, at 2.5 MB in 25,000
# fields (110 ms), it was one.
DATA_UPLOAD_MAX_NUMBER_FIELDS = 61_000
DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024


def _datenbank() -> dict:
    """The connection: in compose taken apart ('POSTGRES_*'), elsewhere 'DATABASE_URL'.

    Until 10/2026 compose put the password into an address, and a password with
    "/", "#", "?" or "%" broke it. The start ended in a 'ParseError' about a
    'DATABASE_URL' that does not even stand in the '.env', and 'openssl rand
    -base64 48', the command named for the other secrets, makes a "/" two times
    in three. Taken apart, the password goes through as it is. 'POSTGRES_HOST'
    decides, and compose sets it. Development and the CI keep the address.
    """
    if host := env.str("POSTGRES_HOST", default=""):
        return {
            "ENGINE": "django.db.backends.postgresql",
            "HOST": host,
            "PORT": env.int("POSTGRES_PORT", default=5432),
            "NAME": env.str("POSTGRES_DB", default="crosstally"),
            "USER": env.str("POSTGRES_USER", default="crosstally"),
            "PASSWORD": env.str("POSTGRES_PASSWORD"),
        }
    return env.dj_db_url("DATABASE_URL")


DATABASES = {"default": _datenbank()}
# Assign, not 'setdefault': 'dj_db_url' sets the value to 0 itself, a
# 'setdefault' would never apply, and every request would open a new connection.
DATABASES["default"]["CONN_MAX_AGE"] = env.int("CONN_MAX_AGE", default=60)
DATABASES["default"]["CONN_HEALTH_CHECKS"] = True

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

# Our own translations take precedence over those of the packages. Here stands
# only what Django itself has not translated yet: its German catalog lags
# behind the version, and without this the administration would read "Run"
# instead of "Ausführen".
LOCALE_PATHS = [BASE_DIR / "locale"]

# Exactly these two. Without the list Django would allow every language it
# ships a catalog for, and an unknown code on the account would go unnoticed.
LANGUAGES = [("de", "Deutsch"), ("en", "English")]

# The server's language. It applies where nobody is signed in (login form,
# error pages), and it is the default for every new account
# ('accounts.models.sprachvorgabe'). Changing it changes nothing about the
# accounts that already exist: there the choice is fixed.
#
# "de" and not "de-de": that is what the language is called in 'LANGUAGES'
# and on the account too. Otherwise the 'Content-Language' header would say
# one thing or the other. It changes nothing about the formats, Django falls
# back from "de-de" to "de" anyway. If something stands here that is not in
# 'LANGUAGES', Django's own check reports it at startup ('translation.E004').
LANGUAGE_CODE = env.str("LANGUAGE_CODE", default="de")

# Without an account the browser remembers a chosen language in a cookie
# ('middleware.sprache_merken'), until the window closes. No script reads it,
# and no other site gets it.
LANGUAGE_COOKIE_HTTPONLY = True
LANGUAGE_COOKIE_SAMESITE = "Lax"

# The time zone of operation. Stored in UTC ('USE_TZ'), displayed in this
# zone: cut-off dates, closing times and the names of the backups.
TIME_ZONE = env.str("TIME_ZONE", default="Europe/Berlin")

USE_I18N = True

USE_TZ = True

STATIC_URL = "static/"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

#: The tests run without a manifest, otherwise they would hang on
#: 'collectstatic'. See 'django_project/test_runner.py'.
TEST_RUNNER = "django_project.test_runner.Testlauf"

#: Whether the test suite is running right now. Then the deviations from
#: 'test_runner.py' apply here too, because not every path takes the
#: 'Testlauf': the testing panel of VS Code calls 'manage.py test
#: --testrunner=…' with its own runner and sets 'TEST_RUN_PIPE' while doing so.
#: Django switches 'DEBUG' off in every test run, the manifest storage then
#: only knows what the last 'collectstatic' collected, and every rendered page
#: failed on a new script. 'test_settings.py' reads the file without these
#: markers and thus checks the values of operation.
TESTLAUF = sys.argv[1:2] == ["test"] or "TEST_RUN_PIPE" in os.environ

# --------------------------------------------------------------------------- #
# E-mail
# --------------------------------------------------------------------------- #
#
# The mails: the link for resetting the password, the invitation of an account
# created without one ('accounts/invitation.py'), the confirmation of a new
# address ('accounts/email_change.py') and the notifications about changes to
# an account ('accounts/notification.py'). They go through
# SMTP only when 'DEBUG' is off **and** a server is configured. Otherwise the
# mail lands on standard output. That way operation without 'DEBUG' can be
# reproduced on the developer's machine without anything going out. Without a
# server an invitation is refused and a new account needs a password.

_mail_server = env.str("EMAIL_HOST", default="")
_mail_ueber_smtp = not DEBUG and bool(_mail_server)

if _mail_ueber_smtp:
    # Implicit TLS (port 465) and STARTTLS (587) are mutually exclusive.
    # Whoever switches on the first does not need to switch off the second.
    _mail_ssl = env.bool("EMAIL_USE_SSL", default=False)
    MAILERS = {
        "default": {
            "BACKEND": "django.core.mail.backends.smtp.EmailBackend",
            "OPTIONS": {
                "host": _mail_server,
                "port": env.int("EMAIL_PORT", default=465 if _mail_ssl else 587),
                "username": env.str("EMAIL_HOST_USER", default=""),
                "password": env.str("EMAIL_HOST_PASSWORD", default=""),
                "use_ssl": _mail_ssl,
                "use_tls": env.bool("EMAIL_USE_TLS", default=not _mail_ssl),
                # A server that does not answer would otherwise hold one of the
                # few 'gunicorn' workers until it gets killed.
                "timeout": 10,
            },
        },
    }
    # Without a sender 'webmaster@localhost' would go out, and almost every
    # server rejects that. Better the start fails with a clear message.
    DEFAULT_FROM_EMAIL = env.str("DEFAULT_FROM_EMAIL")
else:
    MAILERS = {
        "default": {
            "BACKEND": "django.core.mail.backends.console.EmailBackend",
        },
    }
    DEFAULT_FROM_EMAIL = env.str("DEFAULT_FROM_EMAIL", default="webmaster@localhost")
SERVER_EMAIL = DEFAULT_FROM_EMAIL

AUTH_USER_MODEL = "accounts.CustomUser"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "home"

# The sessions of the website lie in the database as with Django's default,
# with the signed-in account in a column of its own ('accounts.Sitzung').
# Removing a passkey ends the account's sessions by that column. Without it,
# every open session had to be decoded to find them (until 10/2026).
SESSION_ENGINE = "accounts.session_store"

CRISPY_ALLOWED_TEMPLATE_PACKS = "bootstrap5"
CRISPY_TEMPLATE_PACK = "bootstrap5"

REST_FRAMEWORK = {
    # Not 'IsAuthenticated': a deactivated account would still get through via
    # the session. The default is therefore the stricter rule, and a new view is
    # correctly protected by itself instead of only through an entry one can
    # forget.
    "DEFAULT_PERMISSION_CLASSES": [
        "api.permissions.IstAktiverBenutzer",
    ],
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    # JSON only, no form and no multipart. The app speaks JSON, and the only
    # website call to the API is the theme switch, which also sends JSON
    # ('theme.js'). Without this, DRF parsed a multipart body on every
    # endpoint that reads one, the anonymous doors too ('token/erneuern/',
    # 'token/pruefen/', 'passkey/anmelden/'). Django writes the file parts of
    # such a body to a temporary file and counts them against neither
    # 'DATA_UPLOAD_MAX_MEMORY_SIZE' nor 'DATA_UPLOAD_MAX_NUMBER_FIELDS'. A body
    # that is not JSON now gets 415 before any part is read.
    "DEFAULT_PARSER_CLASSES": [
        "rest_framework.parsers.JSONParser",
    ],
    # DRF's handler, plus the one answer with its own data ('api/exceptions.py').
    "EXCEPTION_HANDLER": "api.exceptions.behandeln",
    # DRF's browsable interface exists only during development. In operation it
    # showed every signed-in counter the structure and fields of the API, and
    # nobody needs it there: the counting app speaks JSON.
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
        *(["rest_framework.renderers.BrowsableAPIRenderer"] if DEBUG else []),
    ],
    "TEST_REQUEST_DEFAULT_FORMAT": "json",
    "DEFAULT_VERSIONING_CLASS": "rest_framework.versioning.URLPathVersioning",
    "ALLOWED_VERSIONS": ["v1"],
    "DEFAULT_VERSION": "v1",
}

SIMPLE_JWT = {
    # One hour, not eight: signing out blocks only the refresh token, the
    # access token runs out on its own. The app renews it silently on a 401
    # ('api.js', 'refreshOnce'), so nobody notices (2026-10-09).
    "ACCESS_TOKEN_LIFETIME": timedelta(hours=1),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=14),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "CHECK_USER_IS_ACTIVE": True,
    # A changed password ends every sign-in made with the old one. Whoever
    # changes it because a device is gone means exactly that, and without this
    # the device kept counting for up to two weeks. The token carries a check
    # value of the stored password. The package compares it on every request,
    # 'api.serializers.account.TokenErneuernSerializer' when refreshing.
    "CHECK_REVOKE_TOKEN": True,
    # The user name instead of the number: a restored backup reassigns the
    # numbers (the accounts are in the file with a natural key), and the token
    # of a deleted account would otherwise sign in as the one that inherited its
    # number. The name is unique and stays.
    "USER_ID_FIELD": "username",
    # Signing in to the counting app counts as signing in: "Last sign-in" in
    # the user list showed "never" for whoever only counts in the app (until
    # 10/2026). Only the sign-in itself, not the silent renewal of the token.
    # The passkey sign-in of the app does the same ('api/passkey.py').
    "UPDATE_LAST_LOGIN": True,
}

# --------------------------------------------------------------------------- #
# Limiting login attempts ('axes')
# --------------------------------------------------------------------------- #
#
# Both login paths go through Django's 'authenticate()': the form of the web
# interface and the token endpoint of the counting app. A bolt in front of it
# therefore applies to both, without every place having to set it separately.
#
# Counting happens in the **database**, not in the cache: there is no
# 'CACHES' setting, so a cache would run per gunicorn worker on its own and
# the limit would apply as many times as there are workers.

#: The fifth failed attempt is the end. It already counts as rejected itself,
#: so four typos get through. Enough for someone who mistypes, too few for
#: someone who tries passwords.
AXES_FAILURE_LIMIT = 5

#: Then half an hour of rest. Long enough to make guessing pointless, short
#: enough that nobody has to call anyone because of it: the lock lifts by
#: itself.
AXES_COOLOFF_TIME = timedelta(minutes=30)

#: Locked is **user name and address together**. The name alone would let
#: anyone who knows it lock out an account. The address alone plays foul with
#: everyone behind the same connection. In the house that is everyone.
AXES_LOCKOUT_PARAMETERS = [["username", "ip_address"]]

#: Whoever signs in correctly starts again at zero.
AXES_RESET_ON_SUCCESS = True

#: Every failed attempt is a row in the database and can be analysed there.
#: A line in the log would say the same thing again. The **lockout** is still
#: logged, because that is an event and not a counter reading.
AXES_VERBOSE = False

#: No log of the **successful** logins. It would grow with every shift, nobody
#: reads it, and the question who counts is answered by the inventory itself.
AXES_DISABLE_ACCESS_LOG = True

#: The package otherwise registers its three models with English names under
#: "Axes". Visible instead is the proxy 'accounts.Fehlversuch', the same
#: table, labelled in German and boiled down to what counts here.
AXES_ENABLE_ADMIN = False

#: The rejection: a page for the web interface, JSON for the counting app.
AXES_LOCKOUT_CALLABLE = "accounts.lockout.gesperrt"

#: The name counted, cut to the length of an account's name. A longer one
#: ended in a server error under PostgreSQL, at both doors (until 10/2026).
AXES_USERNAME_CALLABLE = "accounts.lockout.benutzername_fuer_axes"

#: Where 'axes' reads the caller's address from. Behind the proxy it is in
#: 'X-Forwarded-For'. 'REMOTE_ADDR' there is only the address of Caddy, the
#: same for everyone. If the header were missing from this list, 'axes' would
#: find no address at all behind the proxy and lock the name alone: anyone who
#: knows a user name could lock out the account. Without a proxy (runserver)
#: 'REMOTE_ADDR' counts, as is the package's default.
AXES_IPWARE_META_PRECEDENCE_ORDER = (
    ("HTTP_X_FORWARDED_FOR", "REMOTE_ADDR") if HINTER_PROXY else ("REMOTE_ADDR",)
)

#: How many hops **before Caddy** appear in 'X-Forwarded-For'. Caddy writes the
#: header itself and is therefore not in it: on a call from the internet only
#: the caller is in it, the number is 0. What an untrusted caller writes in
#: himself Caddy throws away. If 'tailscale serve' is in front, Caddy appends
#: its address, then 1. Reliable is only what stands, counted from the
#: **right**, behind this number. Count wrong, and 'axes' finds no address or
#: a proxy's and locks everyone out at once.
AXES_IPWARE_PROXY_COUNT = env.int("PROXY_COUNT", default=0) if HINTER_PROXY else None

# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #
#
# Without a setting of its own, Django sends server errors **exclusively** to
# 'mail_admins'. And without 'ADMINS' that means: nowhere. An error in
# operation would thus leave no trace, because the default's console handler
# only applies with 'DEBUG'.
#
# Written to standard output. In the container the service that starts it
# collects it. Logging next to it would mean maintaining a file nobody
# rotates.

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "knapp": {
            "format": "{asctime} {levelname} {name}: {message}",
            "style": "{",
        },
    },
    "handlers": {
        "konsole": {
            "class": "logging.StreamHandler",
            "formatter": "knapp",
        },
    },
    "root": {"handlers": ["konsole"], "level": "WARNING"},
    "loggers": {
        # The tracebacks of the 500s. 'propagate' off, otherwise every line
        # would appear twice: once here, once through the root.
        "django.request": {
            "handlers": ["konsole"],
            "level": "ERROR",
            "propagate": False,
        },
        # Lockouts after too many failed attempts.
        "axes": {
            "handlers": ["konsole"],
            "level": "WARNING",
            "propagate": False,
        },
    },
}

# --------------------------------------------------------------------------- #
# Content-Security-Policy
# --------------------------------------------------------------------------- #
#
# The browser executes only what comes from this origin. An injected <script>
# thus stays put, even if a template once lacks an 'escape'. This is possible
# because the page loads nothing foreign: Bootstrap, Tom Select and SortableJS
# lie under 'static/vendor/', our own scripts under 'static/js/', and none of
# them is in the page any more, not even the theme script in the head
# ('static/js/theme.js').
#
# **Without a nonce**, and on purpose: the policy is thus a fixed text. In
# operation WhiteNoise serves the start page of the counting app, bypassing
# every middleware ('wsgi.py'). There would be nobody there to write a nonce
# into the page. So the same thing applies word for word to both paths.
#
# The policy also applies during development: a violation should show up on
# the developer's machine (browser console) and not only in operation.
SECURE_CSP = {
    "default-src": [CSP.SELF],
    "script-src": [CSP.SELF],
    # Also applies to 'style' attributes: until 09/2026 the one relaxation stood
    # here ('style-src-attr' with 'unsafe-inline') for around 130 widths in the
    # templates. They are now classes in 'site.css', the progress bar a <progress>.
    # Measured, no such attribute came from a script: Bootstrap, Tom Select and
    # SortableJS set styles through the CSSOM, and the policy does not block that.
    "style-src": [CSP.SELF],
    # 'data:' for the arrows and ticks that Bootstrap carries as SVG in the stylesheet.
    "img-src": [CSP.SELF, "data:"],
    "font-src": [CSP.SELF],
    # The counting app and the background buttons only talk to our own API.
    "connect-src": [CSP.SELF],
    "manifest-src": [CSP.SELF],
    "worker-src": [CSP.SELF],
    "object-src": [CSP.NONE],
    # An injected <base> would otherwise bend every relative address.
    "base-uri": [CSP.SELF],
    # Forms only go here, because 'default-src' does not cover that.
    "form-action": [CSP.SELF],
    # Like 'X-Frame-Options: DENY', for browsers that only listen to this any more.
    "frame-ancestors": [CSP.NONE],
}

#: One year, that is what the browsers' preload list demands.
HSTS_FUER_PRELOAD = 31_536_000

if not DEBUG:
    SECURE_SSL_REDIRECT = True
    # Short to start with: HSTS is a promise to the browser that cannot be
    # revoked. Once the certificate is reliable, the duration should be raised
    # through 'HSTS_SECONDS'.
    SECURE_HSTS_SECONDS = env.int("HSTS_SECONDS", default=300)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    # 'preload' is the consent to be included in the preload list, and that
    # demands the year. With a shorter duration the flag would be a promise
    # the header itself does not keep. The inclusion would be rejected. That
    # is why it hangs on the duration instead of standing next to it.
    SECURE_HSTS_PRELOAD = SECURE_HSTS_SECONDS >= HSTS_FUER_PRELOAD
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    LANGUAGE_COOKIE_SECURE = True

if TESTLAUF:
    STORAGES["staticfiles"] = {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}
    SECURE_SSL_REDIRECT = False
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
    LANGUAGE_CODE = "de"
    TIME_ZONE = "Europe/Berlin"
