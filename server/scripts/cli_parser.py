import argparse

from .cli_handlers import handle_artikel


def haus_und_datei(wert: str) -> tuple[str, str]:
    """Splits "Haus=Datei" into its two halves."""
    haus, trenner, pfad = wert.partition("=")
    haus, pfad = haus.strip(), pfad.strip()
    if not trenner or not haus or not pfad:
        raise argparse.ArgumentTypeError(
            f"„{wert}“: erwartet wird HAUS=DATEI, zum Beispiel 'Kino Nord=nord.xlsx'."
        )
    return haus, pfad


def get_parser():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    save_parser = argparse.ArgumentParser(add_help=False)
    save_parser.add_argument("-s", "--save", metavar="DEST", help="Speichert das Ergebnis an DEST")

    artikel_parser = subparsers.add_parser(
        "artikel",
        parents=[save_parser],
        help="transformiert Artikelbestandslisten von Compeso",
    )
    artikel_parser.add_argument(
        "quellen",
        nargs="+",
        type=haus_und_datei,
        metavar="HAUS=DATEI",
        help="Artikelbestandsliste je Haus, z. B. 'Kino Nord=nord.xlsx' 'Kino Ost=ost.csv'",
    )
    artikel_parser.add_argument(
        "-a",
        "--all",
        action="store_true",
        help="erstellt auch Dateien für die Warengruppen, Gebinde und Einheiten",
    )
    artikel_parser.set_defaults(func=handle_artikel)

    return parser
