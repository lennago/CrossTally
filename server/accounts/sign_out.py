"""Signing an account out everywhere: its website sessions and the counting app's tokens.

A new password does that by itself. The session hash and the check value in
the token ('CHECK_REVOKE_TOKEN') both come from the stored password. A
removed passkey changes neither, and neither a session nor a token knows
which key it was opened with. Until 10/2026 a lost phone therefore kept its
sign-in after its passkey was removed, and with the refresh tokens rotating
it kept it for as long as it was used.

So the account is signed out the way the app's "Sign out" does it, only on
every device at once: every refresh token still valid goes on the blocklist,
and the website sessions of the account end. The access token of a device
runs out on its own within the hour ('ACCESS_TOKEN_LIFETIME'). The device
that removes the key stays signed in: on the website its session is kept, in
the app the refresh token it sends along ('api/passkey.py'). Until 10/2026
the app got a fresh pair instead, for any access token, and so did a device
that an earlier removal had signed out.

**Under the account's lock.** Renewing swaps the refresh token for a fresh
one. A renewal that asked the blocklist just before the sign-out read the
tokens and swapped just after it kept its fresh token, and the device its
access (until 10/2026). So both lock the account's row first: the sign-out
before it reads the tokens, the renewal before it asks the blocklist once
more and swaps ('api.serializers.account.TokenErneuernSerializer'). Whichever comes
second waits until the other one is written. A sign-out then sees the fresh
token, a renewal finds its own one blocked. The lock leaves the key alone
('FOR NO KEY UPDATE'): a new token of the account, which only points to its
row, does not wait for it.

**Bounded.** Every sign-in and every pair from a website session issued one
more token for two weeks, without any limit, and signing out loaded each one
that still applied as a whole row, its text included, and blocked them in
one list (until 10/2026). Measured with 100,000 tokens: 5.4 s and 225 MB
under SQLite. A counter could make every removal of a passkey and every
deactivation of their account run into the worker's timeout and roll back,
while it held the administrators' rows. Now issuing blocks the oldest
beyond 'HOECHSTENS_GUELTIG' ('paar_ausstellen'), an account gets at most
'HOECHSTENS_JE_STUNDE' new ones within the hour, renewals included
('neue_pruefen'), and blocking reads numbers in steps of 'STAPEL'.
"""

import math
from datetime import timedelta

from django.contrib.sessions.models import Session
from django.db import connection, transaction
from django.db.models import F, QuerySet
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import AuthenticationFailed, Throttled
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.utils import get_md5_hash_password

from django_project.roles import administratoren_sperren

from .models import Adresswechsel, CustomUser, Sitzung

#: Who the app lets in is gone or switched off by the time the pair would be issued.
KONTO_WEG = _("Dieses Konto gibt es nicht mehr.")
KONTO_DEAKTIVIERT = _("Dieses Konto ist deaktiviert.")

#: Valid refresh tokens an account holds at most. A device holds one at a time,
#: the renewal swaps it. What piles up are sign-ins that never signed out: an
#: app beside the website takes a fresh pair from the session on every start, a
#: phone is reset, a browser cleared. Fifty is more than the two weeks of a
#: token bring for a person who starts the app beside the website a few times a
#: day, and the newest stay, the ones of devices in use among them. Beyond it
#: the oldest go, so signing out blocks fifty at most, in one step ('STAPEL').
HOECHSTENS_GUELTIG = 50

#: New refresh tokens an account gets within 'FENSTER', renewals included. A
#: device in use renews about once an hour, a person signs in a few times a day.
#: A full run of the browser tests issued 41 for its demo counter within two
#: minutes (measured 10/2026), and the bound is more than ten times that, for
#: runs one after the other against the same server too. Beyond it the account
#: waits for the window.
HOECHSTENS_JE_STUNDE = 500
FENSTER = timedelta(hours=1)

