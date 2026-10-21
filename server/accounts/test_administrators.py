"""Administrators: created in the administration, always managers, never the last one gone."""

from django.test import TestCase
from django.urls import reverse

from accounts.models import CustomUser, Passkey
from accounts.test_passkey import bestaetigt
from api.test_passkey import in_der_app_entfernen
from django_project.roles import ist_letzter_administrator, nimmt_letzte_administratoren
from django_project.testing import administrator

from .test_notification import formular, konto_anlegen, verwaltungsformular

PASSWORT = "ein-langes-Passwort-7"  # gitleaks:allow


class AdministratorAnlegen(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.chef = administrator(email="chef@example.com", first_name="C", last_name="C")

    def setUp(self):
        self.client.force_login(self.chef)

    def test_das_anlegeformular_bietet_den_administrator(self):
        seite = self.client.get(reverse("admin:accounts_customuser_add"))
        self.assertContains(seite, 'name="is_superuser"')
        self.assertContains(seite, "Erreicht zusätzlich die Verwaltung")

    def test_das_anlegeformular_belegt_die_sprache_mit_der_eigenen_vor(self):
        """As on the site: the invitation goes out in it (until 10/2026 in the server's)."""
        self.chef.sprache = "en"
        self.chef.save()

        seite = self.client.get(reverse("admin:accounts_customuser_add"))

        self.assertContains(seite, 'name="sprache"')
        self.assertContains(seite, '<option value="en" selected>', html=False)

    def test_ein_administrator_ist_gleich_auch_fuehrungskraft(self):
        antwort = self.client.post(
            reverse("admin:accounts_customuser_add"),
            {
                "username": "ada",
                "email": "ada@example.com",
                "first_name": "Ada",
                "last_name": "Lovelace",
                "passwort_setzen": "on",
                "password1": PASSWORT,
                "password2": PASSWORT,
                "is_superuser": "on",
            },
        )
        self.assertEqual(antwort.status_code, 302)
        ada = CustomUser.objects.get(username="ada")
        self.assertTrue(ada.is_superuser)
        self.assertTrue(ada.is_staff)

    def test_beim_aendern_ebenso(self):
        nele = konto_anlegen()
        self.client.post(
            reverse("admin:accounts_customuser_change", args=[nele.pk]),
            verwaltungsformular(nele, is_superuser=True),
        )
        nele.refresh_from_db()
        self.assertTrue(nele.is_superuser)
        self.assertTrue(nele.is_staff)

    def test_die_website_bietet_ihn_nicht(self):
        seite = self.client.get(reverse("user_add"))
        self.assertNotContains(seite, 'name="is_superuser"')

    def test_auf_der_website_bleibt_ein_administrator_fuehrungskraft(self):
        zweiter = administrator(
            username="zweiter", email="zweiter@example.com", first_name="Z", last_name="Z"
        )
        antwort = self.client.post(
            reverse("user_edit", args=[zweiter.pk]), formular(zweiter, is_staff=False)
        )
        self.assertContains(antwort, "immer auch Führungskraft")
        zweiter.refresh_from_db()
        self.assertTrue(zweiter.is_staff)


class LetzterAdministratorInDerVerwaltung(TestCase):
    """Until 10/2026 the administration let the last administrator go in every way."""

    @classmethod
    def setUpTestData(cls):
        cls.chef = administrator(email="chef@example.com", first_name="C", last_name="C")

    def setUp(self):
        self.client.force_login(self.chef)

    def aendern(self, **abweichend):
        return self.client.post(
            reverse("admin:accounts_customuser_change", args=[self.chef.pk]),
            verwaltungsformular(self.chef, **abweichend),
        )

    def test_laesst_sich_nicht_deaktivieren(self):
        self.assertContains(self.aendern(is_active=False), "letzte Administrator")
        self.chef.refresh_from_db()
        self.assertTrue(self.chef.is_active)

    def test_laesst_sich_nicht_zurueckstufen(self):
        self.assertContains(self.aendern(is_superuser=False), "letzte Administrator")
        self.chef.refresh_from_db()
        self.assertTrue(self.chef.is_superuser)

    def test_bietet_kein_loeschen_an(self):
        seite = self.client.get(reverse("admin:accounts_customuser_change", args=[self.chef.pk]))
        self.assertNotContains(
            seite, reverse("admin:accounts_customuser_delete", args=[self.chef.pk])
        )

    def test_die_adresse_zum_loeschen_sagt_warum(self):
        antwort = self.client.post(
            reverse("admin:accounts_customuser_delete", args=[self.chef.pk]),
            {"post": "yes"},
            follow=True,
        )
        self.assertContains(antwort, "letzte Administrator")
        self.assertTrue(CustomUser.objects.filter(pk=self.chef.pk).exists())

    def sammelloeschen(self, *konten):
        return self.client.post(
            reverse("admin:accounts_customuser_changelist"),
            {
                "action": "delete_selected",
                "_selected_action": [konto.pk for konto in konten],
                "post": "yes",
            },
            follow=True,
        )

    def test_gemeinsam_geloescht_bleibt_der_letzte(self):
        nele = konto_anlegen()
        antwort = self.sammelloeschen(self.chef, nele)
        self.assertContains(antwort, "letzte Administrator")
        self.assertEqual(CustomUser.objects.count(), 2)

    def test_zwei_gemeinsam_geloeschte_administratoren_bleiben_auch(self):
        zweiter = administrator(username="zweiter", email="zweiter@example.com")
        self.sammelloeschen(self.chef, zweiter)
        self.assertEqual(CustomUser.objects.filter(is_superuser=True).count(), 2)

    def test_mit_einem_zweiten_geht_es(self):
        zweiter = administrator(username="zweiter", email="zweiter@example.com")
        self.sammelloeschen(zweiter)
        self.assertFalse(CustomUser.objects.filter(pk=zweiter.pk).exists())


class WerAlsAdministratorZaehlt(TestCase):
    """Only whoever gets into the administration: the role, active, and a manager."""

    def test_ein_administrator_ohne_fuehrungskraft_zaehlt_nicht(self):
        chef = administrator()
        administrator(username="ohne", email="ohne@example.com", is_staff=False)
        self.assertTrue(ist_letzter_administrator(chef))

    def test_ein_inaktiver_zaehlt_nicht(self):
        chef = administrator()
        administrator(username="weg", email="weg@example.com", is_active=False)
        self.assertTrue(ist_letzter_administrator(chef))

    def test_zwei_zaehlen(self):
        chef = administrator()
        administrator(username="zweiter", email="zweiter@example.com")
        self.assertFalse(ist_letzter_administrator(chef))

    def test_eine_auswahl_mit_allen_administratoren(self):
        administrator()
        administrator(username="zweiter", email="zweiter@example.com")
        konto_anlegen()
        alle = CustomUser.objects.filter(is_superuser=True)
        self.assertTrue(nimmt_letzte_administratoren(alle))
        self.assertFalse(nimmt_letzte_administratoren(alle.filter(username="zweiter")))
        self.assertFalse(nimmt_letzte_administratoren(CustomUser.objects.filter(username="nele")))


class LetzterAdministratorBehaeltDasPasswort(TestCase):
    """Django's password form offers to switch the password off. Not for the last one."""

    def test_ohne_passkey_wird_abgewiesen(self):
        chef = administrator(email="chef@example.com")
        self.client.force_login(chef)
        antwort = self.client.post(
            reverse("admin:auth_user_password_change", args=[chef.pk]),
            {"usable_password": "false", "unset-password": "1"},
            follow=True,
        )
        self.assertContains(antwort, "keinen Passkey")
        chef.refresh_from_db()
        self.assertTrue(chef.has_usable_password())

    def test_mit_passkey_geht_es(self):
        chef = administrator(email="chef@example.com")
        Passkey.objects.create(
            benutzer=chef, kennung="abc", oeffentlicher_schluessel=b"x", name="Handy"
        )
        self.client.force_login(chef)
        self.client.post(
            reverse("admin:auth_user_password_change", args=[chef.pk]),
            {"usable_password": "false", "unset-password": "1"},
        )
        chef.refresh_from_db()
        self.assertFalse(chef.has_usable_password())


class LetzterAdministratorBehaeltDenPasskey(TestCase):
    """Without a password his last passkey is his only way in. Until 10/2026 it could go."""

    def setUp(self):
        self.chef = administrator(email="chef@example.com")
        self.chef.set_unusable_password()
        self.chef.save()
        self.passkey = Passkey.objects.create(
            benutzer=self.chef, kennung="abc", oeffentlicher_schluessel=b"x", name="Handy"
        )

    def bleibt(self) -> bool:
        return Passkey.objects.filter(pk=self.passkey.pk).exists()

    def test_auf_der_website(self):
        self.client.force_login(self.chef)
        bestaetigt(self.client, self.chef)
        antwort = self.client.post(reverse("passkey_delete", args=[self.passkey.pk]), follow=True)
        self.assertContains(antwort, "Ohne diesen Passkey")
        self.assertTrue(self.bleibt())

    def test_in_der_zaehl_app(self):
        antwort = in_der_app_entfernen(self.chef, self.passkey.pk)
        self.assertEqual(antwort.status_code, 400)
        self.assertIn("Ohne diesen Passkey", antwort.json()["detail"])
        self.assertTrue(self.bleibt())

    def test_in_der_verwaltung(self):
        self.client.force_login(self.chef)
        seite = self.client.get(
            reverse("admin:accounts_passkey_delete", args=[self.passkey.pk]), follow=True
        )
        self.assertContains(seite, "Ohne diesen Passkey")
        antwort = self.client.post(
            reverse("admin:accounts_passkey_changelist"),
            {"action": "delete_selected", "_selected_action": [self.passkey.pk]},
            follow=True,
        )
        self.assertContains(antwort, "Ohne diesen Passkey")
        self.assertTrue(self.bleibt())

    def test_mit_einem_zweiten_passkey_geht_einer(self):
        Passkey.objects.create(
            benutzer=self.chef, kennung="def", oeffentlicher_schluessel=b"y", name="Laptop"
        )
        self.client.force_login(self.chef)
        bestaetigt(self.client, self.chef)
        self.client.post(reverse("passkey_delete", args=[self.passkey.pk]))
        self.assertFalse(self.bleibt())

    def test_mit_passwort_geht_er(self):
        self.chef.set_password(PASSWORT)
        self.chef.save()
        self.client.force_login(self.chef)
        self.client.post(reverse("passkey_delete", args=[self.passkey.pk]), {"passwort": PASSWORT})
        self.assertFalse(self.bleibt())

    def test_mit_einem_zweiten_administrator_geht_er(self):
        administrator(username="zweiter", email="zweiter@example.com")
        self.client.force_login(self.chef)
        bestaetigt(self.client, self.chef)
        self.client.post(reverse("passkey_delete", args=[self.passkey.pk]))
        self.assertFalse(self.bleibt())


def nur_mit_passkey(username: str, kennung: str) -> CustomUser:
    """An administrator without a password who signs in with one passkey."""
    konto = administrator(username=username, email=f"{username}@example.com")
    konto.set_unusable_password()
    konto.save()
    Passkey.objects.create(
        benutzer=konto, kennung=kennung, oeffentlicher_schluessel=b"x", name="Handy"
    )
    return konto


def ohne_weg_hinein(username: str) -> CustomUser:
    """An administrator by the role who cannot sign in: no password, no passkey."""
    konto = administrator(username=username, email=f"{username}@example.com")
    konto.set_unusable_password()
    konto.save()
    return konto


class NurWerSichAnmeldenKannZaehlt(TestCase):
    """The last way into the administration counts only administrators who can sign in.

    Until 10/2026 an administrator without a password and without a passkey
    counted as a second one. Two who sign in with a passkey only could each
    remove the last one, relying on the other, one after the other or in a
    single selection in the administration, and nobody got in any more.
    """

    def entfernen(self, konto: CustomUser):
        return in_der_app_entfernen(konto, konto.passkeys.get().pk)

    def test_einer_ohne_weg_hinein_zaehlt_nicht(self):
        chef = administrator()
        ohne_weg_hinein("ohne")
        self.assertTrue(ist_letzter_administrator(chef))

    def test_einer_mit_passkey_zaehlt(self):
        chef = administrator()
        nur_mit_passkey("zweiter", "def")
        self.assertFalse(ist_letzter_administrator(chef))

    def test_nacheinander_bleibt_der_letzte_passkey(self):
        anna = nur_mit_passkey("anna", "abc")
        bernd = nur_mit_passkey("bernd", "def")

        self.assertEqual(self.entfernen(anna).status_code, 204)
        antwort = self.entfernen(bernd)

        self.assertEqual(antwort.status_code, 400)
        self.assertIn("Ohne diesen Passkey", antwort.json()["detail"])
        self.assertTrue(bernd.passkeys.exists())

    def test_ein_zweiter_ohne_weg_hinein_hilft_dem_letzten_passkey_nicht(self):
        chef = nur_mit_passkey("chef", "abc")
        ohne_weg_hinein("ohne")
        self.client.force_login(chef)
        bestaetigt(self.client, chef)

        antwort = self.client.post(
            reverse("passkey_delete", args=[chef.passkeys.get().pk]), follow=True
        )

        self.assertContains(antwort, "Ohne diesen Passkey")
        self.assertTrue(chef.passkeys.exists())

    def test_gemeinsam_in_der_verwaltung_bleibt_einer(self):
        anna = nur_mit_passkey("anna", "abc")
        bernd = nur_mit_passkey("bernd", "def")
        self.client.force_login(anna)

        antwort = self.client.post(
            reverse("admin:accounts_passkey_changelist"),
            {
                "action": "delete_selected",
                "_selected_action": [p.pk for p in Passkey.objects.all()],
                "post": "yes",
            },
            follow=True,
        )

        self.assertContains(antwort, "Ohne diesen Passkey")
        self.assertEqual(Passkey.objects.filter(benutzer__in=[anna, bernd]).count(), 2)

    def test_gemeinsam_mit_einem_zweiten_der_ein_passwort_hat_geht_es(self):
        anna = nur_mit_passkey("anna", "abc")
        nur_mit_passkey("bernd", "def")
        mit_passwort = administrator(username="chef", email="chef@example.com")
        self.client.force_login(mit_passwort)

        self.client.post(
            reverse("admin:accounts_passkey_changelist"),
            {
                "action": "delete_selected",
                "_selected_action": [p.pk for p in Passkey.objects.all()],
                "post": "yes",
            },
        )

        self.assertFalse(Passkey.objects.exists())
        self.assertFalse(anna.passkeys.exists())

    def test_das_passwort_bleibt_wenn_der_zweite_keinen_weg_hinein_hat(self):
        chef = administrator(email="chef@example.com")
        ohne_weg_hinein("ohne")
        self.client.force_login(chef)

        antwort = self.client.post(
            reverse("admin:auth_user_password_change", args=[chef.pk]),
            {"usable_password": "false", "unset-password": "1"},
            follow=True,
        )

        self.assertContains(antwort, "keinen Passkey")
        chef.refresh_from_db()
        self.assertTrue(chef.has_usable_password())

    def test_ein_leeres_passwort_ist_kein_weg_hinein(self):
        chef = administrator()
        leer = administrator(username="leer", email="leer@example.com")
        CustomUser.objects.filter(pk=leer.pk).update(password="")
        self.assertTrue(ist_letzter_administrator(chef))
