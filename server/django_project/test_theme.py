"""Light and dark: what must not be in the templates because it does not follow along.

Bootstrap 5.3 attaches most colours to the theme. But not the colour variants
of the table and the light surfaces. In dark mode they stay light, and the
muted text on them is light on light: that is how the check list was
unreadable in every row with a deviation on 2026-09-18. No page test sees
that, so the search goes over all templates, as with the Content-Security-Policy.
"""

import re

from django.conf import settings
from django.test import SimpleTestCase

from .test_csp import eigene_vorlagen

NICHT_THEMENFAEHIG = re.compile(
    r"\b(table-(?:light|dark|warning|danger|success|info|primary|secondary)|text-bg-light)\b"
)


class Vorlagen(SimpleTestCase):
    def test_keine_farbe_die_im_dunkeln_hell_bleibt(self):
        for vorlage in eigene_vorlagen():
            # The PDF knows no theme: WeasyPrint reads that, not a browser.
            if "pdf" in vorlage.parts:
                continue
            with self.subTest(vorlage=str(vorlage.relative_to(settings.BASE_DIR))):
                gefunden = NICHT_THEMENFAEHIG.findall(vorlage.read_text(encoding="utf-8"))
                self.assertEqual(
                    gefunden, [], "Dafür gibt es 'tabellenzeile-ruhig' und '-auffaellig'."
                )

    def test_die_eigenen_klassen_haengen_am_thema(self):
        stylesheet = (settings.BASE_DIR / "static" / "site.css").read_text(encoding="utf-8")
        for klasse in ("tabellenzeile-ruhig", "tabellenzeile-auffaellig"):
            with self.subTest(klasse=klasse):
                regel = stylesheet.split(f".{klasse} {{")[1].split("}")[0]
                self.assertIn("--bs-table-bg: var(--bs-", regel)
                self.assertIn("--bs-table-color: var(--bs-body-color)", regel)
