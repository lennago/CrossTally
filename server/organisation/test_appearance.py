"""The own logo: upload, check, derivation and serving.

The focus is the check. Uploading goes into the administration, and what
arrives there is served again from the same origin. A file that is
something other than its name claims must therefore not get in at all.
"""

import io
import pathlib
import struct
import tempfile
import zlib

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from PIL import Image

from django_project.testing import AngemeldeteSeiten

from .forms import ErscheinungsbildForm
from .images import HOECHSTGROESSE, MINDESTKANTE, abgeleitet, gepruefte_vorlage
from .models import STANDARDNAME, Erscheinungsbild


def bild_bytes(kante: int = 512, format: str = "PNG", modus: str = "RGBA", farbe=None) -> bytes:
    """An invented image. The colour makes it distinguishable in the test."""
    bild = Image.new(modus, (kante, kante), farbe or (10, 120, 200, 255))
    puffer = io.BytesIO()
    bild.save(puffer, format=format)
    return puffer.getvalue()


class Ungelesen(SimpleUploadedFile):
    """An upload that must not be read: reading it fails the test."""

    def read(self, *args, **kwargs):
        raise AssertionError("Die Datei wurde gelesen.")


def rechteck(breite: int, hoehe: int) -> bytes:
    bild = Image.new("RGBA", (breite, hoehe), (10, 120, 200, 255))
    puffer = io.BytesIO()
    bild.save(puffer, format="PNG")
    return puffer.getvalue()


class PruefungDerVorlage(TestCase):
    def test_ein_quadratisches_png_geht_durch(self):
        bild = gepruefte_vorlage(bild_bytes())
        self.assertEqual(bild.format, "PNG")
        self.assertEqual(bild.size, (MINDESTKANTE, MINDESTKANTE))

    def test_ein_jpeg_wird_abgelehnt(self):
        """The file name says nothing. The content is read."""
        with self.assertRaisesMessage(ValidationError, "Erwartet wird ein PNG"):
            gepruefte_vorlage(bild_bytes(modus="RGB", format="JPEG"))

    def test_ein_svg_wird_abgelehnt(self):
        """An SVG can carry script and would run from the same origin."""
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
        with self.assertRaisesMessage(ValidationError, "kein lesbares Bild"):
            gepruefte_vorlage(svg)

    def test_irgendeine_datei_wird_abgelehnt(self):
        with self.assertRaisesMessage(ValidationError, "kein lesbares Bild"):
            gepruefte_vorlage("Das ist nur Text, heißt aber logo.png.".encode())

    def test_leer_geht_nicht(self):
        with self.assertRaisesMessage(ValidationError, "leer"):
            gepruefte_vorlage(b"")

    def test_nicht_quadratisch_geht_nicht(self):
        with self.assertRaisesMessage(ValidationError, "quadratisch"):
            gepruefte_vorlage(rechteck(512, 256))

    def test_zu_klein_geht_nicht(self):
        """Below it upscaling would be needed for the home screen."""
        with self.assertRaisesMessage(ValidationError, "kleiner als 512"):
            gepruefte_vorlage(bild_bytes(kante=256))

    def test_zu_gross_geht_nicht(self):
        with self.assertRaisesMessage(ValidationError, "größer als 4096"):
            gepruefte_vorlage(bild_bytes(kante=5000))

    def test_ein_kopf_mit_einer_milliarde_pixeln_geht_nicht(self):
        """A valid header announcing 40000 x 40000 pixels. Until 10/2026 a server error."""
        kopf = struct.pack(">II", 40000, 40000) + b"\x08\x06\x00\x00\x00"
        bombe = (
            b"\x89PNG\r\n\x1a\n"
            + struct.pack(">I", len(kopf))
            + b"IHDR"
            + kopf
            + struct.pack(">I", zlib.crc32(b"IHDR" + kopf))
        )
        with self.assertRaisesMessage(ValidationError, "kein lesbares Bild"):
            gepruefte_vorlage(bombe)

    def test_ein_bewegtes_png_geht_nicht(self):
        einzelbild = Image.new("RGBA", (512, 512), (10, 120, 200, 255))
        puffer = io.BytesIO()
        einzelbild.save(puffer, format="PNG", save_all=True, append_images=[einzelbild])
        with self.assertRaisesMessage(ValidationError, "bewegtes PNG"):
            gepruefte_vorlage(puffer.getvalue())


