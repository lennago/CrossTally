"""Signing in with a passkey (WebAuthn): options, checks and pages.

The browser does the actual work. It asks the device for FaceID, Touch ID,
Windows Hello or a security key and hands back a signature. The server only
poses the question (a random challenge, bound to the domain) and checks the
answer against the public key it has stored ('accounts.Passkey'). Six
endpoints: options and check for registering, for signing in and for
confirming. The library 'webauthn' (py_webauthn) does the parsing and the
cryptography.

**The domain is the key's address.** The RP id is the host of the request,
without port, and the origin is scheme and host. A passkey made under one
domain does not open under another. Behind the proxy the scheme comes from
'X-Forwarded-Proto' ('SECURE_PROXY_SSL_HEADER'). Passkeys need HTTPS or
'localhost', which is why the e2e tests and the browser probes use the
latter and not '127.0.0.1': an address is no valid RP id.

**Discoverable, with user verification.** The key lives on the device and
knows its account, so signing in needs no user name. The device verifies the
person every time (biometrics or PIN), also with a security key. A key
without that is refused when it is registered.

**The challenge lives in the session**, under one key each for signing in,
registering and confirming: a browser runs one of each at a time, and the
check removes it. It holds two minutes, as the app's ('GUELTIG_SEKUNDEN'), and so does the
session of a visitor who is not signed in ('challenge_merken'). Nobody is
signed in through 'authenticate()', hence past 'axes': there is nothing to
guess here.

**A new key needs the account's password**, asked before the browser's
dialog ('PasskeyAnlegenOptionenView'), and so does removing one
('PasskeyLoeschenView'). Here 'authenticate()' does check, so 'axes' counts a
wrong one as at the sign-in.

**Without a password one of the account's own keys confirms instead**, before
a new key, before removing one and before a new address. Until 10/2026 such
an account was asked nothing, and whoever found its session open, or a phone
with its last access token, added a key of their own or pointed the address
at another mailbox.
The confirmation is the ceremony of signing in for an account already known
('bestaetigen'): a question of its own that names the account's keys, and an
answer signed by one of them, with the person verified. It holds
'GUELTIG_SEKUNDEN' for every step in that time: in the session on the
website ('BESTAETIGT'), signed in the app ('bestaetigung_signiert'). An
account with neither a password nor a key confirms nothing. Only a password
from the administration helps it on.
"""

import hashlib
import json
import re
import time
from collections.abc import Callable, Iterable
from http import HTTPStatus
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core import signing
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, resolve_url
from django.utils import formats, timezone, translation
from django.utils.decorators import method_decorator
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views import View
from django.views.decorators.cache import never_cache
from django.views.generic import TemplateView
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.exceptions import WebAuthnException
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from django_project.middleware import im_hintergrund
from django_project.mixin_bases import AnsichtBasis
from django_project.roles import administratoren, administratoren_sperren, mit_passwort
from organisation.models import Erscheinungsbild

from .forms import LETZTER_ADMINISTRATOR_BEHAELT_PASSKEY
from .models import CustomUser, Passkey, Sitzung
from .notification import passkey_melden
from .sign_out import gesperrtes_konto, ueberall_abmelden

# The refusals of signing in, shared with the decoy ('honeypot.py'). The same
# words at both doors, or one request tells the two apart.

#: The question is too old, already answered, or never asked.
ABGELAUFEN = gettext_lazy("Die Anfrage ist abgelaufen. Bitte noch einmal versuchen.")
#: What the browser sent is no answer of a passkey at all.
UNGUELTIG = gettext_lazy("Der Browser hat keinen gültigen Passkey geliefert.")
#: What a key hears that the server does not know: never stored, or removed since.
UNBEKANNTER_SCHLUESSEL = gettext_lazy("Dieser Passkey ist hier nicht bekannt.")
#: The handle the browser sends along belongs to another account than the key.
ANDERES_KONTO = gettext_lazy("Dieser Passkey gehört zu einem anderen Konto.")
#: The key's account is switched off.
DEAKTIVIERT = gettext_lazy("Dieses Konto ist deaktiviert.")
#: The signature does not hold up, against the stored key or the question.
FEHLGESCHLAGEN = gettext_lazy("Die Anmeldung mit diesem Passkey ist fehlgeschlagen.")

#: What a credential id holds: base64url without padding, the alphabet of WebAuthn.
KENNUNG = re.compile(r"[A-Za-z0-9_-]+")

#: A challenge is good for this long, on the website as in the app ('api/passkey.py').
#: The dialog of the device takes seconds.
GUELTIG_SEKUNDEN = 120

