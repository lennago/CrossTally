"""The lock on a running count: counting stops, checking and closing go on."""

from decimal import Decimal

from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError
from django.urls import reverse

from .models import Inventurstatus
from .test_run import LaufSeiten
from .test_views import InventurSeiten, gesehen


class SperreAmModell(InventurSeiten):
    def test_nur_in_der_zaehlung(self):
        inventur = self.inventur()
        with self.assertRaises(ValidationError):
            inventur.sperre_setzen(True)

        inventur.zaehlung_starten()
        inventur.sperre_setzen(True)
        inventur.refresh_from_db()
        self.assertTrue(inventur.gesperrt)

    def test_zurueck_in_die_vorbereitung_oeffnet(self):
        """Started again, the count should be open, not closed by a lock nobody sees."""
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)
        inventur.sperre_setzen(True)

        inventur.zurueck_in_vorbereitung()
        inventur.zaehlung_starten()

        inventur.refresh_from_db()
        self.assertFalse(inventur.gesperrt)

    def test_abgeschlossen_wird_auch_gesperrt(self):
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)
        inventur.zaehlpositionen.update(eingabemenge=Decimal("2"))
        inventur.sperre_setzen(True)

        inventur.abschliessen()

        self.assertEqual(inventur.status, Inventurstatus.ABGESCHLOSSEN)
        self.assertEqual(inventur.ergebnisse.get().gesamtmenge_basiseinheit, Decimal("40"))
        with self.assertRaises(ValidationError):
            inventur.sperre_setzen(False)


class SperreAufDerSeite(InventurSeiten):
    def meldungen(self, antwort) -> str:
        return " ".join(str(meldung) for meldung in get_messages(antwort.wsgi_request))

    def test_der_knopf_sperrt_und_oeffnet(self):
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)
        adresse = reverse("inventur_sperre", args=[inventur.pk])
        seite = self.client.get(inventur.get_absolute_url())
        self.assertContains(seite, adresse)
        self.assertContains(seite, "Sperren")

        antwort = self.client.post(adresse, {"an": "1"})

        self.assertRedirects(antwort, inventur.get_absolute_url())
        self.assertIn("ist gesperrt", self.meldungen(antwort))
        inventur.refresh_from_db()
        self.assertTrue(inventur.gesperrt)
        seite = self.client.get(inventur.get_absolute_url())
        self.assertContains(seite, "Öffnen")
        self.assertContains(seite, "Gesperrt")

        self.client.post(adresse, {"an": "0"})
        inventur.refresh_from_db()
        self.assertFalse(inventur.gesperrt)

    def test_kein_knopf_ausserhalb_der_zaehlung(self):
        inventur = self.inventur()
        adresse = reverse("inventur_sperre", args=[inventur.pk])
        self.assertNotContains(self.client.get(inventur.get_absolute_url()), adresse)

        antwort = self.client.post(adresse, {"an": "1"})

        self.assertIn("nur eine laufende Zählung", self.meldungen(antwort))
        inventur.refresh_from_db()
        self.assertFalse(inventur.gesperrt)

    def test_die_liste_zeigt_das_schloss(self):
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)
        self.assertNotContains(self.client.get(reverse("inventur_list")), "Gesperrt")

        inventur.sperre_setzen(True)

        self.assertContains(self.client.get(reverse("inventur_list")), "Gesperrt")

    def test_abschliessen_geht_auch_gesperrt(self):
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)
        inventur.zaehlpositionen.update(eingabemenge=Decimal("2"))
        inventur.sperre_setzen(True)

        self.client.post(
            reverse("inventur_pruefen", args=[inventur.pk]),
            {"abschliessen": "1", **gesehen(inventur)},
        )

        inventur.refresh_from_db()
        self.assertEqual(inventur.status, Inventurstatus.ABGESCHLOSSEN)


class SperreImLauf(LaufSeiten):
    def test_der_lauf_schliesst_gesperrte_und_offene_ab(self):
        ost, sued = self.lauf()
        self.zaehlen(ost)
        self.zaehlen(sued)
        ost.sperre_setzen(True)

        self.alle_abschliessen()

        for inventur in (ost, sued):
            inventur.refresh_from_db()
            self.assertEqual(inventur.status, Inventurstatus.ABGESCHLOSSEN)
