"""What the screen reader and the keyboard need: checked with axe-core on 2026-09-23.

The pages are Bootstrap forms and tables. Three things were consistently
missing: a name on the selection fields once Tom Select takes them over, an
announcement of the messages that arrive without a reload, and a skip past
the bar to the content. This test pins that down.
"""

from django.urls import reverse

from django_project.testing import AngemeldeteSeiten, skript


class Auswahlfelder(AngemeldeteSeiten):
    def test_tom_select_versteckt_das_original_vor_dem_vorleser(self):
        """Tom Select moves the label. The original would remain a second, nameless field."""
        self.assertIn('setAttribute("aria-hidden", "true")', skript("select"))


class Seitengeruest(AngemeldeteSeiten):
    def test_meldungen_werden_angesagt(self):
        antwort = self.client.get(reverse("inventur_list"))
        self.assertContains(antwort, 'data-meldungen role="status" aria-live="polite"')

    def test_ein_sprung_fuehrt_an_der_leiste_vorbei_zum_inhalt(self):
        antwort = self.client.get(reverse("inventur_list"))
        self.assertContains(antwort, 'href="#inhalt"')
        self.assertContains(antwort, '<main class="flex-grow-1" id="inhalt">')

    def test_der_hilfe_knopf_ist_lesbar(self):
        """As a grey outline it stayed below 4.5:1 on the bar's background."""
        antwort = self.client.get(reverse("inventur_list"))
        self.assertContains(antwort, "btn-secondary order-lg-last")
        self.assertNotContains(antwort, "btn-outline-secondary order-lg-last")
