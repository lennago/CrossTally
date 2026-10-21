"""The mail texts: shipped per language, changed in the administration, checked when saved."""

import io
import pathlib
import tempfile

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from django_project.testing import AngemeldeteSeiten

from .forms import MailtextForm
from .mail_texts import (
    PFLICHT,
    PLATZHALTER,
    SPRACHEN,
    VARIABLEN,
    Mailart,
    ausgefuellt,
    betreffzeile,
    mitgeliefert,
    pruefen,
    sprache_von,
)
from .models import Mailtext

#: The console backend: the mail would only end in the log.
NUR_LOG = {"default": {"BACKEND": "django.core.mail.backends.console.EmailBackend"}}


class MitgelieferteTexte(SimpleTestCase):
    def test_jede_mail_hat_beide_sprachen_und_besteht_die_eigene_pruefung(self):
        for art in Mailart.values:
            for sprache in SPRACHEN:
                with self.subTest(art=art, sprache=sprache):
                    betreff, text = mitgeliefert(art, sprache)
                    self.assertTrue(betreff)
                    self.assertNotIn("\n", betreff)
                    self.assertTrue(text)
                    pruefen(betreff, art, mit_pflicht=False)
                    pruefen(text, art)

    def test_jede_mail_kennt_ihre_platzhalter_und_jeder_ist_erklaert(self):
        self.assertEqual(set(VARIABLEN), set(Mailart.values))
        for art, namen in VARIABLEN.items():
            with self.subTest(art=art):
                self.assertLessEqual(set(namen), set(PLATZHALTER))

    def test_die_englischen_texte_sind_englisch(self):
        """Every German file has an English twin, not a copy of itself."""
        for art in Mailart.values:
            with self.subTest(art=art):
                self.assertNotEqual(mitgeliefert(art, "de"), mitgeliefert(art, "en"))

    def test_eine_unbekannte_sprache_liest_deutsch(self):
        self.assertEqual(sprache_von("en-gb"), "en")
        self.assertEqual(sprache_von("fr"), "de")
        self.assertEqual(sprache_von(""), "de")


class DiePruefung(SimpleTestCase):
    def abgewiesen(self, quelle: str, art: str = Mailart.KONTO_GEAENDERT) -> str:
        with self.assertRaises(ValidationError) as fehler:
            pruefen(quelle, art)
        return " ".join(fehler.exception.messages)

    def test_platzhalter_und_bedingungen_gehen_durch(self):
        pruefen(
            "{{ vorname }} {% if selbst %}a{% elif wer == 'Kim' %}b{% elif not wer %}c"
            "{% else %}{{ wer }}{% endif %}{# Notiz #}{% comment %}x{% endcomment %}",
            Mailart.KONTO_GEAENDERT,
        )

    def test_ein_unbekannter_platzhalter(self):
        self.assertIn("„geheim“ gibt es in dieser Mail nicht.", self.abgewiesen("{{ geheim }}"))

    def test_ein_platzhalter_einer_anderen_mail(self):
        self.assertIn("„link“", self.abgewiesen("{{ link }}"))

    def test_kein_griff_in_ein_objekt(self):
        self.assertIn("„vorname.upper“", self.abgewiesen("{{ vorname.upper }}"))

    def test_keine_filter(self):
        self.assertIn("Filter", self.abgewiesen("{{ vorname|upper }}"))
        self.assertIn("Filter", self.abgewiesen("{% if vorname|length %}x{% endif %}"))

    def test_keine_anderen_anweisungen(self):
        for quelle in (
            "{% for x in aenderungen %}{% endfor %}",
            "{% include 'base.html' %}",
            "{% debug %}",
            "{% now 'Y' %}",
        ):
            with self.subTest(quelle=quelle):
                self.assertIn("Erlaubt sind nur Platzhalter", self.abgewiesen(quelle))

    def test_keine_bibliotheken(self):
        self.assertIn("lässt sich nicht lesen", self.abgewiesen("{% load i18n %}"))

    def test_ein_tippfehler_in_der_vorlage(self):
        self.assertIn("lässt sich nicht lesen", self.abgewiesen("{% if selbst %}offen"))

    def test_ohne_link_kommt_niemand_weiter(self):
        for art in PFLICHT:
            with self.subTest(art=art):
                self.assertIn("{{ link }}", self.abgewiesen("Hallo {{ vorname }}", art))
        # A subject without the link is fine, nobody clicks a subject.
        pruefen("Dein Zugang", Mailart.EINLADUNG, mit_pflicht=False)


