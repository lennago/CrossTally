"""Questions to 'request.user' that the type checker cannot answer.

For it the account is an 'AbstractBaseUser' or an 'AnonymousUser'. That our
user model inherits from 'AbstractUser' and thus knows 'is_superuser' it does
not know without the mypy plugin. Instead of pulling the model in here (the
project level and 'organisation' do not know 'accounts'), the one question
that matters stands in one place.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import UNUSABLE_PASSWORD_PREFIX
from django.db.models import Q, QuerySet


def ist_administrator(konto: object) -> bool:
    return bool(getattr(konto, "is_superuser", False))


def mit_passwort() -> Q:
    """The accounts with a password one can type: not switched off, not empty.

    Django counts an empty field as usable, but nobody signs in with it. The
    account's own 'has_usable_password' answers the same for one account.
    """
    return ~Q(password__startswith=UNUSABLE_PASSWORD_PREFIX) & ~Q(password="")


def _mit_rolle() -> QuerySet:
    return get_user_model().objects.filter(is_superuser=True, is_staff=True, is_active=True)


def administratoren() -> QuerySet:
    """The administrators who really get into the administration.

    The role alone is not enough: without 'is_staff' the middleware sends an
    account to the block page, the administration included
    ('StaffOnlyMiddleware'). Such an account counted as a second
    administrator until 10/2026, and the real last one could be taken away.
    Nor does an account count that cannot sign in, without a password and
    without a passkey. Until 10/2026 it counted, and the last one who could
    sign in lost his way in because of it.
    """
    konten = get_user_model().objects
    mit_passkey = konten.filter(passkeys__isnull=False).values("pk")
    return _mit_rolle().filter(mit_passwort() | Q(pk__in=mit_passkey))


def administratoren_sperren() -> None:
    """Holds the rows of every administrator until the transaction ends.

    Whether a step takes the last way into the administration is a question
    about all of them, and the answer has to hold until the step is written.
    Two steps side by side each counted the other's way in, and both went
    through (until 10/2026). Locked by number, as everywhere else. Taken by
    every step that can take an administrator's way in: removing a passkey,
    switching the password off ('nimmt_den_letzten_weg'), deleting,
    deactivating and demoting ('letzter_administrator_geht').
    """
    list(_mit_rolle().order_by("pk").select_for_update().values_list("pk", flat=True))


def bleibt_administrator(konto: object) -> bool:
    """Whether the values at hand keep the account among the administrators.

    Role, manager and active, as 'administratoren' counts them. A form writes
    its values into the account before it saves, so this asks the instance
    and not the database.
    """
    return all(getattr(konto, feld, False) for feld in ("is_superuser", "is_staff", "is_active"))


def ist_letzter_administrator(konto: object) -> bool:
    """Whether this is the only administrator who gets into the administration.

    Without one nobody could set a password, change the appearance or restore
    a backup, and the administration would be out of reach for good. So the
    last one is neither deactivated nor deleted nor demoted, on the site as in
    the administration (until 10/2026 all of it went through).

    Asked without a lock, for what a page offers and for the form's early
    message. The step that writes asks once more under the lock
    ('letzter_administrator_geht').
    """
    if not administratoren().filter(pk=getattr(konto, "pk", None)).exists():
        return False
    return not administratoren().exclude(pk=getattr(konto, "pk", None)).exists()


def nimmt_letzte_administratoren(konten: QuerySet) -> bool:
    """Whether deleting these accounts at once would leave no administrator.

    One at a time, 'ist_letzter_administrator' answers. Selected together in
    the administration, each of two administrators still sees the other, and
    both would go. Asked without a lock like that one. The step that writes
    asks under the lock ('letzter_administrator_geht').
    """
    gewaehlt = konten.values("pk")
    return (
        administratoren().filter(pk__in=gewaehlt).exists()
        and not administratoren().exclude(pk__in=gewaehlt).exists()
    )


def letzter_administrator_geht(konten: QuerySet) -> bool:
    """Whether taking these accounts leaves no administrator, asked in the step that writes.

    Taking is deleting, deactivating or demoting. Called inside the
    transaction that writes: where the accounts hold an administrator, every
    administrator is locked first ('administratoren_sperren') and the question
    is asked under the lock, so the answer holds until the step is written.
    Asked before and without the lock, two administrators who deleted,
    deactivated or demoted each other at the same moment each still saw the
    other one, and both went through (until 10/2026). Accounts without an
    administrator among them take nobody's way in and lock nothing.
    """
    if not administratoren().filter(pk__in=konten.values("pk")).exists():
        return False
    administratoren_sperren()
    return nimmt_letzte_administratoren(konten)
