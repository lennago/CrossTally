"""What operation leaves behind, cleared away once a day.

Every passkey challenge of a visitor opens a session (a '?sprache=' did too
until 10/2026, now a cookie remembers it). It expires after two minutes
('challenge_merken'), and the clean-up removes expired sessions. The decoy
under '/admin/' notes at most ten attempts per sender and hour, 'axes' every
failed login, every renewal of the app's tokens leaves a row on the
blacklist, and every "Forgot your password?" one for the throttle. Nothing
removed any of it until 10/2026. 'bin/backup.sh' runs this before the dump,
so the daily backup takes the clean-up along. The privacy text names the
30 days. A new address that nobody confirmed leaves its row
('Adresswechsel'), and it goes once its link has expired.

The log of the administration ('django_admin_log') names who changed which
record, and with it the name of that record, an account's person included. It
stayed for good, even after the account was deleted, until 10/2026. Now it
goes after a year (decision of 2026-10-19).
"""

from datetime import timedelta

from axes.models import AccessAttempt, AccessFailureLog, AccessLog
from django.conf import settings
from django.contrib.admin.models import LogEntry
from django.core.management import BaseCommand, call_command
from django.utils import timezone
from django.utils.translation import gettext as _

from accounts.models import Adresswechsel, Anmeldeversuch, Passwortanfrage

#: How long failed logins and attempts at the decoy are kept: enough to
#: notice a pattern, short enough for a retention the privacy text can name.
AUFBEWAHRUNG_TAGE = 30

#: How long the log of the administration is kept. A year, so a question about
#: the last season can still be answered.
PROTOKOLL_TAGE = 365


class Command(BaseCommand):
    help = (
        "Räumt abgelaufene Sitzungen, Token und Links für eine neue Adresse sowie "
        "Anmeldeversuche weg, die älter als 30 Tage sind, und das Protokoll der Verwaltung "
        "nach einem Jahr."
    )

    def handle(self, *args, **options):
        call_command("clearsessions", verbosity=0)
        call_command("flushexpiredtokens", verbosity=0)
        # As long as the link lasts ('email_change.gilt'): afterwards it opens nothing.
        Adresswechsel.objects.filter(
            erstellt_am__lt=timezone.now() - timedelta(seconds=settings.PASSWORD_RESET_TIMEOUT)
        ).delete()
        grenze = timezone.now() - timedelta(days=AUFBEWAHRUNG_TAGE)
        # Counted attempts older than the cool-off lock nobody any more.
        # 'axes' clears them only on the next failed attempt of any account,
        # and the list "who is locked out right now" showed them until then.
        entfernt, _einzeln = AccessAttempt.objects.filter(
            attempt_time__lt=timezone.now() - settings.AXES_COOLOFF_TIME
        ).delete()
        for modell, feld in (
            (Anmeldeversuch, "timestamp"),
            (AccessAttempt, "attempt_time"),
            (AccessFailureLog, "attempt_time"),
            (AccessLog, "attempt_time"),
            (Passwortanfrage, "zeitpunkt"),
        ):
            anzahl, _einzeln = modell.objects.filter(**{f"{feld}__lt": grenze}).delete()
            entfernt += anzahl
        protokoll, _einzeln = LogEntry.objects.filter(
            action_time__lt=timezone.now() - timedelta(days=PROTOKOLL_TAGE)
        ).delete()
        self.stdout.write(
            _(
                "Abgelaufene Sitzungen, Token und Links für eine neue Adresse bereinigt, "
                "%(anzahl)s alte Anmeldeversuche und %(protokoll)s Einträge der Verwaltung von "
                "vor über einem Jahr entfernt."
            )
            % {"anzahl": entfernt, "protokoll": protokoll}
        )
