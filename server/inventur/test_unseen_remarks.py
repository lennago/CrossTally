"""Closing freezes only the remarks the page showed: notes, ticks and differences by hand.

The count state ('zaehlstand') sees only the count positions. A difference by
hand, a tick for the target or a note the app wrote after the check list was
loaded went into the result unseen until 10/2026: the page went on saying
"No new values", and closing froze the app's -5 where the page showed -2.
Now the pages carry the remarks' state ('bemerkungsstand'), the page's own
saves carry it on, and closing compares it under the lock.
"""

from decimal import Decimal
from http import HTTPStatus
from unittest import mock
from urllib.parse import urlencode

from django.contrib.messages import get_messages
from django.urls import reverse

from .closing import NEUE_BEMERKUNGEN, Entscheidungen, NeueBemerkungen, abschliessen
from .models import Inventur, Inventurnotiz, Inventurstatus, Zaehlposition
from .services import bemerkungsstand, notizen_speichern, vermerk_aendern, zaehlstand
from .test_manual_difference import DifferenzVonHand
from .test_run import LaufSeiten
from .test_views import STICHTAG
from .views.review import BEMERKUNGSSTAND_KOPF


def meldungen(antwort) -> list[str]:
    return [str(meldung) for meldung in get_messages(antwort.wsgi_request)]


class WasDerStandSieht(DifferenzVonHand):
    def setUp(self):
        super().setUp()
        self.inv = self.inventur()

    def geaendert(self, schritt, *, mit_notizen: bool = True) -> bool:
        vorher = bemerkungsstand(self.inv, mit_notizen=mit_notizen)
        schritt()
        self.inv.refresh_from_db()
        return bemerkungsstand(self.inv, mit_notizen=mit_notizen) != vorher

    def test_eine_notiz(self):
        self.assertTrue(
            self.geaendert(lambda: vermerk_aendern(self.inv, self.artikel.pk, text="Regal hinten"))
        )

    def test_der_haken_fuer_das_soll(self):
        self.assertTrue(
            self.geaendert(lambda: vermerk_aendern(self.inv, self.artikel.pk, soll_statt_ist=True))
        )

    def test_eine_differenz_von_hand(self):
        self.assertTrue(
            self.geaendert(
                lambda: vermerk_aendern(self.inv, self.artikel.pk, differenz=Decimal("-2"))
            )
        )

    def test_der_schalter_fuer_die_differenz_von_hand(self):
        """It decides which number is frozen: the typed difference or the computed one."""
        self.assertTrue(self.geaendert(lambda: self.inv.differenz_von_hand_setzen(False)))

    def test_eine_zaehlung_nicht(self):
        """That is the count state's business ('zaehlstand')."""
        position = self.inv.zaehlpositionen.get()
        position.eingabemenge = Decimal("3")

        self.assertFalse(self.geaendert(position.save))

    def test_ohne_notizen_sieht_er_den_text_nicht(self):
        """The run shows no notes, and a note alone does not stop it."""
        ohne = bemerkungsstand(self.inv, mit_notizen=False)
        self.assertEqual((ohne[0], bemerkungsstand(self.inv)[0]), ("z", "n"))
        self.assertFalse(
            self.geaendert(
                lambda: vermerk_aendern(self.inv, self.artikel.pk, text="Regal hinten"),
                mit_notizen=False,
            )
        )
        self.assertTrue(
            self.geaendert(
                lambda: vermerk_aendern(self.inv, self.artikel.pk, soll_statt_ist=True),
                mit_notizen=False,
            )
        )


