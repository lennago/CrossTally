from typing import TYPE_CHECKING

from django.conf import settings
from django.contrib import admin, messages
from django.contrib.admin.actions import delete_selected as gewaehlte_loeschen
from django.contrib.admin.utils import unquote
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import Group
from django.db import transaction
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

from django_project.labels import Beschriftung
from django_project.mail import mail_moeglich
from django_project.roles import (
    administratoren_sperren,
    bleibt_administrator,
    ist_letzter_administrator,
    letzter_administrator_geht,
)

from .email_change import (
    BESTAETIGUNG_NICHT_GESENDET,
    BESTAETIGUNG_UNTERWEGS,
    bestaetigung_senden,
    sofort,
)
from .forms import (
    KONTO_GELOESCHT,
    LETZTER_ADMINISTRATOR_BEHAELT_PASSKEY,
    LETZTER_ADMINISTRATOR_BEHAELT_PASSWORT,
    LETZTER_ADMINISTRATOR_BLEIBT,
    CustomUserAdminChangeForm,
    CustomUserCreationForm,
)
from .invitation import einladung_senden, einladungen_entwerten
from .lockout import spuren_loeschen
from .models import Adresswechsel, Anmeldeversuch, CustomUser, Fehlversuch, Passkey
from .notification import (
    Kontostand,
    aenderung_melden,
    loeschung_melden,
    passkey_melden,
    passwort_melden,
    warnen_wenn_nicht_gesendet,
)
from .passkey import nimmt_den_letzten_weg
from .password_forms import PasswortSetzenForm
from .sign_out import ueberall_abmelden

if TYPE_CHECKING:
    # Exists only in the stubs, hence behind the guard.
    from django.contrib.admin.options import _FieldOpts

# What has no business in the administration. Unregistered here because this
# file is loaded after the apps that register it.
#
# - **Groups**: who may do what hangs on 'is_staff' and 'is_superuser' for us.
#   A second track next to it would promise a fine control that is queried
#   nowhere.
# - **Tokens**: the logins of the counting app. The package's admin can do
#   nothing one would need it for: the list is read-only, blocking goes
#   through a second entry with a selection of 'jti' hashes (no device
#   identifier), and because the tokens rotate on every refresh, it grows per
#   device and week. It would take effect only after an hour anyway, when the
#   access token expires. Whoever has to lock someone out at once takes the
#   "Active" tick off the account. 'CHECK_USER_IS_ACTIVE' acts on the next
#   request, and switching it off signs the account out everywhere
#   ('ueberall_abmelden'): switched on again, it brings back none of the old
#   sign-ins (until 10/2026 it brought back all of them).
for _modell in (Group, OutstandingToken, BlacklistedToken):
    admin.site.unregister(_modell)


HAUS_FIELDSET: tuple[Beschriftung, _FieldOpts] = (_("Häuser"), {"fields": ("haeuser",)})

#: Where 'save_model' leaves the stored state for 'save_related'. On the form,
#: because it belongs to one request and the admin serves them all.
VORHER = "_kontostand_vorher"
#: Where 'save_model' leaves an address that waits for the person's confirmation.
NEUE_ADRESSE = "_neue_adresse"


class LetzterAdministratorBleibt(Exception):
    """Raised by the saving step: the change takes the last administrator.

    It reaches the view through Django's transaction, so the whole change
    goes back ('CustomUserAdmin.changeform_view').
    """


class KontoWeg(Exception):
    """Raised by the saving step: the account was deleted while its form was checked.

    Like 'LetzterAdministratorBleibt' it reaches the view through Django's
    transaction, and nothing of the change is written. Carries the name the
    page showed.
    """

    def __init__(self, name: str):
        super().__init__(name)
        self.name = name


