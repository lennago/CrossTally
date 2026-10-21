"""The contract of the demo: it has to keep up with the application.

These tests are the reason the demo lies in the repo and not as a finished
database somewhere next to it. Whoever changes a model, rebuilds a service
or adds a feature notices here that the demo stock has to be updated.

It is not checked that certain numbers come out. It is checked that the
demo **shows** what it promises: every block of the clarification has a
question, the target clarification its cases, the closed runs their
results, and the bundled files can be read in.
"""

import io
import re
import tempfile
from datetime import date, timedelta
from decimal import Decimal
from io import StringIO
from unittest import mock

import openpyxl
from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db.models import Count, F
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone, translation
from rest_framework.test import APIClient

from accounts.models import CustomUser, Passkey
from accounts.test_notification import formular
from accounts.test_passkey import Authenticator
from api.checks import pruefe_nicht_aktiv
from api.serializers import IchSerializer, erwartete_mengen
from django_project import backup
from django_project.testing import skript
from inventur import closing, services
from inventur.evaluation import familien_eines_laufs, warengruppen_einer_inventur
from inventur.forms import ohne_artikel_der_arten
from inventur.models import (
    Inventur,
    Inventurergebnis,
    Inventurstatus,
    Sollbestand,
    Sollherkunft,
    Zaehlposition,
)
from inventur.pdf import pdf_html
from inventur.views import Lauf
from katalog.family_tree import flach
from katalog.formats.winticket import WinTicket
from katalog.forms import (
    WIEDER_AKTIVIEREN,
    BaseZaehllisteFormSet,
    HausartikelFilterForm,
    ZaehllisteFormSet,
    ZaehllisteZeileForm,
)
from katalog.models import (
    QUELLE_EIGEN,
    Artikelstatus,
    Basiseinheit,
    Behaelter,
    Dimension,
    Erwartung,
    ErwartungGebinde,
    Gebinde,
    Hausartikel,
    Inventurart,
    Rezepturartikel,
    Ware,
    Warenfamilie,
    Warengruppe,
    arten_je_haus,
    portion_abgekuerzt,
)
from katalog.ort_copy import kopieren
from katalog.services import (
    EINGESTELLT,
    FEHLT,
    KONFLIKT,
    NEU,
    UMBENANNT,
    als_daten,
    ausfuehren,
    planen,
)
from katalog.ware_merge import dubletten, zusammenfuehren
from katalog.ware_split import aufteilen
from katalog.ware_unit_change import einheit_wechseln
from organisation.forms import MailtextForm
from organisation.mail_texts import Mailart
from organisation.models import STANDARDNAME, Erscheinungsbild, Haus, Mailtext, Ort
from scripts.loader import get_artikel_und_warengruppen
from scripts.normalize import STUECK

from . import branding, builder, catalog, count_sheet, houses, inventories, names_en, users
from .files import BESCHREIBUNGEN, datei
from .houses import NORD, OST, SUED, WEST
from .models import Demokennzeichen
from .state import ist_demobestand, kennzeichnen

#: This many digits an article number has in the inventory management system.
NUMMERNLAENGE = 6

#: A fixed day, so the cut-off dates in the test do not move with the calendar.
AUFBAUTAG = date(2026, 9, 20)


