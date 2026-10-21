"""A damaged workbook on both upload pages: a sentence at the field, not a server error.

A copy that broke off halfway is the everyday case. The library then throws
a class of its own ('BadZipFile'), not the 'ValueError' the readers caught
until 10/2026, and the person saw the error page. A zip without a workbook
and a damaged xls raise classes of their own again.
"""

import io
import zipfile
from datetime import date

import openpyxl
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from django_project.testing import AngemeldeteSeiten, inventur_anlegen
from inventur.models import Sollbestand
from katalog.models import Hausartikel
from organisation.models import Haus

MELDUNG = "keine Excel-Arbeitsmappe oder beschädigt"


def halbe_arbeitsmappe(name: str) -> SimpleUploadedFile:
    """A real workbook, cut in half: no zip any more, but it starts like one."""
    puffer = io.BytesIO()
    mappe = openpyxl.Workbook()
    blatt = mappe.active
    assert blatt is not None
    blatt.append(["101001", "Artikel C", "5"])
    mappe.save(puffer)
    daten = puffer.getvalue()
    return SimpleUploadedFile(name, daten[: len(daten) // 2])


def zip_ohne_mappe(name: str) -> SimpleUploadedFile:
    """A sound zip with the ending of a workbook, but nothing of one inside."""
    puffer = io.BytesIO()
    with zipfile.ZipFile(puffer, "w") as archiv:
        archiv.writestr("liesmich.txt", "Kein Blatt")
    return SimpleUploadedFile(name, puffer.getvalue())


def kaputtes_xls(name: str) -> SimpleUploadedFile:
    """Starts like an old xls (the signature of the compound file), then nothing fits."""
    return SimpleUploadedFile(name, b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 600)


#: The damaged files found in 10/2026, under the two endings the readers accept.
KAPUTT = (
    ("halb kopiert", halbe_arbeitsmappe, "xlsx"),
    ("zip ohne Mappe", zip_ohne_mappe, "xlsx"),
    ("kaputtes xls", kaputtes_xls, "xls"),
)


class HalbKopierteDatei(AngemeldeteSeiten):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.haus = Haus.objects.create(name="Kino Nord")

    def test_der_artikelimport_meldet_sie_am_feld(self):
        antwort = self.client.post(
            reverse("hausartikel_import", args=[self.haus.pk]),
            {"datei": halbe_arbeitsmappe("artikel.xlsx"), "format": "winticket"},
        )

        self.assertContains(antwort, MELDUNG)
        self.assertFalse(Hausartikel.objects.exists())

    def test_der_sollbestand_meldet_sie_am_feld(self):
        inventur = inventur_anlegen(self.haus, stichtag=date(2026, 1, 31))

        antwort = self.client.post(
            inventur.get_absolute_url(),
            {"datei": halbe_arbeitsmappe("soll.xlsx"), "format": "winticket"},
        )

        self.assertContains(antwort, MELDUNG)
        self.assertFalse(Sollbestand.objects.exists())

    def test_jede_kaputte_datei_auf_beiden_seiten(self):
        inventur = inventur_anlegen(self.haus, stichtag=date(2026, 1, 31))
        for fall, datei, endung in KAPUTT:
            for adresse, name in (
                (reverse("hausartikel_import", args=[self.haus.pk]), f"artikel.{endung}"),
                (inventur.get_absolute_url(), f"soll.{endung}"),
            ):
                with self.subTest(fall=fall, datei=name), self.assertLogs(level="WARNING"):
                    antwort = self.client.post(
                        adresse, {"datei": datei(name), "format": "winticket"}
                    )

                    self.assertContains(antwort, MELDUNG)
        self.assertFalse(Hausartikel.objects.exists())
        self.assertFalse(Sollbestand.objects.exists())
