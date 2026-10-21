"""Telling a person by mail what happened to their account.

Two kinds of mail. A **change by someone**, on the site's user form or in the
administration: what changed, from what to what, and who did it. Deleting and
deactivating count as changes. And a **security notice** about what usually
only the person does: a new password, a passkey added or removed. Whoever did
not do it learns about it this way.

The wording is not here. It comes from the administration ('Mailtext'), the
shipped text where nobody changed it. This module only knows what happened
and fills the placeholders: 'aenderungen', 'wer', 'selbst', 'wo', 'passkey'.

Without a mail server nothing goes out and nobody is told ('mail_moeglich').
A server that does not answer stops nothing: the change stands, the failure
is logged, and whoever changed another account gets a warning on the next
page. The mail speaks the language of the account, like the invitation.

A deactivated account hears of its deactivation and nothing more until it is
switched on again (decision of 2026-10-14). Whoever is gone for a while
should not get mail about every edit in between, and whoever was let go gets
none at all after the one that says so. The invitation and "Forgot your
password?" do not pass through here: they have their own rules about who
gets them.

Every function returns whether the mail left: True, False for a server that
did not answer, None when there was nothing to tell or nobody to tell it to.
"""

import logging
from dataclasses import dataclass
from smtplib import SMTPException

from django.contrib import messages
from django.core.mail import send_mail
from django.http import HttpRequest
from django.utils import translation
from django.utils.translation import gettext_lazy as _
from django.utils.translation import pgettext_lazy

from django_project.mail import mail_moeglich
from organisation.mail_texts import Mailart
from organisation.models import Erscheinungsbild, Mailtext

from .models import CustomUser

logger = logging.getLogger(__name__)

#: What a change mail compares, in the order it lists it. Whether the account
#: is active is not listed: the subject and the first sentence say it.
FELDER = {
    "username": _("Benutzername"),
    "first_name": _("Vorname"),
    "last_name": _("Nachname"),
    "email": _("E-Mail-Adresse"),
    "rolle": _("Rolle"),
    "haeuser": _("Häuser"),
}

ROLLEN = {
    "administrator": _("Administrator"),
    "fuehrungskraft": _("Führungskraft"),
    "zaehler": pgettext_lazy("Rolle", "Zähler"),
}

#: Where the person changed their own password, for the placeholder 'wo'.
ORTE = {
    "website": _("auf der Website"),
    "app": _("in der Zähl-App"),
    "vergessen": _("über „Passwort vergessen?“"),
}

#: Shown to whoever made a change when the mail about it did not leave.
NICHT_GESENDET = _(
    "Die Benachrichtigung an %(email)s ging nicht hinaus, der E-Mail-Server antwortet nicht."
)

#: The one mail a deactivated account still gets. The one for switching it on
#: again finds it active already.
AN_DEAKTIVIERTE = (Mailart.KONTO_DEAKTIVIERT,)


@dataclass(frozen=True)
class Kontostand:
    """An account as a change mail compares it: the raw values, not yet in words.

    In words only inside the language of the account. Taken before a change
    from the database, not from the instance at hand: a form writes its values
    into the instance before it saves, and the old ones would be gone.
    """

    username: str
    first_name: str
    last_name: str
    email: str
    rolle: str
    haeuser: tuple[str, ...]
    is_active: bool

    @classmethod
    def aus_der_datenbank(cls, konto: CustomUser) -> Kontostand:
        frisch = CustomUser.objects.get(pk=konto.pk)
        if frisch.is_superuser:
            rolle = "administrator"
        else:
            rolle = "fuehrungskraft" if frisch.is_staff else "zaehler"
        return cls(
            username=frisch.username,
            first_name=frisch.first_name,
            last_name=frisch.last_name,
            email=frisch.email,
            rolle=rolle,
            haeuser=tuple(sorted(frisch.haeuser.values_list("name", flat=True))),
            is_active=frisch.is_active,
        )

    def angezeigt(self, feld: str) -> str:
        wert = getattr(self, feld)
        if feld == "rolle":
            return str(ROLLEN[wert])
        if feld == "haeuser":
            return ", ".join(wert) or "–"
        return wert or "–"


def selbst(von: object, konto: CustomUser) -> bool:
    """Whether the account did it itself.

    By number, because whoever renames themselves is still themselves. A
    deleted account has no number any more, then the name decides.
    """
    if von is None:
        return True
    if konto.pk is not None:
        return getattr(von, "pk", None) == konto.pk
    return getattr(von, "username", None) == konto.username


def urheber(konto: CustomUser, von: object) -> dict[str, object]:
    """'selbst' and 'wer' for the text. Nobody is named for their own doing.

    Whether the name stands in the mail at all is decided per mail in the
    administration ('Mailtext.nennt_namen'). Here it is always handed over.
    """
    if selbst(von, konto):
        return {"selbst": True, "wer": ""}
    return {"selbst": False, "wer": str(von)}