class DemoBestand(TestCase):
    """Everything a once-built stock has to yield."""

    bericht: builder.Aufbaubericht

    @classmethod
    def setUpTestData(cls):
        cls.bericht = builder.aufbauen(AUFBAUTAG)

    def laufende(self, hausname: str) -> Inventur:
        """The running run at the month-end. The cup inventory of Kino Nord runs too."""
        return Inventur.objects.get(
            haus__name=hausname, stichtag=inventories.stichtage(AUFBAUTAG).laufend
        )

    def vorbereitete(self, hausname: str) -> Inventur:
        return Inventur.objects.get(haus__name=hausname, status=Inventurstatus.VORBEREITUNG)

    def test_vier_haeuser(self):
        self.assertEqual(
            sorted(Haus.objects.values_list("name", flat=True)),
            sorted([NORD, OST, WEST, SUED]),
        )

    def test_drei_konten_mit_je_einer_rolle(self):
        rollen = {
            konto.username: (konto.is_superuser, konto.is_staff)
            for konto in CustomUser.objects.filter(is_active=True)
        }
        self.assertEqual(
            rollen, {"admin": (True, True), "leitung": (False, True), "zaehler": (False, False)}
        )
        # Not through 'client.login': 'axes' needs a request for that. Here it
        # is only about the password from DEMO.md really being valid.
        self.assertTrue(CustomUser.objects.get(username="zaehler").check_password(users.PASSWORT))

    def test_jedes_konto_legt_mit_dem_passwort_aus_der_demo_einen_passkey_an(self):
        """DEMO.md names the password, and a new key asks for it, on the website and in the app."""
        for name in ("admin", "leitung", "zaehler"):
            konto = CustomUser.objects.get(username=name)
            with self.subTest(konto=name):
                self.client.force_login(konto)
                frage = self.client.post(
                    reverse("passkey_anlegen_optionen"),
                    {"passwort": users.PASSWORT},
                    content_type="application/json",
                )
                antwort = self.client.post(
                    reverse("passkey_anlegen"),
                    {"credential": Authenticator().registrierung(frage.json()["challenge"])},
                    content_type="application/json",
                )
                self.assertEqual(antwort.status_code, 200, antwort.content)

                app = APIClient()
                app.force_authenticate(konto)
                schritt = app.post(
                    reverse("passkey_anlegen_optionen_api", kwargs={"version": "v1"}),
                    {"passwort": users.PASSWORT},
                    format="json",
                ).data
                credential = Authenticator().registrierung(schritt["options"]["challenge"])
                antwort = app.post(
                    reverse("passkey_anlegen_api", kwargs={"version": "v1"}),
                    {"credential": credential, "challenge": schritt["challenge"]},
                    format="json",
                )
                self.assertEqual(antwort.status_code, 201, antwort.data)
        self.assertEqual(Passkey.objects.count(), 6)

    def test_ein_konto_wartet_auf_seine_einladung(self):
        """Created without a password: the list says "invited", the page offers the mail."""
        konto = CustomUser.objects.get(username=users.EINGELADEN)
        self.assertTrue(konto.ist_eingeladen)
        self.assertFalse(konto.is_active)
        self.assertFalse(konto.has_usable_password())
        self.client.force_login(CustomUser.objects.get(username="leitung"))
        self.assertContains(self.client.get(konto.get_absolute_url()), "Einladung erneut senden")

    def test_eine_aenderung_am_konto_kommt_als_mail(self):
        """Every account has an address: the notification lands in the log or in Mailpit."""
        zaehler = CustomUser.objects.get(username="zaehler")
        self.client.force_login(CustomUser.objects.get(username="leitung"))
        self.client.post(
            reverse("user_edit", args=[zaehler.pk]), formular(zaehler, last_name="Zaunkönig")
        )
        self.assertEqual(mail.outbox[-1].to, ["zaehler@example.org"])
        self.assertIn("Zaunkönig", mail.outbox[-1].body)

    def test_die_demo_zeigt_das_ausgelieferte_logo(self):
        """Like a fresh operation. The own logo one uploads in the demo oneself."""
        self.client.force_login(CustomUser.objects.get(username="leitung"))

        seite = self.client.get(reverse("inventur_list")).content.decode()

        self.assertTrue(Erscheinungsbild.aktuell().ist_standard)
        self.assertIn("logo.svg", seite)
        self.assertIn(STANDARDNAME, seite)
        self.assertNotIn(reverse("icon", kwargs={"name": "logo"}), seite)

    def test_ein_backup_der_demo_ist_verschluesselt(self):
        """DEMO.md recommends one before trying something that cannot be taken back."""
        with (
            tempfile.TemporaryDirectory() as ordner,
            override_settings(BACKUP_DIR=ordner, BACKUP_KEY="nur-fuer-die-demo-und-lang-genug"),
        ):
            stand = backup.erstellen()

            self.assertTrue(stand.verschluesselt)
            self.assertNotIn(NORD.encode(), stand.pfad.read_bytes())
            self.assertIn(NORD.encode(), stand.lesen())

    def test_das_mitgelieferte_logo_laesst_sich_hochladen(self):
        """The image for trying out passes the check of the administration.

        It is uploaded through the same form, and the derived sizes arise along
        with it. If something changes about the check that it no longer passes,
        it breaks here and not only when someone tries it.
        """
        branding.logo_setzen()
        self.client.force_login(CustomUser.objects.get(username="leitung"))

        erscheinungsbild = Erscheinungsbild.aktuell()
        seite = self.client.get(reverse("inventur_list")).content.decode()

        self.assertEqual(erscheinungsbild.name, branding.FASSUNG)
        self.assertEqual(erscheinungsbild.anwendungsname, branding.ANWENDUNGSNAME)
        self.assertIn(branding.ANWENDUNGSNAME, seite, "Der Name steht neben dem Logo")
        self.assertTrue(erscheinungsbild.hat_logo)
        self.assertFalse(Erscheinungsbild.standard().hat_logo, "Der Standard bleibt, wie er ist")
        for name in ("icon-192", "apple-touch-icon", "favicon-32", "icon-maskable-512"):
            with self.subTest(name=name):
                self.assertTrue(erscheinungsbild.bild(name))
        self.assertIn(reverse("icon", kwargs={"name": "logo"}), seite)

    def test_die_eigene_fassung_bringt_ein_impressum_mit(self):
        """The demo ships none, as a fresh operation. The version for trying out has one."""
        self.assertEqual(self.client.get(reverse("impressum")).status_code, 404)

        branding.logo_setzen()

        antwort = self.client.get(reverse("impressum"))
        self.assertContains(antwort, "Beispielkino GmbH<br>Musterweg 1<br>10101 Musterstadt")
        self.assertContains(self.client.get(reverse("login")), reverse("impressum"))

    def test_die_eigene_fassung_nennt_eine_adresse_fuer_rueckmeldungen(self):
        """The demo names none, as a fresh operation. The version for trying out does (DEMO.md)."""
        self.client.force_login(CustomUser.objects.get(username="leitung"))
        self.assertNotContains(self.client.get(reverse("inventur_list")), "mailto:")

        branding.logo_setzen()

        antwort = self.client.get(reverse("inventur_list"))
        self.assertContains(antwort, "Problem oder Wunsch melden")
        self.assertContains(antwort, f"mailto:{branding.RUECKMELDEADRESSE}?")

    def test_ein_eigener_mailtext_reicht_bis_zur_einladung(self):
        """DEMO.md: change the invitation in the administration, send it again, read the log."""
        konto = CustomUser.objects.get(username=users.EINGELADEN)
        Mailtext.alle_anlegen()
        mailtext = Mailtext.objects.get(art=Mailart.EINLADUNG)
        formular = MailtextForm(instance=mailtext)
        daten = {name: formular[name].value() for name in formular.fields}
        daten["text"] = "Hallo {{ vorname }}, hier entlang: {{ link }}\n\nDein Kinoteam"
        formular = MailtextForm(daten, instance=mailtext)
        self.assertTrue(formular.is_valid(), formular.errors)
        formular.save()

        self.client.force_login(CustomUser.objects.get(username="leitung"))
        self.client.post(reverse("user_einladen", args=[konto.pk]))

        (nachricht,) = mail.outbox
        self.assertIn("Dein Kinoteam", nachricht.body)
        self.assertIn("/accounts/einladung/", nachricht.body)

    def test_zuruecksetzen_nimmt_ein_hochgeladenes_logo_mit(self):
        """Otherwise the fresh demo would wear the face of the last try-out."""
        branding.logo_setzen()

        builder.leeren()

        self.assertTrue(Erscheinungsbild.aktuell().ist_standard)
        self.assertFalse(Erscheinungsbild.objects.filter(name=branding.FASSUNG).exists())

    def test_zwei_haeuser_sind_eingepflegt(self):
        for hausname in (NORD, OST):
            with self.subTest(hausname):
                haus = Haus.objects.get(name=hausname)
                self.assertGreater(Hausartikel.objects.filter(haus=haus).count(), 40)
                self.assertGreater(Erwartung.objects.filter(ort__bereich__haus=haus).count(), 40)

    def test_kino_west_ist_leer(self):
        """The third house is set up in the demo itself, through the import."""
        haus = Haus.objects.get(name=WEST)
        self.assertFalse(Hausartikel.objects.filter(haus=haus).exists())
        self.assertFalse(haus.bereiche.exists())

    def test_jeder_artikelstatus_kommt_vor(self):
        """Active, inactive, discontinued and not countable. Otherwise the demo lacks a case."""
        vorhanden = set(Hausartikel.objects.values_list("status", flat=True))
        self.assertEqual(len(vorhanden), 4, f"Gefunden: {sorted(vorhanden)}")

    def test_eine_ware_laesst_sich_auftrennen(self):
        """Both houses carry the same products. Exactly on that the splitting hangs.

        Played through instead of claimed: the packs of the new product arise in
        the process, and the references of the migrated house articles follow
        along ('katalog/ware_split.py').
        """
        ware = (
            Ware.objects.annotate(haeuser=Count("hausartikel__haus", distinct=True))
            .filter(haeuser__gte=2, gebinde__isnull=False)
            .distinct()
            .first()
        )
        assert ware is not None, "Ohne Ware in zwei Häusern hat die Demo nichts aufzuteilen."
        wandern = list(ware.hausartikel.filter(haus__name=OST))
        self.assertTrue(wandern, f"„{ware.name}“ führt Kino Ost nicht.")

        bericht = aufteilen(ware, ware.name, f"{ware.name} (Kino Ost)", wandern)

        self.assertEqual(bericht.gewandert, len(wandern))
        self.assertGreater(bericht.gebinde, 0)
        self.assertEqual(bericht.neu.gebinde.count(), ware.gebinde.count())
        for vorgabe in ErwartungGebinde.objects.filter(
            erwartung__artikel__in=[satz.pk for satz in wandern]
        ):
            vorgabe.full_clean()

    def test_ein_ort_laesst_sich_in_das_andere_haus_kopieren(self):
        """Both houses carry much the same, but not everything. Both cases have to occur.

        Played through instead of claimed: the copy expects the articles of the
        target house, and what that house does not carry is named.
        """
        ort = Ort.objects.get(name="Theke links", bereich__haus__name=NORD)
        ziel = Haus.objects.get(name=OST).bereiche.order_by("name")[0]

        bericht = kopieren(ort, ziel, "Theke links (Kopie)")

        self.assertGreater(bericht.kopiert, 0)
        self.assertTrue(bericht.ausgelassen, "Kino Ost führt alles, was an der Theke links steht.")
        for erwartung in Erwartung.objects.filter(ort=bericht.neu):
            erwartung.full_clean()
        for vorgabe in ErwartungGebinde.objects.filter(erwartung__ort=bericht.neu):
            vorgabe.full_clean()

    def test_waren_mit_gebinden_wiegen_und_rezeptur(self):
        self.assertTrue(Ware.objects.filter(abwiegbar=True).exists())
        self.assertGreater(self.bericht.rezepturartikel, 0)
        self.assertTrue(
            Erwartung.objects.filter(artikel__rezepturartikel__isnull=False).exists(),
            "Rezepturartikel gehören in eine Zählliste, sonst sieht man sie nie.",
        )

    def test_anteilige_rezeptur_aus_sirupen_in_litern(self):
        """The house lemonade states percent, the syrups carry their grams per litre."""
        mischung = Rezepturartikel.objects.get(name="Sirupmischung Hauslimo")
        self.assertTrue(mischung.anteilig and mischung.wird_gewogen)
        komponenten = list(mischung.komponenten.select_related("ware__basiseinheit"))
        self.assertEqual(sum(k.menge_in_basiseinheit for k in komponenten), Decimal("100"))
        self.assertTrue(
            all(k.ware.basiseinheit.dimension == Dimension.VOLUMEN for k in komponenten)
        )
        for komponente in komponenten:
            komponente.full_clean()
        # 1240 g per litre: 40 % of 1240 g of mix is exactly 0.4 l of cola syrup.
        verteilt = mischung.verteilen(Decimal("1240"))
        self.assertEqual(verteilt[Ware.objects.get(name="Colasirup")], Decimal("0.4"))
        self.assertTrue(
            Erwartung.objects.filter(artikel=mischung).exists(),
            "Die Mischung gehört in eine Zählliste, sonst sieht man sie nie.",
        )

    def test_eine_zutat_der_mischung_wechselt_die_einheit(self):
        """Fruit gums from kg to g: the share stays 50 %, and 1000 g of the mix hold 500 g.

        Until 10/2026 the change made 50,000 % of it, and the same 1000 g held
        500,000 g of fruit gums.
        """
        mischung = Rezepturartikel.objects.get(name="Süßwarenmischung")
        fruchtgummi = Ware.objects.get(name="Fruchtgummi lose")
        self.assertEqual(fruchtgummi.basiseinheit.name, "kg")
        self.assertEqual(mischung.verteilen(Decimal("1000"))[fruchtgummi], Decimal("0.5"))

        einheit_wechseln(fruchtgummi, Basiseinheit.objects.get(name="g"))

        mischung = Rezepturartikel.objects.get(pk=mischung.pk)
        anteile = {k.ware.name: k.menge_in_basiseinheit for k in mischung.komponenten.all()}
        self.assertEqual(anteile["Fruchtgummi lose"], Decimal("50"))
        Rezepturartikel.anteile_pruefen(anteile.values())
        self.assertEqual(mischung.verteilen(Decimal("1000"))[fruchtgummi], Decimal("500"))

    def test_eine_ware_traegt_ihre_umrechnungen(self):
        """The bottle knows its millilitres for the family and its grams for the scale."""
        cola = Ware.objects.get(name="Cola 0,5 l")
        self.assertEqual(
            cola.faktoren(),
            {Dimension.VOLUMEN: Decimal("500"), Dimension.MASSE: Decimal("520")},
        )
        familie = cola.warenfamilie
        assert familie is not None and familie.leiteinheit is not None
        self.assertEqual(familie.leiteinheit.name, "Liter")
        self.assertEqual(familie.waren_ohne_faktor(), [])
        # Every family can sum every product it carries.
        for familie in Warenfamilie.objects.all():
            with self.subTest(familie=familie.name):
                self.assertEqual(familie.waren_ohne_faktor(), [])

    def test_familien_stehen_in_familien(self):
        """Two families above others, each summing the whole subtree without a gap."""
        getraenke = Warenfamilie.objects.get(name="Getränke")
        snacks = Warenfamilie.objects.get(name="Snacks")
        self.assertEqual(getraenke.unterfamilien.count(), 3)
        self.assertEqual(snacks.unterfamilien.count(), 5)
        self.assertEqual(getraenke.waren_ohne_faktor(), [])
        self.assertEqual(snacks.waren_ohne_faktor(), [])
        self.assertGreater(getraenke.anzahl_aller_waren, getraenke.anzahl_waren)
        for familie in getraenke.unterfamilien.all():
            self.assertEqual(familie.summendimensionen()[0], Dimension.VOLUMEN)

    def test_ein_haus_zaehlt_in_seiner_eigenen_einheit(self):
        """Two cases: g against kg computes by itself, pieces against kg needs the factor."""
        ost = Haus.objects.get(name=OST)
        chips = Hausartikel.objects.get(haus=ost, artikelnummer="102201")
        self.assertEqual(chips.basiseinheit.name, "g")
        self.assertEqual(chips.ware.basiseinheit.name, "kg")
        self.assertIsNone(chips.faktor_zur_ware)
        bohnen = Hausartikel.objects.get(haus=ost, artikelnummer="102501")
        self.assertEqual(bohnen.basiseinheit.name, "Stück(e)")
        self.assertEqual(bohnen.faktor_zur_ware, Decimal("1"))
        # The house's result is in its unit, in grams thus four digits.
        ergebnis = Inventurergebnis.objects.filter(hausartikel=chips).first()
        self.assertIsNotNone(ergebnis)
        assert ergebnis is not None
        self.assertGreater(ergebnis.gesamtmenge_basiseinheit, 1000)

    def test_ein_abgeschlossenes_ergebnis_behaelt_seine_einheit(self):
        """Kino Ost counts the beans in bags since the running run. Closed results stay in kg."""
        bohnen = Hausartikel.objects.get(haus__name=OST, artikelnummer="102501")
        abgeschlossen = Inventur.objects.filter(
            haus__name=OST, status=Inventurstatus.ABGESCHLOSSEN
        ).latest("stichtag")
        (blatt,) = [
            zeile
            for block in warengruppen_einer_inventur(abgeschlossen)
            for zeile in block.zeilen
            if zeile.nummer == "102501"
        ]
        self.assertRegex(blatt.ist_text, r"^[\d,]+ kg$")
        laufend = next(
            zeile
            for zeile in services.ergebniszeilen(self.laufende(OST))
            if zeile.hausartikel == bohnen
        )
        self.assertEqual(laufend.einheit.name, "Stück(e)")
        # Every other result is in the unit its article counts in today.
        abweichend = {
            ergebnis.hausartikel.artikelnummer
            for ergebnis in Inventurergebnis.objects.select_related("hausartikel")
            if (ergebnis.einheit_id, ergebnis.faktor_zur_ware)
            != (ergebnis.hausartikel.basiseinheit_id, ergebnis.hausartikel.faktor_zur_ware)
        }
        self.assertEqual(abweichend, {"102501"})

    # -- The run that is still open. The demo's purpose hangs on it. --------

    def test_ein_haus_fertig_das_andere_angefangen(self):
        nord = self.laufende(NORD)
        ost = self.laufende(OST)
        self.assertGreater(self._gezaehlt(nord), self._gezaehlt(ost) * 2)
        self.assertGreater(self._gezaehlt(ost), 0, "In Kino Ost ist ein Ort schon gezählt.")
        self.assertLess(
            self._gezaehlt(ost),
            ost.zaehlpositionen.count(),
            "In Kino Ost darf nicht alles gezählt sein.",
        )

    @staticmethod
    def _gezaehlt(inventur: Inventur) -> int:
        return inventur.zaehlpositionen.filter(gezaehlt_am__isnull=False, aktiv=True).count()

    def test_klaerung_hat_in_jedem_block_eine_frage(self):
        """The actual purpose: at the closing of Kino Nord all seven can be seen."""
        klaerung = closing.klaeren(self.laufende(NORD))
        bloecke = {
            "nicht mehr aktiv, aber gezählt": klaerung.wiederaufnahmen,
            "unerwartet gezählt": klaerung.neue_erwartungen,
            "erwartet, nichts eingetragen": klaerung.ungezaehlte_gebinde,
            "gar nicht gezählt": klaerung.ungezaehlte_artikel,
            "gezählt ohne Soll": klaerung.ohne_soll,
            "gezählt, aber in keiner Art der Inventur": klaerung.ausserhalb,
            "fremder Behälter": klaerung.fremde_behaelter,
        }
        for name, eintraege in bloecke.items():
            with self.subTest(name):
                self.assertTrue(eintraege, f"Der Block „{name}“ bliebe leer.")
        # The numbers DEMO.md names.
        self.assertEqual(len(klaerung.wiederaufnahmen), 4)
        self.assertEqual(len(klaerung.ohne_soll), 3)
        self.assertEqual(len(klaerung.ausserhalb), 3)
        self.assertEqual(len(klaerung.fremde_behaelter), 1)

    def test_fuenfzehn_warenfamilien(self):
        """The number DEMO.md names. The next family reminds whoever adds it."""
        self.assertEqual(len(catalog.FAMILIEN), 15)
        self.assertEqual(Warenfamilie.objects.filter(sammelt_ignorierte=False).count(), 15)

    def test_jeder_rezepturartikel_steht_in_einer_zaehlliste(self):
        """Until 10/2026 the hot dog stood on no list and was counted nowhere."""
        for artikel in Rezepturartikel.objects.all():
            with self.subTest(artikel=artikel.name):
                self.assertTrue(Erwartung.objects.filter(artikel_id=artikel.pk).exists())

    def test_kein_soll_behauptet_eine_datei_ohne_zeile_darin(self):
        """The old runs had no file. Until 10/2026 their rows claimed one, with an empty name."""
        self.assertTrue(Sollbestand.objects.filter(herkunft=Sollherkunft.DATEI).exists())
        self.assertFalse(
            Sollbestand.objects.filter(herkunft=Sollherkunft.DATEI, name_in_datei="").exists()
        )

    def test_jede_gezaehlte_zeile_ist_fuer_die_app_sichtbar(self):
        """The app fetches what changed since its last sync. Until 10/2026 the demo wrote past."""
        veraltet = Zaehlposition.objects.filter(
            gezaehlt_am__isnull=False, zuletzt_geaendert__lt=F("gezaehlt_am")
        )
        self.assertEqual(list(veraltet.values_list("pk", flat=True)), [])

    def test_jeder_zeitstempel_haengt_am_aufbautag(self):
        """Until 10/2026 every run said "created" on the build's day, after its closing."""
        for inventur in Inventur.objects.filter(status=Inventurstatus.ABGESCHLOSSEN):
            with self.subTest(inventur=str(inventur)):
                assert inventur.abgeschlossen_am is not None
                self.assertLess(inventur.erstellt_am, inventur.abgeschlossen_am)
        laufend = Inventur.objects.get(
            haus__name=NORD, stichtag=inventories.stichtage(AUFBAUTAG).laufend
        )
        gezaehlt = laufend.zaehlpositionen.filter(gezaehlt_am__isnull=False)
        self.assertTrue(gezaehlt.exists())
        for position in gezaehlt:
            self.assertEqual(timezone.localtime(position.gezaehlt_am).date(), laufend.stichtag)
        ausgeschieden = Hausartikel.objects.filter(
            artikelnummer="101013", gueltig_bis__isnull=False
        ).get()
        assert ausgeschieden.gueltig_bis is not None
        self.assertEqual(
            timezone.localtime(ausgeschieden.gueltig_bis).date(), AUFBAUTAG - timedelta(days=180)
        )

    def test_die_nachtraege_entstanden_wie_in_der_app(self):
        """Every added row passed the API's check, or its article left the range after the count."""
        inventur = self.laufende(NORD)
        zeilen = inventur.zaehlpositionen.filter(unerwartet=True).select_related("artikel")
        self.assertTrue(zeilen.exists())
        for position in zeilen:
            artikel = position.artikel.konkret()
            assert isinstance(artikel, Hausartikel)
            with self.subTest(artikel=artikel.artikelnummer):
                if artikel.artikelnummer in houses.SPAETER_AUSGESCHIEDEN:
                    self.assertFalse(artikel.aktiv)
                    self.assertIsNotNone(artikel.gueltig_bis)
                    self.assertIsNotNone(position.gezaehlt_am)
                    self.assertGreater(artikel.gueltig_bis, position.gezaehlt_am)
                else:
                    pruefe_nicht_aktiv(artikel, inventur)

    def test_die_warenwirtschaft_hat_den_hoernchen_eine_neue_nummer_gegeben(self):
        """After the running count. Until 10/2026 the old number was a row written past the app."""
        nord = Haus.objects.get(name=NORD)
        laufend, vorbereitet = self.laufende(NORD), self.vorbereitete(NORD)
        for neu, alt in houses.NEU_VERGEBEN.items():
            with self.subTest(neu=neu):
                neuer = Hausartikel.objects.get(haus=nord, artikelnummer=neu)
                alter = Hausartikel.objects.get(haus=nord, artikelnummer=alt)
                self.assertTrue(neuer.aktiv)
                self.assertEqual(alter.status, Artikelstatus.EINGESTELLT)
                # That it counts as the new one is the clarification's to answer.
                self.assertIsNone(alter.zaehlt_als)
                self.assertEqual(alter.blockiert_durch(), neuer)
                self.assertNotEqual(alter.name, neuer.name)
                # The old row stood on the list and was counted. The new one took its places.
                gezaehlt = laufend.zaehlpositionen.filter(
                    artikel_id=alter.pk, gezaehlt_am__isnull=False, unerwartet=False
                )
                self.assertTrue(gezaehlt.exists())
                self.assertFalse(Erwartung.objects.filter(artikel_id=alter.pk).exists())
                self.assertTrue(Erwartung.objects.filter(artikel_id=neuer.pk).exists())
                # The file names the old number: in the running run that is its target,
                # in the prepared one a question that names the new row.
                self.assertTrue(laufend.sollbestaende.filter(hausartikel=alter).exists())
                zeile = vorbereitet.sollzeilen.get(artikelnummer=alt)
                klaerung = services.sollklaerung(vorbereitet)
                self.assertEqual(klaerung.nachfolger.get(zeile.pk), neuer)
                self.assertNotIn(neuer, klaerung.ohne_soll)

    def test_die_alte_nummer_der_hoernchen_kommt_zur_neuen(self):
        """The answer in the prepared run. Afterwards the file asks no more."""
        vorbereitet = self.vorbereitete(NORD)
        for neu, alt in houses.NEU_VERGEBEN.items():
            with self.subTest(neu=neu):
                zeile = vorbereitet.sollzeilen.get(artikelnummer=alt)
                neuer = services.sollzeile_dazurechnen(zeile)
                self.assertEqual(neuer.artikelnummer, neu)
                soll = vorbereitet.sollbestaende.get(hausartikel=neuer)
                self.assertEqual(soll.menge_basiseinheit, zeile.menge_basiseinheit)

                inventories.sollbestand_hochladen(
                    vorbereitet, inventories.SOLLDATEI[houses.NORD], names_en.Namen("de")
                )
                self.assertFalse(vorbereitet.sollzeilen.filter(artikelnummer=alt).exists())
                soll = vorbereitet.sollbestaende.get(hausartikel=neuer)
                self.assertEqual(soll.menge_basiseinheit, zeile.menge_basiseinheit)

    def test_ein_eingestellter_artikel_ist_gesperrt(self):
        """A case one otherwise rarely sees: the number has been reassigned."""
        wiederaufnahmen = closing.klaeren(self.laufende(NORD)).wiederaufnahmen
        self.assertTrue(
            any(eintrag.hinderung for eintrag in wiederaufnahmen),
            "Einer der gezählten Artikel darf nicht zurückkommen können.",
        )
        self.assertTrue(
            any(not eintrag.hinderung for eintrag in wiederaufnahmen),
            "Und einer muss sich wieder aktivieren lassen.",
        )

    def test_sollbestand_kam_aus_der_datei(self):
        nord = self.laufende(NORD)
        self.assertGreater(nord.sollbestaende.count(), 40)
        self.assertTrue(
            nord.sollbestaende.filter(name_in_datei__gt="").exists(),
            "Der Upload hält die Bezeichnung aus der Datei fest.",
        )

    def test_sollklaerung_hat_ihre_faelle(self):
        klaerung = services.sollklaerung(self.laufende(NORD))
        self.assertFalse(klaerung.leer)
        gruende = set(self.laufende(NORD).sollzeilen.values_list("grund", flat=True))
        self.assertEqual(
            gruende,
            {"UNBEKANNT", "INAKTIV", "EINGESTELLT"},
            "Alle drei Gründe sollen in der Klärung vorkommen.",
        )

    def test_eingestellter_mit_bestand_null_bleibt_still(self):
        """The file lists a discontinued article with 0. Nobody is asked, the 0 is stored."""
        nord = self.laufende(NORD)
        still = nord.sollbestaende.filter(hausartikel__aktiv=False, menge_basiseinheit=0)
        self.assertEqual(still.count(), 1)
        nummer = still.get().hausartikel.artikelnummer
        self.assertFalse(nord.sollzeilen.filter(artikelnummer=nummer).exists())
        self.assertFalse(nord.sollbestaende_im_umfang().filter(hausartikel__aktiv=False).exists())

    def test_abweichende_bezeichnung_faellt_auf(self):
        """The file names an article differently than the house. The clarification shows it."""
        nord = self.laufende(NORD)
        abweichend = [
            satz
            for satz in nord.sollbestaende.select_related("hausartikel")
            if satz.name_in_datei and satz.name_in_datei != satz.hausartikel.name
        ]
        self.assertTrue(abweichend)

    def test_abschliessen_geht_ohne_antwort(self):
        """Without a tick nothing changes, closing happens nonetheless."""
        nord = self.laufende(NORD)
        bericht = closing.abschliessen(nord, closing.Entscheidungen())
        nord.refresh_from_db()
        self.assertEqual(nord.status, Inventurstatus.ABGESCHLOSSEN)
        self.assertGreater(bericht.eingefroren, 0)

    def test_erwartete_mengen_gibt_es_nur_dort_wo_aufgefuellt_wird(self):
        """The counter keeps a refill list. In storage stands what was delivered.

        In the app that is the row "Expected: 24". If every row had it, the demo
        would look tidier than operation.
        """
        mit = ErwartungGebinde.objects.filter(standard_menge__isnull=False)
        ohne = ErwartungGebinde.objects.filter(standard_menge__isnull=True)
        self.assertTrue(mit.exists())
        self.assertGreater(ohne.count(), mit.count(), "Der Regelfall ist: keine Vorgabe.")
        self.assertFalse(
            mit.filter(erwartung__ort__bereich__name__in=("Lager", "Kühlung")).exists(),
            "Außerhalb der Theke wird nichts vorgegeben.",
        )

    def test_die_app_sieht_zeilen_mit_und_ohne_erwartete_menge(self):
        positionen = list(self.laufende(NORD).zaehlpositionen.all())
        mengen = erwartete_mengen(positionen)
        schluessel = [(p.ort_id, p.artikel_id, p.gebinde_id) for p in positionen]
        self.assertTrue(any(eintrag in mengen for eintrag in schluessel))
        self.assertTrue(any(eintrag not in mengen for eintrag in schluessel))

    def test_die_eiswuerfel_stehen_mit_ihrem_beutel_in_der_tiefkuehltruhe(self):
        """The blueprint names them there, beside the ice cream ('houses.py').

        Until 10/2026 the location expected only single pieces and cartons.
        The ice cubes carry nothing but a bag, so no expectation arose, and
        they stood on no list.
        """
        erwartung = Erwartung.objects.get(
            ort__name="Tiefkühltruhe", artikel__hausartikel__ware__name="Eiswürfel"
        )
        self.assertEqual(
            list(erwartung.gebinde_vorgaben.values_list("gebinde__name", flat=True)), ["Beutel"]
        )

    def test_die_app_sieht_einen_ort_an_dem_nichts_erwartet_wird(self):
        """No row of the inventory names it. The app has it from the master data."""
        nord = self.laufende(NORD)
        ort = Ort.objects.get(name="Aktionstisch", bereich__haus=nord.haus)
        self.assertFalse(Erwartung.objects.filter(ort=ort).exists())
        self.assertFalse(nord.zaehlpositionen.filter(ort=ort).exists())

        app = APIClient()
        app.force_authenticate(user=CustomUser.objects.get(username="zaehler"))
        antwort = app.get(reverse("inventur-stammdaten", args=["v1", nord.pk]))
        self.assertIn(
            {"id": ort.pk, "name": "Aktionstisch", "bereich": "Foyer"},
            [dict(eintrag) for eintrag in antwort.data["orte"]],
        )

    def test_die_suche_zeigt_dass_sie_nach_relevanz_ordnet(self):
        """A term whose hits the number would order differently, otherwise nobody sees it."""
        nord = Haus.objects.get(name=NORD)
        basis = Hausartikel.objects.filter(haus=nord)

        def namen(**angaben):
            formular = HausartikelFilterForm(angaben, haus=nord)
            return [artikel.ware.name for artikel in formular.anwenden(basis)]

        relevant = namen(q="popcorn")
        self.assertNotEqual(relevant, namen(q="popcorn", sortierung="nummer"))
        # The salt is called by the term. The fat only stands in the same group.
        self.assertLess(relevant.index("Popcornsalz"), relevant.index("Kokosfett"))

    def test_gewogen_wird_nur_wo_es_jemand_gesagt_hat(self):
        """The counters of the demo weigh, its store rooms do not. Both are said explicitly."""
        abwiegbar = Erwartung.objects.filter(artikel__hausartikel__ware__abwiegbar=True)
        self.assertTrue(abwiegbar.filter(wiegen_erwartet=True).exists())
        self.assertTrue(abwiegbar.filter(wiegen_erwartet=False).exists())
        # And a fresh one comes without it.
        self.assertFalse(Erwartung().wiegen_erwartet)

    def test_ein_lauf_steht_in_der_vorbereitung(self):
        """Without it target stock upload and target clarification could not be seen.

        Both exist only before the start of the count.
        """
        vorbereitung = Inventur.objects.filter(status=Inventurstatus.VORBEREITUNG)
        self.assertEqual(vorbereitung.count(), 3)
        for inventur in vorbereitung:
            with self.subTest(str(inventur)):
                self.assertGreater(inventur.sollbestaende.count(), 0)
                # The cinemas have a file with open questions, the workshop does not.
                self.assertEqual(services.sollklaerung(inventur).leer, inventur.haus.name == SUED)

    def test_die_eismanufaktur_hat_ihr_soll_ganz_von_hand(self):
        """A house without a file: the maintenance page shows nothing but rows "by hand"."""
        sued = self.vorbereitete(SUED)
        herkuenfte = set(sued.sollbestaende.values_list("herkunft", flat=True))
        self.assertEqual(herkuenfte, {Sollherkunft.HAND})
        self.assertEqual(sued.sollbestaende.count(), len(inventories.SOLL_VON_HAND_EIGEN[SUED]))
        self.assertEqual(services.sollpflege(sued).ohne, [])

    def test_die_eismanufaktur_fuehrt_eigene_artikel_ohne_nummer(self):
        """A house without an inventory management system: the source is "eigen", the number empty.

        The ice cream is the same product as in the cinema, in another unit. That
        is how the houses hang together through the product, without a system
        knowing both.
        """
        sued = Haus.objects.get(name=SUED)
        artikel = list(Hausartikel.objects.filter(haus=sued).select_related("ware", "basiseinheit"))
        self.assertGreaterEqual(len(artikel), 4)
        self.assertTrue(all(a.quelle == QUELLE_EIGEN and a.artikelnummer == "" for a in artikel))
        eis = next(a for a in artikel if a.ware.name == "Eis Vanille")
        self.assertEqual(eis.basiseinheit.name, "kg")
        self.assertTrue(
            Hausartikel.objects.filter(haus__name=NORD, ware=eis.ware).exists(),
            "Dieselbe Ware führt auch Kino Nord, dort mit Nummer.",
        )
        self.assertTrue(Erwartung.objects.filter(ort__bereich__haus=sued).exists())

    def test_ein_soll_von_hand_steht_in_der_vorbereitung(self):
        """Kino Ost entered the article the file does not know by hand.

        Kino Nord did not: there it stays in the clarification, so both paths can
        be seen.
        """
        nord, ost = self.vorbereitete(NORD), self.vorbereitete(OST)
        self.assertTrue(ost.sollbestaende.filter(herkunft=Sollherkunft.HAND).exists())
        self.assertFalse(nord.sollbestaende.filter(herkunft=Sollherkunft.HAND).exists())
        self.assertEqual(services.sollklaerung(ost).ohne_soll, [])
        self.assertEqual(len(services.sollklaerung(nord).ohne_soll), 1)

    def test_die_seiten_der_demo_lassen_sich_aufrufen(self):
        """An end-to-end pass through the interface, with the demo's stock.

        Catches the case that a page is not reachable at all for the state the
        demo leaves a house in. Exactly that was the reason for the run in
        preparation.
        """
        self.client.force_login(CustomUser.objects.get(username="admin"))
        laufend = self.laufende(NORD)
        vorbereitet = Inventur.objects.filter(status=Inventurstatus.VORBEREITUNG).first()
        fertig = Inventur.objects.filter(status=Inventurstatus.ABGESCHLOSSEN).first()
        assert vorbereitet is not None and fertig is not None
        west = Haus.objects.get(name=WEST)
        seiten = {
            "Inventurliste": reverse("inventur_list"),
            "Lauf": reverse("inventur_lauf", args=[laufend.stichtag.isoformat()]),
            "Arten ändern": reverse("inventur_arten", args=[vorbereitet.pk]),
            "Inventurarten": reverse("inventurart_list"),
            "Prüfansicht": reverse("inventur_pruefen", args=[laufend.pk]),
            "Abschluss": reverse("inventur_abschluss", args=[laufend.pk]),
            "Sollbestand klären": reverse("inventur_sollbestand", args=[vorbereitet.pk]),
            "Sollbestand pflegen": reverse("inventur_soll_pflegen", args=[vorbereitet.pk]),
            "Ergebnis": reverse("inventur_detail", args=[fertig.pk]),
            "PDF": reverse("inventur_pdf", args=[fertig.pk]),
            "Auswertung": reverse("auswertung"),
            "Import ins leere Haus": reverse("hausartikel_import", args=[west.pk]),
            "Waren": reverse("ware_list"),
            "Rezepturartikel": reverse("rezepturartikel_list"),
            "Behälter": reverse("behaelter_list"),
        }
        for name, adresse in seiten.items():
            with self.subTest(name):
                self.assertEqual(self.client.get(adresse).status_code, 200, adresse)

    def test_formulare_die_bleiben_schicken_im_hintergrund(self):
        """Without 'data-sofort' the script leaves a form alone, and the page reloads.

        Every form in the content of these pages stays on its page. The
        exception carries a file and may lead on to the clarification.
        """
        self.client.force_login(CustomUser.objects.get(username="admin"))
        nord = Haus.objects.get(name=NORD)
        bereich = nord.bereiche.order_by("name")[0]
        ort = Ort.objects.get(name="Theke links", bereich__haus=nord)
        artikel = Hausartikel.objects.filter(haus=nord, ware__abwiegbar=True, status="AKTIV")[0]
        vorbereitet = Inventur.objects.filter(status=Inventurstatus.VORBEREITUNG)[0]
        seiten = {
            "Haus": nord.get_absolute_url(),
            "Bereich": bereich.get_absolute_url(),
            "Hausartikel": artikel.get_absolute_url(),
            "Ware": artikel.ware.get_absolute_url(),
            "Warengruppe": artikel.warengruppe.get_absolute_url(),
            "Zählliste": reverse("zaehlliste", args=[ort.pk]),
            "Inventur in Vorbereitung": vorbereitet.get_absolute_url(),
            "Inventur in Zählung": self.laufende(NORD).get_absolute_url(),
            "Sollbestand pflegen": reverse("inventur_soll_pflegen", args=[vorbereitet.pk]),
        }
        for name, adresse in seiten.items():
            with self.subTest(name):
                html = self.client.get(adresse).content.decode()
                self.assertIn("js/instant_actions.js", html)
                inhalt = html.split("data-seite>", 1)[1]
                formulare = re.findall(r'<form\s[^>]*method="post"[^>]*>', inhalt)
                self.assertTrue(formulare, "Die Seite hat kein Formular mehr.")
                laut = [f for f in formulare if "data-sofort" not in f and "multipart" not in f]
                self.assertEqual(laut, [])

    # -- What the demo brings in terms of history -----------------------------

    def test_vier_laeufe_ueber_beide_haeuser(self):
        abgeschlossen = Inventur.objects.filter(status=Inventurstatus.ABGESCHLOSSEN)
        self.assertEqual(abgeschlossen.count(), 8)
        # The annual inventory is the one that counts every type.
        arten = abgeschlossen.annotate(arten=Count("inventurarten")).values_list("arten", flat=True)
        self.assertEqual(sorted(arten), [1] * 6 + [len(catalog.INVENTURARTEN)] * 2)

    def test_die_arten_der_demo(self):
        """The order is that of the tabs, also in the name of an annual inventory."""
        self.assertEqual(
            list(Inventurart.objects.values_list("name", flat=True)), list(catalog.INVENTURARTEN)
        )
        jahr = Inventur.objects.filter(stichtag=inventories.stichtage(AUFBAUTAG).jahr).first()
        assert jahr is not None
        self.assertEqual(jahr.arten_anzeige, " + ".join(catalog.INVENTURARTEN))

    def test_ergebnisse_sind_eingefroren(self):
        for inventur in Inventur.objects.filter(status=Inventurstatus.ABGESCHLOSSEN):
            with self.subTest(str(inventur)):
                self.assertGreater(inventur.ergebnisse.count(), 20)
                self.assertIsNotNone(inventur.abgeschlossen_am)
                self.assertFalse(inventur.zaehlpositionen.exists(), "Danach wird aufgeräumt.")

    def test_es_gibt_differenzen_in_beide_richtungen(self):
        ergebnisse = Inventur.objects.filter(status=Inventurstatus.ABGESCHLOSSEN).values_list(
            "ergebnisse__differenz", flat=True
        )
        werte = [wert for wert in ergebnisse if wert is not None]
        self.assertTrue(any(wert > 0 for wert in werte))
        self.assertTrue(any(wert < 0 for wert in werte))

    def test_eine_ware_laeuft_zwischen_den_haeusern_gegeneinander(self):
        """Shortage in one house, surplus in the other: a movement of goods."""
        name = next(iter(inventories.GEGENLAEUFIG))
        ware = Ware.objects.get(name=name)
        # The latest closed monthly run.
        stichtag = inventories.stichtage(AUFBAUTAG).abgeschlossen[0]
        differenzen = {
            inventur.haus.name: inventur.ergebnisse.get(hausartikel__ware=ware).differenz
            for inventur in Inventur.objects.filter(stichtag=stichtag)
        }
        self.assertLess(differenzen[NORD], 0)
        self.assertGreater(differenzen[OST], 0)

    def test_nur_die_zwei_gewollten_waren_laufen_gegeneinander(self):
        """The random deviation is the same per product in both houses. Otherwise half
        the stock would run against itself, and the case could no longer be shown."""
        from inventur.evaluation import familien_eines_laufs  # noqa: PLC0415

        fest = Inventur.objects.filter(status=Inventurstatus.ABGESCHLOSSEN)
        for stichtag in fest.values_list("stichtag", flat=True).distinct():
            lauf = list(fest.filter(stichtag=stichtag))
            # Over the whole tree: the products stand in the families below.
            gegen = {
                zeile.ware.name
                for block in familien_eines_laufs(lauf)
                for zeile in block.alle_zeilen
                if zeile.gegenlaeufig
            }
            with self.subTest(stichtag=stichtag):
                self.assertEqual(gegen, set(inventories.GEGENLAEUFIG))

    def test_das_blatt_des_laufs_beginnt_mit_den_familien(self):
        """In front the families alone, one of them in portions, each linked to its products."""
        stichtag = inventories.stichtage(AUFBAUTAG).abgeschlossen[0]
        lauf = list(Inventur.objects.filter(stichtag=stichtag).order_by("haus__name"))
        bloecke = familien_eines_laufs(lauf)
        html = pdf_html(
            "pdf/run.html",
            {
                "lauf": Lauf(stichtag=stichtag, inventuren=lauf),
                "bloecke": bloecke,
                "anzahl_waren": sum(block.anzahl for block in bloecke),
            },
        )
        uebersicht, je_ware = html.split('class="abschnitt neue-seite"')

        for block in flach(bloecke):
            with self.subTest(familie=str(block.name)):
                self.assertIn(f'href="#{block.kennung}"', uebersicht)
                self.assertIn(f'id="{block.kennung}"', je_ware)
        knabbereien = next(block for block in flach(bloecke) if block.name == "Knabbereien")
        self.assertIn("Portion", knabbereien.differenz_gesamt)
        # On paper the portion is short.
        with portion_abgekuerzt():
            auf_papier = knabbereien.differenz_gesamt
        self.assertRegex(auf_papier, r"^[+-]?[\d,]+ Port\.$")
        self.assertIn(f">{auf_papier}</td>", je_ware)

    def test_die_pruefliste_der_becher_schaltet_die_differenz_von_hand(self):
        """Without a target stock it is switched above the list too, as in the app."""
        self.client.force_login(CustomUser.objects.get(username="admin"))
        antwort = self.client.get(reverse("inventur_pruefen", args=[self.becherinventur().pk]))
        self.assertContains(antwort, "Differenz von Hand ausschalten")

    def becherinventur(self) -> Inventur:
        """The cup inventory alone, in Kino Nord, a week before the running run."""
        return Inventur.objects.get(stichtag=inventories.stichtage(AUFBAUTAG).woche)

    def test_die_becherinventur_zaehlt_nur_ihre_artikel(self):
        """The disposable cup added in the app is the exception, see below."""
        becher = Inventurart.objects.get(name=catalog.BECHERINVENTUR)
        inventur = self.becherinventur()
        self.assertEqual(inventur.haus.name, NORD)
        self.assertEqual(list(inventur.inventurarten.all()), [becher])
        erwartet = inventur.zaehlpositionen.filter(unerwartet=False)
        self.assertTrue(erwartet.exists())
        self.assertEqual(
            set(erwartet.values_list("artikel__hausartikel__inventurart", flat=True)), {becher.pk}
        )

    def test_nur_kino_nord_kann_die_becher_allein_zaehlen(self):
        """Kino Ost carries no cups. With the monthly inventory it can be chosen nonetheless."""
        becher = Inventurart.objects.get(name=catalog.BECHERINVENTUR)
        monat = Inventurart.objects.get(name=catalog.MONATSINVENTUR)
        haeuser = Haus.objects.filter(name__in=[NORD, OST])
        je_haus = arten_je_haus()
        self.assertEqual(ohne_artikel_der_arten(haeuser, [becher], je_haus), [OST])
        self.assertEqual(ohne_artikel_der_arten(haeuser, [monat, becher], je_haus), [])
        # The running run counts both. Kino Ost has a row in the monthly tab only.
        ost = self.laufende(OST)
        self.assertEqual(list(ost.inventurarten.all()), [monat, becher])
        self.assertFalse(
            ost.zaehlpositionen.filter(artikel__hausartikel__inventurart=becher).exists()
        )

    def test_die_becherinventur_fragt_nach_der_art(self):
        """Kino Nord waits for closing: the switch out of the type and the way into it."""
        inventur = self.becherinventur()
        self.assertEqual(inventur.status, Inventurstatus.ZAEHLUNG)
        klaerung = closing.klaeren(inventur)
        (offen,) = klaerung.ungezaehlte_artikel
        self.assertEqual(offen.hausartikel.artikelnummer, inventories.BECHER_OFFEN[0])
        self.assertEqual(
            [art.name for art in offen.andere_arten],
            [catalog.MONATSINVENTUR, catalog.JAHRESINVENTUR],
        )
        (fremd,) = klaerung.ausserhalb
        self.assertEqual(fremd.hausartikel.artikelnummer, inventories.BECHER_NACHTRAEGE[0].nummer)
        self.assertEqual([art.name for art in fremd.ziele], [catalog.BECHERINVENTUR])
        # Kino Ost carries the same cup: asked whether the answer applies there too.
        self.assertEqual(fremd.weitere, 1)

    def test_die_becherinventur_traegt_die_differenz_von_hand(self):
        """No file for the cups: the difference is typed in, the target stays empty."""
        inventur = self.becherinventur()
        self.assertTrue(inventur.differenz_von_hand)
        zeilen = {
            zeile.hausartikel.artikelnummer: zeile for zeile in services.ergebniszeilen(inventur)
        }
        for nummer, differenz, notiz in inventories.BECHER_DIFFERENZEN:
            with self.subTest(nummer=nummer):
                self.assertEqual(zeilen[nummer].differenz, Decimal(differenz))
                self.assertIsNone(zeilen[nummer].sollbestand)
                self.assertEqual(zeilen[nummer].notiz, notiz)
        # One counted cup still waits for its difference: the list shows what is missing.
        self.assertTrue(any(zeile.differenz is None for zeile in zeilen.values()))

    def test_in_kino_nord_zaehlt_beim_sirup_das_soll(self):
        """The mark of 'SOLL_ZAEHLT' stands in the check list, on the website and in the app."""
        inventur = self.laufende(NORD)
        zeilen = {
            zeile.hausartikel.artikelnummer: zeile for zeile in services.ergebniszeilen(inventur)
        }
        for nummer, notiz in inventories.SOLL_ZAEHLT:
            with self.subTest(nummer=nummer):
                self.assertTrue(zeilen[nummer].soll_statt_ist)
                self.assertIsNotNone(zeilen[nummer].sollbestand)
                self.assertEqual(zeilen[nummer].notiz, notiz)

    def test_die_becherinventur_ist_gesperrt_der_monatslauf_nicht(self):
        """Counted, and the check begins. The monthly run is where the app still counts."""
        becher = self.becherinventur()
        self.assertTrue(becher.gesperrt)
        self.assertEqual(becher.status, Inventurstatus.ZAEHLUNG)
        self.assertFalse(self.laufende(NORD).gesperrt)

    def test_die_zaehlliste_ist_nach_arten_unterteilt(self):
        """The drinks store of Kino Nord holds all three types, so the page shows three sections."""
        ort = Ort.objects.get(bereich__haus__name=NORD, name="Getränkelager")
        abschnitte = ZaehllisteFormSet(prefix="zeilen", ort=ort).abschnitte()
        self.assertEqual([art.name for art, _ in abschnitte], list(catalog.INVENTURARTEN))

    def test_ein_artikel_wechselt_ans_ende_seiner_neuen_art(self):
        """The way DEMO.md describes: the white wine of Kino Nord into the monthly inventory."""
        ort = Ort.objects.get(bereich__haus__name=NORD, name="Getränkelager")
        wein = Hausartikel.objects.get(haus__name=NORD, ware__name="Weißwein 0,2 l")
        self.assertTrue(wein.weitere_gefuehrte().exists())
        wein.inventurart_setzen(Inventurart.objects.get(name=catalog.MONATSINVENTUR))
        art, zeilen = ZaehllisteFormSet(prefix="zeilen", ort=ort).abschnitte()[0]
        self.assertEqual(art.name, catalog.MONATSINVENTUR)
        self.assertEqual(zeilen[-1].erwartung_objekt.artikel_id, wein.pk)

    @staticmethod
    def wie_gezeigt(ort: Ort) -> BaseZaehllisteFormSet:
        """The counting list as "Save" sends it back untouched: every tick as it stands."""
        gezeigt = ZaehllisteFormSet(prefix="zeilen", ort=ort)
        daten = {
            "zeilen-TOTAL_FORMS": str(len(gezeigt.forms)),
            "zeilen-INITIAL_FORMS": str(len(gezeigt.forms)),
        }
        for index, zeile in enumerate(gezeigt.forms):
            daten[f"zeilen-{index}-erwartung"] = str(zeile.erwartung_objekt.pk)
            for name in zeile.fields:
                haken = name.startswith("gebinde_") or name == ZaehllisteZeileForm.WIEGEN
                if haken and zeile.initial.get(name):
                    daten[f"zeilen-{index}-{name}"] = "on"
        return ZaehllisteFormSet(daten, prefix="zeilen", ort=ort)

    def test_jede_zaehlliste_laesst_sich_speichern_wie_sie_steht(self):
        """Every row has a pack, or the scale where it is weighed.

        "Save" asks that of every row since 10/2026. Otherwise it would turn the
        demo's own lists away untouched.
        """
        for ort in Ort.objects.filter(erwartungen__isnull=False).distinct():
            with self.subTest(ort=str(ort)):
                gesendet = self.wie_gezeigt(ort)
                self.assertTrue(gesendet.is_valid(), gesendet.errors)

    def test_der_schokoriegel_hat_drei_gebinde_und_das_display_zaehlt_nirgends(self):
        """DEMO.md lets the order of the packs be tried there: every other product has two at most.

        No location expects the display, so no run counts it. Otherwise the
        numbers of the demo would move with it, and the pictures of the README too.
        """
        self.assertEqual(
            list(
                Ware.objects.annotate(anzahl=Count("gebinde"))
                .filter(anzahl__gt=2)
                .values_list("name", flat=True)
            ),
            ["Schokoriegel"],
        )
        riegel = Ware.objects.get(name="Schokoriegel")
        self.assertEqual(
            list(
                riegel.gebinde.filter(aktiv=True)
                .order_by("sortierung")
                .values_list("name", flat=True)
            ),
            ["Einzeln", "Display", "Karton"],
        )
        display = riegel.gebinde.get(name="Display")
        self.assertFalse(ErwartungGebinde.objects.filter(gebinde=display).exists())
        self.assertFalse(Zaehlposition.objects.filter(gebinde=display).exists())

    def test_ein_inaktiver_kasten_steht_grau_und_bekommt_keine_zeile(self):
        """The way DEMO.md describes: the cola's crate set inactive, seen in Kino Nord's store."""
        kasten = Gebinde.objects.get(ware__name="Cola 0,5 l", name="Kasten")
        kasten.aktiv = False
        kasten.save(update_fields=["aktiv"])
        ort = Ort.objects.get(bereich__haus__name=NORD, name="Getränkelager")
        cola = Hausartikel.objects.get(haus__name=NORD, ware__name="Cola 0,5 l")

        (zeile,) = [
            zeile
            for zeile in ZaehllisteFormSet(prefix="zeilen", ort=ort).forms
            if zeile.erwartung_objekt.artikel_id == cola.pk
        ]
        self.assertEqual([vorgabe.gebinde for vorgabe in zeile.inaktive_vorgaben], [kasten])
        # "Save" now asks for another pack: the crate was the only one there.
        gesendet = self.wie_gezeigt(ort)
        self.assertEqual(
            [
                zeile.non_field_errors()
                for zeile in gesendet.forms
                if zeile.errors or zeile.erwartung_objekt.artikel_id == cola.pk
            ],
            [["Ohne Gebinde bekäme der Artikel hier keine Zeile."]],
        )
        # A new inventory gives it no row.
        inventur = Inventur.objects.create(haus=cola.haus, stichtag=AUFBAUTAG + timedelta(days=40))
        inventur.inventurarten.set([cola.inventurart])
        inventur.zaehlpositionen_anlegen()
        self.assertTrue(inventur.zaehlpositionen.filter(artikel=cola).exists())
        self.assertFalse(inventur.zaehlpositionen.filter(gebinde=kasten).exists())

    def test_ein_eingetragener_inaktiver_kasten_fragt_erst(self):
        """The way DEMO.md describes: the crate switched off, then typed in again.

        At Kino Nord's cola, and for a new cola article at Kino West, which does
        not carry it before its import. Both ask, and nothing is saved before.
        """
        self.client.force_login(CustomUser.objects.get(username="leitung"))
        kasten = Gebinde.objects.get(ware__name="Cola 0,5 l", name="Kasten")
        self.client.post(reverse("gebinde_umschalten", args=[kasten.pk]), {"aktiv": "0"})
        cola = Hausartikel.objects.get(haus__name=NORD, ware__name="Cola 0,5 l")
        getippt = {
            "gebinde-TOTAL_FORMS": "1",
            "gebinde-INITIAL_FORMS": "0",
            "gebinde-0-name": "Kasten",
            "gebinde-0-faktor_in_basiseinheit": "24",
        }
        artikel = {
            "quelle": cola.quelle,
            "basiseinheit": "",
            "ware": cola.ware_id,
            "warengruppe": cola.warengruppe_id,
        } | getippt

        west = Haus.objects.get(name=WEST)
        neu = artikel | {"artikelnummer": "103001", "name": "Cola 0,5 l", "inventurart": ""}
        neu |= {"orte-TOTAL_FORMS": "0", "orte-INITIAL_FORMS": "0"}
        gefragt = self.client.post(reverse("hausartikel_add", args=[west.pk]), neu)
        self.assertTrue(gefragt.context["gebinde_formset"].forms[0].auffaellig)
        self.assertFalse(Hausartikel.objects.filter(haus=west).exists())

        adresse = reverse("hausartikel_edit", args=[cola.pk])
        artikel |= {
            "artikelnummer": cola.artikelnummer,
            "name": cola.name,
            "inventurart": cola.inventurart_id,
        }
        gefragt = self.client.post(adresse, artikel)
        self.assertContains(gefragt, "„Kasten“ gibt es bei „Cola 0,5 l“ schon, aber inaktiv")
        kasten.refresh_from_db()
        self.assertFalse(kasten.aktiv)

        antwort = {"gebinde-0-entscheidung": f"{WIEDER_AKTIVIEREN}:{kasten.pk}"}
        self.assertRedirects(self.client.post(adresse, artikel | antwort), cola.get_absolute_url())
        kasten.refresh_from_db()
        self.assertTrue(kasten.aktiv)

    def test_der_import_von_kino_west_holt_den_inaktiven_kasten_zurueck_und_sagt_es(self):
        """The way DEMO.md describes: the cola's crate switched off, then Kino West imports.

        Its file buys the cola in crates of 24, and what the file carries the
        import brings back. The plan shows the crate in the field under its own
        name and says beforehand that adopting switches it on in every house.
        """
        kasten = Gebinde.objects.get(ware__name="Cola 0,5 l", name="Kasten")
        kasten.aktiv_setzen(False)
        west = Haus.objects.get(name=WEST)

        plan, _ = planen(self.tabelle(WEST, "artikel_kino_west.csv"), west)
        cola = next(zeile for zeile in plan if zeile.name == "Cola 0,5 l")
        self.assertEqual((cola.art, cola.ware_pk), (NEU, kasten.ware_id))
        self.assertEqual(cola.gebinde["Kasten"], Decimal("24"))
        self.assertIn("„Kasten = 24“ führt die Ware nur inaktiv.", " ".join(cola.hinweise))

        bericht = ausfuehren([als_daten(cola)], west)
        kasten.refresh_from_db()
        self.assertTrue(kasten.aktiv)
        self.assertEqual(bericht.gebinde_reaktiviert, 1)
        self.assertTrue(Hausartikel.objects.filter(haus=west, ware=kasten.ware).exists())

    def test_kino_ost_zaehlt_den_weisswein_jeden_monat(self):
        """Kino Nord only once a year. The product's page shows it as mixed."""
        weisswein = Ware.objects.get(name="Weißwein 0,2 l")
        self.assertTrue(weisswein.arten_gemischt)
        stichtag = inventories.stichtage(AUFBAUTAG).abgeschlossen[0]
        gezaehlt = Inventurergebnis.objects.filter(
            inventur__stichtag=stichtag, hausartikel__ware=weisswein
        ).values_list("inventur__haus__name", flat=True)
        self.assertEqual(list(gezaehlt), [OST])
        # In the running month Kino Nord counted it nonetheless. The question
        # at closing asks whether the answer applies to Kino Ost's article too.
        nachtrag = next(
            eintrag
            for eintrag in closing.klaeren(self.laufende(NORD)).ausserhalb
            if eintrag.hausartikel.ware_id == weisswein.pk
        )
        self.assertEqual(nachtrag.weitere, 1)

    def test_ein_haus_fuehrt_denselben_becher_zweimal(self):
        """Merged, one stays and the other counts as it, with its stock from the file."""
        bleibt, doppelt = (Ware.objects.get(name=name) for name in houses.DUBLETTE)
        (dublette,) = dubletten(doppelt, bleibt)
        self.assertEqual(dublette.haus.name, NORD)

        bericht = zusammenfuehren(doppelt, bleibt)
        self.assertEqual(bericht.eingestellt, 1)
        alter = Hausartikel.objects.select_related("zaehlt_als").get(
            haus__name=NORD, artikelnummer="101613"
        )
        bleibender = alter.zaehlt_als
        assert bleibender is not None
        self.assertEqual(bleibender.artikelnummer, "101607")
        self.assertGreater(bericht.erwartungen, 0, "an denselben Orten zusammengezählt")

        inventur = self.vorbereitete(NORD)
        vorher = inventur.sollbestaende.get(hausartikel=bleibender).menge_basiseinheit
        inventories.sollbestand_hochladen(
            inventur, inventories.SOLLDATEI[houses.NORD], names_en.Namen("de")
        )
        nachher = inventur.sollbestaende.get(hausartikel=bleibender).menge_basiseinheit
        self.assertEqual(nachher - vorher, 600)

    def test_der_trinkbecher_fragt_nach_dem_inaktiven_karton_des_bechers(self):
        """The way DEMO.md describes: the cup's carton switched off, then the duplicates merged.

        The drinking cup's carton would be absorbed into an inactive one, and
        the preview asks before anything is written.
        """
        self.client.force_login(CustomUser.objects.get(username="leitung"))
        bleibt, doppelt = (Ware.objects.get(name=name) for name in houses.DUBLETTE)
        karton = bleibt.gebinde.get(name="Karton")
        karton.aktiv_setzen(False)
        adresse = reverse("ware_merge", args=[bleibt.pk])

        vorschau = self.client.post(adresse, {"quelle": doppelt.pk})
        self.assertContains(vorschau, "„Karton“ ist hier inaktiv")
        bestaetigt = {"quelle": doppelt.pk, "bestaetigt": "1"}
        bestaetigt["gezeigt"] = vorschau.context["signatur"]
        for feld in vorschau.context["doppelte"].haeuser()[0]["erwartungen"]:
            bestaetigt[feld.html_name] = "on"
        alter_karton = doppelt.gebinde.get(name="Karton")
        bestaetigt[f"aktiv-{alter_karton.pk}"] = f"{WIEDER_AKTIVIEREN}:{karton.pk}"

        antwort = self.client.post(adresse, bestaetigt)
        self.assertRedirects(antwort, bleibt.get_absolute_url(), fetch_redirect_response=False)
        karton.refresh_from_db()
        self.assertTrue(karton.aktiv)
        self.assertFalse(Ware.objects.filter(pk=doppelt.pk).exists())

    def test_der_waffelbruch_kommt_mit_familie_und_platz_zurueck(self):
        """The way DEMO.md describes: a wrong click on "Not countable", undone on the page.

        The product remembers its family, its article the status and the
        place in the raw materials store. "Wieder aufnehmen" brings all three back.
        """
        (name,) = catalog.VERSEHENTLICH_NICHT_ZAEHLBAR
        ware = Ware.objects.get(name=name)
        artikel = ware.hausartikel.get()
        lager = Ort.objects.get(bereich__haus__name=SUED, name="Rohstofflager")
        self.assertTrue(ware.ist_nicht_zaehlbar)
        self.assertEqual(getattr(ware.familie_vorher, "name", None), "Zutaten")
        self.assertEqual(
            (artikel.status, artikel.status_vorher),
            (Artikelstatus.IGNORIERT, Artikelstatus.AKTIV),
        )
        self.assertEqual([platz["ort"] for platz in artikel.erwartungen_vorher], [lager.pk])
        self.assertFalse(Erwartung.objects.filter(artikel=artikel).exists())

        self.client.force_login(CustomUser.objects.get(username="leitung"))
        antwort = self.client.post(
            reverse("ware_status", args=[ware.pk]), {"status": "WIEDER_AUFNEHMEN"}
        )

        self.assertRedirects(antwort, ware.get_absolute_url(), fetch_redirect_response=False)
        ware.refresh_from_db()
        artikel.refresh_from_db()
        self.assertEqual(getattr(ware.warenfamilie, "name", None), "Zutaten")
        self.assertEqual(artikel.status, Artikelstatus.AKTIV)
        self.assertTrue(Erwartung.objects.filter(artikel=artikel, ort=lager).exists())

    def test_der_lauf_haelt_die_haeuser_zusammen(self):
        lauf = Inventur.objects.filter(status=Inventurstatus.ABGESCHLOSSEN).first()
        assert lauf is not None
        self.assertTrue(lauf.gehoert_zu_lauf)

    def test_die_zaehlenden_kamen_beim_anlegen_in_jedes_haus(self):
        """Set for every house of a run at once, as with the box in the form.

        The closed ones lost them again: who counted is not kept.
        """
        for inventur in Inventur.objects.all():
            with self.subTest(str(inventur)):
                erwartet = (
                    []
                    if inventur.status == Inventurstatus.ABGESCHLOSSEN
                    else ["leitung", "zaehler"]
                )
                self.assertEqual(
                    sorted(inventur.zaehlende.values_list("username", flat=True)), erwartet
                )

    # -- The bundled exports ------------------------------------------------

    def tabelle(self, hausname: str, dateiname: str):
        artikel, _ = get_artikel_und_warengruppen({hausname: str(datei(dateiname))})
        return artikel

    def test_alle_dateien_liegen_da(self):
        for name in BESCHREIBUNGEN:
            with self.subTest(name):
                self.assertTrue(datei(name).is_file())

    def test_erster_import_legt_das_leere_haus_an(self):
        haus = Haus.objects.get(name=WEST)
        plan, _ = planen(self.tabelle(WEST, "artikel_kino_west.csv"), haus)
        self.assertGreater(len(plan), 30)
        self.assertEqual({zeile.art for zeile in plan}, {NEU})
        self.assertTrue(
            any(zeile.ware_pk for zeile in plan),
            "Die Waren der anderen Häuser sollen vorgeschlagen werden.",
        )

    def test_zweiter_import_zeigt_jede_art(self):
        haus = Haus.objects.get(name=OST)
        plan, _ = planen(self.tabelle(OST, "artikel_kino_ost.csv"), haus)
        self.assertEqual(
            {zeile.art for zeile in plan}, {NEU, EINGESTELLT, KONFLIKT, UMBENANNT, FEHLT}
        )

    def test_zweiter_import_hat_die_drei_konflikte_aus_der_beschreibung(self):
        """DEMO.md promises a different product group and twice a foreign pack.

        The product group conflict was missing until 09/2026: the file carried the
        number twice, and the import took the first row with the old group.
        """
        haus = Haus.objects.get(name=OST)
        plan, _ = planen(self.tabelle(OST, "artikel_kino_ost.csv"), haus)
        konflikte = {
            zeile.artikelnummer: zeile.abweichungen for zeile in plan if zeile.art == KONFLIKT
        }
        self.assertEqual(len(konflikte), 3)
        self.assertEqual(
            sorted(a for ab in konflikte.values() for a in ab),
            ["gebinde", "gebinde", "warengruppe"],
        )

    def test_der_export_listet_zwei_artikel_aus(self):
        """Their product group is in the file, their number is not. So they are over."""
        haus = Haus.objects.get(name=OST)
        plan, _ = planen(self.tabelle(OST, "artikel_kino_ost.csv"), haus)
        self.assertEqual(
            sorted(zeile.artikelnummer for zeile in plan if zeile.art == FEHLT),
            ["102406", "102505"],
        )

    def test_eine_warengruppe_bleibt_ausserhalb_des_exports(self):
        """Whoever knows nothing changed on one leaves it out of the export."""
        haus = Haus.objects.get(name=OST)
        _, meldungen = planen(self.tabelle(OST, "artikel_kino_ost.csv"), haus)
        self.assertIn("bleiben unberührt", " ".join(meldungen))
        self.assertIn("Sonstiges", " ".join(meldungen))

    def test_unveraenderte_zeilen_stehen_nicht_im_plan(self):
        """What the house carries exactly so is missing from the plan, or it would be unreadable."""
        haus = Haus.objects.get(name=OST)
        plan, meldungen = planen(self.tabelle(OST, "artikel_kino_ost.csv"), haus)
        self.assertLess(len(plan), 12)
        self.assertIn("schon genau so", " ".join(meldungen))
        self.assertTrue(meldungen)

    def test_die_exporte_tragen_die_rohen_schreibweisen(self):
        """Otherwise the demo would not check the normalisation but its result."""
        roh = datei("artikel_kino_west.csv").read_text(encoding="utf-8")
        for wort in ("Kiste(n)", "Sack(Säcke)", "Karnister", "gramm", "ltr"):
            with self.subTest(wort=wort):
                self.assertIn(wort, roh)

    def test_die_normalisierung_macht_hausbegriffe_daraus(self):
        tabelle = self.tabelle(WEST, "artikel_kino_west.csv")
        gebinde = set(tabelle["gebinde"])
        self.assertIn("Kasten", gebinde)
        self.assertIn("Sack", gebinde)
        self.assertIn("Kanister", gebinde)
        self.assertNotIn("Kiste(n)", gebinde)
        # "Becher" is no unit. What cannot be recognised is counted.
        self.assertEqual(set(tabelle["einheit"]) - {STUECK}, {"GRAMM", "KILOGRAMM", "LITER"})

    def test_stueckware_bekommt_das_lose_einzelstueck(self):
        haus = Haus.objects.get(name=WEST)
        plan, _ = planen(self.tabelle(WEST, "artikel_kino_west.csv"), haus)
        bier = next(zeile for zeile in plan if zeile.name == "Bier 0,33 l")
        self.assertEqual(bier.gebinde["Einzeln"], Decimal("1.000"))
        self.assertEqual(bier.gebinde["Kasten"], Decimal("20.000"))

    def test_das_einzelstueck_steht_auch_im_katalog(self):
        einzeln = Gebinde.objects.filter(ist_einzeln=True)
        self.assertTrue(einzeln.exists())
        self.assertEqual(set(einzeln.values_list("faktor_in_basiseinheit", flat=True)), {1})
        with translation.override("en"):
            self.assertEqual(str(einzeln[0].anzeigename), "Single")

    def test_sollbestand_fuer_das_leere_haus_passt_zu_seinen_nummern(self):
        """After the import Kino West carries the numbers from this file."""
        nummern = {
            zeile.split(";")[0]
            for zeile in datei("sollbestand_kino_west.csv")
            .read_text(encoding="utf-8")
            .splitlines()[1:]
        }
        aus_dem_export = set(self.tabelle(WEST, "artikel_kino_west.csv")["artikelnummer"])
        self.assertTrue(aus_dem_export <= nummern | {"103999"})

    def test_die_oberflaeche_erkennt_den_demobestand(self):
        """So nobody takes the invented numbers for real ones."""
        self.assertTrue(ist_demobestand())
        self.client.force_login(CustomUser.objects.get(username="leitung"))
        antwort = self.client.get(reverse("inventur_list"))
        self.assertContains(antwort, "Dieser Bestand ist erfunden")

    def test_das_band_bringt_sein_messskript_mit(self):
        """It sticks at the top, and bar and header bar attach to its height.

        That is not fixed, because the sentence wraps on the phone. Without the
        script "--band-hoehe" would stay zero, and the bar would slide over it.
        """
        self.client.force_login(CustomUser.objects.get(username="leitung"))
        antwort = self.client.get(reverse("inventur_list"))
        self.assertContains(antwort, "js/demo_band.js")
        self.assertIn("--band-hoehe", skript("demo_band"))

    def test_das_band_ist_eine_landmarke(self):
        """Outside header, nav and main a screen reader jumping by landmarks skips it."""
        self.client.force_login(CustomUser.objects.get(username="leitung"))
        antwort = self.client.get(reverse("inventur_list"))
        self.assertContains(antwort, 'class="demo-band" role="region" aria-label="Demo"')

    def test_ohne_band_kein_skript(self):
        Demokennzeichen.objects.all().delete()
        self.client.force_login(CustomUser.objects.get(username="leitung"))
        antwort = self.client.get(reverse("inventur_list"))
        self.assertNotContains(antwort, "js/demo_band.js")

    def test_das_kennzeichen_ueberlebt_das_aufraeumen_in_der_demo(self):
        """Trying out is part of it, deleting too. The notice stays.

        That is why it hangs on a row of its own and not on the house names.
        """
        Haus.objects.filter(name=WEST).delete()
        Haus.objects.filter(name=OST).update(name="Anders benannt")
        self.assertTrue(ist_demobestand())

    def test_auch_die_zaehl_app_erfaehrt_es(self):
        konto = CustomUser.objects.get(username="zaehler")
        self.assertIs(IchSerializer(konto).data["demo"], True)

    # -- An assurance to the reader: none of this is real ------------------

    def test_keine_echten_hausnamen(self):
        self.assertTrue(all(name.startswith("Kino ") for name in (NORD, OST, WEST)))
        self.assertTrue(SUED.endswith(" Süd"))

    def test_alle_nummern_beginnen_mit_101_bis_103(self):
        # Own articles have no number, see 'test_die_eismanufaktur_...'.
        nummern = Hausartikel.objects.exclude(artikelnummer="").values_list(
            "artikelnummer", flat=True
        )
        self.assertTrue(all(nummer[:3] in ("101", "102", "103") for nummer in nummern))
        self.assertTrue(all(len(nummer) == NUMMERNLAENGE for nummer in nummern))


