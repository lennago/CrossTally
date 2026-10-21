"""The command 'manage.py backup': encrypts and decrypts what flows through it.

For the backups that do not arise inside the application. The image of the
database is written by 'pg_dump' in the container of the database, and there
is nothing there to encrypt with. So it flows through here
('bin/backup.sh'), and the states of the development scripts likewise.

    pg_dump … | manage.py backup encrypt > abbild.sql.gz.enc
    manage.py backup decrypt < abbild.sql.gz.enc | psql …
    manage.py dumpdata … | manage.py backup store data/backups/stand
    manage.py backup read data/backups/stand.json.gz.enc | manage.py loaddata --format=json -

'encrypt' and 'decrypt' insist on the key. 'store' and 'read' follow what
is there: without a key 'store' writes as before and says so, and 'read'
passes an unencrypted file through. **With a key 'read' passes one only on
'--plaintext'**, the word of whoever runs it that the file is an old one of
their own. Anyone who may write into the folder could have placed it, and behind
'read' stands 'loaddata', which would load its accounts as they are (until
10/2026). The administration takes no such file at all ('backup.py'). A
state from before a change of the format comes out in today's
('backup_upgrade'), as when restoring in the administration.

The command lies in 'organisation' because the project package is no app
and carries no commands. The actions have English names, as with every
Django command.
"""

import io
import os
import shutil
import sys
import tempfile
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.utils.translation import gettext as _

from django_project.backup_upgrade import umschreiben
from django_project.encryption import (
    ENDUNG,
    STUECK,
    entschluesseln,
    ist_verschluesselt,
    schluessel_gesetzt,
    verschluesseln,
)


class Command(BaseCommand):
    help = (
        "Verschlüsselt und entschlüsselt Backups mit dem Schlüssel aus BACKUP_KEY. "
        "Gelesen wird von der Standardeingabe, geschrieben auf die Standardausgabe."
    )
    #: Runs while the database is away too: that is exactly when an image is
    #: played back, and nothing here asks the database anything.
    requires_system_checks = []

    def add_arguments(self, parser) -> None:
        aktionen = parser.add_subparsers(dest="aktion", required=True)
        aktionen.add_parser(
            "encrypt", help="Packt und verschlüsselt, was hereinkommt. Verlangt den Schlüssel."
        )
        aktionen.add_parser(
            "decrypt", help="Entschlüsselt und entpackt, was hereinkommt. Verlangt den Schlüssel."
        )
        ablegen = aktionen.add_parser(
            "store",
            help=(
                "Schreibt, was hereinkommt, in eine Datei. Mit Schlüssel verschlüsselt, "
                "ohne wie bisher. Gibt den Namen der Datei aus."
            ),
        )
        ablegen.add_argument("path", help="Die Datei ohne Endung, etwa data/backups/stand.")
        ablegen.add_argument(
            "--suffix", default=".json", help="Die Endung des Inhalts (Vorgabe .json)."
        )
        lesen = aktionen.add_parser(
            "read", help="Gibt eine Datei aus, entschlüsselt, wenn sie verschlüsselt ist."
        )
        lesen.add_argument("path", help="Die Datei, wie sie daliegt.")
        lesen.add_argument(
            "--plaintext",
            action="store_true",
            help=(
                "Gibt auch eine unverschlüsselte Datei aus, obwohl ein Schlüssel gesetzt ist. "
                "Nur für einen eigenen alten Stand."
            ),
        )

    def handle(self, *args, **optionen) -> None:
        try:
            getattr(self, f"_{optionen['aktion']}")(optionen)
        except ValidationError as fehler:
            raise CommandError(" ".join(fehler.messages)) from fehler

    def _encrypt(self, optionen) -> None:
        sys.stdout.flush()
        verschluesseln(sys.stdin.buffer, sys.stdout.buffer)

    def _decrypt(self, optionen) -> None:
        self._entschluesselt_ausgeben(sys.stdin.buffer)

    def _store(self, optionen) -> None:
        verschluesselt = schluessel_gesetzt()
        ziel = Path(f"{optionen['path']}{optionen['suffix']}{ENDUNG if verschluesselt else ''}")
        ziel.parent.mkdir(parents=True, exist_ok=True)
        # Next to it first, then renamed: a dump that breaks off must not
        # remain as a state someone restores later.
        daneben = ziel.with_name(f".{ziel.name}")
        try:
            # Nobody else's from the first byte on: it contains password hashes.
            zugriff = os.open(daneben, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with open(zugriff, "wb") as datei:
                if verschluesselt:
                    verschluesseln(sys.stdin.buffer, datei)
                else:
                    shutil.copyfileobj(sys.stdin.buffer, datei, STUECK)
            daneben.replace(ziel)
        finally:
            daneben.unlink(missing_ok=True)
        if not verschluesselt:
            self.stderr.write(
                _(
                    "Unverschlüsselt geschrieben, denn es ist kein Schlüssel gesetzt. "
                    "Mit BACKUP_KEY in der .env wird verschlüsselt."
                )
            )
        self.stdout.write(str(ziel))

    def _read(self, optionen) -> None:
        pfad = Path(optionen["path"])
        if not pfad.is_file():
            raise CommandError(_("Diese Datei gibt es nicht: %(pfad)s") % {"pfad": pfad})
        if schluessel_gesetzt() and not ist_verschluesselt(pfad.name) and not optionen["plaintext"]:
            raise CommandError(
                _(
                    "%(pfad)s ist nicht verschlüsselt, obwohl ein Schlüssel gesetzt ist. So eine "
                    "Datei kann jeder hingelegt haben, der in den Ordner schreiben darf. Ist es "
                    "dein eigener alter Stand, dann mit --plaintext."
                )
                % {"pfad": pfad}
            )
        with pfad.open("rb") as datei:
            if ".json" in pfad.name:
                self._stand_ausgeben(datei, ist_verschluesselt(pfad.name))
            elif ist_verschluesselt(pfad.name):
                self._entschluesselt_ausgeben(datei)
            else:
                shutil.copyfileobj(datei, sys.stdout.buffer, STUECK)

    def _stand_ausgeben(self, datei, verschluesselt: bool) -> None:
        """A state as a whole, in today's format: it can only be rewritten complete.

        A few megabytes, so in memory. Nothing goes out before the key has
        proven to fit.
        """
        if verschluesselt:
            puffer = io.BytesIO()
            entschluesseln(datei, puffer)
            inhalt = puffer.getvalue()
        else:
            inhalt = datei.read()
        sys.stdout.buffer.write(umschreiben(inhalt))
        sys.stdout.buffer.flush()

    def _entschluesselt_ausgeben(self, quelle) -> None:
        """Into a side file first, and out only once it is whole.

        Whether the key fits shows at the end at the earliest. Passed on
        piece by piece, half an image would already be in the database by
        then.
        """
        with tempfile.TemporaryFile() as daneben:
            entschluesseln(quelle, daneben)
            daneben.seek(0)
            shutil.copyfileobj(daneben, sys.stdout.buffer, STUECK)
        sys.stdout.buffer.flush()
