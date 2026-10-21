from typing import TYPE_CHECKING

from axes.models import AccessAttempt
from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.contrib.sessions.base_session import AbstractBaseSession
from django.db import models
from django.urls import reverse
from django.utils import formats, timezone
from django.utils.translation import gettext, pgettext_lazy
from django.utils.translation import gettext_lazy as _

if TYPE_CHECKING:
    from django.db.models.fields.related_descriptors import RelatedManager


class Darstellung(models.TextChoices):
    """Light, dark or like the device.

    The values are those the Django admin stores in the browser under 'theme'.
    That way the administration reads the same choice without being translated.
    """

    AUTO = "auto", _("Automatisch")
    HELL = "light", _("Hell")
    DUNKEL = "dark", _("Dunkel")


class Differenzfarben(models.TextChoices):
    """How much colour a difference carries.

    On the account and not on the application: colour vision is personal. Red
    and green are not distinct for everyone, and in a check list the zero is
    the most frequent row. Whoever only looks for the deviations does not want
    to skim three hundred green numbers.

    Only concerns the web interface and the sheets. The counting app shows no
    differences, and counters therefore do not even see the choice.
    """

    ALLE = "alle", _("Alles färben")
    ABWEICHUNGEN = "abweichungen", _("Nur Abweichungen")
    AUS = "aus", _("Aus")


class Sprache(models.TextChoices):
    """Which language the application speaks with this account.

    The codes are Django's ('LANGUAGES'), so they can be passed on to
    'translation.override' without conversion. Meant are only the texts of the
    application. What comes from an inventory management system or was entered
    by a manager stays as it is.
    """

    DEUTSCH = "de", "Deutsch"
    ENGLISCH = "en", "English"


def sprachvorgabe() -> str:
    """What a new account starts with: the server's language.

    A callable and not a fixed value, because 'LANGUAGE_CODE' comes from the
    environment and is only known at runtime. Whoever switches the server
    changes nothing about the accounts that already exist. There the language
    was chosen, and a choice is not overwritten remotely.
    """
    return settings.LANGUAGE_CODE if settings.LANGUAGE_CODE in Sprache.values else Sprache.DEUTSCH