#: Under this key the session holds the open challenge of signing in. The decoy
#: uses one of its own ('honeypot.py'), so its question never opens the real door.
SITZUNGSSCHLUESSEL = "passkey_challenge"

#: Registering has a key of its own, written only once the password held. The
#: question of signing in is open to anyone, and under the shared key it would
#: have served for a new key just as well.
ANLEGESCHLUESSEL = "passkey_anlegen"

#: A wrong password before a new key, the sentence of a new address ('EmailAendernForm').
PASSWORT_FALSCH = gettext_lazy("Das Passwort stimmt nicht.")

#: Sent without one, where the account has a password.
PASSWORT_FEHLT = gettext_lazy("Bitte gib dein Passwort ein.")

#: Under this key the session holds the open challenge of a confirmation. Its
#: own, as registering has one: the question of signing in is open to anyone,
#: and neither may answer for the other.
BESTAETIGUNGSSCHLUESSEL = "passkey_bestaetigen"

#: And under this one the confirmation itself: the account, and when its key signed.
BESTAETIGT = "passkey_bestaetigt"

#: The salt of the app's confirmation, its own: no challenge passes for one.
BESTAETIGUNGSSALZ = "api.passkey.bestaetigung"

#: A step of an account without a password that came without a confirmation.
BESTAETIGUNG_FEHLT = gettext_lazy("Bitte bestätige zuerst mit einem deiner Passkeys.")

#: One that came too late, for another account, or forged.
BESTAETIGUNG_ABGELAUFEN = gettext_lazy(
    "Die Bestätigung ist abgelaufen. Bitte bestätige noch einmal mit einem deiner Passkeys."
)

#: Neither a password nor a key: nothing confirms, and the person cannot help themselves.
KEIN_PASSKEY = gettext_lazy(
    "Dein Konto hat keinen Passkey, mit dem du bestätigen könntest. "
    "Ein Administrator kann dir ein Passwort setzen."
)

#: The signature of a confirmation does not hold up. Signing in says 'FEHLGESCHLAGEN'.
NICHT_BESTAETIGT = gettext_lazy("Die Bestätigung mit diesem Passkey ist fehlgeschlagen.")

#: Under this key the session holds why a key was not removed, until the list shows
#: it at the key's form ('PasskeyListeView').
NICHT_ENTFERNT = "passkey_nicht_entfernt"

#: Which store a key came from, by its AAGUID. A suggestion for the name,
#: nothing more: the person may call the key what they like. The ids are
#: published by the providers (FIDO metadata, "passkey-authenticator-aaguids").
#: Two names have a German and an English form. They are translated, the
#: product names are not (until 10/2026 an English account got the German ones).
ANBIETER = {
    "fbfc3007-154e-4ecc-8c0b-6e020557d7bd": gettext_lazy("iCloud-Schlüsselbund"),
    "dd4ec289-e01d-41c9-bb89-70fa845d4bf2": gettext_lazy("iCloud-Schlüsselbund"),
    "ea9b8d66-4d01-1d21-3ce4-b6b48cb575d4": gettext_lazy("Google Passwortmanager"),
    "08987058-cadc-4b81-b6e1-30de50dcbe96": "Windows Hello",
    "9ddd1817-af5a-4672-a2b9-3e3dd95000a9": "Windows Hello",
    "6028b017-b1d4-4c02-b4b3-afcdafc96bb2": "Windows Hello",
    "adce0002-35bc-c60a-648b-0b25f1f05503": "Chrome",
    "bada5566-a7aa-401f-bd96-45619a55120d": "1Password",
    "d548826e-79b4-db40-a3d8-11116f7e8349": "Bitwarden",
    "531126d6-e717-415c-9320-3d9aa6981239": "Dashlane",
    "53414d53-554e-4700-0000-000000000000": "Samsung Pass",
    "fbefdf68-fe86-0106-213e-4d5fa24cbe2e": "KeePassXC",
    "50726f74-6f6e-5061-7373-50726f746f6e": "Proton Pass",
}


def rp_id(request: HttpRequest) -> str:
    """The host without port. An IPv6 literal would otherwise be cut at its first colon."""
    return urlsplit("//" + request.get_host()).hostname or ""


def origin(request: HttpRequest) -> str:
    return f"{'https' if request.is_secure() else 'http'}://{request.get_host()}"


def rp_name() -> str:
    return str(Erscheinungsbild.aktuell().titel)


def user_handle(benutzer: CustomUser) -> bytes:
    """What the browser stores as the account: a hash of the user name.

    Not the number, because a restored backup renumbers the accounts. Not
    the name itself, because the standard wants no personal data in it and
    allows 64 bytes at most.
    """
    return hashlib.sha256(benutzer.username.encode()).digest()


