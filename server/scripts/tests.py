"""Tests for 'scripts.loader'.

The test data are deliberately tiny Compeso excerpts built in code: the real
exports under 'data/' are not part of the test fixtures and should not
become so.
"""

import argparse
import io
import os
import tempfile

import pandas as pd
from django.test import SimpleTestCase

from django_project.decimals import dezimal_normalisieren

from . import normalize
from .cli_handlers import quellen_je_haus
from .cli_parser import get_parser, haus_und_datei
from .loader import (
    COLUMNS,
    MAX_ZEILEN,
    LoaderError,
    drop_leere_gebinde,
    get_artikel_und_warengruppen,
    is_transformed,
    read_dataframe,
    rename_rohspalten,
    source_name,
)

# This is what a Compeso export looks like: product groups as an interim
# row, then per article a row with number and designation and a 'gekauft in' info row.
COMPESO_ROWS = [
    ["01 Beispielwarengruppe A", "", "", "", ""],
    ["910001", "Beispielartikel A", "Lieferant A", " ", "2,00"],
    ["", "gekauft in Karton zu 24 Stück(e)", "Bestand: 0,00 Stück(e) zu", "1,00 =", "0,00"],
    ["910002", "Beispielartikel B", "Lieferant B", " ", "3,00"],
    ["", "gekauft in Kiste(n) zu 1 Stück(e)", "Bestand: -10,00 Stück(e) zu", "1,50 =", "-15,00"],
    ["910003", "Beispielartikel C", "Lieferant C", " ", "4,00"],
    ["", "gekauft in Kiste(n) zu 20 Stück(e)", "Bestand: 0,00 Stück(e) zu", "1,00 =", "0,00"],
    ["Summe: Beispielwarengruppe A", "0,00", "", "", ""],
]
COMPESO_HEADER = ["Artikel", "Lieferant", "Unnamed: 2", "VKPreis", "Lagerwert"]


def compeso_csv(name: str = "Artikel_Test.csv", encoding: str = "utf-8") -> io.BytesIO:
    zeilen = [";".join(COMPESO_HEADER)]
    zeilen += [";".join(spalten) for spalten in COMPESO_ROWS]
    datei = io.BytesIO("\n".join(zeilen).encode(encoding))
    datei.name = name
    return datei


def compeso_xlsx(name: str = "Artikel_Test.xlsx") -> io.BytesIO:
    puffer = io.BytesIO()
    pd.DataFrame(COMPESO_ROWS, columns=COMPESO_HEADER).to_excel(puffer, index=False)
    puffer.seek(0)
    puffer.name = name
    return puffer


def transformierte_csv(name: str = "artikel_combined.csv") -> io.BytesIO:
    inhalt = (
        "artikelnummer;name;warengruppe;gebinde;anzahl;einheit;haus\n"
        "910001;Beispielartikel A;01 Beispielwarengruppe A;Karton;24.0;Stück(e);Kino Süd\n"
        "910002;Beispielartikel B;02 Beispielwarengruppe B;Kiste(n);1.0;Stück(e);Kino Süd\n"
    )
    datei = io.BytesIO(inhalt.encode("utf-8"))
    datei.name = name
    return datei


class SourceNameTests(SimpleTestCase):
    def test_pfad(self):
        self.assertEqual(source_name("/tmp/data/Artikel_Sued.xlsx"), "Artikel_Sued.xlsx")

    def test_upload(self):
        self.assertEqual(source_name(compeso_csv("Export.csv")), "Export.csv")

    def test_ohne_namen(self):
        self.assertEqual(source_name(io.BytesIO(b"")), "unbenannt")


class ReadDataframeTests(SimpleTestCase):
    def test_csv_wird_als_text_gelesen(self):
        df = read_dataframe(compeso_csv())
        self.assertEqual(list(df.columns), COMPESO_HEADER)
        self.assertEqual(df.iloc[1, 0], "910001")

    def test_xlsx(self):
        df = read_dataframe(compeso_xlsx())
        self.assertEqual(list(df.columns), COMPESO_HEADER)

    def test_cp1252_wird_erkannt(self):
        df = read_dataframe(compeso_csv(encoding="cp1252"))
        self.assertIn("Stück(e)", str(df.iloc[2, 1]))

    def test_unbekanntes_format(self):
        datei = io.BytesIO(b"egal")
        datei.name = "artikel.txt"
        with self.assertRaisesMessage(LoaderError, "nicht unterstütztes Format"):
            read_dataframe(datei)


