"""The message after a status change speaks the account's language.

'SammelstatusView.MELDUNGEN' is filled when the class is built. With
'gettext' that happened in the server's language once and for all (until
10/2026): an English account read German after setting a product inactive.
"""

from django.test import TestCase
from django.urls import reverse

from django_project.testing import fuehrungskraft
from organisation.models import Haus

from .models import Basiseinheit, Hausartikel, Ware, Warengruppe


class MeldungInDerSprachedesKontos(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ware = Ware.objects.create(
            name="Artikel C", basiseinheit=Basiseinheit.objects.get(name="Stück(e)")
        )
        Hausartikel.objects.create(
            haus=Haus.objects.create(name="Kino Ost"),
            ware=cls.ware,
            warengruppe=Warengruppe.objects.create(name="Warengruppe G"),
            artikelnummer="101001",
            name="Artikel C",
        )

    def test_englisches_konto(self):
        self.client.force_login(fuehrungskraft(sprache="en"))
        antwort = self.client.post(
            reverse("ware_status", args=[self.ware.pk]), {"status": "INAKTIV"}, follow=True
        )
        self.assertContains(antwort, "is inactive")
        self.assertNotContains(antwort, "ist inaktiv")

    def test_deutsches_konto(self):
        self.client.force_login(fuehrungskraft(sprache="de"))
        antwort = self.client.post(
            reverse("ware_status", args=[self.ware.pk]), {"status": "INAKTIV"}, follow=True
        )
        self.assertContains(antwort, "ist inaktiv")
