from django import forms
from django.contrib.auth import authenticate
from django.contrib.auth.forms import UserChangeForm, UserCreationForm
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from django_project.mail import mail_moeglich
from django_project.mixin_bases import ModellformularBasis
from django_project.roles import ist_administrator, ist_letzter_administrator

from .email_change import adresse_vergeben, sofort
from .invitation import gueltig_fuer_tage
from .models import CustomUser

HAUS_WIDGET = {"haeuser": forms.CheckboxSelectMultiple}

#: The last active administrator is neither deactivated nor demoted nor deleted ('roles.py').
LETZTER_ADMINISTRATOR_BLEIBT = _(
    "Das ist der letzte Administrator. "
    "Ohne einen Administrator kommt niemand mehr in die Verwaltung."
)

#: The last administrator keeps a way in: without a passkey the password stays.
LETZTER_ADMINISTRATOR_BEHAELT_PASSWORT = _(
    "Das ist der letzte Administrator, und er hat keinen Passkey. Ohne Passwort käme "
    "niemand mehr in die Verwaltung."
)

#: And the other way round: without a password the last passkey stays.
LETZTER_ADMINISTRATOR_BEHAELT_PASSKEY = _(
    "Das ist der letzte Administrator, und er hat kein Passwort. Ohne diesen Passkey käme "
    "niemand mehr in die Verwaltung."
)

#: On the site, where the form does not offer the administrator role.
ADMINISTRATOR_BLEIBT_FUEHRUNGSKRAFT = _(
    "Ein Administrator ist immer auch Führungskraft. Zurückstufen geht in der Verwaltung."
)

#: Under the address field, where the change waits for the person's confirmation.
ADRESSE_NACH_BESTAETIGUNG = _(
    "An eine neue Adresse geht erst eine Mail mit einem Link. Die Adresse gilt, sobald die "
    "Person ihn bestätigt hat."
)

#: Under the address field of a manager, who does not change it.
ADRESSE_NUR_SELBST = _(
    "Die Adresse ändert die Person selbst über das Kontomenü, oder ein Administrator."
)

#: An address in another spelling counts as the same.
ADRESSE_VERGEBEN = _("Diese Adresse gehört schon zu einem anderen Konto.")

#: An account deleted while its form was checked, on the site and in the administration.
KONTO_GELOESCHT = _(
    "Das Konto „%(name)s“ gibt es nicht mehr. Deine Änderung ist nicht gespeichert."
)


class EindeutigeAdresseMixin(ModellformularBasis):
    """One address, one account, whatever its spelling.

    The database compares exactly ('unique=True'), the own change of address
    without regard to case ('adresse_vergeben'). Creating an account and the
    forms of the administrators compared exactly until 10/2026:
    "ANNA@example.org" next to "anna@example.org" made two accounts with one
    mailbox, and "Forgot your password?" sent a link for each. Checked only
    when the address changes, so an older pair does not lock every other edit.
    """

    def clean_email(self) -> str:
        email: str = self.cleaned_data["email"]
        konto = self.instance
        assert isinstance(konto, CustomUser)
        if email.casefold() != (konto.email or "").casefold() and adresse_vergeben(email, konto):
            raise ValidationError(ADRESSE_VERGEBEN)
        return email