class CustomUser(AbstractUser):
    first_name = models.CharField(_("first name"), max_length=150, blank=False)
    last_name = models.CharField(_("last name"), max_length=150, blank=False)
    email = models.EmailField(_("email address"), blank=False, unique=True)

    haeuser = models.ManyToManyField(
        "organisation.Haus",
        related_name="mitarbeitende",
        blank=True,
        verbose_name=_("Häuser"),
        help_text=_(
            "Wo dieser Benutzer üblicherweise zählt. Dort wird er bei Inventuren vorgeschlagen."
        ),
        db_table="haus_zuordnung",
    )

    darstellung = models.CharField(
        max_length=5,
        choices=Darstellung.choices,
        default=Darstellung.AUTO,
        verbose_name=_("Darstellung"),
        # On the account and not in the browser: the installed app on iOS has its
        # own storage, a choice in the browser would never apply there.
        help_text=_(
            "Hell oder dunkel. Die Wahl gilt auf der Website, in der Verwaltung und in der "
            "Zähl-App, auf jedem Gerät."
        ),
    )

    sprache = models.CharField(
        max_length=2,
        choices=Sprache.choices,
        default=sprachvorgabe,
        verbose_name=_("Sprache"),
        # On the account and not in the browser, for the same reason as the
        # appearance: the installed app has its own storage.
        help_text=_(
            "In welcher Sprache die Anwendung mit dir spricht. Die Wahl gilt auf der "
            "Website, in der Verwaltung und in der Zähl-App. Artikel- und Hausnamen "
            "bleiben, wie sie eingetragen sind."
        ),
    )

    differenzfarben = models.CharField(
        max_length=12,
        choices=Differenzfarben.choices,
        default=Differenzfarben.ALLE,
        verbose_name=_("Farben der Differenz"),
        help_text=_(
            "Grün heißt, es passt. Gelb heißt, es ist mehr da. Rot heißt, es fehlt. "
            "„Nur Abweichungen“ lässt die Null farblos. Die Wahl gilt auf der Website "
            "und auf den Blättern."
        ),
    )

    REQUIRED_FIELDS = ["email", "first_name", "last_name"]

    # Created by Django at runtime. Only named here so pyright knows them.
    passkeys: RelatedManager[Passkey]
    sitzungen: RelatedManager[Sitzung]

    def __str__(self):
        return f"{self.first_name} {self.last_name}"

    def get_absolute_url(self):
        return reverse("user_detail", kwargs={"pk": self.pk})

    def has_usable_password(self) -> bool:
        """Django's answer, and an empty field is no password either.

        No hash matches an empty field, so nobody signs in with it. Django
        counts it as usable, the last way into the administration as none
        ('roles.mit_passwort'). Until 10/2026 an account with it that signs
        in with a passkey was asked for a password it could not type, before
        a new address and before another passkey. Every question about the
        password goes through here, Django's own too: such an account gets
        no link from "Forgot your password?", like one without a password.
        """
        return bool(self.password) and super().has_usable_password()

    @property
    def ist_eingeladen(self) -> bool:
        """Created without a password and waiting for the link from the mail.

        No usable password and never signed in: that is the state, nothing
        else is stored for it ('accounts/invitation.py'). Whoever sets the
        password through the link is switched on with it. Not bound to
        'is_active', so that an account someone switched on by hand in the
        meantime still shows what it is waiting for, and the link still works.
        Switched off again, its links die ('invitation.einladungen_entwerten').

        An account that has signed in waits for nothing, with or without a
        password. Until 10/2026 one that signs in with a passkey only counted
        as invited: the list said so, its page offered the invitation again,
        and the link set a password and switched a deactivated account back
        on, an administrator too.
        """
        return not self.has_usable_password() and self.last_login is None

    @property
    def gilt_fuer_alle_haeuser(self) -> bool:
        """Managers and admins are bound to no single house.

        That says nothing about who may count. That is decided solely by the
        assignment on the inventory ('Inventur.darf_zaehlen'). The house
        assignment ('haeuser') is a suggestion, and for these two roles there is
        none: the house form does not even offer them.
        """
        return self.is_staff or self.is_superuser


class PasskeyManager(models.Manager):
    def get_by_natural_key(self, kennung: str):
        return self.get(kennung=kennung)


class Passkey(models.Model):
    """A public key for signing in without a password, bound to a device.

    What the device keeps (iCloud keychain, Google password manager, Windows
    Hello, a security key) is the private half. Here lie the public half, the
    id the browser presents with it, and the counter a cloned key would trip
    over. The name is for the person, so they tell their devices apart.
    Deleting the row withdraws the sign-in. The key on the device stays and
    is simply unknown then. How it is used stands in 'accounts/passkey.py'.

    The id is the natural key: a restored backup renumbers the accounts, and
    the id is what the browser knows.
    """

    benutzer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="passkeys",
        verbose_name=_("Benutzer"),
    )
    #: The credential id as the browser sends it (base64url). Up to 1023 bytes
    #: by the standard, hence the length.
    kennung = models.CharField(max_length=1400, unique=True, verbose_name=_("Kennung"))
    #: The public key in COSE form, as the library expects it back.
    oeffentlicher_schluessel = models.BinaryField(verbose_name=_("Öffentlicher Schlüssel"))
    #: As the device counts, 32 bits without a sign. PostgreSQL's 'integer' holds
    #: 31, so the column is a 'bigint'. Capped instead (until 10/2026), the count
    #: let a clone through once the key had counted past the cap.
    signaturzaehler = models.PositiveBigIntegerField(default=0, verbose_name=_("Signaturzähler"))
    name = models.CharField(max_length=100, verbose_name=_("Name"))
    erstellt_am = models.DateTimeField(auto_now_add=True, verbose_name=_("erstellt am"))
    zuletzt_benutzt = models.DateTimeField(null=True, blank=True, verbose_name=_("zuletzt benutzt"))

    objects = PasskeyManager()

    # Created by Django at runtime. Only named here so pyright knows them.
    benutzer_id: int

    class Meta:
        verbose_name = _("Passkey")
        verbose_name_plural = _("Passkeys")
        ordering = ["erstellt_am"]

    def __str__(self) -> str:
        return f"{self.name} ({self.benutzer})"

    def natural_key(self) -> tuple[str]:
        return (self.kennung,)


