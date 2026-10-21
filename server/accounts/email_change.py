"""Changing the e-mail address of an account: the new address confirms itself first.

Who may change an address: the person themselves, through the account menu
on the website and in the counting app, and an administrator, on the user
form of the website and in the administration (decision of 2026-10-14).
Managers no longer do. With the address and "Forgot your password?" an
account could be taken over, and the mail to the old address would be the
only trace.

With a mail server the new address applies only once the person confirmed
it. A mail with a link goes there, and the link writes the address
('EmailBestaetigenView'). Until then the account keeps the old one. That
way a typo never cuts an account off from its mails, and nobody enters an
address they cannot read. Without a mail server the address applies at
once ('sofort'). So does it for a deactivated account: it gets no mail
anyway ('notification'), and one still waiting for its invitation is
deactivated too. Its corrected address is where the invitation goes next.

**The pending change lies in the database** ('Adresswechsel', decision of
2026-10-21). Until then the link carried the account, the new and the old
address itself, signed, and nothing could take it back: a new password,
signing out everywhere and deactivating left it valid for its three days,
a used link came back to life once the address went back to the old one,
and a deactivated account took it without hearing of it. Now the link
signs only the number of the row, good for as long as a link of "Forgot
your password?". One change waits per account: a new request replaces it,
so only the last link counts. Used once, it is dead, and so it is after a
new password, after signing out everywhere, after deactivating and after
an address written at once ('Adresswechsel.zuruecknehmen').
"""

from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.core import signing
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from django_project.mail import mail_moeglich
from organisation.mail_texts import Mailart

from .invitation import gueltig_fuer_tage
from .models import Adresswechsel, CustomUser
from .notification import Kontostand, aenderung_melden, senden, urheber

#: Its own salt: a token of the invitation or of the reset opens nothing here,
#: and neither does a link from before the row ('Adresswechsel'), which signed the change itself.
SALZ = "accounts.email_change.Adresswechsel"

#: Shown to whoever changed another account's address while it waits for the confirmation.
BESTAETIGUNG_UNTERWEGS = _(
    "An %(email)s ist eine Mail mit einem Link unterwegs. Die Adresse gilt, sobald die "
    "Person ihn bestätigt hat. Bis dahin bleibt die bisherige."
)
BESTAETIGUNG_NICHT_GESENDET = _(
    "Die Mail an %(email)s ging nicht hinaus, der E-Mail-Server antwortet nicht. Die Adresse "
    "bleibt, wie sie war."
)


class AdresseVergeben(Exception):
    """The address went to another account while the link was on its way."""


class LinkVerbraucht(Exception):
    """The link no longer applies: used, replaced, taken back or expired, or the account changed."""


class _Unbekannt:
    """Someone who entered the address and is gone since: not the person, and nobody to name."""

    pk = None

    def __str__(self) -> str:
        return ""


@dataclass(frozen=True)
class Ergebnis:
    """What happened on a request from the person: applied at once, or the mail is out."""

    #: True: the address applies only once the link in the mail is used.
    bestaetigung: bool
    #: Like 'notification.senden': True sent, False the server did not answer, None nothing.
    gesendet: bool | None


def sofort(konto: CustomUser) -> bool:
    """Whether a new address applies at once instead of after the confirmation.

    Without a mail server nobody could confirm anything. A deactivated
    account gets no mail until it is active again, and one still waiting
    for its invitation is deactivated: it gets the corrected address so the
    invitation can reach it. An active account without a password (signing
    in by passkey only) confirms like every other.
    """
    return not mail_moeglich() or not konto.is_active


def adresse_vergeben(email: str, konto: CustomUser) -> bool:
    """Whether another account carries this address, whatever its case."""
    return CustomUser.objects.filter(email__iexact=email).exclude(pk=konto.pk).exists()


def vormerken(konto: CustomUser, neu: str, von: object | None = None) -> Adresswechsel | None:
    """Notes the new address as waiting, in place of one before it. None for an account gone.

    Under the account's lock, as everything that ends the account's sign-ins
    takes it ('sign_out.py'): a request at the same moment as a new password
    comes before it and is taken back, or after it and stands. The old
    address is read under the lock too. Only someone else is noted as the
    author ('fremd').
    """
    fremd = von is not None and getattr(von, "pk", None) != konto.pk
    with transaction.atomic():
        gesperrt = CustomUser.objects.select_for_update(no_key=True).filter(pk=konto.pk).first()
        if gesperrt is None:
            return None
        Adresswechsel.zuruecknehmen(gesperrt)
        return Adresswechsel.objects.create(
            konto=gesperrt,
            neu=neu,
            alt=gesperrt.email,
            von_id=getattr(von, "pk", None) if fremd else None,
            fremd=fremd,
        )


def signiert(wechsel: Adresswechsel) -> str:
    """What the link carries: the number of the row, signed. Nothing of the addresses."""
    return signing.dumps(wechsel.pk, salt=SALZ)


