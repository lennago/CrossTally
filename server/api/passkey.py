"""Passkeys for the counting app: the same steps as on the website, with a token pair at the end.

The website keeps its challenge in the session ('accounts/passkey.py'). The
app has no session, only a token, and when signing in not even that. So the
challenge travels **signed** to the app and back ('signing.dumps', two
minutes). The checks themselves are the website's ('registrieren', 'anmelden').

**Each question answers once.** A row notes it as open ('Passkeyfrage'),
and the first answer deletes it, whatever the answer says. Until 10/2026 the
signature was all the server asked: the same answer let the account in as
often as it came within the two minutes. Whoever saw the body of a request
and not its answer (a proxy, a firewall, an extension of the browser) got a
token pair of their own. The library compares the counter only where the
key counts, and two answers at once both read it before either wrote it.
Asking needs no sign-in, so the table holds the questions of the last two
minutes and no more: each new question deletes the ones that ran out.

**A new key needs the account's password**, as on the website, asked before
the device's dialog. Its challenge is signed with a salt of its own and names
the account ('ANLEGESALZ'): the question of signing in is open to anyone, and
under the shared salt it would have served for a new key just as well. So
would the question of another account whose password someone knows.

**Without a password one of the account's keys confirms instead**
('bestaetigen_optionen', 'bestaetigen_pruefen'), before a new key as before
a new address. The checks are the website's ('accounts.passkey.bestaetigen').
Its challenge has a salt of its own and names the account
('BESTAETIGUNGSFRAGE'), and so has the confirmation it brings
('accounts.passkey.BESTAETIGUNGSSALZ'): none of the four passes for another.
The app sends the confirmation with the step, and it holds 'GUELTIG_SEKUNDEN'.

The language of a refusal: for Django a call with a token is anonymous, so
the app names its language in the 'X-Sprache' header of every request
('SprachwahlMiddleware'). Unlike '?sprache=' it leaves no cookie behind.
Where the account is known and a person reads the refusal on a form, the
account's language applies, as for a new address.
"""

import hashlib
import json
from datetime import timedelta

from django.contrib.auth.models import update_last_login
from django.core import signing
from django.db import transaction
from django.utils import timezone, translation
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from rest_framework import serializers, status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from webauthn import options_to_json
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url

from accounts.forms import LETZTER_ADMINISTRATOR_BEHAELT_PASSKEY
from accounts.models import CustomUser, Passkey
from accounts.notification import passkey_melden
from accounts.passkey import (
    ABGELAUFEN,
    GUELTIG_SEKUNDEN,
    KEIN_PASSKEY,
    PasskeyFehler,
    anlegeoptionen,
    anmelden,
    anmeldeoptionen,
    bestaetigen,
    bestaetigung_signiert,
    nimmt_den_letzten_weg,
    registrieren,
)
from accounts.sign_out import (
    geltendes_token,
    gesperrtes_konto,
    paar_ausstellen,
    ueberall_abmelden,
)
from django_project.roles import administratoren_sperren

from .models import Passkeyfrage
from .permissions import IstAktiverBenutzer
from .serializers import IchSerializer, PasswortOderPasskeySerializer
from .views import eingabe

SALZ = "api.passkey.challenge"

#: The salt of a challenge for a new key. Only the step that checked the
#: password signs with it, and the account it was asked for stands inside.
ANLEGESALZ = "api.passkey.anlegen"

#: The salt of a confirmation's challenge, with the account inside like a new key's.
BESTAETIGUNGSFRAGE = "api.passkey.bestaetigen"

#: What a device hears that removes a key without a sign-in of its own ('loeschen').
NEU_ANMELDEN = gettext_lazy("Bitte melde dich neu an, um den Passkey zu entfernen.")


def pruefsumme(challenge: bytes) -> str:
    return hashlib.sha256(challenge).hexdigest()


def frage_stellen(challenge: bytes) -> None:
    """The row of a question the app is handed, after the rows of those that ran out.

    Asking needs no sign-in, and a row nobody answers would stay otherwise.
    So each question deletes the ones that ran out, and the table never
    holds more than two minutes of asking.
    """
    jetzt = timezone.now()
    Passkeyfrage.objects.filter(gefragt_am__lt=jetzt - timedelta(seconds=GUELTIG_SEKUNDEN)).delete()
    Passkeyfrage.objects.create(pruefsumme=pruefsumme(challenge), gefragt_am=jetzt)


def frage_beantworten(challenge: bytes) -> bool:
    """Whether the question was open and in time. Afterwards it is not, and one answer hears yes.

    One statement deletes the row, and the number it deleted answers. Under
    PostgreSQL a second delete at the same moment waits for the first one's
    row and then finds none. Outside a transaction, as signing in runs, the
    row is gone before the signature is checked.
    """
    grenze = timezone.now() - timedelta(seconds=GUELTIG_SEKUNDEN)
    offen = Passkeyfrage.objects.filter(pruefsumme=pruefsumme(challenge), gefragt_am__gte=grenze)
    return offen.delete()[0] > 0


