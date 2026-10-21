"""Encrypting the backups: what goes in comes out, and only with the key."""

import io
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock, skipUnless

from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, override_settings

from .encryption import (
    ENDUNG,
    KENNUNG,
    RUNDEN,
    NichtLesbar,
    OhneSchluessel,
    entschluesseln,
    ist_verschluesselt,
    verschluesseln,
)

SCHLUESSEL = "nur-fuer-den-test-und-lang-genug-dafuer"
ANDERER = "ein-anderer-schluessel-und-auch-lang-genug"

#: Longer than one piece, so reading in pieces is part of the test.
INHALT = b'{"model": "katalog.ware", "name": "Artikel C"}\n' * 40_000


@override_settings(BACKUP_KEY=SCHLUESSEL)
class ImTempOrdner(SimpleTestCase):
    def setUp(self):
        ordner = tempfile.TemporaryDirectory()
        self.addCleanup(ordner.cleanup)
        self.ordner = Path(ordner.name)

    def verschluesselt(self, inhalt: bytes = INHALT, name: str = "stand.json.gz.enc") -> Path:
        pfad = self.ordner / name
        with pfad.open("wb") as ziel:
            verschluesseln(io.BytesIO(inhalt), ziel)
        return pfad

    def entschluesselt(self, pfad: Path) -> bytes:
        ziel = io.BytesIO()
        with pfad.open("rb") as quelle:
            entschluesseln(quelle, ziel)
        return ziel.getvalue()


