"""What a count or a weighing is checked against once more under the lock of its row.

'validate' looks before any lock. A product taken off the scale, a container
taken from it, a recipe article moved between pieces and grams: whatever
changes between that look and the row's lock is seen only under the lock
('serializers.counting.gesperrte_zeile'). Until 10/2026 a bowl landed on a row
that no longer weighed, in a container the row no longer offered, and a
number on a row the scale fills. Each counted as counted and added nothing.
The change is made here by hand after the check. Two connections force it
in 'test_weighing_race'.
"""

from decimal import Decimal
from http import HTTPStatus
from unittest import mock

from inventur.models import Wiegung, Zaehlposition
from katalog.models import Rezepturartikel, Ware

from .serializers import counting as counting_serializers
from .serializers.counting import GEWOGEN
from .tests import ApiGrundlagen, adresse


class UnterDerSperreDerZeile(ApiGrundlagen):
    def setUp(self):
        super().setUp()
        self.waage = Zaehlposition.objects.create(
            inventur=self.inventur, ort=self.lager, artikel=self.nachos, unerwartet=True
        )
        self.portionen = Zaehlposition.objects.create(
            inventur=self.inventur, ort=self.lager, artikel=self.portion, unerwartet=True
        )
        self.anmelden()

    @staticmethod
    def nach_der_pruefung(serializer, tun):
        """'tun' happens after 'validate' of 'serializer' passed, before the row is locked."""
        echt = serializer.validate

        def pruefen_und_tun(selbst, daten):
            daten = echt(selbst, daten)
            tun()
            return daten

        return mock.patch.object(serializer, "validate", autospec=True, side_effect=pruefen_und_tun)

    def wiegen(self, **daten):
        return self.client.post(
            adresse("wiegung-list"),
            {"zaehlposition": self.waage.pk, "brutto_gramm": "1250", "behaelter": self.schale.pk}
            | daten,
        )

    def nicht_mehr_gewogen(self) -> None:
        Ware.objects.filter(pk=self.nachos_ware.pk).update(abwiegbar=False)

    def in_gramm(self) -> None:
        Rezepturartikel.objects.filter(pk=self.portion.pk).update(basiseinheit=self.gramm)

    def test_eine_schale_auf_einer_zeile_die_nicht_mehr_wiegt(self):
        with self.nach_der_pruefung(
            counting_serializers.WiegungSerializer, self.nicht_mehr_gewogen
        ):
            rsp = self.wiegen()

        self.assertEqual(rsp.status_code, HTTPStatus.BAD_REQUEST, rsp.data)
        self.assertEqual(rsp.data, {"zaehlposition": ["„Artikel N lose“ wird nicht gewogen."]})
        self.assertFalse(Wiegung.objects.exists())

    def test_eine_schale_die_der_ware_nicht_mehr_gehoert(self):
        with self.nach_der_pruefung(
            counting_serializers.WiegungSerializer,
            lambda: self.nachos_ware.behaelter.remove(self.schale),
        ):
            rsp = self.wiegen()

        self.assertEqual(rsp.status_code, HTTPStatus.BAD_REQUEST, rsp.data)
        self.assertEqual(
            rsp.data,
            {
                "behaelter": [
                    "„Schale“ steht für „Artikel N lose“ nicht zur Wahl. Er ist weder dem "
                    "Artikel noch einem Haus zugeordnet."
                ]
            },
        )
        self.assertFalse(Wiegung.objects.exists())

    def test_eine_berichtigung_auf_einer_zeile_die_nicht_mehr_wiegt(self):
        wiegung = Wiegung.objects.create(
            zaehlposition=self.waage, brutto_gramm=Decimal("900"), behaelter=self.schale
        )

        with self.nach_der_pruefung(
            counting_serializers.WiegungSerializer, self.nicht_mehr_gewogen
        ):
            rsp = self.client.patch(adresse("wiegung-detail", wiegung.pk), {"brutto_gramm": "950"})

        self.assertEqual(rsp.status_code, HTTPStatus.BAD_REQUEST, rsp.data)
        wiegung.refresh_from_db()
        self.assertEqual(wiegung.brutto_gramm, Decimal("900"))

    def test_eine_anzahl_auf_einer_zeile_die_inzwischen_gewogen_wird(self):
        with self.nach_der_pruefung(
            counting_serializers.ZaehlpositionEingabeSerializer, self.in_gramm
        ):
            rsp = self.client.patch(
                adresse("zaehlposition-detail", self.portionen.pk), {"eingabemenge": "3"}
            )

        self.assertEqual(rsp.status_code, HTTPStatus.BAD_REQUEST, rsp.data)
        self.assertEqual(rsp.data, {"eingabemenge": [str(GEWOGEN)]})
        self.portionen.refresh_from_db()
        self.assertIsNone(self.portionen.eingabemenge)

    def neue_zeile(self):
        return self.client.post(
            adresse("zaehlposition-list"),
            {
                "inventur": self.inventur.pk,
                "ort": self.theke.pk,
                "artikel": self.portion.pk,
                "eingabemenge": "2",
            },
        )

    def test_eine_neue_zeile_mit_anzahl_die_inzwischen_gewogen_wird(self):
        with self.nach_der_pruefung(
            counting_serializers.ZaehlpositionAnlegenSerializer, self.in_gramm
        ):
            rsp = self.neue_zeile()

        self.assertEqual(rsp.status_code, HTTPStatus.BAD_REQUEST, rsp.data)
        self.assertEqual(rsp.data, {"eingabemenge": [str(GEWOGEN)]})
        self.assertFalse(
            Zaehlposition.objects.filter(ort=self.theke, artikel=self.portion).exists()
        )

    def test_ebenso_eine_wiederbelebte(self):
        """The row was removed. Revived with a number, it would be weighed by now."""
        Zaehlposition.objects.filter(pk=self.portionen.pk).update(ort=self.theke, aktiv=False)

        with self.nach_der_pruefung(
            counting_serializers.ZaehlpositionAnlegenSerializer, self.in_gramm
        ):
            rsp = self.neue_zeile()

        self.assertEqual(rsp.status_code, HTTPStatus.BAD_REQUEST, rsp.data)
        self.assertEqual(rsp.data, {"eingabemenge": [str(GEWOGEN)]})
        self.portionen.refresh_from_db()
        self.assertEqual((self.portionen.aktiv, self.portionen.eingabemenge), (False, None))

    def test_ohne_aenderung_dazwischen_geht_alles_durch(self):
        """The checks under the lock refuse nothing the check before let through."""
        self.assertEqual(self.wiegen().status_code, HTTPStatus.CREATED)
        rsp = self.client.patch(
            adresse("zaehlposition-detail", self.portionen.pk), {"eingabemenge": "3"}
        )
        self.assertEqual(rsp.status_code, HTTPStatus.OK, rsp.data)