def challenge_merken(
    request: HttpRequest, challenge: bytes, schluessel: str = SITZUNGSSCHLUESSEL
) -> None:
    """With the time of the question: the answer has 'GUELTIG_SEKUNDEN'.

    A visitor who is not signed in gets a session for the question alone,
    and it holds as long as the question. With Django's two weeks every
    anonymous click on the button, and on the decoy's, left a row behind,
    and removing a passkey decoded all of them to find an account's
    sessions. Anyone could send millions of them (until 10/2026). Sessions
    now name their account ('Sitzung'), and the short life keeps the table
    small. The session of a signed-in person keeps its age, and signing in
    gives the short one its full age ('frist_aufheben').

    **The rows of the last two minutes, no more.** A visitor who sends its
    session back keeps its one row. A new row first deletes the ones
    without an account that ran out: they stayed until the nightly cleanup
    otherwise, and a caller without a cookie wrote one per request (until
    10/2026). No limit per sender: the session has no column for the
    address. A limit for all would let anyone switch the passkey sign-in
    off for everyone. Both doors ask here, so the decoy answers the same.
    """
    request.session[schluessel] = {
        "challenge": bytes_to_base64url(challenge),
        "gefragt": time.time(),
    }
    if not request.user.is_authenticated:
        request.session.set_expiry(GUELTIG_SEKUNDEN)
        if request.session.session_key is None:
            Sitzung.objects.filter(konto__isnull=True, expire_date__lte=timezone.now()).delete()


def frist_aufheben(sender, request: HttpRequest | None, user, **kwargs) -> None:
    """Whoever signs in gets a session of the full age, also after a passkey's question.

    Django keeps the content of the session when it signs someone in, under
    a new key ('cycle_key'), and with it the short expiry of
    'challenge_merken'. Without this a password typed after a click on the
    passkey button signed in for two minutes. Connected to 'user_logged_in'
    ('apps.py'). Nothing else sets an expiry of its own.
    """
    sitzung = getattr(request, "session", None)
    if sitzung is not None:
        sitzung.set_expiry(None)


def challenge_holen(request: HttpRequest, schluessel: str = SITZUNGSSCHLUESSEL) -> bytes | None:
    """Once, and only within 'GUELTIG_SEKUNDEN' of the question.

    Whoever answers twice to the same question is refused the second time.
    Until 10/2026 a question stayed in the session for as long as the
    session lived: a dialog of the device that was broken off left it lying
    for whoever sat down at the browser later. The app's challenge always
    had its two minutes. A question stored without its time, by the state
    from before, counts as expired. The decoy asks and checks through the
    same two functions, so a late answer reads the same there ('honeypot.py').
    """
    wert = request.session.pop(schluessel, None)
    if not isinstance(wert, dict) or time.time() - wert.get("gefragt", 0) > GUELTIG_SEKUNDEN:
        return None
    return base64url_to_bytes(wert["challenge"])


def als_json(options) -> JsonResponse:
    return JsonResponse(json.loads(options_to_json(options)))


def fehler(text: str, status: int = 400) -> JsonResponse:
    return JsonResponse({"fehler": text}, status=status)


def konto(request: HttpRequest) -> CustomUser:
    benutzer = request.user
    assert isinstance(benutzer, CustomUser)
    return benutzer


def gelesen(request: HttpRequest) -> dict:
    """The JSON body of the script, or an empty dict for anything else.

    A text with a lone surrogate ('\\ud800') counts as anything else. JSON
    lets one in by its escape, and no browser sends one: no database stores
    it, and no hash takes it. Until 10/2026 it ended in a server error, at
    the sign-in, at the decoy's record of the attempt and with a password.
    """
    try:
        daten = json.loads(request.body or b"{}")
        json.dumps(daten, ensure_ascii=False).encode()
    except ValueError:
        return {}
    return daten if isinstance(daten, dict) else {}


def ohne_unspeicherbares(text: str) -> str:
    """The text without what no database stores: a NUL and lone surrogates.

    PostgreSQL refuses a NUL in a text column, and no backend encodes a lone
    surrogate. Nobody types either into a name.
    """
    return text.replace("\x00", "").encode("utf-8", "ignore").decode("utf-8")


def vorgeschlagener_name(aaguid: str) -> str:
    """In the active language. Stored is the text, so it stays when the language changes."""
    if anbieter := ANBIETER.get(aaguid):
        return str(anbieter)
    return _("Passkey vom %(datum)s") % {
        "datum": formats.date_format(timezone.localdate(), "SHORT_DATE_FORMAT")
    }


