"""The API reads JSON only, so a form or multipart body is refused before it is read.

Until 10/2026 DRF kept its default parsers, and every endpoint that reads a
body parsed a multipart one too, the anonymous doors among them. Django writes
the file parts of a multipart body to a temporary file and counts them against
neither 'DATA_UPLOAD_MAX_MEMORY_SIZE' nor 'DATA_UPLOAD_MAX_NUMBER_FIELDS'. Four
parallel anonymous POSTs with file parts of several GB filled the disk and held
every worker. With JSON only such a body gets 415, and no part is read.
"""

from urllib.parse import urlencode

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.files.uploadhandler import FileUploadHandler
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from .tests import adresse

#: Set by 'MeldenderHandler' when Django starts to receive an uploaded file.
#: A module level list, because 'FILE_UPLOAD_HANDLERS' names the handler by a
#: dotted path and builds it anew for every request.
GERUFEN: list[tuple[str, str]] = []


class MeldenderHandler(FileUploadHandler):
    """Records every file part Django begins to read. It must stay empty here.

    If DRF parses a multipart body, Django hands the file parts to this
    handler. If DRF refuses the body first, the handler is never touched.
    """

    def new_file(self, field_name, file_name, *args, **kwargs):
        GERUFEN.append((field_name, file_name))
        super().new_file(field_name, file_name, *args, **kwargs)

    def receive_data_chunk(self, raw_data, start):
        return raw_data

    def file_complete(self, file_size):
        return None


#: Anonymous doors that read the body. None needs an account, so parsing
#: happens before any permission could refuse first.
ANONYME_TUEREN = ("token_pruefen", "token_erneuern", "passkey_anmelden_api")

#: One file part, so Django would write a temporary file if it parsed the body.
EINE_DATEI = {"datei": SimpleUploadedFile("x.bin", b"0123456789", "application/octet-stream")}


@override_settings(
    FILE_UPLOAD_HANDLERS=["api.test_parsers.MeldenderHandler"], FILE_UPLOAD_MAX_MEMORY_SIZE=0
)
class KeinFormularKeinMultipart(APITestCase):
    """A form or multipart body to an anonymous endpoint is refused with 415."""

    def setUp(self):
        GERUFEN.clear()

    def test_multipart_wird_mit_415_abgewiesen(self):
        for tuer in ANONYME_TUEREN:
            with self.subTest(tuer):
                antwort = self.client.post(adresse(tuer), EINE_DATEI, format="multipart")
                self.assertEqual(antwort.status_code, status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)

    def test_kein_dateiteil_wird_geschrieben(self):
        """The whole point: the file part is never read, so nothing lands in a temp file."""
        self.client.post(adresse("passkey_anmelden_api"), EINE_DATEI, format="multipart")
        self.assertEqual(GERUFEN, [])

    def test_formular_wird_mit_415_abgewiesen(self):
        for tuer in ANONYME_TUEREN:
            with self.subTest(tuer):
                antwort = self.client.post(
                    adresse(tuer),
                    urlencode({"token": "x"}),
                    content_type="application/x-www-form-urlencoded",
                )
                self.assertEqual(antwort.status_code, status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)

    def test_json_wird_weiter_gelesen(self):
        """JSON still reaches the view. An empty sign-in is a 400, not a 415."""
        antwort = self.client.post(adresse("passkey_anmelden_api"), {}, format="json")
        self.assertEqual(antwort.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(GERUFEN, [])