@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    add_form = CustomUserCreationForm
    form = CustomUserAdminChangeForm
    # Writes the password and nothing else of the row ('password_forms.py').
    change_password_form = PasswortSetzenForm
    list_display = [
        "email",
        "username",
        "first_name",
        "last_name",
        "is_staff",
        "haus_liste",
    ]
    # Not '*UserAdmin.list_filter': that names the groups, which are not in use here.
    list_filter = ("is_staff", "is_superuser", "is_active", "haeuser")
    actions = ["delete_selected"]
    fieldsets = (*(UserAdmin.fieldsets or ()), HAUS_FIELDSET)
    # Not '*UserAdmin.add_fieldsets': since Django 5.1 'usable_password' stands
    # there, which only 'AdminUserCreationForm' knows. The page died of it with a
    # 'FieldError'. And the e-mail was missing, which is required and unique here:
    # the second account created that way would have failed on the empty address.
    # So the same is asked as when creating through the site ('/users/'), with the
    # same form. Only the administrator role is extra: it can be given only
    # here. With a mail server the tick "Set the password yourself" stands
    # before the two fields ('get_fieldsets'), as on the site.
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "username",
                    "email",
                    "first_name",
                    "last_name",
                    "sprache",
                    "password1",
                    "password2",
                    "is_staff",
                    "is_superuser",
                ),
            },
        ),
        HAUS_FIELDSET,
    )

    class Media:
        # Shows the password fields only with the tick. On the change form
        # there is no tick, and the script does nothing.
        js = ("js/password_choice.js",)

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("haeuser")

    def get_changeform_initial_data(self, request):
        # The language is preselected with the creator's own, as on the site:
        # the invitation goes out in it (until 10/2026 the form had no field
        # here, and the server's language was taken).
        # 'request.user' is typed as any user for pyright, in the administration it is ours.
        vorgabe = getattr(request.user, "sprache", "")
        return super().get_changeform_initial_data(request) | {"sprache": vorgabe}

    def get_fieldsets(self, request, obj=None):
        """With a mail server the add form asks for the tick before the password fields."""
        fieldsets = super().get_fieldsets(request, obj)
        if obj is not None or not mail_moeglich():
            return fieldsets
        ergaenzt = []
        for name, optionen in fieldsets:
            # Every fieldset has its 'fields', Django checks it (admin.E011).
            # A default of '()' only made Pylance read the list as 'list[Never]'.
            felder = list(optionen["fields"])
            if "password1" in felder:
                felder.insert(felder.index("password1"), "passwort_setzen")
            ergaenzt.append((name, {**optionen, "fields": tuple(felder)}))
        return tuple(ergaenzt)

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        """A change the saving step refuses goes back whole, and the page says why.

        An account deleted meanwhile leads to the list, as on the site.
        """
        try:
            return super().changeform_view(request, object_id, form_url, extra_context)
        except LetzterAdministratorBleibt:
            self.message_user(request, LETZTER_ADMINISTRATOR_BLEIBT, messages.ERROR)
            return HttpResponseRedirect(request.get_full_path())
        except KontoWeg as weg:
            self.message_user(request, KONTO_GELOESCHT % {"name": weg.name}, messages.ERROR)
            return HttpResponseRedirect(reverse("admin:accounts_customuser_changelist"))

    def save_model(self, request, obj, form, change):
        """The same form as on the site, so the same mail when the password stays empty.

        On a change the stored state is noted first, because the form has
        already written its values into 'obj'. 'save_related' tells the person.
        Written are the columns the form changed ('geaenderte_spalten'), as on
        the site ('UserUpdateView'): saved whole, the row as the request read
        it undid a new password, a deactivation or a sign-in that came in
        between, and an account deleted meanwhile came back (until 10/2026).
        Read under the account's lock, after the administrators'.

        Whether the change takes the last administrator is asked once more
        here, under the lock of the administrators, in the transaction Django
        saves in ('letzter_administrator_geht'). The form asked before and
        without a lock. Switched off, the account is signed out everywhere,
        and without a password its invitation links die, as on the site
        ('UserUpdateView').
        """
        if not change:
            super().save_model(request, obj, form, change)
        else:
            self._aenderung_speichern(obj, form)
        # The mails go out after Django's transaction: a mail server that does
        # not answer would otherwise hold the row, and for an administrator the
        # rows of every administrator, for its timeout.
        if neu := getattr(form, NEUE_ADRESSE, None):
            transaction.on_commit(lambda: self._bestaetigung_senden(request, obj, neu))
        if not change and getattr(form, "einladen", False):
            transaction.on_commit(lambda: self._einladung_senden(request, obj))

    def _aenderung_speichern(self, obj: CustomUser, form) -> None:
        """The change of an account: its columns, the address waiting, and signing out."""
        assert isinstance(form, CustomUserAdminChangeForm)
        if not bleibt_administrator(obj) and letzter_administrator_geht(
            CustomUser.objects.filter(pk=obj.pk)
        ):
            raise LetzterAdministratorBleibt
        gespeichert = form.gesperrt_gelesen()
        if gespeichert is None:
            raise KontoWeg(form.angezeigter_name)
        vorher = Kontostand.aus_der_datenbank(gespeichert)
        setattr(form, VORHER, vorher)
        spalten = form.geaenderte_spalten()
        # A new address waits for the person's confirmation, as on the site
        # ('UserUpdateView'): the column stays as it is, and the mail with
        # the link goes out after the commit. One written at once takes a
        # waiting one back.
        if "email" in spalten and obj.email != vorher.email and not sofort(gespeichert):
            setattr(form, NEUE_ADRESSE, obj.email)
            spalten.remove("email")
            obj.email = vorher.email
        try:
            with transaction.atomic():
                obj.save(update_fields=spalten)
        except CustomUser.NotUpdated as fehler:
            raise KontoWeg(form.angezeigter_name) from fehler
        if "email" in spalten:
            Adresswechsel.zuruecknehmen(obj)
        if vorher.is_active and "is_active" in spalten and not obj.is_active:
            ueberall_abmelden(obj)
            einladungen_entwerten(gespeichert)

    def _bestaetigung_senden(self, request, obj: CustomUser, neu: str) -> None:
        gesendet = bestaetigung_senden(obj, neu, request, von=request.user)
        if gesendet is False:
            self.message_user(
                request, BESTAETIGUNG_NICHT_GESENDET % {"email": neu}, messages.WARNING
            )
        else:
            self.message_user(request, BESTAETIGUNG_UNTERWEGS % {"email": neu})

    def _einladung_senden(self, request, obj: CustomUser) -> None:
        if einladung_senden(obj, request):
            self.message_user(
                request,
                _("Die Einladung ist an %(email)s unterwegs.") % {"email": obj.email},
            )
        else:
            self.message_user(
                request,
                _(
                    "Das Konto ist angelegt. Die Einladung ging nicht hinaus, der "
                    "E-Mail-Server antwortet nicht. Auf der Kontoseite lässt sie sich "
                    "erneut senden."
                ),
                level=messages.WARNING,
            )

    def save_related(self, request, form, formsets, change):
        """Only once the sites are saved too does the mail see the whole change."""
        super().save_related(request, form, formsets, change)
        vorher = getattr(form, VORHER, None)
        if vorher is not None:
            konto = form.instance
            assert isinstance(konto, CustomUser)
            transaction.on_commit(
                lambda: warnen_wenn_nicht_gesendet(
                    request, konto, aenderung_melden(konto, vorher, request.user)
                )
            )

    def user_change_password(self, request, id, form_url=""):
        """A password set here is told to the person. Only here another one sets it.

        Django's form offers to switch the password off. For the last
        administrator who can sign in, without a passkey, that would lock the
        administration for good, and nobody could set a new one (until 10/2026
        it went through). Asked and written under the same lock as removing a
        passkey ('nimmt_den_letzten_weg'): until 10/2026 an administrator
        without any way in still counted here as a second one.
        """
        abschalten = request.method == "POST" and request.POST.get("usable_password") == "false"
        with transaction.atomic():
            if abschalten:
                administratoren_sperren()
            konto_vorher = CustomUser.objects.filter(pk=unquote(id)).first()
            if (
                abschalten
                and konto_vorher is not None
                and ist_letzter_administrator(konto_vorher)
                and not konto_vorher.passkeys.exists()
            ):
                self.message_user(request, LETZTER_ADMINISTRATOR_BEHAELT_PASSWORT, messages.ERROR)
                return HttpResponseRedirect(
                    reverse("admin:accounts_customuser_change", args=[konto_vorher.pk])
                )
            alt = konto_vorher.password if konto_vorher is not None else None
            antwort = super().user_change_password(request, id, form_url)
        konto = CustomUser.objects.filter(pk=unquote(id)).first()
        if konto is not None and alt is not None and konto.password != alt:
            warnen_wenn_nicht_gesendet(
                request, konto, passwort_melden(konto, "verwaltung", request.user)
            )
        return antwort

    def has_delete_permission(self, request, obj=None) -> bool:
        """The last administrator offers no "Delete" ('roles.py')."""
        if obj is not None and ist_letzter_administrator(obj):
            return False
        return super().has_delete_permission(request, obj)

    def delete_view(self, request, object_id, extra_context=None):
        """Called by its address anyway, the page says why instead of a bare 403.

        Deleting asks under the lock of the administrators and in the same
        transaction ('letzter_administrator_geht').
        """
        with transaction.atomic():
            konto = self.get_object(request, unquote(object_id))
            if konto is not None and (
                letzter_administrator_geht(CustomUser.objects.filter(pk=konto.pk))
                if request.method == "POST"
                else ist_letzter_administrator(konto)
            ):
                self.message_user(request, LETZTER_ADMINISTRATOR_BLEIBT, messages.ERROR)
                return HttpResponseRedirect(
                    reverse("admin:accounts_customuser_change", args=[konto.pk])
                )
            return super().delete_view(request, object_id, extra_context)

    def delete_model(self, request, obj):
        """What the lockout kept about the name goes with the account, as on the site.

        The mail waits for the commit, as on 'save_model'.
        """
        super().delete_model(request, obj)
        spuren_loeschen(obj.get_username())
        transaction.on_commit(
            lambda: warnen_wenn_nicht_gesendet(request, obj, loeschung_melden(obj, request.user))
        )

    def delete_queryset(self, request, queryset):
        konten = list(queryset)
        super().delete_queryset(request, queryset)
        for konto in konten:
            spuren_loeschen(konto.get_username())
            transaction.on_commit(
                lambda konto=konto: warnen_wenn_nicht_gesendet(
                    request, konto, loeschung_melden(konto, request.user)
                )
            )

    @admin.action(permissions=["delete"], description=gewaehlte_loeschen.short_description)
    def delete_selected(self, request, queryset):
        """Django's action of the same name, which it replaces for accounts.

        One at a time, 'has_delete_permission' guards the last administrator.
        Selected together, each of two administrators still sees the other
        one, and both would go. Asked and deleted under the lock of the
        administrators, in one transaction ('letzter_administrator_geht').
        """
        with transaction.atomic():
            if letzter_administrator_geht(queryset):
                self.message_user(request, LETZTER_ADMINISTRATOR_BLEIBT, messages.ERROR)
                return None
            return gewaehlte_loeschen(self, request, queryset)

    @admin.display(description=_("Häuser"))
    def haus_liste(self, obj: CustomUser) -> str:
        return ", ".join(haus.name for haus in obj.haeuser.all()) or "–"


