"""The forms that set a password. They write the password and nothing else of the account.

Django's password forms save the whole row of the account, as the request
read it when it began. Whatever was saved in between went back to the state
of before: a demotion, a deactivation, a new address. An account deleted in
between came back, with its number and its old password (until 10/2026).
Each form here writes only the fields it sets ('update_fields'). A row
deleted in between then raises 'CustomUser.NotUpdated' instead of coming
back.

Nothing else is written along. No step here signs anyone in, so the last
sign-in ('last_login') stays as it is, and the links of the invitation and
of "Forgot your password?" die with the new password anyway. So does a link
for a new address ('Adresswechsel'), which carries no password: its row is
taken back here.
"""

from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.forms import AdminPasswordChangeForm, PasswordChangeForm, SetPasswordForm
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpRequest
from django.views.decorators.debug import sensitive_variables

from .models import Adresswechsel, CustomUser
from .passkey import passwort_haelt


def passwort_speichern(konto: AbstractBaseUser, *weitere: str) -> AbstractBaseUser:
    """Writes the password of the account, and the fields named in 'weitere', nothing else.

    A new address still waiting for its confirmation goes with it
    ('Adresswechsel'). Whoever changes the password because someone else
    had it means that too: until 10/2026 a link to that someone's mailbox
    still applied for three days. Every new password passes here, the
    app's as well ('api.serializers.account.PasswortAendernSerializer').

    In a savepoint of its own: a row gone in between must not spoil an
    enclosing transaction. The write locks the account's row, so a
    confirmation of the address at the same moment waits for the commit.
    """
    with transaction.atomic():
        konto.save(update_fields=["password", *weitere])
        Adresswechsel.zuruecknehmen(konto)
    return konto


class PasswortAendernForm(PasswordChangeForm):
    """The own password, given against the old one, which counts like a sign-in.

    Django checks the old one with 'check_password()', past 'axes'. On the
    website and in the administration wrong ones went uncounted, as many as
    one liked, and the right one changed the password afterwards (until
    10/2026). Asked through 'authenticate()' ('passwort_haelt'), the fifth
    locks this door with the others, as in the app. The request has to be
    Django's: 'axes' notes the lock on the one it gets.
    """

    def __init__(self, user, *args, request: HttpRequest, **kwargs):
        super().__init__(user, *args, **kwargs)
        self.request = request

    @sensitive_variables("alt")
    def clean_old_password(self) -> str:
        alt = self.cleaned_data["old_password"]
        konto = self.user
        assert isinstance(konto, CustomUser)
        if not passwort_haelt(self.request, konto, alt):
            raise ValidationError(
                self.error_messages["password_incorrect"], code="password_incorrect"
            )
        return alt

    def save(self, commit=True):
        konto = super().save(commit=False)
        return passwort_speichern(konto) if commit else konto


class NeuesPasswortForm(SetPasswordForm):
    """The new password behind the link of "Forgot your password?"."""

    def save(self, commit=True):
        konto = super().save(commit=False)
        return passwort_speichern(konto) if commit else konto


class ErstesPasswortForm(SetPasswordForm):
    """The first password behind the link of the invitation. It switches the account on."""

    def save(self, commit=True):
        konto = super().save(commit=False)
        konto.is_active = True
        return passwort_speichern(konto, "is_active") if commit else konto


class PasswortSetzenForm(AdminPasswordChangeForm):
    """The administration sets the password of an account, or switches it off."""

    def save(self, commit=True):
        konto = super().save(commit=False)
        return passwort_speichern(konto) if commit else konto