class ZeilenTests(SimpleTestCase):
    """A CSV has as many rows as a workbook may have, the header counted.

    Until 10/2026 a CSV had no limit. A file of the upload limit carries a
    million short rows, and pandas and the transformation took them all.
    """

    @staticmethod
    def csv(zeilen: int) -> io.BytesIO:
        datei = io.BytesIO(("Artikel;Lieferant\n" + "101001;Ware N\n" * zeilen).encode())
        datei.name = "artikel.csv"
        return datei

    def test_bis_an_die_grenze(self):
        self.assertEqual(len(read_dataframe(self.csv(MAX_ZEILEN - 1))), MAX_ZEILEN - 1)

    def test_eine_zeile_mehr(self):
        with self.assertRaisesMessage(LoaderError, "Die Datei hat mehr als 50000 Zeilen."):
            read_dataframe(self.csv(MAX_ZEILEN))

    def test_die_grenze_ist_die_der_arbeitsmappe(self):
        """The loader runs without Django, so the number stands there once more."""
        from katalog.formats.workbook import MAX_ZEILEN as ARBEITSMAPPE  # noqa: PLC0415

        self.assertEqual(MAX_ZEILEN, ARBEITSMAPPE)


class RenameRohspaltenTests(SimpleTestCase):
    def test_benennt_die_ersten_beiden_spalten(self):
        df = rename_rohspalten(read_dataframe(compeso_csv()))
        self.assertEqual(list(df.columns), ["artikelnummer", "name"])

    def test_laesst_benannte_spalten_in_ruhe(self):
        vorher = pd.DataFrame({"artikelnummer": ["000001"], "name": ["Beispielartikel"]})
        self.assertEqual(list(rename_rohspalten(vorher).columns), ["artikelnummer", "name"])

    def test_eine_spalte_ist_zu_wenig(self):
        with self.assertRaises(LoaderError):
            rename_rohspalten(pd.DataFrame({"nur_eine": ["x"]}), "kaputt.csv")


class IsTransformedTests(SimpleTestCase):
    def test_erkennt_zielspalten(self):
        self.assertTrue(is_transformed(pd.DataFrame(columns=COLUMNS)))

    def test_rohexport_ist_nicht_transformiert(self):
        self.assertFalse(is_transformed(pd.DataFrame(columns=COMPESO_HEADER)))