class Stichtage(SimpleTestCase):
    """The demo does not age: its cut-off dates hang on the day of the build."""

    def test_laufender_lauf_liegt_im_vormonat(self):
        self.assertEqual(inventories.stichtage(date(2026, 9, 20)).laufend, date(2026, 8, 31))

    def test_abgeschlossene_liegen_davor(self):
        tage = inventories.stichtage(date(2026, 9, 20))
        self.assertEqual(
            tage.abgeschlossen, [date(2026, 7, 31), date(2026, 6, 30), date(2026, 5, 31)]
        )

    def test_jahresinventur_liegt_vor_allen_monaten(self):
        tage = inventories.stichtage(date(2026, 9, 20))
        self.assertEqual(tage.jahr, date(2025, 12, 31))
        self.assertLess(tage.jahr, min(tage.abgeschlossen))

    def test_die_vorbereitete_liegt_am_jahresende(self):
        tage = inventories.stichtage(date(2026, 9, 20))
        self.assertEqual(tage.vorbereitung, date(2026, 12, 31))
        self.assertNotEqual(tage.vorbereitung, tage.jahr)

    def test_im_januar_ist_der_laufende_lauf_die_jahresinventur(self):
        """Until 10/2026 the year end was a monthly run, and the annual one lay in 2024."""
        tage = inventories.stichtage(date(2026, 1, 15))
        self.assertEqual(tage.laufend, date(2025, 12, 31))
        self.assertEqual(tage.abgeschlossen[0], date(2025, 11, 30))
        self.assertEqual(tage.jahr, tage.laufend)
        self.assertNotEqual(tage.vorbereitung, tage.jahr)
        # A house has one inventory per day: the prepared one moves on a year.
        self.assertEqual(tage.vorbereitung, date(2026, 12, 31))

    def test_im_maerz_ist_ein_abgeschlossener_lauf_die_jahresinventur(self):
        tage = inventories.stichtage(date(2026, 3, 1))
        self.assertEqual(
            tage.abgeschlossen, [date(2026, 1, 31), date(2025, 12, 31), date(2025, 11, 30)]
        )
        self.assertEqual(tage.jahr, date(2025, 12, 31))

    def test_der_letzte_tag_eines_schaltjahrs(self):
        self.assertEqual(inventories.monatsende(date(2028, 2, 3)), date(2028, 2, 29))


