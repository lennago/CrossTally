"""The location rows of a new house article: labelled by the location they send.

Only creating shows them, one row per location of the house, with the
location in a hidden field. A page that comes back after a rejected main
form keeps the rows as sent.
"""

from decimal import Decimal

from django.urls import reverse

from django_project.testing import AngemeldeteSeiten, bereich
from organisation.models import Haus, Ort

from .models import Basiseinheit, Dimension, Erwartung, Gebinde, Ware, Warengruppe

#: What a ticked location says when the article would get no row there.
OHNE_GEBINDE = "Ohne Gebinde bekäme der Artikel hier keine Zeile."


class Orte(AngemeldeteSeiten):
    """Kino Nord with two locations. 'Ware W' goes on the scale, 'Ware N' comes in boxes."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.haus = Haus.objects.create(name="Kino Nord")
        cls.lager = bereich(cls.haus, "Lager")
        cls.lager_a = Ort.objects.create(bereich=cls.lager, name="Lager A")
        cls.lager_c = Ort.objects.create(bereich=cls.lager, name="Lager C")
        cls.gruppe = Warengruppe.objects.create(name="Warengruppe G")
        stueck = Basiseinheit.objects.get(dimension=Dimension.STUECK)
        cls.ware = Ware.objects.create(name="Ware N", basiseinheit=stueck)
        Gebinde.objects.create(ware=cls.ware, name="Karton", faktor_in_basiseinheit=Decimal("12"))
        cls.waage = Ware.objects.create(
            name="Ware W", basiseinheit=Basiseinheit.objects.get(name="kg"), abwiegbar=True
        )

    def senden(self, *zeilen: tuple[Ort, list[str]], **abweichend):
        daten = {
            "artikelnummer": "101001",
            "name": "Artikel N",
            "ware": self.ware.pk,
            "neuer_ware": "",
            "basiseinheit": "",
            "inventurart": "",
            "warengruppe": self.gruppe.pk,
            "neue_warengruppe": "",
            "ean": "",
            "gebinde-TOTAL_FORMS": "0",
            "gebinde-INITIAL_FORMS": "0",
            "gebinde-MIN_NUM_FORMS": "0",
            "gebinde-MAX_NUM_FORMS": "1000",
            "orte-TOTAL_FORMS": str(len(zeilen)),
            "orte-INITIAL_FORMS": "0",
            "orte-MIN_NUM_FORMS": "0",
            "orte-MAX_NUM_FORMS": "1000",
        }
        for stelle, (ort, gebinde) in enumerate(zeilen):
            daten[f"orte-{stelle}-ort"] = ort.pk
            daten[f"orte-{stelle}-erwartet"] = "on"
            daten[f"orte-{stelle}-gebinde"] = gebinde
        return self.client.post(reverse("hausartikel_add", args=[self.haus.pk]), daten | abweichend)

    def test_eine_zeile_steht_unter_dem_ort_den_sie_sendet(self):
        """Lager B came in between showing and sending. The rows keep their own names.

        Until 10/2026 the second row came back under "Lager B", by its
        position, while it still sent Lager C.
        """
        Ort.objects.create(bereich=self.lager, name="Lager B")

        antwort = self.senden(
            (self.lager_a, ["Karton"]), (self.lager_c, ["Karton"]), warengruppe=""
        )

        self.assertEqual(antwort.status_code, 200)
        self.assertEqual(
            [form.ort_objekt for form in antwort.context["orte_formset"].forms],
            [self.lager_a, self.lager_c],
        )

    def test_ein_ort_doppelt_wird_abgewiesen(self):
        """Only a changed page sends that. Until 10/2026 it ended at the unique index."""
        antwort = self.senden((self.lager_a, ["Karton"]), (self.lager_a, ["Karton"]))

        self.assertContains(antwort, "Ein Ort steht doppelt in der Liste.")
        self.assertFalse(Erwartung.objects.exists())

    def test_eine_abgewiesene_ware_bleibt_fuer_die_orte_gewaehlt(self):
        """Ware W chosen and a new one described: the form says that, the location does not.

        Ware W goes on the scale where no pack is ticked. Until 10/2026 the
        rejected choice counted as a new product, and the location reported
        a missing pack.
        """
        antwort = self.senden((self.lager_a, []), ware=self.waage.pk, neuer_ware="Ware X")

        self.assertContains(antwort, "Entweder eine vorhandene Ware wählen")
        self.assertNotContains(antwort, OHNE_GEBINDE)

    def test_die_gebinde_einer_abgewiesenen_ware_bleiben_waehlbar(self):
        antwort = self.senden((self.lager_a, ["Karton"]), neuer_ware="Ware X")

        self.assertContains(antwort, "Entweder eine vorhandene Ware wählen")
        zeile = antwort.context["orte_formset"].forms[0]
        self.assertEqual(zeile.errors, {})
