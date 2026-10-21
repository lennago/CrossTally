"""Backups from before the inventory types became tabs: they keep what they say."""

import io
import json
import sys
import tempfile
from pathlib import Path
from unittest import mock

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import SimpleTestCase, override_settings

from inventur.models import Artentscheidung, Inventur
from katalog.models import Hausartikel, Inventurart

from . import backup
from .backup_upgrade import umschreiben
from .test_admin_site import ImBackupOrdner
from .test_encryption import Strom

ZEIT = "2026-09-01T10:00:00Z"


def artikel(pk: int, haus: int, ware: str, nummer: str) -> list[dict]:
    """A house article as 'dumpdata' writes it: the parent row and its own."""
    return [
        {
            "model": "katalog.artikel",
            "pk": pk,
            "fields": {"name": ware, "erstellt_am": ZEIT, "geaendert_am": ZEIT},
        },
        {
            "model": "katalog.hausartikel",
            "pk": pk,
            "fields": {
                "artikel_ptr": pk,
                "quelle": "winticket",
                "haus": haus,
                "ware": [ware],
                "warengruppe": 1,
                "basiseinheit": ["Stück(e)"],
                "faktor_zur_ware": None,
                "artikelnummer": nummer,
                "ean": "",
                "status": "AKTIV",
                "aktiv": True,
                "gueltig_bis": None,
                "zaehlt_als": None,
            },
        },
    ]


def ware(name: str, umfang: str) -> dict:
    return {
        "model": "katalog.ware",
        "fields": {
            "name": name,
            "basiseinheit": ["Stück(e)"],
            "warenfamilie": None,
            "inventurumfang": umfang,
            "abwiegbar": False,
            "behaelter": [],
        },
    }


def inventur(pk: int, haus: int, typ: str, stichtag: str, art: str | None = None) -> dict:
    return {
        "model": "inventur.inventur",
        "pk": pk,
        "fields": {
            "haus": haus,
            "typ": typ,
            "inventurart": [art] if art else None,
            "stichtag": stichtag,
            "status": "VORBEREITUNG",
            "erstellt_am": ZEIT,
            "abgeschlossen_am": None,
            "zaehlende": [],
        },
    }


def alter_stand() -> list[dict]:
    """A state as the application wrote it until 10/2026, in the order of 'dumpdata'.

    Artikel C is counted monthly, Ware J only annually. The reusable cups are
    in an own type that counts along nowhere, and a weekly count takes its
    products into month and year. It holds none.
    """
    return [
        {"model": "organisation.haus", "pk": 1, "fields": {"name": "Kino Nord"}},
        {"model": "organisation.haus", "pk": 2, "fields": {"name": "Kino Ost"}},
        {
            "model": "katalog.basiseinheit",
            "fields": {
                "name": "Stück(e)",
                "kennung": "STUECK",
                "nachkommastellen": 0,
                "dimension": "STUECK",
                "faktor_zur_referenz": "1.000000",
            },
        },
        {"model": "katalog.warengruppe", "pk": 1, "fields": {"name": "Warengruppe G"}},
        ware("Artikel C", "MONAT_UND_JAHR"),
        ware("Ware J", "NUR_JAHR"),
        ware("Mehrwegbecher", "MONAT_UND_JAHR"),
        {
            "model": "katalog.inventurart",
            "fields": {"name": "Wochenzählung", "umfang": "MONAT_UND_JAHR", "waren": []},
        },
        {
            "model": "katalog.inventurart",
            "fields": {"name": "Becherinventur", "umfang": "KEINER", "waren": [["Mehrwegbecher"]]},
        },
        *artikel(1, 1, "Artikel C", "101001"),
        *artikel(2, 2, "Artikel C", "102001"),
        *artikel(3, 1, "Ware J", "101002"),
        *artikel(4, 1, "Mehrwegbecher", "101003"),
        inventur(1, 1, "MONAT", "2026-08-31"),
        inventur(2, 1, "JAHR", "2026-12-31"),
        inventur(3, 2, "EIGEN", "2026-09-07", art="Becherinventur"),
        {
            "model": "inventur.umfangsentscheidung",
            "pk": 1,
            "fields": {"inventur": 1, "ware": ["Artikel C"], "umfang": "NUR_JAHR"},
        },
        {
            "model": "inventur.artentscheidung",
            "pk": 1,
            "fields": {
                "inventur": 1,
                "ware": ["Mehrwegbecher"],
                "inventurart": ["Becherinventur"],
                "aktion": "AUFGENOMMEN",
            },
        },
        {
            "model": "inventur.artentscheidung",
            "pk": 2,
            "fields": {
                "inventur": 1,
                "ware": ["Ware J"],
                "inventurart": ["Wochenzählung"],
                "aktion": "HERAUSGENOMMEN",
            },
        },
    ]


