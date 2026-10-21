"""The administration: who gets in, what is there, and the backups."""

import fcntl
import io
import json
import re
import shutil
import subprocess
import tempfile
from datetime import date
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.core import mail
from django.core.exceptions import ValidationError
from django.core.management import CommandError, call_command
from django.db import DataError, OperationalError
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import CustomUser, Passkey, Sitzung
from demo.state import ist_demobestand, kennzeichnen
from django_project.testing import inventur_anlegen
from inventur.models import Inventur
from katalog.models import Behaelter
from organisation.models import Erscheinungsbild, Haus

from . import backup
from .encryption import ENDUNG, KENNUNG, NichtLesbar
from .testing import skript, stylesheet

User = CustomUser


#: In operation a key is always set. The tests of the backups run with one
#: too, so they go the way operation goes.
SCHLUESSEL = "nur-fuer-den-test-und-lang-genug-dafuer"


def benutzer(**abweichend):
    vorgaben = {
        "username": "ada",
        "email": "ada@example.org",
        "password": "geheim-genug-2026",
        "first_name": "Ada",
        "last_name": "Admin",
    }
    return User.objects.create_user(**(vorgaben | abweichend))


class Deadlock(Exception):
    """What the driver raises for a deadlock, as far as the restore looks at it."""

    sqlstate = "40P01"


def leeren_im_deadlock(name, *args, **kwargs):
    """'flush' ended by PostgreSQL: the driver's error, wrapped by Django and by the command."""
    if name != "flush":
        return call_command(name, *args, **kwargs)
    fehler = OperationalError("deadlock detected")
    fehler.__cause__ = Deadlock("deadlock detected")
    befehl = CommandError("Database couldn't be flushed.")
    befehl.__cause__ = fehler
    raise befehl


class ImBackupOrdner(TestCase):
    """Backups land in the test folder, not next to the real states.

    Also for tests that only open the start page: it reads the folder, and what
    lies there should be decided by the test.
    """

    def setUp(self):
        self.ordner = tempfile.TemporaryDirectory()
        self.addCleanup(self.ordner.cleanup)
        umgebung = override_settings(BACKUP_DIR=self.ordner.name, BACKUP_KEY=SCHLUESSEL)
        umgebung.enable()
        self.addCleanup(umgebung.disable)


class Zugang(ImBackupOrdner):
    def test_administrator_kommt_hinein(self):
        self.client.force_login(benutzer(is_staff=True, is_superuser=True))

        antwort = self.client.get(reverse("admin:index"))

        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, "Backup erstellen")

    def test_fuehrungskraft_bekommt_den_grund_zu_lesen(self):
        """The admin's login form would never accept their account and not say why.

        Here it is said.
        """
        self.client.force_login(benutzer(username="carla", email="c@example.org", is_staff=True))

        antwort = self.client.get(reverse("admin:index"))

        self.assertEqual(antwort.status_code, 403)
        self.assertContains(antwort, "Administratorkonto", status_code=403)
        # No login form: their account is signed in, it only lacks the role.
        self.assertNotContains(antwort, 'type="password"', status_code=403)

    def test_auch_die_anmeldung_der_verwaltung_sagt_es(self):
        """Its address does not go through 'admin_view' and needs a check of its own."""
        self.client.force_login(benutzer(username="carla", email="c@example.org", is_staff=True))

        antwort = self.client.get(reverse("admin:login"))

        self.assertEqual(antwort.status_code, 403)
        self.assertContains(antwort, "Administratorkonto", status_code=403)

    def test_zaehlende_landen_auf_der_sperrseite(self):
        self.client.force_login(benutzer(username="zoe", email="z@example.org"))

        antwort = self.client.get(reverse("admin:index"))

        self.assertRedirects(antwort, reverse("kein_zugriff"))


class EigenesPasswortInDerVerwaltung(TestCase):
    """The administration's page for the own password tells the person, as the website does.

    Whoever held an administrator's session and knew the old password could
    change it there without a word: the owner was signed out everywhere and
    shut out of the strongest account without learning why (until 10/2026).
    """

    NEU = "ganz-anderes-Passwort-47"

    def setUp(self):
        self.admin = benutzer(is_staff=True, is_superuser=True)
        self.client.force_login(self.admin)

    def aendern(self, alt: str = "geheim-genug-2026"):
        return self.client.post(
            reverse("admin:password_change"),
            {"old_password": alt, "new_password1": self.NEU, "new_password2": self.NEU},
        )

    def test_eine_mail_sagt_es_der_person(self):
        antwort = self.aendern()

        self.assertRedirects(antwort, reverse("admin:password_change_done"))
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.check_password(self.NEU))
        (nachricht,) = mail.outbox
        self.assertEqual(nachricht.to, ["ada@example.org"])
        # Changed by the person, as on the website: no author, the way back named.
        self.assertIn("auf der Website geändert", nachricht.body)
        self.assertIn("Passwort vergessen?", nachricht.body)
        self.assertNotIn("Geändert von", nachricht.body)

    def test_ein_falsches_altes_passwort_aendert_nichts_und_schickt_nichts(self):
        antwort = self.aendern(alt="nicht-das-alte-2026")

        self.assertEqual(antwort.status_code, 200)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.check_password("geheim-genug-2026"))
        self.assertEqual(mail.outbox, [])


class Darstellung(ImBackupOrdner):
    """Light and dark: one choice for site and administration."""

    def setUp(self):
        super().setUp()
        self.admin = benutzer(is_staff=True, is_superuser=True)
        self.client.force_login(self.admin)

    def test_die_wahl_steht_in_beiden_leisten(self):
        for adresse in (reverse("inventur_list"), reverse("admin:index")):
            with self.subTest(adresse=adresse):
                antwort = self.client.get(adresse)

                self.assertContains(antwort, 'data-thema="auto"')
                self.assertContains(antwort, 'data-thema="light"')
                self.assertContains(antwort, 'data-thema="dark"')

    def test_das_skript_steht_im_kopf(self):
        """If it came later, the light version would flash on every page change."""
        for adresse in (reverse("inventur_list"), reverse("admin:index")):
            with self.subTest(adresse=adresse):
                seite = self.client.get(adresse).content.decode()

                self.assertLess(seite.index("js/theme.js"), seite.index("<body"))

    def test_die_wahl_am_konto_steht_gleich_in_der_seite(self):
        """Otherwise a second device would see what applies to the account only after a click."""
        self.admin.darstellung = "dark"
        self.admin.save(update_fields=["darstellung"])
        for adresse in (reverse("inventur_list"), reverse("admin:index")):
            with self.subTest(adresse=adresse):
                seite = self.client.get(adresse).content.decode()
                self.assertIn('data-am-konto="dark"', seite)
                self.assertIn(reverse("ich", args=["v1"]), seite)

    def test_anmeldeseite_kennt_kein_konto(self):
        """Nobody is known there yet. What the browser last knew applies."""
        self.client.logout()
        seite = self.client.get(reverse("login")).content.decode()
        self.assertIn("js/theme.js", seite)
        self.assertNotIn("data-am-konto", seite)

    def test_die_verwaltung_nutzt_denselben_schluessel_wie_der_admin(self):
        """Django stores its own choice under 'theme'. We stick to that.

        Otherwise each half would have its own setting.
        """
        seite = self.client.get(reverse("admin:index")).content.decode()

        self.assertIn('const SCHLUESSEL = "theme"', skript("theme"))
        # And the admin's dark mode is switched on, otherwise it would stay light.
        self.assertIn("dark_mode", seite)