class ImJanuar(TestCase):
    """A build in January: the running run lies on the year end itself."""

    @classmethod
    def setUpTestData(cls):
        builder.aufbauen(date(2026, 1, 15))

    def test_im_januar_zaehlt_der_laufende_lauf_alle_arten(self):
        laufend = Inventur.objects.get(haus__name=NORD, stichtag=date(2025, 12, 31))
        self.assertEqual(laufend.inventurarten.count(), len(catalog.INVENTURARTEN))
        self.assertFalse(Inventur.objects.filter(stichtag=date(2024, 12, 31)).exists())

    def test_im_januar_baut_die_demo_trotzdem(self):
        """The running run and the prepared one would otherwise fall on the same day."""
        self.assertEqual(Inventur.objects.filter(stichtag=date(2025, 12, 31)).count(), 2)
        self.assertEqual(Inventur.objects.filter(stichtag=date(2026, 12, 31)).count(), 3)


@override_settings(DEBUG=True)
class Befehl(TestCase):
    """'manage.py demo' where it refuses. What it builds is in 'BefehlBaut'.

    With 'DEBUG', as during development: Django's test runner switches it off,
    and without it the command asks for '--demo-server' before anything else
    ('OhneDebug').
    """

    def test_weigert_sich_neben_vorhandenen_daten(self):
        Haus.objects.create(name="Bestehendes Haus")
        with self.assertRaisesMessage(CommandError, "keine Demo"):
            call_command("demo", verbosity=0)

    def test_weigert_sich_auch_neben_einem_konto_mit_demo_namen(self):
        """Otherwise the build ended behind an existing 'admin' in an IntegrityError."""
        CustomUser.objects.create_superuser(username="admin", password="x", email="a@example.org")
        with self.assertRaises(CommandError):
            call_command("demo", verbosity=0)

    def test_weigert_sich_neben_einem_einzigen_eigenen_konto(self):
        """The first administrator of a fresh server is enough.

        Until 10/2026 the demo's accounts arose beside it, their password public in DEMO.md.
        """
        CustomUser.objects.create_superuser(
            username="chef", password="geheim-genug-2026", email="chef@example.org"
        )
        with self.assertRaisesMessage(CommandError, "keine Demo"):
            call_command("demo", verbosity=0)
        self.assertEqual(list(CustomUser.objects.values_list("username", flat=True)), ["chef"])
        self.assertFalse(Haus.objects.exists())

    def test_auch_mit_reset_bleibt_ein_eigener_bestand_stehen(self):
        """'--reset' clears away a demo and nothing else.

        Until 10/2026 it cleared whatever stood there and built the demo in its place.
        """
        Haus.objects.create(name="Bestehendes Haus")
        with self.assertRaisesMessage(CommandError, "keine Demo"):
            call_command("demo", reset=True, noinput=True, verbosity=0)
        self.assertEqual(list(Haus.objects.values_list("name", flat=True)), ["Bestehendes Haus"])
        self.assertFalse(CustomUser.objects.exists())
        self.assertFalse(ist_demobestand())

    def test_eine_demo_baut_sie_nur_mit_reset_neu(self):
        kennzeichnen()
        Haus.objects.create(name="Beim Ausprobieren angelegt")
        with self.assertRaisesMessage(CommandError, "--reset"):
            call_command("demo", verbosity=0)
        self.assertEqual(Haus.objects.count(), 1)

    def test_ohne_terminal_bricht_das_zuruecksetzen_sauber_ab(self):
        """A cron job or a pipe has no answer. Until 10/2026 that was an EOFError traceback."""
        kennzeichnen()
        Haus.objects.create(name="Beim Ausprobieren angelegt")
        with (
            mock.patch("builtins.input", side_effect=EOFError),
            self.assertRaisesMessage(CommandError, "Abgebrochen"),
        ):
            call_command("demo", reset=True, verbosity=0)
        self.assertTrue(Haus.objects.filter(name="Beim Ausprobieren angelegt").exists())

    def test_meldet_ein_unbrauchbares_datum(self):
        with self.assertRaises(CommandError):
            call_command("demo", date="10.03.2026", verbosity=0)


