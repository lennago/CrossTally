"""The development scripts that load a state: 'bin/full-loaddata.sh' and 'bin/loaddata.sh'.

Run in a copy of the folder, with a stand-in for the interpreter that notes
each call and plays its part: 'backup read' prints the file it is given,
'loaddata' swallows what comes in. What the scripts ask of 'manage.py' is
the test, not what Django does with it.
"""

import shutil
import subprocess
import tempfile
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

#: The interpreter of the copy. It notes its arguments, one call per line.
STELLVERTRETER = """#!/bin/sh
printf '%s\\n' "$*" >> "$(dirname "$0")/../../aufrufe.txt"
if [ "$2" = backup ]; then
    for letztes in "$@"; do :; done
    cat "$letztes"
fi
if [ "$2" = loaddata ]; then
    cat > /dev/null
fi
"""

#: A state with an account in it, as 'bin/full-dumpdata.sh' writes it.
MIT_KONTO = b'[\n{\n  "model": "accounts.customuser",\n  "fields": {"username": "ada"}\n}\n]\n'

#: The master data alone, as 'bin/dumpdata.sh' writes them.
OHNE_KONTO = b'[\n{\n  "model": "katalog.ware",\n  "fields": {"name": "Ware N"}\n}\n]\n'


class Skript(SimpleTestCase):
    def ausfuehren(self, skript: str, *argumente: str, inhalt: bytes = MIT_KONTO) -> list[str]:
        """Runs the script on a state in its folder. Back come the calls of 'manage.py'."""
        ordner = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, ordner)
        (ordner / "bin").mkdir()
        shutil.copy(settings.BASE_DIR / "bin" / skript, ordner / "bin")
        python = ordner / ".venv" / "bin" / "python"
        python.parent.mkdir(parents=True)
        python.write_text(STELLVERTRETER, encoding="utf-8")
        python.chmod(0o755)
        unterordner = "data/backups/complete" if skript == "full-loaddata.sh" else "data/backups"
        stand = ordner / unterordner / "stand_2026-10-01_12-00-00.json"
        stand.parent.mkdir(parents=True)
        stand.write_bytes(inhalt)

        lauf = subprocess.run(
            ["bash", str(ordner / "bin" / skript), *argumente, str(stand)],
            capture_output=True,
            check=False,
        )

        self.assertEqual(lauf.returncode, 0, lauf.stderr.decode())
        aufrufe = (ordner / "aufrufe.txt").read_text(encoding="utf-8").splitlines()
        return [
            aufruf.removeprefix("manage.py ").replace(str(stand), "<stand>") for aufruf in aufrufe
        ]


class Unverschluesselt(Skript):
    """An unencrypted state is read only on the word of whoever calls ('--plaintext').

    'backup read' refuses one without it while a key is set: anyone who may
    write into the folder could have placed it. The scripts pass the word on.
    """

    def test_ohne_das_wort_liest_es_wie_jeder_stand(self):
        for skript in ("full-loaddata.sh", "loaddata.sh"):
            with self.subTest(skript=skript):
                self.assertEqual(self.ausfuehren(skript)[0], "backup read <stand>")

    def test_das_wort_geht_an_backup_read(self):
        for skript in ("full-loaddata.sh", "loaddata.sh"):
            with self.subTest(skript=skript):
                aufrufe = self.ausfuehren(skript, "--plaintext")
                self.assertEqual(aufrufe[0], "backup read --plaintext <stand>")
                self.assertIn("loaddata --ignorenonexistent --format=json -", aufrufe)


class Abmelden(Skript):
    """A state with accounts brings their passwords and passkeys back as they were at its moment.

    The sessions of the website and the refresh tokens of the counting app
    stay in the database while the scripts merge. Those a newer password had
    shut out would apply again. So everyone signs in anew afterwards, as after
    a restore in the administration and after a dump loaded with psql
    ('sign_out_everyone'). Until 10/2026 the scripts signed nobody out.
    """

    def test_ein_ganzer_stand_meldet_danach_jeden_ab(self):
        aufrufe = self.ausfuehren("full-loaddata.sh")

        self.assertEqual(aufrufe[-1], "sign_out_everyone")
        self.assertIn("loaddata --ignorenonexistent --format=json -", aufrufe[:-1])

    def test_die_stammdaten_mit_konten_ebenso(self):
        """'bin/loaddata.sh' takes any state as its argument, a whole one too."""
        aufrufe = self.ausfuehren("loaddata.sh", inhalt=MIT_KONTO)

        self.assertEqual(aufrufe[-1], "sign_out_everyone")

    def test_die_stammdaten_allein_lassen_jede_anmeldung_stehen(self):
        """No account in the state, so no password of yesterday."""
        aufrufe = self.ausfuehren("loaddata.sh", inhalt=OHNE_KONTO)

        self.assertNotIn("sign_out_everyone", aufrufe)
        self.assertEqual(aufrufe[-1], "loaddata --ignorenonexistent --format=json -")
