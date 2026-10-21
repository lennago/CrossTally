from http import HTTPStatus

from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.http import Http404, HttpResponse, HttpResponseForbidden
from django.shortcuts import redirect
from django.urls import Resolver404, resolve
from django.utils import translation
from django.utils.cache import add_never_cache_headers, patch_vary_headers

from .routes import freie_praefixe

#: Through this parameter a page passes its language where no account can be
#: asked for it.
SPRACHPARAMETER = "sprache"
#: The header the counting app sends the language in ("X-Sprache").
SPRACHKOPF = "X-Sprache"


def im_hintergrund(request) -> bool:
    """Whether a script of the page asks, not the browser for a page to show.

    The website's scripts send "X-Requested-With: fetch" with every request.
    Such a request wants a status and not a page: 'fetch' follows a redirect
    by itself and takes whatever comes at the end for the answer.
    """
    return request.headers.get("X-Requested-With") == "fetch"


def sprache_merken(antwort, sprache: str) -> None:
    """The browser remembers the choice as long as nobody is signed in.

    In a cookie and not in the session: every anonymous call with '?sprache='
    wrote a session into the database, with no limit and kept for two weeks
    (until 10/2026). The cookie lives as long as the browser window, carries
    nothing but the two letters and is sent to this site only.
    """
    antwort.set_cookie(
        settings.LANGUAGE_COOKIE_NAME,
        sprache,
        max_age=settings.LANGUAGE_COOKIE_AGE,
        path=settings.LANGUAGE_COOKIE_PATH,
        domain=settings.LANGUAGE_COOKIE_DOMAIN,
        secure=settings.LANGUAGE_COOKIE_SECURE,
        httponly=settings.LANGUAGE_COOKIE_HTTPONLY,
        samesite=settings.LANGUAGE_COOKIE_SAMESITE,
    )