def anmeldeoptionen(request: HttpRequest, benutzer: CustomUser | None = None):
    """The question for signing in, or with 'benutzer' for confirming that account.

    The decoy asks the one without ('honeypot.py'). A confirmation names the
    account's keys, so the device offers no key of another account.
    """
    return generate_authentication_options(
        rp_id=rp_id(request),
        allow_credentials=None
        if benutzer is None
        else [
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(passkey.kennung))
            for passkey in Passkey.objects.filter(benutzer=benutzer)
        ],
        user_verification=UserVerificationRequirement.REQUIRED,
    )


def anlegeoptionen(request: HttpRequest, benutzer: CustomUser):
    """The question for registering: the account, and which keys it already has."""
    return generate_registration_options(
        rp_id=rp_id(request),
        rp_name=rp_name(),
        user_id=user_handle(benutzer),
        user_name=benutzer.username,
        user_display_name=str(benutzer),
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
        # The device must not make a second key for the same account.
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(passkey.kennung))
            for passkey in Passkey.objects.filter(benutzer=benutzer)
        ],
    )


def passwort_haelt(request: HttpRequest, benutzer: CustomUser, passwort: str) -> bool:
    """Whether the typed password is the account's, counted like a sign-in.

    Through 'authenticate()' and not 'check_password()': that way the bolt of
    'axes' counts a wrong one here as at the sign-in, and five of them lock
    this door with the others. The request has to be Django's, also for the
    API: 'axes' notes the lock on the one it gets ('TokenHolenView').
    """
    return authenticate(request, username=benutzer.get_username(), password=passwort) is not None


def bestaetigung_merken(request: HttpRequest, benutzer: CustomUser) -> None:
    """The website's confirmation, with its account and its time.

    It holds 'GUELTIG_SEKUNDEN' for every step in that time and is not used
    up: a dialog of the device broken off may be tried again, as a password
    typed once stays in its field. The app's is not used up either, for it
    is signed and stored nowhere ('bestaetigung_signiert'). Whoever wants to
    use it within the two minutes needs this very session.
    """
    request.session[BESTAETIGT] = {"konto": benutzer.get_username(), "gefragt": time.time()}


def bestaetigung_signiert(benutzer: CustomUser) -> str:
    """The app's confirmation. It has no session, so the confirmation travels signed.

    Only the device that confirmed reads it, in the answer to its signature.
    A phone with nothing but a leftover access token gets none.
    """
    return signing.dumps(benutzer.get_username(), salt=BESTAETIGUNGSSALZ)


def _unbestaetigt(benutzer: CustomUser, gegeben: bool) -> str:
    """Why a step of an account without a password waits, as a sentence for the person."""
    if not Passkey.objects.filter(benutzer=benutzer).exists():
        return str(KEIN_PASSKEY)
    return str(BESTAETIGUNG_ABGELAUFEN if gegeben else BESTAETIGUNG_FEHLT)


def fehlende_bestaetigung(request: HttpRequest, benutzer: CustomUser) -> str:
    """'' if the session holds a confirmation of this account, otherwise why the step waits.

    Of this account and no other: whoever signs in as another one gets an
    empty session from Django anyway.
    """
    wert = request.session.get(BESTAETIGT)
    if (
        isinstance(wert, dict)
        and wert.get("konto") == benutzer.get_username()
        and 0 <= time.time() - wert.get("gefragt", 0) <= GUELTIG_SEKUNDEN
    ):
        return ""
    return _unbestaetigt(benutzer, BESTAETIGT in request.session)


def fehlende_bestaetigung_der_app(token, benutzer: CustomUser) -> str:
    """'' if the app sent a confirmation of this account in time, otherwise why the step waits."""
    try:
        name = signing.loads(str(token or ""), salt=BESTAETIGUNGSSALZ, max_age=GUELTIG_SEKUNDEN)
    except signing.BadSignature, TypeError, ValueError:
        name = None
    if name == benutzer.get_username():
        return ""
    return _unbestaetigt(benutzer, bool(token))


def fehlender_beweis(request: HttpRequest, benutzer: CustomUser, passwort) -> str:
    """'' if the website may take a step that could take the account, otherwise why it waits.

    The account's password, typed with the step. Without a usable password
    the confirmation with one of its keys in the session ('fehlende_bestaetigung').
    A new key and removing one ask the same, and so does a new address
    ('EmailAendernForm'): a session does not say who sits at the device.
    Called before any transaction, for 'passwort_haelt' hashes.
    """
    if not benutzer.has_usable_password():
        return fehlende_bestaetigung(request, benutzer)
    if not isinstance(passwort, str) or not passwort:
        return str(PASSWORT_FEHLT)
    if not passwort_haelt(request, benutzer, passwort):
        return str(PASSWORT_FALSCH)
    return ""


