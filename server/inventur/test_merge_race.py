"""Creating and deleting inventories while a product is merged.

Creating the count positions locks the house's locations and articles before
it reads the expectations ('Inventur._orte_und_artikel_sperren'). A merge
that came first is waited for, and the rows name the pack it left. One that
comes later waits for the rows and re-hangs them along. Until 10/2026
nothing was locked there: the new rows named a pack the merge was deleting,
and creating ended in a server error at its commit.

Deleting an inventory or a run locks the inventory without the key, as the
closing does. A merge that touched rows of the inventory twice checks their
keys against it at its commit. With 'FOR UPDATE' it waited there while the
delete waited for its rows, and PostgreSQL broke one of the two off (until
10/2026).

SQLite locks nothing: 'ErstGesperrt' reads the locks off the queries, the
two classes after it force the overlap on PostgreSQL with two connections,
in both orders.
"""

import threading
from decimal import Decimal
from http import HTTPStatus
from unittest import mock, skipUnless

from django.db import connection
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse

from api.test_closing_race import _bis_jemand_wartet
from api.test_merge_split_race import abfragen_waehrend
from api.tests import STICHTAG
from django_project.testing import fuehrungskraft, inventur_anlegen, monatsinventur
from inventur.models import Inventur, Zaehlposition
from inventur.services import inventuren_anlegen
from katalog.models import (
    Artikel,
    Basiseinheit,
    Erwartung,
    ErwartungGebinde,
    Gebinde,
    Hausartikel,
    Ware,
    Warengruppe,
)
from katalog.ware_merge import zusammenfuehren
from organisation.models import Bereich, Haus, Ort


class MitBestand:
    """A house with one location, a product in crates of 20 and a second one to merge into.

    The product that stays has a crate of 20 of its own: the two crates
    merge, and the one of the product that goes is deleted.
    """

    haus: Haus
    ort: Ort
    ware: Ware
    kasten: Gebinde
    artikel: Hausartikel
    ziel: Ware
    bleibt: Gebinde

    def bestand_anlegen(self) -> None:
        self.haus = Haus.objects.create(name="Kino Ost")
        self.ort = Ort.objects.create(
            name="Thekenfront", bereich=Bereich.objects.create(name="Theke", haus=self.haus)
        )
        stueck = Basiseinheit.objects.get(name="Stück(e)")
        self.ware = Ware.objects.create(name="Artikel C", basiseinheit=stueck)
        self.kasten = Gebinde.objects.create(
            ware=self.ware, name="Kasten", faktor_in_basiseinheit=Decimal("20")
        )
        self.artikel = Hausartikel.objects.create(
            haus=self.haus,
            ware=self.ware,
            warengruppe=Warengruppe.objects.create(name="Warengruppe G"),
            artikelnummer="101001",
            name="Artikel C",
        )
        ErwartungGebinde.objects.create(
            erwartung=Erwartung.objects.create(ort=self.ort, artikel=self.artikel),
            gebinde=self.kasten,
        )
        self.ziel = Ware.objects.create(name="Artikel C neu", basiseinheit=stueck)
        self.bleibt = Gebinde.objects.create(
            ware=self.ziel, name="Kasten", faktor_in_basiseinheit=Decimal("20")
        )


def erste_abfrage(abfragen, modell, *, gesperrt: bool = False, beginnt: str = "") -> int:
    """The place of the first query on 'modell', of the first that locks it, or that begins so.

    'beginnt' is the start of the SQL, such as "INSERT" or "UPDATE".
    """
    for stelle, (art, sperre, _ohne, _reihe, sql) in enumerate(abfragen):
        if art is not modell or (gesperrt and not sperre):
            continue
        if not beginnt or sql.upper().startswith(beginnt):
            return stelle
    welche = "sperrende " if gesperrt else beginnt
    raise AssertionError(f"keine {welche}Abfrage auf {modell.__name__}")


