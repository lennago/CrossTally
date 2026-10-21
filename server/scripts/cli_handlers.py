from argparse import Namespace

from .loader import LoaderError, SourceType, get_artikel_und_spalten


def quellen_je_haus(paare: list[tuple[str, str]]) -> dict[str, SourceType]:
    """The pairs from the command line as {house name: file}.

    A house twice would be silent: the dict would keep only the last file.
    Since exactly one export exists per house, that is a typo and not a wish.
    """
    quellen: dict[str, SourceType] = {}
    for haus, pfad in paare:
        if haus in quellen:
            raise SystemExit(f"Fehler: {haus} ist zweimal angegeben. Je Haus gibt es eine Datei.")
        quellen[haus] = pfad
    return quellen


def handle_artikel(args: Namespace):
    cols = ["warengruppe", "gebinde", "einheit"] if args.all else ...
    dest = args.save if args.save else ...
    try:
        tables = get_artikel_und_spalten(
            quellen_je_haus(args.quellen), additional_cols=cols, dest=dest
        )
    except LoaderError as fehler:
        raise SystemExit(f"Fehler: {fehler}") from fehler

    if dest is ...:
        for table in tables:
            print(table)
    else:
        print(f"{len(tables)} Tabelle(n) nach {dest} geschrieben.")