class RollenbeschriftungMixin(ModellformularBasis):
    """Names the roles the way they are called in the house, and keeps them consistent.

    Django labels 'is_staff' as "staff status", which is misleading, since only
    staff have access to the site at all. What is meant is the **manager**: they
    may use the web interface. 'is_superuser' is accordingly the
    **administrator**. Only the administration offers that box.

    An administrator is always a manager too. Without 'is_staff' the
    middleware sends the account to the block page, the administration
    included. Until 10/2026 such an administrator could arise in the
    administration and still counted for the protection of the last one
    ('roles.py'). Ticking the administrator now sets the manager along. Where
    the form lacks the box (the site), the manager cannot be taken from an
    administrator.

    'static/js/role_choice.js' hides the house assignment as soon as the box is
    ticked. A manager is bound to no single house, and their assignment to an
    inventory happens explicitly there. Nothing is removed in the process:
    whoever turns from counter into manager keeps their assignment in case it
    goes back.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if (feld := self.fields.get("is_staff")) is not None:
            feld.label = _("Führungskraft")
            feld.help_text = _(
                "Darf die Weboberfläche benutzen: Stammdaten pflegen, Inventuren anlegen, "
                "prüfen und abschließen."
            )
        if (feld := self.fields.get("is_superuser")) is not None:
            feld.label = _("Administrator")
            feld.help_text = _(
                "Erreicht zusätzlich die Verwaltung und die Backups. Ein Administrator ist "
                "immer auch Führungskraft."
            )
        if (feld := self.fields.get("is_active")) is not None:
            # Django's own text advises deactivating instead of deleting. Here it
            # is the other way round, as the help and the privacy text say: whoever
            # leaves is deleted (until 10/2026 the field contradicted both). On the
            # form and not on the model, which would need a migration.
            feld.help_text = _(
                "Ohne Haken wird das Konto überall abgemeldet und kann sich nicht mehr "
                "anmelden, auch nicht in der Zähl-App. Gedacht für eine Pause. Wer den "
                "Betrieb verlässt, wird gelöscht."
            )

    def clean(self):
        super().clean()
        daten = self.cleaned_data
        if "is_superuser" in self.fields:
            if daten.get("is_superuser"):
                daten["is_staff"] = True
        elif (
            ist_administrator(self.instance)
            and "is_staff" in self.fields
            and not daten.get("is_staff")
        ):
            self.add_error("is_staff", ADMINISTRATOR_BLEIBT_FUEHRUNGSKRAFT)
        # The instance still holds the stored values here: the form writes
        # into it only after 'clean()'. Asked without a lock, for the early
        # message. The step that saves asks once more under the lock
        # ('letzter_administrator_geht').
        if self.instance.pk and ist_letzter_administrator(self.instance):
            self.letzten_administrator_halten()
        return daten

    def letzten_administrator_halten(self) -> bool:
        """Puts the message on each field that would take the last administrator's way in.

        Returns whether there was one.
        """
        felder = [
            feld
            for feld in ("is_active", "is_superuser")
            if feld in self.fields and not self.cleaned_data.get(feld)
        ]
        for feld in felder:
            self.add_error(feld, LETZTER_ADMINISTRATOR_BLEIBT)
        return bool(felder)


class NurGeaendertesMixin(ModellformularBasis):
    """An edit of the account writes the columns it changed. Nothing else of the row.

    Django's 'ModelForm.save' writes the whole row as the request read it when
    it began. What was saved in between went back: a new password, a
    deactivation, a sign-in, another language. An account deleted in between
    came back (until 10/2026). The step that saves writes these columns
    ('update_fields') and the relations through 'save_m2m', as the form does.
    """

    def geaenderte_spalten(self) -> list[str]:
        """The columns of the row the form changes, by what was typed and by what 'clean' set.

        Django's 'changed_data' names what was typed or ticked differently. A
        value 'clean' sets itself is not in it: ticking the administrator ticks
        the manager ('RollenbeschriftungMixin'). So the cleaned value counts as
        well, against the start of the form as Django holds it, without the
        microseconds a field does not show. A relation to many is no column.
        """
        spalten = {feld.name for feld in self.instance._meta.concrete_fields}
        getippt = set(self.changed_data)
        daten = self.cleaned_data
        return [
            name
            for name, feld in self.fields.items()
            if name in spalten
            and (name in getippt or daten.get(name) != self.get_initial_for_field(feld, name))
        ]

    @property
    def angezeigter_name(self) -> str:
        """The account's name as the page showed it. The instance already carries what was typed."""
        return f"{self.initial.get('first_name', '')} {self.initial.get('last_name', '')}".strip()

    def gesperrt_gelesen(self) -> CustomUser | None:
        """The account's row, locked until the transaction ends and read only then. None if gone.

        Without the key, as every writer of the account locks it
        ('accounts/sign_out.py'), and after the administrators, whom
        'letzter_administrator_geht' locks first. The state before the change
        is then what is stored, and a deletion waits until the change is
        written, houses included.
        """
        return CustomUser.objects.select_for_update(no_key=True).filter(pk=self.instance.pk).first()