class ErstGesperrt(MitBestand, TestCase):
    """The locks come before what they protect, read off the queries ('abfragen_waehrend')."""

    def setUp(self):
        self.bestand_anlegen()

    def erste(self, abfragen, modell, *, gesperrt: bool = False) -> int:
        """The place of the first query on 'modell', or of the first one that locks it."""
        return erste_abfrage(abfragen, modell, gesperrt=gesperrt)

    def pruefe_ohne_schluessel(self, abfragen, stelle: int) -> None:
        """Without the key, by number, and the locking query itself joins nothing."""
        _modell, _gesperrt, ohne_schluessel, reihenfolge, sql = abfragen[stelle]
        self.assertTrue(ohne_schluessel, f"FOR UPDATE statt FOR NO KEY UPDATE: {sql}")
        self.assertEqual(reihenfolge, ("pk",))
        # A subquery may join. The lock applies to the tables of the query itself.
        self.assertNotIn("JOIN", sql.upper().split(" WHERE ", 1)[0], sql)

    def test_das_anlegen_sperrt_orte_und_artikel_bevor_es_die_gebinde_liest(self):
        abfragen = abfragen_waehrend(
            lambda: inventuren_anlegen([self.haus], [monatsinventur()], STICHTAG)
        )

        ort = self.erste(abfragen, Ort, gesperrt=True)
        artikel = self.erste(abfragen, Artikel, gesperrt=True)
        self.assertLess(ort, artikel, "die Artikel vor den Orten, anders als die App")
        for stelle in (ort, artikel):
            self.pruefe_ohne_schluessel(abfragen, stelle)
        # What a merge changes is read only under the lock: the packs of the
        # counting lists, the packs themselves and the expectations with them.
        for modell in (ErwartungGebinde, Gebinde):
            self.assertLess(artikel, self.erste(abfragen, modell), modell.__name__)
        self.assertEqual(Zaehlposition.objects.get().gebinde, self.kasten)

    def test_jeder_weg_in_die_methode_sperrt_zuerst(self):
        """Creating, a change of the types and the clarification of the target all call it."""
        inventur = inventur_anlegen(self.haus, stichtag=STICHTAG)
        abfragen = abfragen_waehrend(inventur.zaehlpositionen_anlegen)

        self.assertLess(
            self.erste(abfragen, Artikel, gesperrt=True), self.erste(abfragen, ErwartungGebinde)
        )

    def loeschen(self, adresse: str) -> list:
        client = Client()
        client.force_login(fuehrungskraft())
        antworten = []
        abfragen = abfragen_waehrend(lambda: antworten.append(client.post(adresse)))
        self.assertEqual(antworten[0].status_code, HTTPStatus.FOUND)
        self.assertFalse(Inventur.objects.exists())
        return abfragen

    def pruefe_erst_gesperrt(self, abfragen) -> None:
        """The inventory without the key, then every row by number, then Django looks.

        A weighing of the app locks only its row. Until 10/2026 one written
        between Django's look and its delete ended the delete at the
        weighing's foreign key ('zeilen_der_inventuren_sperren').
        """
        inventur = self.erste(abfragen, Inventur, gesperrt=True)
        self.assertTrue(abfragen[inventur][2], f"FOR UPDATE statt FOR NO KEY UPDATE: {inventur}")
        zeilen = self.erste(abfragen, Zaehlposition, gesperrt=True)
        _modell, _gesperrt, ohne_schluessel, reihenfolge, sql = abfragen[zeilen]
        self.assertFalse(ohne_schluessel, sql)
        self.assertEqual(reihenfolge, ("pk",), sql)
        self.assertNotIn("JOIN", sql.upper(), sql)
        nachgesehen = next(
            stelle
            for stelle, (art, gesperrt, *_rest) in enumerate(abfragen)
            if art is Zaehlposition and not gesperrt
        )
        self.assertLess(inventur, zeilen)
        self.assertLess(zeilen, nachgesehen)

    def test_das_loeschen_einer_inventur_sperrt_sie_ohne_schluessel(self):
        inventur = inventur_anlegen(self.haus, stichtag=STICHTAG)
        abfragen = self.loeschen(reverse("inventur_delete", args=[inventur.pk]))

        self.pruefe_erst_gesperrt(abfragen)

    def test_das_loeschen_eines_laufs_sperrt_seine_inventuren_ohne_schluessel(self):
        inventur_anlegen(self.haus, stichtag=STICHTAG)
        inventur_anlegen(Haus.objects.create(name="Kino Nord"), stichtag=STICHTAG)
        abfragen = self.loeschen(reverse("inventur_lauf_delete", args=[STICHTAG.isoformat()]))

        self.pruefe_erst_gesperrt(abfragen)
        (zeilen,) = [
            sql for art, gesperrt, *_r, sql in abfragen if art is Zaehlposition and gesperrt
        ]
        self.assertIn(" IN (", zeilen, "die Zeilen aller Inventuren in einer Abfrage")


