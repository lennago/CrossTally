"""Deletion goes through the confirmation, not through the HTTP verb DELETE.

Django's 'DeleteView' answers DELETE bypassing 'form_valid', and that is
where the rules live. Checked through the URL resolver instead of a list of
names: a delete view added tomorrow is included by itself.
"""

from datetime import date

from django.test import SimpleTestCase
from django.urls import URLPattern, URLResolver, get_resolver, reverse
from django.views.generic import DeleteView

from django_project.testing import inventur_anlegen
from inventur.models import Inventur, Inventurstatus
from katalog.models import Behaelter
from organisation.models import Haus

from .testing import AngemeldeteSeiten, fuehrungskraft


def loeschansichten(resolver: URLResolver | None = None):
    """Every 'DeleteView' of the project along with its URL name."""
    for muster in (resolver or get_resolver()).url_patterns:
        if isinstance(muster, URLResolver):
            yield from loeschansichten(muster)
        elif isinstance(muster, URLPattern):
            klasse = getattr(muster.callback, "view_class", None)
            if klasse is not None and issubclass(klasse, DeleteView):
                yield muster.name, klasse


class KeinDeleteVerb(SimpleTestCase):
    def test_keine_loeschansicht_beantwortet_delete(self):
        ansichten = dict(loeschansichten())
        self.assertGreaterEqual(len(ansichten), 11, "Der Resolver findet die Löschansichten nicht.")
        for name, klasse in ansichten.items():
            with self.subTest(ansicht=name):
                self.assertNotIn(
                    "delete",
                    klasse.http_method_names,
                    f"{klasse.__name__} erbt nicht von 'NurMitRueckfrage'.",
                )


class DeleteAnDenRegelnVorbei(AngemeldeteSeiten):
    """The three cases that got through on 2026-09-18."""

    def test_fuehrungskraft_loescht_keine_abgeschlossene_inventur(self):
        inventur = inventur_anlegen(
            Haus.objects.create(name="Kino Ost"), stichtag=date(2026, 1, 31)
        )
        inventur.zaehlung_starten()
        inventur.abschliessen()
        self.client.force_login(fuehrungskraft(email="leitung@example.org"))

        antwort = self.client.delete(reverse("inventur_delete", args=[inventur.pk]))

        self.assertEqual(antwort.status_code, 405)
        self.assertEqual(Inventur.objects.get().status, Inventurstatus.ABGESCHLOSSEN)

    def test_ohne_behaelter_bleibt(self):
        ohne = Behaelter.ohne_behaelter()
        antwort = self.client.delete(reverse("behaelter_delete", args=[ohne.pk]))
        self.assertEqual(antwort.status_code, 405)
        self.assertTrue(Behaelter.objects.filter(pk=ohne.pk).exists())

    def test_geschuetztes_haus_wird_nicht_zum_serverfehler(self):
        haus = Haus.objects.create(name="Kino Süd")
        antwort = self.client.delete(reverse("haus_delete", args=[haus.pk]))
        self.assertEqual(antwort.status_code, 405)
        self.assertTrue(Haus.objects.filter(pk=haus.pk).exists())
