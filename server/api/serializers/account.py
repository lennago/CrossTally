"""The account: renewing its token, a new password or address, and who is signed in."""

from typing import Any

from django.contrib.auth import authenticate
from django.contrib.auth.models import update_last_login
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.exceptions import InvalidToken
from rest_framework_simplejwt.serializers import (
    TokenObtainPairSerializer,
    TokenObtainSerializer,
    TokenRefreshSerializer,
)
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.token_blacklist.models import OutstandingToken
from rest_framework_simplejwt.utils import get_md5_hash_password

from accounts.email_change import adresse_vergeben
from accounts.models import CustomUser
from accounts.passkey import (
    BESTAETIGUNG_FEHLT,
    PASSWORT_FALSCH,
    fehlende_bestaetigung_der_app,
    passwort_haelt,
)
from accounts.password_forms import passwort_speichern
from accounts.sign_out import gesperrtes_konto, neue_pruefen, paar_ausstellen
from demo.state import ist_demobestand
from django_project.mail import mail_moeglich
from organisation.models import Erscheinungsbild

#: The account a request was let in with is gone by the time it writes.
KONTO_WEG = _("Dieses Konto gibt es nicht mehr.")


class TokenHolenSerializer(TokenObtainPairSerializer):
    """Signing in with user name and password: the account let in under its lock.

    The package checks the password and the tick "Active" ('authenticate()')
    and issues the pair afterwards. A deactivation in between signed the
    account out under its lock, read the tokens, and missed the pair issued
    after it: refused while the account was off, it applied again once the
    account was switched on within its two weeks (until 10/2026). So the
    account's row is locked without its key, as for a renewal
    ('accounts/sign_out.py'), and asked once more: still active, and still
    the password just checked. Read under the lock, a password changed in
    between would otherwise go into the pair as if it had been typed. The
    answer is then that of a wrong password, as the package gives it.
    """

    def validate(self, attrs: dict[str, Any]) -> dict[str, str]:
        TokenObtainSerializer.validate(self, attrs)
        angemeldet = self.user
        with transaction.atomic():
            konto = gesperrtes_konto(angemeldet.get_username()) if angemeldet else None
            if (
                konto is None
                or not konto.is_active
                or konto.password != getattr(angemeldet, "password", None)
            ):
                raise AuthenticationFailed(
                    self.error_messages["no_active_account"], "no_active_account"
                )
            erneuerung = paar_ausstellen(konto)
            if api_settings.UPDATE_LAST_LOGIN:
                # The sender is the model, as when Django sends 'user_logged_in'.
                update_last_login(type(konto), konto)
        return {"refresh": str(erneuerung), "access": str(erneuerung.access_token)}


class TokenErneuernSerializer(TokenRefreshSerializer):
    """Refreshing ends with the password the token was issued under.

    The package compares the check value of the password only on the access
    token ('CHECK_REVOKE_TOKEN'). A refresh token from before the change would
    keep handing out access tokens, each with the old value and thus rejected
    at once. The app would get a fresh token on every attempt, never reach the
    login form and stay "offline". Rejected here, it reads 401 and asks for
    the password.

    Checked **before** the swap: otherwise the old token would land on the
    blocklist and a new one in the list of issued tokens for a request that
    fails.

    **And only a token the server knows.** Every refresh token it hands out
    is entered in the list of issued tokens: on signing in, on renewing
    ('outstand'), and a restore in the administration carries them over. One
    it does not know comes from a state that never saw it: after a dump was
    loaded with psql, a device that signed in or renewed since. The package
    would enter it now and swap it for a fresh one, so 'sign_out_everyone'
    missed it (until 10/2026). Known **for this account**, by its row: the
    token names the account by its user name ('USER_ID_FIELD'), and renamed,
    it named whoever took the old name. For two accounts with an empty
    password field the check value below is the same, and the token of the
    one renewed as the other (until 10/2026).

    **Under the account's lock.** Signing out takes it before it reads the
    tokens ('accounts/sign_out.py'). The package asks the blocklist when it
    reads the token and swaps afterwards. A sign-out that blocked the tokens
    in between left the fresh one open, and a lost device kept its access
    (until 10/2026). Locked first, the blocklist is asked once more: a
    sign-out that came first is seen, one that comes later waits for the swap
    and blocks the fresh token too.

    **And not beyond the bound of the hour** ('neue_pruefen'). A renewal
    issues a token as well. It swaps one for another, so the account holds
    no more of them, but every one counts within the hour.
    """

    def validate(self, attrs: dict[str, Any]) -> dict[str, str]:
        erneuerung = self.token_class(attrs["refresh"])
        name = erneuerung.payload.get(api_settings.USER_ID_CLAIM)
        with transaction.atomic():
            # A missing account is the view's business ('TokenErneuernView').
            konto = gesperrtes_konto(name)
            erneuerung.check_blacklist()
            ausgegeben = OutstandingToken.objects.filter(jti=erneuerung[api_settings.JTI_CLAIM])
            if konto is not None:
                ausgegeben = ausgegeben.filter(user=konto)
            if not ausgegeben.exists():
                raise InvalidToken(
                    str(_("Diese Anmeldung kennt der Server nicht. Bitte melde dich neu an."))
                )
            if konto is not None and erneuerung.payload.get(
                api_settings.REVOKE_TOKEN_CLAIM
            ) != get_md5_hash_password(konto.password):
                raise InvalidToken(str(_("Das Passwort wurde geändert. Bitte melde dich neu an.")))
            if konto is not None:
                neue_pruefen(konto)
            return super().validate(attrs)


