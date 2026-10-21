"""What happens when a wrong password comes too often.

Counting and locking is done by 'axes'. Here stands only **what** the
rejection looks like. It has to be two things, because two very different
callers turn to the same login:

- the **web interface** gets a page in the application's clothes, like the
  block page of the counters,
- the **counting app** gets JSON. It reads every response with 'JSON.parse'.
  That breaks on a line of text, and its error path would then take the lock
  for a fault of the line and resubmit the job again and again. Axes answers
  with JSON of its own accord only on 'X-Requested-With: XMLHttpRequest', and
  that is exactly the header the app does not set.

The distinction goes by path, not by the Accept header: the app sends its
login to '/api/', and that is the more reliable information about who is
asking.

**Which name is counted** is up to 'benutzername_fuer_axes': the one typed,
cut to the length of an account's name.
"""

from axes.models import AccessAttempt, AccessFailureLog, AccessLog
from django.conf import settings
from django.db import models
from django.http import JsonResponse
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _

from .models import Anmeldeversuch, CustomUser

_BENUTZERNAME = CustomUser._meta.get_field(CustomUser.USERNAME_FIELD)
assert isinstance(_BENUTZERNAME, models.CharField) and _BENUTZERNAME.max_length

#: No account's name is longer. What comes in longer is cut before 'axes' and
#: the decoy store it: their columns hold 255 characters, and PostgreSQL
#: refuses a longer value instead of cutting it.
NAMENSLAENGE: int = _BENUTZERNAME.max_length


def gekappt(name: str | None) -> str | None:
    """A typed user name as it is stored with a failed attempt."""
    return name[:NAMENSLAENGE] if isinstance(name, str) else name


def benutzername_fuer_axes(request, credentials=None) -> str | None:
    """The name 'axes' counts and locks under ('AXES_USERNAME_CALLABLE').

    The same field the package itself would read, from the credentials of
    'authenticate()' or else from the form, only cut. Until 10/2026 a name
    of more than 255 characters ended in a server error at the login, at the
    token endpoint of the app and at the decoy under PostgreSQL. The row of
    the failed attempt did not fit, and the attempt counted nowhere. No
    account has such a name. Cut, it is refused like any other wrong name,
    and the attempt counts.
    """
    feld = str(settings.AXES_USERNAME_FORM_FIELD)
    if credentials:
        return gekappt(credentials.get(feld))
    return gekappt(getattr(request, "data", request.POST).get(feld))


#: What stands in both cases, once as a page, once as a field.
MELDUNG = _(
    "Zu viele Fehlversuche. Dieses Konto ist von hier aus vorübergehend "
    "gesperrt. Bitte später noch einmal versuchen."
)


def gesperrt(request, original_response=None, credentials=None):
    """The rejection after too many failed attempts.

    Does **not** give away whether the account exists at all: locked is the
    combination of user name and address, and the message is the same for an
    invented name as for a real one.
    """
    if request.path_info.startswith("/api/"):
        return JsonResponse({"detail": MELDUNG, "gesperrt": True}, status=429)
    return render(
        request,
        "accounts/locked_out.html",
        # Minutes, not the raw 'timedelta': that rendered as "0:30:00" (until 10/2026).
        {"meldung": MELDUNG, "sperrminuten": int(settings.AXES_COOLOFF_TIME.total_seconds() // 60)},
        status=429,
    )


def spuren_loeschen(benutzername: str) -> None:
    """What the lock-out kept about this name goes with the account.

    'axes' stores the user name as text, not as a key, so deleting the
    account left failed logins, the access log and the attempts at the decoy
    standing with name and address (until 10/2026). The counting data knows
    no account and stays.
    """
    for modell in (AccessAttempt, AccessFailureLog, AccessLog, Anmeldeversuch):
        modell.objects.filter(username=benutzername).delete()