class OhneDebug(TestCase):
    """A server in operation runs without 'DEBUG', as Django's test runner does.

    Its database is empty right after setting it up, before the first account,
    and nothing in the data tells it from a fresh one during development. The
    demo then asks for the switch that says the server is meant to show it.
    Until 10/2026 it built there, and anyone who read DEMO.md could sign in.
    """

    def test_ohne_den_schalter_entsteht_nichts(self):
        with self.assertRaisesMessage(CommandError, "--demo-server"):
            call_command("demo", verbosity=0)
        self.assertFalse(CustomUser.objects.exists())
        self.assertFalse(ist_demobestand())

    def test_auch_eine_demo_baut_er_ohne_den_schalter_nicht_neu(self):
        kennzeichnen()
        with self.assertRaisesMessage(CommandError, "--demo-server"):
            call_command("demo", reset=True, noinput=True, verbosity=0)
        self.assertTrue(ist_demobestand())

    def test_die_meldung_spricht_die_sprache_der_demo(self):
        with self.assertRaisesMessage(CommandError, "DEBUG is off"):
            call_command("demo", "--language", "en", verbosity=0)

    def test_ein_eigener_bestand_bleibt_auch_mit_dem_schalter_unberuehrt(self):
        """The switch says what the server is for. It does not make a database in use the demo's."""
        Haus.objects.create(name="Bestehendes Haus")
        with self.assertRaisesMessage(CommandError, "keine Demo"):
            call_command("demo", demo_server=True, reset=True, noinput=True, verbosity=0)
        self.assertEqual(list(Haus.objects.values_list("name", flat=True)), ["Bestehendes Haus"])