class PasswortAendernSerializer(serializers.Serializer):
    """A new password, given against the old one.

    The old one is asked because a token does not say who holds the device. A
    phone left lying would otherwise suffice to take over the account. It is
    checked through 'authenticate()' and not through 'check_password()': that
    way the bolt of 'axes' counts here as at the sign-in. Otherwise this would
    be the one door at which guessing costs nothing.

    The new one passes the same rules as on the website
    ('AUTH_PASSWORD_VALIDATORS'). Their messages go onto the form as they are.
    It comes only once: typing it twice guards against a slip of the finger,
    and that is the business of the form.

    The context carries the account ('konto') and **Django's** request, for the
    same reason as in 'TokenHolenView'.
    """

    altes_passwort = serializers.CharField(write_only=True, trim_whitespace=False)
    neues_passwort = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate_altes_passwort(self, wert: str) -> str:
        konto = self.context["konto"]
        gefunden = authenticate(self.context["request"], username=konto.username, password=wert)
        if gefunden is None:
            raise serializers.ValidationError(_("Das alte Passwort stimmt nicht."))
        return wert

    def validate_neues_passwort(self, wert: str) -> str:
        try:
            validate_password(wert, self.context["konto"])
        except DjangoValidationError as fehler:
            raise serializers.ValidationError(fehler.messages) from fehler
        return wert

    def save(self, **kwargs) -> CustomUser:
        konto = self.context["konto"]
        konto.set_password(self.validated_data["neues_passwort"])
        # Only the password: the language or the appearance may have been
        # changed from another device in the same moment. The way of every
        # new password, which takes a waiting address back as well.
        passwort_speichern(konto)
        return konto