#: Refresh tokens read and blocked per step. Big enough for every account within
#: its bound in one, small enough that a list from before the bound costs no memory.
STAPEL = 500

#: What the person hears beyond 'HOECHSTENS_JE_STUNDE'.
ZU_OFT = _(
    "Dieses Konto hat sich in der letzten Stunde zu oft angemeldet. Bitte später noch einmal "
    "versuchen."
)


class ZuOftAngemeldet(Throttled):
    """429, a sentence for the person, and in 'Retry-After' when the next token can come.

    DRF's own text adds the seconds in its words. The app takes a 429 at the
    renewal for "later", at the sign-in for the bolt of failed attempts.
    """

    def __init__(self, warten: float) -> None:
        super().__init__(detail=str(ZU_OFT))
        self.wait = max(1, math.ceil(warten))


def _konten_sperren(konten: QuerySet[CustomUser]) -> None:
    """Holds the rows of these accounts until the transaction ends, by number."""
    list(konten.select_for_update(no_key=True).order_by("pk").values_list("pk", flat=True))


def gesperrtes_konto(name: str | None) -> CustomUser | None:
    """The account of this user name, locked until the transaction ends, and read only then.

    For the renewal of a token, the other side of 'tokens_sperren'. Read
    under the lock, the account carries the password as it stands after a
    change that came first. Called inside 'transaction.atomic()'.
    """
    return CustomUser.objects.select_for_update(no_key=True).filter(username=name).first()


def paar_ausstellen(konto: CustomUser) -> RefreshToken:
    """A new refresh token for the account, issued under its lock and only while it is active.

    For every way that lets the account into the app with a pair of its
    own: the password ('api.serializers.account.TokenHolenSerializer'), the
    website's session ('token_aus_sitzung'), a new password
    ('passwort_aendern'), a passkey ('api/passkey.py', under the lock its
    sign-in already holds). A deactivation signs the account out under the
    same lock. A pair issued after it read the
    tokens outlived it: refused while the account was off, valid again once
    it was switched on (until 10/2026). So the row is locked without its key
    and read anew, and a gone or switched-off account hears 401. Inside the
    caller's transaction the lock holds until that one ends.

    The token carries the password of the instance given
    ('CHECK_REVOKE_TOKEN'). The caller vouches for it: a password changed
    meanwhile leaves the old check value in the pair, and the pair is refused.
    """
    with transaction.atomic():
        gesperrt = CustomUser.objects.select_for_update(no_key=True).filter(pk=konto.pk).first()
        if gesperrt is None:
            raise AuthenticationFailed(KONTO_WEG, code="user_not_found")
        if not gesperrt.is_active:
            raise AuthenticationFailed(KONTO_DEAKTIVIERT, code="user_inactive")
        neue_pruefen(gesperrt)
        erneuerung = RefreshToken.for_user(konto)
        _ueberzaehlige_sperren(gesperrt)
        return erneuerung


def neue_pruefen(konto: CustomUser) -> None:
    """Refuses one more token for the account beyond the bound of the hour ('ZuOftAngemeldet').

    Counted are the account's rows in the list of issued tokens within
    'FENSTER', each by its time: a sign-in, a pair from a session, a renewal.
    The index on the account finds them. Called under the account's lock,
    by 'paar_ausstellen' and by the renewal, so two at the same moment do
    not both slip under the bound.
    """
    seit = timezone.now() - FENSTER
    letzte = OutstandingToken.objects.filter(user=konto, created_at__gte=seit)
    if letzte.count() < HOECHSTENS_JE_STUNDE:
        return
    # Once the token at the bound leaves the window, fewer than the bound remain.
    frei_ab = letzte.order_by("-created_at").values_list("created_at", flat=True)[
        HOECHSTENS_JE_STUNDE - 1
    ]
    raise ZuOftAngemeldet((frei_ab + FENSTER - timezone.now()).total_seconds())