class Ableitung(TestCase):
    def test_jede_groesse_entsteht(self):
        bilder = abgeleitet(bild_bytes(kante=1024), "#ffffff")

        erwartet = {
            "logo": 512,
            "icon-192": 192,
            "apple-touch-icon": 180,
            "favicon-32": 32,
            "icon-maskable-512": 512,
            "icon-maskable-192": 192,
        }
        self.assertEqual(set(bilder), set(erwartet))
        for name, kante in erwartet.items():
            with self.subTest(name=name):
                self.assertEqual(Image.open(io.BytesIO(bilder[name])).size, (kante, kante))
                self.assertEqual(Image.open(io.BytesIO(bilder[name])).format, "PNG")

    def test_das_randfuellende_icon_traegt_den_hintergrund(self):
        """The corner belongs to the background, the centre to the motif. Android cuts the edge."""
        bilder = abgeleitet(bild_bytes(farbe=(10, 120, 200, 255)), "#ff0000")

        maskable = Image.open(io.BytesIO(bilder["icon-maskable-512"])).convert("RGBA")
        self.assertEqual(maskable.getpixel((2, 2)), (255, 0, 0, 255))
        self.assertEqual(maskable.getpixel((256, 256)), (10, 120, 200, 255))

    def test_die_uebrigen_behalten_ihre_durchsichtigkeit(self):
        """In the bar the logo sits on the page's background, and that changes."""
        durchsichtig = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
        puffer = io.BytesIO()
        durchsichtig.save(puffer, format="PNG")

        bilder = abgeleitet(puffer.getvalue(), "#ff0000")

        logo = Image.open(io.BytesIO(bilder["logo"])).convert("RGBA")
        self.assertEqual(logo.getpixel((256, 256)), (0, 0, 0, 0))


class Formular(TestCase):
    def daten(self, **abweichend) -> dict:
        return {"name": "Eigenes", "hintergrund": "#ffffff"} | abweichend

    def datei(self, daten: bytes | None = None, name: str = "logo.png"):
        return {"datei": SimpleUploadedFile(name, daten or bild_bytes(), "image/png")}

    def test_hochladen_fuellt_alle_spalten(self):
        formular = ErscheinungsbildForm(self.daten(), self.datei())

        self.assertTrue(formular.is_valid(), formular.errors)
        erscheinungsbild = formular.save()

        self.assertTrue(erscheinungsbild.hat_logo)
        for name in ("logo", "icon_192", "apple_touch_icon", "favicon_32"):
            with self.subTest(name=name):
                self.assertTrue(bytes(getattr(erscheinungsbild, name)))

    def test_der_grund_steht_am_feld(self):
        """A server error on an empty page told nobody what to do."""
        formular = ErscheinungsbildForm(self.daten(), self.datei(rechteck(512, 128)))

        self.assertFalse(formular.is_valid())
        self.assertIn("quadratisch", str(formular.errors["datei"]))

    def test_ohne_datei_bleibt_das_logo_stehen(self):
        vorher = ErscheinungsbildForm(self.daten(), self.datei()).save()

        formular = ErscheinungsbildForm(self.daten(hintergrund="#00ff00"), {}, instance=vorher)
        self.assertTrue(formular.is_valid(), formular.errors)
        nachher = formular.save()

        self.assertTrue(nachher.hat_logo)
        self.assertEqual(nachher.hintergrund, "#00ff00")

    def test_entfernen_setzt_zurueck(self):
        vorher = ErscheinungsbildForm(self.daten(), self.datei()).save()

        formular = ErscheinungsbildForm(self.daten(entfernen="on"), {}, instance=vorher)
        self.assertTrue(formular.is_valid(), formular.errors)

        self.assertFalse(formular.save().hat_logo)

    def test_beides_zugleich_geht_nicht(self):
        formular = ErscheinungsbildForm(self.daten(entfernen="on"), self.datei())

        self.assertFalse(formular.is_valid())
        self.assertIn("Entweder", str(formular.errors))

    def test_eine_zu_grosse_datei_wird_gar_nicht_erst_gelesen(self):
        """The limit stands at the field. Until 10/2026 the form read the whole upload first."""
        datei = Ungelesen("logo.png", b"\0" * (3 * 1024 * 1024), "image/png")

        formular = ErscheinungsbildForm(self.daten(), {"datei": datei})

        self.assertFalse(formular.is_valid())

        self.assertEqual(formular.errors["datei"], ["Das Bild ist größer als 2 MB."])

    def test_die_grenze_am_feld_ist_die_der_pruefung(self):
        """Up to the limit the file is read and checked as an image, one byte more it is not."""
        for groesse, datei, meldung in (
            (HOECHSTGROESSE, SimpleUploadedFile, "Die Datei ist kein lesbares Bild."),
            (HOECHSTGROESSE + 1, Ungelesen, "Das Bild ist größer als 2 MB."),
        ):
            with self.subTest(groesse=groesse):
                hochgeladen = datei("logo.png", b"\0" * groesse, "image/png")

                formular = ErscheinungsbildForm(self.daten(), {"datei": hochgeladen})

                self.assertFalse(formular.is_valid())
                self.assertEqual(formular.errors["datei"], [meldung])

    def test_eine_unmoegliche_farbe_geht_nicht(self):
        formular = ErscheinungsbildForm(self.daten(hintergrund="rot"), self.datei())

        self.assertFalse(formular.is_valid())
        self.assertIn("hintergrund", formular.errors)