def mit_challenge(options, konto: CustomUser | None = None, salz: str = ANLEGESALZ) -> Response:
    """The options as JSON, and the challenge once more, signed, for the way back.

    With 'konto' for a new key or a confirmation: then under 'salz' and with
    the account's name. The question is noted as open ('frage_stellen').
    """
    frage_stellen(options.challenge)
    challenge = bytes_to_base64url(options.challenge)
    return Response(
        {
            "options": json.loads(options_to_json(options)),
            "challenge": (
                signing.dumps(challenge, salt=SALZ)
                if konto is None
                else signing.dumps([challenge, konto.get_username()], salt=salz)
            ),
        }
    )


def challenge_aus(token, konto: CustomUser | None = None, salz: str = ANLEGESALZ) -> bytes | None:
    """The challenge the app sends back, if it is ours, in time and not answered before.

    With 'konto' only one signed under 'salz' for exactly this account. The
    signature says the server asked, the row that nobody answered yet
    ('frage_beantworten'). The row goes with this answer, whatever the
    answer says: as on the website, whoever answers twice is refused.
    """
    try:
        if konto is None:
            wert = signing.loads(str(token or ""), salt=SALZ, max_age=GUELTIG_SEKUNDEN)
        else:
            wert, name = signing.loads(str(token or ""), salt=salz, max_age=GUELTIG_SEKUNDEN)
            if name != konto.get_username():
                return None
        challenge = base64url_to_bytes(wert)
    except signing.BadSignature, TypeError, ValueError:
        return None
    return challenge if frage_beantworten(challenge) else None


def abgewiesen(text: str) -> Response:
    """Like every other refusal of the API: 'detail'. Until 10/2026 this one said 'fehler'."""
    return Response({"detail": text}, status=status.HTTP_400_BAD_REQUEST)


class PasskeySerializer(serializers.ModelSerializer):
    class Meta:
        model = Passkey
        fields = ("id", "name", "erstellt_am", "zuletzt_benutzt")


class AnlegenSerializer(PasswortOderPasskeySerializer):
    """The password before a new key, or a passkey's confirmation, as before a new address.

    A token does not say who holds the device. Until 10/2026 it sufficed, and
    whoever found the app open on a phone left lying added a key of their
    own, which kept working after the owner changed the password. The checks
    are those of a new address ('PasswortOderPasskeySerializer').
    """


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def anmelden_optionen(request, version=None):
    """Step one of signing in: the question, without a user name."""
    return mit_challenge(anmeldeoptionen(request._request))


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def anmelden_pruefen(request, version=None):
    """Step two: the signature opens the account and brings a token pair.

    Like 'token_aus_sitzung', with who signed in next to the tokens.
    """
    daten = eingabe(request)
    challenge = challenge_aus(daten.get("challenge"))
    if challenge is None:
        return abgewiesen(_("Die Anfrage ist abgelaufen. Bitte noch einmal versuchen."))

    def einlassen(konto: CustomUser) -> tuple[CustomUser, RefreshToken]:
        # A sign-in, as with the password ('UPDATE_LAST_LOGIN' in 'SIMPLE_JWT'). The
        # sender is the model, as when Django sends 'user_logged_in'. The pair comes
        # within the account's bounds, as every other one ('paar_ausstellen').
        update_last_login(type(konto), konto)
        return konto, paar_ausstellen(konto)

    try:
        benutzer, erneuerung = anmelden(
            request._request, daten.get("credential"), challenge, einlassen
        )
    except PasskeyFehler as grund:
        return abgewiesen(str(grund))
    return Response(
        {
            "access": str(erneuerung.access_token),
            "refresh": str(erneuerung),
            "ich": IchSerializer(benutzer).data,
        }
    )


@api_view(["GET"])
@permission_classes([IstAktiverBenutzer])
def liste(request, version=None):
    """The keys of the signed-in account, for the dialog in the app."""
    eigene = Passkey.objects.filter(benutzer=request.user)
    return Response(PasskeySerializer(eigene, many=True).data)


@api_view(["POST"])
@permission_classes([IstAktiverBenutzer])
def anlegen_optionen(request, version=None):
    """Step one of registering: the password, then the question, signed for this account.

    Without a password the confirmation of a passkey instead ('bestaetigung',
    from 'bestaetigen_pruefen'). The challenge comes only once one of them
    held, so it proves the check to the second step. A refusal stands at its
    field, in the account's language, as for a new address.
    """
    konto = request.user
    anfrage = request._request
    with translation.override(konto.sprache or translation.get_language()):
        AnlegenSerializer(data=request.data, context={"request": anfrage, "konto": konto}).is_valid(
            raise_exception=True
        )
    return mit_challenge(anlegeoptionen(anfrage, konto), konto)