@admin.register(Passkey)
class PasskeyAdmin(admin.ModelAdmin):
    """Who signs in with which device. Only withdrawing happens here.

    A key arises only through the browser of the person, so there is nothing
    to add or to change by hand. Deleting withdraws the sign-in, say for a
    lost phone, when the person cannot do it themselves.
    """

    list_display = ("benutzer", "name", "erstellt_am", "zuletzt_benutzt")
    list_select_related = ("benutzer",)
    search_fields = ("benutzer__username", "benutzer__last_name", "name")
    ordering = ("benutzer", "erstellt_am")
    actions = ["delete_selected"]

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        """The last way of the last administrator offers no "Delete" ('nimmt_den_letzten_weg')."""
        if obj is not None and nimmt_den_letzten_weg([obj]):
            return False
        return super().has_delete_permission(request, obj)

    def delete_view(self, request, object_id, extra_context=None):
        """Called by its address anyway, the page says why instead of a bare 403.

        Deleting asks under the lock and in the same transaction
        ('nimmt_den_letzten_weg').
        """
        with transaction.atomic():
            if request.method == "POST":
                administratoren_sperren()
            passkey = self.get_object(request, unquote(object_id))
            if passkey is not None and nimmt_den_letzten_weg([passkey]):
                self.message_user(request, LETZTER_ADMINISTRATOR_BEHAELT_PASSKEY, messages.ERROR)
                return HttpResponseRedirect(reverse("admin:accounts_passkey_changelist"))
            return super().delete_view(request, object_id, extra_context)

    @admin.action(permissions=["delete"], description=gewaehlte_loeschen.short_description)
    def delete_selected(self, request, queryset):
        """Django's action of the same name. Selected together, the keys of several accounts go.

        Asked about all of them at once and under the lock: one at a time,
        each of two administrators still had the other's key.
        """
        with transaction.atomic():
            administratoren_sperren()
            if nimmt_den_letzten_weg(queryset):
                self.message_user(request, LETZTER_ADMINISTRATOR_BEHAELT_PASSKEY, messages.ERROR)
                return None
            return gewaehlte_loeschen(self, request, queryset)

    def delete_model(self, request, obj):
        """Withdrawn here, the person learns of it: someone else did it."""
        super().delete_model(request, obj)
        self.abmelden_und_melden(request, [obj])

    def delete_queryset(self, request, queryset):
        passkeys = list(queryset.select_related("benutzer"))
        super().delete_queryset(request, queryset)
        self.abmelden_und_melden(request, passkeys)

    @staticmethod
    def abmelden_und_melden(request, passkeys) -> None:
        """Whoever signed in with the keys is signed out everywhere ('sign_out.py').

        Only the administration's own session stays, should the keys be its own.
        The accounts by number, in the order 'alle_abmelden' locks them too.
        The mails go out once the removal is committed ('melden').
        """
        konten = {passkey.benutzer for passkey in passkeys}
        for konto in sorted(konten, key=lambda konto: konto.pk):
            ueberall_abmelden(konto, ausser=request.session.session_key)
        transaction.on_commit(lambda: PasskeyAdmin.melden(request, passkeys))

    @staticmethod
    def melden(request, passkeys) -> None:
        """One mail per removed key, sent after the commit.

        Sent inside, the transaction held the rows of every administrator
        ('administratoren_sperren'). A mail server that did not answer kept
        every renewal and sign-in of an administrator waiting, ten seconds per
        key. With six keys the request ran past the worker's minute and rolled
        back, after the mails had gone out (until 10/2026). The commit comes
        before the response, so a warning still reaches the next page.
        """
        for passkey in passkeys:
            konto = passkey.benutzer
            gesendet = passkey_melden(konto, passkey.name, angelegt=False, von=request.user)
            warnen_wenn_nicht_gesendet(request, konto, gesendet)


