"""An old remark on an article in "Not countable" neither shows in the check list nor freezes.

Since 10/2026 a note, "Target counts" or a difference by hand is refused for
such an article ('services.notes._ohne_vermerk'), and moving a product there
deletes the remarks of its articles in running inventories
('katalog.hausartikel_status.aufraeumen'). One written before that fix, on an
article that is ignored now, still lay there: the check list showed a row for
it, and the closing froze one, for something that is not a product. Check
list and closing skip it alike, so the page still shows what the closing
freezes. Where the article was counted, its row stands as before. A remark
outside the inventory's types stays: it may carry information, the check list
shows it, and it can be emptied there.
"""

from decimal import Decimal

from django.test import Client
from django.urls import reverse

from api.test_review_list import PruefGrundlagen
from django_project.testing import fuehrungskraft, jahresinventur
from katalog.models import Artikelstatus, Hausartikel, Ware

from .closing import Entscheidungen, NeueBemerkungen, abschliessen
from .models import Inventur, Inventurnotiz, Sollbestand, Zaehlposition
from .services import Ergebniszeile, ergebniszeilen


class Grundlage(PruefGrundlagen):
    """Ware P went into "Not countable". Its old remark lies on it, stored past the services."""

    def setUp(self):
        pfand = Ware.objects.create(name="Ware P", basiseinheit=self.stueck)
        self.ignoriert = Hausartikel.objects.create(
            haus=self.haus,
            ware=pfand,
            warengruppe=self.gruppe,
            artikelnummer="101005",
            name="Ware P",
        )
        Zaehlposition.objects.create(inventur=self.inventur, ort=self.theke, artikel=self.ignoriert)
        pfand.ignorieren()
        self.ignoriert.refresh_from_db()
        self.assertEqual(self.ignoriert.status, Artikelstatus.IGNORIERT)
        Inventurnotiz.objects.create(
            inventur=self.inventur,
            hausartikel=self.ignoriert,
            text="Rest im Lager",
            soll_statt_ist=True,
        )

    def pruefzeilen(self) -> dict[int, Ergebniszeile]:
        """The rows of the check list, as the website and the app get them."""
        return {zeile.hausartikel.pk: zeile for zeile in ergebniszeilen(self.inventur)}

    def pruefliste(self):
        client = Client()
        client.force_login(fuehrungskraft())
        return client.get(reverse("inventur_pruefen", args=[self.inventur.pk]))

    def schliessen(self, seite=None) -> dict[int, str]:
        """Closes with the states the page carried. Back come the frozen rows and their notes."""
        seite = seite or self.pruefliste()
        abschliessen(
            Inventur.objects.get(pk=self.inventur.pk),
            Entscheidungen(),
            gesehen=seite.context["zaehlstand"],
            bemerkungen=seite.context["bemerkungsstand"],
        )
        return dict(self.inventur.ergebnisse.values_list("hausartikel_id", "notiz"))

    def zaehlen(self, artikel: Hausartikel, menge: str) -> None:
        Zaehlposition.objects.create(
            inventur=self.inventur, ort=self.theke, artikel=artikel, eingabemenge=Decimal(menge)
        )


class NurEinVermerk(Grundlage):
    def test_die_pruefliste_zeigt_ihn_nicht(self):
        self.assertNotIn(self.ignoriert.pk, self.pruefzeilen())

    def test_zurueck_aus_nicht_zaehlbar_haelt_den_abschluss_an(self):
        """The page showed no row for it. Brought back in between, nothing freezes unseen."""
        seite = self.pruefliste()
        self.assertNotContains(seite, "Rest im Lager")
        Ware.objects.get(pk=self.ignoriert.ware_id).wieder_aufnehmen()
        self.assertIn(self.ignoriert.pk, self.pruefzeilen())

        with self.assertRaises(NeueBemerkungen):
            self.schliessen(seite)
        self.assertFalse(self.inventur.ergebnisse.exists())

    def test_die_app_auch_nicht(self):
        self.anmelden()

        self.assertNotIn(self.ignoriert.pk, self.zeilen())

    def test_der_abschluss_friert_ihn_nicht_ein(self):
        self.assertNotIn(self.ignoriert.pk, self.schliessen())

    def test_eine_differenz_von_hand_ebenso(self):
        self.inventur.differenz_von_hand_setzen(True)
        Inventurnotiz.objects.filter(hausartikel=self.ignoriert).update(
            text="", soll_statt_ist=False, differenz=Decimal("-3")
        )

        self.assertNotIn(self.ignoriert.pk, self.pruefzeilen())
        self.assertNotIn(self.ignoriert.pk, self.schliessen())


class AndereZeilen(Grundlage):
    def test_ein_gezaehlter_artikel_bleibt_wie_er_war(self):
        self.zaehlen(self.cola, "2")
        Inventurnotiz.objects.create(
            inventur=self.inventur, hausartikel=self.cola, text="zwei Kästen hinten"
        )

        self.assertEqual(self.pruefzeilen()[self.cola.pk].notiz, "zwei Kästen hinten")
        self.assertEqual(self.schliessen()[self.cola.pk], "zwei Kästen hinten")

    def test_einer_mit_soll_ebenso(self):
        Sollbestand.objects.create(
            inventur=self.inventur, hausartikel=self.cola, menge_basiseinheit=Decimal("30")
        )
        Inventurnotiz.objects.create(inventur=self.inventur, hausartikel=self.cola, text="Bruch")

        self.assertEqual(self.pruefzeilen()[self.cola.pk].notiz, "Bruch")
        self.assertEqual(self.schliessen()[self.cola.pk], "Bruch")

    def test_gezaehlt_behaelt_auch_ein_nicht_zaehlbarer_seine_zeile(self):
        """Past the services: moving the product refuses as long as something is counted."""
        self.inventur.zaehlpositionen.filter(artikel=self.ignoriert).update(
            aktiv=True, eingabemenge=Decimal("4")
        )

        self.assertEqual(self.pruefzeilen()[self.ignoriert.pk].notiz, "Rest im Lager")
        self.assertEqual(self.schliessen()[self.ignoriert.pk], "Rest im Lager")

    def test_ausserhalb_der_arten_bleibt_der_vermerk(self):
        """Not counted in this inventory, but no product of "Not countable": it stays."""
        jahr = Hausartikel.objects.create(
            haus=self.haus,
            ware=Ware.objects.create(name="Ware J", basiseinheit=self.stueck),
            warengruppe=self.gruppe,
            artikelnummer="101006",
            name="Ware J",
            inventurart=jahresinventur(),
        )
        Inventurnotiz.objects.create(inventur=self.inventur, hausartikel=jahr, text="steht hinten")

        self.assertEqual(self.pruefzeilen()[jahr.pk].notiz, "steht hinten")
        self.assertEqual(self.schliessen()[jahr.pk], "steht hinten")


class SeiteUndAbschluss(Grundlage):
    def test_der_abschluss_friert_ein_was_die_seite_zeigt(self):
        """With the page's count and remarks' state the closing goes through, row for row."""
        self.zaehlen(self.cola, "2")
        Inventurnotiz.objects.create(inventur=self.inventur, hausartikel=self.cola, text="Bruch")
        seite = self.pruefliste()
        gezeigt = {
            zeile.hausartikel.pk: zeile.notiz
            for zeile in seite.context["alle"]
            if zeile.gezaehlt or zeile.sollbestand is not None or zeile.notiz
        }

        eingefroren = self.schliessen(seite)

        self.assertEqual(eingefroren, gezeigt)
        self.assertEqual(eingefroren, {self.cola.pk: "Bruch"})