class Ausliefern(AngemeldeteSeiten):
    def adresse(self, name: str = "logo") -> str:
        return reverse("icon", kwargs={"name": name})

    def hochladen(self) -> Erscheinungsbild:
        """An own version with logo, chosen right away: that is how the site sees it."""
        formular = ErscheinungsbildForm(
            {"name": "Eigenes", "hintergrund": "#ffffff"},
            {"datei": SimpleUploadedFile("logo.png", bild_bytes(), "image/png")},
        )
        assert formular.is_valid(), formular.errors
        fassung = formular.save()
        fassung.aktivieren()
        return fassung

    def test_ohne_eigenes_logo_gilt_der_platzhalter(self):
        antwort = self.client.get(self.adresse())

        self.assertEqual(antwort.status_code, 302)
        self.assertIn("logo.svg", antwort["Location"])

    def test_mit_eigenem_logo_kommt_das_bild(self):
        self.hochladen()

        antwort = self.client.get(self.adresse())

        self.assertEqual(antwort.status_code, 200)
        self.assertEqual(antwort["Content-Type"], "image/png")
        self.assertEqual(Image.open(io.BytesIO(antwort.content)).size, (512, 512))

    def test_das_groesste_icon_ist_die_vorlage_selbst(self):
        """An own field for it would be the same sequence of numbers a second time."""
        self.hochladen()

        gross = self.client.get(self.adresse("icon-512"))

        self.assertEqual(Image.open(io.BytesIO(gross.content)).size, (512, 512))

    def test_jedes_icon_des_manifests_antwortet(self):
        """The counting app points to it. Without an answer it would have no icon."""
        for name in ("icon-192", "icon-512", "icon-maskable-192", "icon-maskable-512"):
            with self.subTest(name=name):
                self.assertIn(self.client.get(self.adresse(name)).status_code, (200, 302))

    def test_der_platzhalter_wird_nur_kurz_gemerkt(self):
        """Remembered for a day, the reference survived the first upload."""
        verweis = self.client.get(self.adresse())
        self.assertIn("max-age=300", verweis["Cache-Control"])

        self.hochladen()
        bild = self.client.get(self.adresse())

        self.assertIn("max-age=86400", bild["Cache-Control"])
        self.assertTrue(bild["ETag"])

    def test_die_seite_haengt_den_stand_an(self):
        """A new logo is a new address. Otherwise the old one would stay in the browser."""
        erscheinungsbild = self.hochladen()

        seite = self.client.get(reverse("inventur_list")).content.decode()

        self.assertIn(f"?stand={int(erscheinungsbild.geaendert_am.timestamp())}", seite)

    def test_ein_unbekannter_name_gibt_es_nicht(self):
        self.assertEqual(self.client.get(self.adresse("gibtsnicht")).status_code, 404)

    def test_auch_ohne_anmeldung(self):
        """The browser tab shows its image on the login page already."""
        self.hochladen()
        self.client.logout()

        self.assertEqual(self.client.get(self.adresse()).status_code, 200)

    def test_die_seite_zeigt_das_eigene_logo(self):
        self.hochladen()

        seite = self.client.get(reverse("inventur_list")).content.decode()

        self.assertIn(self.adresse(), seite)
        self.assertNotIn("logo.svg", seite)

    def test_ohne_eigenes_logo_bleibt_das_svg(self):
        """The placeholder is an SVG and thus sharp at every size."""
        seite = self.client.get(reverse("inventur_list")).content.decode()

        self.assertIn("logo.svg", seite)