class DasAusfuellen(SimpleTestCase):
    def test_eine_bedingung_hinterlaesst_keine_luecke(self):
        quelle = "Hallo,\n\n{% if wer %}\nVon {{ wer }}.\n{% endif %}\n\n\nTschüss"
        self.assertEqual(ausgefuellt(quelle, {"wer": ""}), "Hallo,\n\nTschüss")
        self.assertEqual(ausgefuellt(quelle, {"wer": "Kim"}), "Hallo,\n\nVon Kim.\n\nTschüss")

    def test_nichts_wird_maskiert(self):
        """A mail is plain text. '&' stays '&', not '&amp;'."""
        self.assertEqual(ausgefuellt("{{ wer }}", {"wer": "Kim & <Co>"}), "Kim & <Co>")

    def test_der_betreff_hat_eine_zeile(self):
        quelle = "Neu\n{% if wer %}\nvon {{ wer }}{% endif %}"
        self.assertEqual(betreffzeile(quelle, {"wer": "Kim"}), "Neu von Kim")


class DasModell(TestCase):
    def test_ohne_zeile_gilt_der_mitgelieferte_text(self):
        mailtext = Mailtext.fuer(Mailart.EINLADUNG)
        self.assertIsNone(mailtext.pk)
        self.assertEqual(mailtext.geltend("de"), mitgeliefert(Mailart.EINLADUNG, "de"))
        self.assertTrue(mailtext.nennt_namen)
        self.assertFalse(mailtext.angepasst)

    def test_jedes_feld_faellt_fuer_sich_zurueck(self):
        mailtext = Mailtext.objects.create(art=Mailart.EINLADUNG, betreff="Willkommen")
        betreff, text = mailtext.geltend("de")
        self.assertEqual(betreff, "Willkommen")
        self.assertEqual(text, mitgeliefert(Mailart.EINLADUNG, "de")[1])
        self.assertEqual(mailtext.geltend("en"), mitgeliefert(Mailart.EINLADUNG, "en"))
        self.assertTrue(mailtext.angepasst)

    def test_ohne_namen_bleibt_wer_leer(self):
        mailtext = Mailtext(
            art=Mailart.KONTO_GELOESCHT, nennt_namen=False, text="{{ wer }}|{{ vorname }}"
        )
        self.assertEqual(mailtext.ausfuellen("de", {"wer": "Kim", "vorname": "Alex"})[1], "|Alex")

    def test_die_pruefung_steht_am_feld(self):
        mailtext = Mailtext(
            art=Mailart.EINLADUNG, betreff="{{ link }}", text="ohne", text_en="{{ x }}"
        )
        with self.assertRaises(ValidationError) as fehler:
            mailtext.full_clean()
        self.assertEqual(set(fehler.exception.message_dict), {"text", "text_en"})

    def test_ob_eine_mail_jemanden_nennen_kann(self):
        self.assertTrue(Mailtext(art=Mailart.KONTO_GEAENDERT).hat_urheber)
        self.assertFalse(Mailtext(art=Mailart.PASSWORT_VERGESSEN).hat_urheber)
        self.assertFalse(Mailtext(art=Mailart.PASSKEY_ANGELEGT).hat_urheber)

    def test_alle_anlegen_legt_jede_mail_einmal_an(self):
        Mailtext.objects.create(art=Mailart.KONTO_GELOESCHT, text="Eigenes")
        Mailtext.alle_anlegen()
        Mailtext.alle_anlegen()
        self.assertEqual(Mailtext.objects.count(), len(Mailart.values))
        self.assertEqual(Mailtext.objects.get(art=Mailart.KONTO_GELOESCHT).text, "Eigenes")