def nimmt_den_letzten_weg(passkeys: Iterable[Passkey]) -> bool:
    """Whether removing these passkeys leaves no administrator who can sign in.

    Without one nobody gets into the administration any more, only
    'manage.py changepassword' on the machine. The other direction, switching
    the password off, the administration asks the same way
    ('CustomUserAdmin.user_change_password'). Counted are the administrators
    with a way in afterwards: a password they can type, or a passkey that
    stays. Until 10/2026 every administrator counted, one without either too,
    and each of two who sign in with a passkey only could remove the last one,
    relying on the other. A selection over both in the administration did the
    same at once.

    Asked under 'administratoren_sperren', in the transaction that deletes:
    otherwise two removals side by side each still see the other's key.
    """
    nummern = [passkey.pk for passkey in passkeys]
    vorher = administratoren()
    bleibende = Passkey.objects.exclude(pk__in=nummern).values("benutzer")
    nachher = vorher.filter(mit_passwort() | Q(pk__in=bleibende))
    return vorher.exists() and not nachher.exists()


class PasskeyFehler(Exception):
    """A refused step. The text is the sentence for the person."""


def _geprueft(credential) -> dict:
    """The browser's answer as a dict, or the refusal the page knows.

    The library parses a dict or a JSON text. Anything else, a list or a
    number, ran into an 'AttributeError' before it until 10/2026: a server
    error with a traceback for a request anyone can send without signing in.
    'response' has to be a dict as well, 'vor_der_signatur' reads the user
    handle from it.

    **The id is base64url**, WebAuthn's alphabet ('KENNUNG'), before any
    query. Anything else is no browser's either. PostgreSQL does not compare
    an id with a NUL to a text column, and no backend encodes one with a lone
    surrogate ('\\ud800'): looking either up ended in a server error, at
    both doors and in the app (until 10/2026). Only the NUL was refused then.
    """
    credential = credential or {}
    if (
        not isinstance(credential, dict)
        or not isinstance(credential.get("response", {}), dict)
        or not isinstance(kennung := credential.get("id"), str)
        or not KENNUNG.fullmatch(kennung)
    ):
        raise PasskeyFehler(str(UNGUELTIG))
    return credential


def registrieren(
    request: HttpRequest, benutzer: CustomUser, credential, challenge: bytes, name: str
) -> Passkey:
    """The browser's answer to 'anlegeoptionen' becomes a stored key.

    Shared by the website and the API of the counting app: the same checks,
    the same sentences on refusal.
    """
    try:
        ergebnis = verify_registration_response(
            credential=_geprueft(credential),
            expected_challenge=challenge,
            expected_rp_id=rp_id(request),
            expected_origin=origin(request),
            require_user_verification=True,
        )
    except WebAuthnException as grund:
        raise PasskeyFehler(str(UNGUELTIG)) from grund
    if not (name := ohne_unspeicherbares(name).strip()[:100]):
        # In the account's language, as its mails: the request of the app may carry another.
        with translation.override(benutzer.sprache):
            name = vorgeschlagener_name(ergebnis.aaguid)
    try:
        # Its own savepoint: a refused row must not spoil the enclosing transaction.
        with transaction.atomic():
            return Passkey.objects.create(
                benutzer=benutzer,
                kennung=bytes_to_base64url(ergebnis.credential_id),
                oeffentlicher_schluessel=ergebnis.credential_public_key,
                signaturzaehler=ergebnis.sign_count,
                name=name,
            )
    except IntegrityError as grund:
        raise PasskeyFehler(_("Dieser Passkey ist schon hinterlegt.")) from grund