class Nebeneinander(MitBestand, TransactionTestCase):
    """Two sides in two threads, the first one held while it holds its locks.

    It goes on once the other side waits for a lock, or is done because
    nothing made it wait ('test_closing_race._bis_jemand_wartet'). What either
    side raises is kept, not lost in its thread.
    """

    serialized_rollback = True

    def setUp(self):
        self.bestand_anlegen()
        self.chef = fuehrungskraft()

    def gleichzeitig(
        self,
        zuerst,
        danach,
        halt,
        *,
        nach_dem_aufruf: bool = False,
        fehler_erlaubt: tuple[str, ...] = (),
    ) -> dict:
        """Runs 'zuerst' until it reaches 'halt', then 'danach' beside it.

        'halt' names the class (or module) and the method to stop at, before
        it is called or, with 'nach_dem_aufruf', after it has read what it
        reads. A side in 'fehler_erlaubt' may raise, the test looks at what.
        """
        ziel, name = halt
        echt = getattr(ziel, name)
        gehalten = threading.Event()
        fertig = {"zuerst": threading.Event(), "danach": threading.Event()}
        ergebnis: dict = {}

        def halten(*args, **kwargs):
            if gehalten.is_set():
                return echt(*args, **kwargs)
            gehalten.set()
            if not nach_dem_aufruf:
                _bis_jemand_wartet(fertig["danach"])
                return echt(*args, **kwargs)
            gelesen = echt(*args, **kwargs)
            _bis_jemand_wartet(fertig["danach"])
            return gelesen

        def laufen(seite, tun):
            if seite == "danach":
                gehalten.wait(timeout=10)
            try:
                ergebnis[seite] = tun()
            except Exception as fehler:  # The test names it, the thread would hide it.
                ergebnis[f"{seite}_fehler"] = fehler
            finally:
                fertig[seite].set()
                connection.close()

        with mock.patch.object(ziel, name, autospec=True, side_effect=halten):
            faeden = [
                threading.Thread(target=laufen, args=("zuerst", zuerst)),
                threading.Thread(target=laufen, args=("danach", danach)),
            ]
            for faden in faeden:
                faden.start()
            for faden in faeden:
                faden.join()
        for seite in ("zuerst", "danach"):
            if seite not in fehler_erlaubt:
                self.assertNotIn(f"{seite}_fehler", ergebnis)
        return ergebnis

    def zusammenfuehren(self):
        zusammenfuehren(Ware.objects.get(pk=self.ware.pk), Ware.objects.get(pk=self.ziel.pk))

    def pruefe_zusammengefuehrt(self) -> None:
        self.assertFalse(Ware.objects.filter(pk=self.ware.pk).exists())
        self.assertFalse(Gebinde.objects.filter(pk=self.kasten.pk).exists())
        self.assertEqual(Hausartikel.objects.get(pk=self.artikel.pk).ware_id, self.ziel.pk)

    def auf_der_website(self, adresse: str):
        def schicken():
            client = Client(raise_request_exception=False)
            client.force_login(self.chef)
            return client.post(adresse)

        return schicken


