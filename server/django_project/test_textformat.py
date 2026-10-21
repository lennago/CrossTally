"""The five rules of the text format, also where a block has more than one line."""

from django.test import SimpleTestCase

from .textformat import als_html


class Bloecke(SimpleTestCase):
    def test_ueberschrift_ueber_ihrer_ersten_zeile(self):
        html = str(als_html("## Angaben\nBeispielkino GmbH\nMusterstraße 1"))
        self.assertIn('<h2 class="h5 mt-4">Angaben</h2>', html)
        self.assertIn("<p>Beispielkino GmbH<br>Musterstraße 1</p>", html)

    def test_umgebrochener_listenpunkt(self):
        html = str(als_html("- erster Punkt\n- zweiter Punkt,\nder weitergeht"))
        self.assertIn(
            "<ul><li>erster Punkt</li><li>zweiter Punkt,<br>der weitergeht</li></ul>", html
        )

    def test_html_bleibt_text(self):
        html = str(als_html("## <b>x</b>\n- <i>y</i>\n\n<script>"))
        self.assertNotIn("<b>", html)
        self.assertNotIn("<i>", html)
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_fett(self):
        self.assertIn("<strong>so</strong>", str(als_html("schreibt **so** fett")))