class EigeneSicherungen(DifferenzVonHand):
    """The page's own writes are no news to it ('notizen_speichern')."""

    def setUp(self):
        super().setUp()
        self.inv = self.inventur()

    def test_tragen_den_stand_weiter(self):
        gesehen = bemerkungsstand(self.inv)

        getragen = notizen_speichern(
            self.inv, {self.artikel.pk: "Regal hinten"}, bemerkungen=gesehen
        ).bemerkungen

        self.assertNotEqual(getragen, gesehen)
        self.assertEqual(getragen, bemerkungsstand(self.inv))

    def test_nicht_ueber_einen_fremden_eintrag_hinweg(self):
        """Someone wrote at another article in between: the page keeps the state it had."""
        gesehen = bemerkungsstand(self.inv)
        vermerk_aendern(self.inv, self.anderer.pk, text="aus der Zähl-App")

        getragen = notizen_speichern(
            self.inv, {self.artikel.pk: "Regal hinten"}, bemerkungen=gesehen
        ).bemerkungen

        self.assertEqual(getragen, gesehen)
        self.assertNotEqual(getragen, bemerkungsstand(self.inv))

    def test_ohne_aenderung_bleibt_er_wie_er_kam(self):
        """Nothing to write, nothing locked: what the page carries stays as it is."""
        self.assertEqual(
            notizen_speichern(self.inv, {}, bemerkungen="n-wie-gesehen").bemerkungen,
            "n-wie-gesehen",
        )

    def test_ohne_stand_kommt_keiner_zurueck(self):
        self.assertIsNone(notizen_speichern(self.inv, {self.artikel.pk: "Regal"}).bemerkungen)


class DerAbschlussVergleicht(DifferenzVonHand):
    def setUp(self):
        super().setUp()
        self.inv = self.inventur()

    def schliessen(self, bemerkungen: str) -> None:
        abschliessen(
            self.inv, Entscheidungen(), gesehen=zaehlstand(self.inv), bemerkungen=bemerkungen
        )

    def test_ein_fremder_eintrag_seit_dem_laden_haelt_ihn_an(self):
        gesehen = bemerkungsstand(self.inv)
        vermerk_aendern(self.inv, self.artikel.pk, differenz=Decimal("-5"))

        with self.assertRaises(NeueBemerkungen):
            self.schliessen(gesehen)

        self.inv.refresh_from_db()
        self.assertEqual(self.inv.status, Inventurstatus.ZAEHLUNG)

    def test_unter_der_sperre_entscheidet_der_vergleich(self):
        with (
            mock.patch("inventur.closing.decisions.bemerkungsstand_wie", return_value="anders"),
            self.assertRaises(NeueBemerkungen),
        ):
            self.schliessen(bemerkungsstand(self.inv))

    def test_mit_dem_gesehenen_stand_schliesst_er_ab(self):
        self.schliessen(bemerkungsstand(self.inv))

        self.inv.refresh_from_db()
        self.assertEqual(self.inv.status, Inventurstatus.ABGESCHLOSSEN)