class _Leisteneintraege(HTMLParser):
    """Reads from the first <nav> of a page what can be clicked in it.

    At the front the bar carries Bootstrap's classes, in the administration its
    own. The two cannot be compared by markup, but by their entries. A menu
    head is a link to "#" at the front and a <summary> at the back. Both count
    as 'menue'. Buttons count only inside a list item: the toggle for the
    collapsible menu and "Help" belong to the bar's construction, not to what
    it offers.
    """

    def __init__(self):
        super().__init__()
        self.eintraege: list[tuple[str, str, str]] = []
        self._in_nav = False
        self._fertig = False
        self._li_tiefe = 0
        self._offen: tuple[str, str] | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if self._fertig:
            return
        merkmale = dict(attrs)
        if tag == "nav":
            self._in_nav = True
        if not self._in_nav:
            return
        if tag == "li":
            self._li_tiefe += 1
        elif tag == "a":
            ziel = merkmale.get("href") or ""
            # The mail behind "Report a problem or suggestion" names the page it
            # stands on. The same entry thus differs front and back after the '?'.
            if ziel.startswith("mailto:"):
                ziel = ziel.partition("?")[0]
            self._offen = ("menue", "") if ziel == "#" else ("link", ziel)
        elif tag == "summary":
            self._offen = ("menue", "")
        elif tag == "button" and self._li_tiefe:
            self._offen = ("knopf", merkmale.get("data-thema") or merkmale.get("type") or "")
        else:
            return
        if tag != "li":
            self._text = []

    def handle_data(self, data):
        if self._offen:
            self._text.append(data)

    def handle_endtag(self, tag):
        if not self._in_nav or self._fertig:
            return
        if tag == "nav":
            self._fertig = True
        elif tag == "li":
            self._li_tiefe -= 1
        elif tag in ("a", "summary", "button") and self._offen:
            art, ziel = self._offen
            self.eintraege.append((art, " ".join("".join(self._text).split()), ziel))
            self._offen = None


def leisteneintraege(seite: str) -> list[tuple[str, str, str]]:
    leser = _Leisteneintraege()
    leser.feed(seite)
    return leser.eintraege


class Leiste(ImBackupOrdner):
    """The bar exists twice: once with Bootstrap, once without.

    Loading Bootstrap into the admin reformatted every list there, so the bar
    of the administration is a replica ('admin/components/navbar.html').
    Whoever adds an entry at the front easily forgets the replica. Then the
    administration would lead somewhere else than the site, and nobody would notice.
    """

    def setUp(self):
        super().setUp()
        self.client.force_login(benutzer(is_staff=True, is_superuser=True))

    def test_beide_leisten_bieten_dasselbe_an(self):
        vorne = leisteneintraege(self.client.get(reverse("inventur_list")).content.decode())
        hinten = leisteneintraege(self.client.get(reverse("admin:index")).content.decode())

        # Otherwise we would compare two empty lists and be satisfied.
        self.assertIn(("link", "Inventuren", reverse("inventur_list")), vorne)
        self.assertIn(("knopf", "Abmelden", "submit"), vorne)
        self.assertEqual(vorne, hinten)

    def test_die_rueckmeldung_steht_in_beiden_leisten(self):
        fassung = Erscheinungsbild.standard().kopie("Eigene")
        fassung.rueckmeldeadresse = "rueckmeldung@example.org"
        fassung.save()
        fassung.aktivieren()

        vorne = leisteneintraege(self.client.get(reverse("inventur_list")).content.decode())
        hinten = leisteneintraege(self.client.get(reverse("admin:index")).content.decode())

        eintrag = ("link", "Problem oder Wunsch melden", "mailto:rueckmeldung@example.org")
        self.assertIn(eintrag, vorne)
        self.assertEqual(vorne, hinten)

    @override_settings(APP_VERSION="9.8.7")
    def test_die_version_steht_in_beiden_leisten(self):
        """On a question the first thing asked, even when it comes from the administration."""
        for adresse in (reverse("inventur_list"), reverse("admin:index")):
            with self.subTest(adresse=adresse):
                self.assertContains(self.client.get(adresse), "Version 9.8.7")

    def test_das_skript_der_leiste_passt_zur_vorlage(self):
        """It closes the menus it finds through these classes."""
        seite = self.client.get(reverse("admin:index")).content.decode()

        self.assertIn("js/admin_navbar.js", seite)
        for klasse in ("leiste", "leiste-menue"):
            with self.subTest(klasse=klasse):
                self.assertIn(f'class="{klasse}', seite)
                self.assertIn(f".{klasse}", skript("admin_navbar"))

    def test_das_demoband_steht_in_beiden_oberflaechen(self):
        """The notice belongs wherever someone could take data for real."""
        kennzeichnen()
        for adresse in (reverse("inventur_list"), reverse("admin:index")):
            with self.subTest(adresse=adresse):
                antwort = self.client.get(adresse)
                self.assertContains(antwort, "demo-band")
                self.assertContains(antwort, "demo-marke")

    def test_die_klassen_des_demobands_kennen_beide_stylesheets(self):
        """The admin loads no Bootstrap. A class only in 'site.css' has no effect there.

        That is exactly what happened on the first attempt: the banner was there
        but looked like ordinary text.
        """
        for klasse in ("demo-band", "demo-marke"):
            for datei in ("site.css", "admin_site.css"):
                with self.subTest(klasse=klasse, datei=datei):
                    self.assertIn(f".{klasse}", stylesheet(datei))

    def test_beide_leisten_haengen_sich_unter_das_band(self):
        """The banner sticks at the top. A bar at 0 would slide over it when scrolling.

        The banner's height is in '--band-hoehe' and is measured, because the
        sentence wraps on a narrow device ('js/demo_band.js').
        """
        for datei in ("site.css", "admin_site.css"):
            with self.subTest(datei=datei):
                regeln = stylesheet(datei)
                band = regeln.split(".demo-band {", 1)[1].split("}", 1)[0]
                self.assertIn("position: sticky", band)
                self.assertIn("top: var(--band-hoehe)", regeln)

    def test_ein_popup_bekommt_keine_leiste(self):
        """It is a popup for selecting. Nobody should navigate away from there."""
        antwort = self.client.get(reverse("admin:accounts_customuser_changelist") + "?_popup=1")

        self.assertNotContains(antwort, 'class="leiste"')