class GetArtikelUndWarengruppenTests(SimpleTestCase):
    """The sources come as {house name: source}. The key becomes the house column."""

    def test_rohe_csv(self):
        artikel, warengruppen = get_artikel_und_warengruppen({"Kino Süd": compeso_csv()})
        self.assertEqual(list(artikel.columns), COLUMNS)
        self.assertEqual(artikel["artikelnummer"].tolist(), ["910001", "910002", "910003"])
        # Count 1 on piece goods is no pack but the single piece.
        # 'Kiste(n)' is called 'Kasten' in the house.
        self.assertEqual(artikel["gebinde"].tolist(), ["Karton", "Einzeln", "Kasten"])
        self.assertEqual(artikel["anzahl"].tolist(), [24.0, 1.0, 20.0])
        self.assertEqual(artikel["einheit"].tolist(), [normalize.STUECK] * 3)
        self.assertEqual(artikel["haus"].tolist(), ["Kino Süd"] * 3)
        self.assertEqual(warengruppen["warengruppe"].tolist(), ["01 Beispielwarengruppe A"])

    def test_eine_nummer_mit_zusatz_ist_ein_artikel_und_keine_warengruppe(self):
        """WinTICKET carries "101014-1". The product group row has no designation."""
        zeilen = [
            ["01 Beispielwarengruppe A", "", "", "", ""],
            ["101014-1", "Beispielartikel V", "Lieferant A", " ", "2,00"],
            ["", "gekauft in Karton zu 6 Stück(e)", "Bestand: 0,00 Stück(e) zu", "1,00 =", "0,00"],
            ["Summe: Beispielwarengruppe A", "0,00", "", "", ""],
        ]
        datei = io.BytesIO(
            "\n".join(";".join(spalten) for spalten in [COMPESO_HEADER, *zeilen]).encode("utf-8")
        )
        datei.name = "Artikel_Test.csv"
        artikel, warengruppen = get_artikel_und_warengruppen({"Kino Süd": datei})
        self.assertEqual(artikel["artikelnummer"].tolist(), ["101014-1"])
        self.assertEqual(artikel["warengruppe"].tolist(), ["01 Beispielwarengruppe A"])
        self.assertEqual(warengruppen["warengruppe"].tolist(), ["01 Beispielwarengruppe A"])

    @staticmethod
    def export(*zeilen: list[str]) -> io.BytesIO:
        datei = io.BytesIO(
            "\n".join(";".join(spalten) for spalten in [COMPESO_HEADER, *zeilen]).encode("utf-8")
        )
        datei.name = "Artikel_Test.csv"
        return datei

    @staticmethod
    def info(text: str) -> list[str]:
        return ["", text, "Bestand: 0,00 Stück(e) zu", "1,00 =", "0,00"]

    @staticmethod
    def artikel(nummer: str, name: str) -> list[str]:
        return [nummer, name, "Lieferant A", " ", "2,00"]

    def gebinde(self, *zeilen: list[str]) -> list[tuple]:
        artikel, _ = get_artikel_und_warengruppen({"Kino Süd": self.export(*zeilen)})
        return [
            (nummer, None if pd.isna(gebinde) else gebinde, None if pd.isna(anzahl) else anzahl, e)
            for nummer, gebinde, anzahl, e in zip(
                artikel["artikelnummer"],
                artikel["gebinde"],
                artikel["anzahl"],
                artikel["einheit"],
                strict=True,
            )
        ]

    def test_eine_fehlende_infozeile_verschiebt_nichts(self):
        """Each info row belongs to the article above it.

        Until 10/2026 the i-th went to the i-th article: B took the crate of C,
        C the litre bottle of D, and D stood without anything.
        """
        self.assertEqual(
            self.gebinde(
                ["01 Beispielwarengruppe A", "", "", "", ""],
                self.artikel("910001", "Beispielartikel A"),
                self.info("gekauft in Karton zu 24 Stück(e)"),
                self.artikel("910002", "Beispielartikel B"),
                self.artikel("910003", "Beispielartikel C"),
                self.info("gekauft in Kiste(n) zu 20 Stück(e)"),
                self.artikel("910004", "Beispielartikel D"),
                self.info("gekauft in Flasche(n) zu 1 Liter"),
            ),
            [
                ("910001", "Karton", 24.0, normalize.STUECK),
                ("910002", "", None, normalize.STUECK),
                ("910003", "Kasten", 20.0, normalize.STUECK),
                ("910004", "Flasche", 1.0, normalize.LITER),
            ],
        )

    def test_eine_warengruppenzeile_beendet_den_artikel_darueber(self):
        """An info row behind a product group row belongs to nobody, not to the article before."""
        self.assertEqual(
            self.gebinde(
                ["01 Beispielwarengruppe A", "", "", "", ""],
                self.artikel("910001", "Beispielartikel A"),
                self.info("gekauft in Karton zu 24 Stück(e)"),
                ["02 Beispielwarengruppe B", "", "", "", ""],
                self.info("gekauft in Kiste(n) zu 20 Stück(e)"),
                self.artikel("910002", "Beispielartikel B"),
                self.info("gekauft in Karton zu 6 Stück(e)"),
            ),
            [
                ("910001", "Karton", 24.0, normalize.STUECK),
                ("910002", "Karton", 6.0, normalize.STUECK),
            ],
        )

    def test_eine_infozeile_wird_nur_bis_zur_grenze_gelesen(self):
        """Beyond 200 characters no real info row goes, and the pattern costs the square.

        Until 10/2026 every info row of 4,000 characters took 54 ms, and a CSV
        of the upload limit two minutes. A pack name the plan refuses as too
        long still comes through, so the plan can say so.
        """
        self.assertEqual(
            self.gebinde(
                ["01 Beispielwarengruppe A", "", "", "", ""],
                self.artikel("910001", "Beispielartikel A"),
                self.info(f"gekauft in {'K' * 300} zu 6 Stück(e)"),
                self.artikel("910002", "Beispielartikel B"),
                self.info(f"gekauft in {'K' * 150} zu 6 Stück(e)"),
            ),
            [
                ("910001", "", None, normalize.STUECK),
                ("910002", "K" * 150, 6.0, normalize.STUECK),
            ],
        )

    def test_gekauft_in_zu_0_bleibt_ohne_gebinde(self):
        """It names neither pack nor unit. Until 10/2026 the article fell out here.

        Then the plan offered a carried one under "Missing from the file", and
        a new one never came into it. The second spelling stayed all along.
        """
        self.assertEqual(
            self.gebinde(
                ["01 Beispielwarengruppe A", "", "", "", ""],
                self.artikel("910001", "Beispielartikel A"),
                self.info("gekauft in zu 0"),
                self.artikel("910002", "Beispielartikel B"),
                self.info("gekauft in  zu 0"),
            ),
            [
                ("910001", "", 0.0, normalize.STUECK),
                ("910002", "", 0.0, normalize.STUECK),
            ],
        )

    def test_die_anzahl_liest_sich_wie_jede_andere_zahl(self):
        """Until 10/2026 "1.000,00" broke off the import. "1.000" stays one, as everywhere."""
        self.assertEqual(
            self.gebinde(
                ["01 Beispielwarengruppe A", "", "", "", ""],
                self.artikel("910001", "Beispielartikel A"),
                self.info("gekauft in Karton zu 1.000,00 g"),
                self.artikel("910002", "Beispielartikel B"),
                self.info("gekauft in Karton zu 2,5 kg"),
            ),
            [
                ("910001", "Karton", 1000.0, normalize.GRAMM),
                ("910002", "Karton", 2.5, normalize.KILOGRAMM),
            ],
        )

    def test_eine_unlesbare_anzahl_nennt_den_grund_im_protokoll(self):
        """The reason is English and technical. Until 10/2026 it stood in the sentence."""
        datei = self.export(
            self.artikel("910001", "Beispielartikel A"), self.info("gekauft in Karton zu 1.2.3 g")
        )
        with self.assertRaises(LoaderError) as fang, self.assertLogs("scripts.loader") as protokoll:
            get_artikel_und_warengruppen({"Kino Süd": datei})
        self.assertEqual(
            str(fang.exception),
            "Artikel_Test.csv: Die Umwandlung ist gescheitert. Der Grund steht im Protokoll.",
        )
        self.assertIn("1.2.3", protokoll.output[0])

    def test_rohes_xlsx_ergibt_dasselbe(self):
        aus_csv, _ = get_artikel_und_warengruppen({"Kino Süd": compeso_csv()})
        aus_xlsx, _ = get_artikel_und_warengruppen({"Kino Süd": compeso_xlsx()})
        self.assertTrue(aus_csv.astype(str).equals(aus_xlsx.astype(str)))

    def test_bereits_transformierte_datei_bleibt_unveraendert(self):
        artikel, warengruppen = get_artikel_und_warengruppen({"Kino Süd": transformierte_csv()})
        self.assertEqual(artikel.shape, (2, len(COLUMNS)))
        self.assertFalse(artikel["warengruppe"].isna().any())
        self.assertEqual(artikel.loc[0, "gebinde"], "Karton")
        self.assertEqual(len(warengruppen), 2)

    def test_mehrere_haeuser_werden_zusammengefuehrt(self):
        """The same product in two houses stays twice. The houses separate them."""
        artikel, _ = get_artikel_und_warengruppen(
            {"Kino Süd": compeso_csv(), "Kino Nord": compeso_xlsx()}
        )
        self.assertEqual(
            artikel["artikelnummer"].tolist(),
            ["910001", "910002", "910003", "910001", "910002", "910003"],
        )
        self.assertEqual(set(artikel["haus"]), {"Kino Süd", "Kino Nord"})

    def test_ohne_dateien(self):
        with self.assertRaises(LoaderError):
            get_artikel_und_warengruppen({})

    def test_leere_datei(self):
        datei = io.BytesIO(b"Artikel;Lieferant\n")
        datei.name = "leer.csv"
        with self.assertRaisesMessage(LoaderError, "enthält keine Zeilen"):
            get_artikel_und_warengruppen({"Kino Süd": datei})

    def test_dest_legt_die_ergebnisse_ab(self):
        with tempfile.TemporaryDirectory() as ordner:
            get_artikel_und_warengruppen({"Kino Süd": compeso_csv()}, dest=ordner)
            self.assertTrue(os.path.isfile(os.path.join(ordner, "artikel_combined.csv")))
            self.assertTrue(os.path.isfile(os.path.join(ordner, "warengruppe.csv")))
            wieder = pd.read_csv(os.path.join(ordner, "artikel_combined.csv"), sep=";", dtype=str)
            self.assertEqual(wieder["artikelnummer"].tolist(), ["910001", "910002", "910003"])


