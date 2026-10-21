"""The check list in the counting app: reading, notes, the difference by hand, "target counts".

Counters see it too. Whoever sees where the count deviates can count again
before anyone closes. Writing goes one field at a time, the others stay,
the difference only once a manager switched it on, and "target counts" only
for managers.
"""

from decimal import Decimal
from unittest import mock

from django.db.models.sql.compiler import SQLCompiler

from django_project.testing import fuehrungskraft, inventur_anlegen
from inventur.models import Inventur, Inventurstatus, Sollbestand

from .tests import STICHTAG, ApiGrundlagen, adresse
from .views import review as pruefliste


class PruefGrundlagen(ApiGrundlagen):
    def zeilen(self) -> dict[int, dict]:
        rsp = self.client.get(adresse("inventur-pruefliste", self.inventur.pk))
        self.assertEqual(rsp.status_code, 200, rsp.data)
        return {zeile["hausartikel"]: zeile for zeile in rsp.data["zeilen"]}

    def aendern(self, hausartikel, **daten):
        return self.client.patch(
            adresse("pruefliste_zeile", self.inventur.pk, hausartikel.pk), daten, format="json"
        )

    def umschalten(self, an: bool):
        return self.client.post(
            adresse("differenz_von_hand_api", self.inventur.pk), {"an": an}, format="json"
        )

    def leitung(self):
        """A manager who counts here too. Only assigned ones see the inventory in the app."""
        leitung = fuehrungskraft()
        self.inventur.zaehlende.add(leitung)
        return leitung


class Lesen(PruefGrundlagen):
    def test_zaehlende_sehen_die_liste(self):
        self.anmelden()

        zeile = self.zeilen()[self.cola.pk]

        self.assertEqual(zeile["nummer"], "101001")
        self.assertEqual(zeile["name"], "Artikel C 0,33")
        self.assertEqual(zeile["einheit"], "STUECK")
        self.assertEqual((zeile["ist"], zeile["soll"], zeile["differenz"]), ("0", None, None))
        self.assertFalse(zeile["gezaehlt"])

    def test_gezaehlt_und_mit_soll(self):
        self.anmelden()
        self.inventur.zaehlpositionen.update(eingabemenge=Decimal("2"))
        Sollbestand.objects.create(
            inventur=self.inventur, hausartikel=self.cola, menge_basiseinheit=Decimal("30")
        )

        zeile = self.zeilen()[self.cola.pk]

        self.assertEqual((zeile["ist"], zeile["soll"], zeile["differenz"]), ("40", "30", "10"))
        self.assertTrue(zeile["gezaehlt"])

    def test_eine_auf_null_gerundete_differenz_kommt_als_null(self):
        """40 against a target of 40.4: "0", not "-0". The app would show a shortage of nothing."""
        self.anmelden()
        self.inventur.zaehlpositionen.update(eingabemenge=Decimal("2"))
        Sollbestand.objects.create(
            inventur=self.inventur, hausartikel=self.cola, menge_basiseinheit=Decimal("40.4")
        )

        zeile = self.zeilen()[self.cola.pk]

        self.assertEqual((zeile["ist"], zeile["soll"], zeile["differenz"]), ("40", "40", "0"))

    def test_die_liste_sagt_was_sich_eintragen_laesst(self):
        self.anmelden()
        rsp = self.client.get(adresse("inventur-pruefliste", self.inventur.pk))
        self.assertFalse(rsp.data["differenz_von_hand"])
        self.assertFalse(rsp.data["hat_sollbestand"])

        rsp = self.client.get(adresse("inventur-detail", self.inventur.pk))
        self.assertFalse(rsp.data["differenz_von_hand"])
        self.assertFalse(rsp.data["hat_sollbestand"])

    def test_wer_nicht_zugeordnet_ist_sieht_nichts(self):
        self.anmelden(self.fremder)
        rsp = self.client.get(adresse("inventur-pruefliste", self.inventur.pk))
        self.assertEqual(rsp.status_code, 404)