class DiePruefliste(DifferenzVonHand):
    """The case of the report: the page shows -2, the app writes -5, closing is pressed."""

    def setUp(self):
        super().setUp()
        self.inv = self.inventur()
        self.adresse = reverse("inventur_pruefen", args=[self.inv.pk])
        vermerk_aendern(self.inv, self.artikel.pk, differenz=Decimal("-2"))

    def laden(self) -> dict[str, str]:
        """What the form of the page sends, the row of the article included."""
        seite = self.client.get(self.adresse)
        pk = self.artikel.pk
        return {
            "zaehlstand": seite.context["zaehlstand"],
            "bemerkungsstand": seite.context["bemerkungsstand"],
            f"differenz_{pk}": "-2",
            f"differenz_vorher_{pk}": "-2",
            f"notiz_{pk}": "",
            f"notiz_vorher_{pk}": "",
        }

    def test_die_seite_traegt_den_stand(self):
        antwort = self.client.get(self.adresse)

        self.assertContains(antwort, f'name="bemerkungsstand" value="{bemerkungsstand(self.inv)}"')

    def test_was_die_app_nach_dem_laden_eintraegt_friert_nicht_ein(self):
        formular = self.laden()
        vermerk_aendern(
            self.inv, self.artikel.pk, differenz=Decimal("-5"), differenz_vorher=Decimal("-2")
        )

        antwort = self.client.post(self.adresse, formular | {"abschliessen": "1"})

        self.assertRedirects(antwort, self.adresse)
        self.assertEqual(meldungen(antwort), [str(NEUE_BEMERKUNGEN)])
        self.inv.refresh_from_db()
        self.assertEqual(self.inv.status, Inventurstatus.ZAEHLUNG)
        self.assertEqual(
            Inventurnotiz.objects.get(hausartikel=self.artikel).differenz, Decimal("-5")
        )

    def test_die_eigene_sicherung_im_hintergrund_haelt_ihn_nicht_an(self):
        formular = self.laden()
        notiz = {f"notiz_{self.artikel.pk}": "Regal hinten", "nebenbei": "1"}

        gesichert = self.client.post(self.adresse, formular | notiz)

        self.assertEqual(gesichert.status_code, HTTPStatus.NO_CONTENT)
        getragen = gesichert[BEMERKUNGSSTAND_KOPF]
        self.assertEqual(getragen, bemerkungsstand(self.inv))
        formular |= {
            "bemerkungsstand": getragen,
            f"notiz_{self.artikel.pk}": "Regal hinten",
            f"notiz_vorher_{self.artikel.pk}": "Regal hinten",
        }
        self.client.post(self.adresse, formular | {"abschliessen": "1"})
        self.inv.refresh_from_db()
        self.assertEqual(self.inv.status, Inventurstatus.ABGESCHLOSSEN)
        self.assertEqual(self.inv.ergebnisse.get(hausartikel=self.artikel).notiz, "Regal hinten")

    def test_eine_notiz_im_abschluss_selbst_haelt_ihn_nicht_an(self):
        """Without the script the form goes out as it stands, the note in it."""
        formular = self.laden() | {f"notiz_{self.artikel.pk}": "Regal hinten"}

        self.client.post(self.adresse, formular | {"abschliessen": "1"})

        self.inv.refresh_from_db()
        self.assertEqual(self.inv.status, Inventurstatus.ABGESCHLOSSEN)

    def test_ein_fremder_eintrag_bleibt_auch_nach_einer_eigenen_sicherung_ungesehen(self):
        formular = self.laden()
        vermerk_aendern(self.inv, self.anderer.pk, text="aus der Zähl-App")
        notiz = {f"notiz_{self.artikel.pk}": "Regal hinten", "nebenbei": "1"}

        gesichert = self.client.post(self.adresse, formular | notiz)

        self.assertEqual(gesichert[BEMERKUNGSSTAND_KOPF], formular["bemerkungsstand"])
        antwort = self.client.post(self.adresse, formular | {"abschliessen": "1"})
        self.assertEqual(meldungen(antwort), [str(NEUE_BEMERKUNGEN)])


class DieKlaerung(DifferenzVonHand):
    """With questions the state goes along to their page and is compared there once more."""

    def setUp(self):
        super().setUp()
        self.inv = self.inventur()
        # Ware N where nobody expected it: a question before closing.
        Zaehlposition.objects.create(
            inventur=self.inv,
            ort=self.ort,
            artikel=self.anderer,
            eingabemenge=Decimal("3"),
            unerwartet=True,
        )
        self.pruefliste = reverse("inventur_pruefen", args=[self.inv.pk])
        self.klaerung = reverse("inventur_abschluss", args=[self.inv.pk])

    def test_traegt_den_stand_und_vergleicht_ihn(self):
        seite = self.client.get(self.pruefliste)
        stand, gesehen = seite.context["zaehlstand"], seite.context["bemerkungsstand"]

        antwort = self.client.post(
            self.pruefliste,
            {"abschliessen": "1", "zaehlstand": stand, "bemerkungsstand": gesehen},
        )

        self.assertRedirects(
            antwort, f"{self.klaerung}?{urlencode({'stand': stand, 'bemerkungen': gesehen})}"
        )
        fragen = self.client.get(self.klaerung, {"stand": stand, "bemerkungen": gesehen})
        self.assertContains(fragen, f'name="bemerkungsstand" value="{gesehen}"')

        vermerk_aendern(self.inv, self.artikel.pk, soll_statt_ist=True)
        antwort = self.client.post(self.klaerung, {"zaehlstand": stand, "bemerkungsstand": gesehen})

        self.assertRedirects(antwort, self.pruefliste)
        self.assertEqual(meldungen(antwort), [str(NEUE_BEMERKUNGEN)])
        self.inv.refresh_from_db()
        self.assertEqual(self.inv.status, Inventurstatus.ZAEHLUNG)


