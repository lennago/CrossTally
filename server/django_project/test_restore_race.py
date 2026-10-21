"""A device renews its sign-in while the administration restores a state, in a forced order.

'flush' empties every table with one 'TRUNCATE' and takes them in an order of
its own: Django gathers the names in a set, so the order changes with every
process. Renewing locks the account's row first and reads the token tables
after it ('api.serializers.account.TokenErneuernSerializer'). Where the 'TRUNCATE'
took a token table before the accounts, each side waited for the other,
PostgreSQL ended the restore, and the administration showed a server error
(until 10/2026). Here the token tables come first ('django_table_names'
patched), and the renewal is held right after its lock until the restore
waits for a lock or is done ('api.test_closing_race._bis_jemand_wartet').
Runs in the CI, which tests against PostgreSQL.
"""

import tempfile
from http import HTTPStatus
from unittest import mock, skipUnless

from django.db import connection
from django.db.backends.base.introspection import BaseDatabaseIntrospection
from django.test import Client, override_settings
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.test_sign_out_race import Nebeneinander
from api.serializers import account as account_serializers
from api.tests import adresse

from . import backup

#: In operation a key is always set, as in the other tests of the backups.
SCHLUESSEL = "nur-fuer-den-test-und-lang-genug-dafuer"

ECHTE_TABELLEN = BaseDatabaseIntrospection.django_table_names


def tokens_vorn(introspection, *args, **kwargs) -> list[str]:
    """Django's tables, the token tables first and the accounts last."""
    tabellen = ECHTE_TABELLEN(introspection, *args, **kwargs)
    tokens = [name for name in tabellen if name.startswith("token_blacklist_")]
    konten = [name for name in tabellen if name == "accounts_customuser"]
    rest = [name for name in tabellen if name not in tokens and name not in konten]
    return tokens + rest + konten


@skipUnless(connection.vendor == "postgresql", "SQLite kennt keine Sperre einzelner Zeilen")
class EinspielenUndErneuernGleichzeitig(Nebeneinander):
    def setUp(self):
        super().setUp()
        ordner = tempfile.TemporaryDirectory()
        self.addCleanup(ordner.cleanup)
        umgebung = override_settings(BACKUP_DIR=ordner.name, BACKUP_KEY=SCHLUESSEL)
        umgebung.enable()
        self.addCleanup(umgebung.disable)
        self.handy = str(RefreshToken.for_user(self.zoe))
        self.stand = backup.erstellen()
        self.verwaltung = Client(raise_request_exception=False)
        self.verwaltung.force_login(self.chef)

    def erneuern(self):
        """Without raising: the test client catches the error of every request under way.

        The restore's server error would otherwise come out on this side.
        """
        app = APIClient(raise_request_exception=False)
        return app.post(adresse("token_erneuern"), {"refresh": self.handy})

    def einspielen(self):
        return self.verwaltung.post(
            reverse("admin:backup_einspielen", args=[self.stand.name]),
            {"bestaetigung": "einspielen"},
        )

    def test_das_einspielen_wartet_auf_die_erneuerung(self):
        with mock.patch.object(
            BaseDatabaseIntrospection, "django_table_names", autospec=True, side_effect=tokens_vorn
        ):
            ergebnis = self.gleichzeitig(
                self.erneuern,
                self.einspielen,
                (account_serializers, "gesperrtes_konto"),
                nach_dem_aufruf=True,
            )

        self.assertEqual(ergebnis["zuerst"].status_code, HTTPStatus.OK)
        self.assertEqual(ergebnis["danach"].status_code, HTTPStatus.OK)
        self.assertContains(ergebnis["danach"], "Stand eingespielt")
        # The fresh token came after the restore had read the tokens: unknown afterwards.
        self.pruefe_nichts_gilt_mehr(self.handy, ergebnis["zuerst"].data["refresh"])
