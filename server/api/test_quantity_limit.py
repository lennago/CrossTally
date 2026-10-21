"""The app hears while counting what no result holds, for portions and weighings too.

The count in packs was refused at input already ('tests.WasNichtGeht').
Portions and weighings went in and stopped the closing (until 10/2026). Now
every path that writes them answers 400 with the sentence on the field the
person typed, and the app lists the job as not accepted with that reason.
"""

from decimal import Decimal
from http import HTTPStatus

from inventur.models import Wiegung, Zaehlposition

from .tests import ApiGrundlagen, adresse

#: Ten million portions of 140 g each ('ApiGrundlagen.portion').
PORTIONEN_ZU_VIEL = (
    "Für „Artikel N“ wären das 1400000000 g, mehr als ein Ergebnis fasst. Bitte die Zahl prüfen."
)
SCHALEN_ZU_VIEL = "Das wären 1000000000 g, mehr als ein Ergebnis fasst. Bitte die Zahl prüfen."
KENNUNG = "5d9c2b7e-4f1a-4c3b-8e2d-6a7b8c9d0e1f"


class MehrAlsEinErgebnisFasst(ApiGrundlagen):
    def setUp(self):
        super().setUp()
        self.anmelden()

    def waage(self) -> int:
        """A row for the nachos that go on the scale at the counter."""
        rsp = self.client.post(
            adresse("zaehlposition-list"),
            {"inventur": self.inventur.pk, "ort": self.theke.pk, "artikel": self.nachos.pk},
        )
        self.assertEqual(rsp.status_code, HTTPStatus.CREATED, rsp.data)
        return rsp.data["id"]

    def wiegen(self, position: int, brutto: str, **mehr):
        return self.client.post(
            adresse("wiegung-list"),
            {
                "zaehlposition": position,
                "brutto_gramm": brutto,
                "behaelter": self.schale.pk,
                **mehr,
            },
        )

    def pruefe_abgewiesen(self, rsp, feld: str, meldung: str):
        self.assertEqual(rsp.status_code, HTTPStatus.BAD_REQUEST, rsp.data)
        self.assertEqual(rsp.data, {feld: [meldung]})

    def gewogen(self, position: int) -> list[Decimal]:
        return sorted(
            Wiegung.objects.filter(zaehlposition_id=position).values_list("brutto_gramm", flat=True)
        )

    def test_portionen_beim_anlegen_und_beim_zaehlen(self):
        neu = {"inventur": self.inventur.pk, "ort": self.lager.pk, "artikel": self.portion.pk}

        rsp = self.client.post(adresse("zaehlposition-list"), {**neu, "eingabemenge": "10000000"})
        self.pruefe_abgewiesen(rsp, "eingabemenge", PORTIONEN_ZU_VIEL)
        self.assertFalse(Zaehlposition.objects.filter(artikel_id=self.portion.pk).exists())

        position = self.client.post(adresse("zaehlposition-list"), neu).data["id"]
        ziel = adresse("zaehlposition-detail", position)
        rsp = self.client.patch(ziel, {"eingabemenge": "10000000"})
        self.pruefe_abgewiesen(rsp, "eingabemenge", PORTIONEN_ZU_VIEL)
        self.assertIsNone(Zaehlposition.objects.get(pk=position).eingabemenge)

        self.assertEqual(self.client.patch(ziel, {"eingabemenge": "7000000"}).status_code, 200)

    def test_eine_weitere_schale(self):
        position = self.waage()
        self.assertEqual(self.wiegen(position, "600000250").status_code, HTTPStatus.CREATED)

        self.pruefe_abgewiesen(self.wiegen(position, "400000250"), "brutto_gramm", SCHALEN_ZU_VIEL)

        self.assertEqual(self.gewogen(position), [Decimal("600000250")])
        zeile = self.client.get(adresse("zaehlposition-detail", position)).data
        self.assertEqual(zeile["menge_basiseinheit"], "600000000")

    def test_eine_berichtigte_schale(self):
        position = self.waage()
        erste = self.wiegen(position, "600000250").data["id"]
        self.wiegen(position, "300000250")

        rsp = self.client.patch(adresse("wiegung-detail", erste), {"brutto_gramm": "700000250"})

        self.pruefe_abgewiesen(rsp, "brutto_gramm", SCHALEN_ZU_VIEL)
        self.assertEqual(self.gewogen(position), [Decimal("300000250"), Decimal("600000250")])

    def test_dieselbe_schale_noch_einmal_mit_neuem_gewicht(self):
        """A weighing corrected in a dead spot comes as the repeated job ('create')."""
        position = self.waage()
        self.wiegen(position, "600000250", kennung=KENNUNG)
        self.wiegen(position, "300000250")

        rsp = self.wiegen(position, "700000250", kennung=KENNUNG)

        self.pruefe_abgewiesen(rsp, "brutto_gramm", SCHALEN_ZU_VIEL)
        self.assertEqual(self.gewogen(position), [Decimal("300000250"), Decimal("600000250")])

    def test_eine_wiederbelebte_zeile_rechnet_ohne_ihre_alten_schalen(self):
        """Removed in the app, the row comes back with the new bowl alone."""
        position = self.waage()
        self.wiegen(position, "900000250")
        self.client.delete(adresse("zaehlposition-detail", position))

        rsp = self.wiegen(position, "900000250")

        self.assertEqual(rsp.status_code, HTTPStatus.CREATED, rsp.data)
        self.assertEqual(self.gewogen(position), [Decimal("900000250")])