def zeile(
    nummer,
    name,
    gebinde,
    anzahl,
    gruppe="01 Beispielwarengruppe A",
    einheit=normalize.STUECK,
    haus="Kino Süd",
):
    """A row of the finished article table, as 'COLUMNS' describes it.

    'einheit' carries the id, 'einheit_roh' the word from the file. Both are
    on the same value here where the test does not distinguish them.
    """
    return [nummer, name, gruppe, gebinde, anzahl, einheit, haus, einheit]


class DropLeereGebindeTests(SimpleTestCase):
    """A 0 is a gap as soon as another house has maintained the same article."""

    # Per case the rows of the table and what of them remains: name, pack, count.
    FAELLE = {
        "null fällt gegen echte Anzahl weg": (
            [
                zeile("910003", "Beispielartikel C", "Kiste(n)", 0.0),
                zeile("910003", "Beispielartikel C", "Kiste(n)", 24.0),
            ],
            [("Beispielartikel C", "Kiste(n)", 24.0)],
        ),
        # Otherwise the article would vanish entirely. It just should get no pack.
        "null bleibt, wenn niemand einen Wert hat": (
            [zeile("910004", "Beispielartikel D", "Karton", 0.0)],
            [("Beispielartikel D", "Karton", 0.0)],
        ),
        "leere Anzahl zählt wie null": (
            [
                zeile("910003", "Beispielartikel C", "Kiste(n)", None),
                zeile("910003", "Beispielartikel C", "Kiste(n)", 24.0),
            ],
            [("Beispielartikel C", "Kiste(n)", 24.0)],
        ),
        # The 0 belongs to "Beispielartikel F", not to "Beispielartikel E".
        "abweichender Name bleibt als eigener Konflikt": (
            [
                zeile("910005", "Beispielartikel E", "Karton", 20.0),
                zeile("910005", "Beispielartikel F", "Karton", 0.0),
            ],
            [("Beispielartikel E", "Karton", 20.0), ("Beispielartikel F", "Karton", 0.0)],
        ),
        "null fällt auch bei anderem Gebinde weg": (
            [
                zeile("910002", "Beispielartikel B", "Karton", 0.0),
                zeile("910002", "Beispielartikel B", "Kiste(n)", 1.0),
            ],
            [("Beispielartikel B", "Kiste(n)", 1.0)],
        ),
        # The gap is under 'Karton'/'Stück', the value under 'Beutel'/'Gramm'.
        "null fällt auch bei anderer Einheit weg": (
            [
                zeile("910006", "Beispielartikel G", "Karton", 0.0, einheit="Stück(e)"),
                zeile("910006", "Beispielartikel G", "Beutel", 1000.0, einheit="g"),
            ],
            [("Beispielartikel G", "Beutel", 1000.0)],
        ),
        # One house carries the number under another name. Its 0 does not belong.
        "abweichender Name schützt seine null": (
            [
                zeile("910006", "Beispielartikel G anders", "Karton", 0.0),
                zeile("910006", "Beispielartikel G", "Beutel", 1000.0),
            ],
            [
                ("Beispielartikel G", "Beutel", 1000.0),
                ("Beispielartikel G anders", "Karton", 0.0),
            ],
        ),
        # 500 against 1000 is no gap but a contradiction. That belongs reported.
        "echter Faktorunterschied bleibt stehen": (
            [
                zeile("910007", "Beispielartikel H", "Tüte(n)", 500.0),
                zeile("910007", "Beispielartikel H", "Tüte(n)", 1000.0),
            ],
            [("Beispielartikel H", "Tüte(n)", 500.0), ("Beispielartikel H", "Tüte(n)", 1000.0)],
        ),
    }

    def test_was_stehen_bleibt(self):
        for fall, (zeilen, erwartet) in self.FAELLE.items():
            with self.subTest(fall):
                gefiltert = drop_leere_gebinde(pd.DataFrame(zeilen, columns=COLUMNS))
                uebrig = sorted(zip(gefiltert["name"], gefiltert["gebinde"], gefiltert["anzahl"]))
                self.assertEqual(uebrig, sorted(erwartet))

    def test_greift_ueber_haeuser_hinweg(self):
        """The 0 of one house drops against the maintained value of the other.

        Grouped only by number and designation. The house is deliberately not in
        the key.
        """
        inhalt = (
            "artikelnummer;name;warengruppe;gebinde;anzahl;einheit;haus\n"
            "910001;Beispielartikel A;01 Beispielwarengruppe A;"
            "Karton;0.0;Stück(e);Kino Nord\n"
        )
        ohne_anzahl = io.BytesIO(inhalt.encode())
        ohne_anzahl.name = "haus_a.csv"
        artikel, _ = get_artikel_und_warengruppen(
            {"Kino Nord": ohne_anzahl, "Kino Süd": transformierte_csv()}
        )
        artikel_a = artikel[artikel["artikelnummer"] == "910001"]
        self.assertEqual(artikel_a["anzahl"].tolist(), ["24.0"])


