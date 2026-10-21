"""Saving and restoring through Django's fixtures.

The round trip is the actual test: dump, delete everything, restore and
check that the state is the same again. It covers all tables and their
references in one pass.
"""

import io
import json
import pathlib
import tempfile
from decimal import Decimal

from django.core.management import call_command
from django.test import TestCase

from accounts.models import CustomUser
from django_project.testing import bereich
from organisation.models import Bereich, Haus, Ort

from .models import (
    Basiseinheit,
    Behaelter,
    Dimension,
    Einheitenkennung,
    Erwartung,
    ErwartungGebinde,
    Gebinde,
    Hausartikel,
    Inventurart,
    Rezepturartikel,
    Rezepturkomponente,
    Ware,
    Warengruppe,
)


def stand_aufbauen():
    """A small but complete data set: every table occurs."""
    stueck = Basiseinheit.objects.get(dimension=Dimension.STUECK)
    kg = Basiseinheit.objects.filter(dimension=Dimension.MASSE).first()

    kino_nord = Haus.objects.create(name="Kino Nord")
    kino_sued = Haus.objects.create(name="Kino Süd")
    theke = Ort.objects.create(name="Theke", bereich=bereich(kino_nord))
    Ort.objects.create(name="Lager", bereich=bereich(kino_nord))

    getraenke = Warengruppe.objects.create(name="Warengruppe G")
    snacks = Warengruppe.objects.create(name="Warengruppe S")

    cola = Ware.objects.create(name="Artikel C 0,33", basiseinheit=stueck)
    nachos = Ware.objects.create(name="Artikel N", basiseinheit=kg)
    woche = Inventurart.objects.create(name="Wochenzählung Bar")
    Gebinde.objects.create(ware=cola, name="Kasten", faktor_in_basiseinheit=24)
    Gebinde.objects.create(ware=cola, name="Flasche(n)", faktor_in_basiseinheit=1)
    Gebinde.objects.create(ware=nachos, name="Karton", faktor_in_basiseinheit=5)
    nachos.abwiegbar = True
    nachos.save(update_fields=["abwiegbar"])
    nachos.behaelter.set([Behaelter.objects.create(name="Schale", tara=250)])

    grund = Hausartikel.objects.create(
        haus=kino_nord,
        ware=cola,
        warengruppe=getraenke,
        artikelnummer="101001",
        name="Artikel C 0,33 Fl",
        ean="4001234567890",
    )
    Hausartikel.objects.create(
        haus=kino_sued,
        ware=cola,
        warengruppe=getraenke,
        artikelnummer="200001",
        name="Artikel C Kino Süd",
    )
    alt = Hausartikel.objects.create(
        haus=kino_nord,
        ware=nachos,
        warengruppe=snacks,
        artikelnummer="101009",
        name="Artikel N alt",
        inventurart=woche,
    )
    alt.deaktivieren()

    portion = Rezepturartikel.objects.create(name="Portion N", basiseinheit=stueck)
    Rezepturkomponente.objects.create(
        rezepturartikel=portion, ware=nachos, menge_in_basiseinheit=Decimal("0.14")
    )

    erwartung = Erwartung.objects.create(ort=theke, artikel=grund)
    Erwartung.objects.create(ort=theke, artikel=portion)
    ErwartungGebinde.objects.create(
        erwartung=erwartung,
        gebinde=Gebinde.objects.get(ware=cola, name="Kasten"),
        standard_menge=Decimal("2"),
    )


