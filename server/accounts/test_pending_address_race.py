"""A pending address change and what ends it, at the same moment, under PostgreSQL.

The confirmation locks the account's row without its key and reads its row
of 'Adresswechsel' once more under the lock ('email_change.anwenden'). A new
password takes the row back in the transaction that writes the password,
under the lock the write takes on the account's row. Whichever comes second
waits for the other: two confirmations change the address once, and a
confirmation behind a new password finds its row gone. One side is held,
in a forced order, until the other one waits for a lock or is done
('Nebeneinander.gleichzeitig').
"""

from http import HTTPStatus
from unittest import skipUnless

from django.core import mail
from django.db import connection
from django.test import Client
from django.urls import reverse

from accounts import email_change
from accounts.models import Adresswechsel, CustomUser
from accounts.test_email_change import link_aus
from accounts.test_sign_out_race import Nebeneinander

NEU = "zoe.neu@example.org"
NEUES_PASSWORT = "ganz-anderes-Passwort-47"


@skipUnless(connection.vendor == "postgresql", "SQLite kennt keine Sperre einzelner Zeilen")
class BestaetigenNebeneinander(Nebeneinander):
    def setUp(self):
        super().setUp()
        browser = Client()
        browser.force_login(self.zoe)
        antwort = browser.post(reverse("email_aendern"), {"email": NEU, "passwort": "x"})
        self.assertEqual(antwort.status_code, HTTPStatus.FOUND)
        self.link = link_aus(mail.outbox[-1])
        mail.outbox.clear()

    def bestaetigen(self):
        return Client(raise_request_exception=False).post(self.link)

    def neues_passwort(self):
        browser = Client(raise_request_exception=False)
        browser.force_login(self.zoe)
        return browser.post(
            reverse("password_change"),
            {"old_password": "x", "new_password1": NEUES_PASSWORT, "new_password2": NEUES_PASSWORT},
        )

    def adresse(self) -> str:
        return CustomUser.objects.get(pk=self.zoe.pk).email

    def test_zwei_bestaetigungen_aendern_die_adresse_einmal(self):
        """The first holds the account: the second waits and finds its row gone."""
        ergebnis = self.gleichzeitig(
            self.bestaetigen, self.bestaetigen, (email_change, "_schreiben"), nach_dem_aufruf=True
        )

        self.assertEqual(ergebnis["zuerst"].status_code, HTTPStatus.FOUND)
        self.assertEqual(ergebnis["danach"].status_code, HTTPStatus.OK)
        self.assertContains(ergebnis["danach"], "Link ungültig")
        self.assertEqual(self.adresse(), NEU)
        self.assertFalse(Adresswechsel.objects.exists())
        self.assertEqual([nachricht.to for nachricht in mail.outbox], [["zoe@example.com", NEU]])

    def test_eine_bestaetigung_hinter_einem_neuen_passwort_ist_tot(self):
        """The new password takes the row back under its write: the confirmation waits for it."""
        ergebnis = self.gleichzeitig(
            self.neues_passwort,
            self.bestaetigen,
            (Adresswechsel, "zuruecknehmen"),
            nach_dem_aufruf=True,
            autospec=False,
        )

        self.assertEqual(ergebnis["zuerst"].status_code, HTTPStatus.FOUND)
        self.assertContains(ergebnis["danach"], "Link ungültig")
        self.assertEqual(self.adresse(), "zoe@example.com")
        self.assertTrue(CustomUser.objects.get(pk=self.zoe.pk).check_password(NEUES_PASSWORT))
