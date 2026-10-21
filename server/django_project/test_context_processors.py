"""What every page knows by itself."""

from django.test import override_settings
from django.urls import reverse

from demo.state import kennzeichnen
from django_project.testing import AngemeldeteSeiten
from organisation.models import Haus


class VersionImKontomenue(AngemeldeteSeiten):
    @override_settings(APP_VERSION="0.3.0")
    def test_steht_auf_jeder_seite(self):
        """On a question from the cinema the first thing asked: which version is running?"""
        for name in ("haus_list", "warengruppe_list"):
            with self.subTest(name):
                self.assertContains(self.client.get(reverse(name)), "Version 0.3.0")


class DemoBand(AngemeldeteSeiten):
    """A fictional stock says so on every page."""

    def test_ohne_kennzeichen_steht_da_nichts(self):
        Haus.objects.create(name="Kino Nord")
        antwort = self.client.get(reverse("haus_list"))
        self.assertNotContains(antwort, "Dieser Bestand ist erfunden")

    def test_mit_kennzeichen_traegt_die_seite_das_band(self):
        kennzeichnen()
        antwort = self.client.get(reverse("haus_list"))
        self.assertContains(antwort, "Dieser Bestand ist erfunden")
        # The badge in the bar stays put when scrolling, the banner does not.
        self.assertContains(antwort, "demo-marke")

    def test_es_haengt_nicht_an_den_haeusern(self):
        """In the demo one may delete a house without losing the notice."""
        kennzeichnen()
        Haus.objects.all().delete()
        self.assertContains(self.client.get(reverse("haus_list")), "Dieser Bestand ist erfunden")