class CliQuellenTests(SimpleTestCase):
    """The house name hangs on the file, not in a second list next to it."""

    def parse(self, *argumente):
        return get_parser().parse_args(["artikel", *argumente])

    def test_haus_und_datei_werden_getrennt(self):
        args = self.parse("Kino Nord=nord.xlsx", "Kino Ost=ost.csv")
        self.assertEqual(args.quellen, [("Kino Nord", "nord.xlsx"), ("Kino Ost", "ost.csv")])

    def test_leerzeichen_um_den_trenner_stoeren_nicht(self):
        self.assertEqual(self.parse(" Kino Süd = sued.xlsx ").quellen, [("Kino Süd", "sued.xlsx")])

    def test_gleichheitszeichen_im_pfad_bleibt_beim_pfad(self):
        """Split at the first '='. House names contain none."""
        self.assertEqual(
            haus_und_datei("Kino West=/tmp/export=2026.csv"), ("Kino West", "/tmp/export=2026.csv")
        )

    def test_ohne_hausname_wird_abgewiesen(self):
        for wert in ("nord.xlsx", "=nord.xlsx", "Kino Nord="):
            with self.subTest(wert=wert), self.assertRaises(argparse.ArgumentTypeError):
                haus_und_datei(wert)

    def test_argparse_meldet_den_fehler_statt_abzustuerzen(self):
        with self.assertRaises(SystemExit):
            self.parse("nord.xlsx")

    def test_quellen_werden_zum_dict_fuer_den_loader(self):
        self.assertEqual(
            quellen_je_haus([("Kino Nord", "a.xlsx"), ("Kino Süd", "e.xlsx")]),
            {"Kino Nord": "a.xlsx", "Kino Süd": "e.xlsx"},
        )

    def test_dasselbe_haus_zweimal_ist_ein_vertipper(self):
        """Without the check the dict would silently keep only the second file."""
        with self.assertRaises(SystemExit):
            quellen_je_haus([("Kino Nord", "a.xlsx"), ("Kino Nord", "b.xlsx")])


