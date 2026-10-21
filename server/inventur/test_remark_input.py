"""What the check list takes from outside: how long a note may be, what the page sends back.

A note had no limit. Two megabytes were stored and came back with every
check list of the app and of the website (until 10/2026). It holds at most
'NOTIZ_HOECHSTLAENGE' characters now. A longer one is refused with a
sentence: in the app at its field, on the page for its row, while the
other rows are saved.

A background save hands the remarks' state back in a header. Where nothing
was written, that was the value the page sent, as it sent it: one with a
line break ended the answer in a server error (until 10/2026). And the
sentence of a refusal names articles, so it goes out as text, not as HTML.
"""

from http import HTTPStatus

from django.core.exceptions import ValidationError
from django.urls import reverse

from api.test_review_list import PruefGrundlagen

from .services import NOTIZ_HOECHSTLAENGE, bemerkungsstand, vermerk_aendern
from .test_manual_difference import DifferenzVonHand
from .views.review import BEMERKUNGSSTAND_KOPF, NICHT_GESPEICHERT_KOPF

#: What the app hears for a longer note, at the field.
ZU_LANG = "Eine Notiz hat höchstens 2000 Zeichen."


class NotizInDerApp(PruefGrundlagen):
    def setUp(self):
        self.anmelden()

    def test_eine_zu_lange_notiz_wird_abgewiesen(self):
        rsp = self.aendern(self.cola, notiz="x" * (NOTIZ_HOECHSTLAENGE + 1))

        self.assertEqual(rsp.status_code, HTTPStatus.BAD_REQUEST)
        self.assertEqual(rsp.data["notiz"], [ZU_LANG])
        self.assertFalse(self.inventur.notizen.exists())

    def test_bis_zur_grenze_geht_sie_durch(self):
        """Counted as it is stored: without the spaces around it."""
        rsp = self.aendern(self.cola, notiz=f"  {'x' * NOTIZ_HOECHSTLAENGE}  ")

        self.assertEqual(rsp.status_code, HTTPStatus.OK, rsp.data)
        self.assertEqual(len(self.inventur.notizen.get().text), NOTIZ_HOECHSTLAENGE)

    def test_der_dienst_weist_sie_ebenso_ab(self):
        """For every other way in, the demo for one."""
        with self.assertRaisesMessage(ValidationError, ZU_LANG):
            vermerk_aendern(self.inventur, self.cola.pk, text="x" * (NOTIZ_HOECHSTLAENGE + 1))
        self.assertFalse(self.inventur.notizen.exists())


class NotizInDerPruefliste(DifferenzVonHand):
    def setUp(self):
        super().setUp()
        self.inv = self.inventur(von_hand=False)
        self.adresse = reverse("inventur_pruefen", args=[self.inv.pk])

    def test_das_feld_nimmt_nicht_mehr_an(self):
        antwort = self.client.get(self.adresse)

        self.assertContains(
            antwort, f'name="notiz_{self.artikel.pk}" maxlength="{NOTIZ_HOECHSTLAENGE}"'
        )

    def test_eine_zu_lange_zeile_bleibt_liegen(self):
        """A page from before the limit, or a request built by hand. The other row is saved."""
        artikel, anderer = self.artikel.pk, self.anderer.pk

        antwort = self.client.post(
            self.adresse,
            {
                f"notiz_{artikel}": "x" * (NOTIZ_HOECHSTLAENGE + 1),
                f"notiz_vorher_{artikel}": "",
                f"notiz_{anderer}": "Bruch",
                f"notiz_vorher_{anderer}": "",
                "nebenbei": "1",
            },
        )

        self.assertEqual(
            (antwort.status_code, antwort.content.decode()),
            (
                HTTPStatus.BAD_REQUEST,
                "Die Notiz zu „Artikel C 0,33 Flasche“ hat mehr als 2000 Zeichen. Sie ist nicht "
                "gespeichert.",
            ),
        )
        self.assertEqual(
            antwort[NICHT_GESPEICHERT_KOPF].split(), [f"notiz_{artikel}", f"differenz_{artikel}"]
        )
        self.assertEqual(
            dict(self.inv.notizen.values_list("hausartikel", "text")), {anderer: "Bruch"}
        )


class AntwortImHintergrund(DifferenzVonHand):
    """What a background save of the check list answers, whatever the page sent."""

    def setUp(self):
        super().setUp()
        self.inv = self.inventur(von_hand=False)
        self.adresse = reverse("inventur_pruefen", args=[self.inv.pk])

    def nebenbei(self, **felder):
        return self.client.post(self.adresse, felder | {"nebenbei": "1"})

    def test_ein_stand_mit_zeilenumbruch_kommt_nicht_zurueck(self):
        for felder in ({}, {f"notiz_{self.artikel.pk}": "Bruch"}):
            with self.subTest(felder):
                antwort = self.nebenbei(bemerkungsstand="n1\r\nX-Probe: 1", **felder)

                self.assertEqual(antwort.status_code, HTTPStatus.NO_CONTENT)
                self.assertFalse(antwort.has_header(BEMERKUNGSSTAND_KOPF))
                self.assertFalse(antwort.has_header("X-Probe"))

    def test_der_eigene_stand_kommt_wie_bisher_zurueck(self):
        """Nothing written, nobody else either: the page carries on the state it has."""
        stand = bemerkungsstand(self.inv)

        antwort = self.nebenbei(bemerkungsstand=stand)

        self.assertEqual(antwort[BEMERKUNGSSTAND_KOPF], stand)

    def test_eine_abweisung_ist_text(self):
        pk = self.artikel.pk
        zu_lang = {f"notiz_{pk}": "x" * (NOTIZ_HOECHSTLAENGE + 1), f"notiz_vorher_{pk}": ""}

        abgewiesen = self.nebenbei(**zu_lang)
        self.inv.abschliessen()
        abgeschlossen = self.nebenbei(**{f"notiz_{pk}": "Bruch", f"notiz_vorher_{pk}": ""})

        for antwort, status in (
            (abgewiesen, HTTPStatus.BAD_REQUEST),
            (abgeschlossen, HTTPStatus.CONFLICT),
        ):
            with self.subTest(status):
                self.assertEqual(antwort.status_code, status)
                self.assertEqual(antwort["Content-Type"], "text/plain; charset=utf-8")