class CustomUserCreationForm(EindeutigeAdresseMixin, RollenbeschriftungMixin, UserCreationForm):
    """An invitation by mail, or a password now.

    **With a mail server the invitation is the normal way**: the person sets
    the password themselves, and nobody else ever knows it. The account
    arises without a usable password and inactive, and the link in the mail
    switches it on ('accounts/invitation.py'). Whoever wants to give a
    password anyway ticks "Set the password yourself". Only then do the two
    fields appear ('static/js/password_choice.js') and count. A password
    without the tick is refused instead of guessed at: without JavaScript the
    fields stand there, and it stays unclear what was meant.

    **Without a mail server the tick does not exist.** The password fields are
    required like in any other form, and nothing on the page talks of an
    invitation: the mail would end in the log, and the person would wait for
    nothing.

    Django's form hashes whatever stands in the first field when saving, an
    empty text too. Hence the own saving step.
    """

    passwort_setzen = forms.BooleanField(required=False, label=_("Passwort selbst vergeben"))

    class Meta:
        model = CustomUser
        # The language too: the invitation goes out in it. Until 10/2026 a new
        # account spoke the server's language, whoever created it.
        fields = ("username", "email", "first_name", "last_name", "is_staff", "haeuser", "sprache")
        widgets = HAUS_WIDGET

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        mit_mail = mail_moeglich()
        if mit_mail:
            self.fields["passwort_setzen"].help_text = _(
                "Sonst bekommt die Person eine E-Mail mit einem Link und setzt ihr Passwort "
                "selbst. Der Link gilt %(tage)s Tage."
            ) % {"tage": gueltig_fuer_tage()}
            for name in ("password1", "password2"):
                self.fields[name].required = False
            # The tick stands before the two fields it opens.
            andere = [name for name in self.fields if name not in ("password1", "password2")]
            self.order_fields([*andere, "password1", "password2"])
        else:
            del self.fields["passwort_setzen"]
        # The admin's add form leaves the field out ('add_fieldsets').
        if "sprache" in self.fields:
            # Not required: a form without the field (the admin, an older
            # client) gets the server's language, as before.
            self.fields["sprache"].required = False
            self.fields["sprache"].help_text = (
                _(
                    "Die Sprache der Einladung und der Oberfläche. Die Person kann sie später "
                    "selbst umstellen."
                )
                if mit_mail
                else _("Die Sprache der Oberfläche. Die Person kann sie später selbst umstellen.")
            )

    def clean_sprache(self) -> str:
        return self.cleaned_data.get("sprache") or self.instance.sprache

    @property
    def einladen(self) -> bool:
        """Whether the mail is due: there is a mail server, and the tick stayed empty."""
        daten = getattr(self, "cleaned_data", {})
        return "passwort_setzen" in self.fields and not daten.get("passwort_setzen")

    def clean(self) -> dict:
        """With the tick both fields, without it none. Without a mail server Django decides.

        There the two fields are simply required, and Django names a missing one.
        """
        super().clean()
        daten = self.cleaned_data
        if "passwort_setzen" not in self.fields:
            return daten
        if daten.get("passwort_setzen"):
            for name in ("password1", "password2"):
                if not daten.get(name) and name not in self.errors:
                    self.add_error(name, self.fields[name].error_messages["required"])
        elif daten.get("password1") or daten.get("password2"):
            self.add_error(
                "passwort_setzen",
                _(
                    "Für ein eigenes Passwort den Haken setzen. Sonst die Felder leer lassen, "
                    "dann geht die Einladung hinaus."
                ),
            )
        return daten

    def set_password_and_save(self, user, password_field_name="password1", commit=True):
        """Django's own step, except that no password means no password.

        Django would hash the empty text, and that would be a password one can
        sign in with. Without one the account also waits switched off until the
        link from the mail switches it on.
        """
        if self.einladen:
            user.set_unusable_password()
            user.is_active = False
        else:
            user.set_password(self.cleaned_data[password_field_name])
        if commit:
            user.save()
        return user