class Notizen(PruefGrundlagen):
    def test_zaehlende_hinterlegen_eine_notiz(self):
        self.anmelden()

        rsp = self.aendern(self.cola, notiz="  zwei Kästen hinten  ")

        self.assertEqual(rsp.status_code, 200, rsp.data)
        self.assertEqual(rsp.data["zeile"]["notiz"], "zwei Kästen hinten")
        self.assertEqual(self.inventur.notizen.get().text, "zwei Kästen hinten")

    def test_geleert_ist_sie_weg(self):
        self.anmelden()
        self.aendern(self.cola, notiz="weg damit")

        self.aendern(self.cola, notiz="")

        self.assertEqual(self.inventur.notizen.count(), 0)

    def test_ohne_jede_zeile_faellt_der_artikel_heraus(self):
        """Ware N has no count position. Only its note kept it in the list."""
        self.anmelden()
        self.aendern(self.nachos, notiz="lag noch in der Küche")
        self.assertIn(self.nachos.pk, self.zeilen())

        rsp = self.aendern(self.nachos, notiz="")

        self.assertIsNone(rsp.data["zeile"])
        self.assertNotIn(self.nachos.pk, self.zeilen())

    def test_ohne_feld_nichts_zu_tun(self):
        self.anmelden()
        self.assertEqual(self.aendern(self.cola).status_code, 400)
        self.assertEqual(self.aendern(self.cola, notiz_vorher="").status_code, 400)

    def test_eine_inzwischen_geaenderte_notiz_wird_nicht_ueberschrieben(self):
        """Typed in a dead spot, and someone else wrote since: theirs stands, the app hears it."""
        self.anmelden()
        self.aendern(self.cola, notiz="zwei Kästen hinten")

        rsp = self.aendern(self.cola, notiz="aus dem Funkloch", notiz_vorher="")

        self.assertEqual(rsp.status_code, 409, rsp.data)
        self.assertEqual(rsp.data["konflikt"], "notiz")
        self.assertEqual(rsp.data["zeile"]["notiz"], "zwei Kästen hinten")
        self.assertIn("„zwei Kästen hinten“", rsp.data["detail"])
        self.assertEqual(self.inventur.notizen.get().text, "zwei Kästen hinten")

    def test_mit_dem_gesehenen_stand_schreibt_er(self):
        self.anmelden()
        self.aendern(self.cola, notiz="zwei Kästen hinten")

        rsp = self.aendern(self.cola, notiz="drei Kästen", notiz_vorher="zwei Kästen hinten")

        self.assertEqual(rsp.status_code, 200, rsp.data)
        self.assertEqual(self.inventur.notizen.get().text, "drei Kästen")
        # Nothing stored, nothing seen: that matches too.
        rsp = self.aendern(self.nachos, notiz="neu", notiz_vorher=None)
        self.assertEqual(rsp.status_code, 200, rsp.data)

    def test_ohne_gesehenen_stand_schreibt_er_wie_bisher(self):
        """An app from before 10/2026 sends no 'notiz_vorher' and is not refused."""
        self.anmelden()
        self.aendern(self.cola, notiz="zwei Kästen hinten")
        rsp = self.aendern(self.cola, notiz="aus dem Funkloch")
        self.assertEqual(rsp.status_code, 200, rsp.data)
        self.assertEqual(self.inventur.notizen.get().text, "aus dem Funkloch")

    def test_eine_inzwischen_geaenderte_differenz_ebenso(self):
        self.inventur.differenz_von_hand_setzen(True)
        self.anmelden()
        self.aendern(self.cola, differenz="-3")

        rsp = self.aendern(self.cola, differenz="-5", differenz_vorher=None)

        self.assertEqual(rsp.status_code, 409, rsp.data)
        self.assertEqual(rsp.data["konflikt"], "differenz")
        self.assertEqual(rsp.data["zeile"]["differenz"], "-3")
        self.assertIn("„-3“", rsp.data["detail"])
        self.assertEqual(
            self.aendern(self.cola, differenz="-5", differenz_vorher="-3").status_code, 200
        )

    def test_derselbe_auftrag_zweimal_ist_kein_konflikt(self):
        """The answer was lost in a dead spot, the app sends the same job again."""
        self.anmelden()
        for _ in range(2):
            rsp = self.aendern(self.cola, notiz="lag hinten", notiz_vorher="")
            self.assertEqual(rsp.status_code, 200, rsp.data)
        self.assertEqual(self.inventur.notizen.get().text, "lag hinten")

    def test_dieselbe_differenz_zweimal_ist_kein_konflikt(self):
        self.inventur.differenz_von_hand_setzen(True)
        self.anmelden()
        for _ in range(2):
            rsp = self.aendern(self.cola, differenz="-3", differenz_vorher=None)
            self.assertEqual(rsp.status_code, 200, rsp.data)
        # "-3.000" is the same number: the stored value, as the server writes it.
        rsp = self.aendern(self.cola, differenz="-3.000", differenz_vorher=None)
        self.assertEqual(rsp.status_code, 200, rsp.data)
        self.assertEqual(self.inventur.notizen.get().differenz, Decimal("-3"))

    def test_in_der_vorbereitung_wartet_der_auftrag(self):
        """Taken back into preparation: the app holds the job, as with counts."""
        self.anmelden()
        self.inventur.zurueck_in_vorbereitung()

        rsp = self.aendern(self.cola, notiz="später")

        self.assertEqual(rsp.status_code, 409)
        self.assertTrue(rsp.data["vorlaeufig"])

    def test_nach_dem_abschluss_sagt_der_auftrag_warum(self):
        """Closing forgot who counted. The late job hears why anyway, not a bare 404."""
        self.anmelden()
        self.inventur.abschliessen()

        rsp = self.aendern(self.cola, notiz="zu spät")

        self.assertEqual(rsp.status_code, 409)
        self.assertEqual(rsp.data["status"], Inventurstatus.ABGESCHLOSSEN)
        self.assertFalse(rsp.data["vorlaeufig"])

    def test_ein_fremdes_konto_erfaehrt_den_namen_einer_abgeschlossenen_inventur_nicht(self):
        """Found for everyone after closing, so the answer names neither house nor day.

        Until 10/2026 a counter assigned to nothing could list every closed
        inventory of every house over the numbers.
        """
        fremde = inventur_anlegen(self.fremdes_haus, stichtag=STICHTAG)
        fremde.zaehlung_starten()
        fremde.abschliessen()
        self.anmelden(self.fremder)

        for aufruf in (
            lambda: self.client.patch(
                adresse("pruefliste_zeile", fremde.pk, self.cola.pk), {"notiz": "x"}, format="json"
            ),
            lambda: self.client.post(adresse("sperre_api", fremde.pk), {"an": True}, format="json"),
            lambda: self.client.post(
                adresse("differenz_von_hand_api", fremde.pk), {"an": True}, format="json"
            ),
        ):
            rsp = aufruf()
            self.assertEqual(rsp.status_code, 409)
            self.assertFalse(rsp.data["vorlaeufig"])
            self.assertNotIn("Kino Süd", rsp.data["detail"])
            self.assertNotIn("31.01.2026", rsp.data["detail"])

    def test_ein_artikel_eines_anderen_hauses_wird_abgewiesen(self):
        self.anmelden()
        fremd = self.cola.__class__.objects.create(
            haus=self.fremdes_haus,
            ware=self.ware,
            warengruppe=self.gruppe,
            artikelnummer="301001",
            name="Artikel C Kino Süd",
        )
        self.assertEqual(self.aendern(fremd, notiz="geht nicht").status_code, 400)