def als_text(saetze: list[dict]) -> bytes:
    return json.dumps(saetze, ensure_ascii=False).encode()


@override_settings(LANGUAGE_CODE="de")
class Regeln(SimpleTestCase):
    """The rules of the former type migrations, on the file."""

    def setUp(self):
        self.neu = json.loads(umschreiben(als_text(alter_stand())))

    def von(self, modell: str) -> list[dict]:
        return [satz["fields"] for satz in self.neu if satz["model"] == modell]

    def test_die_arten_in_der_reihenfolge_der_migration(self):
        self.assertEqual(
            self.von("katalog.inventurart"),
            [
                {"name": "Monatsinventur", "reihenfolge": 1},
                {"name": "Jahresinventur", "reihenfolge": 2},
                {"name": "Becherinventur", "reihenfolge": 3},
                {"name": "Wochenzählung", "reihenfolge": 4},
            ],
        )

    def test_jeder_hausartikel_bekommt_die_art_seiner_ware(self):
        """The own type before the scope. An own type without products takes none."""
        arten = {
            felder["artikelnummer"]: felder["inventurart"]
            for felder in self.von("katalog.hausartikel")
        }
        self.assertEqual(
            arten,
            {
                "101001": ["Monatsinventur"],
                "102001": ["Monatsinventur"],
                "101002": ["Jahresinventur"],
                "101003": ["Becherinventur"],
            },
        )

    def test_die_arten_stehen_vor_den_artikeln(self):
        """As 'dumpdata' sorts it: the rows name them by their name."""
        modelle = [satz["model"] for satz in self.neu]
        self.assertLess(modelle.index("katalog.inventurart"), modelle.index("katalog.hausartikel"))

    def test_jede_inventur_zaehlt_was_sie_zaehlte(self):
        """Month and year take the own types that counted along, the own one only itself."""
        arten: dict[int, list[str]] = {}
        for felder in self.von("inventur.inventurinventurart"):
            arten.setdefault(felder["inventur"], []).append(felder["inventurart"][0])
        self.assertEqual(
            arten,
            {
                1: ["Monatsinventur", "Wochenzählung"],
                2: ["Monatsinventur", "Jahresinventur", "Wochenzählung"],
                3: ["Becherinventur"],
            },
        )

    def test_die_entscheidungen_gelten_einer_art(self):
        """A decision "out of the type" said nothing about where to. One on the scope does."""
        self.assertCountEqual(
            [
                (felder["inventur"], felder["ware"], felder["inventurart"])
                for felder in self.von("inventur.artentscheidung")
            ],
            [(1, ["Mehrwegbecher"], ["Becherinventur"]), (1, ["Artikel C"], ["Jahresinventur"])],
        )

    def test_von_zwei_entscheidungen_zur_selben_ware_gilt_die_spaetere(self):
        stand = alter_stand()
        stand.append(
            {
                "model": "inventur.umfangsentscheidung",
                "pk": 2,
                "fields": {"inventur": 1, "ware": ["Mehrwegbecher"], "umfang": "NUR_JAHR"},
            }
        )
        neu = json.loads(umschreiben(als_text(stand)))
        becher = [
            satz["fields"]["inventurart"]
            for satz in neu
            if satz["model"] == "inventur.artentscheidung"
            and satz["fields"]["ware"] == ["Mehrwegbecher"]
        ]
        self.assertEqual(becher, [["Jahresinventur"]])

    def test_vom_alten_format_bleibt_nichts(self):
        alte_felder = {
            "katalog.ware": {"inventurumfang"},
            "katalog.inventurart": {"umfang", "waren"},
            "inventur.inventur": {"typ", "inventurart"},
            "inventur.artentscheidung": {"aktion"},
        }
        self.assertNotIn("inventur.umfangsentscheidung", {satz["model"] for satz in self.neu})
        for satz in self.neu:
            with self.subTest(modell=satz["model"]):
                self.assertFalse(alte_felder.get(satz["model"], set()) & set(satz["fields"]))

    def test_ohne_jahresware_keine_jahresinventur(self):
        """As in the migration: the annual inventory arises only where something needs it."""
        stand = [
            satz
            for satz in alter_stand()
            if satz["model"] not in ("inventur.inventur", "inventur.umfangsentscheidung")
            and satz["model"] != "inventur.artentscheidung"
            and satz["fields"].get("name") != "Ware J"
            and satz["fields"].get("ware") != ["Ware J"]
        ]
        neu = json.loads(umschreiben(als_text(stand)))
        namen = [s["fields"]["name"] for s in neu if s["model"] == "katalog.inventurart"]
        self.assertEqual(namen, ["Monatsinventur", "Becherinventur", "Wochenzählung"])

    @override_settings(LANGUAGE_CODE="en")
    def test_die_arten_heissen_in_der_sprache_des_servers(self):
        """As the migration names them on a server that speaks English."""
        neu = json.loads(umschreiben(als_text(alter_stand())))
        namen = [s["fields"]["name"] for s in neu if s["model"] == "katalog.inventurart"]
        self.assertEqual(namen[:2], ["Monthly inventory", "Annual inventory"])

    def test_die_versuche_am_koeder_bekommen_ihr_neues_etikett(self):
        """Until 10/2026 they were the package's model. Same table, new label."""
        alt = [
            {
                "model": "admin_honeypot.loginattempt",
                "pk": 1,
                "fields": {"username": "x", "ip_address": "10.0.0.1", "timestamp": ZEIT},
            },
            {"model": "organisation.haus", "pk": 1, "fields": {"name": "Kino Nord"}},
        ]
        neu = json.loads(umschreiben(als_text(alt)))
        self.assertEqual(
            [satz["model"] for satz in neu], ["accounts.anmeldeversuch", "organisation.haus"]
        )
        self.assertEqual(neu[0]["fields"]["username"], "x")

    def test_ein_neuer_stand_bleibt_wie_er_ist(self):
        neu = umschreiben(als_text(alter_stand()))
        self.assertIs(umschreiben(neu), neu)

    def test_was_kein_stand_ist_bleibt_wie_es_ist(self):
        """What is wrong with it is for 'loaddata' to say."""
        for inhalt in (b"", b"kein json", b'{"model": "katalog.ware"}', "Ä".encode("latin-1")):
            with self.subTest(inhalt=inhalt):
                self.assertIs(umschreiben(inhalt), inhalt)

    def test_zwei_inventuren_am_selben_tag_werden_zur_meldung(self):
        """From now on a house has one per day. Which goes is a person's decision."""
        stand = alter_stand()
        stand.append(inventur(4, 1, "EIGEN", "2026-08-31", art="Becherinventur"))
        with self.assertRaisesMessage(ValidationError, "Kino Nord am 31.08.2026"):
            umschreiben(als_text(stand))


