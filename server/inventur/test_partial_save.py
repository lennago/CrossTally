"""A background save of the check list that stored only part names the fields it left.

A 400 used to say only what went wrong: a difference that is no number, a row
someone else wrote in, differences of a page from before the switch went off.
The other rows were stored all the same. The page kept what they showed at
loading, and changing one of them again heard that someone else had written
there (until 10/2026). Now the answer names the fields it did not store, and
'notes.js' moves the rest along ('app/e2e/review.spec.js').
"""

from http import HTTPStatus
from unittest import mock

from django.urls import reverse

from .models import Inventur
from .services import bemerkungsstand, vermerk_aendern
from .test_manual_difference import DifferenzVonHand
from .views.review import BEMERKUNGSSTAND_KOPF, NICHT_GESPEICHERT_KOPF, InventurPruefenView


class WasLiegenBleibt(DifferenzVonHand):
    def setUp(self):
        super().setUp()
        self.inv = self.inventur()
        self.adresse = reverse("inventur_pruefen", args=[self.inv.pk])

    def nebenbei(self, **felder):
        """As 'notes.js' sends rows when a field is left."""
        return self.client.post(self.adresse, felder | {"nebenbei": "1"})

    def notizen(self) -> dict[int, str]:
        return dict(self.inv.notizen.values_list("hausartikel", "text"))

    def test_eine_differenz_ohne_zahl_nennt_nur_ihr_feld(self):
        """Note and tick of the row are stored, and the page carries that on as its own."""
        pk = self.artikel.pk

        antwort = self.nebenbei(
            bemerkungsstand=bemerkungsstand(self.inv),
            **{
                f"notiz_{pk}": "Regal hinten",
                f"notiz_vorher_{pk}": "",
                f"differenz_{pk}": "drei",
                f"differenz_vorher_{pk}": "",
            },
        )

        self.assertEqual(
            (antwort.status_code, antwort.content.decode()),
            (
                HTTPStatus.BAD_REQUEST,
                "Nicht gespeichert, weil die Differenz keine Zahl ist (höchstens drei "
                "Nachkommastellen): Artikel C 0,33 Flasche.",
            ),
        )
        self.assertEqual(antwort[NICHT_GESPEICHERT_KOPF], f"differenz_{pk}")
        self.assertEqual(self.notizen(), {pk: "Regal hinten"})
        self.assertIsNone(self.inv.notizen.get().differenz)
        self.assertEqual(antwort[BEMERKUNGSSTAND_KOPF], bemerkungsstand(self.inv))

    def test_eine_zeile_mit_fremdem_eintrag_bleibt_ganz(self):
        """The app wrote in one row since the page was loaded. The other row is stored."""
        artikel, anderer = self.artikel.pk, self.anderer.pk
        vermerk_aendern(self.inv, artikel, text="aus der Zähl-App", text_vorher="")

        antwort = self.nebenbei(
            **{
                f"notiz_{artikel}": "Regal hinten",
                f"notiz_vorher_{artikel}": "",
                f"notiz_{anderer}": "Bruch",
                f"notiz_vorher_{anderer}": "",
            }
        )

        self.assertEqual(
            (antwort.status_code, antwort.content.decode()),
            (
                HTTPStatus.BAD_REQUEST,
                "Nicht gespeichert, weil inzwischen jemand anderes dort etwas eingetragen hat: "
                "Artikel C 0,33 Flasche. Neu laden zeigt, was jetzt dort steht.",
            ),
        )
        self.assertEqual(
            antwort[NICHT_GESPEICHERT_KOPF].split(), [f"notiz_{artikel}", f"differenz_{artikel}"]
        )
        self.assertEqual(self.notizen(), {artikel: "aus der Zähl-App", anderer: "Bruch"})

    def test_eine_seite_von_vor_dem_ausschalten_nennt_ihre_differenzen(self):
        artikel, anderer = self.artikel.pk, self.anderer.pk
        self.inv.differenz_von_hand_setzen(False)

        antwort = self.nebenbei(
            **{
                f"notiz_{anderer}": "Neue Notiz zu N",
                f"notiz_vorher_{anderer}": "",
                f"differenz_{anderer}": "",
                f"differenz_vorher_{anderer}": "",
                f"notiz_{artikel}": "",
                f"notiz_vorher_{artikel}": "",
                f"differenz_{artikel}": "-3",
                f"differenz_vorher_{artikel}": "",
            }
        )

        self.assertEqual(antwort.status_code, HTTPStatus.BAD_REQUEST)
        self.assertIn("die eingetragenen Differenzen nicht", antwort.content.decode())
        # Unchanged, the other difference had nothing to store.
        self.assertEqual(antwort[NICHT_GESPEICHERT_KOPF], f"differenz_{artikel}")
        self.assertEqual(self.notizen(), {anderer: "Neue Notiz zu N"})

    def test_beides_in_einer_zeile_nennt_jedes_feld_einmal(self):
        pk = self.artikel.pk
        vermerk_aendern(self.inv, pk, text="aus der Zähl-App", text_vorher="")

        antwort = self.nebenbei(
            **{
                f"notiz_{pk}": "Regal hinten",
                f"notiz_vorher_{pk}": "",
                f"differenz_{pk}": "drei",
                f"differenz_vorher_{pk}": "",
            }
        )

        self.assertEqual(
            antwort[NICHT_GESPEICHERT_KOPF].split(), [f"notiz_{pk}", f"differenz_{pk}"]
        )

    def test_wo_nichts_gespeichert_ist_nennt_sie_nichts(self):
        """Switched off after the request read the inventory: the save goes back whole.

        The lock reads it anew, refuses the difference, and the note of the
        other row goes back with it. Without the header the page moves nothing.
        """
        artikel, anderer = self.artikel.pk, self.anderer.pk
        gelesen = Inventur.objects.get(pk=self.inv.pk)
        self.inv.differenz_von_hand_setzen(False)

        with mock.patch.object(InventurPruefenView, "get_object", return_value=gelesen):
            antwort = self.nebenbei(
                **{
                    f"notiz_{anderer}": "Neue Notiz zu N",
                    f"notiz_vorher_{anderer}": "",
                    f"differenz_{artikel}": "-3",
                    f"differenz_vorher_{artikel}": "",
                }
            )

        self.assertEqual(
            (antwort.status_code, antwort.content.decode()),
            (
                HTTPStatus.BAD_REQUEST,
                "Bei dieser Inventur wird die Differenz nicht von Hand eingetragen. Sie ergibt "
                "sich aus dem Sollbestand.",
            ),
        )
        self.assertFalse(antwort.has_header(NICHT_GESPEICHERT_KOPF))
        self.assertEqual(self.notizen(), {})

    def test_alles_gespeichert_braucht_ihn_nicht(self):
        pk = self.artikel.pk

        antwort = self.nebenbei(**{f"notiz_{pk}": "Regal hinten", f"notiz_vorher_{pk}": ""})

        self.assertEqual(antwort.status_code, HTTPStatus.NO_CONTENT)
        self.assertFalse(antwort.has_header(NICHT_GESPEICHERT_KOPF))