class ImBackup(TestCase):
    """The logo lies in the database so the backup takes it along.

    If it lay in a directory of its own next to it, it would be the only part
    of operation a snapshot does not capture. 'dumpdata' writes a
    'BinaryField' as Base64.
    """

    def test_ein_stand_bringt_das_logo_zurueck(self):
        formular = ErscheinungsbildForm(
            {"name": "Eigenes", "hintergrund": "#123456"},
            {"datei": SimpleUploadedFile("logo.png", bild_bytes(), "image/png")},
        )
        assert formular.is_valid(), formular.errors
        vorher = formular.save()
        vorher.aktivieren()

        puffer = io.StringIO()
        call_command(
            "dumpdata",
            "organisation",
            natural_foreign=True,
            natural_primary=True,
            indent=2,
            stdout=puffer,
        )
        Erscheinungsbild.objects.all().delete()
        ordner = tempfile.TemporaryDirectory()
        self.addCleanup(ordner.cleanup)
        pfad = pathlib.Path(ordner.name) / "stand.json"
        pfad.write_text(puffer.getvalue(), encoding="utf-8")
        call_command("loaddata", str(pfad), verbosity=0)

        nachher = Erscheinungsbild.aktuell()
        self.assertEqual(nachher.name, "Eigenes")
        self.assertEqual(nachher.hintergrund, "#123456")
        self.assertEqual(nachher.bild("logo"), vorher.bild("logo"))

    def test_ein_alter_stand_ohne_die_tabelle_geht_auch(self):
        """Older backups do not know the appearance. Then the standard applies."""
        alt = '[{"model": "organisation.haus", "fields": {"name": "Kino Nord"}}]'
        ordner = tempfile.TemporaryDirectory()
        self.addCleanup(ordner.cleanup)
        pfad = pathlib.Path(ordner.name) / "stand.json"
        pfad.write_text(alt, encoding="utf-8")
        Erscheinungsbild.objects.all().delete()

        call_command("loaddata", str(pfad), verbosity=0)

        aktuell = Erscheinungsbild.aktuell()
        self.assertTrue(aktuell.ist_standard)
        self.assertTrue(aktuell.aktiv)
        self.assertFalse(aktuell.hat_logo)


class InDerVerwaltung(AngemeldeteSeiten):
    """Uploading happens there, so the form has to be present too."""

    def hochladen(self, name: str = "Eigenes") -> None:
        antwort = self.client.post(
            reverse("admin:organisation_erscheinungsbild_add"),
            {
                "name": name,
                "hintergrund": "#ffffff",
                "datei": SimpleUploadedFile("logo.png", bild_bytes(), "image/png"),
            },
        )
        self.assertEqual(antwort.status_code, 302, antwort.context and "Formular abgelehnt")

    def test_die_maske_steht(self):
        antwort = self.client.get(reverse("admin:organisation_erscheinungsbild_add"))

        self.assertContains(antwort, "Logo")
        self.assertContains(antwort, 'type="file"')

    def test_hochladen_ueber_die_maske_legt_eine_fassung_an(self):
        """New is not active yet. Choosing happens in the list."""
        self.hochladen()

        fassung = Erscheinungsbild.objects.get(name="Eigenes")
        self.assertTrue(fassung.hat_logo)
        self.assertFalse(fassung.aktiv)
        self.assertTrue(Erscheinungsbild.aktuell().ist_standard)

    def test_eine_abgelehnte_datei_kommt_als_meldung_zurueck(self):
        antwort = self.client.post(
            reverse("admin:organisation_erscheinungsbild_add"),
            {
                "name": "Eigenes",
                "hintergrund": "#ffffff",
                "datei": SimpleUploadedFile("logo.png", b"kein Bild", "image/png"),
            },
        )

        self.assertContains(antwort, "kein lesbares Bild")
        self.assertFalse(Erscheinungsbild.objects.filter(name="Eigenes").exists())

    def test_die_liste_nennt_den_standard_uebersetzt_und_aktiv(self):
        antwort = self.client.get(reverse("admin:organisation_erscheinungsbild_changelist"))
        self.assertContains(antwort, "Standard")
        self.assertContains(antwort, 'name="action"')

    def test_der_standard_oeffnet_sich_nur_zum_lesen(self):
        standard = Erscheinungsbild.standard()
        antwort = self.client.get(
            reverse("admin:organisation_erscheinungsbild_change", args=[standard.pk])
        )
        self.assertEqual(antwort.status_code, 200)
        self.assertNotContains(antwort, 'type="file"')
        # And a submitted form changes nothing either.
        antwort = self.client.post(
            reverse("admin:organisation_erscheinungsbild_change", args=[standard.pk]),
            {"name": "Standard", "hintergrund": "#000000", "betreiber": "Betrieb"},
        )
        standard.refresh_from_db()
        self.assertEqual(standard.betreiber, "")

    def test_kopieren_und_waehlen_ueber_die_liste(self):
        adresse = reverse("admin:organisation_erscheinungsbild_changelist")
        standard = Erscheinungsbild.standard()
        antwort = self.client.post(
            adresse, {"action": "kopie_anlegen", "_selected_action": [standard.pk]}
        )
        self.assertEqual(antwort.status_code, 302)
        kopie = Erscheinungsbild.objects.get(name="Kopie von Standard")
        self.assertFalse(kopie.aktiv)
        self.assertEqual(kopie.datenschutztext, standard.datenschutztext)

        antwort = self.client.post(
            adresse, {"action": "aktiv_setzen", "_selected_action": [kopie.pk]}
        )
        self.assertEqual(antwort.status_code, 302)
        self.assertEqual(Erscheinungsbild.aktuell(), kopie)
        standard.refresh_from_db()
        self.assertFalse(standard.aktiv)

    def test_waehlen_verlangt_genau_eine(self):
        adresse = reverse("admin:organisation_erscheinungsbild_changelist")
        standard = Erscheinungsbild.standard()
        kopie = standard.kopie("Zweite")
        self.client.post(
            adresse, {"action": "aktiv_setzen", "_selected_action": [standard.pk, kopie.pk]}
        )
        self.assertTrue(Erscheinungsbild.aktuell().ist_standard)

    def test_standard_und_aktive_lassen_sich_nicht_loeschen(self):
        standard = Erscheinungsbild.standard()
        kopie = standard.kopie("Zweite")
        kopie.aktivieren()
        for fassung in (standard, kopie):
            with self.subTest(fassung=fassung.name):
                antwort = self.client.get(
                    reverse("admin:organisation_erscheinungsbild_delete", args=[fassung.pk])
                )
                self.assertEqual(antwort.status_code, 403)