class BefehlBaut(TestCase):
    """'manage.py demo' once, with reset and a chosen day. Until 10/2026 every check built anew.

    On a server meant for the demo: without 'DEBUG', with its switch, and over a
    demo that someone added a house to while trying it out.
    """

    ausgabe: str

    @classmethod
    def setUpTestData(cls):
        kennzeichnen()
        Haus.objects.create(name="Beim Ausprobieren angelegt")
        ausgabe = io.StringIO()
        call_command(
            "demo",
            date="2026-03-10",
            reset=True,
            noinput=True,
            demo_server=True,
            verbosity=1,
            stdout=ausgabe,
        )
        cls.ausgabe = ausgabe.getvalue()

    def test_baut_auf_den_gewuenschten_stichtag(self):
        self.assertEqual(Haus.objects.count(), 4)
        self.assertTrue(Inventur.objects.filter(stichtag=date(2026, 2, 28)).exists())

    def test_setzt_auf_wunsch_zurueck(self):
        self.assertEqual(
            sorted(Haus.objects.values_list("name", flat=True)),
            sorted([NORD, OST, WEST, SUED]),
        )

    def test_die_uebersicht_nennt_das_eingeladene_konto_ohne_passwort(self):
        """Until 10/2026 the overview promised the invited account a password and a role."""
        self.assertIn("außer für eingeladen", self.ausgabe)
        self.assertIn("wartet auf die Einladung", self.ausgabe)
        self.assertNotIn("Passwort für alle", self.ausgabe)

    def test_die_uebersicht_spricht_deutsch(self):
        self.assertIn("Häuser:", self.ausgabe)

    def test_die_zaehlwoerter_folgen_der_sprache(self):
        """Until 10/2026 the English build said "4 Häuser, 80 Waren" too."""
        with translation.override("en"):
            self.assertIn("4 sites, 81 products, ", builder.was_dasteht())
        with translation.override("de"):
            self.assertIn("4 Häuser, 81 Waren, ", builder.was_dasteht())


