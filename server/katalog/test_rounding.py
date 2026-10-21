"""Half up everywhere, as people round: 12.5 pieces are 13 on the sheet.

Python rounds half to even by default. Until 10/2026 the unit change and the
merge rounded half up while the sheet, the check list and the app rounded
half to even, so 12.5 read as 12 and 13.5 as 14.
"""

from decimal import Decimal

from django.test import TestCase

from .formats.base import menge_lesen
from .models import Basiseinheit
from .models.units import menge_kurz


class Rundung(TestCase):
    def test_einheit(self):
        stueck = Basiseinheit.objects.get(name="Stück(e)")
        self.assertEqual(stueck.runden(Decimal("12.5")), Decimal("13"))
        self.assertEqual(stueck.runden(Decimal("13.5")), Decimal("14"))
        kilogramm = Basiseinheit.objects.get(name="kg")
        self.assertEqual(kilogramm.runden(Decimal("0.0005")), Decimal("0.001"))

    def test_keine_negative_null(self):
        """A shortage that rounds to nothing is 0. "-0" would still read like a shortage."""
        stueck = Basiseinheit.objects.get(name="Stück(e)")
        kilogramm = Basiseinheit.objects.get(name="kg")
        for einheit, wert in ((stueck, "-0.4"), (kilogramm, "-0.0004")):
            with self.subTest(einheit=einheit.name):
                gerundet = einheit.runden(Decimal(wert))
                self.assertFalse(gerundet.is_signed())
                self.assertEqual(menge_kurz(gerundet), "0")
        # Half away from zero, also below it.
        self.assertEqual(stueck.runden(Decimal("-0.5")), Decimal("-1"))

    def test_menge_aus_der_datei(self):
        self.assertEqual(menge_lesen("0,0005"), Decimal("0.001"))