class Fassungen(TestCase):
    """The standard is always there and stays as it is. Everything else is a copy."""

    def test_der_standard_ist_nach_der_migration_da_und_aktiv(self):
        standard = Erscheinungsbild.objects.get(name="Standard")
        self.assertTrue(standard.aktiv)
        self.assertEqual(Erscheinungsbild.aktuell(), standard)
        self.assertIn("Dein Passwort niemals im Klartext", standard.datenschutztext)

    def test_der_standard_laesst_sich_nicht_aendern(self):
        standard = Erscheinungsbild.standard()
        standard.betreiber = "Betrieb"
        with self.assertRaises(ValidationError):
            standard.save()
        with self.assertRaises(ValidationError):
            standard.full_clean()
        standard.refresh_from_db()
        self.assertEqual(standard.betreiber, "")

    def test_der_standard_laesst_sich_nicht_loeschen(self):
        with self.assertRaises(ValidationError):
            Erscheinungsbild.standard().delete()

    def test_die_aktive_laesst_sich_nicht_loeschen_die_andere_schon(self):
        kopie = Erscheinungsbild.standard().kopie("Zweite")
        kopie.aktivieren()
        with self.assertRaises(ValidationError):
            kopie.delete()
        Erscheinungsbild.standard().aktivieren()
        kopie.delete()
        self.assertFalse(Erscheinungsbild.objects.filter(name="Zweite").exists())

    def test_eine_kopie_nimmt_alles_mit_und_laesst_sich_aendern(self):
        formular = ErscheinungsbildForm(
            {"name": "Eigenes", "hintergrund": "#123456", "betreiber": "Betrieb"},
            {"datei": SimpleUploadedFile("logo.png", bild_bytes(), "image/png")},
        )
        assert formular.is_valid(), formular.errors
        eigenes = formular.save()
        kopie = eigenes.kopie(eigenes.kopiename())
        self.assertEqual(kopie.name, "Kopie von Eigenes")
        self.assertEqual(kopie.bild("logo"), eigenes.bild("logo"))
        self.assertEqual(kopie.betreiber, "Betrieb")
        self.assertEqual(eigenes.kopie(eigenes.kopiename()).name, "Kopie von Eigenes 2")
        kopie.betreiber = "Anderer"
        kopie.save()

    def test_ohne_aktive_uebernimmt_der_standard(self):
        """After an old backup or the reset demo. Written once."""
        Erscheinungsbild.objects.all().delete()
        aktuell = Erscheinungsbild.aktuell()
        self.assertTrue(aktuell.ist_standard and aktuell.aktiv)
        with self.assertNumQueries(1):
            Erscheinungsbild.aktuell()

    def test_die_vorschau_zeigt_die_fassung_und_nicht_die_aktive(self):
        formular = ErscheinungsbildForm(
            {"name": "Eigenes", "hintergrund": "#ffffff"},
            {"datei": SimpleUploadedFile("logo.png", bild_bytes(), "image/png")},
        )
        assert formular.is_valid(), formular.errors
        eigenes = formular.save()
        adresse = reverse("icon", kwargs={"name": "logo"})
        self.assertEqual(self.client.get(adresse).status_code, 302)
        self.assertEqual(self.client.get(f"{adresse}?von={eigenes.pk}").status_code, 200)


