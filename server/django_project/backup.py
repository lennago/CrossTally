"""Backups from within the interface: create and restore.

**The format is JSON** (`dumpdata`), not `pg_dump`. The reason is where this
runs: inside the application. It knows the database only through Django, has
no `pg_dump` in the image, and should also work on SQLite, where development
happens. The nightly backups of operation (`bin/backup.sh`) are unaffected.
They are the image of the database, this is the state of the data.

**Restoring replaces.** First the current state is saved, then the tables are
emptied and the chosen state loaded, both in one transaction, so that an error
halfway does not leave an empty database behind. Whoever triggers it is signed
out afterwards: the sessions live in the database too. The folder is cleaned
up only after loading, and never of the chosen state.

**Not during a count.** The counting apps keep rows and numbers of the server
in their storage. What is gone after the restore could no longer be matched,
and counted quantities would be lost. Development continues through the
console ('loaddata'), where the lock does not apply.

**Only one at a time.** A double click submitted the form twice. Both requests
saved, emptied and loaded side by side, and under SQLite the second failed
with "database is locked". The browser, however, shows the second one's
response. A file lock in the backup folder therefore rejects it, across all
processes of the server.

**Afterwards everyone is signed out.** The sessions go with the emptying.
Every refresh token of the counting app that still applied is read before
emptying and written back onto the blocklist, also one the state does not
know: a state holds no tokens. The access token runs out within the hour.
Until 10/2026 only the blocklist was kept, and yesterday's state let back in
whatever had been shut out since: a device after a new password, a removed
passkey, a deactivated account. Their passwords and passkeys are those of the
state again, the page says so before the restore.

**Every state is encrypted** as soon as a key is set, and in operation one
always is ('encryption.py'). The name says which kind a file is:
'….json.gz.enc' or, from before 09/2026 and during development without a
key, '….json'. Written is only the one the key allows. **Restored with a key
is only an encrypted one** ('pruefe_verschluesselt'). Anyone who may write
into the folder could have placed an unencrypted file, and its accounts and
password hashes would be loaded as they are (until 10/2026). A state of
one's own from before 09/2026 is encrypted with the key first, through
'manage.py backup store' (DEPLOYMENT.md, "Restoring"), and is offered like
any other afterwards. That way the administration takes no file anyone
could have written, and saying "this one is mine" stays with whoever has
the key. Without a key, during development, the unencrypted ones are the
only kind.
"""

import fcntl
import io
import os
import re
import secrets
import tempfile
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import CommandError, call_command
from django.core.serializers.base import DeserializationError
from django.db import DatabaseError, OperationalError, connection, transaction
from django.utils import formats, timezone
from django.utils.translation import gettext as _
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

from inventur.models import Inventur, Inventurstatus
from katalog.models import Gebinde

from .backup_upgrade import umschreiben
from .encryption import (
    ENDUNG,
    entschluesseln,
    ist_verschluesselt,
    schluessel_gesetzt,
    verschluesseln,
)

#: What belongs in the state. Not included: sessions and blocked tokens. They
#: only apply to the moment, and yesterday's state must not sign anyone back
#: in. 'contenttypes' and permissions are recreated by 'flush' itself.
#: For 'accounts' the account, its passkeys and the attempts at the decoy, not
#: the proxy onto the lockout's rows: it would show up as a warning in every
#: run, and serialized it never is. The passkeys hold public keys only. After
#: a restore under the same domain the devices sign in as before. The demo's
#: marker goes along: a state of the demo comes back with its banner, a real
#: one without (until 10/2026 every restore dropped it).
APPS = (
    "accounts.CustomUser",
    "accounts.Passkey",
    "accounts.Anmeldeversuch",
    "organisation",
    "katalog",
    "inventur",
    "demo.Demokennzeichen",
)

#: The same pattern as in 'bin/full-dumpdata.sh'. The files should not differ
#: by who wrote them.
ZEITFORMAT = "%Y-%m-%d_%H-%M-%S"
#: The second is not always enough as a name: on restore, the safety copy is
#: created right before loading, and the same name twice would mean it
#: overwrites the state it is meant to save. Hence the sequence number.
NAME = re.compile(r"^stand_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}(-\d+)?\.json(\.gz\.enc)?$")
LAENGE_ZEITPUNKT = len("2026-09-16_07-12-55")