class ImBackup(TestCase):
    def sichern_und_einspielen(self) -> None:
        puffer = io.StringIO()
        call_command(
            "dumpdata",
            "organisation",
            natural_foreign=True,
            natural_primary=True,
            stdout=puffer,
        )
        ordner = tempfile.TemporaryDirectory()
        self.addCleanup(ordner.cleanup)
        pfad = pathlib.Path(ordner.name) / "stand.json"
        pfad.write_text(puffer.getvalue(), encoding="utf-8")
        call_command("loaddata", str(pfad), verbosity=0)

    def test_ein_eigener_text_kommt_zurueck_ohne_doppelte_zeile(self):
        """Over the natural key: loading onto the rows already there updates them."""
        Mailtext.alle_anlegen()
        Mailtext.objects.filter(art=Mailart.EINLADUNG).update(text="Eigenes {{ link }}")
        self.sichern_und_einspielen()
        self.assertEqual(Mailtext.objects.count(), len(Mailart.values))
        self.assertEqual(Mailtext.fuer(Mailart.EINLADUNG).text, "Eigenes {{ link }}")

    def test_ein_alter_stand_ohne_die_tabelle_schickt_den_mitgelieferten_text(self):
        Mailtext.objects.all().delete()
        self.sichern_und_einspielen()
        self.assertFalse(Mailtext.fuer(Mailart.EINLADUNG).angepasst)


class DasFormular(TestCase):
    def formular(self, art=Mailart.KONTO_GEAENDERT, **daten) -> MailtextForm:
        mailtext = Mailtext.objects.get_or_create(art=art)[0]
        betreff, text = mitgeliefert(art, "de")
        betreff_en, text_en = mitgeliefert(art, "en")
        felder = {
            "betreff": betreff,
            "text": text,
            "betreff_en": betreff_en,
            "text_en": text_en,
            "nennt_namen": "on",
        } | daten
        return MailtextForm(felder, instance=mailtext)

    def test_ein_leeres_feld_zeigt_den_mitgelieferten_text(self):
        mailtext = Mailtext.objects.create(art=Mailart.KONTO_GEAENDERT, betreff="Eigener")
        formular = MailtextForm(instance=mailtext)
        self.assertEqual(formular["betreff"].value(), "Eigener")
        self.assertEqual(formular["text"].value(), mitgeliefert(Mailart.KONTO_GEAENDERT, "de")[1])

    def test_unveraendert_bleibt_das_feld_leer(self):
        formular = self.formular()
        self.assertTrue(formular.is_valid(), formular.errors)
        mailtext = formular.save()
        self.assertFalse(mailtext.angepasst)

    def test_zeilenenden_des_browsers_zaehlen_nicht_als_eigener_text(self):
        text = mitgeliefert(Mailart.KONTO_GEAENDERT, "de")[1].replace("\n", "\r\n") + "  \r\n"
        formular = self.formular(text=text)
        self.assertTrue(formular.is_valid(), formular.errors)
        self.assertEqual(formular.save().text, "")

    def test_ein_eigener_text_wird_gespeichert(self):
        formular = self.formular(text="Moin {{ vorname }}!\r\n\r\n{{ aenderungen }}")
        self.assertTrue(formular.is_valid(), formular.errors)
        self.assertEqual(formular.save().text, "Moin {{ vorname }}!\n\n{{ aenderungen }}")

    def test_ein_geleertes_feld_heisst_mitgeliefert(self):
        Mailtext.objects.create(art=Mailart.KONTO_GEAENDERT, text="Moin")
        formular = self.formular(text="")
        self.assertTrue(formular.is_valid(), formular.errors)
        self.assertEqual(formular.save().text, "")

    def test_ein_kaputter_text_kommt_mit_grund_zurueck(self):
        formular = self.formular(text_en="{{ vorname|upper }}")
        self.assertFalse(formular.is_valid())
        self.assertIn("Filter", str(formular.errors["text_en"]))