def _ueberzaehlige_sperren(konto: CustomUser) -> None:
    """The account's oldest valid refresh tokens beyond 'HOECHSTENS_GUELTIG' onto the blocklist."""
    offen = OutstandingToken.objects.filter(
        user=konto, expires_at__gt=timezone.now(), blacklistedtoken__isnull=True
    )
    if offen.count() <= HOECHSTENS_GUELTIG:
        return
    neueste = offen.order_by(F("created_at").desc(nulls_last=True), "-pk")
    behalten = list(neueste.values_list("pk", flat=True)[:HOECHSTENS_GUELTIG])
    _sperren(offen.exclude(pk__in=behalten))


def _sperren(tokens: QuerySet[OutstandingToken]) -> int:
    """These refresh tokens onto the blocklist, those that still apply. Returns how many.

    In steps of 'STAPEL', by number: one statement reads the numbers, one
    writes them. Until 10/2026 every token was loaded as a whole row and
    blocked in one list. An account holds 'HOECHSTENS_GUELTIG' at most, so
    one step is the rule. More come from before that bound, or with
    'alle_abmelden'.
    """
    offen = tokens.filter(expires_at__gt=timezone.now(), blacklistedtoken__isnull=True)
    gesperrt = letzte = 0
    while True:
        nummern = list(
            offen.filter(pk__gt=letzte).order_by("pk").values_list("pk", flat=True)[:STAPEL]
        )
        BlacklistedToken.objects.bulk_create(
            [BlacklistedToken(token_id=nummer) for nummer in nummern], ignore_conflicts=True
        )
        gesperrt += len(nummern)
        if len(nummern) < STAPEL:
            return gesperrt
        letzte = nummern[-1]


def geltendes_token(konto: CustomUser, gegeben) -> str | None:
    """The 'jti' of this refresh token, if it is a sign-in of the account that still holds.

    For a device that shows its own sign-in before it signs the account out
    everywhere else ('api/passkey.py'). An access token does not show that:
    nothing blocks it, it only runs out. A refresh token holds where the
    renewal would take it ('TokenErneuernSerializer'): signed, not expired,
    not on the blocklist, known to the server, issued to this account and
    under its present password. Called under the account's lock
    ('gesperrtes_konto'), so a sign-out at the same moment is seen.
    """
    if not gegeben:
        return None
    try:
        erneuerung = RefreshToken(gegeben)
    except TokenError:
        return None
    jti = erneuerung.get(api_settings.JTI_CLAIM)
    if erneuerung.get(api_settings.USER_ID_CLAIM) != konto.get_username():
        return None
    if api_settings.CHECK_REVOKE_TOKEN and erneuerung.get(
        api_settings.REVOKE_TOKEN_CLAIM
    ) != get_md5_hash_password(konto.password):
        return None
    if not OutstandingToken.objects.filter(jti=jti, user=konto).exists():
        return None
    return jti


def tokens_sperren(konto: CustomUser, ausser_token: str | None = None) -> None:
    """Every refresh token of the account that still applies, onto the blocklist.

    'ausser_token' names the one that stays, by its 'jti': the one of the
    app that removes a passkey. Read under the account's lock, which a
    renewal takes as well (see above). Inside a caller's transaction the lock
    holds until that one ends.
    """
    with transaction.atomic():
        _konten_sperren(CustomUser.objects.filter(pk=konto.pk))
        tokens = OutstandingToken.objects.filter(user=konto)
        if ausser_token:
            tokens = tokens.exclude(jti=ausser_token)
        _sperren(tokens)


def nach_kennung_sperren(kennung: str) -> None:
    """The refresh token with this id onto the blocklist, if it still applies.

    For a sign-out the app owes and settles without a website session
    ('api.views.account.abmelden_nachgeholt'), so the account is read off the
    token. Then under that account's lock, as for a renewal ('tokens_sperren'):
    a renewal of the same token at the same moment swapped it before, or it
    finds it blocked after. The token is read once more under the lock.
    """
    with transaction.atomic():
        inhaber = OutstandingToken.objects.filter(jti=kennung).values_list("user_id", flat=True)
        _konten_sperren(CustomUser.objects.filter(pk__in=list(inhaber)))
        _sperren(OutstandingToken.objects.filter(jti=kennung))


