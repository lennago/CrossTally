"""The sheets load nothing from outside ('pdf.ohne_nachladen')."""

import re
from datetime import date
from types import SimpleNamespace
from unittest import mock

from django.conf import settings
from django.test import RequestFactory, SimpleTestCase

from . import pdf


class NichtsNachladen(SimpleTestCase):
    """WeasyPrint's own fetcher would open every address in the document, local files included.

    The sheets need none: no image, no stylesheet, no font from outside.
    Measured on every sheet the tests render (2026-10-18): WeasyPrint asks
    the fetcher for nothing, so refusing everything takes nothing away.
    """

    def test_der_fetcher_oeffnet_keine_adresse(self):
        """Refused before anything is opened, whatever the protocol."""
        fetcher = pdf.ohne_nachladen()
        for adresse in (
            "https://example.org/bild.png",
            (settings.BASE_DIR / "manage.py").as_uri(),
            "data:text/plain,x",
        ):
            with self.subTest(adresse.split(":", 1)[0]), self.assertRaises(ValueError):
                fetcher.fetch(adresse)

    def test_jedes_blatt_bekommt_ihn(self):
        anfrage = RequestFactory().get("/")
        anfrage.user = mock.Mock(differenzfarben="alle")
        fetcher = object()

        with (
            mock.patch.object(pdf, "ohne_nachladen", return_value=fetcher),
            mock.patch("weasyprint.HTML") as html,
        ):
            html.return_value.write_pdf.return_value = b"%PDF-1.7"
            antwort = pdf.als_pdf(anfrage, "pdf/base.html", {}, "Blatt")

        self.assertIs(html.call_args.kwargs["url_fetcher"], fetcher)
        self.assertEqual(antwort.content, b"%PDF-1.7")


class Benannt(SimpleNamespace):
    """A stand-in for a record whose name the sheet prints ('{{ lauf }}')."""

    def __str__(self) -> str:
        return self.name


class DieFusszeileIstCssText(SimpleTestCase):
    """The footer is the "content" string of a page margin ('pdf/base.html').

    There an entity is printed as it stands. Until 10/2026 a house named
    "Kino Ost & Süd" read "Kino Ost &amp; Süd" at the foot of every page,
    and a quote read "&#x27;".
    """

    @staticmethod
    def fusszeile(vorlage: str, **kontext) -> str:
        blatt = pdf.pdf_html(vorlage, {"bloecke": [], "anzahl_waren": 0, **kontext})
        stil = blatt.split("</style>", 1)[0]
        treffer = re.search(r"@bottom-left \{\s*content: (\"[^\n]*\");", stil)
        if treffer is None:
            raise AssertionError(stil)
        return treffer.group(1)

    def test_der_name_des_hauses_steht_wie_er_heisst(self):
        for name, im_css in (
            ("Kino Ost & Süd", "Kino Ost & Süd"),
            ("Kino 'Nord'", r"Kino \27 Nord\27 "),
            ('Kino "West"', r"Kino \22 West\22 "),
            # Neither a line break nor a closing tag gets out of the string.
            ("Kino Süd\n} body {", r"Kino Süd\a } body {"),
            ("Kino </style> Nord", r"Kino \3c /style\3e  Nord"),
        ):
            inventur = SimpleNamespace(
                haus=SimpleNamespace(name=name),
                stichtag=date(2026, 1, 31),
                abgeschlossen_am=None,
                arten_anzeige="Monatsinventur",
            )
            with self.subTest(name):
                self.assertEqual(
                    self.fusszeile("pdf/inventur.html", inventur=inventur),
                    f'"{im_css} · 31.01.2026"',
                )

    def test_ebenso_der_name_des_laufs(self):
        """It carries the types, and their names are chosen by the managers."""
        lauf = Benannt(name="31.01.2026 Monats- & Jahresinventur", inventuren=[])

        self.assertEqual(
            self.fusszeile("pdf/run.html", lauf=lauf),
            '"31.01.2026 Monats- & Jahresinventur · 0 Häuser"',
        )