@admin.register(Anmeldeversuch)
class AnmeldeversuchAdmin(admin.ModelAdmin):
    """Who knocked on the wrong admin. For viewing only.

    Recorded is the user name, not the password. The decoy runs the hasher
    over it once, for the time a real check takes, and keeps nothing
    ('accounts/honeypot.py').
    """

    list_display = ("timestamp", "username", "ip_address", "path", "browser")
    list_filter = ("timestamp",)
    search_fields = ("username", "ip_address", "user_agent")
    ordering = ("-timestamp",)
    # Empty since 10/2026. A row from an older backup may still carry the key of a
    # sign-in of its day ('honeypot.aufzeichnen'), and nobody needs to read it.
    exclude = ("session_key",)

    #: This much of the user agent fits into the column without blowing it up.
    #: The rest is on the detail page.
    KENNUNG_KURZ = 40

    @admin.display(description=_("Browserkennung"))
    def browser(self, obj: Anmeldeversuch) -> str:
        kennung = obj.user_agent or ""
        if len(kennung) > self.KENNUNG_KURZ:
            return f"{kennung[: self.KENNUNG_KURZ]}…"
        return kennung or "–"

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False


@admin.register(Fehlversuch)
class FehlversuchAdmin(admin.ModelAdmin):
    """Who mistyped how often and who is locked out right now because of it.

    The only line here that **does something** is deleting: with it the lock is
    lifted, and whoever locked themselves out does not have to wait the half
    hour. Nothing is created or changed by hand. The rows are written by
    'axes' itself.

    The columns are methods: the fields belong to the package and have
    English names.
    """

    list_display = ("zeitpunkt", "username", "ip", "versuche", "gesperrt")
    search_fields = ("username", "ip_address")
    ordering = ("-attempt_time",)

    @admin.display(description=_("Letzter Versuch"), ordering="attempt_time")
    def zeitpunkt(self, obj: Fehlversuch):
        return obj.attempt_time

    @admin.display(description=_("IP-Adresse"), ordering="ip_address")
    def ip(self, obj: Fehlversuch) -> str:
        return obj.ip_address or "–"

    @admin.display(description=_("Fehlversuche"), ordering="failures_since_start")
    def versuche(self, obj: Fehlversuch) -> int:
        return obj.failures_since_start

    @admin.display(description=_("Gesperrt"), boolean=True)
    def gesperrt(self, obj: Fehlversuch) -> bool:
        """The limit and the cool-off are in the settings, not on the row.

        A row older than the cool-off no longer locks anyone. 'axes' clears
        such rows only on the next failed attempt of any account, and until
        10/2026 the column read "locked" for them.
        """
        return (
            obj.failures_since_start >= settings.AXES_FAILURE_LIMIT
            and obj.attempt_time >= timezone.now() - settings.AXES_COOLOFF_TIME
        )

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False