def sitzungen_beenden(konto: CustomUser, ausser: str | None = None) -> None:
    """Ends the account's website sessions, all but the one named 'ausser'.

    Found by the account's column, which the store writes on every save
    ('session_store.py'): one query in an index, however many other
    sessions are open. Until 10/2026 a session named its account only inside
    its encoded content, and every open one was decoded here, inside the
    transaction that holds the administrators.
    """
    sitzungen = Sitzung.objects.filter(konto=konto)
    if ausser:
        sitzungen = sitzungen.exclude(session_key=ausser)
    sitzungen.delete()


def sitzung_gilt(schluessel: str | None) -> bool:
    """Whether this website session is still open, asked anew.

    The request read it when it began. A sign-out on another device may have
    ended it since, and only the table knows that.
    """
    offen = Sitzung.objects.filter(session_key=schluessel, expire_date__gt=timezone.now())
    return bool(schluessel) and offen.exists()


def ueberall_abmelden(
    konto: CustomUser, ausser: str | None = None, ausser_token: str | None = None
) -> None:
    """Signs the account out on every device, and a new address waiting for it goes too.

    'ausser' names the session that stays, 'ausser_token' the refresh token
    ('tokens_sperren'). The waiting address ('Adresswechsel'): whoever signs
    a lost device out means its sessions and also what someone entered on
    it. Until 10/2026 a link to a stranger's mailbox applied for three days
    after removing a passkey or deactivating, and a deactivated account
    took it without hearing of it.
    """
    with transaction.atomic():
        tokens_sperren(konto, ausser_token)
        sitzungen_beenden(konto, ausser)
        Adresswechsel.zuruecknehmen(konto)


def alle_abmelden() -> tuple[int, int]:
    """Every account signed out at once. Returns the refresh tokens blocked and the sessions ended.

    For a database loaded from a dump with psql, or from a state with accounts
    through the scripts under 'bin/' ('manage.py sign_out_everyone'): it holds
    the sessions and tokens of its moment, and they would apply again,
    whatever happened since. The restore through the administration does the
    same by itself ('django_project/backup.py'). The addresses waiting for a
    confirmation go as well, as for one account ('ueberall_abmelden').

    Under the lock of every account, as for one (see above), so a renewal at
    the same moment does not slip past. The administrators first, as removing
    a passkey takes them ('administratoren_sperren'), then every account by
    number. By number alone, this would hold a counter and wait for an
    administrator with a higher number, while a removal holds that one and
    waits for the counter.
    """
    with transaction.atomic():
        administratoren_sperren()
        _konten_sperren(CustomUser.objects.all())
        tokens = _sperren(OutstandingToken.objects.all())
        sitzungen = _jede_sitzung_beenden()
        if _gibt_es(Adresswechsel):
            Adresswechsel.objects.all().delete()
    return tokens, sitzungen


def _gibt_es(modell) -> bool:
    """Whether the model's table is there: a dump from before lacks the newer ones."""
    return modell._meta.db_table in connection.introspection.table_names()


def _jede_sitzung_beenden() -> int:
    """Every session of the website, in both tables. Returns how many ended.

    The command runs in today's image, also on a dump from before 10/2026
    ('DEPLOYMENT.md', "Restoring"). Such a dump holds its sessions in Django's
    own table ('django_session') and has no table for 'Sitzung' yet. Left
    there, they would apply again: under the old version as they are, under
    this one once the migration that adds the table moves them over
    ('accounts/0003').
    """
    beendet, _einzeln = Session.objects.all().delete()
    if _gibt_es(Sitzung):
        beendet += Sitzung.objects.all().delete()[0]
    return beendet