class DerNameDerAnwendung(AngemeldeteSeiten):
    """The name next to the logo comes from the active version, not from the '.env'.

    The environment describes the server. What the application is called is
    decided by the business using it, in the administration, like the logo
    next to it.
    """

    def eigene(self, name: str) -> Erscheinungsbild:
        fassung = Erscheinungsbild.standard().kopie("Eigenes")
        fassung.anwendungsname = name
        fassung.save()
        fassung.aktivieren()
        return fassung

    def test_der_standard_heisst_wie_die_anwendung(self):
        self.assertEqual(Erscheinungsbild.standard().anwendungsname, STANDARDNAME)
        antwort = self.client.get(reverse("haus_list"))
        self.assertContains(antwort, STANDARDNAME)

    def test_die_aktive_fassung_gibt_den_namen(self):
        """In the bar, in the browser tab and in the administration, including escaping.

        The admin index names the backup folder. Below the checkout it can carry the
        default name itself, as in a repository called 'CrossTally', hence a folder of
        its own.
        """
        self.eigene("Lager & Co")
        with tempfile.TemporaryDirectory() as ordner, self.settings(BACKUP_DIR=ordner):
            for name in ("haus_list", "admin:index"):
                with self.subTest(name=name):
                    antwort = self.client.get(reverse(name))
                    self.assertContains(antwort, "Lager &amp; Co")
                    self.assertNotContains(antwort, STANDARDNAME)

    def test_der_name_wechselt_mit_der_fassung(self):
        self.eigene("Lager & Co")
        Erscheinungsbild.standard().aktivieren()
        antwort = self.client.get(reverse("haus_list"))
        self.assertContains(antwort, STANDARDNAME)
        self.assertNotContains(antwort, "Lager &amp; Co")

    def test_eine_kopie_nimmt_den_namen_mit(self):
        eigene = self.eigene("Lager & Co")
        self.assertEqual(eigene.kopie(eigene.kopiename()).anwendungsname, "Lager & Co")

    def test_die_verwaltung_bietet_das_feld_an(self):
        antwort = self.client.get(reverse("admin:organisation_erscheinungsbild_add"))
        self.assertContains(antwort, 'name="anwendungsname"')
        self.assertContains(antwort, f'value="{STANDARDNAME}"')

    def test_der_name_laesst_sich_ueber_die_maske_aendern(self):
        fassung = Erscheinungsbild.standard().kopie("Eigenes")
        antwort = self.client.post(
            reverse("admin:organisation_erscheinungsbild_change", args=[fassung.pk]),
            {
                "name": "Eigenes",
                "anwendungsname": "Lager & Co",
                "hintergrund": "#ffffff",
                "datenschutztext": fassung.datenschutztext,
                "datenschutztext_en": fassung.datenschutztext_en,
            },
        )
        self.assertEqual(antwort.status_code, 302, antwort.context and "Formular abgelehnt")
        fassung.refresh_from_db()
        self.assertEqual(fassung.anwendungsname, "Lager & Co")

    def test_leer_heisst_der_ausgelieferte_name(self):
        """Like "Remove logo": back to the shipped one, without choosing the standard."""
        eigene = self.eigene("")
        self.assertEqual(eigene.titel, STANDARDNAME)
        self.assertContains(self.client.get(reverse("haus_list")), STANDARDNAME)

    def test_der_standard_behaelt_seinen_namen(self):
        standard = Erscheinungsbild.standard()
        standard.anwendungsname = "Anders"
        with self.assertRaises(ValidationError):
            standard.save()