@override_settings(DEBUG=True)
class Wiederholbar(TestCase):
    """The same build yields the same stock, even after a reset.

    Until 09/2026 the target hung on the primary key of the house article,
    and that is a different one on the second build: only 212 of 435 frozen
    results still matched. Whoever shows the demo twice should see the same
    numbers twice.
    """

    @staticmethod
    def ergebnisse() -> dict:
        return {
            (
                e.inventur.haus.name,
                e.inventur.stichtag,
                e.hausartikel.artikelnummer,
            ): (
                e.gesamtmenge_basiseinheit,
                e.sollbestand,
            )
            for e in Inventurergebnis.objects.select_related("inventur__haus", "hausartikel")
        }

    def test_zweimal_dieselben_zahlen(self):
        call_command("demo", date="2026-09-21", verbosity=0)
        erster = self.ergebnisse()
        call_command("demo", date="2026-09-21", reset=True, noinput=True, verbosity=0)
        # 435 before 10/2026. The duplicate cup in Kino Nord adds 4, the reusable
        # cups in its annual inventory 2, the white wine Kino Ost counts monthly 3,
        # the guacamole, active until after the running count, 1 in the annual
        # inventory of Kino Nord, the lemon syrup of Kino Nord 4, and the ice
        # cubes in its freezer 1, in the annual inventory.
        self.assertEqual(len(erster), 450)
        self.assertEqual(self.ergebnisse(), erster)


