"""The snapshot behind the review list carries the same values as the table.

'refresh.js' copies the snapshot's spans into the table after a filter
replaced the list. Until 10/2026 the snapshot was the whole table once more.
Now it is only the values, and this test keeps the two from drifting apart.
"""

import re
from datetime import date
from decimal import Decimal
from html.parser import HTMLParser

from django.urls import reverse

from django_project.testing import AngemeldeteSeiten, inventur_anlegen
from inventur.models import Sollbestand
from katalog.models import (
    Basiseinheit,
    Erwartung,
    ErwartungGebinde,
    Gebinde,
    Hausartikel,
    Ware,
    Warengruppe,
)
from organisation.models import Bereich, Haus, Ort


class _Werte(HTMLParser):
    """The inner HTML of every element with 'data-wert', per key, in document order."""

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.gefunden: list[tuple[str, str]] = []
        self._offen: list[tuple[str, list[str], int]] = []
        self._tiefe = 0

    def handle_starttag(self, tag, attrs):
        self._tiefe += 1
        attrs = dict(attrs)
        text = self.get_starttag_text() or ""
        for _schluessel, teile, _ebene in self._offen:
            teile.append(text)
        if "data-wert" in attrs:
            self._offen.append((str(attrs["data-wert"] or ""), [], self._tiefe))

    def handle_endtag(self, tag):
        if self._offen and self._offen[-1][2] == self._tiefe:
            schluessel, teile, _ebene = self._offen.pop()
            self.gefunden.append((schluessel, "".join(teile)))
        for _schluessel, teile, _ebene in self._offen:
            teile.append(f"</{tag}>")
        self._tiefe -= 1

    def handle_data(self, data):
        for _schluessel, teile, _ebene in self._offen:
            teile.append(data)

    def handle_entityref(self, name):
        self.handle_data(f"&{name};")

    def handle_charref(self, name):
        self.handle_data(f"&#{name};")


def werte(html: str) -> dict[str, str]:
    """Key to inner HTML with whitespace collapsed: indentation differs, content must not."""
    leser = _Werte()
    leser.feed(html)
    return {
        schluessel: re.sub(r"\s+", " ", inhalt).strip() for schluessel, inhalt in leser.gefunden
    }


class SchnappschussGleichtTabelle(AngemeldeteSeiten):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        stueck = Basiseinheit.objects.get(name="Stück(e)")
        haus = Haus.objects.create(name="Kino Nord")
        ort = Ort.objects.create(
            name="Theke", bereich=Bereich.objects.create(name="Theke", haus=haus)
        )
        gruppe = Warengruppe.objects.create(name="Warengruppe G")
        cls.artikel = []
        for nummer, name in enumerate(("Artikel C", "Artikel F", "Artikel S"), start=1):
            ware = Ware.objects.create(name=name, basiseinheit=stueck)
            kasten = Gebinde.objects.create(ware=ware, name="Kasten", faktor_in_basiseinheit=20)
            artikel = Hausartikel.objects.create(
                haus=haus, ware=ware, warengruppe=gruppe, artikelnummer=f"10100{nummer}", name=name
            )
            erwartung = Erwartung.objects.create(ort=ort, artikel=artikel)
            ErwartungGebinde.objects.create(erwartung=erwartung, gebinde=kasten)
            cls.artikel.append(artikel)
        cls.inventur = inventur_anlegen(haus, stichtag=date(2026, 1, 31))
        cls.inventur.zaehlpositionen_anlegen()
        cls.inventur.zaehlung_starten()
        # One counted with a target, one counted without, one not counted at all.
        erste, zweite, _dritte = cls.artikel
        cls.inventur.zaehlpositionen.filter(artikel=erste).update(eingabemenge=Decimal("2"))
        cls.inventur.zaehlpositionen.filter(artikel=zweite).update(eingabemenge=Decimal("1"))
        Sollbestand.objects.create(
            inventur=cls.inventur, hausartikel=erste, menge_basiseinheit=Decimal("30")
        )

    def seite(self) -> str:
        antwort = self.client.get(reverse("inventur_pruefen", args=[self.inventur.pk]))
        self.assertEqual(antwort.status_code, 200)
        return antwort.content.decode()

    def test_der_schnappschuss_gleicht_der_tabelle(self):
        html = self.seite()
        tabelle, schnappschuss = html.split("<template data-schnappschuss>")
        schnappschuss = schnappschuss.split("</template>")[0]
        in_tabelle = werte(tabelle)
        im_schnappschuss = werte(schnappschuss)
        # The rows' values. The page's own state and the sums per family stay
        # out: the first changes on every fetch, the second hangs on the filter.
        je_zeile = re.compile(r"^(stand|ist|soll|differenz)-\d+$")
        self.assertEqual(
            set(im_schnappschuss),
            {schluessel for schluessel in in_tabelle if je_zeile.match(schluessel)},
        )
        self.assertTrue(im_schnappschuss)
        for schluessel, inhalt in im_schnappschuss.items():
            with self.subTest(wert=schluessel):
                self.assertEqual(inhalt, in_tabelle[schluessel])
        # The row's class comes along too: the script copies it as well.
        auffaellig = self.artikel[0].pk
        self.assertIn(f'data-zeile="{auffaellig}" class="tabellenzeile-auffaellig"', schnappschuss)

    def test_der_schnappschuss_ist_kein_zweites_formular(self):
        """The whole table once more weighed 5 KB per row. Fields have no business in it."""
        html = self.seite()
        schnappschuss = html.split("<template data-schnappschuss>")[1].split("</template>")[0]
        self.assertNotIn("<textarea", schnappschuss)
        self.assertNotIn("<input", schnappschuss)
        self.assertNotIn("<table", schnappschuss)
