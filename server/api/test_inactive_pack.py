"""A new row of the app never stands on an inactive pack.

An inactive pack gets no row when an inventory is created, and the app does
not offer it ('api/master_data.py'): the product is no longer carried in it.
What is left on the shelf is counted in one of the active packs. A job from
a dead spot can still name one that was set inactive meanwhile. Until
10/2026 it created the row, or revived a removed one, on that pack. Now it
hears what a pack that is gone hears ('GEBINDE_WEG'), and the app fetches
its master data anew. Rows that already stand on the pack keep counting
until closing, and a row that exists answers as it did ('dublette').
"""

from decimal import Decimal
from http import HTTPStatus
from unittest import mock

from inventur.models import Zaehlposition
from katalog.models import Gebinde

from .serializers import GEBINDE_WEG
from .serializers import counting as counting_serializers
from .tests import ApiGrundlagen, adresse


class InaktivesGebinde(ApiGrundlagen):
    def setUp(self):
        super().setUp()
        self.anmelden()

    def stilllegen(self) -> None:
        Gebinde.objects.filter(pk=self.einzeln.pk).update(aktiv=False)

    def anlegen(self, **daten):
        return self.client.post(
            adresse("zaehlposition-list"),
            {
                "inventur": self.inventur.pk,
                "ort": self.lager.pk,
                "artikel": self.cola.pk,
                "gebinde": self.einzeln.pk,
                "eingabemenge": "4",
            }
            | daten,
        )

    def zeilen_im_lager(self):
        return Zaehlposition.objects.filter(inventur=self.inventur, ort=self.lager)

    def pruefe_abgewiesen(self, rsp) -> None:
        self.assertEqual(rsp.status_code, HTTPStatus.BAD_REQUEST, rsp.data)
        self.assertEqual(rsp.data, {"gebinde": [str(GEBINDE_WEG)]})

    def test_keine_neue_zeile(self):
        self.stilllegen()

        rsp = self.anlegen()

        self.pruefe_abgewiesen(rsp)
        self.assertFalse(self.zeilen_im_lager().exists())

    def test_keine_wiederbelebte(self):
        """Removed before the pack went inactive: it does not come back on it."""
        entfernt = Zaehlposition.objects.create(
            inventur=self.inventur, ort=self.lager, artikel=self.cola, gebinde=self.einzeln
        )
        Zaehlposition.objects.filter(pk=entfernt.pk).update(aktiv=False)
        self.stilllegen()

        rsp = self.anlegen()

        self.pruefe_abgewiesen(rsp)
        entfernt.refresh_from_db()
        self.assertEqual((entfernt.aktiv, entfernt.eingabemenge), (False, None))

    def test_auch_wenn_es_erst_nach_der_pruefung_stillgelegt_wird(self):
        """Asked once more under the lock of the article ('aktuelles_gebinde')."""
        echt = counting_serializers.ZaehlpositionAnlegenSerializer.validate

        def pruefen_und_stilllegen(serializer, daten):
            daten = echt(serializer, daten)
            self.stilllegen()
            return daten

        with mock.patch.object(
            counting_serializers.ZaehlpositionAnlegenSerializer,
            "validate",
            autospec=True,
            side_effect=pruefen_und_stilllegen,
        ):
            rsp = self.anlegen()

        self.pruefe_abgewiesen(rsp)
        self.assertFalse(self.zeilen_im_lager().exists())

    def test_eine_vorhandene_zeile_nennt_sich_weiter(self):
        """The first send created it, the answer was lost: the app switches to changing it."""
        vorhanden = Zaehlposition.objects.create(
            inventur=self.inventur,
            ort=self.lager,
            artikel=self.cola,
            gebinde=self.einzeln,
            eingabemenge=Decimal("4"),
            unerwartet=True,
        )
        self.stilllegen()

        rsp = self.anlegen()

        self.assertEqual(rsp.status_code, HTTPStatus.BAD_REQUEST, rsp.data)
        self.assertEqual(rsp.data["vorhanden_id"], [str(vorhanden.pk)])

    def test_auf_einer_vorhandenen_zeile_wird_weiter_gezaehlt(self):
        """Counted is counted, until closing."""
        position = self.inventur.zaehlpositionen.get(gebinde=self.kasten)
        Gebinde.objects.filter(pk=self.kasten.pk).update(aktiv=False)

        rsp = self.client.patch(adresse("zaehlposition-detail", position.pk), {"eingabemenge": "3"})

        self.assertEqual(rsp.status_code, HTTPStatus.OK, rsp.data)
        position.refresh_from_db()
        self.assertEqual(position.eingabemenge, Decimal("3"))