class Aufraeumen(TestCase):
    """What the command clears away with --reset."""

    band_vorher: bool

    @classmethod
    def setUpTestData(cls):
        builder.aufbauen(AUFBAUTAG)
        cls.band_vorher = ist_demobestand()
        builder.leeren()

    def test_leeren_laesst_die_stammdaten_stehen(self):
        self.assertFalse(builder.bestand_vorhanden())
        self.assertFalse(Inventur.objects.exists())
        self.assertFalse(CustomUser.objects.filter(username="admin").exists())
        # Base units, "Ohne Behälter" and "Nicht zählbar" come from a migration
        # and belong to the application, not to the demo.
        self.assertTrue(Basiseinheit.objects.exists())
        self.assertTrue(Behaelter.objects.filter(immer_erlaubt=True).exists())
        self.assertTrue(Warenfamilie.objects.filter(sammelt_ignorierte=True).exists())

    def test_danach_traegt_die_oberflaeche_kein_band_mehr(self):
        """The marker hangs on the data. With them it vanishes."""
        self.assertTrue(self.band_vorher)
        self.assertFalse(ist_demobestand())


class EnglischeNamen(SimpleTestCase):
    """The table stands next to the blueprints and has to cover them entirely.

    A forgotten name would silently fall back to German, and in an English
    demo a German product would then be on the shelf.
    """

    def namen_der_bauplaene(self) -> set[str]:
        aus_katalog = (
            {f.name for f in catalog.FAMILIEN}
            | {f.beschreibung for f in catalog.FAMILIEN}
            | {b[0] for b in catalog.BEHAELTER}
            | {w.name for w in catalog.WAREN}
            | {z.name for z in catalog.REZEPTURARTIKEL}
            | {g[0] for w in catalog.WAREN for g in w.gebinde}
            | set(catalog.INVENTURARTEN)
        )
        plaene = houses.ARTIKEL_NORD + houses.ARTIKEL_OST + houses.ARTIKEL_SUED
        aus_haeusern = (
            {houses.NORD, houses.OST, houses.WEST, houses.SUED}
            | {a.warengruppe for a in plaene}
            | {a.bezeichnung for a in plaene if a.bezeichnung}
            | {o.bereich for o in houses.ORTE_NORD + houses.ORTE_OST + houses.ORTE_SUED}
            | {o.name for o in houses.ORTE_NORD + houses.ORTE_OST + houses.ORTE_SUED}
        )
        aus_konten = {
            teil for _, vorname, nachname, *_ in users.KONTEN for teil in (vorname, nachname)
        }
        aus_notizen = {notiz for _, _, notiz in inventories.BECHER_DIFFERENZEN if notiz} | {
            notiz for _, notiz in inventories.SOLL_ZAEHLT
        }
        # The loose single unit carries a switch instead of a name.
        return (aus_katalog | aus_haeusern | aus_konten | aus_notizen) - {catalog.EINZELN[0]}

    def test_jeder_name_der_bauplaene_hat_eine_uebersetzung(self):
        fehlend = sorted(self.namen_der_bauplaene() - set(names_en.ENGLISCH))
        self.assertEqual(fehlend, [])

    def test_kein_eintrag_liegt_unbenutzt_herum(self):
        """Otherwise the table would grow with names that no longer exist."""
        # The count sheet carries the same names as its CSV, and it is no text.
        aus_dateien = {
            feld.strip()
            for name in BESCHREIBUNGEN
            if name.endswith(".csv")
            for zeile in datei(name).read_text(encoding="utf-8").splitlines()
            for feld in zeile.split(";")
        }
        benutzt = self.namen_der_bauplaene() | aus_dateien
        ungenutzt = sorted(set(names_en.ENGLISCH) - benutzt)
        self.assertEqual(ungenutzt, [])

    def test_das_einzelstueck_wird_nicht_uebersetzt(self):
        """Its name stays stored, the interface makes "Single" of it."""
        self.assertNotIn(catalog.EINZELN[0], names_en.ENGLISCH)


class EnglischeImportdateien(SimpleTestCase):
    """Under 'exports/en/' lies the same file, only with English designations."""

    def test_zu_jeder_datei_gibt_es_die_englische(self):
        for name in BESCHREIBUNGEN:
            with self.subTest(name=name):
                self.assertTrue(datei(name, "en").is_file())

    def test_sie_entstehen_aus_der_deutschen_mit_der_namenstabelle(self):
        """So they do not drift apart when one of the two sides changes.

        The count sheet is no text file. It arises from the CSV of its language,
        and 'Zaehlblatt' checks that.
        """
        for name in BESCHREIBUNGEN:
            if name.endswith(".xlsx"):
                continue
            with self.subTest(name=name):
                deutsch = datei(name).read_text(encoding="utf-8").splitlines()
                erwartet = [names_en.zeile_in_datei(zeile) for zeile in deutsch]
                englisch = datei(name, "en").read_text(encoding="utf-8").splitlines()
                self.assertEqual(englisch, erwartet)

    def test_das_geruest_der_zeile_bleibt_deutsch(self):
        """The "gekauft in … zu …" is the inventory management system's format, not our text."""
        roh = datei("artikel_kino_west.csv", "en").read_text(encoding="utf-8")
        self.assertIn("gekauft in Kiste(n) zu 24 Stück(e)", roh)
        self.assertIn("Cola 0.5 l", roh)
        self.assertNotIn("Cola 0,5 l", roh)


class Zaehlblatt(SimpleTestCase):
    """Next to the target file of Kino West lies the WinTICKET count sheet. Both say the same."""

    def gelesen(self, name: str, sprache: str) -> list[tuple[str, Decimal, str]]:
        with datei(name, sprache).open("rb") as quelle:
            zeilen = WinTicket().sollbestand_lesen(quelle).zeilen
        return [(zeile.nummer, zeile.menge, zeile.name) for zeile in zeilen]

    def test_das_zaehlblatt_liest_sich_wie_die_csv(self):
        for sprache in count_sheet.SPRACHEN:
            with self.subTest(sprache=sprache):
                aus_blatt = self.gelesen("sollbestand_kino_west.xlsx", sprache)
                self.assertTrue(aus_blatt)
                self.assertEqual(aus_blatt, self.gelesen("sollbestand_kino_west.csv", sprache))

    def test_es_ist_das_blatt_das_aus_der_csv_entsteht(self):
        """Whoever changes the CSV rebuilds the sheet ('python -m demo.count_sheet')."""
        for sprache in count_sheet.SPRACHEN:
            with self.subTest(sprache=sprache):
                mappe = openpyxl.load_workbook(datei("sollbestand_kino_west.xlsx", sprache))
                erstes = mappe.active
                assert erstes is not None
                blatt = [list(zeile) for zeile in erstes.iter_rows(values_only=True)]
                erwartet = count_sheet.zaehlblatt(
                    count_sheet.zeilen_lesen(datei("sollbestand_kino_west.csv", sprache))
                )
                self.assertEqual(blatt, erwartet)

    def test_das_blatt_sieht_aus_wie_gedruckt(self):
        """Header row per page, one storage row, dashes for entering, quantities with comma."""
        blatt = count_sheet.zaehlblatt([("101001", "1234,5", "Ware N")] * 30)
        self.assertEqual(count_sheet.wie_gedruckt("13,861"), "13,861")
        self.assertEqual(blatt[0], list(count_sheet.KOPF))
        self.assertEqual(blatt[1], list(count_sheet.LAGER))
        self.assertEqual(sum(1 for zeile in blatt if zeile[0] == "Artikel"), 3)
        self.assertEqual(
            blatt[2], ["101001", "Ware N", "1.234,50", count_sheet.LEER, count_sheet.LEER]
        )


class EnglischerBestand(TestCase):
    """The same stock, only with English names."""

    bericht: builder.Aufbaubericht

    @classmethod
    def setUpTestData(cls):
        cls.bericht = builder.aufbauen(AUFBAUTAG, "en")

    def test_die_haeuser_heissen_englisch(self):
        self.assertEqual(
            sorted(Haus.objects.values_list("name", flat=True)),
            ["Cinema East", "Cinema North", "Cinema West", "Ice cream workshop South"],
        )

    def test_waren_orte_und_familien_ziehen_mit(self):
        self.assertTrue(Ware.objects.filter(name="Lemon soda 0.5 l").exists())
        self.assertTrue(Ort.objects.filter(name="Counter left").exists())
        self.assertTrue(Warenfamilie.objects.filter(name="Soft drinks").exists())
        self.assertTrue(Warengruppe.objects.filter(name="210 Sales - Popcorn").exists())

    def test_der_bestand_ist_derselbe(self):
        """Names are translated, not quantities. The wiring has to hold.

        The numbers are also in the German build ('DemoBestand'). If they deviate,
        a mapping through a name has reached into nothing.
        """
        self.assertEqual(self.bericht.waren, 81)
        self.assertEqual(self.bericht.rezepturartikel, 7)
        self.assertEqual(self.bericht.hausartikel, 142)
        # 167 until 10/2026, plus the hot dog at the fridge, the lemon syrup in the
        # store, the ice cubes in the freezer and the waffle crumbs in the raw
        # materials store. A wrong click took the last one out again, and its
        # article remembers it ('catalog.VERSEHENTLICH_NICHT_ZAEHLBAR').
        self.assertEqual(self.bericht.erwartungen, 171)

    def test_die_zaehllisten_haben_ihre_gebinde_gefunden(self):
        """The blueprint names them in German, stored they are in English."""
        self.assertTrue(ErwartungGebinde.objects.filter(gebinde__name="Crate").exists())
        self.assertTrue(ErwartungGebinde.objects.filter(standard_menge__isnull=False).exists())

    def test_das_einzelstueck_behaelt_seinen_schalter(self):
        einzeln = Gebinde.objects.filter(ist_einzeln=True)
        self.assertTrue(einzeln.exists())
        self.assertEqual(set(einzeln.values_list("name", flat=True)), {"Einzeln"})
        with translation.override("en"):
            self.assertEqual(str(einzeln[0].anzeigename), "Single")

    def test_die_konten_sprechen_dieselbe_sprache(self):
        konto = CustomUser.objects.get(username="zaehler")
        self.assertEqual(konto.sprache, "en")
        self.assertEqual(konto.last_name, "Siskin")

    def test_der_sollbestand_kam_aus_der_englischen_datei(self):
        """Otherwise it would find the numbers, but the designation would not fit."""
        inventur = Inventur.objects.get(
            haus__name="Cinema North", stichtag=inventories.stichtage(AUFBAUTAG).laufend
        )
        self.assertTrue(inventur.sollbestaende.exists())


@override_settings(DEBUG=True)
class SprachwahlDesBefehls(TestCase):
    """How the language reaches the command. One English build serves the checks."""

    ausgabe: str

    @classmethod
    def setUpTestData(cls):
        ausgabe = StringIO()
        call_command("demo", "-l", "en", date="2026-09-20", stdout=ausgabe)
        cls.ausgabe = ausgabe.getvalue()

    def test_der_kurze_schalter_baut_englisch(self):
        self.assertTrue(Haus.objects.filter(name="Cinema North").exists())

    def test_die_ausgabe_spricht_die_sprache_der_demo(self):
        """Whoever builds the English demo reads the overview in English too."""
        self.assertIn("Sites:", self.ausgabe)
        self.assertIn("Annual inventory", self.ausgabe)
        self.assertIn("Cinema North", self.ausgabe)
        self.assertNotIn("Häuser", self.ausgabe)

    def test_der_lange_schalter_weist_eine_unbekannte_sprache_ab(self):
        """Parsed before anything is built."""
        with self.assertRaises(CommandError):
            call_command("demo", "--language", "kl", verbosity=0)


class SprachvorgabeDesServers(TestCase):
    def test_ohne_angabe_gilt_die_sprache_des_servers(self):
        with override_settings(LANGUAGE_CODE="en", DEBUG=True):
            call_command("demo", date="2026-09-20", verbosity=0)
        self.assertTrue(Haus.objects.filter(name="Cinema North").exists())