#: The merge is held once it has re-hung the rows and deleted its crate,
#: while it holds the articles and the rows ('zaehlpositionen_sperren').
ZUSAMMENFUEHREN_HAELT = (Hausartikel, "plaetze_umschreiben")


@skipUnless(connection.vendor == "postgresql", "SQLite kennt keine Sperre einzelner Zeilen")
class AnlegenWaehrendDesZusammenfuehrens(Nebeneinander):
    """Creating an inventory with its rows while the product of an expected article merges."""

    def anlegen(self):
        return inventuren_anlegen([Haus.objects.get(pk=self.haus.pk)], [monatsinventur()], STICHTAG)

    def pruefe_angelegt(self) -> None:
        """The row stands on the crate that stays, whoever came first."""
        position = Zaehlposition.objects.get(inventur__haus=self.haus)
        self.assertEqual(position.gebinde_id, self.bleibt.pk)
        self.assertEqual(position.artikel_id, self.artikel.pk)

    def test_das_anlegen_wartet_aufs_zusammenfuehren(self):
        self.gleichzeitig(self.zusammenfuehren, self.anlegen, ZUSAMMENFUEHREN_HAELT)

        self.pruefe_zusammengefuehrt()
        self.pruefe_angelegt()

    def test_das_zusammenfuehren_wartet_aufs_anlegen(self):
        """Held after the expectations are read: what the rows will name is fixed by then."""
        self.gleichzeitig(
            self.anlegen,
            self.zusammenfuehren,
            (Inventur, "_erwartungen_im_umfang"),
            nach_dem_aufruf=True,
        )

        self.pruefe_zusammengefuehrt()
        self.pruefe_angelegt()


@skipUnless(connection.vendor == "postgresql", "SQLite kennt keine Sperre einzelner Zeilen")
class LoeschenWaehrendDesZusammenfuehrens(Nebeneinander):
    """Deleting a running inventory, or its run, while a product with counted rows merges."""

    def setUp(self):
        super().setUp()
        self.inventur = inventur_anlegen(self.haus, stichtag=STICHTAG)
        self.inventur.zaehlpositionen_anlegen()
        self.inventur.zaehlung_starten()
        # Two crates at the counter: the row the merge touches twice.
        self.inventur.zaehlpositionen.update(eingabemenge=Decimal("2"))

    def loeschen(self):
        return self.auf_der_website(reverse("inventur_delete", args=[self.inventur.pk]))()

    def lauf_loeschen(self):
        adresse = reverse("inventur_lauf_delete", args=[STICHTAG.isoformat()])
        return self.auf_der_website(adresse)()

    def pruefe_geloescht(self, antwort) -> None:
        self.assertEqual(antwort.status_code, HTTPStatus.FOUND)
        self.assertFalse(Inventur.objects.filter(pk=self.inventur.pk).exists())
        self.assertFalse(Zaehlposition.objects.exists())

    def test_das_loeschen_wartet_aufs_zusammenfuehren(self):
        ergebnis = self.gleichzeitig(self.zusammenfuehren, self.loeschen, ZUSAMMENFUEHREN_HAELT)

        self.pruefe_zusammengefuehrt()
        self.pruefe_geloescht(ergebnis["danach"])

    def test_das_zusammenfuehren_wartet_aufs_loeschen(self):
        """Held after the inventory is locked, right before the delete collects its rows."""
        ergebnis = self.gleichzeitig(self.loeschen, self.zusammenfuehren, (Inventur, "delete"))

        self.pruefe_zusammengefuehrt()
        self.pruefe_geloescht(ergebnis["zuerst"])

    def test_der_lauf_wartet_aufs_zusammenfuehren(self):
        ergebnis = self.gleichzeitig(
            self.zusammenfuehren, self.lauf_loeschen, ZUSAMMENFUEHREN_HAELT
        )

        self.pruefe_zusammengefuehrt()
        self.pruefe_geloescht(ergebnis["danach"])
