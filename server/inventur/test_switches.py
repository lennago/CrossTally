"""The switches and the remarks of an inventory check the fresh row under a lock.

An object a request loaded can be stale: another request may have closed the
inventory or read in a target in the meantime. Until 10/2026 the switches
trusted the object, a remark was written past a closing, and a stale
difference on the check list threw the notes of the other rows back.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.urls import reverse

from .models import Inventur, Inventurnotiz, Inventurstatus, Sollbestand
from .services import InventurAbgeschlossen, notizen_speichern, vermerk_aendern
from .test_manual_difference import DifferenzVonHand


class SchalterAmFrischenSatz(DifferenzVonHand):
    def test_ein_altes_objekt_schaltet_nach_dem_abschluss_nicht(self):
        inventur = self.inventur(von_hand=False)
        alt = Inventur.objects.get(pk=inventur.pk)
        Inventur.objects.get(pk=inventur.pk).abschliessen()

        for schalten in (
            lambda: alt.differenz_von_hand_setzen(True),
            lambda: alt.sperre_setzen(True),
            alt.zurueck_in_vorbereitung,
        ):
            with self.assertRaises(ValidationError):
                schalten()

        frisch = Inventur.objects.get(pk=inventur.pk)
        self.assertEqual(frisch.status, Inventurstatus.ABGESCHLOSSEN)
        self.assertFalse(frisch.differenz_von_hand)
        self.assertFalse(frisch.gesperrt)

    def test_ein_altes_objekt_sieht_das_inzwischen_eingelesene_soll(self):
        inventur = self.inventur(von_hand=False)
        alt = Inventur.objects.get(pk=inventur.pk)
        Sollbestand.objects.create(
            inventur=inventur, hausartikel=self.artikel, menge_basiseinheit=Decimal("30")
        )

        with self.assertRaises(ValidationError):
            alt.differenz_von_hand_setzen(True)

        self.assertFalse(Inventur.objects.get(pk=inventur.pk).differenz_von_hand)


class VermerkeNachDemAbschluss(DifferenzVonHand):
    def test_ein_vermerk_nach_dem_abschluss_wird_abgewiesen(self):
        """The closing froze the notes. One arriving after it must not land anywhere."""
        inventur = self.inventur()
        alt = Inventur.objects.get(pk=inventur.pk)
        inventur.abschliessen()

        with self.assertRaises(InventurAbgeschlossen) as ergebnis:
            vermerk_aendern(alt, self.artikel.pk, text="zu spät")

        self.assertEqual(ergebnis.exception.inventur.status, Inventurstatus.ABGESCHLOSSEN)
        self.assertFalse(Inventurnotiz.objects.filter(inventur=inventur).exists())


class DifferenzenEinerAelterenSeite(DifferenzVonHand):
    """The page was built with the difference by hand on, then someone switched it off."""

    def test_die_notizen_bleiben_die_differenzen_nicht(self):
        inventur = self.inventur()
        inventur.differenz_von_hand_setzen(False)

        notizen_speichern(
            inventur,
            {self.anderer.pk: "Neue Notiz zu N"},
            set(),
            {self.anderer.pk: ("", False)},
            {self.artikel.pk: (Decimal("-3"), None)},
        )

        notiz = Inventurnotiz.objects.get(inventur=inventur, hausartikel=self.anderer)
        self.assertEqual(notiz.text, "Neue Notiz zu N")
        self.assertFalse(
            Inventurnotiz.objects.filter(inventur=inventur, hausartikel=self.artikel).exists()
        )

    def test_die_seite_sagt_es(self):
        inventur = self.inventur()
        inventur.differenz_von_hand_setzen(False)

        antwort = self.client.post(
            reverse("inventur_pruefen", args=[inventur.pk]),
            {
                f"notiz_{self.anderer.pk}": "Neue Notiz zu N",
                f"notiz_vorher_{self.anderer.pk}": "",
                f"differenz_{self.artikel.pk}": "-3",
                f"differenz_vorher_{self.artikel.pk}": "",
            },
            follow=True,
        )

        self.assertContains(antwort, "die eingetragenen Differenzen nicht")
        self.assertTrue(
            Inventurnotiz.objects.filter(inventur=inventur, hausartikel=self.anderer).exists()
        )