#: This many states are kept. Older ones are removed by 'erstellen'.
BEHALTEN = 7

#: The SQLSTATE of "deadlock detected" in PostgreSQL.
DEADLOCK = "40P01"


@dataclass(frozen=True)
class Stand:
    """A backup as it lies in the directory."""

    pfad: Path

    @property
    def name(self) -> str:
        return self.pfad.name

    @property
    def stamm(self) -> str:
        """The name without its extensions, whichever kind of file it is."""
        return self.name.removesuffix(ENDUNG).removesuffix(".json")

    @property
    def verschluesselt(self) -> bool:
        return ist_verschluesselt(self.name)

    @property
    def einspielbar(self) -> bool:
        """Whether the administration restores it, so its list offers only what it takes.

        With a key set, only an encrypted state ('pruefe_verschluesselt').
        """
        return self.verschluesselt or not schluessel_gesetzt()

    @property
    def zeitpunkt(self) -> datetime:
        """From the name, not from the file: copying changes the date."""
        anfang = len("stand_")
        roh = datetime.strptime(self.name[anfang : anfang + LAENGE_ZEITPUNKT], ZEITFORMAT)
        return timezone.make_aware(roh) if timezone.is_naive(roh) else roh

    @property
    def laufnummer(self) -> int:
        """Which state of this second it is, for the order."""
        rest = self.stamm[len("stand_") + LAENGE_ZEITPUNKT :]
        return int(rest.lstrip("-")) if rest else 1

    def lesen(self) -> bytes:
        """The content as it was before encrypting."""
        if not self.verschluesselt:
            return self.pfad.read_bytes()
        inhalt = io.BytesIO()
        with self.pfad.open("rb") as datei:
            entschluesseln(datei, inhalt)
        return inhalt.getvalue()

    @property
    def groesse(self) -> str:
        """The size with the decimal separator of the chosen language."""
        megabyte = self.pfad.stat().st_size / 1024 / 1024
        return f"{formats.number_format(round(megabyte, 1), 1)} MB"


def verzeichnis() -> Path:
    ordner = Path(settings.BACKUP_DIR)
    ordner.mkdir(parents=True, exist_ok=True)
    return ordner


def staende() -> list[Stand]:
    """The existing states, newest first.

    Only what matches the name pattern is read. Whatever else lies in the folder
    (say, the dumps from `bin/backup.sh`) does not belong here and is not
    offered for restoring either.
    """
    return sorted(
        (Stand(pfad) for pfad in verzeichnis().glob("stand_*.json*") if NAME.match(pfad.name)),
        key=lambda stand: (stand.zeitpunkt, stand.laufnummer),
        reverse=True,
    )


def stand_holen(name: str) -> Stand | None:
    """A state by its name, or nothing.

    The name comes from an address. It is therefore compared against the list and
    not joined into a path: a `../` in the name would otherwise lead out of the
    directory.
    """
    return next((stand for stand in staende() if stand.name == name), None)


def freier_name(ordner: Path, zeitpunkt: str, endung: str = "") -> Path:
    """A name that is still free, with a sequence number if need be.

    Two backups in the same second are no edge case: restoring saves first and
    then loads, and whoever presses the button twice is fast enough too. A name
    counts as taken whichever kind of file carries it. Otherwise an encrypted
    and an old state of the same second would share their number.
    """

    def belegt(stamm: str) -> bool:
        return any((ordner / f"{stamm}.json{art}").exists() for art in ("", ENDUNG))

    stamm = f"stand_{zeitpunkt}"
    nummer = 2
    while belegt(stamm):
        stamm = f"stand_{zeitpunkt}-{nummer}"
        nummer += 1
    return ordner / f"{stamm}.json{endung}"