def ergebnis(pk: int, hausartikel: int) -> dict:
    """A frozen result as the application wrote it until 10/2026: without a unit."""
    return {
        "model": "inventur.inventurergebnis",
        "pk": pk,
        "fields": {
            "inventur": 1,
            "hausartikel": hausartikel,
            "gesamtmenge_basiseinheit": "10.000",
            "sollbestand": None,
            "differenz": None,
            "notiz": "",
            "eingefroren_am": ZEIT,
        },
    }


class ErgebnisseOhneEinheit(SimpleTestCase):
    """Since 10/2026 a result carries its unit ('inventur/0002'). An older one gets its article's.

    In the file the results stand before the house articles, as 'dumpdata'
    writes them. The receiver at loading would not find the article yet.
    """

    def test_die_einheit_und_der_faktor_des_artikels(self):
        stand = [ergebnis(1, 1), *artikel(1, 1, "Artikel C", "101001")]
        stand[2]["fields"] |= {"basiseinheit": ["kg"], "faktor_zur_ware": "0.500000000000"}

        felder = json.loads(umschreiben(als_text(stand)))[0]["fields"]

        self.assertEqual(felder["einheit"], ["kg"])
        self.assertEqual(felder["faktor_zur_ware"], "0.500000000000")

    def test_vor_09_2026_die_der_ware(self):
        """Then the article had no unit of its own ('katalog/apps.py', 'einheit_nachtragen')."""
        stand = [
            {"model": "katalog.ware", "fields": {"name": "Artikel C", "basiseinheit": ["kg"]}},
            ergebnis(1, 1),
            *artikel(1, 1, "Artikel C", "101001"),
        ]
        del stand[3]["fields"]["basiseinheit"]

        felder = json.loads(umschreiben(als_text(stand)))[1]["fields"]

        self.assertEqual(felder["einheit"], ["kg"])

    def test_ohne_den_artikel_im_stand_bleibt_es_dem_empfaenger(self):
        """A part of a backup, for a catalog that is already there ('inventur/apps.py')."""
        felder = json.loads(umschreiben(als_text([ergebnis(1, 7)])))[0]["fields"]

        self.assertNotIn("einheit", felder)

    def test_ein_ergebnis_mit_einheit_bleibt_wie_es_ist(self):
        stand = [ergebnis(1, 1), *artikel(1, 1, "Artikel C", "101001")]
        stand[0]["fields"] |= {"einheit": ["Stück(e)"], "faktor_zur_ware": None}
        inhalt = als_text(stand)

        self.assertIs(umschreiben(inhalt), inhalt)