class SprachwahlMiddleware:
    """Sets the language from the account for the duration of a request.

    Django's own 'LocaleMiddleware' asks session, cookie and the browser. Here,
    however, the choice hangs on the **account** ('CustomUser.sprache'), for the
    same reason as the appearance: it should apply on every device, including
    the installed app with its own storage.

    Whoever is not signed in gets what `?sprache=` says, then what the browser
    remembered of it ('sprache_merken'), otherwise 'LANGUAGE_CODE'. The
    parameter is there for the privacy page: the counting app links to it, but
    identifies itself with a token and has no Django session. For Django the
    call is thus anonymous, and until 09/2026 the page always came up in
    German, even when the app ran in English. The cookie carries the choice on:
    whoever opens the login form in English also gets "Forgot your password"
    and the block page in English, and the login form offers the choice itself
    ('SprachwahlView'). After signing in, the account applies.

    'override' and not 'activate': the language applies to exactly this request.
    Otherwise the worker would serve the next one in the previous one's language.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        sprache, aus_dem_browser = self._sprache(request)
        with translation.override(sprache):
            antwort = self.get_response(request)
            # A cache must not take a German page for an English one. That is
            # what this header is for.
            antwort.headers.setdefault("Content-Language", translation.get_language())
        if aus_dem_browser:
            patch_vary_headers(antwort, ("Cookie",))
            gemerkt = request.COOKIES.get(settings.LANGUAGE_COOKIE_NAME)
            if request.GET.get(SPRACHPARAMETER) == sprache and gemerkt != sprache:
                sprache_merken(antwort, sprache)
        return antwort

    @staticmethod
    def _sprache(request) -> tuple[str, bool]:
        """The language, and whether it came from the browser rather than an account."""
        benutzer = getattr(request, "user", None)
        if benutzer is not None and benutzer.is_authenticated and benutzer.sprache:
            return benutzer.sprache, False
        sprachen = dict(settings.LANGUAGES)
        gewuenscht = request.GET.get(SPRACHPARAMETER, "")
        if gewuenscht in sprachen:
            return gewuenscht, True
        # The counting app: for Django a call with a token is anonymous, DRF
        # checks the token only in the view. The app sends the language it
        # shows in a header, so the answers come in it ('api.js', 'call').
        # Nothing is stored for it.
        im_kopf = request.headers.get(SPRACHKOPF, "")
        if im_kopf in sprachen:
            return im_kopf, False
        gemerkt = request.COOKIES.get(settings.LANGUAGE_COOKIE_NAME, "")
        if gemerkt in sprachen:
            return gemerkt, True
        return settings.LANGUAGE_CODE, False


class StaffOnlyMiddleware:
    """The web interface belongs to management. Rejection differs by role.

    **Counters** always land on the block page, even on addresses that do not
    exist: a 404 would be no answer for them but an invitation to keep trying.
    They are signed in, they only lack the role, and the block page tells them
    so along with the way into the app.

    **Anonymous** visitors are sent to the login only where there is something
    to sign in to. Otherwise there would be a redirect for every typo, and
    whoever tries addresses would get, with the login page, the answer
    "there is something here" that a 404 denies them.

    **Managers** get through. What does not exist is a 404 for them too, with a
    navigation bar, see '404.html'.

    The real admin under 'ADMIN_URL' counts as **non-existent** for anonymous
    visitors: a redirect to the login would confirm there that the guessed
    address is right, while every other one would get a 404. Whoever needs it
    signs in at the front and follows the link in the bar.

    **Scripts** get a status instead of the redirect ('im_hintergrund'): 401
    without a session, 403 without the role. The sign-in page answers 200,
    and until 10/2026 a save in the background (the order of the packs, a
    note) counted as done after the session had expired, although nothing
    was stored. The 401 names no scheme to sign in with, because the sign-in
    is a page and not an HTTP challenge.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if self._ist_freigegeben(request) or request.user.is_staff:
            return self.get_response(request)
        if request.user.is_authenticated:
            if im_hintergrund(request):
                return HttpResponseForbidden()
            return redirect("kein_zugriff")
        if self._ist_der_verborgene_admin(request):
            # The URLconf without admin silences CommonMiddleware: otherwise it
            # would turn the call without a trailing slash into a redirect to
            # the one with, and that only exists when the page exists.
            request.urlconf = "django_project.urls_anonymous"
            raise Http404
        if not self._gibt_es(request):
            # Pass through, do not answer ourselves: that way APPEND_SLASH and
            # the 404 template apply as everywhere else.
            return self.get_response(request)
        if im_hintergrund(request):
            return HttpResponse(status=HTTPStatus.UNAUTHORIZED)
        return redirect_to_login(request.get_full_path(), str(settings.LOGIN_URL))

    def _ist_freigegeben(self, request) -> bool:
        return request.path_info.startswith(freie_praefixe())

    def _ist_der_verborgene_admin(self, request) -> bool:
        """Both spellings: with and without a trailing slash.

        If one stayed open, the redirect to the other would give away that
        something lies there.
        """
        admin = "/" + str(settings.ADMIN_URL).strip("/")
        return request.path_info == admin or request.path_info.startswith(admin + "/")

    def _gibt_es(self, request) -> bool:
        try:
            resolve(request.path_info)
        except Resolver404:
            return False
        return True


#: What a signed-in person fetches that may stay in the browser's cache: the
#: files are the same for everyone, and the icons sit in every tab.
OEFFENTLICHE_PRAEFIXE = ("/static/", "/app/", "/icons/")


class KeinCacheFuerPrivates:
    """'Cache-Control: no-store' on the API and on every page of a signed-in person.

    With a token the browser does not cache heuristically, but '/api/v1/ich/'
    and the master data are reachable with the website's session too, and
    there only the heuristic applies. Nothing of the API is meant to be kept:
    the app holds its own copy in IndexedDB. Until 10/2026 no answer said so.

    The pages of the website carried only 'Vary: Cookie' until 10/2026. On a
    shared machine "Back" after signing out could show the check list or the
    list of accounts from the cache. The login page and the administration
    already said 'no-store'; now every signed-in answer does.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if response.has_header("Cache-Control"):
            return response
        benutzer = getattr(request, "user", None)
        angemeldet = benutzer is not None and benutzer.is_authenticated
        if request.path.startswith("/api/") or (
            angemeldet and not request.path.startswith(OEFFENTLICHE_PRAEFIXE)
        ):
            add_never_cache_headers(response)
        return response
