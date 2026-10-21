"""The account in the app: who is signed in, its tokens, signing out, password and address."""

import re
from collections.abc import Mapping

from django.contrib.auth import get_user, logout, update_session_auth_hash
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.utils import translation
from django.utils.translation import gettext_lazy as _
from rest_framework.authentication import SessionAuthentication
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    parser_classes,
    permission_classes,
)
from rest_framework.exceptions import (
    NotAuthenticated,
    ParseError,
    UnsupportedMediaType,
    ValidationError,
)
from rest_framework.parsers import JSONParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from accounts.email_change import einleiten
from accounts.notification import passwort_melden
from accounts.sign_out import (
    gesperrtes_konto,
    nach_kennung_sperren,
    paar_ausstellen,
    sitzung_gilt,
)

from ..permissions import IstAktiverBenutzer
from ..serializers import (
    EinstellungenSerializer,
    EmailAendernSerializer,
    IchSerializer,
    PasswortAendernSerializer,
    TokenErneuernSerializer,
)
from ..serializers.account import TokenHolenSerializer
from .base import eingabe


@api_view(["GET", "PATCH"])
@permission_classes([IstAktiverBenutzer])
def ich(request, version=None):
    """Who is signed in. The PWA shows it and recognises an account switch.

    Via PATCH **appearance and language** can be changed, and only those. The
    website sends the appearance here (via session, with CSRF), the app both
    via token. One place for all, so the choice hangs on the account and not
    on the device.

    The website's language goes through a form of its own
    ('accounts.views.SprachwahlView'): the page is built server-side and has
    to be reloaded after switching anyway.
    """
    if request.method == "PATCH":
        eingabe = EinstellungenSerializer(request.user, data=request.data, partial=True)
        eingabe.is_valid(raise_exception=True)
        eingabe.save()
    return Response(IchSerializer(request.user).data)


@api_view(["POST"])
@authentication_classes([SessionAuthentication])
@permission_classes([IstAktiverBenutzer])
def token_aus_sitzung(request, version=None):
    """A token pair for whoever is already signed in on the website.

    Otherwise whoever has the web interface open and opens the PWA next to it
    would have to sign in. Twice the same password for the same person. Here
    only the session counts: a token sent along must not overrule a
    **different** user, otherwise the app would get the old account back after
    a switch on the website.

    Under the account's lock, as a renewal ('accounts/sign_out.py'). Removing
    a passkey ends the account's sessions. A session read just before still
    got its pair, and the pair outlived the session (until 10/2026). Asked
    once more under the lock, an ended session hears what no session hears.
    The pair carries the password as the session saw it: one changed in
    between leaves the old check value in it, and the pair is refused. An
    account switched off meanwhile gets none ('paar_ausstellen').
    """
    with transaction.atomic():
        if gesperrtes_konto(request.user.get_username()) is None or not sitzung_gilt(
            request._request.session.session_key
        ):
            raise NotAuthenticated
        erneuerung = paar_ausstellen(request.user)
    return Response(
        {
            "access": str(erneuerung.access_token),
            "refresh": str(erneuerung),
            "ich": IchSerializer(request.user).data,
        }
    )


#: A refresh token's id as SimpleJWT makes it ('uuid4().hex'): 32 hex digits in lower case.
KENNUNG = re.compile(r"[0-9a-f]{32}")


def kennung_aus(wert: object) -> str | None:
    """The id of a refresh token as the app names it, or None for anything else.

    Only what SimpleJWT could have made goes into a query. Until 10/2026 any
    text of up to 255 characters did: a NUL ended in a server error under
    PostgreSQL, half of a surrogate pair on every database, and the request
    never got as far as signing out.
    """
    return wert if isinstance(wert, str) and KENNUNG.fullmatch(wert) else None