class Uebersicht(ImBackupOrdner):
    """Only what the web interface cannot do."""

    def setUp(self):
        super().setUp()
        self.client.force_login(benutzer(is_staff=True, is_superuser=True))
        self.modelle = {
            eintrag["object_name"]
            for app in self.client.get(reverse("admin:index")).context["app_list"]
            for eintrag in app["models"]
        }

    def test_inventur_und_anmeldeversuche_stehen_dort(self):
        self.assertIn("Inventur", self.modelle)
        self.assertIn("Anmeldeversuch", self.modelle)
        # Who is locked out right now, and the only place where a lock can be
        # lifted before the waiting time expires.
        self.assertIn("Fehlversuch", self.modelle)

    def test_die_englischen_modelle_des_sperrpakets_bleiben_draussen(self):
        """Otherwise "Access attempts" would stand under "Axes" next to our proxy.

        The same table twice, once German and once English, and the English
        version without the columns that matter here.
        """
        for name in ("AccessAttempt", "AccessLog", "AccessFailureLog"):
            with self.subTest(modell=name):
                self.assertNotIn(name, self.modelle)

    def test_was_die_seite_selbst_pflegt_steht_nicht_dort(self):
        """Two ways for the same work would be one too many.

        One would have the form's rules, the other would not.
        """
        for name in ("Ware", "Hausartikel", "Warengruppe", "Haus", "Behaelter"):
            with self.subTest(modell=name):
                self.assertNotIn(name, self.modelle)

    def test_die_auswahlfelder_funktionieren_trotzdem(self):
        """Hidden does not mean unregistered: the autocompletion in the

        inventory form asks the house article admin.
        """
        antwort = self.client.get(
            reverse("admin:autocomplete"),
            {
                "app_label": "inventur",
                "model_name": "sollbestand",
                "field_name": "hausartikel",
                "term": "",
            },
        )

        self.assertEqual(antwort.status_code, 200)


class BackupErstellen(ImBackupOrdner):
    def setUp(self):
        super().setUp()
        self.client.force_login(benutzer(is_staff=True, is_superuser=True))

    def test_skript_und_knopf_sichern_dasselbe(self):
        """'bin/full-dumpdata.sh' writes the same models as the button ('APPS').

        In any order: 'dumpdata' sorts by the dependencies itself.
        """
        skript_text = (settings.BASE_DIR / "bin" / "full-dumpdata.sh").read_text(encoding="utf-8")
        aufruf = re.search(r"dumpdata (.*?) --natural-foreign", skript_text, re.DOTALL)
        assert aufruf is not None
        self.assertEqual(sorted(aufruf.group(1).replace("\\", " ").split()), sorted(backup.APPS))

    def test_ein_abgebrochenes_skript_hinterlaesst_keinen_stand(self):
        """'dumpdata' breaks off halfway. Until 10/2026 the half got the final name.

        In a copy of the folder, with a stand-in for the interpreter: it prints
        half a state and fails, or writes what reaches 'backup store'.
        """
        ordner = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, ordner)
        (ordner / "bin").mkdir()
        shutil.copy(settings.BASE_DIR / "bin" / "full-dumpdata.sh", ordner / "bin")
        python = ordner / ".venv" / "bin" / "python"
        python.parent.mkdir(parents=True)
        python.write_text(
            "#!/bin/sh\n"
            'if [ "$2" = dumpdata ]; then printf \'[{"model": "katalog.ware",\'; exit 1; fi\n'
            'cat > "$4.json" && printf "%s" "$4.json"\n',
            encoding="utf-8",
        )
        python.chmod(0o755)

        lauf = subprocess.run(
            ["bash", str(ordner / "bin" / "full-dumpdata.sh")], capture_output=True, check=False
        )

        self.assertNotEqual(lauf.returncode, 0)
        self.assertEqual(list((ordner / "data" / "backups" / "complete").glob("stand_*")), [])

    def test_der_knopf_geht_nur_einmal_ab(self):
        """Writing takes a moment. The button locks and says so until the page is back."""
        seite = self.client.get(reverse("admin:index"))

        self.assertContains(seite, 'data-einmal="Backup wird erstellt …"')
        self.assertContains(seite, "js/submit_once.js")
        self.assertIn("form[data-einmal]", skript("submit_once"))

    def test_der_knopf_schreibt_einen_stand(self):
        Behaelter.objects.create(name="Kiste", tara=Decimal("120"))

        antwort = self.client.post(reverse("admin:backup_erstellen"), follow=True)

        stand = backup.staende()[0]
        self.assertRedirects(antwort, reverse("admin:index"))
        self.assertContains(antwort, stand.name)
        inhalt = json.loads(stand.lesen())
        self.assertIn("katalog.behaelter", {satz["model"] for satz in inhalt})

    def test_nur_per_post(self):
        """A link that a preview image calls must not write anything."""
        self.assertEqual(self.client.get(reverse("admin:backup_erstellen")).status_code, 405)

    def test_die_datei_gehoert_niemandem_sonst(self):
        """It contains password hashes."""
        stand = backup.erstellen()

        self.assertEqual(stand.pfad.stat().st_mode & 0o077, 0)

    def test_aeltere_staende_werden_weggeraeumt(self):
        for nummer in range(9):
            (Path(self.ordner.name) / f"stand_2026-01-0{nummer + 1}_00-00-00.json").write_text("[]")

        backup.erstellen(behalten=3)

        self.assertEqual(len(backup.staende()), 3)
        # The newest is the one just written, the oldest are gone.
        self.assertNotIn("stand_2026-01-01_00-00-00.json", {s.name for s in backup.staende()})

    def test_fremde_dateien_bleiben_liegen(self):
        """The dumps from 'bin/backup.sh' lie in the same folder."""
        fremd = Path(self.ordner.name) / "inventur-2026-01-01-0300.sql.gz"
        fremd.write_bytes(b"nicht unseres")

        backup.erstellen(behalten=0)

        self.assertTrue(fremd.exists())
        self.assertEqual(backup.staende(), [])