class Adresswechsel(models.Model):
    """A new e-mail address that waits for the person's confirmation. Its link names this row.

    Until 10/2026 the link carried the change itself, signed, and nothing
    stored it. So nothing could take it back: a new password, signing out
    everywhere and deactivating left it valid for its three days, a used
    link came back to life once the address went back to the old one, and a
    deactivated account took it without hearing of it. Now the link signs
    only this row's number ('accounts/email_change.py'), and the row goes
    with its use, with a newer request, with every new password, with
    signing out everywhere and with deactivating ('zuruecknehmen').

    One per account: a new request replaces the row, so only the last link
    counts. Not in backups ('django_project/backup.py' names its models): a
    restored state signs everyone out, and a link from before has no
    business coming back with it. Expired rows go with the nightly clean-up.
    """

    konto = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="+",
        verbose_name=_("Konto"),
    )
    neu = models.EmailField(verbose_name=_("Neue Adresse"))
    #: What the account carried when the request came. The link holds only while it still does.
    alt = models.EmailField(verbose_name=_("Bisherige Adresse"))
    #: Who entered it, if not the person. Deleted since, the account is gone from here, and
    #: 'fremd' still says that it was someone else: the mail then names nobody.
    von = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name=_("Eingetragen von"),
    )
    fremd = models.BooleanField(default=False, verbose_name=_("Von jemand anderem eingetragen"))
    erstellt_am = models.DateTimeField(auto_now_add=True, verbose_name=_("erstellt am"))

    # Created by Django at runtime. Only named here so pyright knows them.
    konto_id: int
    von_id: int | None

    class Meta:
        # One German word for both, two English ones: the singular in a context of its own.
        verbose_name = pgettext_lazy("Einzahl", "Adresswechsel")
        verbose_name_plural = _("Adresswechsel")

    def __str__(self) -> str:
        return f"{self.alt} → {self.neu}"

    @classmethod
    def zuruecknehmen(cls, konto: models.Model) -> None:
        """The account's pending change goes, and with it its link.

        Called by whatever ends the account's sign-ins or sets its address:
        every new password ('password_forms.passwort_speichern', the app's as
        well), signing out everywhere and deactivating ('sign_out.py'), a
        newer request and an address written at once ('email_change.py').
        """
        cls.objects.filter(konto=konto).delete()


class Sitzung(AbstractBaseSession):
    """A session of the website that names its account in a column of its own.

    Django's own table ('django_session') says whose a session is only inside
    the signed content. To end an account's sessions when a passkey goes,
    every open session was decoded, one by one (until 10/2026). The store
    writes the account here on every save, read from that content
    ('session_store.py'), so the column says what the content says, and an
    account's sessions are a lookup in its index. A visitor who is not signed
    in has none. The settings name the store ('SESSION_ENGINE'), and
    'accounts/0003' moved the open sessions over from Django's table.
    """

    #: Without a constraint in the database. A session is saved when its
    #: request ends, whatever happened to the account meanwhile. With one, an
    #: account deleted in between turned that save into an error, and Django's
    #: 'create()' takes every such error for a key that is taken and tries the
    #: next key, without end. A row naming an account that is gone signs
    #: nobody in: Django finds no account and reads the session as anonymous.
    #: Deleting an account still takes its sessions along, Django does that
    #: itself ('CASCADE').
    konto = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        db_constraint=False,
        related_name="sitzungen",
        verbose_name=_("Konto"),
    )

    # Created by Django at runtime. Only named here so pyright knows them.
    konto_id: int | None

    class Meta:
        verbose_name = _("Sitzung")
        verbose_name_plural = _("Sitzungen")

    def __str__(self) -> str:
        """Its expiry, not its key: the key is the sign-in itself.

        Before an account is deleted, the administration lists its sessions,
        each by this text. Django's own text is the key.
        """
        ende = formats.date_format(timezone.localtime(self.expire_date), "SHORT_DATETIME_FORMAT")
        return gettext("gültig bis %(ende)s") % {"ende": ende}

    @classmethod
    def get_session_store_class(cls):
        from .session_store import SessionStore  # noqa: PLC0415  (the store imports the model)

        return SessionStore


