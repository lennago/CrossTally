"""Into "Not countable" and back: a wrong click undone leaves every row as it was.

A product goes there to stay. It comes back only when someone clicked wrong
(decision of 2026-10-14). Until then the way in made every row ignored and
the way back made every row active, and a discontinued old number of the
product counted as the new one from then on: its target was added silently
at the next read-in instead of being asked about.
"""

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from django_project.testing import AngemeldeteSeiten
from katalog.models import Artikelstatus, Basiseinheit, Hausartikel, Ware, Warengruppe
from katalog.ware_merge import zusammenfuehren
from organisation.models import Haus


class Rundreise(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.nord = Haus.objects.create(name="Kino Nord")
        cls.ost = Haus.objects.create(name="Kino Ost")
        cls.gruppe = Warengruppe.objects.create(name="Warengruppe G")
        cls.stueck = Basiseinheit.objects.get(name="Stück(e)")

    def setUp(self):
        self.ware = Ware.objects.create(name="Ware Z", basiseinheit=self.stueck)
        self.alt = self.hausartikel(self.nord, "101001", Artikelstatus.EINGESTELLT)
        Hausartikel.objects.filter(pk=self.alt.pk).update(
            gueltig_bis=timezone.now() - timedelta(days=90)
        )
        self.alt.refresh_from_db()
        self.neu = self.hausartikel(self.nord, "101002")
        self.saison = self.hausartikel(self.ost, "102002", Artikelstatus.INAKTIV)

    def hausartikel(self, haus, nummer, status=Artikelstatus.AKTIV) -> Hausartikel:
        return Hausartikel.objects.create(
            haus=haus,
            ware=self.ware,
            warengruppe=self.gruppe,
            artikelnummer=nummer,
            name=f"Ware Z {nummer}",
            status=status,
        )

    def stand(self) -> dict[str, tuple[str, str, int | None]]:
        return {
            satz.artikelnummer: (satz.status, satz.status_vorher, satz.zaehlt_als_id)
            for satz in self.ware.hausartikel.order_by("artikelnummer")
        }

    def test_hinein_merkt_sich_jede_zeile_was_sie_war(self):
        self.ware.ignorieren()

        self.assertEqual(
            self.stand(),
            {
                "101001": (Artikelstatus.IGNORIERT, Artikelstatus.EINGESTELLT, None),
                "101002": (Artikelstatus.IGNORIERT, Artikelstatus.AKTIV, None),
                "102002": (Artikelstatus.IGNORIERT, Artikelstatus.INAKTIV, None),
            },
        )
        self.neu.refresh_from_db()
        self.assertIsNotNone(self.neu.gueltig_bis)

    def test_zurueck_stellt_jeden_status_wieder_her(self):
        """The old number stays discontinued and counts as nothing: no silent adding later."""
        datum_alt = self.alt.gueltig_bis
        self.ware.ignorieren()

        self.ware.wieder_aufnehmen()

        self.assertEqual(
            self.stand(),
            {
                "101001": (Artikelstatus.EINGESTELLT, "", None),
                "101002": (Artikelstatus.AKTIV, "", None),
                "102002": (Artikelstatus.INAKTIV, "", None),
            },
        )
        self.alt.refresh_from_db()
        self.neu.refresh_from_db()
        self.assertEqual(self.alt.gueltig_bis, datum_alt)
        self.assertIsNone(self.neu.gueltig_bis)

    def test_eine_neu_vergebene_alte_nummer_sperrt_den_rueckweg_nicht(self):
        """The old number belongs to another product by now. The old row comes back discontinued.

        It needs neither number nor product. Until 10/2026 the whole way back failed on it.
        """
        andere = Ware.objects.create(name="Ware Q", basiseinheit=self.stueck)
        Hausartikel.objects.create(
            haus=self.nord,
            ware=andere,
            warengruppe=self.gruppe,
            artikelnummer="101001",
            name="Ware Q",
        )
        self.ware.ignorieren()

        self.ware.wieder_aufnehmen()

        self.assertEqual(self.stand()["101001"], (Artikelstatus.EINGESTELLT, "", None))
        self.assertEqual(self.stand()["101002"], (Artikelstatus.AKTIV, "", None))

    def test_auch_ohne_gedaechtnis_sperrt_die_nummer_einer_dublette_nicht(self):
        """The old row comes back counting as the young one, so its number may be taken.

        Until 10/2026 every row without a memory had to find its number free,
        also one that comes back discontinued.
        """
        self.ware.ignorieren()
        Hausartikel.objects.filter(ware=self.ware).update(status_vorher="")
        self.fremd("101001")

        self.ware.wieder_aufnehmen()

        stand = self.stand()
        self.assertEqual(stand["101001"], (Artikelstatus.EINGESTELLT, "", self.neu.pk))
        self.assertEqual(stand["101002"], (Artikelstatus.AKTIV, "", None))

    def test_die_zeile_die_gefuehrt_zurueckkaeme_braucht_ihre_nummer_weiter(self):
        self.ware.ignorieren()
        Hausartikel.objects.filter(ware=self.ware).update(status_vorher="")
        self.fremd("101002")

        with self.assertRaisesMessage(ValidationError, "101002 „Ware Z 101002“ käme nicht"):
            self.ware.wieder_aufnehmen()

    def fremd(self, nummer: str) -> Hausartikel:
        """A carried article of another product that took the number in Kino Nord."""
        return Hausartikel.objects.create(
            haus=self.nord,
            ware=Ware.objects.create(name="Ware Q", basiseinheit=self.stueck),
            warengruppe=self.gruppe,
            artikelnummer=nummer,
            name="Ware Q",
        )

    def test_eine_markierte_dublette_behaelt_ihre_marke(self):
        Hausartikel.objects.filter(pk=self.alt.pk).update(zaehlt_als=self.neu)
        self.ware.ignorieren()

        self.ware.wieder_aufnehmen()

        self.assertEqual(self.stand()["101001"], (Artikelstatus.EINGESTELLT, "", self.neu.pk))

    def test_ohne_gedaechtnis_entscheidet_weiter_das_datum(self):
        """Rows ignored from the start, or from before 10/2026, know nothing. The rule stays."""
        self.ware.ignorieren()
        Hausartikel.objects.filter(ware=self.ware).update(status_vorher="")

        self.ware.wieder_aufnehmen()

        stand = self.stand()
        # The row that left the carried state last was the one carried before.
        self.assertEqual(stand["101002"], (Artikelstatus.AKTIV, "", None))
        self.assertEqual(stand["101001"], (Artikelstatus.EINGESTELLT, "", self.neu.pk))
        self.assertEqual(stand["102002"], (Artikelstatus.AKTIV, "", None))

    def test_eine_zeile_ohne_gedaechtnis_zaehlt_als_die_mit(self):
        """Only one carried row per house: the one that remembers being carried wins."""
        self.ware.ignorieren()
        Hausartikel.objects.filter(pk=self.alt.pk).update(status_vorher="")

        self.ware.wieder_aufnehmen()

        stand = self.stand()
        self.assertEqual(stand["101002"], (Artikelstatus.AKTIV, "", None))
        self.assertEqual(stand["101001"], (Artikelstatus.EINGESTELLT, "", self.neu.pk))

    def test_auch_eine_inaktive_zeile_mit_gedaechtnis_gilt_als_gefuehrt(self):
        fremd = self.hausartikel(self.ost, "102009", Artikelstatus.EINGESTELLT)
        self.ware.ignorieren()
        Hausartikel.objects.filter(pk=fremd.pk).update(status_vorher="")

        self.ware.wieder_aufnehmen()

        stand = self.stand()
        self.assertEqual(stand["102002"], (Artikelstatus.INAKTIV, "", None))
        self.assertEqual(stand["102009"], (Artikelstatus.EINGESTELLT, "", self.saison.pk))

    def test_eine_von_anfang_an_ignorierte_zeile_hat_kein_gedaechtnis(self):
        self.ware.ignorieren()
        pfand = Hausartikel.objects.create(
            haus=self.ost,
            ware=self.ware,
            warengruppe=self.gruppe,
            artikelnummer="102010",
            name="Ware Z 102010",
            status=Artikelstatus.IGNORIERT,
        )
        pfand.refresh_from_db()
        self.assertEqual((pfand.status, pfand.status_vorher), (Artikelstatus.IGNORIERT, ""))


class ZweiWarenMitGedaechtnis(AngemeldeteSeiten):
    """Two products in "Not countable", each remembering an active row in Kino Nord.

    Merged, the house holds two rows of one product that both want to come
    back active. Until 10/2026 the unique index of the house refused that:
    the way back ended on the error page, and there was no other.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.nord = Haus.objects.create(name="Kino Nord")
        cls.gruppe = Warengruppe.objects.create(name="Warengruppe G")
        cls.stueck = Basiseinheit.objects.get(name="Stück(e)")

    def setUp(self):
        super().setUp()
        self.ziel = Ware.objects.create(name="Ware P", basiseinheit=self.stueck)
        self.quelle = Ware.objects.create(name="Ware Q", basiseinheit=self.stueck)
        self.bleibt = self.hausartikel(self.ziel, "101001")
        self.geht = self.hausartikel(self.quelle, "101002")
        self.ziel.ignorieren()
        self.quelle.ignorieren()

    def hausartikel(self, ware, nummer) -> Hausartikel:
        return Hausartikel.objects.create(
            haus=self.nord,
            ware=ware,
            warengruppe=self.gruppe,
            artikelnummer=nummer,
            name=f"{ware.name} {nummer}",
        )

    def stand(self) -> dict[str, tuple[str, int | None]]:
        return {
            satz.artikelnummer: (satz.status, satz.zaehlt_als_id)
            for satz in Hausartikel.objects.filter(ware=self.ziel).order_by("artikelnummer")
        }

    def test_nach_dem_zusammenfuehren_kommt_die_bleibende_zeile_aktiv_zurueck(self):
        zusammenfuehren(self.quelle, self.ziel)

        Ware.objects.get(pk=self.ziel.pk).wieder_aufnehmen()

        self.assertEqual(
            self.stand(),
            {
                "101001": (Artikelstatus.AKTIV, None),
                "101002": (Artikelstatus.EINGESTELLT, self.bleibt.pk),
            },
        )

    def test_ueber_die_seite_ohne_serverfehler(self):
        zusammenfuehren(self.quelle, self.ziel)

        antwort = self.client.post(
            reverse("ware_status", args=[self.ziel.pk]), {"status": "WIEDER_AUFNEHMEN"}
        )

        self.assertRedirects(antwort, self.ziel.get_absolute_url(), fetch_redirect_response=False)
        self.assertEqual(self.stand()["101001"], (Artikelstatus.AKTIV, None))

    def test_auch_ein_wechsel_der_ware_bringt_nur_eine_zeile_gefuehrt_zurueck(self):
        """A row that changed its product while in "Not countable": the way back sorts it out."""
        artikel = Hausartikel.objects.get(pk=self.geht.pk)
        artikel.ware = self.ziel
        artikel.save()

        Ware.objects.get(pk=self.ziel.pk).wieder_aufnehmen()

        aktiv = Hausartikel.objects.get(ware=self.ziel, status=Artikelstatus.AKTIV)
        eingestellt = Hausartikel.objects.get(ware=self.ziel, status=Artikelstatus.EINGESTELLT)
        self.assertEqual(eingestellt.zaehlt_als, aktiv)