def abbild() -> dict:
    """The state as a comparable collection, independent of primary keys."""
    return {
        "haeuser": sorted(Haus.objects.values_list("name", flat=True)),
        "bereiche": sorted(Bereich.objects.values_list("haus__name", "name")),
        "orte": sorted(Ort.objects.values_list("bereich__haus__name", "bereich__name", "name")),
        "warengruppen": sorted(Warengruppe.objects.values_list("name", flat=True)),
        "ware": sorted(Ware.objects.values_list("name", "basiseinheit__name")),
        "gebinde": sorted(
            Gebinde.objects.values_list(
                "ware__name", "name", "faktor_in_basiseinheit", "sortierung", "aktiv"
            )
        ),
        "abwiegbar": sorted(Ware.objects.filter(abwiegbar=True).values_list("name", flat=True)),
        "behaelter": sorted(Ware.objects.values_list("name", "behaelter__name")),
        "hausartikel": sorted(
            Hausartikel.objects.values_list(
                "haus__name",
                "artikelnummer",
                "name",
                "ware__name",
                "warengruppe__name",
                "inventurart__name",
                "ean",
                "aktiv",
            )
        ),
        "rezepturartikel": sorted(
            Rezepturartikel.objects.values_list("name", "basiseinheit__name")
        ),
        "rezepturen": sorted(
            Rezepturkomponente.objects.values_list(
                "rezepturartikel__name", "ware__name", "menge_in_basiseinheit"
            )
        ),
        "erwartungen": sorted(
            Erwartung.objects.values_list(
                "ort__bereich__haus__name", "ort__name", "artikel__name", "sortierung"
            )
        ),
        "vorgaben": sorted(
            ErwartungGebinde.objects.values_list(
                "erwartung__ort__name", "gebinde__name", "standard_menge"
            )
        ),
        "inventurarten": sorted(Inventurart.objects.values_list("name", "reihenfolge")),
    }


def alles_loeschen():
    """Everything except the master data from the migration, the monthly inventory included."""
    ErwartungGebinde.objects.all().delete()
    Erwartung.objects.all().delete()
    Rezepturkomponente.objects.all().delete()
    Rezepturartikel.objects.all().delete()
    Hausartikel.objects.all().delete()
    # After the house articles: they hold their type ('PROTECT').
    Inventurart.objects.exclude(pk=Inventurart.erste().pk).delete()
    Gebinde.objects.all().delete()
    Ware.objects.all().delete()
    Warengruppe.objects.all().delete()
    Ort.objects.all().delete()
    Haus.objects.all().delete()