class ZurueckInDieVorbereitung(PruefGrundlagen):
    """Taken back into preparation after the view looked: the job waits on the device.

    The app writes remarks only in the count, and that is asked once more
    under the inventory's lock ('vermerk_aendern', 'pruefen'). Until 10/2026
    only the view asked, before the lock, and a remark checked a moment
    earlier landed in the preparation with 200. Two connections in
    'test_review_race'.
    """

    def danach_zurueck(self):
        """The view finds the inventory in the count, then a second tab takes it back."""
        echt = pruefliste.zum_schreiben

        def zurueck(request, pk):
            inventur = echt(request, pk)
            Inventur.objects.get(pk=pk).zurueck_in_vorbereitung()
            return inventur

        return mock.patch.object(pruefliste, "zum_schreiben", side_effect=zurueck)

    def pruefe_gehalten(self, rsp) -> None:
        self.assertEqual(rsp.status_code, 409, rsp.data)
        self.assertEqual(
            (rsp.data["konflikt"], rsp.data["status"], rsp.data["vorlaeufig"]),
            ("status", Inventurstatus.VORBEREITUNG, True),
        )
        self.assertFalse(self.inventur.notizen.exists())

    def test_eine_notiz(self):
        self.anmelden()

        with self.danach_zurueck():
            rsp = self.aendern(self.cola, notiz="später")

        self.pruefe_gehalten(rsp)

    def test_eine_differenz_von_hand(self):
        self.inventur.differenz_von_hand_setzen(True)
        self.anmelden()

        with self.danach_zurueck():
            rsp = self.aendern(self.cola, differenz="-3")

        self.pruefe_gehalten(rsp)

    def test_soll_zaehlt(self):
        Sollbestand.objects.create(
            inventur=self.inventur, hausartikel=self.cola, menge_basiseinheit=Decimal("30")
        )
        self.anmelden(self.leitung())

        with self.danach_zurueck():
            rsp = self.aendern(self.cola, soll_statt_ist=True)

        self.pruefe_gehalten(rsp)


