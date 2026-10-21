"""The format layer: the register, the choice in the form and the WinTICKET reader."""

import io
import zipfile
from decimal import Decimal

import openpyxl
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from django_project.form_fields import auswahl
from django_project.testing import AngemeldeteSeiten, inventur_anlegen
from inventur.forms import SollbestandImportForm
from organisation.models import Haus

from . import formats
from .formats import Format, Lesefehler, format_holen, formatwahl
from .formats.winticket import WinTicket
from .formats.workbook import MAX_ZEILEN, arbeitsmappe_pruefen
from .forms import ArtikelImportForm


def datei(inhalt: str, name: str = "datei.csv") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, inhalt.encode("utf-8"), "text/csv")


def mappe(*zeilen: tuple, name: str = "zaehlblatt.xlsx") -> SimpleUploadedFile:
    """An Excel workbook from rows, as WinTICKET writes the count sheet."""
    arbeitsmappe = openpyxl.Workbook()
    blatt = arbeitsmappe.active
    assert blatt is not None
    for zeile in zeilen:
        blatt.append(list(zeile))
    puffer = io.BytesIO()
    arbeitsmappe.save(puffer)
    return SimpleUploadedFile(name, puffer.getvalue(), "application/octet-stream")


def arbeitsmappe_mit(
    dimension: str, fuellung: int = 0, name: str = "soll.xlsx", zeilen: str = ""
) -> SimpleUploadedFile:
    """A zip that looks like a workbook to the check: one sheet with the given size.

    The sheet stands in the namespace openpyxl reads, also under the prefix 'x'.
    """
    haupt = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    blatt = (
        f'<worksheet xmlns="{haupt}" xmlns:x="{haupt}"><dimension ref="{dimension}"/>'
        f"<sheetData>{' ' * fuellung}{zeilen}</sheetData></worksheet>"
    )
    puffer = io.BytesIO()
    with zipfile.ZipFile(puffer, "w", zipfile.ZIP_DEFLATED) as archiv:
        archiv.writestr("[Content_Types].xml", "<Types/>")
        archiv.writestr("xl/workbook.xml", "<workbook/>")
        archiv.writestr("xl/worksheets/sheet1.xml", blatt)
    return SimpleUploadedFile(name, puffer.getvalue(), "application/octet-stream")


#: The header as it recurs per page in the count sheet. "Artikel" spans
#: number and designation, which is why the actual stock is the second entry.
KOPF = ("Artikel", "Istbestand lt. Rechner", "Zählbestand", "Differenz")


class Register(SimpleTestCase):
    def test_jedes_format_hat_kennung_namen_und_hinweise(self):
        for kennung, format in formats.FORMATE.items():
            with self.subTest(kennung=kennung):
                self.assertEqual(format.kennung, kennung)
                self.assertTrue(str(format.name))
                self.assertTrue(format.artikelstamm_endungen)
                self.assertTrue(str(format.artikelstamm_hinweis))
                self.assertTrue(format.sollbestand_endungen)
                self.assertTrue(str(format.sollbestand_hinweis))

    def test_der_standard_ist_winticket(self):
        self.assertIsInstance(format_holen(formats.STANDARD), WinTicket)
        self.assertEqual([kennung for kennung, _name in formatwahl()], ["winticket"])

    def test_die_hilfe_am_feld_nennt_jedes_format(self):
        self.assertIn("WinTICKET", formats.hinweise("artikelstamm"))
        self.assertIn("Artikelnummer;Menge", formats.hinweise("sollbestand"))

    def test_der_vertrag_ist_abstrakt(self):
        with self.assertRaises(NotImplementedError):
            Format().sollbestand_lesen(datei("101001;1"))


