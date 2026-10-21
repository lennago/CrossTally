"""A number of another house answers like a number that does not exist, on every counting endpoint.

Otherwise the difference tells a counter what exists elsewhere: which
inventories, rows, weighings, locations and articles other houses carry.
Until 10/2026 a new row named a location or an article of another house with
a sentence of its own, and beside a second wrong field with none at all
('serializers.counting.ZaehlpositionAnlegenSerializer._im_haus'). A closed
inventory says that it is closed, to everyone: a device that still holds its
number learns that waiting makes no sense ('checks.pruefe_zugeordnet').
"""

from decimal import Decimal

from django_project.testing import inventur_anlegen
from inventur.models import Wiegung, Zaehlposition
from katalog.models import Hausartikel
from organisation.models import Bereich, Ort

from .tests import STICHTAG, ApiGrundlagen, adresse

#: Far beyond every number the tests create.
WEG = 99999999


class FremdWieUnbekannt(ApiGrundlagen):
    """The counter of Kino Ost and the running inventory of Kino Süd, which he does not count in."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.regal = Ort.objects.create(
            name="Regal R", bereich=Bereich.objects.create(name="Lager", haus=cls.fremdes_haus)
        )
        cls.lose = Hausartikel.objects.create(
            haus=cls.fremdes_haus,
            ware=cls.nachos_ware,
            warengruppe=cls.gruppe,
            artikelnummer="101902",
            name="Artikel N lose",
        )
        cls.fremde = inventur_anlegen(cls.fremdes_haus, stichtag=STICHTAG)
        cls.fremde.zaehlung_starten()
        cls.zeile = Zaehlposition.objects.create(
            inventur=cls.fremde, ort=cls.regal, artikel=cls.lose, unerwartet=True
        )
        cls.wiegung = Wiegung.objects.create(
            zaehlposition=cls.zeile, brutto_gramm=Decimal("900"), behaelter=cls.schale
        )

    def setUp(self):
        self.anmelden()

    def anfragen(self, inventur, zeile, wiegung, ort, artikel) -> dict:
        """Every counting endpoint with these numbers, by name: (status, body)."""
        client = self.client
        aufrufe = {
            "Inventur": lambda: client.get(adresse("inventur-detail", inventur)),
            "Stammdaten": lambda: client.get(adresse("inventur-stammdaten", inventur)),
            "Prüfliste": lambda: client.get(adresse("inventur-pruefliste", inventur)),
            "Notiz": lambda: client.patch(
                adresse("pruefliste_zeile", inventur, self.lose.pk), {"notiz": "x"}, format="json"
            ),
            "Differenz von Hand": lambda: client.post(
                adresse("differenz_von_hand_api", inventur), {"an": True}, format="json"
            ),
            "Sperre": lambda: client.post(
                adresse("sperre_api", inventur), {"an": True}, format="json"
            ),
            "Zeilen der Inventur": lambda: client.get(
                adresse("zaehlposition-list"), {"inventur": inventur}
            ),
            "Neue Zeile": lambda: client.post(
                adresse("zaehlposition-list"),
                {"inventur": inventur, "ort": self.theke.pk, "artikel": self.cola.pk},
            ),
            "Zeile": lambda: client.get(adresse("zaehlposition-detail", zeile)),
            "Menge": lambda: client.patch(
                adresse("zaehlposition-detail", zeile), {"eingabemenge": "1"}
            ),
            "Zeile entfernen": lambda: client.delete(adresse("zaehlposition-detail", zeile)),
            "Wiegungen der Zeile": lambda: client.get(
                adresse("wiegung-list"), {"zaehlposition": zeile}
            ),
            "Neue Wiegung": lambda: client.post(
                adresse("wiegung-list"),
                {"zaehlposition": zeile, "brutto_gramm": "700", "behaelter": self.schale.pk},
            ),
            "Wiegung": lambda: client.get(adresse("wiegung-detail", wiegung)),
            "Berichtigung": lambda: client.patch(
                adresse("wiegung-detail", wiegung), {"brutto_gramm": "800"}
            ),
            "Wiegung entfernen": lambda: client.delete(adresse("wiegung-detail", wiegung)),
            "Ort der neuen Zeile": lambda: client.post(
                adresse("zaehlposition-list"),
                {"inventur": self.inventur.pk, "ort": ort, "artikel": self.cola.pk},
            ),
            "Artikel der neuen Zeile": lambda: client.post(
                adresse("zaehlposition-list"),
                {"inventur": self.inventur.pk, "ort": self.theke.pk, "artikel": artikel},
            ),
        }
        antworten = {}
        for name, aufruf in aufrufe.items():
            rsp = aufruf()
            antworten[name] = (rsp.status_code, rsp.json() if rsp.content else None)
        return antworten

    def test_jede_schnittstelle_antwortet_gleich(self):
        weg = self.anfragen(WEG, WEG, WEG, WEG, WEG)
        fremd = self.anfragen(
            self.fremde.pk, self.zeile.pk, self.wiegung.pk, self.regal.pk, self.lose.pk
        )

        for name, antwort in weg.items():
            with self.subTest(name):
                self.assertIn(antwort[0], (200, 400, 404), antwort)
                self.assertEqual(fremd[name], antwort)
        self.assertEqual(Wiegung.objects.get().brutto_gramm, Decimal("900"))
        self.assertTrue(Zaehlposition.objects.get(pk=self.zeile.pk).aktiv)
