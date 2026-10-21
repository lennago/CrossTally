"""Encrypts and decrypts backups with the key from the environment.

A backup holds the accounts with their password hashes and everything the
houses counted. It is copied, mailed and kept on other machines, and there
nobody watches over it any more. Hence every backup is encrypted, with
'BACKUP_KEY' from the '.env'.

**The format is OpenSSL's own** ('openssl enc'): first gzip, then AES-256 in
CBC mode, the key derived from the passphrase with PBKDF2. A format of our
own would mean that a backup can only be read with this code. This way one
line is enough, on any machine with OpenSSL, even if the application no
longer exists:

    openssl enc -d -aes-256-cbc -pbkdf2 -iter 600000 -md sha256 \\
        -pass env:BACKUP_KEY -in stand.json.gz.enc | gunzip

**The 'openssl' program, not a Python package.** The usual package brings its
own copy of OpenSSL in its wheel. The program comes from the system and with
it its license text, as with libpq (see 'pyproject.toml').

**Encrypted, not signed.** CBC knows no checksum of its own, 'openssl enc'
offers no mode that has one. A wrong key and a damaged file still show up:
almost always at the padding, otherwise at the checksum of gzip. Against
someone who may write into the backup folder this protects only in part. A
file of their own does not get in: an unencrypted one is refused while a key
is set ('backup.pruefe_verschluesselt'), and one made without the key fails
as a wrong key does. An older state of the folder they could still put in
place of a newer one.

**The key goes through the environment of the child process**, never through
its arguments. Those can be read by everyone on the machine ('ps').
"""

import contextlib
import gzip
import os
import shutil
import subprocess
import zlib
from typing import BinaryIO

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

#: What an encrypted file is called at the end, after its own extension.
ENDUNG = ".gz.enc"

#: How an encrypted file begins. 'openssl enc' writes this in front of the salt.
KENNUNG = b"Salted__"

#: Rounds of PBKDF2. The more, the longer guessing a key takes. 600,000 is
#: what OWASP names for PBKDF2 with SHA-256 (2023). Changing the number
#: makes every older backup unreadable for this code: it is not in the file.
RUNDEN = 600_000

#: Read and written in pieces, so a dump of any size fits through.
STUECK = 1024 * 1024

VERFAHREN = ("-aes-256-cbc", "-pbkdf2", "-iter", str(RUNDEN), "-md", "sha256")


class OhneSchluessel(ValidationError):
    """No key is set, so nothing can be encrypted or read."""


class NichtLesbar(ValidationError):
    """The key does not fit, or the file is damaged. Both look the same."""


def schluessel_gesetzt() -> bool:
    return bool(settings.BACKUP_KEY)


def ist_verschluesselt(name: str) -> bool:
    return name.endswith(ENDUNG)


def _openssl(*argumente: str, **strom) -> subprocess.Popen:
    if not schluessel_gesetzt():
        raise OhneSchluessel(_("Es ist kein Schlüssel gesetzt. BACKUP_KEY gehört in die .env."))
    if shutil.which("openssl") is None:
        raise NichtLesbar(
            _("Das Programm „openssl“ fehlt auf diesem Rechner. Ohne es geht kein Backup.")
        )
    return subprocess.Popen(
        ["openssl", "enc", *VERFAHREN, "-pass", "env:BACKUP_KEY", *argumente],
        env=os.environ | {"BACKUP_KEY": settings.BACKUP_KEY},
        stderr=subprocess.PIPE,
        **strom,
    )


def verschluesseln(quelle: BinaryIO, ziel: BinaryIO) -> None:
    """Packs 'quelle' and writes it encrypted to 'ziel'.

    'ziel' has to be a real file: the child process writes there itself. That
    way nobody has to read its output while feeding it, and neither waits for
    the other.
    """
    vorgang = _openssl("-e", "-salt", stdin=subprocess.PIPE, stdout=ziel)
    assert vorgang.stdin is not None and vorgang.stderr is not None
    try:
        with gzip.GzipFile(fileobj=vorgang.stdin, mode="wb") as gepackt:
            shutil.copyfileobj(quelle, gepackt, STUECK)
        vorgang.stdin.close()
    except BrokenPipeError:
        # The child has given up. Why is said by what it wrote. Closing still
        # tries to send the rest of the buffer and fails again, but it frees
        # the pipe. Left open, it failed later during cleanup, with a
        # traceback in the log of the backup.
        with contextlib.suppress(BrokenPipeError):
            vorgang.stdin.close()
    meldung = vorgang.stderr.read().decode(errors="replace").strip()
    if vorgang.wait() != 0:
        raise NichtLesbar(_("Das Verschlüsseln ist gescheitert: %(grund)s") % {"grund": meldung})


def entschluesseln(quelle: BinaryIO, ziel: BinaryIO) -> None:
    """Writes the content of the encrypted 'quelle' to 'ziel', unpacked.

    'quelle' has to be a real file, for the same reason as in 'verschluesseln'.
    **What lands in 'ziel' only counts if this returns.** A wrong key shows up
    at the end at the earliest, and a damaged file where the damage lies.
    Until then pieces have been written. Whoever passes the content on
    decrypts into a side file first.
    """
    vorgang = _openssl("-d", stdin=quelle, stdout=subprocess.PIPE)
    assert vorgang.stdout is not None and vorgang.stderr is not None
    gescheitert = False
    try:
        with gzip.GzipFile(fileobj=vorgang.stdout, mode="rb") as gepackt:
            shutil.copyfileobj(gepackt, ziel, STUECK)
    except OSError, EOFError, zlib.error:
        # What came out is not gzip: with the wrong key it is noise.
        gescheitert = True
    vorgang.stdout.close()
    vorgang.stderr.read()
    if vorgang.wait() != 0 or gescheitert:
        raise NichtLesbar(
            _("Der Schlüssel passt nicht zu dieser Datei, oder die Datei ist beschädigt.")
        )