@api_view(["POST"])
@permission_classes([IstAktiverBenutzer])
def abmelden(request, version=None):
    """Signing out applies to both, session and token.

    Whoever signs out in the app is gone on the website too. Otherwise the app
    would fetch a token again at once on the next start via 'token_aus_sitzung'
    and the sign-out would have no effect.

    **A sign-out the app owes** comes later, by the session alone. The app
    signed out without network and dropped its tokens: kept on a shared
    phone, the refresh token would stay behind as a sign-in. So it names the
    token by its id ('jti'). The id shows nobody that the token is theirs,
    and only one of the session's account goes on the blocklist. Under the
    account's lock, as for a renewal ('accounts/sign_out.py'): a renewal of
    the token at the same moment swapped it before, or it is refused after.
    With 'sitzung_behalten' the session stays, for someone signed in on the
    website since, on purpose. Until 10/2026 that session ended, and the
    token held until it ran out.
    """
    daten = eingabe(request)
    # Anything but an object counts as sent without a token: signed out is
    # signed out, as for a body without one.
    gegeben = daten.get("refresh")
    if gegeben:
        try:
            RefreshToken(gegeben).blacklist()
        except TokenError, UnicodeError:
            # Expired, already blocked, or no token at all: a lone surrogate
            # raises past 'TokenError' while it is read, and ended in a server
            # error before the sign-out (until 10/2026). Signed out is the goal.
            pass
    kennung = kennung_aus(daten.get("jti"))
    if kennung is not None:
        with transaction.atomic():
            konto = gesperrtes_konto(request.user.get_username())
            if konto is not None:
                eigenes = OutstandingToken.objects.filter(jti=kennung, user=konto).first()
                if eigenes is not None:
                    BlacklistedToken.objects.get_or_create(token=eigenes)
    if daten.get("sitzung_behalten") is not True:
        logout(request)
    return Response(status=204)


#: What the owed sign-out without a session hears for a body it cannot use.
KENNUNG_ERWARTET = _(
    "Erwartet wird die Kennung eines Erneuerungstokens: 32 Zeichen aus 0 bis 9 und a bis f."
)


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
@parser_classes([JSONParser])
def abmelden_nachgeholt(request, version=None):
    """A sign-out the app owes, settled by the refresh token's id alone, without a session.

    The app could not deliver its sign-out and noted the token's id
    ('SIGN_OUT_OWED'). 'abmelden' settles that by the website's session.
    Whoever signed out on the website meanwhile, or whose session ran out,
    has none, and nothing blocked the token while the app counted the
    sign-out as settled (until 10/2026). Knowing the id means having held
    the token, and the only effect is a sign-out, so no sign-in is asked.
    No authentication either: a website session open beside it would
    otherwise ask for a CSRF value.

    JSON only, as for signing in ('TokenHolenView'). The answer is 204 for
    every well-formed id, an unknown or expired one too, so it tells nobody
    which tokens exist. Anything else hears 400. The app is built against
    exactly this.
    """
    try:
        daten = request.data
    except ParseError, UnsupportedMediaType:
        daten = None
    kennung = kennung_aus(daten.get("jti")) if isinstance(daten, Mapping) else None
    if kennung is None:
        raise ValidationError({"jti": [KENNUNG_ERWARTET]})
    nach_kennung_sperren(kennung)
    return Response(status=204)


@api_view(["POST"])
@permission_classes([IstAktiverBenutzer])
def passwort_aendern(request, version=None):
    """A new password from the app, and this device stays signed in.

    The website has its own form for it, but the counters never get to see
    the web interface, and the app has no session there. Without this path a
    counter could only have a new password set by a manager.

    A new password ends every sign-in made with the old one
    ('CHECK_REVOKE_TOKEN'), the one of this device too. The response therefore
    brings a fresh token pair. Every other device asks for the new password.

    The website's session in the same browser is carried along, provided it
    belongs to the same account. It would end otherwise, and an app that has
    its sign-in from there would sign out on its next start.

    The messages come in the **account's** language and not in the server's.
    A person reads them on the form, and for Django a call with a token is
    anonymous ('SprachwahlMiddleware').
    """
    konto = request.user
    anfrage = request._request
    # Asked before the change: afterwards the session no longer fits the
    # password, and 'get_user' would throw it away.
    eigene_sitzung = get_user(anfrage).pk == konto.pk
    with translation.override(konto.sprache or translation.get_language()):
        eingabe = PasswortAendernSerializer(
            data=request.data, context={"request": anfrage, "konto": konto}
        )
        eingabe.is_valid(raise_exception=True)
    eingabe.save()
    passwort_melden(konto, "app")
    if eigene_sitzung:
        update_session_auth_hash(anfrage, konto)
    # Under the account's lock: switched off meanwhile, the device gets no pair.
    erneuerung = paar_ausstellen(konto)
    return Response({"access": str(erneuerung.access_token), "refresh": str(erneuerung)})