class Einspielen(ImBackupOrdner):
    """Through the administration: afterwards the database says what the old state said.

    Encrypted, as the administration wrote it with the key. An unencrypted one
    it does not restore while a key is set ('backup.pruefe_verschluesselt').
    """

    def stand(self, saetze: list[dict] | None = None) -> backup.Stand:
        pfad = Path(self.ordner.name) / "stand_2026-10-01_12-00-00.json.gz.enc"
        backup.schreiben(als_text(alter_stand() if saetze is None else saetze), pfad)
        return backup.Stand(pfad)

    def test_arten_artikel_und_inventuren_kommen_an(self):
        backup.einspielen(self.stand())

        self.assertEqual(
            list(Inventurart.objects.values_list("name", flat=True)),
            ["Monatsinventur", "Jahresinventur", "Becherinventur", "Wochenzählung"],
        )
        arten = dict(Hausartikel.objects.values_list("artikelnummer", "inventurart__name"))
        self.assertEqual(arten["101002"], "Jahresinventur")
        self.assertEqual(arten["101003"], "Becherinventur")
        jahr = Inventur.objects.get(pk=2)
        self.assertEqual(jahr.arten_anzeige, "Monatsinventur + Jahresinventur + Wochenzählung")
        self.assertEqual(Inventur.objects.get(pk=3).arten_anzeige, "Becherinventur")
        self.assertEqual(
            Artentscheidung.objects.get(ware__name="Artikel C").inventurart.name, "Jahresinventur"
        )

    def test_die_datei_bleibt_wie_sie_ist(self):
        """Rewritten in memory only. Whoever needs the old state still has it."""
        stand = self.stand()
        vorher = stand.pfad.read_bytes()

        backup.einspielen(stand)

        self.assertEqual(stand.pfad.read_bytes(), vorher)
        self.assertEqual(sorted(p.name for p in Path(self.ordner.name).glob("einspielen_*")), [])

    def test_zwei_am_selben_tag_lassen_alles_wie_es_ist(self):
        """Said before the safety copy and before emptying."""
        saetze = alter_stand()
        saetze.append(inventur(4, 1, "EIGEN", "2026-08-31", art="Becherinventur"))
        stand = self.stand(saetze)
        art = Inventurart.objects.create(name="Bleibt")

        with self.assertRaisesMessage(ValidationError, "nur eine Inventur"):
            backup.einspielen(stand)

        self.assertTrue(Inventurart.objects.filter(pk=art.pk).exists())
        self.assertEqual(len(backup.staende()), 1)


@override_settings(LANGUAGE_CODE="de")
class Lesen(SimpleTestCase):
    """'manage.py backup read' for the development scripts: the same state comes out.

    The files are unencrypted, so the call says so ('--plaintext'). Without it
    the command refuses them while a key is set, as it is in the tests.
    """

    def setUp(self):
        ordner = tempfile.TemporaryDirectory()
        self.addCleanup(ordner.cleanup)
        self.ordner = Path(ordner.name)

    def lesen(self, pfad: Path) -> bytes:
        with (self.ordner / "heraus").open("w+b") as ziel:
            with mock.patch.object(sys, "stdout", Strom(ziel)):
                call_command("backup", "read", "--plaintext", str(pfad), stdout=io.StringIO())
            ziel.seek(0)
            return ziel.read()

    def test_ein_alter_stand_kommt_im_heutigen_format(self):
        pfad = self.ordner / "stand_2026-10-01_12-00-00.json"
        pfad.write_bytes(als_text(alter_stand()))

        heraus = self.lesen(pfad)

        self.assertEqual(heraus, umschreiben(pfad.read_bytes()))
        self.assertNotIn(b"inventurumfang", heraus)

    def test_was_kein_stand_ist_geht_durch(self):
        """An image of the database, say: no '.json' in the name."""
        pfad = self.ordner / "abbild.sql"
        pfad.write_bytes(b'[{"model": "katalog.ware", "fields": {"inventurumfang": "NUR_JAHR"}}]')

        self.assertEqual(self.lesen(pfad), pfad.read_bytes())