class PasswortOderPasskeySerializer(serializers.Serializer):
    """The password before a step that could take the account, or without one a passkey.

    A token does not say who holds the device. An account with a password
    types it, checked through 'authenticate()', so the bolt of 'axes' counts.
    An account without a usable password has nothing to type. It confirms
    with one of its keys first ('passkey/bestaetigen/'), and the signed
    confirmation comes along ('bestaetigung'). Until 10/2026 such an account
    was asked nothing: a phone signed out by a removal added a key of its own
    with its last access token, or pointed the address at another mailbox.
    Shared by a new address, a new key and the removal of one ('api/passkey.py').

    The context carries the account ('konto') and **Django's** request, for
    the same reason as in 'TokenHolenView'.
    """

    passwort = serializers.CharField(write_only=True, trim_whitespace=False)
    # Not required and empty by default, so a missing one reaches its check and
    # hears the sentence for the person rather than DRF's "required".
    bestaetigung = serializers.CharField(
        write_only=True,
        required=False,
        default="",
        allow_blank=True,
        allow_null=True,
        error_messages={"invalid": BESTAETIGUNG_FEHLT},
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        ohne = "bestaetigung" if self.context["konto"].has_usable_password() else "passwort"
        self.fields.pop(ohne)

    def validate_passwort(self, wert: str) -> str:
        if not passwort_haelt(self.context["request"], self.context["konto"], wert):
            raise serializers.ValidationError(PASSWORT_FALSCH)
        return wert

    def validate_bestaetigung(self, wert: str | None) -> str | None:
        if grund := fehlende_bestaetigung_der_app(wert, self.context["konto"]):
            raise serializers.ValidationError(grund)
        return wert


class EmailAendernSerializer(PasswortOderPasskeySerializer):
    """A new address from the app, given against the password like a new password.

    Or, without a password, against a confirmation with a passkey
    ('PasswortOderPasskeySerializer'). The address itself passes the same
    rules as on the website's form.
    """

    email = serializers.EmailField(max_length=254)

    def validate_email(self, wert: str) -> str:
        konto = self.context["konto"]
        neu = wert.strip()
        if neu.casefold() == konto.email.casefold():
            raise serializers.ValidationError(_("Das ist schon deine Adresse."))
        if adresse_vergeben(neu, konto):
            raise serializers.ValidationError(
                _("Diese Adresse gehört schon zu einem anderen Konto.")
            )
        return neu


class IchSerializer(serializers.Serializer):
    """Returns who is signed in. The PWA shows it and knows when it has to switch."""

    id = serializers.IntegerField()
    benutzername = serializers.CharField(source="username")
    vorname = serializers.CharField(source="first_name")
    nachname = serializers.CharField(source="last_name")
    email = serializers.EmailField()
    #: Whether the account signs in with a password at all. Without one the
    #: app asks for none when the address changes.
    hat_passwort = serializers.SerializerMethodField()
    #: Whether a mail reaches anyone: the app says so before a new address.
    mail = serializers.SerializerMethodField()
    ist_fuehrungskraft = serializers.BooleanField(source="is_staff")
    ist_admin = serializers.BooleanField(source="is_superuser")
    haeuser = serializers.SerializerMethodField()
    darstellung = serializers.CharField()
    sprache = serializers.CharField()
    demo = serializers.SerializerMethodField()
    datenschutz = serializers.SerializerMethodField()
    datenschutz_adresse = serializers.SerializerMethodField()
    impressum = serializers.SerializerMethodField()
    impressum_adresse = serializers.SerializerMethodField()
    rueckmeldeadresse = serializers.SerializerMethodField()
    anwendungsname = serializers.SerializerMethodField()

    def to_representation(self, benutzer):
        # The active appearance once per answer, not once per field: four of
        # them read it. Until 10/2026 that was one query each.
        self._erscheinungsbild = Erscheinungsbild.aktuell()
        return super().to_representation(benutzer)

    def get_hat_passwort(self, benutzer) -> bool:
        return benutzer.has_usable_password()

    def get_mail(self, benutzer) -> bool:
        return mail_moeglich()

    def get_anwendungsname(self, benutzer) -> str:
        """The name from the administration, for the tab of the app.

        The start page the server hands out carries it too, but the service
        worker answers every navigation from its precache, where the name of
        the build stands. So the app sets its title from this answer.
        """
        return self._erscheinungsbild.titel

    def get_datenschutz(self, benutzer) -> bool:
        """Whether the "Privacy" page exists. Without it the account menu shows no link.

        It is emptied in the administration ('Erscheinungsbild'). Like 'demo' it
        hangs on this answer: the app fetches it when signing in.
        """
        return self._erscheinungsbild.hat_datenschutzseite

    def get_datenschutz_adresse(self, benutzer) -> str:
        """Where the page lies. The address follows the server's language ('routes.py').

        The app is built with '/datenschutz/' and does not know whether the server
        speaks English. An old state without the entry takes the German path.
        """
        return reverse("datenschutz")

    def get_impressum(self, benutzer) -> bool:
        """Whether there is a "Legal notice" page. Only where the administration wrote one."""
        return self._erscheinungsbild.hat_impressum

    def get_impressum_adresse(self, benutzer) -> str:
        """Where the page lies, '/impressum/' or '/legal-notice/' ('routes.py')."""
        return reverse("impressum")

    def get_rueckmeldeadresse(self, benutzer) -> str:
        """Where "Report a problem or suggestion" in the account menu writes to.

        Empty means: the menu has no such entry. The app builds the mail itself
        ('services/feedback.js'), because only the device knows what still waits on it.
        """
        return self._erscheinungsbild.rueckmeldeadresse

    def get_haeuser(self, benutzer) -> list[dict]:
        return [{"id": haus.pk, "name": haus.name} for haus in benutzer.haeuser.all()]

    def get_demo(self, benutzer) -> bool:
        """Whether the fictional stock is running here. The app then carries a banner.

        It hangs on this answer and not on every response: the app fetches it
        when signing in and keeps it as long as the same account is signed in.
        The website shows the same through its context processor.
        """
        return ist_demobestand()


class EinstellungenSerializer(serializers.ModelSerializer):
    """What a user changes about themselves through the API.

    Name, role and houses are maintained by management in the web interface.
    Appearance and language are personal preferences and apply everywhere: on
    the website, in the administration and in the app. That is why they hang
    on the account and not on the device, and why the same path leads here
    for both.

    **Written are only the fields sent.** DRF saves the whole row, as the
    authentication read it. What was saved in between went back: a new
    password, a demotion, a deactivation. A deleted account came back with
    its number and its old password, and its tokens worked again (until
    10/2026). A row gone in between answers like a token whose account is
    gone: 401, and the app asks for a new sign-in.
    """

    class Meta:
        model = CustomUser
        fields = ("darstellung", "sprache")

    def update(self, instance: CustomUser, validated_data: dict[str, Any]) -> CustomUser:
        for feld, wert in validated_data.items():
            setattr(instance, feld, wert)
        try:
            # Its own savepoint: the refused write must not spoil an enclosing transaction.
            with transaction.atomic():
                instance.save(update_fields=list(validated_data))
        except CustomUser.NotUpdated as fehler:
            raise AuthenticationFailed(KONTO_WEG, code="user_not_found") from fehler
        return instance