class Formulare(TestCase):
    """The choice of format stands on both uploads and decides about the extension."""

    def test_beide_formulare_bieten_dieselben_formate(self):
        for formular in (ArtikelImportForm, SollbestandImportForm):
            with self.subTest(formular=formular.__name__):
                self.assertEqual(auswahl(formular(), "format").choices, formatwahl())

    def test_der_hinweis_am_dateifeld_kommt_vom_format(self):
        self.assertIn("Artikelbestandsliste", ArtikelImportForm().fields["datei"].help_text)
        self.assertIn("Artikelnummer;Menge", SollbestandImportForm().fields["datei"].help_text)

    def test_ein_unbekanntes_format_wird_abgewiesen(self):
        formular = ArtikelImportForm({"format": "xyz"}, {"datei": datei("a;b")})
        self.assertFalse(formular.is_valid())
        self.assertIn("format", formular.errors)

    def test_die_endung_muss_zum_format_passen(self):
        formular = SollbestandImportForm(
            {"format": "winticket"}, {"datei": datei("101001;1", name="soll.pdf")}
        )
        self.assertFalse(formular.is_valid())
        self.assertIn("WinTICKET", str(formular.errors["datei"]))
        self.assertIn("pdf", str(formular.errors["datei"]))

    def test_ohne_endung_nennt_die_meldung_den_dateinamen(self):
        formular = SollbestandImportForm({"format": "winticket"}, {"datei": datei("1;1", "soll")})
        self.assertFalse(formular.is_valid())
        self.assertIn("„soll“", str(formular.errors["datei"]))

    def test_das_gueltige_formular_liefert_das_format_selbst(self):
        formular = SollbestandImportForm({"format": "winticket"}, {"datei": datei("101001;1")})
        self.assertTrue(formular.is_valid(), formular.errors)
        self.assertIsInstance(formular.cleaned_data["format"], WinTicket)


