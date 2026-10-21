"""Closing goes only with both states the page saw: the count state and the remarks' state.

Every page that leads there carries them: the check list in its form, the
way to the questions as '?stand=' and '?bemerkungen=', the page of the
questions in its form, after a refused answer too, and "Close all" per house.
Until 10/2026 a request without them closed without comparing anything. The
page of the questions reached without its query froze whatever had arrived
meanwhile, the check list without its remarks' state froze remarks nobody had
seen, and so did a run whose session carried only the count state. Pages from
before the states existed are long gone. A request without them now goes back
to look again.
"""

from urllib.parse import urlencode

from django.contrib.messages import get_messages
from django.urls import reverse

from .closing import WECHSEL_PRAEFIX
from .models import Inventur, Inventurstatus
from .services import bemerkungsstand, zaehlstand
from .test_closing import Klaerung
from .test_run import LaufSeiten
from .test_views import STICHTAG, gesehen
from .views.review import OHNE_STAND


def meldungen(antwort) -> list[str]:
    return [str(meldung) for meldung in get_messages(antwort.wsgi_request)]


class DiePruefliste(Klaerung):
    """Everything counted, nothing to ask: the check list closes by itself."""

    def setUp(self):
        super().setUp()
        self.zaehlen()
        self.adresse = reverse("inventur_pruefen", args=[self.inv.pk])

    def test_ohne_einen_der_staende_schliesst_sie_nicht_ab(self):
        """The note in the form is saved all the same, as with a stale state."""
        for fehlt in ("zaehlstand", "bemerkungsstand"):
            with self.subTest(fehlt=fehlt):
                formular = gesehen(self.inv) | {
                    "abschliessen": "1",
                    f"notiz_{self.artikel.pk}": f"ohne {fehlt}",
                }
                del formular[fehlt]

                antwort = self.client.post(self.adresse, formular)

                self.assertRedirects(antwort, self.adresse)
                self.assertEqual(meldungen(antwort), [str(OHNE_STAND)])
                self.inv.refresh_from_db()
                self.assertEqual(self.inv.status, Inventurstatus.ZAEHLUNG)
                self.assertEqual(self.inv.notizen.get().text, f"ohne {fehlt}")

    def test_mit_beiden_schliesst_sie_ab(self):
        antwort = self.client.post(self.adresse, gesehen(self.inv) | {"abschliessen": "1"})

        self.assertRedirects(antwort, self.inv.get_absolute_url())
        self.inv.refresh_from_db()
        self.assertEqual(self.inv.status, Inventurstatus.ABGESCHLOSSEN)


class DieFragen(Klaerung):
    """Artikel C stays uncounted: that is a question, and closing goes through its page."""

    def setUp(self):
        super().setUp()
        self.pruefliste = reverse("inventur_pruefen", args=[self.inv.pk])
        self.fragen = reverse("inventur_abschluss", args=[self.inv.pk])

    def abfrage(self) -> dict[str, str]:
        return {"stand": zaehlstand(self.inv), "bemerkungen": bemerkungsstand(self.inv)}

    def test_der_weg_dorthin_traegt_beide(self):
        antwort = self.client.post(self.pruefliste, gesehen(self.inv) | {"abschliessen": "1"})

        self.assertRedirects(antwort, f"{self.fragen}?{urlencode(self.abfrage())}")
        seite = self.client.get(self.fragen, self.abfrage())
        for name, wert in gesehen(self.inv).items():
            self.assertContains(seite, f'name="{name}" value="{wert}"')

    def test_ohne_einen_der_staende_schliessen_sie_nicht_ab(self):
        """Reached without the query, the page has nothing to compare and sends back."""
        for fehlt in (("zaehlstand",), ("bemerkungsstand",), ("zaehlstand", "bemerkungsstand")):
            with self.subTest(fehlt=fehlt):
                formular = gesehen(self.inv)
                for name in fehlt:
                    del formular[name]

                antwort = self.client.post(self.fragen, formular)

                self.assertRedirects(antwort, self.pruefliste)
                self.assertEqual(meldungen(antwort), [str(OHNE_STAND)])
                self.inv.refresh_from_db()
                self.assertEqual(self.inv.status, Inventurstatus.ZAEHLUNG)

    def test_eine_abgelehnte_antwort_behaelt_beide(self):
        """The page comes back with both, and its form sends to its own address."""
        adresse = f"{self.fragen}?{urlencode(self.abfrage())}"
        falsch = {f"ungezaehlt_{self.artikel.pk}": f"{WECHSEL_PRAEFIX}0"}

        antwort = self.client.post(adresse, gesehen(self.inv) | falsch)

        self.assertContains(antwort, "Abgeschlossen wurde nichts")
        self.assertContains(antwort, '<form method="post">')
        for name, wert in gesehen(self.inv).items():
            self.assertContains(antwort, f'name="{name}" value="{wert}"')
        antwort = self.client.post(adresse, gesehen(self.inv))
        self.assertRedirects(antwort, self.inv.get_absolute_url())
        self.inv.refresh_from_db()
        self.assertEqual(self.inv.status, Inventurstatus.ABGESCHLOSSEN)