class ImLauf(LaufSeiten):
    """ "Close all" carries the remarks' state per house, without the notes' text."""

    def adresse(self, name: str = "inventur_lauf") -> str:
        return reverse(name, kwargs={"stichtag": STICHTAG.isoformat()})

    def gesehen(self) -> dict[str, str]:
        """What the button of the tab "Total" sends."""
        felder = {}
        for nummer, stand, bemerkungen in self.client.get(self.adresse()).context["gesehen"]:
            felder[f"zaehlstand_{nummer}"] = stand
            felder[f"bemerkungsstand_{nummer}"] = bemerkungen
        return felder

    def zwei_gezaehlte(self) -> tuple[Inventur, Inventur]:
        ost, sued = self.lauf()
        for inventur in (ost, sued):
            inventur.zaehlpositionen.update(eingabemenge=Decimal("2"))
        return ost, sued

    def test_der_knopf_traegt_den_stand_jedes_hauses(self):
        ost, sued = self.zwei_gezaehlte()

        seite = self.client.get(self.adresse())

        for inventur in (ost, sued):
            stand = bemerkungsstand(inventur, mit_notizen=False)
            self.assertContains(seite, f'name="bemerkungsstand_{inventur.pk}" value="{stand}"')

    def test_ein_haken_seit_dem_laden_schliesst_nichts_ab(self):
        ost, _sued = self.zwei_gezaehlte()
        gesehen = self.gesehen()
        vermerk_aendern(ost, self.artikel.pk, soll_statt_ist=True)

        antwort = self.client.post(self.adresse("inventur_lauf_abschluss"), gesehen)

        self.assertRedirects(antwort, self.adresse())
        self.assertEqual(
            set(Inventur.objects.values_list("status", flat=True)), {Inventurstatus.ZAEHLUNG}
        )
        self.assertIn(
            "In Kino Ost wurde seit dem Laden der Seite „Soll zählt“ oder eine Differenz von "
            "Hand geändert",
            " ".join(meldungen(antwort)),
        )

    def test_eine_notiz_haelt_ihn_nicht_an(self):
        """The tab "Total" shows no notes: a note written meanwhile is nothing unseen there."""
        ost, _sued = self.zwei_gezaehlte()
        gesehen = self.gesehen()
        vermerk_aendern(ost, self.artikel.pk, text="Regal hinten")

        self.client.post(self.adresse("inventur_lauf_abschluss"), gesehen)

        self.assertEqual(
            set(Inventur.objects.values_list("status", flat=True)), {Inventurstatus.ABGESCHLOSSEN}
        )

    def test_unter_der_sperre_sagt_er_es_genauso(self):
        self.zwei_gezaehlte()
        gesehen = self.gesehen()

        with mock.patch("inventur.closing.decisions.bemerkungsstand_wie", return_value="anders"):
            antwort = self.client.post(self.adresse("inventur_lauf_abschluss"), gesehen)

        self.assertIn("„Soll zählt“ oder eine Differenz von Hand", " ".join(meldungen(antwort)))
        self.assertEqual(
            set(Inventur.objects.values_list("status", flat=True)), {Inventurstatus.ZAEHLUNG}
        )