@api_view(["POST"])
@permission_classes([IstAktiverBenutzer])
def anlegen_pruefen(request, version=None):
    """The browser's answer becomes a stored key, named by the person or by its provider."""
    daten = eingabe(request)
    challenge = challenge_aus(daten.get("challenge"), request.user)
    if challenge is None:
        return abgewiesen(_("Die Anfrage ist abgelaufen. Bitte noch einmal versuchen."))
    try:
        passkey = registrieren(
            request._request,
            request.user,
            daten.get("credential"),
            challenge,
            str(daten.get("name") or ""),
        )
    except PasskeyFehler as grund:
        return abgewiesen(str(grund))
    passkey_melden(request.user, passkey.name, angelegt=True)
    return Response(PasskeySerializer(passkey).data, status=status.HTTP_201_CREATED)


@api_view(["POST"])
@permission_classes([IstAktiverBenutzer])
def bestaetigen_optionen(request, version=None):
    """Step one of a confirmation: the question for the account's own keys, signed for it.

    For an account without a usable password, before a new key or a new
    address ('PasswortOderPasskeySerializer'). Without a key there is nothing
    to ask, and the refusal says who helps.
    """
    konto = request.user
    with translation.override(konto.sprache or translation.get_language()):
        if not Passkey.objects.filter(benutzer=konto).exists():
            return abgewiesen(str(KEIN_PASSKEY))
    return mit_challenge(anmeldeoptionen(request._request, konto), konto, BESTAETIGUNGSFRAGE)


@api_view(["POST"])
@permission_classes([IstAktiverBenutzer])
def bestaetigen_pruefen(request, version=None):
    """Step two: the signature of one of the account's keys brings the signed confirmation.

    The checks are the website's ('accounts.passkey.bestaetigen'). Only the
    device that confirmed reads the answer, and it takes one of the account's
    keys with the person verified: a leftover access token alone brings none.
    """
    konto = request.user
    daten = eingabe(request)
    with translation.override(konto.sprache or translation.get_language()):
        challenge = challenge_aus(daten.get("challenge"), konto, BESTAETIGUNGSFRAGE)
        if challenge is None:
            return abgewiesen(str(ABGELAUFEN))
        try:
            bestaetigung = bestaetigen(
                request._request, konto, daten.get("credential"), challenge, bestaetigung_signiert
            )
        except PasskeyFehler as grund:
            return abgewiesen(str(grund))
    return Response({"bestaetigung": bestaetigung})


@api_view(["DELETE"])
@permission_classes([IstAktiverBenutzer])
def loeschen(request, version=None, pk=None):
    """Withdraws one of the own keys. A foreign one does not exist from here.

    Whoever signed in with it is signed out, on the website and in the app
    ('accounts/sign_out.py'), but not this device. It sends its refresh
    token along, and that one stays, as the website's session in the same
    browser does: an app that has its sign-in from there would otherwise
    sign out on its next start. The answer is 204 and brings no tokens.

    **Against the password, as a new key**, or without one against a
    passkey's confirmation ('PasswortOderPasskeySerializer', with the
    refusals of 'anlegen_optionen'). Until 10/2026 the refresh token
    sufficed: whoever held the unlocked phone removed every key, and the
    owner was signed out everywhere else. Asked before the transaction:
    hashing under the administrators' lock would hold every other removal as
    long.

    **The refresh token shows the sign-in, the access token does not.**
    Signing out blocks the refresh tokens, an access token runs on for up to
    an hour. Until 10/2026 this answered any access token with a fresh pair.
    A phone signed out by an earlier removal took a lasting sign-in from it
    and could remove the owner's other keys. Without a refresh token that
    still holds nothing is removed (403), and the app asks for a new sign-in.
    It is asked under the account's lock, after the administrators', as the
    renewal asks it ('geltendes_token').
    """
    with translation.override(request.user.sprache or translation.get_language()):
        PasswortOderPasskeySerializer(
            data=request.data, context={"request": request._request, "konto": request.user}
        ).is_valid(raise_exception=True)
    gegeben = eingabe(request).get("refresh")
    if not isinstance(gegeben, str) or not gegeben.isascii():
        # A token is ASCII. One with a lone surrogate raised past 'TokenError'
        # while it was read, a server error under the lock (until 10/2026).
        gegeben = None
    with transaction.atomic():
        administratoren_sperren()
        konto = gesperrtes_konto(request.user.get_username())
        jti = None if konto is None else geltendes_token(konto, gegeben)
        if konto is None or jti is None:
            return Response({"detail": str(NEU_ANMELDEN)}, status=status.HTTP_403_FORBIDDEN)
        passkey = Passkey.objects.filter(benutzer=konto, pk=pk).first()
        if passkey is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        if nimmt_den_letzten_weg([passkey]):
            return abgewiesen(str(LETZTER_ADMINISTRATOR_BEHAELT_PASSKEY))
        passkey.delete()
        ueberall_abmelden(konto, ausser=request._request.session.session_key, ausser_token=jti)
    passkey_melden(konto, passkey.name, angelegt=False)
    return Response(status=status.HTTP_204_NO_CONTENT)