class HinUndZurueck(ImTempOrdner):
    def test_was_hineingeht_kommt_heraus(self):
        self.assertEqual(self.entschluesselt(self.verschluesselt()), INHALT)

    def test_auch_ein_leerer_inhalt(self):
        self.assertEqual(self.entschluesselt(self.verschluesselt(b"")), b"")

    def test_in_der_datei_steht_nichts_lesbares(self):
        roh = self.verschluesselt().read_bytes()

        self.assertTrue(roh.startswith(KENNUNG))
        self.assertNotIn(b"katalog", roh)
        # Packed first: forty thousand equal rows shrink to almost nothing.
        self.assertLess(len(roh), len(INHALT) // 100)

    def test_zweimal_dasselbe_ergibt_nicht_dieselbe_datei(self):
        """The salt is new every time. Otherwise equal states could be told from outside."""
        eins = self.verschluesselt(name="eins.json.gz.enc").read_bytes()
        zwei = self.verschluesselt(name="zwei.json.gz.enc").read_bytes()

        self.assertNotEqual(eins, zwei)

    def test_der_name_sagt_welche_art_datei_es_ist(self):
        self.assertTrue(ist_verschluesselt(f"stand.json{ENDUNG}"))
        self.assertTrue(ist_verschluesselt(f"crosstally-2026-01-31-0300.sql{ENDUNG}"))
        self.assertFalse(ist_verschluesselt("stand.json"))
        self.assertFalse(ist_verschluesselt("crosstally-2026-01-31-0300.sql.gz"))


@skipUnless(shutil.which("openssl") and shutil.which("gunzip"), "braucht openssl und gunzip")
class OhneDieAnwendung(ImTempOrdner):
    """A backup has to stay readable when the application is gone.

    That is the reason for OpenSSL's own format. The line is the one from
    DEPLOYMENT.md, and it stands here so that it stays true.
    """

    def test_openssl_allein_liest_den_stand(self):
        pfad = self.verschluesselt()

        entschluesselt = subprocess.run(
            [
                "openssl",
                "enc",
                "-d",
                "-aes-256-cbc",
                "-pbkdf2",
                "-iter",
                str(RUNDEN),
                "-md",
                "sha256",
                "-pass",
                "env:BACKUP_KEY",
                "-in",
                str(pfad),
            ],  # fmt: skip
            env={"BACKUP_KEY": SCHLUESSEL, "PATH": "/usr/bin:/bin:/usr/local/bin"},
            capture_output=True,
            check=True,
        )
        entpackt = subprocess.run(
            ["gunzip"], input=entschluesselt.stdout, capture_output=True, check=True
        )

        self.assertEqual(entpackt.stdout, INHALT)

    def test_die_runden_stehen_so_in_der_anleitung(self):
        """Whoever changes the number makes every older backup unreadable for the line."""
        wurzel = Path(__file__).resolve().parents[2]
        for datei in ("DEPLOYMENT.md", "DEPLOYMENT.en.md"):
            if not (wurzel / datei).exists():
                # In the image the repository root is not there.
                self.skipTest(f"{datei} liegt hier nicht")
            with self.subTest(datei=datei):
                self.assertIn(f"-iter {RUNDEN} ", (wurzel / datei).read_text(encoding="utf-8"))


class NichtZuLesen(ImTempOrdner):
    def test_der_falsche_schluessel(self):
        pfad = self.verschluesselt()

        with override_settings(BACKUP_KEY=ANDERER), self.assertRaises(NichtLesbar):
            self.entschluesselt(pfad)

    def test_eine_abgeschnittene_datei(self):
        pfad = self.verschluesselt()
        roh = pfad.read_bytes()
        pfad.write_bytes(roh[: len(roh) // 2])

        with self.assertRaises(NichtLesbar):
            self.entschluesselt(pfad)

    def test_ein_gekipptes_bit(self):
        """Encrypted is not signed. But gzip carries a checksum, and it shows."""
        pfad = self.verschluesselt()
        roh = pfad.read_bytes()
        for stelle in (20, len(roh) // 2, len(roh) - 1):
            with self.subTest(stelle=stelle):
                pfad.write_bytes(roh[:stelle] + bytes([roh[stelle] ^ 1]) + roh[stelle + 1 :])
                with self.assertRaises(NichtLesbar):
                    self.entschluesselt(pfad)

    def test_eine_datei_die_nie_verschluesselt_war(self):
        pfad = self.ordner / "stand.json.gz.enc"
        pfad.write_bytes(INHALT)

        with self.assertRaises(NichtLesbar):
            self.entschluesselt(pfad)

    @override_settings(BACKUP_KEY="")
    def test_ohne_schluessel_geht_beides_nicht(self):
        pfad = self.ordner / "stand.json.gz.enc"
        with self.assertRaises(OhneSchluessel), pfad.open("wb") as ziel:
            verschluesseln(io.BytesIO(INHALT), ziel)
        with self.assertRaises(OhneSchluessel), pfad.open("rb") as quelle:
            entschluesseln(quelle, io.BytesIO())

    def test_ohne_das_programm_gibt_es_eine_meldung(self):
        pfad = self.ordner / "stand.json.gz.enc"
        with (
            mock.patch.object(shutil, "which", return_value=None),
            self.assertRaisesMessage(NichtLesbar, "openssl"),
            pfad.open("wb") as ziel,
        ):
            verschluesseln(io.BytesIO(INHALT), ziel)


class OpensslVerweigert(ImTempOrdner):
    """A broken 'openssl' call fails the backup with its reason, not later at the restore."""

    def setUp(self):
        super().setUp()
        # An option it does not know, as when a new version drops one.
        verweigert = mock.patch("django_project.encryption.VERFAHREN", ("-aes-999-cbc",))
        verweigert.start()
        self.addCleanup(verweigert.stop)

    def test_mit_dem_grund_von_openssl(self):
        with self.assertRaisesMessage(NichtLesbar, "Das Verschlüsseln ist gescheitert"):
            self.verschluesselt()
        with self.assertRaisesMessage(NichtLesbar, "aes-999-cbc"):
            self.verschluesselt()

    def test_auch_wenn_es_mitten_im_lesen_aufgibt(self):
        """Random data does not fit into the pipe, and the child gives up while being fed."""
        nachlese = []
        with (
            mock.patch.object(sys, "unraisablehook", nachlese.append),
            self.assertRaisesMessage(NichtLesbar, "aes-999-cbc"),
        ):
            self.verschluesselt(os.urandom(1 << 20))
        # Until 10/2026 the pipe stayed open and failed once more during
        # cleanup, with a traceback in the log of the backup.
        self.assertEqual(nachlese, [])


class DerSchluesselBleibtVerborgen(ImTempOrdner):
    def test_er_steht_nicht_in_den_argumenten(self):
        """Those can be read by everyone on the machine. The environment of the process cannot."""
        with mock.patch.object(subprocess, "Popen", wraps=subprocess.Popen) as gestartet:
            self.verschluesselt()

        (argumente,), angaben = gestartet.call_args
        self.assertNotIn(SCHLUESSEL, " ".join(argumente))
        self.assertIn("env:BACKUP_KEY", argumente)
        self.assertEqual(angaben["env"]["BACKUP_KEY"], SCHLUESSEL)


class Strom:
    """Stands in for the standard input and output of a command: text outside, bytes inside."""

    def __init__(self, datei):
        self.buffer = datei

    def flush(self) -> None:
        self.buffer.flush()


class Befehl(ImTempOrdner):
    """'manage.py backup': for the backups that do not arise inside the application."""

    def ausfuehren(self, *argumente, eingabe: bytes = b"") -> bytes:
        """What the command writes to its standard output, fed with 'eingabe'."""
        hinein = self.ordner / "hinein"
        hinein.write_bytes(eingabe)
        with hinein.open("rb") as quelle, (self.ordner / "heraus").open("w+b") as ziel:
            with (
                mock.patch.object(sys, "stdin", Strom(quelle)),
                mock.patch.object(sys, "stdout", Strom(ziel)),
            ):
                call_command("backup", *argumente, stdout=io.StringIO(), stderr=io.StringIO())
            ziel.seek(0)
            return ziel.read()

    def test_durchgereicht_und_zurueck(self):
        verschluesselt = self.ausfuehren("encrypt", eingabe=INHALT)

        self.assertTrue(verschluesselt.startswith(KENNUNG))
        self.assertEqual(self.ausfuehren("decrypt", eingabe=verschluesselt), INHALT)

    def test_mit_dem_falschen_schluessel_kommt_nichts_heraus(self):
        """Not even a piece: behind the command stands 'psql' and executes what arrives."""
        verschluesselt = self.ausfuehren("encrypt", eingabe=INHALT)

        with override_settings(BACKUP_KEY=ANDERER), self.assertRaises(CommandError):
            self.ausfuehren("decrypt", eingabe=verschluesselt)
        self.assertEqual((self.ordner / "heraus").read_bytes(), b"")

    def test_eine_abgeschnittene_datei_gibt_nichts_weiter(self):
        verschluesselt = self.ausfuehren("encrypt", eingabe=INHALT)

        with self.assertRaises(CommandError):
            self.ausfuehren("decrypt", eingabe=verschluesselt[: len(verschluesselt) // 2])
        self.assertEqual((self.ordner / "heraus").read_bytes(), b"")

    @override_settings(BACKUP_KEY="")
    def test_verschluesseln_verlangt_den_schluessel(self):
        with self.assertRaisesMessage(CommandError, "BACKUP_KEY"):
            self.ausfuehren("encrypt", eingabe=INHALT)

    def abgelegt(self, **angaben) -> tuple[Path, str]:
        """The file 'store' wrote and what it said about it."""
        hinein = self.ordner / "hinein"
        hinein.write_bytes(INHALT)
        gesagt, gewarnt = io.StringIO(), io.StringIO()
        with hinein.open("rb") as quelle, mock.patch.object(sys, "stdin", Strom(quelle)):
            call_command(
                "backup", "store", str(self.ordner / "stand"), stdout=gesagt, stderr=gewarnt
            )
        return Path(gesagt.getvalue().strip()), gewarnt.getvalue()

    def test_ablegen_verschluesselt_und_nennt_die_datei(self):
        pfad, gewarnt = self.abgelegt()

        self.assertEqual(pfad.name, "stand.json.gz.enc")
        self.assertEqual(self.entschluesselt(pfad), INHALT)
        self.assertEqual(pfad.stat().st_mode & 0o077, 0)
        self.assertEqual(gewarnt, "")
        # The side file it wrote into first is gone.
        self.assertEqual([datei.name for datei in self.ordner.glob(".*")], [])

    @override_settings(BACKUP_KEY="")
    def test_ablegen_ohne_schluessel_schreibt_wie_bisher_und_sagt_es(self):
        pfad, gewarnt = self.abgelegt()

        self.assertEqual(pfad.name, "stand.json")
        self.assertEqual(pfad.read_bytes(), INHALT)
        self.assertEqual(pfad.stat().st_mode & 0o077, 0)
        self.assertIn("Unverschlüsselt geschrieben", gewarnt)

    def test_lesen_nimmt_beide_arten(self):
        """An unencrypted file only when the call says it is an own old one ('--plaintext')."""
        verschluesselt, _ = self.abgelegt()
        alt = self.ordner / "stand_von_frueher.json"
        alt.write_bytes(INHALT)

        self.assertEqual(self.ausfuehren("read", str(verschluesselt)), INHALT)
        self.assertEqual(self.ausfuehren("read", "--plaintext", str(alt)), INHALT)

    def test_mit_schluessel_liest_es_unverschluesseltes_nur_auf_ausdruecklichen_wunsch(self):
        """Anyone who may write into the folder could have placed it. Until 10/2026 it went through.

        Nothing comes out: behind the command stands 'loaddata', and it would load the accounts.
        """
        for name in ("stand_von_frueher.json", "abbild.sql"):
            with self.subTest(name=name):
                alt = self.ordner / name
                alt.write_bytes(INHALT)

                with self.assertRaisesMessage(CommandError, "--plaintext"):
                    self.ausfuehren("read", str(alt))
                self.assertEqual((self.ordner / "heraus").read_bytes(), b"")

    @override_settings(BACKUP_KEY="")
    def test_ohne_schluessel_liest_es_unverschluesseltes_wie_bisher(self):
        """During development without a key there is no other kind."""
        alt = self.ordner / "stand_von_frueher.json"
        alt.write_bytes(INHALT)

        self.assertEqual(self.ausfuehren("read", str(alt)), INHALT)

    def test_lesen_folgt_einem_verweis(self):
        """The development scripts leave a link to the most recent state."""
        verschluesselt, _ = self.abgelegt()
        verweis = self.ordner / "neuester.json.gz.enc"
        verweis.symlink_to(verschluesselt.name)

        self.assertEqual(self.ausfuehren("read", str(verweis)), INHALT)

    def test_eine_datei_die_es_nicht_gibt(self):
        with self.assertRaisesMessage(CommandError, "gibt es nicht"):
            self.ausfuehren("read", str(self.ordner / "fehlt.json"))
