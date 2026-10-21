"""The memory of a list: filter, scroll position and the way back.

Checked is the interplay of page and script: what 'view_state.js' looks for
in the document, the template must also deliver. The behaviour in the
browser itself (sessionStorage, scrolling) is checked by no Django test.
"""

from django.test import SimpleTestCase
from django.urls import reverse

from django_project.testing import AngemeldeteSeiten, skript
from katalog.models import Warengruppe


class SkriptWirdEingebunden(AngemeldeteSeiten):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.gruppe = Warengruppe.objects.create(name="Warengruppe G")

    def test_auf_jeder_seite(self):
        """It is in 'base.html', because every page should remember its state."""
        for name in ("warengruppe_list", "haus_list"):
            with self.subTest(name):
                self.assertIn("js/view_state.js", self.client.get(reverse(name)).content.decode())

    def test_nach_dem_aufklappen(self):
        """The scroll position is only right once 'expand.js' has opened the groups."""
        html = self.client.get(reverse("warengruppe_list")).content.decode()
        self.assertLess(html.index("js/expand.js"), html.index("js/view_state.js"))

    def test_liste_bietet_das_filterformular_an(self):
        """Only where there is one does the script remember the query."""
        self.assertIn("data-filter", self.client.get(reverse("warengruppe_list")).content.decode())

    def test_brotkrumen_kommen_ohne_abfrage(self):
        """The script appends the remembered query. An own '?' would hold it back."""
        html = self.client.get(self.gruppe.get_absolute_url()).content.decode()
        self.assertIn('<ol class="breadcrumb', html)
        self.assertIn(f'<a href="{reverse("warengruppe_list")}">Warengruppen</a>', html)


class SkriptSuchtWasDieSeiteBietet(SimpleTestCase):
    def setUp(self):
        self.quelle = skript("view_state")

    def test_greift_die_brotkrumen(self):
        self.assertIn(".breadcrumb a[href]", self.quelle)

    def test_merkt_die_abfrage_nur_mit_filterformular(self):
        self.assertIn("form[data-filter]", self.quelle)

    def test_merkt_je_pfad_im_tab(self):
        self.assertIn("sessionStorage", self.quelle)
        self.assertIn("window.location.pathname", self.quelle)

    def test_merkt_und_setzt_den_scrollstand(self):
        self.assertIn("scrollY", self.quelle)
        self.assertIn("window.scrollTo", self.quelle)

    def test_sichert_den_stand_auch_beim_weglegen(self):
        """On mobile devices a 'pagehide' often no longer arrives."""
        self.assertIn("pagehide", self.quelle)
        self.assertIn("visibilitychange", self.quelle)
