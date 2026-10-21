"""A locked count: the app checks, it does not count ('Inventur.sperre_setzen').

Counts answer like an inventory in preparation, so a phone keeps what it
sends from a dead spot until the lock opens. The check list goes on: that is
when checking begins.
"""

from decimal import Decimal
from http import HTTPStatus

from inventur.models import Wiegung, Zaehlposition

from .test_review_list import PruefGrundlagen
from .tests import adresse


class Gesperrt(PruefGrundlagen):
    def setUp(self):
        super().setUp()
        self.gewogen = Zaehlposition.objects.create(
            inventur=self.inventur, ort=self.lager, artikel=self.nachos
        )
        self.wiegung = Wiegung.objects.create(
            zaehlposition=self.gewogen, brutto_gramm=Decimal("900"), behaelter=self.schale
        )
        self.position = self.inventur.zaehlpositionen.get(artikel=self.cola)
        self.inventur.sperre_setzen(True)

    def test_jeder_schreibweg_des_zaehlens_wartet(self):
        """409 with 'vorlaeufig': the app keeps the job, also one from before the lock."""
        self.anmelden()
        faelle = {
            "Menge": lambda: self.client.patch(
                adresse("zaehlposition-detail", self.position.pk), {"eingabemenge": "3"}
            ),
            "Zeile anlegen": lambda: self.client.post(
                adresse("zaehlposition-list"),
                {"inventur": self.inventur.pk, "ort": self.lager.pk, "artikel": self.cola.pk},
            ),
            "Zeile löschen": lambda: self.client.delete(
                adresse("zaehlposition-detail", self.position.pk)
            ),
            "Wiegung anlegen": lambda: self.client.post(
                adresse("wiegung-list"),
                {
                    "zaehlposition": self.gewogen.pk,
                    "brutto_gramm": "700",
                    "behaelter": self.schale.pk,
                },
            ),
            "Wiegung berichtigen": lambda: self.client.patch(
                adresse("wiegung-detail", self.wiegung.pk), {"brutto_gramm": "950"}
            ),
            "Wiegung löschen": lambda: self.client.delete(
                adresse("wiegung-detail", self.wiegung.pk)
            ),
        }
        for name, aufruf in faelle.items():
            with self.subTest(name):
                rsp = aufruf()
                self.assertEqual(rsp.status_code, HTTPStatus.CONFLICT, rsp.data)
                self.assertEqual(
                    (rsp.data["konflikt"], rsp.data["vorlaeufig"], rsp.data["gesperrt"]),
                    ("status", True, True),
                )
                self.assertIn("gesperrt", rsp.data["detail"])
        self.position.refresh_from_db()
        self.assertIsNone(self.position.eingabemenge)
        self.assertTrue(self.position.aktiv)
        self.assertEqual(self.inventur.zaehlpositionen.count(), 2)
        self.assertEqual(self.gewogen.wiegungen.get().brutto_gramm, Decimal("900"))

    def test_geoeffnet_geht_die_menge_durch(self):
        self.inventur.sperre_setzen(False)
        self.anmelden()

        rsp = self.client.patch(
            adresse("zaehlposition-detail", self.position.pk), {"eingabemenge": "3"}
        )

        self.assertEqual(rsp.status_code, HTTPStatus.OK, rsp.data)

    def test_die_app_sieht_die_inventur_mit_ihrem_schloss(self):
        self.anmelden()

        [inventur] = self.client.get(adresse("inventur-list")).data
        detail = self.client.get(adresse("inventur-detail", self.inventur.pk)).data

        for daten in (inventur, detail):
            self.assertTrue(daten["gesperrt"])
            self.assertFalse(daten["zaehlbar"])

    def test_die_pruefliste_geht_weiter(self):
        """Notes and the difference by hand: that is what the lock is for."""
        self.inventur.differenz_von_hand_setzen(True)
        self.anmelden()

        liste = self.client.get(adresse("inventur-pruefliste", self.inventur.pk))
        self.assertEqual(liste.status_code, 200)
        self.assertEqual(self.aendern(self.cola, notiz="Kasten im Keller").status_code, 200)
        self.assertEqual(self.aendern(self.cola, differenz="-2").status_code, 200)
        self.assertEqual(self.inventur.notizen.get().differenz, Decimal("-2"))


class SperreUmschalten(PruefGrundlagen):
    def sperren(self, an):
        return self.client.post(adresse("sperre_api", self.inventur.pk), {"an": an}, format="json")

    def test_die_fuehrungskraft_sperrt_und_oeffnet(self):
        self.anmelden(self.leitung())

        rsp = self.sperren(True)
        self.assertEqual(rsp.status_code, HTTPStatus.OK, rsp.data)
        self.assertEqual(rsp.data, {"gesperrt": True})
        self.inventur.refresh_from_db()
        self.assertTrue(self.inventur.gesperrt)

        # Locked, the switch still works: otherwise nobody could open it in the app.
        rsp = self.sperren(False)
        self.assertEqual(rsp.data, {"gesperrt": False})

    def test_zaehlende_schalten_nicht(self):
        self.anmelden()
        self.assertEqual(self.sperren(True).status_code, HTTPStatus.FORBIDDEN)
        self.inventur.refresh_from_db()
        self.assertFalse(self.inventur.gesperrt)

    def test_nur_ja_oder_nein(self):
        self.anmelden(self.leitung())
        self.assertEqual(self.sperren("ja").status_code, HTTPStatus.BAD_REQUEST)

    def test_ein_koerper_ohne_felder_ist_eine_absage(self):
        """A JSON array, a string or 'null': the answer to a missing 'an', not a server error.

        Until 10/2026 both switches read the body with '.get', and anything
        but an object ended in a server error.
        """
        self.client.raise_request_exception = False
        self.anmelden(self.leitung())
        for name in ("sperre_api", "differenz_von_hand_api"):
            for koerper in ("[]", '"x"', "null"):
                with self.subTest(name, koerper=koerper):
                    rsp = self.client.post(
                        adresse(name, self.inventur.pk), koerper, content_type="application/json"
                    )

                    self.assertEqual(rsp.status_code, HTTPStatus.BAD_REQUEST)
                    self.assertIn("an", rsp.json())
        self.inventur.refresh_from_db()
        self.assertFalse(self.inventur.gesperrt)

    def test_in_der_vorbereitung_gibt_es_nichts_zu_sperren(self):
        self.inventur.zurueck_in_vorbereitung()
        self.anmelden(self.leitung())

        rsp = self.sperren(True)

        self.assertEqual(rsp.status_code, HTTPStatus.CONFLICT, rsp.data)
        self.assertEqual(rsp.data["status"], "VORBEREITUNG")
