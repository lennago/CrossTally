"""Inviting a new account by e-mail: the link, its token and the mail.

With a mail server a new account is invited unless whoever creates it ticks
"Set the password yourself" ('CustomUserCreationForm'). The person gets a mail
with a link, sets the password there and is switched on with it. Until then
the account is inactive and carries no usable password. No usable password
and never signed in is the state "invited" ('CustomUser.ist_eingeladen'),
nothing else is stored for it. Switched off, the links sent so far die
('einladungen_entwerten').
"""

import logging
from smtplib import SMTPException

from django.conf import settings
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.core.mail import send_mail
from django.db import transaction
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from organisation.mail_texts import Mailart

from .models import CustomUser
from .notification import verfasst


class EinladungsToken(PasswordResetTokenGenerator):
    """Its own salt: a reset link must not open an invitation, nor the other way round.

    Everything else is Django's. The token carries the password hash and the
    last login, so it dies with the password set through it and with the
    first sign-in, and it lasts 'PASSWORD_RESET_TIMEOUT'.
    """

    key_salt = "accounts.invitation.EinladungsToken"


einladungs_token = EinladungsToken()


def gueltig_fuer_tage() -> int:
    """How long the link lasts, for the mail and the help text."""
    return max(1, settings.PASSWORD_RESET_TIMEOUT // 86400)


def einladungslink(benutzer: CustomUser, request) -> str:
    """The address from the request: the host the manager works on, https behind the proxy."""
    return request.build_absolute_uri(
        reverse(
            "einladung",
            kwargs={
                "uidb64": urlsafe_base64_encode(force_bytes(benutzer.pk)),
                "token": einladungs_token.make_token(benutzer),
            },
        )
    )


def einladungen_entwerten(konto: CustomUser) -> None:
    """Every invitation link sent to an account without a password dies, with a fresh unusable one.

    For an account that is switched off ('UserUpdateView', the
    administration). The link does not ask whether the account is active,
    so that one switched on by hand still finds its way in
    ('CustomUser.ist_eingeladen'). Its token carries the password field
    ('EinladungsToken'), and the field changes here. Until 10/2026 a manager
    who switched an invited account on and off again to block it left the
    old link alive for its three days, and it switched the account back on,
    with its role and its houses. The account still counts as invited, and
    a new invitation sends a link that works. An account with a password
    keeps it. Called under the account's lock, with the row as read under it.
    """
    if konto.has_usable_password():
        return
    konto.set_unusable_password()
    with transaction.atomic():
        konto.save(update_fields=["password"])


logger = logging.getLogger(__name__)


def einladung_senden(benutzer: CustomUser, request) -> bool:
    """The mail with the link, in the language of the account.

    The wording comes from the administration ('Mailtext'), the shipped text
    where nobody changed it. Whether it names who created the account is
    decided there too. The sender is the one from the settings.

    Returns whether the mail left. A mail server that does not answer is no
    reason for an error page: the account exists and is wanted, and the
    account's page offers to send the invitation again. Until 10/2026 the
    exception went through, and the second attempt said "name taken".
    """
    werte = {
        "link": einladungslink(benutzer, request),
        "app": request.build_absolute_uri(reverse("pwa")),
        "website": request.build_absolute_uri(reverse("login")),
        "wer": str(request.user),
        "tage": gueltig_fuer_tage(),
    }
    betreff, text = verfasst(benutzer, Mailart.EINLADUNG, werte)
    try:
        send_mail(betreff, text, None, [benutzer.email])
    except SMTPException, OSError:
        logger.exception("Invitation mail for account %s not sent", benutzer.pk)
        return False
    return True