class CliArtikelTests(SimpleTestCase):
    """The run from the command line to the written file."""

    def lauf(self, *argumente):
        args = get_parser().parse_args(["artikel", *argumente])
        args.func(args)

    def test_zwei_haeuser_landen_beide_in_der_tabelle(self):
        with tempfile.TemporaryDirectory() as ordner:
            for haus in ("Kino Nord", "Kino Süd"):
                with open(os.path.join(ordner, f"{haus}.csv"), "wb") as datei:
                    datei.write(compeso_csv().getvalue())
            ziel = os.path.join(ordner, "ausgabe")

            self.lauf(
                "-s",
                ziel,
                f"Kino Nord={os.path.join(ordner, 'Kino Nord.csv')}",
                f"Kino Süd={os.path.join(ordner, 'Kino Süd.csv')}",
            )
            geschrieben = pd.read_csv(
                os.path.join(ziel, "artikel_combined.csv"), sep=";", dtype=str
            )

        self.assertEqual(sorted(set(geschrieben["haus"])), ["Kino Nord", "Kino Süd"])

    def test_unlesbare_datei_endet_mit_klartext(self):
        with self.assertRaises(SystemExit) as fehler:
            self.lauf("Kino Nord=gibtsnicht.xlsx")
        self.assertIn("gibtsnicht.xlsx", str(fehler.exception))


