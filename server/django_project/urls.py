"""The addresses of the project.

The addresses of the web interface go through 'routes.pfad' and thus follow
the server's language ('/haeuser/' or '/sites/'). What the counting app,
Django or the browser know for certain stays: '/api/', '/app/', '/accounts/',
'/admin/', '/icons/'.

The counting PWA lies under '/app/' and thus on the **same origin** as the
API: no CORS, no preflights, one deployment. The service worker can only
control the scope it lies under itself. That is why all its extras are served
from there too and not through '/static/'.
"""

import json

from django.conf import settings
from django.contrib import admin
from django.http import Http404, HttpResponse, HttpResponseNotFound, JsonResponse
from django.urls import include, path, re_path
from django.utils.html import escape
from django.utils.translation import gettext as _
from django.views.decorators.http import require_safe
from django.views.generic import RedirectView
from django.views.static import serve

from accounts.passkey import (
    PasskeyAnlegenOptionenView,
    PasskeyAnlegenView,
    PasskeyAnmeldenOptionenView,
    PasskeyAnmeldenView,
    PasskeyBestaetigenOptionenView,
    PasskeyBestaetigenView,
    PasskeyListeView,
    PasskeyLoeschenView,
)
from accounts.password_reset import (
    GedrosseltePasswordResetView,
    GemeldetePasswordResetConfirmView,
    PasswortVergessenGesendetView,
)
from accounts.views import (
    AnmeldenView,
    EinladungFertigView,
    EinladungView,
    EmailAendernView,
    EmailBestaetigenView,
    EmailBestaetigtView,
    EmailWechselFertigView,
    GemeldetePasswordChangeView,
    KeinZugriffView,
)
from organisation.icons import icon
from organisation.models import STANDARDNAME, Erscheinungsbild

from .routes import pfad
from .settings import ADMIN_URL, BASE_DIR
from .views import DatenschutzView, ImpressumView

#: 'app/' builds into here (see 'vite.config.js').
PWA = BASE_DIR / "pwa"


#: What the app is called in the build ('vite.config.js'). When serving, this
#: becomes the name from the active version of the appearance.
PWA_NAME = STANDARDNAME


@require_safe
def pwa_start(request):
    """The app itself, with the name from the administration. Without a build, a notice page.

    The 'index.html' comes from the build and only knows the name from
    'vite.config.js'. Whoever gives the application its own name in the
    administration should read it in the counting app's tab too. That is why
    Django serves the start page and the manifest itself, not WhiteNoise
    ('wsgi.py' takes both out).
    """
    startseite = PWA / "index.html"
    if not startseite.exists():
        # 'npm ci' and not 'npm install', like the README: it installs what the
        # lock file names and leaves the lock file as it is.
        return HttpResponseNotFound(
            _(
                "Die Zähl-App ist noch nicht gebaut: im Ordner 'app/' einmal "
                "'npm ci' und dann 'npm run build' ausführen."
            )
        )
    name = Erscheinungsbild.aktuell().titel
    seite = startseite.read_text(encoding="utf-8").replace(
        f"<title>{PWA_NAME}</title>", f"<title>{escape(name)}</title>"
    )
    return HttpResponse(seite, content_type="text/html", headers={"Cache-Control": "max-age=60"})


@require_safe
def pwa_manifest(request):
    """The manifest of the counting app, with the name from the administration."""
    datei = PWA / "manifest.webmanifest"
    if not datei.exists():
        raise Http404
    manifest = json.loads(datei.read_text(encoding="utf-8"))
    name = Erscheinungsbild.aktuell().titel
    manifest["name"] = name
    manifest["short_name"] = name
    return JsonResponse(
        manifest,
        content_type="application/manifest+json",
        headers={"Cache-Control": "max-age=60"},
    )


#: The real admin. Stands here under its own name because 'urls_anonym'
#: takes it out. For anonymous visitors it does not exist.
admin_adresse = path(ADMIN_URL, admin.site.urls)