class BackupEinspielen(ImBackupOrdner):
    def setUp(self):
        super().setUp()
        self.admin = benutzer(is_staff=True, is_superuser=True)
        self.client.force_login(self.admin)

    def test_der_stand_kommt_zurueck(self):
        # Changed instead of deleted: a delete in the test transaction would leave
        # pending foreign key checks in PostgreSQL, and the 'TRUNCATE' of the
        # restore would refuse. In operation this enclosing transaction does not
        # exist.
        Behaelter.objects.create(name="Kiste", tara=Decimal("120"))
        stand = backup.erstellen()
        Behaelter.objects.filter(name="Kiste").update(name="Umbenannt")
        Behaelter.objects.create(name="Danach", tara=Decimal("1"))

        backup.einspielen(stand)

        self.assertTrue(Behaelter.objects.filter(name="Kiste").exists())
        self.assertFalse(Behaelter.objects.filter(name__in=["Umbenannt", "Danach"]).exists())

    def test_ein_stand_mit_altem_feld_laesst_sich_einspielen(self):
        """A backup from before 2026-09-21 still carries the default container.

        A field that no longer exists must not turn a backup into archive
        material: it is skipped, not rejected.
        """
        Behaelter.objects.create(name="Kiste", tara=Decimal("120"))
        stand = backup.erstellen()
        saetze = json.loads(stand.lesen())
        for satz in saetze:
            if satz["model"] == "katalog.behaelter":
                satz["fields"]["standardbehaelter"] = None
        backup.schreiben(json.dumps(saetze).encode(), stand.pfad)
        Behaelter.objects.filter(name="Kiste").update(name="Umbenannt")

        backup.einspielen(stand)

        self.assertTrue(Behaelter.objects.filter(name="Kiste").exists())

    def test_ein_stand_mit_altem_modell_wird_zur_meldung(self):
        """Before the rename of 2026-09-22 the models had other names.

        'loaddata' silently skips an unknown model, and the rows pointing to it
        fail at the database. That ended as a 500.
        """
        Behaelter.objects.create(name="Kiste", tara=Decimal("120"))
        stand = backup.erstellen()
        saetze = json.loads(stand.lesen())
        for satz in saetze:
            if satz["model"] == "katalog.behaelter":
                satz["model"] = "katalog.altmodell"
        saetze.append({"model": "katalog.gebinde", "pk": 1, "fields": {"name": "Kasten"}})
        backup.schreiben(json.dumps(saetze).encode(), stand.pfad)

        with self.assertRaisesMessage(ValidationError, "lässt sich nicht einspielen"):
            backup.einspielen(stand)
        self.assertTrue(Behaelter.objects.filter(name="Kiste").exists())

    def test_ein_kaputter_stand_wird_zur_meldung(self):
        """A truncated file: 'loaddata' raises a deserialization error, not a database one.

        Until 10/2026 only the latter was caught, and the former ended as a 500.
        """
        Behaelter.objects.create(name="Kiste", tara=Decimal("120"))
        stand = backup.erstellen()
        backup.schreiben(stand.lesen()[:40], stand.pfad)

        with self.assertRaisesMessage(ValidationError, "lässt sich nicht einspielen"):
            backup.einspielen(stand)
        self.assertTrue(Behaelter.objects.filter(name="Kiste").exists())

    def test_ein_datenbankfehler_beim_laden_wird_zur_meldung(self):
        """Any database error, not only 'IntegrityError'. A 'DataError' was a 500 until 10/2026."""
        Behaelter.objects.create(name="Kiste", tara=Decimal("120"))
        stand = backup.erstellen()
        echt = backup.call_command

        def laden_scheitert(name, *args, **kwargs):
            if name == "loaddata":
                raise DataError("value too long for type character varying(100)")
            return echt(name, *args, **kwargs)

        with (
            mock.patch("django_project.backup.call_command", side_effect=laden_scheitert),
            self.assertRaisesMessage(ValidationError, "lässt sich nicht einspielen"),
        ):
            backup.einspielen(stand)
        self.assertTrue(Behaelter.objects.filter(name="Kiste").exists())

    def test_vorher_wird_gesichert(self):
        """Whoever picks the wrong state should be able to go back."""
        stand = backup.erstellen()
        Behaelter.objects.create(name="Danach", tara=Decimal("1"))

        vorher = backup.einspielen(stand)

        gesichert = vorher.lesen().decode()
        self.assertIn("katalog.behaelter", {satz["model"] for satz in json.loads(gesichert)})
        self.assertIn("Danach", gesichert)

    def test_ueber_die_oberflaeche_mit_tippbestaetigung(self):
        stand = backup.erstellen()
        Behaelter.objects.create(name="Danach", tara=Decimal("1"))

        antwort = self.client.post(
            reverse("admin:backup_einspielen", args=[stand.name]), {"bestaetigung": "einspielen"}
        )

        self.assertContains(antwort, "Stand eingespielt")
        self.assertFalse(Behaelter.objects.filter(name="Danach").exists())
        # The sessions live in the database too. Signed out is signed out.
        # And for the signed-out the administration does not exist (StaffOnlyMiddleware).
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 404)

    def test_ein_deadlock_beim_leeren_ist_eine_meldung(self):
        """PostgreSQL ended the restore for a deadlock: a message, and a second try goes through.

        Until 10/2026 that was a server error. The transaction rolls back whole.
        """
        stand = backup.erstellen()
        Behaelter.objects.create(name="Danach", tara=Decimal("1"))
        adresse = reverse("admin:backup_einspielen", args=[stand.name])

        with mock.patch.object(backup, "call_command", side_effect=leeren_im_deadlock):
            antwort = self.client.post(adresse, {"bestaetigung": "einspielen"}, follow=True)

        self.assertContains(antwort, f"Der Stand {stand.name} ließ sich gerade nicht einspielen")
        self.assertContains(antwort, "Die Datenbank ist unverändert. Ein zweiter Versuch genügt.")
        self.assertTrue(Behaelter.objects.filter(name="Danach").exists())
        self.assertEqual(backup.staende(), [stand])
        antwort = self.client.post(adresse, {"bestaetigung": "einspielen"})
        self.assertContains(antwort, "Stand eingespielt")
        self.assertFalse(Behaelter.objects.filter(name="Danach").exists())

    def test_ein_anderer_fehler_beim_leeren_bleibt_ein_fehler(self):
        """Only the deadlock becomes a message. Whatever else 'flush' meets stays what it is."""
        stand = backup.erstellen()

        def scheitert(name, *args, **kwargs):
            if name == "flush":
                raise CommandError("Database couldn't be flushed.") from OperationalError("weg")
            return call_command(name, *args, **kwargs)

        with (
            mock.patch.object(backup, "call_command", side_effect=scheitert),
            self.assertRaises(CommandError),
        ):
            backup.einspielen(stand)

    def test_ohne_das_wort_geschieht_nichts(self):
        stand = backup.erstellen()
        Behaelter.objects.create(name="Danach", tara=Decimal("1"))

        antwort = self.client.post(
            reverse("admin:backup_einspielen", args=[stand.name]), {"bestaetigung": "ja"}
        )

        self.assertContains(antwort, "muss")
        self.assertTrue(Behaelter.objects.filter(name="Danach").exists())

    def test_ein_erfundener_stand_fuehrt_zurueck(self):
        antwort = self.client.post(
            reverse("admin:backup_einspielen", args=["stand_2000-01-01_00-00-00.json"]),
            {"bestaetigung": "einspielen"},
            follow=True,
        )

        self.assertContains(antwort, "gibt es nicht")

    def test_die_sicherung_ueberschreibt_den_stand_nicht(self):
        """Both happen in the same second.

        With the same name the safety copy would have overwritten exactly the
        state that is to be restored.
        """
        Behaelter.objects.create(name="Kiste", tara=Decimal("120"))
        stand = backup.erstellen()
        Behaelter.objects.filter(name="Kiste").update(name="Umbenannt")

        vorher = backup.einspielen(stand)

        self.assertNotEqual(vorher.name, stand.name)
        self.assertTrue(Behaelter.objects.filter(name="Kiste").exists())

    def test_der_entschluesselte_stand_liegt_nicht_im_backup_ordner(self):
        """The backup folder lies on the machine. A side file there outlived a killed worker."""
        stand = backup.erstellen()
        gelesen = []
        echt = backup.call_command

        def laden(name, *args, **kwargs):
            if name == "loaddata":
                gelesen.append(Path(args[0]))
            return echt(name, *args, **kwargs)

        with mock.patch("django_project.backup.call_command", side_effect=laden):
            backup.einspielen(stand)

        self.assertEqual(len(gelesen), 1)
        self.assertNotEqual(gelesen[0].parent, backup.verzeichnis())
        self.assertFalse(gelesen[0].exists())

    def test_ein_stand_der_demo_bringt_sein_band_mit(self):
        """Until 10/2026 every restore dropped the marker, and invented data looked real."""
        kennzeichnen()
        stand = backup.erstellen()

        backup.einspielen(stand)

        self.assertTrue(ist_demobestand())

    def test_ein_echter_stand_nimmt_der_demo_das_band(self):
        stand = backup.erstellen()
        kennzeichnen()

        backup.einspielen(stand)

        self.assertFalse(ist_demobestand())

    def acht_aeltere(self, inhalt: bytes, endung: str = ENDUNG) -> list[backup.Stand]:
        """Eight states from January, as after a restore: seven kept and the safety copy.

        Oldest first. The one made to read the content is gone again.
        """
        for stand in backup.staende():
            stand.pfad.unlink()
        ordner = backup.verzeichnis()
        staende = []
        for tag in range(1, 9):
            pfad = backup.freier_name(ordner, f"2026-01-{tag:02d}_08-00-00", endung)
            backup.schreiben(inhalt, pfad)
            staende.append(backup.Stand(pfad))
        return staende

    def test_der_aelteste_von_acht_bleibt_liegen(self):
        """After a restore eight states lie there, and the oldest may be chosen next.

        Until 10/2026 the safety copy cleaned up first and deleted exactly it.
        """
        Behaelter.objects.create(name="Kiste", tara=Decimal("120"))
        aeltester = self.acht_aeltere(backup.erstellen().lesen())[0]
        Behaelter.objects.filter(name="Kiste").update(name="Umbenannt")

        backup.einspielen(aeltester)

        self.assertTrue(Behaelter.objects.filter(name="Kiste").exists())
        self.assertTrue(aeltester.pfad.exists())
        self.assertIn(aeltester.name, [stand.name for stand in backup.staende()])

    def test_ohne_schluessel_laedt_der_aelteste_aus_seiner_datei(self):
        """Unencrypted, 'loaddata' reads the file itself. Deleted first, it was not found."""
        Behaelter.objects.create(name="Kiste", tara=Decimal("120"))
        with override_settings(BACKUP_KEY=""):
            aeltester = self.acht_aeltere(backup.erstellen().lesen(), endung="")[0]
            Behaelter.objects.filter(name="Kiste").update(name="Umbenannt")

            backup.einspielen(aeltester)

        self.assertTrue(Behaelter.objects.filter(name="Kiste").exists())
        self.assertTrue(aeltester.pfad.exists())

    def test_ein_stand_der_nicht_laedt_bleibt_liegen(self):
        """The code of its time may still read it. Nothing is cleaned up then."""
        Behaelter.objects.create(name="Kiste", tara=Decimal("120"))
        staende = self.acht_aeltere(backup.erstellen().lesen())
        aeltester = staende[0]
        backup.schreiben(aeltester.lesen()[:40], aeltester.pfad)

        with self.assertRaisesMessage(ValidationError, "lässt sich nicht einspielen"):
            backup.einspielen(aeltester)

        self.assertTrue(all(stand.pfad.exists() for stand in staende))
        # The eight, and no safety copy: the database is unchanged.
        self.assertEqual(len(backup.staende()), 8)

    def test_gescheiterte_versuche_hinterlassen_nichts(self):
        """Until 10/2026 each one left its safety copy, eleven states after three tries."""
        Behaelter.objects.create(name="Kiste", tara=Decimal("120"))
        staende = self.acht_aeltere(backup.erstellen().lesen())
        kaputt = staende[0]
        backup.schreiben(kaputt.lesen()[:40], kaputt.pfad)

        for _versuch in range(3):
            with self.assertRaisesMessage(ValidationError, "lässt sich nicht einspielen"):
                backup.einspielen(kaputt)

        self.assertEqual({s.name for s in backup.staende()}, {s.name for s in staende})

    def test_ein_backup_waehrend_des_einspielens_raeumt_nicht_auf(self):
        """The running restore reads one of the states, maybe the oldest.

        Until 10/2026 "Create backup" from a second tab deleted it meanwhile.
        The restore cleans up itself at its end and spares it.
        """
        staende = self.acht_aeltere(backup.erstellen().lesen())
        sperre = open(Path(self.ordner.name) / ".einspielen.lock", "w")
        self.addCleanup(sperre.close)
        fcntl.flock(sperre, fcntl.LOCK_EX)

        neu = backup.erstellen()

        uebrig = {stand.name for stand in backup.staende()}
        self.assertEqual(uebrig, {stand.name for stand in staende} | {neu.name})

    def test_danach_bleiben_die_juengsten_und_der_gewaehlte(self):
        """The safety copy does not count against the seven, the chosen one stays besides."""
        Behaelter.objects.create(name="Kiste", tara=Decimal("120"))
        staende = self.acht_aeltere(backup.erstellen().lesen())
        mittlerer = staende[3]

        vorher = backup.einspielen(mittlerer)

        uebrig = {stand.name for stand in backup.staende()}
        self.assertEqual(len(uebrig), backup.BEHALTEN + 1)
        self.assertIn(vorher.name, uebrig)
        self.assertIn(mittlerer.name, uebrig)
        self.assertNotIn(staende[0].name, uebrig)

    def test_zwei_staende_in_derselben_sekunde(self):
        erster = backup.erstellen()
        zweiter = backup.erstellen()

        self.assertNotEqual(erster.name, zweiter.name)
        self.assertEqual(len(backup.staende()), 2)
        # The second is the newer one and comes first.
        self.assertEqual(backup.staende()[0].name, zweiter.name)

    def test_kein_ausbruch_aus_dem_ordner(self):
        """The name comes from an address.

        It is therefore compared against the list, not joined into a path.
        """
        self.assertIsNone(backup.stand_holen("../../etc/passwd"))
        self.assertIsNone(backup.stand_holen("stand_2026-01-01_00-00-00.json"))