def vor_der_signatur(credential, angemeldet: CustomUser | None = None) -> tuple[dict, Passkey]:
    """The checks of signing in before the signature: the answer, its key, the key's account.

    The answer has to be one ('_geprueft'), its key has to be stored, the
    handle the browser sends along has to fit the key's account, and the
    account has to be active. Each refusal is the sentence for the person.
    The decoy runs the same checks and says "failed" only after them
    ('honeypot.py'). Its own copy of them had drifted (until 10/2026): what
    was no browser's answer at all heard "not known" there, a known key with
    a foreign handle or of a deactivated account "failed".

    **With 'angemeldet' the account is known before**, as for a confirmation
    ('bestaetigen'): the key has to be one of its own. Then the device may
    leave the handle out, as the standard allows once the question named the
    account's keys (WebAuthn, 7.2 step 6). One it sends has to fit all the same.
    """
    credential = _geprueft(credential)
    passkey = Passkey.objects.select_related("benutzer").filter(kennung=credential["id"]).first()
    if passkey is None:
        raise PasskeyFehler(str(UNBEKANNTER_SCHLUESSEL))
    benutzer = passkey.benutzer
    handle = str((credential.get("response") or {}).get("userHandle") or "")
    if angemeldet is not None and benutzer.pk != angemeldet.pk:
        raise PasskeyFehler(str(ANDERES_KONTO))
    if (handle or angemeldet is None) and handle != bytes_to_base64url(user_handle(benutzer)):
        raise PasskeyFehler(str(ANDERES_KONTO))
    if not benutzer.is_active:
        raise PasskeyFehler(str(DEAKTIVIERT))
    return credential, passkey


def anmelden[T](
    request: HttpRequest,
    credential,
    challenge: bytes,
    einlassen: Callable[[CustomUser], T],
    angemeldet: CustomUser | None = None,
) -> T:
    """The browser's answer to 'anmeldeoptionen' lets the key's account in, or nothing.

    Who signs in says the key, not a user name. The handle the browser sends
    along has to fit the key's account, the account has to be active
    ('vor_der_signatur'), and the signature has to hold up against the
    stored public key. How the account is let in is the caller's: a session
    on the website, a token pair in the app ('einlassen').

    **Under the account's lock**, as a renewal ('sign_out.py'). Removing the
    key signs the account out everywhere. A sign-in that read the key just
    before still let the account in afterwards, and its session or pair
    outlived the removal (until 10/2026). So the key is asked once more under
    the lock, and only then is the account let in.

    **The account as it stands under the lock**, read there and let in as
    such. A deactivation signs the account out everywhere as well. Until
    10/2026 the account read before the lock was let in, active or not: a
    deactivation that came in between had signed out what was there, the
    session or pair came afterwards, and it held again once the account was
    switched on. One switched off meanwhile hears 'DEAKTIVIERT'. One deleted
    or renamed meanwhile hears 'UNBEKANNTER_SCHLUESSEL', as a key never
    stored does. A new password in between goes into the session's check
    value, not the old one.

    The counter is written before the lock and on its own, so call this
    outside 'transaction.atomic()'. Under the lock, its row would wait for a
    removal that deleted the key and waits for the account. Held in a
    transaction while waiting for the account, it would stop an
    administrator's removal, which holds the account before it deletes the
    key. Both ended in "deadlock detected" under PostgreSQL. A key gone by
    the time the counter is written is refused like one never stored. Until
    10/2026 that was a server error.

    With 'angemeldet' the same ceremony confirms an account that is signed in
    already ('bestaetigen'), and 'einlassen' notes the confirmation.
    """
    credential, passkey = vor_der_signatur(credential, angemeldet)
    benutzer = passkey.benutzer
    try:
        ergebnis = verify_authentication_response(
            credential=credential,
            expected_challenge=challenge,
            expected_rp_id=rp_id(request),
            expected_origin=origin(request),
            credential_public_key=bytes(passkey.oeffentlicher_schluessel),
            credential_current_sign_count=passkey.signaturzaehler,
            require_user_verification=True,
        )
    except WebAuthnException as grund:
        absage = FEHLGESCHLAGEN if angemeldet is None else NICHT_BESTAETIGT
        raise PasskeyFehler(str(absage)) from grund
    gezaehlt = Passkey.objects.filter(pk=passkey.pk).update(
        signaturzaehler=ergebnis.new_sign_count,
        zuletzt_benutzt=timezone.now(),
    )
    if not gezaehlt:
        raise PasskeyFehler(str(UNBEKANNTER_SCHLUESSEL))
    with transaction.atomic():
        gesperrt = gesperrtes_konto(benutzer.get_username())
        if (
            gesperrt is None
            or gesperrt.pk != benutzer.pk
            or not Passkey.objects.filter(pk=passkey.pk, benutzer=gesperrt).exists()
        ):
            raise PasskeyFehler(str(UNBEKANNTER_SCHLUESSEL))
        if not gesperrt.is_active:
            raise PasskeyFehler(str(DEAKTIVIERT))
        return einlassen(gesperrt)