class DerLauf(LaufSeiten):
    """ "Close all" carries both per house, and so does its way to a house with questions."""

    def test_ohne_den_stand_der_bemerkungen_schliesst_er_nichts_ab(self):
        ost, sued = self.lauf()
        self.zaehlen(ost)
        self.zaehlen(sued)
        nur_zaehlstand = {
            f"zaehlstand_{inventur.pk}": zaehlstand(inventur) for inventur in (ost, sued)
        }

        antwort = self.alle_abschliessen(nur_zaehlstand)

        self.assertRedirects(antwort, self.adresse())
        self.assertEqual(
            set(Inventur.objects.values_list("status", flat=True)), {Inventurstatus.ZAEHLUNG}
        )
        self.assertEqual(
            self.meldungen(antwort),
            [
                "Nicht abgeschlossen: Für Kino Ost, Kino Süd fehlt, welche Werte du gesehen hast. "
                "Bitte prüfen und dann noch einmal „Alle abschließen“."
            ],
        )

    def test_der_weg_zu_den_fragen_traegt_beide(self):
        ost, sued = self.lauf()
        self.zaehlen(sued)  # Ost stays uncounted: that is a question.

        antwort = self.alle_abschliessen()

        abfrage = {
            "lauf": 1,
            "stand": zaehlstand(ost),
            "bemerkungen": bemerkungsstand(ost, mit_notizen=False),
        }
        ziel = reverse("inventur_abschluss", args=[ost.pk])
        self.assertRedirects(antwort, f"{ziel}?{urlencode(abfrage)}")

    def test_eine_sitzung_mit_nur_dem_zaehlstand_schliesst_kein_weiteres_haus_ab(self):
        """Pressed before the remarks' state existed: what Süd showed of them is not known."""
        ost, sued = self.lauf()  # Ost stays uncounted: that is a question.
        self.zaehlen(sued)
        sitzung = self.client.session
        sitzung[f"lauf_gesehen:{STICHTAG.isoformat()}"] = {
            str(ost.pk): zaehlstand(ost),
            str(sued.pk): zaehlstand(sued),
        }
        sitzung.save()

        antwort = self.client.post(self.zwischenseite(ost), gesehen(ost))

        self.assertRedirects(antwort, self.adresse())
        ost.refresh_from_db()
        sued.refresh_from_db()
        self.assertEqual(
            (ost.status, sued.status), (Inventurstatus.ABGESCHLOSSEN, Inventurstatus.ZAEHLUNG)
        )
        self.assertIn(
            "Für Kino Süd fehlt, welche Werte du gesehen hast.", self.meldungen(antwort)[-1]
        )