class Anmeldeversuch(models.Model):
    """One knock at the decoy under '/admin/': who, from where, with what, at which address.

    Taken over from django-admin-honeypot together with the decoy
    ('accounts/honeypot.py'). Until 10/2026 this was a proxy onto the
    package's model. The table keeps the package's name and columns, so a
    database from before needs no migration, and an old backup names the
    model under its old label ('backup_upgrade'). The password is not
    recorded. The decoy runs the hasher over it once, for the time a real
    check takes, and keeps nothing. Nor the session's key, which for a
    signed-in visitor would be their sign-in.
    """

    #: The package's primary key, an integer. Named so the column stays as it is.
    id = models.AutoField(primary_key=True, verbose_name=_("ID"))
    username = models.CharField(_("Benutzername"), max_length=255, blank=True, null=True)  # noqa: DJ001  (the package's column)
    ip_address = models.GenericIPAddressField(_("IP-Adresse"), blank=True, null=True)
    #: Empty since 10/2026: for a signed-in visitor the key was the sign-in itself
    #: ('honeypot.aufzeichnen'). The column stays, as the package left it.
    session_key = models.CharField(_("Sitzung"), max_length=50, blank=True, null=True)  # noqa: DJ001  (the package's column)
    user_agent = models.TextField(_("Browserkennung"), blank=True, null=True)  # noqa: DJ001  (the package's column)
    timestamp = models.DateTimeField(_("Zeitpunkt"), auto_now_add=True)
    path = models.TextField(_("Adresse"), blank=True, null=True)  # noqa: DJ001  (the package's column)

    class Meta:
        db_table = "admin_honeypot_loginattempt"
        verbose_name = _("Anmeldeversuch am falschen Admin")
        verbose_name_plural = _("Anmeldeversuche am falschen Admin")
        ordering = ("-timestamp",)
        # The decoy counts every sender's attempts of the last hour before it
        # records one more ('honeypot.aufzeichnen'). Without the index that was
        # a pass over the whole table on every knock (until 10/2026).
        indexes = [models.Index(fields=["ip_address", "timestamp"], name="anmeldeversuch_ip_zeit")]

    def __str__(self) -> str:
        return self.username or "–"


class Fehlversuch(AccessAttempt):
    """The counted failed attempts at the **real** login, named in German.

    A proxy onto the package's model, so the administration does not show
    "Access attempts" under "Axes". No table of its own arises from it.

    The list is visible because it answers a question nobody else can answer:
    **who is locked out right now**, and because deleting a row lifts the
    lock. Without it only waiting or a command on the command line would remain.
    """

    class Meta:
        proxy = True
        verbose_name = _("Fehlversuch bei der Anmeldung")
        verbose_name_plural = _("Fehlversuche bei der Anmeldung")
        ordering = ("-attempt_time",)


class Passwortanfrage(models.Model):
    """One "Forgot your password?" request, for the throttle.

    'axes' counts only 'authenticate()'. The reset had no limit: every POST
    with a known address produced a mail, and a flood of them cost the
    operation its mails (until 10/2026). Address and sender are counted per
    hour; what is over the limit gets the same "done" page and no mail. The
    rows go after 30 days ('manage.py cleanup').
    """

    #: Lower-cased, so the count does not depend on the spelling.
    email = models.CharField(max_length=254, verbose_name=_("E-Mail-Adresse"))
    ip_address = models.GenericIPAddressField(null=True, blank=True, verbose_name=_("Adresse"))
    zeitpunkt = models.DateTimeField(auto_now_add=True, verbose_name=_("Zeitpunkt"))

    class Meta:
        verbose_name = _("Anfrage „Passwort vergessen?“")
        verbose_name_plural = _("Anfragen „Passwort vergessen?“")
        indexes = [
            models.Index(fields=["email", "zeitpunkt"], name="passwortanfrage_email_zeit"),
            models.Index(fields=["ip_address", "zeitpunkt"], name="passwortanfrage_ip_zeit"),
        ]

    def __str__(self) -> str:
        return f"{self.email} ({self.zeitpunkt:%Y-%m-%d %H:%M})"
