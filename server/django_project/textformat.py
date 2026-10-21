"""A text from the administration as a page, without HTML coming in from outside.

The privacy text and the legal notice are in the database and are edited
there. A template would be too much power (a script in the text would run on
every page), plain paragraphs too little (the bundled page has headings and
lists). In between lies a format with five rules one can remember:

- an empty line separates paragraphs,
- a single line break stays one,
- "## " at the start of a line makes a subheading,
- "- " at the start of a line makes a list item,
- "**like this**" writes bold.

The single line break came with the legal notice in 10/2026: an address has
one line per item, and until then its lines ran into one. The shipped privacy
text has none, every paragraph stands on one line.

Everything else is text. Escaping happens before the conversion, so a "<" in
the text stays a "<", and the Content-Security-Policy stays as it is.
"""

import re

from django.utils.html import escape, format_html, format_html_join
from django.utils.safestring import SafeString, mark_safe

_FETT = re.compile(r"\*\*(.+?)\*\*")


def _zeile(text: str) -> SafeString:
    """One line: escaped, then the bold inserted."""
    return mark_safe(_FETT.sub(r"<strong>\1</strong>", escape(text)))


def _absatz(zeilen: list[str]) -> SafeString:
    return format_html("<p>{}</p>", _zeilen(zeilen))


def _zeilen(zeilen: list[str]) -> SafeString:
    """Several lines, each escaped, the single line break kept."""
    return mark_safe("<br>".join(str(_zeile(zeile)) for zeile in zeilen))


def _block(zeilen: list[str]) -> SafeString:
    if zeilen[0].startswith("## "):
        # The heading may stand right above its first line: since a single
        # line break stays (10/2026), that is how an address is written.
        kopf = format_html('<h2 class="h5 mt-4">{}</h2>', _zeile(zeilen[0][3:]))
        rest = zeilen[1:]
        return kopf if not rest else mark_safe(f"{kopf}\n{_block(rest)}")
    if zeilen[0].startswith("- "):
        # A line without the dash continues the item before it.
        punkte: list[list[str]] = []
        for zeile in zeilen:
            if zeile.startswith("- ") or not punkte:
                punkte.append([zeile[2:]])
            else:
                punkte[-1].append(zeile)
        return format_html(
            "<ul>{}</ul>",
            format_html_join("", "<li>{}</li>", ((_zeilen(punkt),) for punkt in punkte)),
        )
    return _absatz(zeilen)


def als_html(text: str) -> SafeString:
    """The whole text as a sequence of blocks."""
    bloecke = [
        [zeile.rstrip() for zeile in block.strip().splitlines()]
        for block in re.split(r"\n\s*\n", text.strip())
        if block.strip()
    ]
    return mark_safe("\n".join(str(_block(zeilen)) for zeilen in bloecke))