urlpatterns = [
    admin_adresse,
    # The decoy with our login form, see 'accounts/honeypot.py'.
    path("admin/", include("accounts.honeypot")),
    # Signing in with a passkey: the script on the login form addresses both
    # relative to the page ('passkey/options/'), so the decoy under '/admin/'
    # can show the same form down to the character ('accounts/honeypot.py').
    path(
        "accounts/login/passkey/options/",
        PasskeyAnmeldenOptionenView.as_view(),
        name="passkey_anmelden_optionen",
    ),
    path(
        "accounts/login/passkey/anmelden/",
        PasskeyAnmeldenView.as_view(),
        name="passkey_anmelden",
    ),
    # The keys of the own account, reachable for everyone with an account.
    pfad("accounts/passkeys/", PasskeyListeView.as_view(), name="passkey_list"),
    pfad(
        "accounts/passkeys/add/options/",
        PasskeyAnlegenOptionenView.as_view(),
        name="passkey_anlegen_optionen",
    ),
    pfad("accounts/passkeys/add/", PasskeyAnlegenView.as_view(), name="passkey_anlegen"),
    # An account without a password confirms with one of its keys before a new
    # key or a new address ('PasskeyBestaetigenView'). The passkey page and the
    # address page reach both through '{% url %}': on an English server the
    # segment reads 'confirm'.
    pfad(
        "accounts/passkeys/bestaetigen/options/",
        PasskeyBestaetigenOptionenView.as_view(),
        name="passkey_bestaetigen_optionen",
    ),
    pfad(
        "accounts/passkeys/bestaetigen/",
        PasskeyBestaetigenView.as_view(),
        name="passkey_bestaetigen",
    ),
    pfad(
        "accounts/passkeys/<int:pk>/delete/",
        PasskeyLoeschenView.as_view(),
        name="passkey_delete",
    ),
    # In front of Django's own: the reset with a limit per address and sender,
    # our own mail, and only with a mail server ('NurMitMailserver'), the page
    # after it as well. Same paths, same names, so every link keeps working.
    path(
        "accounts/password_reset/",
        GedrosseltePasswordResetView.as_view(),
        name="password_reset",
    ),
    path(
        "accounts/password_reset/done/",
        PasswortVergessenGesendetView.as_view(),
        name="password_reset_done",
    ),
    # Likewise: a new password, set by oneself or through the link, gets its mail.
    path(
        "accounts/password_change/",
        GemeldetePasswordChangeView.as_view(),
        name="password_change",
    ),
    path(
        "accounts/reset/<uidb64>/<token>/",
        GemeldetePasswordResetConfirmView.as_view(),
        name="password_reset_confirm",
    ),
    # The sign-in with a password lets the account in under its lock and asks
    # once more there ('AnmeldenView'). Same path and name as Django's.
    path("accounts/login/", AnmeldenView.as_view(), name="login"),
    path("accounts/", include("django.contrib.auth.urls")),
    # The invitation of a new account: reachable without signing in, like
    # everything under '/accounts/' ('StaffOnlyMiddleware'), with the segments
    # behind it in the server's language.
    pfad("accounts/einladung/<uidb64>/<token>/", EinladungView.as_view(), name="einladung"),
    pfad("accounts/einladung/fertig/", EinladungFertigView.as_view(), name="einladung_fertig"),
    # The own address, reachable for everyone with an account like the
    # passkeys. The link from the mail opens without signing in: whoever
    # confirms may sit at a device the account is not signed in on.
    pfad("accounts/email/", EmailAendernView.as_view(), name="email_aendern"),
    pfad("accounts/email/fertig/", EmailWechselFertigView.as_view(), name="email_fertig"),
    pfad(
        "accounts/email/bestaetigen/fertig/",
        EmailBestaetigtView.as_view(),
        name="email_bestaetigt",
    ),
    pfad(
        "accounts/email/bestaetigen/<str:token>/",
        EmailBestaetigenView.as_view(),
        name="email_bestaetigen",
    ),
    # Its own address and not under '/users/': the middleware sends counters
    # here, and the way there must not be locked itself.
    pfad("kein-zugriff/", KeinZugriffView.as_view(), name="kein_zugriff"),
    # Plain text, readable without signing in: it concerns everyone who has an
    # account here, including the counters who otherwise never get to see the
    # web interface. The middleware lets the address through ('FREIE_MUSTER').
    pfad("datenschutz/", DatenschutzView.as_view(), name="datenschutz"),
    # Likewise, as far as the business wrote one ('Erscheinungsbild.impressum').
    pfad("impressum/", ImpressumView.as_view(), name="impressum"),
    pfad("users/", include("accounts.urls")),
    pfad("haeuser/", include("organisation.urls")),
    # Fixed addresses for logo and icons. Without an own logo they point to
    # the bundled files, see 'organisation/icons.py'.
    re_path(r"^icons/(?P<name>[a-z0-9-]+)\.png$", icon, name="icon"),
    pfad("katalog/", include("katalog.urls")),
    path("api/", include("api.urls")),
    path("app/", pwa_start, name="pwa"),
    path("app/index.html", pwa_start),
    path("app/manifest.webmanifest", pwa_manifest, name="pwa_manifest"),
    path("", include("inventur.urls")),
    path("", RedirectView.as_view(pattern_name="inventur_list"), name="home"),
]

if settings.DEBUG:
    # In operation WhiteNoise serves the app's extras ('wsgi.py').
    # 'serve' is meant for development and for nothing else.
    urlpatterns += [re_path(r"^app/(?P<path>.*)$", serve, {"document_root": PWA})]
