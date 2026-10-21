#!/usr/bin/env python3
"""Mirrors the source language into its own catalog.

If the server is set to English ('LANGUAGE_CODE=en'), Django sets the English
catalog as the **fallback** for every other language
('DjangoTranslation._add_fallback'). A German sentence missing from the German
catalog would then come out in English for a German account. And there it is
missing, because German is the source language: the msgid *is* the German
text, no translation is needed for it.

That is why every entry stands in the German catalog once more, as its own
translation. This script writes that, and 'test_translations.py' pins it
down.

**Exempt is what Django translates itself.** A model that reuses Django's own
msgid ("email address" on the account) would otherwise get the English text
as German translation, and the catalog here wins against Django's
"E-Mail-Adresse". That is how "First name" stood on the German user page
until 09/2026.

Called from 'server/', after every 'makemessages'. Through 'uv run' like the
commands around it: a bare 'python' has no Django outside the venv.

    uv run python bin/mirror_source_catalog.py
    uv run manage.py compilemessages --ignore=.venv
"""

import gettext
import re
import sys
from pathlib import Path

import django

WURZEL = Path(__file__).resolve().parent.parent
QUELLE = WURZEL / "locale" / "en" / "LC_MESSAGES" / "django.po"
ZIEL = WURZEL / "locale" / "de" / "LC_MESSAGES" / "django.po"

#: Everything below belongs to the script. Above stands what Django leaves open.
MARKE = "# --- From here on the mirror of the source language ---"

KOPF = """# The project's own texts, each as its own translation.
#
# If the server is set to English, Django uses the English catalog as the
# fallback for every other language. Without these lines a German sentence
# missing here would come out in English for a German account.
#
# Written by bin/mirror_source_catalog.py. Nothing is added here by hand.
"""

#: This wide msgmerge wraps a long string.
BREITE = 72


def entpacken(zeilen: list[str]) -> str:
    return "".join(re.findall(r'"((?:[^"\\]|\\.)*)"', "\n".join(zeilen)))


def eintraege(pfad: Path) -> list[tuple[str, str]]:
    """(msgctxt, msgid) per block, in the order of the file."""
    return eintraege_aus(pfad.read_text(encoding="utf-8"))


def eintraege_aus(text: str) -> list[tuple[str, str]]:
    return [(kontext, schluessel) for kontext, schluessel, _plural in bloecke_aus(text)]


def bloecke_aus(text: str) -> list[tuple[str, str, str]]:
    """(msgctxt, msgid, msgid_plural) per block, in the order of the file.

    The plural is empty where the entry has none. Until 10/2026 the plural
    entries were skipped altogether, and a German account on an English server
    fell back to English for every text with a plural.
    """
    gefunden = []
    for block in text.split("\n\n"):
        kontext: list[str] = []
        mid: list[str] = []
        plural: list[str] = []
        wohin: list[str] | None = None
        for zeile in block.split("\n"):
            if zeile.startswith("msgctxt "):
                wohin = kontext
                wohin.append(zeile[8:])
            elif zeile.startswith("msgid_plural "):
                wohin = plural
                wohin.append(zeile[13:])
            elif zeile.startswith("msgid "):
                wohin = mid
                wohin.append(zeile[6:])
            elif zeile.startswith("msgstr"):
                wohin = None
            elif zeile.startswith('"') and wohin is not None:
                wohin.append(zeile)
        if schluessel := entpacken(mid):
            gefunden.append((entpacken(kontext), schluessel, entpacken(plural)))
    return gefunden


def djangos_katalog(sprache: str = "de") -> set[tuple[str, str]]:
    """(msgctxt, msgid) from all German catalogs Django ships.

    Those of the contribs too, because the account inherits its fields from
    'django.contrib.auth'.
    """
    gefunden: set[tuple[str, str]] = set()
    for mo in Path(django.__file__).parent.rglob(f"locale/{sprache}/LC_MESSAGES/django.mo"):
        with mo.open("rb") as datei:
            # The raw catalog is only reachable through the private attribute.
            katalog = gettext.GNUTranslations(datei)._catalog  # pyright: ignore[reportAttributeAccessIssue]
        for schluessel in katalog:
            msgid = schluessel[0] if isinstance(schluessel, tuple) else schluessel
            kontext, _, msgid = msgid.rpartition("\x04")
            if msgid:
                gefunden.add((kontext, msgid))
    return gefunden


def als_zeilen(text: str, marke: str) -> list[str]:
    """'msgid'/'msgstr', for long texts over several lines."""
    if len(text) <= BREITE:
        return [f'{marke} "{text}"']
    teile: list[str] = []
    aktuell = ""
    for wort in text.split(" "):
        kandidat = f"{aktuell} {wort}" if aktuell else wort
        if len(kandidat) > BREITE and aktuell:
            teile.append(aktuell + " ")
            aktuell = wort
        else:
            aktuell = kandidat
    teile.append(aktuell)
    return [f'{marke} ""', *(f'"{teil}"' for teil in teile)]


def block(kontext: str, schluessel: str, plural: str = "") -> str:
    """One entry, each form as its own translation. Both catalogs count two forms."""
    zeilen = als_zeilen(kontext, "msgctxt") if kontext else []
    zeilen += als_zeilen(schluessel, "msgid")
    if plural:
        zeilen += als_zeilen(plural, "msgid_plural")
        zeilen += als_zeilen(schluessel, "msgstr[0]")
        zeilen += als_zeilen(plural, "msgstr[1]")
    else:
        zeilen += als_zeilen(schluessel, "msgstr")
    return "\n".join(zeilen)


def main() -> int:
    bisher = ZIEL.read_text(encoding="utf-8").split(f"\n{MARKE}\n")[0].rstrip("\n")
    # What Django leaves open already stands above. A second block with the same
    # msgid would be an error for msgfmt.
    schon_da = set(eintraege_aus(bisher)) | djangos_katalog()
    neu = [
        (kontext, schluessel, plural)
        for kontext, schluessel, plural in bloecke_aus(QUELLE.read_text(encoding="utf-8"))
        if (kontext, schluessel) not in schon_da
    ]
    bloecke = "\n\n".join(block(*eintrag) for eintrag in neu)
    ZIEL.write_text(f"{bisher}\n\n{MARKE}\n{KOPF}\n{bloecke}\n", encoding="utf-8")
    print(f"{len(neu)} Einträge gespiegelt nach {ZIEL.relative_to(WURZEL)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