def verfasst(konto: CustomUser, art: str, werte: dict[str, object]) -> tuple[str, str]:
    """Subject and text of a mail to this account, in its language.

    For every mail to a person: the notices here, the invitation and the
    link of "Forgot your password?". The placeholders every mail knows are
    filled here, the caller adds those of its mail. Rendered inside the
    account's language, so a lazy text among the values speaks it too.
    """
    grundwerte = {
        "vorname": konto.first_name,
        "nachname": konto.last_name,
        "benutzername": konto.username,
        "anwendung": Erscheinungsbild.aktuell().titel,
    }
    with translation.override(konto.sprache):
        return Mailtext.fuer(art).ausfuellen(konto.sprache, grundwerte | werte)


def senden(konto: CustomUser, an: list[str], art: str, werte: dict[str, object]) -> bool | None:
    """Writes the mail and sends it, to each address once.

    Not to a deactivated account, except to say that it is deactivated. The
    instance says whether it is: whoever deactivates and renames in one save
    passes it after the save, and the one mail carries both.
    """
    if not konto.is_active and art not in AN_DEAKTIVIERTE:
        return None
    eindeutig: dict[str, str] = {}
    for adresse in an:
        if adresse:
            eindeutig.setdefault(adresse.lower(), adresse)
    if not eindeutig or not mail_moeglich():
        return None
    betreff, text = verfasst(konto, art, werte)
    try:
        send_mail(betreff, text, None, list(eindeutig.values()))
    except SMTPException, OSError:
        # The account, not the addresses: the log is read by the operator,
        # and it keeps its lines for weeks.
        logger.exception("Notification for account %s not sent", konto.pk)
        return False
    return True


def aenderung_melden(konto: CustomUser, vorher: Kontostand, von: object) -> bool | None:
    """After a change of name, address, role, sites or activity.

    A new address learns that it is the new one, the old one learns that it
    is no longer: whoever took over an account that way would otherwise go
    unnoticed. Switched off or on, it is the mail for that, with the other
    changes of the same save in it.
    """
    nachher = Kontostand.aus_der_datenbank(konto)
    # Deactivated before and after: the person heard of the deactivation and
    # hears nothing more until the account is switched on again.
    if not vorher.is_active and not nachher.is_active:
        return None
    geaendert = [feld for feld in FELDER if getattr(vorher, feld) != getattr(nachher, feld)]
    if not geaendert and vorher.is_active == nachher.is_active:
        return None
    if vorher.is_active == nachher.is_active:
        art = Mailart.KONTO_GEAENDERT
    else:
        art = Mailart.KONTO_AKTIVIERT if nachher.is_active else Mailart.KONTO_DEAKTIVIERT
    with translation.override(konto.sprache):
        aenderungen = "\n".join(
            f"{FELDER[feld]}: {vorher.angezeigt(feld)} → {nachher.angezeigt(feld)}"
            for feld in geaendert
        )
    return senden(
        konto,
        [vorher.email, nachher.email],
        art,
        {"aenderungen": aenderungen} | urheber(konto, von),
    )


def loeschung_melden(konto: CustomUser, von: object) -> bool | None:
    """After deleting. The instance still knows name, address and language."""
    return senden(konto, [konto.email], Mailart.KONTO_GELOESCHT, urheber(konto, von))


def passwort_melden(konto: CustomUser, wo: str, von: object = None) -> bool | None:
    """After a new password: 'website', 'app', 'vergessen' or 'verwaltung'.

    Only the administration sets the password of another account. Everywhere
    else it is the person themselves, or someone who knows the old password.
    The administration can also switch signing in with a password off
    entirely, and that is a mail of its own.
    """
    if wo == "verwaltung":
        art = (
            Mailart.PASSWORT_GESETZT
            if konto.has_usable_password()
            else Mailart.PASSWORT_ABGESCHALTET
        )
        return senden(konto, [konto.email], art, urheber(konto, von))
    with translation.override(konto.sprache):
        ort = str(ORTE[wo])
    return senden(konto, [konto.email], Mailart.PASSWORT_GEAENDERT, {"wo": ort})


def passkey_melden(
    konto: CustomUser, passkey: str, angelegt: bool, von: object = None
) -> bool | None:
    """After a passkey was added or removed. Only removing can be someone else's doing."""
    if angelegt:
        return senden(konto, [konto.email], Mailart.PASSKEY_ANGELEGT, {"passkey": passkey})
    return senden(
        konto,
        [konto.email],
        Mailart.PASSKEY_ENTFERNT,
        {"passkey": passkey} | urheber(konto, von),
    )


def warnen_wenn_nicht_gesendet(request: HttpRequest, konto: CustomUser, gesendet: bool | None):
    """Tells whoever changed another account that the mail about it did not leave."""
    if gesendet is False:
        messages.warning(request, NICHT_GESENDET % {"email": konto.email})