class InDerVerwaltung(AngemeldeteSeiten):
    def liste(self):
        return self.client.get(reverse("admin:organisation_mailtext_changelist"))

    def seite(self, art: str):
        self.liste()
        mailtext = Mailtext.objects.get(art=art)
        return self.client.get(reverse("admin:organisation_mailtext_change", args=[mailtext.pk]))

    def test_die_liste_legt_jede_mail_an_und_steht_in_ihrer_reihenfolge(self):
        antwort = self.liste()
        self.assertEqual(Mailtext.objects.count(), len(Mailart.values))
        inhalt = antwort.content.decode()
        erste, letzte = Mailart.EINLADUNG.label, Mailart.PASSKEY_ENTFERNT.label
        self.assertLess(inhalt.index(str(erste)), inhalt.index(str(letzte)))

    def test_anlegen_und_loeschen_gibt_es_nicht(self):
        self.liste()
        mailtext = Mailtext.objects.get(art=Mailart.EINLADUNG)
        self.assertEqual(
            self.client.get(reverse("admin:organisation_mailtext_add")).status_code, 403
        )
        loeschen = reverse("admin:organisation_mailtext_delete", args=[mailtext.pk])
        self.assertEqual(self.client.get(loeschen).status_code, 403)

    def test_die_seite_erklaert_platzhalter_und_zeigt_eine_vorschau(self):
        antwort = self.seite(Mailart.EINLADUNG)
        self.assertContains(antwort, "Geht an ein neues Konto ohne Passwort.")
        self.assertContains(antwort, "<code>{{ link }}</code>", html=True)
        self.assertContains(antwort, "Kim Leitung hat dir ein Konto bei CrossTally angelegt.")
        self.assertContains(antwort, "Kim Leitung has created an account for you at CrossTally.")
        self.assertContains(antwort, 'name="nennt_namen"')

    def test_ohne_urheber_gibt_es_den_haken_nicht(self):
        self.assertNotContains(self.seite(Mailart.PASSWORT_VERGESSEN), 'name="nennt_namen"')

    def test_ein_eigener_text_ueber_die_maske(self):
        self.liste()
        mailtext = Mailtext.objects.get(art=Mailart.PASSKEY_ANGELEGT)
        formular = MailtextForm(instance=mailtext)
        daten = {name: formular[name].value() for name in ("betreff", "text_en", "betreff_en")}
        daten["text"] = "Neuer Passkey „{{ passkey }}“."
        antwort = self.client.post(
            reverse("admin:organisation_mailtext_change", args=[mailtext.pk]), daten
        )
        self.assertEqual(antwort.status_code, 302)
        mailtext.refresh_from_db()
        self.assertEqual((mailtext.betreff, mailtext.text), ("", "Neuer Passkey „{{ passkey }}“."))

    def test_namen_fuer_mehrere_mails_auf_einmal(self):
        self.liste()
        aktion = {
            "action": "keine_namen_nennen",
            "_selected_action": list(Mailtext.objects.values_list("pk", flat=True)),
        }
        self.client.post(reverse("admin:organisation_mailtext_changelist"), aktion)
        for mailtext in Mailtext.objects.all():
            with self.subTest(art=mailtext.art):
                self.assertEqual(mailtext.nennt_namen, not mailtext.hat_urheber)
        aktion["action"] = "namen_nennen"
        self.client.post(reverse("admin:organisation_mailtext_changelist"), aktion)
        self.assertFalse(Mailtext.objects.filter(nennt_namen=False).exists())

    def test_zuruecksetzen_holt_den_mitgelieferten_text(self):
        self.liste()
        Mailtext.objects.update(betreff="Eigen", text_en="Own")
        self.client.post(
            reverse("admin:organisation_mailtext_changelist"),
            {
                "action": "zuruecksetzen",
                "_selected_action": list(Mailtext.objects.values_list("pk", flat=True)),
            },
        )
        self.assertFalse(any(mailtext.angepasst for mailtext in Mailtext.objects.all()))

    @override_settings(MAILERS=NUR_LOG, DEBUG=False)
    def test_ohne_mailserver_bleibt_alles_bearbeitbar_mit_hinweis(self):
        self.assertContains(self.liste(), "Zurzeit ist kein E-Mail-Server eingerichtet")
        antwort = self.seite(Mailart.KONTO_GEAENDERT)
        self.assertContains(antwort, "Zurzeit ist kein E-Mail-Server eingerichtet")
        self.assertContains(antwort, 'name="text"')

    def test_mit_mailserver_kein_hinweis(self):
        self.assertNotContains(self.liste(), "Zurzeit ist kein E-Mail-Server eingerichtet")