class BackupEinspielenAufEnglisch(ImBackupOrdner):
    """The page asks for the translated word, and the view has to take it.

    Until 10/2026 the page said "restore" and the view compared with
    "einspielen": an English account could not restore at all.
    """

    def setUp(self):
        super().setUp()
        self.client.force_login(benutzer(is_staff=True, is_superuser=True, sprache="en"))
        self.stand = backup.erstellen()

    def test_das_uebersetzte_wort_spielt_ein(self):
        adresse = reverse("admin:backup_einspielen", args=[self.stand.name])
        seite = self.client.get(adresse)
        self.assertContains(seite, "<code>restore</code>")
        antwort = self.client.post(adresse, {"bestaetigung": "restore"})
        self.assertTemplateUsed(antwort, "admin/backup_done.html")

    def test_das_deutsche_wort_geht_weiter(self):
        adresse = reverse("admin:backup_einspielen", args=[self.stand.name])
        antwort = self.client.post(adresse, {"bestaetigung": "einspielen"})
        self.assertTemplateUsed(antwort, "admin/backup_done.html")

    def test_ein_deadlock_beim_leeren_auf_englisch(self):
        adresse = reverse("admin:backup_einspielen", args=[self.stand.name])
        with mock.patch.object(backup, "call_command", side_effect=leeren_im_deadlock):
            antwort = self.client.post(adresse, {"bestaetigung": "restore"}, follow=True)
        self.assertContains(antwort, f"The backup {self.stand.name} could not be restored just now")
        self.assertContains(antwort, "The database is unchanged. A second attempt will do.")