def schreiben(inhalt: bytes, pfad: Path) -> None:
    """Writes 'inhalt' to 'pfad', encrypted if the name says so.

    The file belongs to nobody else from the first byte on, not only after
    writing: it contains password hashes.
    """
    with open(os.open(pfad, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as datei:
        if ist_verschluesselt(pfad.name):
            verschluesseln(io.BytesIO(inhalt), datei)
        else:
            datei.write(inhalt)


def erstellen(behalten: int | None = BEHALTEN) -> Stand:
    """Write the current state away. With 'behalten' None nothing is cleaned up yet."""
    ordner = verzeichnis()
    endung = ENDUNG if schluessel_gesetzt() else ""
    ziel = freier_name(ordner, timezone.localtime().strftime(ZEITFORMAT), endung)

    # Into memory and from there straight into the encryption: the readable
    # state never lies on the disk. A state is a few megabytes.
    inhalt = io.StringIO()
    call_command(
        "dumpdata",
        *APPS,
        natural_foreign=True,
        natural_primary=True,
        indent=2,
        stdout=inhalt,
        verbosity=0,
    )

    # Write next to it first, then rename: an aborted dump must not remain as
    # a half state that someone restores later.
    # With the process and a random part in the name: two "Create backup" in
    # the same second get their own side file, as the target name is numbered.
    # The ending stays whole: whether a file is encrypted is read from it.
    kopf, _punkt, endung = ziel.name.partition(".")
    vorlaeufig = ordner / f".{kopf}-{os.getpid()}-{secrets.token_hex(3)}.{endung}"
    try:
        schreiben(inhalt.getvalue().encode(), vorlaeufig)
        vorlaeufig.replace(ziel)
    finally:
        vorlaeufig.unlink(missing_ok=True)

    if behalten is not None:
        try:
            with _allein():
                aufraeumen(behalten)
        except LaeuftSchon:
            # A restore is running and reads one of the states. It cleans up
            # itself at its end and spares that one. Until 10/2026 a backup
            # written meanwhile deleted it, should it be the oldest.
            pass
    return Stand(ziel)


def aufraeumen(behalten: int = BEHALTEN, schonen: Iterable[Path] = ()) -> None:
    """Removes the oldest states beyond 'behalten'. What 'schonen' names stays, however old."""
    geschont = set(schonen)
    for alt in staende()[behalten:]:
        if alt.pfad not in geschont:
            alt.pfad.unlink(missing_ok=True)


def laufende_zaehlungen() -> list[Inventur]:
    """The inventories whose count is running. As long as there are any, nothing is restored.

    With their types, because the page names each of them ('Inventur.__str__').
    """
    return list(
        Inventur.objects.filter(status=Inventurstatus.ZAEHLUNG)
        .select_related("haus")
        .prefetch_related(*Inventur.NAME_BRAUCHT)
    )


class LaeuftSchon(ValidationError):
    """A second restore while the first is still running."""


class Unverschluesselt(ValidationError):
    """An unencrypted state while a key is set. It is not restored here."""


def pruefe_verschluesselt(stand: Stand) -> None:
    """With a key set, only an encrypted state is restored. Without one there is no other kind.

    An unencrypted file in the folder is what someone who may write there
    could have placed, with accounts of their own in it. The way for an old
    state of one's own goes through the key, on the console: 'manage.py backup
    store' writes it encrypted, and then it stands here like any other.
    """
    if stand.einspielbar:
        return
    raise Unverschluesselt(
        _(
            "%(name)s ist nicht verschlüsselt. Solange ein Schlüssel gesetzt ist, spielt die "
            "Verwaltung nur verschlüsselte Stände ein, denn eine unverschlüsselte Datei kann "
            "jeder hingelegt haben, der in den Ordner schreiben darf. Einen eigenen alten "
            "Stand verschlüsselt „manage.py backup store“ auf dem Rechner, danach steht er "
            "hier zum Einspielen bereit (DEPLOYMENT.md, „Zurückspielen“)."
        )
        % {"name": stand.name}
    )


@contextmanager
def _allein() -> Iterator[None]:
    """Lets only one restore through at a time. A second one is rejected, not postponed.

    Waiting would mean restoring the same state right away once more, with a
    second safety copy before it that nobody needs.
    """
    with open(verzeichnis() / ".einspielen.lock", "w") as datei:
        try:
            fcntl.flock(datei, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise LaeuftSchon(_("Es wird gerade schon ein Stand eingespielt.")) from None
        try:
            yield
        finally:
            fcntl.flock(datei, fcntl.LOCK_UN)


@contextmanager
def _lesbar(stand: Stand) -> Iterator[Path]:
    """The state as a file 'loaddata' can read, in today's format.

    'loaddata' reads from a path and tells the format by its ending. An
    encrypted state and one from before a change of the format
    ('backup_upgrade') therefore go into a side file that belongs to nobody
    else and is gone afterwards.

    In the temporary folder of the process, not in the backup folder. That one
    lies on the machine itself (compose mounts it), and a worker killed halfway
    left the readable state there, password hashes included (until 10/2026).
    """
    inhalt = stand.lesen()
    lesbar = umschreiben(inhalt)
    if lesbar is inhalt and not stand.verschluesselt:
        yield stand.pfad
        return
    with tempfile.NamedTemporaryFile(prefix="einspielen_", suffix=".json", delete=False) as datei:
        daneben = Path(datei.name)
        datei.write(lesbar)
    try:
        yield daneben
    finally:
        daneben.unlink(missing_ok=True)


def einspielen(stand: Stand) -> Stand:
    """Empty everything and load the state. Returns the safety copy made before.

    The safety copy is created **before** emptying: whoever picks the wrong state
    should be able to go back. It does not count against the seven that are
    otherwise kept. It is the return ticket, not the seventh backup.

    Cleaned up is **after** loading, and the chosen state is spared. After every
    restore eight states lie there, and the oldest can be the one chosen next.
    Until 10/2026 the safety copy cleaned up first and deleted it: encrypted it
    was gone from the folder afterwards, unencrypted the restore failed on the
    missing file, and a state that failed to load was lost although the code
    of its time could still have read it. A restore that fails takes its
    safety copy back: the database is unchanged, so the folder is too.

    Decrypted and rewritten is **before** all that. A state the key does not
    fit, an unencrypted one while a key is set ('pruefe_verschluesselt'), or
    one the new format cannot take, is a message and nothing else: no safety
    copy, no emptied table.
    """
    pruefe_verschluesselt(stand)
    if laufende := laufende_zaehlungen():
        namen = ", ".join(str(inventur) for inventur in laufende)
        raise ValidationError(
            _(
                "Eingespielt wird nicht, solange gezählt wird. Die Zähl-Apps hielten sonst Zeilen, "
                "die es nicht mehr gibt. In der Zählung: %(namen)s."
            )
            % {"namen": namen}
        )
    with _allein(), _lesbar(stand) as datei:
        vorher = erstellen(behalten=None)
        try:
            _laden(datei, stand)
        except BaseException:
            # The database is unchanged, and the safety copy shows what it holds
            # anyway. Gone with it, a failed restore leaves the folder as it was.
            # Until 10/2026 each one left a copy, and the folder kept more than
            # the seven states the privacy text names.
            vorher.pfad.unlink(missing_ok=True)
            raise
        aufraeumen(BEHALTEN + 1, schonen=[stand.pfad])
    return vorher


def _laden(datei: Path, stand: Stand) -> None:
    """Empties the tables and loads the state, in one transaction.

    A deadlock that PostgreSQL ends the restore with is a message, not a
    server error: the transaction is rolled back whole, and the next try
    meets other moments. It is not retried here. Whoever restores sees what
    happened and decides.
    """
    tokens = _tokens_von_vorher()
    try:
        _leeren_und_laden(datei, stand, tokens)
    except (CommandError, OperationalError) as fehler:
        if not _ist_deadlock(fehler):
            raise
        raise ValidationError(
            _(
                "Der Stand %(name)s ließ sich gerade nicht einspielen, weil im selben Augenblick "
                "anderswo in die Datenbank geschrieben wurde. Die Datenbank ist unverändert. Ein "
                "zweiter Versuch genügt."
            )
            % {"name": stand.name}
        ) from fehler


def _ist_deadlock(fehler: BaseException) -> bool:
    """Whether PostgreSQL ended the transaction for a deadlock, wrapped as it may be.

    'flush' wraps what the database raised in a 'CommandError', Django the
    driver's error in its own 'OperationalError'. Only the driver's error at
    the end of the chain says which one it was: 'sqlstate' in psycopg,
    'pgcode' in psycopg2.
    """
    kette: BaseException | None = fehler
    while kette is not None:
        if DEADLOCK in (getattr(kette, "sqlstate", None), getattr(kette, "pgcode", None)):
            return True
        kette = kette.__cause__
    return False


def _konten_zuerst() -> None:
    """Locks the accounts' table before 'flush' takes all the others.

    'flush' empties every table with one 'TRUNCATE', in an order of its own:
    Django gathers the names in a set, so the order changes with every
    process. Renewing a token, signing in and signing out lock the account's
    row first and read tokens, sessions or keys after it ('accounts/sign_out.py').
    A 'TRUNCATE' that took a token table and then waited for the accounts met
    one of them halfway, each waited for the other, and PostgreSQL ended the
    restore with "deadlock detected" (until 10/2026). Locked first, the restore
    waits for the ones under way and stops the next ones at their first lock.
    SQLite knows no 'LOCK TABLE' and locks the whole file anyway.
    """
    if connection.vendor != "postgresql":
        return
    tabelle = connection.ops.quote_name(get_user_model()._meta.db_table)
    with connection.cursor() as cursor:
        cursor.execute(f"LOCK TABLE {tabelle} IN ACCESS EXCLUSIVE MODE")


def _leeren_und_laden(datei: Path, stand: Stand, tokens: list[dict]) -> None:
    """The transaction itself. What it raises arrives after its rollback."""
    with transaction.atomic():
        _konten_zuerst()
        call_command("flush", interactive=False, verbosity=0)
        # An older state still carries fields that no longer exist (the default
        # container until 09/2026). They are skipped instead of rejected,
        # otherwise every backup from before a change would be archive material.
        try:
            call_command("loaddata", str(datei), verbosity=0, ignorenonexistent=True)
        except (DatabaseError, DeserializationError, CommandError) as fehler:
            # A state from before a rename names models that no longer exist.
            # 'loaddata' skips them silently, and the rows pointing to them fail
            # at the database. The transaction rolls back, and the database
            # stays as it was.
            raise ValidationError(
                _(
                    "%(name)s lässt sich nicht einspielen: %(grund)s. Der Stand stammt "
                    "vermutlich von vor einer Umstellung des Datenmodells. Die "
                    "Datenbank ist unverändert."
                )
                % {"name": stand.name, "grund": fehler}
            ) from fehler
        _alle_gesperrt_zurueck(tokens)
        # An older state does not know the switch on the pack yet. Without this
        # the loose single unit would stay German in every language.
        Gebinde.einzelstuecke_nachtragen()
        # Who counted is not kept once an inventory is closed (2026-10-13).
        # A state from before still carries it.
        Inventur.zaehlende.through.objects.filter(
            inventur__status=Inventurstatus.ABGESCHLOSSEN
        ).delete()


def _tokens_von_vorher() -> list[dict]:
    """Every refresh token that still applies, with the account as a name, not as a number."""
    return list(
        OutstandingToken.objects.filter(expires_at__gt=timezone.now()).values(
            "jti", "token", "created_at", "expires_at", "user__username"
        )
    )


def _alle_gesperrt_zurueck(saetze: list[dict]) -> None:
    """Re-enter after emptying, every one on the blocklist. A vanished account stays empty."""
    namen = {satz["user__username"] for satz in saetze if satz["user__username"]}
    konten = {k.get_username(): k for k in get_user_model().objects.filter(username__in=namen)}
    tokens = OutstandingToken.objects.bulk_create(
        OutstandingToken(
            jti=satz["jti"],
            token=satz["token"],
            created_at=satz["created_at"],
            expires_at=satz["expires_at"],
            user=konten.get(satz["user__username"]),
        )
        for satz in saetze
    )
    BlacklistedToken.objects.bulk_create(BlacklistedToken(token=token) for token in tokens)