def bestaetigen[T](
    request: HttpRequest,
    benutzer: CustomUser,
    credential,
    challenge: bytes,
    merken: Callable[[CustomUser], T],
) -> T:
    """The answer to a confirmation's question, from an account that is signed in already.

    The checks of signing in, every one of them ('anmelden'): the person
    verified, origin and RP id, the signature against the stored key, the
    counter, and key and account asked once more under the account's lock.
    Only the key has to be one of this account's own ('vor_der_signatur').
    What the confirmation becomes is the caller's: a note in the session on
    the website ('bestaetigung_merken'), a signed token in the app
    ('bestaetigung_signiert'). Called outside 'transaction.atomic()', like
    signing in.
    """
    return anmelden(request, credential, challenge, merken, angemeldet=benutzer)


class NurAngemeldet(LoginRequiredMixin, AnsichtBasis):
    """'LoginRequiredMixin', but 'passkey.js' hears 401 and not the sign-in page.

    'fetch' follows the redirect by itself, and the sign-in page answers 200.
    Until 10/2026 the script took that page for the options and showed a
    technical error. After the second step it reloaded as if the key were
    stored. The text for the 401 stands in the page, in its language
    ('components/passkey_script.html'): without a session the server no
    longer knows the account's.
    """

    def handle_no_permission(self):
        if im_hintergrund(self.request) and not self.request.user.is_authenticated:
            return HttpResponse(status=HTTPStatus.UNAUTHORIZED)
        return super().handle_no_permission()


class PasskeyAnlegenOptionenView(NurAngemeldet, View):
    """Step one of registering: the password, then what the browser needs to create a key.

    The password is asked as before a new address ('EmailAendernForm'): a
    session does not say who sits at the device. Until 10/2026 it sufficed,
    and whoever found a browser open added a key of their own, which kept
    working after the owner changed the password. An account without a
    usable password has nothing to type. It confirms with one of its keys
    first ('PasskeyBestaetigenView'), and the confirmation in the session
    stands in for the password.

    Asked before the browser's dialog, and the challenge goes into the
    session only once the password or the confirmation held. The second step
    takes no other ('ANLEGESCHLUESSEL'), so the challenge itself proves the
    check.
    """

    def post(self, request):
        benutzer = konto(request)
        if grund := fehlender_beweis(request, benutzer, gelesen(request).get("passwort")):
            return fehler(grund)
        options = anlegeoptionen(request, benutzer)
        challenge_merken(request, options.challenge, ANLEGESCHLUESSEL)
        return als_json(options)


class PasskeyAnlegenView(NurAngemeldet, View):
    """Step two: the browser's answer becomes a stored key."""

    def post(self, request):
        daten = gelesen(request)
        challenge = challenge_holen(request, ANLEGESCHLUESSEL)
        if challenge is None:
            return fehler(str(ABGELAUFEN))
        try:
            passkey = registrieren(
                request,
                konto(request),
                daten.get("credential"),
                challenge,
                str(daten.get("name") or ""),
            )
        except PasskeyFehler as grund:
            return fehler(str(grund))
        passkey_melden(konto(request), passkey.name, angelegt=True)
        messages.success(request, _("Passkey „%(name)s“ ist angelegt.") % {"name": passkey.name})
        return JsonResponse({"ok": True})


class PasskeyBestaetigenOptionenView(NurAngemeldet, View):
    """Step one of a confirmation: the question, for the account's own keys only.

    For an account without a usable password, before a new key or a new
    address, where another account types its password. The challenge lies
    under a key of its own ('BESTAETIGUNGSSCHLUESSEL'), so a question of
    signing in or of registering answers nothing here, and the other way
    round. Without a key there is nothing to ask ('KEIN_PASSKEY').
    """

    def post(self, request):
        benutzer = konto(request)
        if not Passkey.objects.filter(benutzer=benutzer).exists():
            return fehler(str(KEIN_PASSKEY))
        options = anmeldeoptionen(request, benutzer)
        challenge_merken(request, options.challenge, BESTAETIGUNGSSCHLUESSEL)
        return als_json(options)


class PasskeyBestaetigenView(NurAngemeldet, View):
    """Step two: the signature of one of the account's keys, noted in the session.

    The checks are those of signing in ('bestaetigen'). The step that needs
    the confirmation asks the session ('fehlende_bestaetigung').
    """

    def post(self, request):
        daten = gelesen(request)
        challenge = challenge_holen(request, BESTAETIGUNGSSCHLUESSEL)
        if challenge is None:
            return fehler(str(ABGELAUFEN))

        def merken(benutzer: CustomUser) -> None:
            bestaetigung_merken(request, benutzer)

        try:
            bestaetigen(request, konto(request), daten.get("credential"), challenge, merken)
        except PasskeyFehler as grund:
            return fehler(str(grund))
        return JsonResponse({"ok": True})