class DifferenzVonHand(PruefGrundlagen):
    def test_ohne_schalter_keine_differenz(self):
        self.anmelden()

        rsp = self.aendern(self.cola, differenz="-3")

        self.assertEqual(rsp.status_code, 400)
        self.assertEqual(self.inventur.notizen.count(), 0)

    def test_zaehlende_schalten_nicht_um(self):
        self.anmelden()
        self.assertEqual(self.umschalten(True).status_code, 403)

    def test_die_fuehrungskraft_schaltet_und_zaehlende_tragen_ein(self):
        self.anmelden(self.leitung())
        rsp = self.umschalten(True)
        self.assertEqual(rsp.status_code, 200, rsp.data)
        self.assertTrue(rsp.data["differenz_von_hand"])

        self.anmelden()
        rsp = self.aendern(self.cola, differenz="-3.5")

        self.assertEqual(rsp.status_code, 200, rsp.data)
        self.assertEqual(rsp.data["zeile"]["differenz"], "-3.5")
        self.assertIsNone(rsp.data["zeile"]["soll"])
        # The note stays as it was: one field at a time.
        self.assertEqual(self.inventur.notizen.get().text, "")

    def test_mit_sollbestand_bleibt_es_beim_rechnen(self):
        Sollbestand.objects.create(
            inventur=self.inventur, hausartikel=self.cola, menge_basiseinheit=Decimal("30")
        )
        self.anmelden(self.leitung())

        rsp = self.umschalten(True)

        self.assertEqual(rsp.status_code, 400)
        self.inventur.refresh_from_db()
        self.assertFalse(self.inventur.differenz_von_hand)

    def test_nur_ja_oder_nein(self):
        self.anmelden(self.leitung())
        rsp = self.client.post(
            adresse("differenz_von_hand_api", self.inventur.pk), {"an": "ja"}, format="json"
        )
        self.assertEqual(rsp.status_code, 400)

    def test_die_eingetragene_differenz_wird_eingefroren(self):
        self.inventur.differenz_von_hand_setzen(True)
        self.anmelden()
        self.aendern(self.cola, differenz="-2")

        self.inventur.abschliessen()

        ergebnis = self.inventur.ergebnisse.get(hausartikel=self.cola)
        self.assertEqual(ergebnis.differenz, Decimal("-2"))
        self.assertEqual(self.inventur.status, Inventurstatus.ABGESCHLOSSEN)


class Abweichung(PruefGrundlagen):
    """Whether a row deviates comes from the server, decided like on the website.

    On the difference rounded like the unit. The app takes 'auffaellig' and
    keeps no rule of its own, so both count the same rows "with deviation".
    """

    def test_gerechnet_zaehlt_die_gerundete_differenz(self):
        """Actual 40 pieces. A target that rounds to 40 is no deviation."""
        self.anmelden()
        self.inventur.zaehlpositionen.update(eingabemenge=Decimal("2"))
        for soll, differenz, auffaellig in (
            ("40.4", "0", False),
            ("39.6", "0", False),
            ("39.4", "1", True),
            ("41", "-1", True),
        ):
            with self.subTest(soll=soll):
                Sollbestand.objects.update_or_create(
                    inventur=self.inventur,
                    hausartikel=self.cola,
                    defaults={"menge_basiseinheit": Decimal(soll)},
                )
                zeile = self.zeilen()[self.cola.pk]
                self.assertEqual((zeile["differenz"], zeile["auffaellig"]), (differenz, auffaellig))

    def test_von_hand_wie_getippt_aber_gerundet_entschieden(self):
        """A piece counts whole: 0.4 stands in the field as typed and is no deviation."""
        self.inventur.differenz_von_hand_setzen(True)
        self.anmelden()
        for eingabe, auffaellig in (("0.4", False), ("-0.4", False), ("0.5", True), ("-3", True)):
            with self.subTest(eingabe=eingabe):
                rsp = self.aendern(self.cola, differenz=eingabe)
                self.assertEqual(rsp.status_code, 200, rsp.data)
                self.assertEqual(rsp.data["zeile"]["differenz"], eingabe)
                self.assertEqual(rsp.data["zeile"]["auffaellig"], auffaellig)
                self.assertEqual(self.zeilen()[self.cola.pk]["auffaellig"], auffaellig)

    def test_ohne_soll_und_ohne_differenz_keine_abweichung(self):
        self.anmelden()
        self.assertFalse(self.zeilen()[self.cola.pk]["auffaellig"])