class ZahlenTests(SimpleTestCase):
    """The scripts run without Django and carry the decimal rule a second time."""

    def test_wie_dezimal_normalisieren(self):
        for text in (
            "1.234,5",
            "1,234.5",
            "1,5",
            "1.5",
            "1.000",
            "1.000,00",
            " 12 ",
            "1 234,5",
            "1\u00a0234,5",
            "",
            "1.2.3",
        ):
            with self.subTest(text=text):
                self.assertEqual(normalize.zahl_normalisieren(text), dezimal_normalisieren(text))


class NormalisierenTests(SimpleTestCase):
    """Compeso's words become the house's terms."""

    def test_jede_schreibweise_trifft_dieselbe_kennung(self):
        for wort in ("Gramm", "gramm", "GRAMM", " g ", "Gram"):
            with self.subTest(wort=wort):
                self.assertEqual(normalize.einheitenkennung(wort), (normalize.GRAMM, False))

    def test_was_keine_einheit_ist_wird_gezaehlt(self):
        """The unit column often holds a packaging. That is counted."""
        for wort in ("Becher", "Eimer", "Glas", "", None):
            with self.subTest(wort=wort):
                kennung, geraten = normalize.einheitenkennung(wort)
                self.assertEqual(kennung, normalize.STUECK)
                self.assertTrue(geraten)

    def test_eine_erkannte_einheit_gilt_als_gelesen(self):
        self.assertEqual(normalize.einheitenkennung("Liter"), (normalize.LITER, False))

    def test_eine_kennung_bleibt_ihre_kennung(self):
        """An already normalised file can be read in again."""
        self.assertEqual(
            normalize.einheitenkennung(normalize.KILOGRAMM), (normalize.KILOGRAMM, False)
        )

    def test_ein_stueck_ist_kein_gebinde_sondern_das_einzelne(self):
        for bezeichnung in ("Kiste(n)", "Karton", "Stück(e)", "Sack(Säcke)"):
            with self.subTest(bezeichnung=bezeichnung):
                self.assertEqual(
                    normalize.gebindename(bezeichnung, "1", normalize.STUECK), normalize.EINZELN
                )

    def test_ein_liter_in_der_flasche_bleibt_ein_gebinde(self):
        self.assertEqual(normalize.gebindename("Flasche(n)", "1", normalize.LITER), "Flasche")

    def test_die_bezeichnung_wird_zum_hausbegriff(self):
        faelle = (("Kiste(n)", "Kasten"), ("POM", "BiB"), ("Karnister", "Kanister"))
        for bezeichnung, erwartet in faelle:
            with self.subTest(bezeichnung=bezeichnung):
                self.assertEqual(
                    normalize.gebindename(bezeichnung, "24", normalize.STUECK), erwartet
                )

    def test_unbekanntes_bleibt_stehen(self):
        """Better an unfamiliar name than a wrong one. It can be changed in the plan."""
        self.assertEqual(normalize.gebindename("Bündel", "12", normalize.STUECK), "Bündel")

    def test_eine_unlesbare_anzahl_macht_kein_einzelstueck(self):
        self.assertEqual(normalize.gebindename("Karton", "viele", normalize.STUECK), "Karton")