# Both doors of signing in say 'no-store' like the decoy under '/admin/'
# ('honeypot.py') and like the login page. Until 10/2026 only the decoy did,
# and one request told the two apart by their headers.
@method_decorator(never_cache, name="dispatch")
class PasskeyAnmeldenOptionenView(View):
    """Step one of signing in: the challenge, without a user name."""

    def post(self, request):
        options = anmeldeoptionen(request)
        challenge_merken(request, options.challenge)
        return als_json(options)


@method_decorator(never_cache, name="dispatch")
class PasskeyAnmeldenView(View):
    """Step two: the signature opens the account the key belongs to.

    Signed in with the model backend, not through 'authenticate()': there is
    no password to check, the signature is the proof. 'next' comes from the
    login form, checked like Django checks it.
    """

    def post(self, request):
        daten = gelesen(request)
        challenge = challenge_holen(request)
        if challenge is None:
            return fehler(str(ABGELAUFEN))

        def einlassen(benutzer: CustomUser) -> None:
            login(request, benutzer, backend="django.contrib.auth.backends.ModelBackend")
            # Saved here, under the account's lock. The middleware writes the
            # account into the session only after the response, and a removal
            # in between ended every session but this one, which did not name
            # the account yet. One that comes after this and before the
            # middleware leaves it nothing to save: Django answers 400
            # ('SessionInterrupted'), and nobody is signed in.
            request.session.save()

        try:
            anmelden(request, daten.get("credential"), challenge, einlassen)
        except PasskeyFehler as grund:
            return fehler(str(grund))
        return JsonResponse({"weiter": self.ziel(request, str(daten.get("next") or ""))})

    @staticmethod
    def ziel(request: HttpRequest, gewuenscht: str) -> str:
        if gewuenscht and url_has_allowed_host_and_scheme(
            gewuenscht, allowed_hosts={request.get_host()}, require_https=request.is_secure()
        ):
            return gewuenscht
        return resolve_url(settings.LOGIN_REDIRECT_URL)


class PasskeyListeView(LoginRequiredMixin, TemplateView):
    """The keys of the account, to add to and to withdraw.

    Under '/accounts/', like changing the password: counters get here too,
    although the rest of the site is closed to them. A removal that was
    refused left its reason in the session, and the key's form shows it once
    ('NICHT_ENTFERNT').
    """

    template_name = "accounts/passkey_list.html"

    def get_context_data(self, **kwargs):
        abgelehnt = self.request.session.pop(NICHT_ENTFERNT, None)
        if not isinstance(abgelehnt, dict):
            abgelehnt = {}
        return super().get_context_data(**kwargs) | {
            "passkeys": Passkey.objects.filter(benutzer=konto(self.request)),
            "abgelehnt": abgelehnt.get("passkey"),
            "absage": abgelehnt.get("grund", ""),
        }


class PasskeyLoeschenView(LoginRequiredMixin, View):
    """Withdraws one of the own keys. The device keeps its half and is simply unknown then.

    Whoever signed in with it is signed out, on the website and in the app
    ('sign_out.py'). This session stays: the person is sitting at it.

    **Against the password, as a new key**, or without one against the
    confirmation with one of the account's keys ('fehlender_beweis'). Until
    10/2026 the session sufficed. Whoever found it open removed every key,
    and the owner of an account without a password had no way in left, while
    the session went on and gave the app a pair that renews itself
    ('token_aus_sitzung'). Asked before the transaction: hashing under the
    administrators' lock would hold every other removal as long. A refusal
    goes back to the list and stands at the key's form.
    """

    def post(self, request, pk):
        benutzer = konto(request)
        get_object_or_404(Passkey, pk=pk, benutzer=benutzer)
        if grund := fehlender_beweis(request, benutzer, request.POST.get("passwort")):
            request.session[NICHT_ENTFERNT] = {"passkey": pk, "grund": grund}
            return redirect("passkey_list")
        with transaction.atomic():
            administratoren_sperren()
            passkey = get_object_or_404(Passkey, pk=pk, benutzer=benutzer)
            if nimmt_den_letzten_weg([passkey]):
                messages.error(request, LETZTER_ADMINISTRATOR_BEHAELT_PASSKEY)
                return redirect("passkey_list")
            passkey.delete()
            ueberall_abmelden(benutzer, ausser=request.session.session_key)
        passkey_melden(benutzer, passkey.name, angelegt=False)
        messages.success(request, _("Passkey „%(name)s“ ist entfernt.") % {"name": passkey.name})
        return redirect("passkey_list")