def token(konto: CustomUser, neu: str, von: object | None = None) -> str:
    """The content of a link for a new address, the change noted as waiting ('vormerken')."""
    wechsel = vormerken(konto, neu, von)
    if wechsel is None:
        raise CustomUser.DoesNotExist
    return signiert(wechsel)


def gilt(wechsel: Adresswechsel, konto: CustomUser) -> bool:
    """Whether a waiting change still applies to the account as it stands.

    Within the time of the link, for an active account, and only while the
    account carries the address the request came from. A deactivated
    account loses its row anyway ('sign_out.ueberall_abmelden'). Asked here
    as well, for one switched off past that step.
    """
    frist = timezone.now() - timedelta(seconds=settings.PASSWORD_RESET_TIMEOUT)
    return wechsel.erstellt_am > frist and konto.is_active and konto.email == wechsel.alt


def wechsel_aus(token: str) -> Adresswechsel | None:
    """Reads a link. None for one that is forged, expired, replaced, taken back or used."""
    try:
        nummer = signing.loads(token, salt=SALZ, max_age=settings.PASSWORD_RESET_TIMEOUT)
    except signing.BadSignature:
        return None
    if type(nummer) is not int:
        return None
    wechsel = Adresswechsel.objects.select_related("konto").filter(pk=nummer).first()
    if wechsel is None or not gilt(wechsel, wechsel.konto):
        return None
    return wechsel


def urheber_von(wechsel: Adresswechsel) -> object | None:
    """Who entered the address, for the change mail. None for the person themselves."""
    if not wechsel.fremd:
        return None
    return CustomUser.objects.filter(pk=wechsel.von_id).first() or _Unbekannt()


def bestaetigungslink(wechsel: Adresswechsel, request) -> str:
    """The address from the request: the host the person works on, https behind the proxy."""
    return request.build_absolute_uri(
        reverse("email_bestaetigen", kwargs={"token": signiert(wechsel)})
    )


def bestaetigung_senden(
    konto: CustomUser, neu: str, request, von: object | None = None
) -> bool | None:
    """The mail with the link, to the new address only. Returns like 'notification.senden'.

    The change is noted first ('vormerken'), in place of one before it. The
    mail names neither the user name nor the old address: whoever typed a
    stranger's address by mistake would otherwise hand them both.
    """
    wechsel = vormerken(konto, neu, von)
    if wechsel is None:
        return None
    werte = {
        "link": bestaetigungslink(wechsel, request),
        "tage": gueltig_fuer_tage(),
    } | urheber(konto, von)
    return senden(konto, [neu], Mailart.EMAIL_BESTAETIGEN, werte)


def _schreiben(konto: CustomUser, neu: str) -> Kontostand:
    """Writes the address and takes a waiting one back. Returns the state from before.

    The state is for the change mail. A change still waiting would otherwise
    hold for the address of before, and an address that went back to it
    within the time of the link revived it.
    """
    vorher = Kontostand.aus_der_datenbank(konto)
    konto.email = neu
    konto.save(update_fields=["email"])
    Adresswechsel.zuruecknehmen(konto)
    return vorher


def sofort_setzen(konto: CustomUser, neu: str, von: object | None = None) -> bool | None:
    """Writes the address now and tells the old and the new one, as every change does."""
    return aenderung_melden(konto, _schreiben(konto, neu), von)


def einleiten(konto: CustomUser, neu: str, request, von: object | None = None) -> Ergebnis:
    """A new address from the person: at once where nothing can be confirmed, else the mail."""
    if sofort(konto):
        return Ergebnis(bestaetigung=False, gesendet=sofort_setzen(konto, neu, von))
    return Ergebnis(bestaetigung=True, gesendet=bestaetigung_senden(konto, neu, request, von))


def anwenden(wechsel: Adresswechsel) -> bool | None:
    """Writes the confirmed address and deletes the row. Under the account's lock, without key.

    Asked again under the lock, with the row read anew, what 'wechsel_aus'
    asked before: whether the row is still there (used, replaced or taken
    back meanwhile), whether it holds ('gilt'), and whether the new address
    is free. Each can have changed between reading the link and this write.
    Everything that takes a row back locks the account first or writes its
    row, so a confirmation and a new password at the same moment come one
    after the other. Afterwards the change mail goes to the old and the new
    address, naming who entered it, as if it had been written at once.

    The mail leaves after the commit, so call this outside
    'transaction.atomic()'. Sent under the lock, a mail server that did not
    answer held the account's row for its timeout, and every renewal and
    sign-in of the account waited with it (until 10/2026).
    """
    with transaction.atomic():
        konto = (
            CustomUser.objects.select_for_update(no_key=True).filter(pk=wechsel.konto_id).first()
        )
        offen = Adresswechsel.objects.filter(pk=wechsel.pk).first()
        if konto is None or offen is None or not gilt(offen, konto):
            raise LinkVerbraucht
        if adresse_vergeben(offen.neu, konto):
            raise AdresseVergeben
        vorher = _schreiben(konto, offen.neu)
    return aenderung_melden(konto, vorher, urheber_von(offen))