class CustomUserAdminChangeForm(
    NurGeaendertesMixin, EindeutigeAdresseMixin, RollenbeschriftungMixin, UserChangeForm
):
    class Meta:
        model = CustomUser
        fields = "__all__"
        widgets = HAUS_WIDGET


class CustomUserChangeForm(
    NurGeaendertesMixin, EindeutigeAdresseMixin, RollenbeschriftungMixin, forms.ModelForm
):
    """The user form of the site. The address is for administrators only.

    A manager sees it, greyed out: a disabled field keeps its stored value
    whatever the browser sends. An administrator changes it, and with a mail
    server the change waits for the person's confirmation
    ('accounts/email_change.py'). The view does that, the form only says so.
    """

    class Meta:
        model = CustomUser
        fields = (
            "username",
            "email",
            "first_name",
            "last_name",
            "is_staff",
            "is_active",
            "haeuser",
        )
        widgets = HAUS_WIDGET

    def __init__(self, *args, bearbeiter=None, **kwargs):
        super().__init__(*args, **kwargs)
        feld = self.fields["email"]
        if bearbeiter is not None and not ist_administrator(bearbeiter):
            feld.disabled = True
            feld.help_text = ADRESSE_NUR_SELBST
        elif self.instance.pk and not sofort(self.instance):
            feld.help_text = ADRESSE_NACH_BESTAETIGUNG


class EmailAendernForm(forms.Form):
    """The person's own new address, given against the password or a passkey.

    The password is asked because a session does not say who sits at the
    device. A browser left open would otherwise suffice to point the account
    at a stranger's mailbox, and "Forgot your password?" would do the rest.
    Checked through 'authenticate()', so the lock of 'axes' counts here as at
    the sign-in.

    An account without a usable password has nothing to type. It confirms
    with one of its passkeys first, on the same page, and until 10/2026 it
    was asked nothing at all. The view says whether the confirmation holds
    ('unbestaetigt', the sentence of 'accounts.passkey.fehlende_bestaetigung',
    empty where it holds). The form cannot ask itself: that module imports
    this one.
    """

    email = forms.EmailField(label=_("Neue E-Mail-Adresse"), max_length=254)
    passwort = forms.CharField(
        label=_("Dein Passwort"),
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
        help_text=_("Zur Sicherheit, damit niemand die Adresse ändert, der nur dein Gerät hat."),
    )

    def __init__(self, *args, konto: CustomUser, request, unbestaetigt: str = "", **kwargs):
        super().__init__(*args, **kwargs)
        self.konto = konto
        self.request = request
        self.unbestaetigt = unbestaetigt
        if not konto.has_usable_password():
            del self.fields["passwort"]

    def clean(self):
        super().clean()
        if "passwort" not in self.fields and self.unbestaetigt:
            raise ValidationError(self.unbestaetigt)
        return self.cleaned_data

    def clean_email(self) -> str:
        neu = self.cleaned_data["email"].strip()
        if neu.casefold() == self.konto.email.casefold():
            raise ValidationError(_("Das ist schon deine Adresse."))
        if adresse_vergeben(neu, self.konto):
            raise ValidationError(ADRESSE_VERGEBEN)
        return neu

    def clean_passwort(self) -> str:
        wert = self.cleaned_data["passwort"]
        if authenticate(self.request, username=self.konto.username, password=wert) is None:
            raise ValidationError(_("Das Passwort stimmt nicht."))
        return wert
