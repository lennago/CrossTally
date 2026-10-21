"""Locking the count while the app writes a quantity or a weighing.

Locking waits for what is being counted at that moment: it locks the
inventory and then every row by number, as the closing does
('Inventur.sperre_setzen', 'zum_abschluss_sperren'). A quantity or a
weighing locks only its row and asks the inventory's state once more under
it ('serializers.counting.gesperrte_zeile'). Until 10/2026 the lock touched
only the inventory, which those writes never lock: a count checked a moment
before "Lock" landed after it, with 200 or 201.

'ErstDieZeilen' reads the locks off the queries and puts the lock between
the check and the row by hand, on SQLite as well. 'NebenDerSperre' forces
the overlap on PostgreSQL with two connections, in both orders.
"""

from decimal import Decimal
from http import HTTPStatus
from unittest import mock, skipUnless

from django.db import connection

from inventur.models import Inventur, Inventurstatus, Wiegung, Zaehlposition
from inventur.test_merge_race import Nebeneinander, erste_abfrage
from katalog.models import Artikel
from katalog.test_ignore_race import MitZaehlung

from .serializers import counting
from .test_merge_split_race import abfragen_waehrend
from .tests import ApiGrundlagen, adresse


class ErstDieZeilen(ApiGrundlagen):
    def setUp(self):
        super().setUp()
        self.position = self.inventur.zaehlpositionen.get(artikel=self.cola)
        self.gewogen = Zaehlposition.objects.create(
            inventur=self.inventur, ort=self.lager, artikel=self.nachos
        )
        self.wiegung = Wiegung.objects.create(
            zaehlposition=self.gewogen, brutto_gramm=Decimal("900"), behaelter=self.schale
        )

    def dazwischen(self, **stand):
        """The inventory changes after the check and before the write locks its row."""
        echt = counting.gesperrte_zeile

        def erst_umgestellt(position):
            Inventur.objects.filter(pk=self.inventur.pk).update(**stand)
            return echt(position)

        return mock.patch.object(counting, "gesperrte_zeile", side_effect=erst_umgestellt)

    def test_das_sperren_sperrt_jede_zeile_nach_der_inventur(self):
        """In the closing's order: inventory without the key, the articles, then the rows."""
        abfragen = abfragen_waehrend(
            lambda: Inventur.objects.get(pk=self.inventur.pk).sperre_setzen(True)
        )

        inventur = erste_abfrage(abfragen, Inventur, gesperrt=True)
        artikel = erste_abfrage(abfragen, Artikel, gesperrt=True)
        zeilen = erste_abfrage(abfragen, Zaehlposition, gesperrt=True)
        geschrieben = erste_abfrage(abfragen, Inventur, beginnt="UPDATE")
        self.assertLess(inventur, artikel)
        self.assertLess(artikel, zeilen, "die Zeilen vor den Artikeln")
        self.assertLess(zeilen, geschrieben, "gesperrt, bevor die Zeilen gesperrt sind")
        self.assertTrue(abfragen[inventur][2], "die Inventur mit FOR UPDATE")
        self.assertEqual(abfragen[zeilen][3], ("pk",))
        self.inventur.refresh_from_db()
        self.assertTrue(self.inventur.gesperrt)

    def test_jeder_schreibweg_hinter_der_sperre_wartet(self):
        """409 with 'gesperrt', and nothing written: the app keeps the job until it opens."""
        self.anmelden()
        faelle = {
            "Menge": lambda: self.client.patch(
                adresse("zaehlposition-detail", self.position.pk), {"eingabemenge": "3"}
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
        }
        for name, aufruf in faelle.items():
            with self.subTest(name):
                Inventur.objects.filter(pk=self.inventur.pk).update(gesperrt=False)
                with self.dazwischen(gesperrt=True):
                    rsp = aufruf()

                self.assertEqual(rsp.status_code, HTTPStatus.CONFLICT, rsp.data)
                self.assertTrue(rsp.data["gesperrt"])
        self.position.refresh_from_db()
        self.assertIsNone(self.position.eingabemenge)
        self.assertEqual(
            list(self.gewogen.wiegungen.values_list("brutto_gramm", flat=True)), [Decimal("900")]
        )

    def test_zurueck_in_die_vorbereitung_haelt_die_menge_auch_auf(self):
        """Taken back meanwhile: 409 with 'vorlaeufig', as for any write in preparation."""
        self.anmelden()
        with self.dazwischen(status=Inventurstatus.VORBEREITUNG):
            rsp = self.client.patch(
                adresse("zaehlposition-detail", self.position.pk), {"eingabemenge": "3"}
            )

        self.assertEqual(rsp.status_code, HTTPStatus.CONFLICT, rsp.data)
        self.assertTrue(rsp.data["vorlaeufig"])
        self.position.refresh_from_db()
        self.assertIsNone(self.position.eingabemenge)


@skipUnless(connection.vendor == "postgresql", "SQLite kennt keine Sperre einzelner Zeilen")
class NebenDerSperre(MitZaehlung, Nebeneinander):
    """A quantity or a weighing from the app and "Lock" at the same moment, in both orders."""

    def setUp(self):
        self.zaehlung_anlegen()

    def sperren(self) -> None:
        Inventur.objects.get(pk=self.inventur.pk).sperre_setzen(True)

    def test_eine_menge_vor_der_sperre_geprueft_wartet_danach(self):
        """Checked before the lock, its row after it: the quantity hears 409 and writes nothing."""
        ergebnis = self.gleichzeitig(self.menge, self.sperren, (counting, "gesperrte_zeile"))

        self.assertEqual(ergebnis["zuerst"].status_code, HTTPStatus.CONFLICT)
        self.assertTrue(ergebnis["zuerst"].data["gesperrt"])
        self.assertEqual(self.zeile(self.kastenzeile), (True, None, 0))

    def test_eine_wiegung_vor_der_sperre_geprueft_wartet_danach(self):
        ergebnis = self.gleichzeitig(self.wiegung, self.sperren, (counting, "gesperrte_zeile"))

        self.assertEqual(ergebnis["zuerst"].status_code, HTTPStatus.CONFLICT)
        self.assertTrue(ergebnis["zuerst"].data["gesperrt"])
        self.assertEqual(self.zeile(self.waage), (True, None, 0))

    def test_das_sperren_wartet_auf_die_menge_die_ihre_zeile_haelt(self):
        """Once locked, the quantity is in: the check list begins on numbers that stand."""

        def sperren_und_nachsehen():
            self.sperren()
            return Zaehlposition.objects.get(pk=self.kastenzeile.pk).eingabemenge

        ergebnis = self.gleichzeitig(self.menge, sperren_und_nachsehen, (counting, "pruefen"))

        self.assertEqual(ergebnis["zuerst"].status_code, HTTPStatus.OK)
        self.assertEqual(ergebnis["danach"], Decimal("7"))
        self.assertTrue(Inventur.objects.get(pk=self.inventur.pk).gesperrt)
