"""Signs every account out, for a database loaded past the administration.

It is for a dump of 'bin/backup.sh' loaded with psql, and for a state with
accounts that 'bin/loaddata.sh' or 'bin/full-loaddata.sh' loads, which call it
at their end. Such a database holds the sessions of the website and the
refresh tokens of the counting app as they were at its moment. Loaded again,
they would apply again, revoked since or not: a device shut out by a new
password or a removed passkey would be back. After a restore everyone signs in
anew (decision of 2026-10-20). The administration's restore does that by
itself. This command does the same for the ways past it (DEPLOYMENT.md,
"Restoring").
"""

from django.core.management import BaseCommand
from django.utils.translation import gettext as _

from accounts.sign_out import alle_abmelden


class Command(BaseCommand):
    help = (
        "Meldet jeden ab: jede Sitzung der Website endet, und jedes noch gültige "
        "Erneuerungstoken der Zähl-App kommt auf die Sperrliste."
    )

    def handle(self, *args, **options):
        tokens, sitzungen = alle_abmelden()
        self.stdout.write(
            _(
                "Abgemeldet. Beendete Sitzungen der Website: %(sitzungen)s. Gesperrte "
                "Erneuerungstoken der Zähl-App: %(tokens)s."
            )
            % {"sitzungen": sitzungen, "tokens": tokens}
        )