class SollZaehlt(PruefGrundlagen):
    """At closing the target becomes the result. Only managers set it, as on the website."""

    def soll(self, menge: str = "30") -> None:
        Sollbestand.objects.create(
            inventur=self.inventur, hausartikel=self.cola, menge_basiseinheit=Decimal(menge)
        )

    def test_die_fuehrungskraft_setzt_es_und_nimmt_es_zurueck(self):
        self.soll()
        self.anmelden(self.leitung())

        rsp = self.aendern(self.cola, soll_statt_ist=True)

        self.assertEqual(rsp.status_code, 200, rsp.data)
        self.assertTrue(rsp.data["zeile"]["soll_statt_ist"])
        self.assertTrue(self.inventur.notizen.get().soll_statt_ist)
        self.assertTrue(self.zeilen()[self.cola.pk]["soll_statt_ist"])

        rsp = self.aendern(self.cola, soll_statt_ist=False)

        self.assertFalse(rsp.data["zeile"]["soll_statt_ist"])
        self.assertEqual(self.inventur.notizen.count(), 0)

    def test_die_notiz_bleibt_dabei_stehen(self):
        self.soll()
        self.anmelden(self.leitung())
        self.aendern(self.cola, notiz="Sirup steckt in der Maschine")

        self.aendern(self.cola, soll_statt_ist=True)

        notiz = self.inventur.notizen.get()
        self.assertEqual((notiz.text, notiz.soll_statt_ist), ("Sirup steckt in der Maschine", True))

    def test_zaehlende_setzen_es_nicht(self):
        """It replaces the count at closing, so a counter could hide a shortage with it."""
        self.soll()
        self.anmelden()

        rsp = self.aendern(self.cola, soll_statt_ist=True)

        self.assertEqual(rsp.status_code, 403)
        self.assertEqual(self.inventur.notizen.count(), 0)

    def test_zaehlende_sehen_es(self):
        self.soll()
        self.inventur.notizen.create(hausartikel=self.cola, soll_statt_ist=True)
        self.anmelden()
        self.assertTrue(self.zeilen()[self.cola.pk]["soll_statt_ist"])

    def test_ohne_soll_gibt_es_nichts_zu_uebernehmen(self):
        self.anmelden(self.leitung())

        rsp = self.aendern(self.cola, soll_statt_ist=True)

        self.assertEqual(rsp.status_code, 400)
        self.assertIn("Ohne Sollbestand gibt es nichts zu übernehmen.", str(rsp.data))
        self.assertEqual(self.inventur.notizen.count(), 0)

    def test_zuruecknehmen_geht_auch_ohne_soll(self):
        """A target that vanished in the meantime must not leave the mark stuck."""
        self.soll()
        self.anmelden(self.leitung())
        self.aendern(self.cola, soll_statt_ist=True)
        Sollbestand.objects.all().delete()

        rsp = self.aendern(self.cola, soll_statt_ist=False)

        self.assertEqual(rsp.status_code, 200, rsp.data)
        self.assertEqual(self.inventur.notizen.count(), 0)

    def test_beim_abschluss_gilt_das_soll(self):
        self.soll()
        self.inventur.zaehlpositionen.update(eingabemenge=Decimal("2"))
        self.anmelden(self.leitung())
        self.aendern(self.cola, soll_statt_ist=True)

        self.inventur.abschliessen()

        ergebnis = self.inventur.ergebnisse.get(hausartikel=self.cola)
        self.assertEqual(ergebnis.gesamtmenge_basiseinheit, Decimal("30"))
        self.assertEqual(ergebnis.differenz, Decimal("0"))


class SperreOhneAeusserenJoin(PruefGrundlagen):
    """The write locks the remark's row. PostgreSQL refuses that on an outer join.

    The same trap as with the weighings on 2026-10-12: SQLite locks nothing,
    so the lock query itself is looked at here ('api/tests.py').
    """

    def test_notiz_und_differenz_sperren_ohne_aeusseren_join(self):
        self.inventur.differenz_von_hand_setzen(True)
        self.anmelden()
        gesperrt = []
        echt = SQLCompiler.as_sql

        def aufzeichnen(compiler, *args, **kwargs):
            sql, params = echt(compiler, *args, **kwargs)
            if compiler.query.select_for_update:
                gesperrt.append(sql)
            return sql, params

        with mock.patch.object(SQLCompiler, "as_sql", aufzeichnen):
            self.assertEqual(self.aendern(self.cola, notiz="erst").status_code, 200)
            self.assertEqual(self.aendern(self.cola, differenz="-1").status_code, 200)

        self.assertTrue(gesperrt, "ohne gesperrte Abfrage prüft der Test nichts")
        for sql in gesperrt:
            self.assertNotIn("OUTER JOIN", sql.upper())