#: What the app hears when the mail with the link did not leave.
MAIL_NICHT_GESENDET = _(
    "Die Mail ging nicht hinaus, der E-Mail-Server antwortet nicht. Bitte später noch einmal "
    "versuchen."
)


@api_view(["POST"])
@permission_classes([IstAktiverBenutzer])
def email_aendern(request, version=None):
    """A new address from the app, as from the website's account menu.

    The counters never get to see the website, and the app has no session
    there. With a mail server the new address gets the mail with the link
    and applies once it is confirmed ('accounts/email_change.py'). Without
    one it applies now. The answer says which of the two happened. The
    messages come in the account's language, as for the password.

    Against the password, or without one against a passkey's confirmation
    ('PasswortOderPasskeySerializer'). The 'bestaetigung' of the request is
    that token. The one of the answer says whether the mail with the link
    went out.
    """
    konto = request.user
    anfrage = request._request
    with translation.override(konto.sprache or translation.get_language()):
        eingabe = EmailAendernSerializer(
            data=request.data, context={"request": anfrage, "konto": konto}
        )
        eingabe.is_valid(raise_exception=True)
        neu = eingabe.validated_data["email"]
        ergebnis = einleiten(konto, neu, anfrage)
        if ergebnis.bestaetigung and ergebnis.gesendet is False:
            return Response({"detail": str(MAIL_NICHT_GESENDET)}, status=503)
    return Response({"email": neu, "bestaetigung": ergebnis.bestaetigung})


class TokenHolenView(TokenObtainPairView):
    """Signing in with user name and password, with the same bolt as at the front.

    The failed attempts are counted by 'axes', which notes the lock on the
    request it gets hold of at 'authenticate()'. But DRF passes its **own**
    request object there, and the 'AxesMiddleware' later sees only Django's:
    the note would be on the one, the other would be read, and the lock would
    have no effect: the endpoint would keep answering with 401, as often as
    you like.

    That is why the serializer explicitly gets Django's request here. It is
    only needed for exactly this call anyway.

    **JSON only**, as the app sends it. The endpoint needs neither a session
    nor a CSRF token, and a form on any foreign page reached it from a
    visitor's browser without a preflight. Every such failed sign-in counted
    under the name and the visitor's address, and the fifth locked the name
    for everyone behind that address, at every door (until 10/2026). A body
    in JSON needs a preflight, and the server answers none.

    The account is let in under its lock ('TokenHolenSerializer').
    """

    parser_classes = [JSONParser]
    serializer_class = TokenHolenSerializer

    def get_serializer_context(self):
        return {**super().get_serializer_context(), "request": self.request._request}


class TokenErneuernView(TokenRefreshView):
    """Refresh the access token and cleanly reject a token without an account.

    The package looks up the account for the token but does not catch that it
    may be missing: deleted, renamed ('USER_ID_FIELD' is the user name) or
    vanished with a backup. Out came a 500, and the app takes that for a fault
    of the server instead of what it is: a sign-in that no longer applies.
    With 401 it leads to the login form.

    A token from before a password change is rejected by the serializer.
    """

    serializer_class = TokenErneuernSerializer

    def post(self, request, *args, **kwargs):
        try:
            return super().post(request, *args, **kwargs)
        except ObjectDoesNotExist as fehler:
            # 'InvalidToken' takes only a 'str' according to the stubs. At runtime a
            # lazy object would work too, but there is no placeholder in it anyway.
            meldung = str(_("Zu diesem Token gibt es kein Konto mehr."))
            raise InvalidToken(meldung) from fehler
