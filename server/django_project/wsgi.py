"""The entry point for the application server and the serving of files.

Django itself serves no static files in operation. Instead of feeding a web
server with a shared directory, **WhiteNoise** hangs in front here: it gets
'/static/' from 'STATIC_ROOT' and the counting app under '/app/'. The proxy
in front thus only does TLS, and the same files do not have to lie in two
places.

The path has to stay '/app/': a service worker only controls the scope it is
served under itself.

During development this does not apply. There 'runserver' serves both itself.
"""

import os
import re

from django.core.wsgi import get_wsgi_application
from whitenoise import WhiteNoise

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "django_project.settings")

# Only after the environment is set: the settings read it.
from django.conf import settings
from django.utils.csp import CSP, build_policy

#: 'app/' builds into here (see 'vite.config.js').
PWA = settings.BASE_DIR / "pwa"

#: The same policy as on the pages from Django, built once: it carries no
#: nonce and is therefore a fixed text ('SECURE_CSP' in 'settings.py').
RICHTLINIE = build_policy(settings.SECURE_CSP)


#: What never changes because its content is in the name: the collected files
#: carry twelve digits of their checksum ('site.2223d855119a.css'), Vite writes
#: them into the name under 'assets/' as well. As a WSGI wrapper WhiteNoise
#: does not know the manifest (only its Django middleware brings the
#: detection), and without it one minute applied to **everything**: after that
#: the browser requested a dozen files anew on every page change (measured on
#: 2026-09-18). In the cinema basement every one of these round trips is one
#: too many. 'sw.js', 'index.html' and the manifest stay short, because on
#: them depends that a new version arrives.
UNVERAENDERLICH = re.compile(r"^/static/.+\.[0-9a-f]{12}\.[^/]+$|^/app/assets/")


def ist_unveraenderlich(pfad: str, adresse: str) -> bool:
    return UNVERAENDERLICH.search(adresse) is not None


def mit_richtlinie(kopfzeilen, pfad, adresse):
    """Attaches the Content-Security-Policy to what WhiteNoise serves itself.

    Django's middleware never sees these responses, because WhiteNoise is in
    front. Without this, of all things the start page of the counting app would
    run without a policy, and the service worker likewise: for it applies what
    is on its own file. On everything and not only on the 'index.html', so
    nobody has to think about which extension it counts for (a directly opened
    SVG is a document too).
    """
    kopfzeilen[str(CSP.HEADER_ENFORCE)] = RICHTLINIE
    # What the 'SecurityMiddleware' otherwise sets, from the same settings, so
    # both paths say the same. Without 'nosniff' the browser guesses itself
    # what a file with a wrong extension is.
    if settings.SECURE_CONTENT_TYPE_NOSNIFF:
        kopfzeilen["X-Content-Type-Options"] = "nosniff"
    if settings.SECURE_REFERRER_POLICY:
        kopfzeilen["Referrer-Policy"] = settings.SECURE_REFERRER_POLICY
    if settings.SECURE_HSTS_SECONDS:
        kopfzeilen["Strict-Transport-Security"] = _hsts()


def _hsts() -> str:
    """As the 'SecurityMiddleware' builds it, because it never sees the app's start page.

    Read when a file is added, not once at import: WhiteNoise fixes the
    headers per file at that moment, and a test hands in its own settings.
    """
    wert = f"max-age={settings.SECURE_HSTS_SECONDS}"
    if settings.SECURE_HSTS_INCLUDE_SUBDOMAINS:
        wert += "; includeSubDomains"
    if settings.SECURE_HSTS_PRELOAD:
        wert += "; preload"
    return wert


def anwendung(static_root, pwa) -> WhiteNoise:
    """Django behind WhiteNoise, as the application server runs it.

    A function, so a test can hand in folders of its own: on a fresh
    workstation and in the CI neither the collected files nor the built app
    exist, and the test client never passes through here.
    """
    app = WhiteNoise(
        get_wsgi_application(),
        add_headers_function=mit_richtlinie,
        immutable_file_test=ist_unveraenderlich,
        # '/app/' should serve the 'index.html' and not run into nothing.
        index_file=True,
        # WhiteNoise keeps its own list of file types. The manifest's extension
        # is not in it, and without the right type the browser does not accept it.
        mimetypes={".webmanifest": "application/manifest+json"},
    )

    if static_root and os.path.isdir(static_root):
        app.add_files(str(static_root), prefix=settings.STATIC_URL)

    # Without a build the folder does not exist. Then the notice page from
    # 'urls.py' takes over. That is the normal case on a fresh workstation.
    if pwa.is_dir():
        app.add_files(str(pwa), prefix="app/")
        # Start page and manifest are served by Django itself: they carry the name
        # from the administration, which the build does not know ('urls.pwa_start', 'pwa_manifest').
        for adresse in ("/app/", "/app/index.html", "/app/manifest.webmanifest"):
            app.files.pop(adresse, None)
    return app


application = anwendung(settings.STATIC_ROOT, PWA)