class DumpdataRundlauf(TestCase):
    """The path through Django's own fixtures, for backup and setting up anew.

    The natural keys on 'Basiseinheit' and 'Behaelter' are the reason this
    still works even when the master data from the data migration carries
    other numbers in the target database.
    """

    BEFEHL = dict(natural_foreign=True, natural_primary=True, indent=2)

    def setUp(self):
        stand_aufbauen()

    def dump(self, *apps) -> str:
        puffer = io.StringIO()
        call_command("dumpdata", *apps, stdout=puffer, **self.BEFEHL)
        return puffer.getvalue()

    def laden(self, text: str) -> None:
        ordner = tempfile.TemporaryDirectory()
        self.addCleanup(ordner.cleanup)
        pfad = pathlib.Path(ordner.name) / "stand.json"
        pfad.write_text(text, encoding="utf-8")
        call_command("loaddata", str(pfad), verbosity=0)

    def test_stand_kommt_vollstaendig_zurueck(self):
        vorher = abbild()
        text = self.dump("katalog", "organisation")

        alles_loeschen()
        self.laden(text)
        self.assertEqual(abbild(), vorher)

    def test_auch_bei_abweichenden_stammdaten_nummern(self):
        """The case where the path without natural keys fails."""
        vorher = abbild()
        text = self.dump("katalog", "organisation")

        alles_loeschen()
        Basiseinheit.objects.all().delete()
        Behaelter.objects.all().delete()
        Basiseinheit.objects.create(
            name="Stück(e)", kennung=Einheitenkennung.STUECK, dimension=Dimension.STUECK
        )
        Basiseinheit.objects.create(
            name="kg",
            kennung=Einheitenkennung.KILOGRAMM,
            dimension=Dimension.MASSE,
            faktor_zur_referenz=1000,
        )
        Behaelter.objects.create(name="Ohne Behälter (Waage genullt)", tara=0)

        self.laden(text)
        self.assertEqual(abbild(), vorher)

    def test_verweise_stehen_als_name_in_der_datei(self):
        """Instead of "basiseinheit: 1" there stands "basiseinheit: [Stück(e)]"."""
        saetze = json.loads(self.dump("katalog"))
        cola = next(
            satz
            for satz in saetze
            if satz["model"] == "katalog.ware" and satz["fields"]["name"] == "Artikel C 0,33"
        )
        self.assertEqual(cola["fields"]["basiseinheit"], ["Stück(e)"])

        einheit = next(satz for satz in saetze if satz["model"] == "katalog.basiseinheit")
        self.assertIsNone(einheit.get("pk"), "Die Nummer gehört nicht in die Datei")

    def test_ein_backup_ohne_kennung_laesst_sich_einspielen(self):
        """The case that almost slipped through during the change (09/2026).

        A state from before the id carries the base unit only with its name.
        Without rework all five would get an empty id, and the second would run
        into the unique index: "UNIQUE constraint failed". Such a backup would
        thus only be archive material.
        """
        saetze = json.loads(self.dump("katalog", "organisation"))
        for satz in saetze:
            if satz["model"] == "katalog.basiseinheit":
                del satz["fields"]["kennung"]

        alles_loeschen()
        Basiseinheit.objects.all().delete()
        self.laden(json.dumps(saetze))

        self.assertEqual(
            dict(Basiseinheit.objects.values_list("name", "kennung")),
            {
                "Stück(e)": "STUECK",
                "g": "GRAMM",
                "kg": "KILOGRAMM",
                "Milliliter": "MILLILITER",
                "Liter": "LITER",
                "Portion": "PORTION",
            },
        )

    def test_ein_backup_ohne_hauseinheit_laesst_sich_einspielen(self):
        """A state from before 09/2026 does not know the house's unit.

        It was added as a required field. Without rework every older state
        would run into "NOT NULL constraint failed". The 'pre_save' receiver
        inserts the product's unit, because exactly so the row counted until then.
        """
        saetze = json.loads(self.dump("katalog", "organisation"))
        for satz in saetze:
            if satz["model"] == "katalog.hausartikel":
                del satz["fields"]["basiseinheit"]
                del satz["fields"]["faktor_zur_ware"]

        alles_loeschen()
        self.laden(json.dumps(saetze))

        for artikel in Hausartikel.objects.select_related("basiseinheit", "ware"):
            self.assertEqual(artikel.basiseinheit_id, artikel.ware.basiseinheit_id)
            self.assertIsNone(artikel.faktor_zur_ware)

    def test_ein_backup_ohne_inventurart_laesst_sich_einspielen(self):
        """A state from before 10/2026 knows no type at the house article.

        It became a required field. What comes without one gets the type the
        product's other articles agree on, otherwise the first. The scope of
        the old state is rewritten by 'django_project/backup_upgrade.py'
        before loading, here only the fallback is at stake.
        """
        saetze = json.loads(self.dump("katalog", "organisation"))
        for satz in saetze:
            if satz["model"] == "katalog.hausartikel":
                del satz["fields"]["inventurart"]

        alles_loeschen()
        self.laden(json.dumps(saetze))

        self.assertEqual(
            set(Hausartikel.objects.values_list("inventurart", flat=True)),
            {Inventurart.erste().pk},
        )

    def test_ein_backup_ohne_schalter_bekommt_ihn_zurueck(self):
        """Otherwise the loose single unit would stand in German again after restoring."""
        stueckware = Ware.objects.create(
            name="Artikel F 0,33", basiseinheit=Basiseinheit.objects.get(kennung="STUECK")
        )
        einzeln = Gebinde.objects.create(
            ware=stueckware, name="Stück(e)", faktor_in_basiseinheit=Decimal("1")
        )
        # A litre in a bottle is rightly called so and stays untouched.
        literware = Ware.objects.create(
            name="Artikel S", basiseinheit=Basiseinheit.objects.get(kennung="LITER")
        )
        flasche = Gebinde.objects.create(
            ware=literware, name="Einzeln", faktor_in_basiseinheit=Decimal("1")
        )

        self.assertEqual(Gebinde.einzelstuecke_nachtragen(), 1)

        einzeln.refresh_from_db()
        flasche.refresh_from_db()
        self.assertEqual((einzeln.name, einzeln.ist_einzeln), ("Einzeln", True))
        self.assertFalse(flasche.ist_einzeln, "Literware hat kein loses Einzelstück")

    def test_benutzer_behalten_ihr_passwort(self):
        CustomUser.objects.create_user(
            username="zoe",
            email="zoe@example.com",
            password="geheim1234",
            first_name="Zoe",
            last_name="Zählerin",
        )
        text = self.dump("accounts")

        CustomUser.objects.all().delete()
        self.laden(text)

        zoe = CustomUser.objects.get(username="zoe")
        self.assertTrue(zoe.check_password("geheim1234"))
