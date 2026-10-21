"""Names someone typed reach the help texts of the forms as text, never as markup.

Help texts are HTML: crispy renders them with '|safe', as Django's own field
templates do. A name of an inventory type or a pack that stands in one would
otherwise put a button into the page's form, which sends the form's token
along to wherever its 'formaction' points.
"""

import re
from decimal import Decimal

from django.urls import reverse
from django.utils.html import escape

from django_project.testing import AngemeldeteSeiten
from organisation.models import Haus

from .models import Basiseinheit, Dimension, Gebinde, Inventurart, Ware

#: Short enough for every name field (100 characters).
MARKUP = '<button formaction="/x/" class="btn btn-primary">Speichern</button>'


def hilfetext(seite: str, feld: str) -> str:
    """The help text below a field, as it stands in the page."""
    treffer = re.search(rf'id="{feld}_helptext"[^>]*>(.*?)</', seite, re.DOTALL)
    assert treffer is not None, f"no help text at {feld}"
    return treffer.group(1)


class GespeicherteNamenImHilfetext(AngemeldeteSeiten):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.haus = Haus.objects.create(name="Kino Nord")
        cls.stueck = Basiseinheit.objects.get(dimension=Dimension.STUECK)

    def test_die_erste_inventurart_beim_anlegen_eines_hausartikels(self):
        """The create form names the type a new product goes into."""
        art = Inventurart.erste()
        art.name = MARKUP
        art.save()

        antwort = self.client.get(reverse("hausartikel_add", args=[self.haus.pk]))

        self.assertNotContains(antwort, MARKUP)
        text = hilfetext(antwort.content.decode(), "id_inventurart")
        self.assertIn(escape(MARKUP), text)

    def test_der_name_eines_gebindes_beim_zusammenfuehren(self):
        """Same name, another quantity: the preview says which pack carries the name already."""
        ziel = Ware.objects.create(name="Ware N", basiseinheit=self.stueck)
        quelle = Ware.objects.create(name="Ware N alt", basiseinheit=self.stueck)
        Gebinde.objects.create(
            ware=ziel, name=MARKUP, faktor_in_basiseinheit=Decimal("6"), sortierung=1
        )
        umbenennen = Gebinde.objects.create(
            ware=quelle, name=MARKUP, faktor_in_basiseinheit=Decimal("12"), sortierung=1
        )

        antwort = self.client.post(reverse("ware_merge", args=[ziel.pk]), {"quelle": quelle.pk})

        self.assertEqual(antwort.status_code, 200)
        self.assertNotContains(antwort, MARKUP)
        text = hilfetext(antwort.content.decode(), f"id_neu-{umbenennen.pk}")
        self.assertIn(escape(MARKUP), text)