class WinTicketLeser(SimpleTestCase):
    def setUp(self):
        self.format = WinTicket()

    def test_die_nummer_bleibt_wie_die_datei_sie_schreibt(self):
        """WinTICKET has no scheme: "10137" and "101014-1" occur like that."""
        for roh in ("101001", "010137", "10137", "101014-1", "1234567", "12345a"):
            with self.subTest(roh=roh):
                self.assertEqual(self.format.artikelnummer(f" {roh} "), roh)

    def test_eine_nummer_ueber_der_spalte_wird_nicht_zeichenweise_angesehen(self):
        """Longer than its column, it is too long whatever else is wrong with it.

        The caller says so ('zu_lang'). Until 10/2026 every character was
        looked at first: 500 rows of a number of a megabyte took 12 seconds.
        """
        self.assertEqual(self.format.artikelnummer(f" 1 {'2' * 30} "), f"1 {'2' * 30}")
        self.assertIsNone(self.format.artikelnummer(f"1 {'2' * 18}"))

    def test_eine_zu_lange_nummer_mit_leerzeichen_heisst_zu_lang(self):
        gelesen = self.format.sollbestand_lesen(mappe(KOPF, (f"1 {'2' * 30}", "Ware N", "3")))

        self.assertEqual(
            gelesen.fehler,
            [f"Zeile 2: Die Artikelnummer „1 {'2' * 30}“ ist länger als 20 Zeichen."],
        )

    def test_was_nicht_mit_einer_ziffer_beginnt_ist_keine_nummer(self):
        """Sum rows and headings of the export, and spaces have no number."""
        for roh in ("", "  ", "Summe: Warengruppe G", "a12345", "12 34"):
            with self.subTest(roh=roh):
                self.assertIsNone(self.format.artikelnummer(roh))

    def test_sollbestand_mit_kopfzeile_und_bezeichnung(self):
        gelesen = self.format.sollbestand_lesen(
            datei(
                "Artikelnummer;Bestand;Bezeichnung\n101001;1.234,5;Artikel C\n\n101002;Ware N;7\n"
            )
        )
        self.assertEqual(gelesen.fehler, [])
        self.assertEqual(
            [(z.nummer, z.menge, z.name, z.stelle) for z in gelesen.zeilen],
            [
                ("101001", Decimal("1234.500"), "Artikel C", 2),
                ("101002", Decimal("7.000"), "Ware N", 4),
            ],
        )

    def test_unlesbare_zeilen_werden_gemeldet_statt_verworfen(self):
        gelesen = self.format.sollbestand_lesen(datei("101001;1\n101002\n101003;viel\n"))
        self.assertEqual(len(gelesen.zeilen), 1)
        self.assertEqual(len(gelesen.fehler), 2)
        self.assertIn("Zeile 2", gelesen.fehler[0])
        self.assertIn("Zeile 3", gelesen.fehler[1])

    def test_eine_csv_mit_zeilenenden_aus_einem_wagenruecklauf(self):
        """As old Excel for the Mac writes it. Until 10/2026 the reader broke off with a 500."""
        gelesen = self.format.sollbestand_lesen(
            datei("Artikelnummer;Menge\r101001;4\r101002;5,5;Ware N\r\r101003;6\r\n101004;7\n")
        )

        self.assertEqual(gelesen.fehler, [])
        self.assertEqual(
            [(z.nummer, z.menge, z.stelle) for z in gelesen.zeilen],
            [
                ("101001", Decimal("4.000"), 2),
                ("101002", Decimal("5.500"), 3),
                ("101003", Decimal("6.000"), 5),
                ("101004", Decimal("7.000"), 6),
            ],
        )

    def test_ein_wagenruecklauf_mitten_in_der_zeile_beendet_sie(self):
        """It ends the row as in a file of the Mac. What stands behind it is a row of its own."""
        gelesen = self.format.sollbestand_lesen(datei("101001;4\n101002;Ware\rN;5\n"))

        self.assertEqual([z.nummer for z in gelesen.zeilen], ["101001"])
        self.assertEqual(
            gelesen.fehler,
            ["Zeile 2: „101002;Ware“ ist keine Menge.", "Zeile 3: „N;5“ ist keine Menge."],
        )

    def test_ein_feld_ueber_der_grenze_des_lesers_ist_ein_lesefehler(self):
        """Python's reader holds 131,072 characters in a field. Until 10/2026 a server error."""
        inhalt = "101001;4\n101002;" + "N" * 140_000 + "\n101003;5\n"

        with self.assertRaises(Lesefehler) as fang, self.assertLogs("katalog.formats", "WARNING"):
            self.format.sollbestand_lesen(datei(inhalt))

        self.assertEqual(
            str(fang.exception), "Die Datei lässt sich ab Zeile 2 nicht als CSV lesen."
        )

    def test_eine_csv_mit_mehr_zeilen_als_eine_arbeitsmappe_wird_abgewiesen(self):
        """As for a workbook, 50,000 rows. Until 10/2026 a CSV had no limit.

        1 MB of one row repeated made 116,508 rows and 39 MB, the upload
        limit about ten times that.
        """
        with self.assertRaisesMessage(Lesefehler, "Die Datei hat mehr als 50000 Zeilen."):
            self.format.sollbestand_lesen(datei("101001;1\n" * (MAX_ZEILEN + 1)))

        gelesen = self.format.sollbestand_lesen(datei("101001;1\n" * MAX_ZEILEN))
        self.assertEqual(len(gelesen.zeilen), MAX_ZEILEN)

    def test_ein_artikelstamm_als_csv_ebenso(self):
        """The header counts as a row, as it does in a workbook."""
        kopf = "artikelnummer;name;warengruppe;gebinde;anzahl;einheit;haus\n"
        zeile = "101001;Ware N;Warengruppe G;Karton;6;STUECK;Kino Nord\n"
        with self.assertRaisesMessage(Lesefehler, "Die Datei hat mehr als 50000 Zeilen."):
            self.format.artikelstamm_lesen(datei(kopf + zeile * MAX_ZEILEN), "Kino Nord")

    def test_cp1252_wird_gelesen(self):
        rohdaten = "101001;2;Brötchen\n".encode("cp1252")
        gelesen = self.format.sollbestand_lesen(SimpleUploadedFile("s.csv", rohdaten, "text/csv"))
        self.assertEqual(gelesen.zeilen[0].name, "Brötchen")

    def test_das_zaehlblatt_liefert_nur_zeilen_mit_einer_zahl_in_der_dritten_spalte(self):
        """Header rows per page and storage rows drop away without a message. So is the sheet."""
        gelesen = self.format.sollbestand_lesen(
            mappe(
                KOPF,
                ("Lager Nummer 1 Hauptlager",),
                ("101001", "Artikel C", "-1.279,00", "_____", "_____"),
                (101002, "Ware N", 140, "_____", "_____"),
                (101003, "Artikel F", 0.5, "_____", "_____"),
                KOPF,
                (101004, "Ware K", "12", "_____", "_____"),
                (None, None, None, None, None),
            )
        )
        self.assertEqual(gelesen.fehler, [])
        self.assertEqual(
            [(z.nummer, z.menge, z.name, z.stelle) for z in gelesen.zeilen],
            [
                ("101001", Decimal("-1279.000"), "Artikel C", 3),
                ("101002", Decimal("140.000"), "Ware N", 4),
                ("101003", Decimal("0.500"), "Artikel F", 5),
                ("101004", Decimal("12.000"), "Ware K", 7),
            ],
        )

    def test_im_zaehlblatt_bleibt_eine_kurze_nummer_kurz(self):
        """WinTICKET writes "10137" as text. That is the article's name there, so it is here."""
        gelesen = self.format.sollbestand_lesen(
            mappe(KOPF, (10137, "Ware A", -2), ("101014-1", "Ware V", "3"))
        )
        self.assertEqual([z.nummer for z in gelesen.zeilen], ["10137", "101014-1"])

    def test_eine_zahl_neben_etwas_das_keine_nummer_ist_wird_gemeldet(self):
        gelesen = self.format.sollbestand_lesen(
            mappe(KOPF, ("Summe", "", "12"), ("a 1", "Ware N", "3"))
        )
        self.assertEqual(gelesen.zeilen, [])
        self.assertEqual(len(gelesen.fehler), 2)
        self.assertIn("Zeile 2", gelesen.fehler[0])
        self.assertIn("„Summe“", gelesen.fehler[0])

    def test_ein_zaehlblatt_mit_weniger_als_drei_spalten_ist_ein_lesefehler(self):
        """It says what is missing. Until 10/2026 it read as a damaged file."""
        with self.assertRaises(Lesefehler) as fang:
            self.format.sollbestand_lesen(mappe(("Artikel", "Bestand"), ("101001", "1")))
        self.assertEqual(
            str(fang.exception),
            "Das Zählblatt braucht drei Spalten: Artikelnummer, Bezeichnung und Bestand. Diese "
            "Mappe hat 2. Mit zwei Spalten geht die Datei als CSV („Artikelnummer;Menge“).",
        )

    def test_etwas_das_keine_mappe_ist_ist_ein_lesefehler(self):
        """In the person's words. The library's reason goes to the log, not to the page."""
        with self.assertRaises(Lesefehler) as fang, self.assertLogs("katalog.formats", "WARNING"):
            self.format.sollbestand_lesen(datei("101001;1", name="soll.xlsx"))
        self.assertEqual(
            str(fang.exception), "Die Datei ist keine Excel-Arbeitsmappe oder beschädigt."
        )

    def test_zeilen_mit_nummer_aber_ohne_menge_werden_gezaehlt(self):
        """A formula without a stored value, a date in the column: said, not swallowed."""
        gelesen = self.format.sollbestand_lesen(
            mappe(
                KOPF,
                ("101001", "Artikel C", "12", "_____", "_____"),
                ("101002", "Ware N", None, "_____", "_____"),
                ("101003", "Artikel F", "heute", "_____", "_____"),
                ("Lager Nummer 1 Hauptlager",),
            )
        )
        self.assertEqual([z.nummer for z in gelesen.zeilen], ["101001"])
        self.assertEqual(
            gelesen.fehler,
            ["2 Zeilen mit Artikelnummer, aber ohne lesbare Menge, übergangen."],
        )

    def test_eine_arbeitsmappe_mit_zu_vielen_zeilen_wird_vor_dem_lesen_abgewiesen(self):
        with self.assertRaises(Lesefehler) as fang:
            self.format.sollbestand_lesen(arbeitsmappe_mit(dimension="A1:C60000"))
        self.assertIn("mehr als 50000 Zeilen", str(fang.exception))

    def test_eine_arbeitsmappe_die_entpackt_zu_gross_ist_wird_vor_dem_lesen_abgewiesen(self):
        with self.assertRaises(Lesefehler) as fang:
            self.format.sollbestand_lesen(
                arbeitsmappe_mit(dimension="A1:C10", fuellung=41 * 1024 * 1024)
            )
        self.assertIn("entpackt", str(fang.exception))

    def test_der_artikelstamm_hat_dieselbe_grenze(self):
        with self.assertRaises(Lesefehler):
            self.format.artikelstamm_lesen(arbeitsmappe_mit(dimension="A1:H60000"), "Kino Ost")

    def test_gezaehlt_werden_die_zeilen_nicht_nur_das_tag(self):
        """A sheet that claims two rows and carries more is measured by what it carries.

        Until 10/2026 the dimension tag alone counted, and pandas read every
        row behind it. A row without a number follows the one before, as
        openpyxl reads it. The rows lie in several pieces of a megabyte, and
        the limit holds exactly: one row more is refused, one less is not.
        With a namespace prefix, as some writers put it, they count the same.
        """
        for praefix in ("", "x:"):
            zeile = f"<{praefix}row><{praefix}c><{praefix}v>1</{praefix}v></{praefix}c>"
            zeile += f"</{praefix}row>"
            with self.subTest(praefix=praefix):
                with self.assertRaises(Lesefehler) as fang:
                    arbeitsmappe_pruefen(arbeitsmappe_mit(dimension="A1:C2", zeilen=zeile * 50_001))
                self.assertIn("mehr als 50000 Zeilen", str(fang.exception))
                arbeitsmappe_pruefen(arbeitsmappe_mit(dimension="A1:C2", zeilen=zeile * 50_000))

    def test_ein_beschaedigtes_blatt_meldet_der_leser(self):
        """Counting reads the whole sheet. A damaged one is the reader's sentence, not a 500.

        Broken at its start, the packed data throw 'zlib.error', in the middle
        only the checksum fails ('BadZipFile'). The check refuses it itself:
        until 10/2026 it let it pass and forgot what it had counted.
        """
        heil = mappe(KOPF, *(("101001", "Artikel C", "12"),) * 2000).read()
        eintrag = zipfile.ZipFile(io.BytesIO(heil)).getinfo("xl/worksheets/sheet1.xml")
        kopf = eintrag.header_offset
        # The sheet's data begin behind its local header: 30 bytes, name and extra field.
        beginn = kopf + 30 + int.from_bytes(heil[kopf + 26 : kopf + 28], "little")
        beginn += int.from_bytes(heil[kopf + 28 : kopf + 30], "little")
        for stelle in (beginn, beginn + eintrag.compress_size // 2):
            with self.subTest(stelle=stelle - beginn):
                kaputt = heil[:stelle] + b"\xff" * 8 + heil[stelle + 8 :]
                with self.assertRaisesMessage(Lesefehler, "beschädigt"):
                    arbeitsmappe_pruefen(io.BytesIO(kaputt))
                with self.assertRaises(Lesefehler):
                    self.format.sollbestand_lesen(
                        SimpleUploadedFile("soll.xlsx", kaputt, "application/octet-stream")
                    )

    def test_zu_lange_nummern_und_bezeichnungen_bleiben_draussen(self):
        """Their columns hold 20 and 200 characters. The rest of the file is read."""
        lang = "1" + "0" * 24
        for gelesen in (
            self.format.sollbestand_lesen(
                datei(f"{lang};4;Ware N\n101002;3;{'N' * 201}\n101003;2;Ware M\n")
            ),
            self.format.sollbestand_lesen(
                mappe(
                    KOPF,
                    (lang, "Ware N", "4"),
                    ("101002", "N" * 201, "3"),
                    ("101003", "Ware M", "2"),
                )
            ),
        ):
            with self.subTest(gelesen=gelesen):
                self.assertEqual([zeile.nummer for zeile in gelesen.zeilen], ["101003"])
                self.assertEqual(len(gelesen.fehler), 2)
                self.assertIn(
                    f"Die Artikelnummer „{lang}“ ist länger als 20 Zeichen.", gelesen.fehler[0]
                )
                self.assertIn("Die Bezeichnung ist länger als 200 Zeichen.", gelesen.fehler[1])

    def test_eine_kleine_mappe_geht_durch_die_pruefung(self):
        """The check reads the zip only. A real workbook of a few rows passes it untouched."""
        arbeitsmappe_pruefen(mappe(KOPF, ("101001", "Artikel C", "12")))

    def test_ohne_dateinamen_gilt_die_datei_als_csv(self):
        gelesen = self.format.sollbestand_lesen(io.BytesIO(b"101001;4;Ware N\n"))
        self.assertEqual([z.menge for z in gelesen.zeilen], [Decimal("4")])

    def test_eine_datei_mehrerer_haeuser_wird_abgewiesen(self):
        """The command line writes 'artikel_combined.csv' for several houses.

        Uploaded for one, until 10/2026 the other house's articles came into
        its plan, and rows of one number from two houses merged.
        """
        inhalt = (
            "artikelnummer;name;warengruppe;gebinde;anzahl;einheit;haus\n"
            "101001;Artikel C;Warengruppe G;Kasten;24;STUECK;Kino Nord\n"
            "101001;Pfand 0,15;Warengruppe P;Karton;1;STUECK;Kino Ost\n"
        )
        with self.assertRaisesMessage(
            Lesefehler,
            "Die Datei führt Artikel mehrerer Häuser (Kino Nord, Kino Ost). Bitte je Haus eine "
            "eigene Datei hochladen.",
        ):
            self.format.artikelstamm_lesen(datei(inhalt, name="artikel_combined.csv"), "Kino Nord")

    def test_eine_umgewandelte_datei_eines_hauses_gilt_fuer_das_gewaehlte(self):
        """Under the name the command line was given, which need not be the house's of today."""
        inhalt = (
            "artikelnummer;name;warengruppe;gebinde;anzahl;einheit;haus\n"
            "101001;Artikel C;Warengruppe G;Kasten;24;STUECK;Nord\n"
        )
        tabelle = self.format.artikelstamm_lesen(datei(inhalt), "Kino Nord")
        self.assertEqual(list(tabelle["haus"]), ["Kino Nord"])

    def test_ein_unbrauchbarer_artikelstamm_ist_ein_lesefehler(self):
        with self.assertRaises(Lesefehler) as fang:
            self.format.artikelstamm_lesen(datei("", name="leer.csv"), "Kino Nord")
        self.assertIn("leer.csv", str(fang.exception))


class SollbestandSeite(AngemeldeteSeiten):
    """The target stock as CSV on the inventory page. What the reader cannot read is a sentence."""

    def setUp(self):
        super().setUp()
        self.inventur = inventur_anlegen(Haus.objects.create(name="Kino Nord"))

    def hochladen(self, inhalt: str):
        return self.client.post(
            self.inventur.get_absolute_url(), {"datei": datei(inhalt), "format": "winticket"}
        )

    def test_eine_csv_mit_wagenruecklauf_wird_gelesen(self):
        antwort = self.hochladen("101001;4\r101002;5\r")

        self.assertEqual(antwort.status_code, 302)
        self.assertEqual(
            sorted(self.inventur.sollzeilen.values_list("artikelnummer", flat=True)),
            ["101001", "101002"],
        )

    def test_ein_zu_langes_feld_steht_am_feld(self):
        with self.assertLogs("katalog.formats", "WARNING"):
            antwort = self.hochladen("101001;4\n101002;" + "N" * 140_000 + "\n")

        self.assertEqual(antwort.status_code, 200)
        self.assertEqual(
            antwort.context["form"].errors["datei"],
            ["Die Datei lässt sich ab Zeile 2 nicht als CSV lesen."],
        )
        self.assertFalse(self.inventur.sollzeilen.exists())


class ImportSeite(AngemeldeteSeiten):
    """The format's read error lands on the file field."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.haus = Haus.objects.create(name="Kino Nord")

    def test_lesefehler_steht_am_feld(self):
        antwort = self.client.post(
            reverse("hausartikel_import", args=[self.haus.pk]),
            {"format": "winticket", "datei": datei("a;b\n", name="leer.csv")},
        )
        self.assertEqual(antwort.status_code, 200)
        self.assertIn("leer.csv", str(antwort.context["form"].errors["datei"]))

    def test_eine_datei_mehrerer_haeuser_steht_am_feld(self):
        inhalt = (
            "artikelnummer;name;warengruppe;gebinde;anzahl;einheit;haus\n"
            "101001;Artikel C;Warengruppe G;Kasten;24;STUECK;Kino Nord\n"
            "101002;Ware N;Warengruppe G;Karton;12;STUECK;Kino Ost\n"
        )
        antwort = self.client.post(
            reverse("hausartikel_import", args=[self.haus.pk]),
            {"format": "winticket", "datei": datei(inhalt, name="artikel_combined.csv")},
        )
        self.assertEqual(antwort.status_code, 200)
        self.assertIn("mehrerer Häuser", str(antwort.context["form"].errors["datei"]))
        self.assertNotIn("formset", antwort.context)