class BackupVerschluesselt(ImBackupOrdner):
    """Every state is encrypted. An old one from before is restored once it is encrypted too.

    A state holds the accounts with their password hashes. It is copied and
    kept on other machines, where nobody watches over it any more.
    """

    def setUp(self):
        super().setUp()
        self.client.force_login(benutzer(is_staff=True, is_superuser=True))

    def alter_stand(self, name="stand_2026-01-01_00-00-00.json") -> backup.Stand:
        """As the application wrote it until 09/2026: readable JSON."""
        pfad = Path(self.ordner.name) / name
        vorlage = backup.erstellen()
        pfad.write_bytes(vorlage.lesen())
        vorlage.pfad.unlink()
        return backup.Stand(pfad)

    def test_in_der_datei_steht_nichts_lesbares(self):
        Behaelter.objects.create(name="Kiste", tara=Decimal("120"))

        stand = backup.erstellen()

        roh = stand.pfad.read_bytes()
        self.assertTrue(stand.name.endswith(".json.gz.enc"))
        self.assertTrue(roh.startswith(KENNUNG))
        self.assertNotIn(b"Kiste", roh)
        self.assertNotIn(b"katalog", roh)

    def test_im_ordner_liegt_danach_nichts_lesbares(self):
        """Neither the state itself nor what restoring unpacked along the way."""
        Behaelter.objects.create(name="Kiste", tara=Decimal("120"))

        backup.einspielen(backup.erstellen())

        for datei in Path(self.ordner.name).iterdir():
            with self.subTest(datei=datei.name):
                self.assertNotIn(b"Kiste", datei.read_bytes())

    def test_mit_dem_falschen_schluessel_geschieht_nichts(self):
        """No safety copy, no emptied table: decrypted is before all that."""
        Behaelter.objects.create(name="Kiste", tara=Decimal("120"))
        stand = backup.erstellen()
        Behaelter.objects.create(name="Danach", tara=Decimal("1"))

        with override_settings(BACKUP_KEY="ein-anderer-schluessel-und-auch-lang-genug"):
            with self.assertRaises(NichtLesbar):
                backup.einspielen(stand)

        self.assertTrue(Behaelter.objects.filter(name="Danach").exists())
        self.assertEqual(len(backup.staende()), 1)

    def test_die_oberflaeche_sagt_dass_der_schluessel_nicht_passt(self):
        stand = backup.erstellen()

        with override_settings(BACKUP_KEY="ein-anderer-schluessel-und-auch-lang-genug"):
            antwort = self.client.post(
                reverse("admin:backup_einspielen", args=[stand.name]),
                {"bestaetigung": "einspielen"},
                follow=True,
            )

        self.assertContains(antwort, "Der Schlüssel passt nicht")
        self.assertNotContains(antwort, "Stand eingespielt")

    def test_eine_beschaedigte_datei_wird_zur_meldung(self):
        stand = backup.erstellen()
        roh = stand.pfad.read_bytes()
        stand.pfad.write_bytes(roh[: len(roh) // 2])

        with self.assertRaises(NichtLesbar):
            backup.einspielen(stand)

    def test_ein_unverschluesselter_stand_wird_mit_schluessel_nicht_eingespielt(self):
        """Anyone who may write into the folder could have placed it there.

        Until 10/2026 it was loaded as it was, accounts and password hashes
        included. No safety copy and no emptied table: the check comes first.
        """
        Behaelter.objects.create(name="Kiste", tara=Decimal("120"))
        alt = self.alter_stand()
        Behaelter.objects.filter(name="Kiste").update(name="Umbenannt")

        with self.assertRaisesMessage(backup.Unverschluesselt, "nicht verschlüsselt"):
            backup.einspielen(alt)

        self.assertTrue(Behaelter.objects.filter(name="Umbenannt").exists())
        self.assertEqual([stand.name for stand in backup.staende()], [alt.name])

    def test_die_verwaltung_sagt_es_schon_beim_oeffnen(self):
        """Nobody types the word for a state that is not restored anyway."""
        alt = self.alter_stand()

        antwort = self.client.get(reverse("admin:backup_einspielen", args=[alt.name]), follow=True)

        self.assertRedirects(antwort, reverse("admin:index"))
        self.assertContains(antwort, "nicht verschlüsselt")
        self.assertContains(antwort, "manage.py backup store")

    def test_auch_getippt_spielt_die_verwaltung_ihn_nicht_ein(self):
        Behaelter.objects.create(name="Kiste", tara=Decimal("120"))
        alt = self.alter_stand()
        Behaelter.objects.filter(name="Kiste").update(name="Umbenannt")

        antwort = self.client.post(
            reverse("admin:backup_einspielen", args=[alt.name]),
            {"bestaetigung": "einspielen"},
            follow=True,
        )

        self.assertContains(antwort, "nicht verschlüsselt")
        self.assertTrue(Behaelter.objects.filter(name="Umbenannt").exists())
        self.assertEqual(len(backup.staende()), 1)

    def test_mit_dem_schluessel_abgelegt_steht_ein_alter_stand_zum_einspielen_bereit(self):
        """The way DEPLOYMENT.md names for an own state from before 09/2026: 'backup store'."""
        Behaelter.objects.create(name="Kiste", tara=Decimal("120"))
        alt = self.alter_stand()
        Behaelter.objects.filter(name="Kiste").update(name="Umbenannt")

        with alt.pfad.open("rb") as quelle, mock.patch("sys.stdin", mock.Mock(buffer=quelle)):
            call_command("backup", "store", str(alt.pfad.parent / alt.stamm), stdout=io.StringIO())
        alt.pfad.unlink()
        (verschluesselt,) = backup.staende()
        vorher = backup.einspielen(verschluesselt)

        self.assertEqual(verschluesselt.name, f"{alt.stamm}.json.gz.enc")
        self.assertTrue(Behaelter.objects.filter(name="Kiste").exists())
        self.assertTrue(vorher.verschluesselt)

    def test_die_liste_sagt_welcher_stand_unverschluesselt_ist(self):
        self.alter_stand()

        seite = self.client.get(reverse("admin:index"))

        self.assertContains(seite, "unverschlüsselt", count=1)

    def test_die_liste_bietet_einen_unverschluesselten_stand_nicht_zum_einspielen_an(self):
        """With a key set, the click would only end in the refusal. The row says what to do."""
        alt = self.alter_stand()
        neu = backup.erstellen()

        seite = self.client.get(reverse("admin:index"))

        self.assertNotContains(seite, reverse("admin:backup_einspielen", args=[alt.name]))
        self.assertContains(seite, reverse("admin:backup_einspielen", args=[neu.name]))
        self.assertContains(seite, "erst verschlüsseln", count=1)

    @override_settings(BACKUP_KEY="")
    def test_ohne_schluessel_bietet_die_liste_ihn_an(self):
        alt = self.alter_stand()

        seite = self.client.get(reverse("admin:index"))

        self.assertContains(seite, reverse("admin:backup_einspielen", args=[alt.name]))
        self.assertNotContains(seite, "erst verschlüsseln")

    def test_alte_und_neue_teilen_sich_die_laufnummern(self):
        """Otherwise two states of the same second would carry the same name stem."""
        ordner = Path(self.ordner.name)
        (ordner / "stand_2026-01-01_00-00-00.json").write_text("[]")

        frei = backup.freier_name(ordner, "2026-01-01_00-00-00", ".gz.enc")

        self.assertEqual(frei.name, "stand_2026-01-01_00-00-00-2.json.gz.enc")
        self.assertEqual(backup.Stand(frei).laufnummer, 2)

    def test_alte_staende_altern_mit_heraus(self):
        for nummer in range(4):
            self.alter_stand(f"stand_2026-01-0{nummer + 1}_00-00-00.json")

        backup.erstellen(behalten=2)

        namen = [stand.name for stand in backup.staende()]
        self.assertEqual(len(namen), 2)
        self.assertTrue(namen[0].endswith(".json.gz.enc"))
        self.assertEqual(namen[1], "stand_2026-01-04_00-00-00.json")

    @override_settings(BACKUP_KEY="")
    def test_ohne_schluessel_wird_wie_bisher_geschrieben(self):
        """Only during development. In operation the application does not start without one."""
        stand = backup.erstellen()

        self.assertTrue(stand.name.endswith(".json"))
        self.assertIn("model", json.loads(stand.pfad.read_text())[0])


class BackupDoppeltAbgeschickt(ImBackupOrdner):
    """Enter in the field and then the button: two requests that ran side by side."""

    def setUp(self):
        super().setUp()
        self.client.force_login(benutzer(is_staff=True, is_superuser=True))
        self.stand = backup.erstellen()

    def waehrend_eines_anderen(self):
        """Holds the lock as if a first restore were running right now."""
        datei = open(Path(self.ordner.name) / ".einspielen.lock", "w")
        self.addCleanup(datei.close)
        fcntl.flock(datei, fcntl.LOCK_EX)

    def test_das_zweite_wird_abgewiesen(self):
        self.waehrend_eines_anderen()
        Behaelter.objects.create(name="Danach", tara=Decimal("1"))

        with self.assertRaises(backup.LaeuftSchon):
            backup.einspielen(self.stand)

        self.assertTrue(Behaelter.objects.filter(name="Danach").exists())
        # No second "before" safety copy next to the first.
        self.assertEqual(len(backup.staende()), 1)

    def test_die_oberflaeche_zeigt_eine_seite_statt_der_verwaltung(self):
        """Redirected into the administration, a signed-out user would face a 404 right away."""
        self.waehrend_eines_anderen()

        antwort = self.client.post(
            reverse("admin:backup_einspielen", args=[self.stand.name]),
            {"bestaetigung": "einspielen"},
        )

        self.assertContains(antwort, "Stand wird schon eingespielt")

    def test_nach_dem_ersten_geht_das_naechste(self):
        """The lock applies only while one is running. It does not get stuck."""
        backup.einspielen(self.stand)
        backup.einspielen(self.stand)

        self.assertEqual(len(backup.staende()), 3)

    def test_das_formular_geht_nur_einmal_ab(self):
        seite = self.client.get(reverse("admin:backup_einspielen", args=[self.stand.name]))

        self.assertContains(seite, "data-einmal=")
        self.assertContains(seite, "js/submit_once.js")
        self.assertIn("form[data-einmal]", skript("submit_once"))


class BackupWaehrendDerZaehlung(ImBackupOrdner):
    """The counting apps hold rows of the current state. Restoring happens only afterwards."""

    def setUp(self):
        super().setUp()
        haus = Haus.objects.create(name="Kino Ost")
        self.inventur = inventur_anlegen(haus, stichtag=date(2026, 1, 31))
        self.inventur.zaehlung_starten()
        self.client.force_login(benutzer(is_staff=True, is_superuser=True))

    def test_einspielen_ist_gesperrt(self):
        stand = backup.erstellen()
        with self.assertRaises(ValidationError):
            backup.einspielen(stand)

    def test_die_oberflaeche_sagt_es_und_tut_nichts(self):
        stand = backup.erstellen()
        antwort = self.client.post(
            reverse("admin:backup_einspielen", args=[stand.name]),
            {"bestaetigung": "einspielen"},
            follow=True,
        )
        self.assertContains(antwort, "solange gezählt wird")
        self.assertContains(antwort, "gesperrt")
        self.assertNotContains(antwort, "Stand eingespielt")
        self.assertTrue(Inventur.objects.filter(pk=self.inventur.pk).exists())

    def test_erstellen_geht_weiter(self):
        antwort = self.client.post(reverse("admin:backup_erstellen"), follow=True)
        self.assertEqual(len(backup.staende()), 1)
        self.assertContains(antwort, backup.staende()[0].name)

    def test_der_stand_kennt_die_konten_ohne_warnung(self):
        """Account and passkeys, not the proxies onto the login attempts. Those warned."""
        Passkey.objects.create(
            benutzer=CustomUser.objects.get(),
            kennung="probe-kennung",
            oeffentlicher_schluessel=b"\x01",
            name="Laptop",
        )
        stand = backup.erstellen()
        modelle = {satz["model"] for satz in json.loads(stand.lesen())}
        self.assertIn("accounts.customuser", modelle)
        self.assertIn("accounts.passkey", modelle)
        self.assertEqual(
            {m for m in modelle if m.startswith("accounts.")},
            {"accounts.customuser", "accounts.passkey"},
        )


class BackupUndTokens(TransactionTestCase):
    """After the restore every app sign-in is over, and the tokens hang on the name.

    'TransactionTestCase', not 'TestCase': the 'TRUNCATE' of the restore does
    not tolerate pending foreign key checks of an enclosing transaction in
    PostgreSQL, and a token points to its account.
    """

    serialized_rollback = True

    def setUp(self):
        self.ordner = tempfile.TemporaryDirectory()
        self.addCleanup(self.ordner.cleanup)
        umgebung = override_settings(BACKUP_DIR=self.ordner.name, BACKUP_KEY=SCHLUESSEL)
        umgebung.enable()
        self.addCleanup(umgebung.disable)

    def test_gesperrte_tokens_bleiben_gesperrt(self):
        """Whoever signed out today must not be signed back in by yesterday's state."""
        gesperrt = RefreshToken.for_user(benutzer())
        gesperrt.blacklist()
        stand = backup.erstellen()

        backup.einspielen(stand)

        antwort = APIClient().post(
            reverse("token_erneuern", args=["v1"]), {"refresh": str(gesperrt)}
        )
        self.assertEqual(antwort.status_code, 401)

    def erneuern(self, refresh) -> int:
        antwort = APIClient().post(
            reverse("token_erneuern", args=["v1"]), {"refresh": str(refresh)}
        )
        return antwort.status_code

    def test_danach_ist_jede_app_abgemeldet(self):
        """Until 10/2026 every token not on the blocklist went on renewing."""
        handy = RefreshToken.for_user(benutzer())
        stand = backup.erstellen()

        backup.einspielen(stand)

        self.assertEqual(self.erneuern(handy), 401)

    def test_danach_ist_jede_sitzung_der_website_beendet(self):
        """They go with the emptying, those of every account and those without one."""
        browser = Client()
        browser.force_login(benutzer())
        Client().post(reverse("passkey_anmelden_optionen"))
        self.assertEqual(Sitzung.objects.count(), 2)
        stand = backup.erstellen()

        backup.einspielen(stand)

        self.assertFalse(Sitzung.objects.exists())
        self.assertNotEqual(browser.get(reverse("passkey_list")).status_code, 200)

    def test_auch_ein_token_das_der_stand_nicht_kennt(self):
        """Issued after the backup and renewed once: the state knows neither."""
        konto = benutzer()
        stand = backup.erstellen()
        erneuert = APIClient().post(
            reverse("token_erneuern", args=["v1"]), {"refresh": str(RefreshToken.for_user(konto))}
        )

        backup.einspielen(stand)

        self.assertEqual(self.erneuern(erneuert.data["refresh"]), 401)

    def test_ein_neues_passwort_von_danach_wird_nicht_zurueckgenommen(self):
        """The old password comes back with the state, the phone the new one shut out does not."""
        konto = benutzer()
        handy = RefreshToken.for_user(konto)
        stand = backup.erstellen()
        konto.set_password("ein-neues-und-langes-2026")
        konto.save()
        self.assertEqual(self.erneuern(handy), 401)

        backup.einspielen(stand)

        self.assertEqual(self.erneuern(handy), 401)

    def test_ein_deaktiviertes_konto_bleibt_draussen(self):
        konto = benutzer()
        handy = RefreshToken.for_user(konto)
        stand = backup.erstellen()
        konto.is_active = False
        konto.save()

        backup.einspielen(stand)

        self.assertTrue(User.objects.get(username="ada").is_active)
        self.assertEqual(self.erneuern(handy), 401)

    def test_die_seite_sagt_es_vorher(self):
        stand = backup.erstellen()
        self.client.force_login(benutzer(is_staff=True, is_superuser=True))

        seite = self.client.get(reverse("admin:backup_einspielen", args=[stand.name]))

        self.assertContains(seite, "Danach ist jeder abgemeldet")
        self.assertContains(seite, "Konten, Passwörter und Passkeys sind danach die aus dem Stand")

    def test_tokens_haengen_am_namen_nicht_an_der_nummer(self):
        """After the restore the numbers shift. A token must not change accounts."""
        weg = benutzer(username="weg", email="weg@example.org")
        chef = benutzer(username="chef", email="chef@example.org", is_staff=True)
        token_weg = str(RefreshToken.for_user(weg).access_token)
        token_chef = str(RefreshToken.for_user(chef).access_token)
        alte_nummer = chef.pk
        weg.delete()
        stand = backup.erstellen()

        backup.einspielen(stand)

        self.assertNotEqual(User.objects.get(username="chef").pk, alte_nummer)
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {token_chef}")
        self.assertEqual(api.get(reverse("ich", args=["v1"])).data["benutzername"], "chef")
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {token_weg}")
        self.assertEqual(api.get(reverse("ich", args=["v1"])).status_code, 401)

    def test_wer_gezaehlt_hat_kommt_mit_einem_alten_stand_nicht_zurueck(self):
        """A state from before 2026-10-13 still names who counted in a closed inventory.

        Here and not in 'BackupEinspielen': the inventory points to its house,
        and the pending foreign key checks of an enclosing transaction would make
        the 'TRUNCATE' of the restore refuse under PostgreSQL.
        """
        inventur = inventur_anlegen(
            Haus.objects.create(name="Kino Nord"), stichtag=date(2026, 1, 31)
        )
        inventur.zaehlung_starten()
        inventur.abschliessen()
        inventur.zaehlende.add(benutzer(is_staff=True, is_superuser=True))
        stand = backup.erstellen()

        backup.einspielen(stand)

        abgeschlossen = Inventur.objects.get(status="ABGESCHLOSSEN")
        self.assertFalse(abgeschlossen.zaehlende.exists())
