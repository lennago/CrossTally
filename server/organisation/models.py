from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import TYPE_CHECKING

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models, transaction
from django.template import TemplateSyntaxError
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from django_project.deletion import zum_loeschen_sperren
from django_project.labels import Beschriftung
from django_project.renaming import umbenennen

from .images import STANDARDHINTERGRUND
from .mail_texts import (
    VARIABLEN,
    Mailart,
    ausgefuellt,
    betreffzeile,
    mitgeliefert,
    pruefen,
    sprache_von,
)
from .privacy_text import datenschutztext_de, datenschutztext_en

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    # Only for pyright: without the mypy plugin of the django-stubs, reverse
    # relations, '_id' columns and 'get_…_display' are unknown. Nothing of this
    # is loaded at runtime. The dependency direction of the apps stays as it is.
    from collections.abc import Callable, Mapping

    from django.contrib.auth.models import AbstractUser
    from django.db.models.fields.related_descriptors import ManyRelatedManager, RelatedManager

    from inventur.models import Zaehlposition
    from katalog.models import Erwartung


#: "#rrggbb", nothing else. The colour lands unchanged in the image and in no
#: template, still nothing else should get in in the first place.
hex_farbe = RegexValidator(r"^#[0-9a-fA-F]{6}$", _("Erwartet wird eine Farbe als „#rrggbb“."))


class Haus(models.Model):
    name = models.CharField(max_length=100, unique=True, verbose_name=_("Name"))

    # Created by Django at runtime. Only named here so pyright knows them.
    bereiche: RelatedManager[Bereich]
    mitarbeitende: ManyRelatedManager[AbstractUser, models.Model]

    class Meta:
        verbose_name = _("Haus")
        verbose_name_plural = _("Häuser")
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    def get_absolute_url(self):
        return reverse("haus_detail", kwargs={"pk": self.pk})

    def alle_orte(self):
        """The locations of this house across all areas.

        Deliberately a method and no 'orte' reference: 'Ort' hangs on the area,
        and a name that looks like a relation could not be prefetched. The silent
        N+1 would only show up in operation.
        """
        return Ort.objects.filter(bereich__haus=self)

    def zugehoerige_benutzer(self):
        """The staff of the house plus all managers and admins.

        Assigned, or as manager or admin everywhere anyway
        ('CustomUser.gilt_fuer_alle_haeuser'). **Not** the answer to who may
        count. That is decided by the assignment on the individual inventory
        ('Inventur.darf_zaehlen'), into which managers are also entered
        explicitly. That is why the method is no longer called
        'zaehlberechtigte': whoever stands here is at most suggested there.
        """

        return (
            get_user_model()
            .objects.filter(
                models.Q(haeuser=self) | models.Q(is_staff=True) | models.Q(is_superuser=True)
            )
            .distinct()
            .order_by("last_name", "first_name")
        )


class Bereich(models.Model):
    """Group of locations within a house: counter, storage, cooling.

    Purely for structure: counting happens at the location, the expectations
    hang there too. The level makes the counting list readable: a counter
    consists of front, back and freezer, and when counting one wants those
    together instead of scattered in an alphabetical list of locations.
    """

    name = models.CharField(max_length=100, verbose_name=_("Name"))
    haus = models.ForeignKey(
        Haus, on_delete=models.CASCADE, related_name="bereiche", verbose_name=_("Haus")
    )

    # Created by Django at runtime. Only named here so pyright knows them.
    haus_id: int
    orte: RelatedManager[Ort]

    class Meta:
        verbose_name = _("Bereich")
        verbose_name_plural = _("Bereiche")
        ordering = ["haus__name", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["haus", "name"],
                name="bereichsname_je_haus_eindeutig",
                violation_error_message=_("Diesen Bereich gibt es in dem Haus schon."),
            )
        ]

    def __str__(self) -> str:
        return f"{self.haus.name} - {self.name}"

    def save(self, *args, **kwargs):
        """A new name reaches the rows at its locations, the app groups its areas by it.

        Until 10/2026 a renamed area stood twice in the app until a full sync
        ('django_project.renaming').
        """
        with umbenennen(
            self,
            kwargs.get("update_fields"),
            lambda: zeilen_der_orte_sperren(self.orte.values_list("pk", flat=True)),
        ):
            super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("bereich_detail", kwargs={"pk": self.pk})


class Ort(models.Model):
    name = models.CharField(max_length=100, verbose_name=_("Name"))
    bereich = models.ForeignKey(
        Bereich, on_delete=models.CASCADE, related_name="orte", verbose_name=_("Bereich")
    )

    # Created by Django at runtime. Only named here so pyright knows them.
    bereich_id: int
    erwartungen: RelatedManager[Erwartung]

    class Meta:
        verbose_name = _("Ort")
        verbose_name_plural = _("Orte")
        ordering = ["bereich__haus__name", "bereich__name", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["bereich", "name"],
                name="ortsname_je_bereich_eindeutig",
                violation_error_message=_("Diesen Ort gibt es in dem Bereich schon."),
            )
        ]

    def __str__(self) -> str:
        return f"{self.bereich.haus.name} - {self.bereich.name} - {self.name}"

    def save(self, *args, **kwargs):
        """A new name reaches the rows at the location ('django_project.renaming')."""
        with umbenennen(
            self, kwargs.get("update_fields"), lambda: zeilen_der_orte_sperren([self.pk])
        ):
            super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("ort_detail", kwargs={"pk": self.pk})

    @property
    def anzeigename(self) -> str:
        """The own name, without house and area in front.

        A location is called what the house named it and is not translated. The
        property exists nonetheless: the shared pages for splitting and merging
        show locations and product families with the same row, and for the family
        the translation of "Not countable" hangs on it.
        """
        return self.name

    @property
    def haus(self) -> Haus:
        """The house of the location, through the area, not as a second field.

        In queries the path is accordingly 'bereich__haus', not 'haus'.
        """
        return self.bereich.haus


#: The name of the row the application brings itself. Stored, not translated,
#: like "Not countable": on it hangs that it cannot be changed.
STANDARD = "Standard"

#: What the application is called as long as nobody gives it an own name. The
#: build of the counting app carries the same ('vite.config.js'), Django swaps
#: it when serving for the one from the active version.
STANDARDNAME = "CrossTally"


def zeilen_der_orte_sperren(orte: Iterable[int]) -> models.QuerySet[Zaehlposition] | None:
    """For a new name of these locations or of their area: the locations, then the rows at them.

    A new row of the app locks its location before it reads it
    ('api.serializers.counting.ort_sperren'). It then waits for the rename
    and reads the new name. Without the key, as there: the locations stay.
    Each by number and without 'Meta.ordering', which would lock area and
    house along.
    """
    from inventur.models import Zaehlposition  # noqa: PLC0415  (the inventory lies above)

    gesperrt = list(
        Ort.objects.select_for_update(no_key=True)
        .filter(pk__in=list(orte))
        .order_by("pk")
        .values_list("pk", flat=True)
    )
    zeilen = list(
        Zaehlposition.objects.select_for_update()
        .filter(ort_id__in=gesperrt)
        .order_by("pk")
        .values_list("pk", flat=True)
    )
    return Zaehlposition.objects.filter(pk__in=zeilen) if zeilen else None


def orte_der_bereiche_sperren(bereiche: Iterable[int]) -> None:
    """To delete these areas: their locations, for the area's page and the administration.

    With an area its locations go, and a new row of the app locks its
    location, not the area ('api.serializers.counting.ort_sperren').
    """
    zum_loeschen_sperren(
        Ort, Ort.objects.filter(bereich_id__in=list(bereiche)).values_list("pk", flat=True)
    )


class ErscheinungsbildManager(models.Manager):
    def get_by_natural_key(self, name: str):
        return self.get(name=name)


def in_sprache(deutsch: str, englisch: str, sprache: str) -> str:
    """The text in the account's language, otherwise in the other, otherwise empty.

    Whoever filled in only one language means it for all, because a text in
    the other language says more than none.
    """
    eigener = {"de": deutsch, "en": englisch}.get(sprache[:2], "")
    return eigener or deutsch or englisch


class Erscheinungsbild(models.Model):
    """Name, logo, operator, legal notice and privacy text, in versions to switch between.

    There are any number of rows, **exactly one is active**, and the pages read
    the active one. The row **"Standard"** comes with the application itself:
    the name "CrossTally", the shipped logo, no operator, the bundled privacy
    text.
    It cannot be changed and not deleted, only copied. Whoever wants something
    of their own copies it and changes the copy. That way the shipped state
    always stays reachable, and a switch back is one click. If the row is
    missing (old backup, reset demo), it is recreated on the first access
    ('aktuell').

    Here also stand **the name of the application, operator, contact, legal
    notice and privacy text**, not in the '.env': the environment describes
    the operation of the server, the database the business using the
    application. The name belongs to the logo it stands next to and changes
    with the version. The privacy text is prefilled with the bundled one and is
    extended, changed or replaced. The legal notice starts empty: the shipped
    application has no operator to name, and an internal tool usually needs
    none ('DEPLOYMENT.md'). Whoever needs one writes it whole, there is no
    frame around it. An own text thus needs neither a changed template nor an
    own image, and the backup takes it along like the logo. **Empty means: no
    page.** Then there is no link in the footer and in the account menu of the
    counting app either.

    The **address for feedback** starts empty for the same reason as the legal
    notice. A default would ship in the published image, and every other
    business would send its reports, with names and quantities, to an address
    it never chose (decision of 2026-10-16). Without one the account menu has
    no "Report a problem or suggestion".

    The images lie as bytes **in the database** and not in a directory of their
    own. The reason is the backup: 'dumpdata' writes a 'BinaryField' as Base64
    and thus takes the logo along. A volume next to it would be the only part
    of operation a backup does not capture. It is not large, a few dozen
    kilobytes per size.

    One file is uploaded, all other sizes are derived. How and why is in
    'images.py'.
    """

    name = models.CharField(
        max_length=100,
        unique=True,
        default=STANDARD,
        verbose_name=_("Name"),
        help_text=_("Wie diese Fassung in der Liste heißt."),
    )
    aktiv = models.BooleanField(
        default=False,
        verbose_name=_("aktiv"),
        help_text=_("Die aktive Fassung sehen alle. Gewählt wird sie in der Liste."),
    )
    anwendungsname = models.CharField(
        max_length=60,
        blank=True,
        default=STANDARDNAME,
        verbose_name=_("Name der Anwendung"),
        help_text=_(
            "Steht neben dem Logo in der Leiste, im Browsertab und am Icon der Zähl-App. "
            "Leer lassen heißt „CrossTally“."
        ),
    )
    #: 512 x 512, the source for everything else and at the same time the largest icon.
    logo = models.BinaryField(blank=True, verbose_name=_("Logo"))
    icon_192 = models.BinaryField(blank=True, verbose_name=_("Icon 192 px"))
    apple_touch_icon = models.BinaryField(blank=True, verbose_name=_("Apple-Touch-Icon"))
    favicon_32 = models.BinaryField(blank=True, verbose_name=_("Favicon 32 px"))
    icon_maskable_512 = models.BinaryField(blank=True, verbose_name=_("maskierbares Icon 512 px"))
    icon_maskable_192 = models.BinaryField(blank=True, verbose_name=_("maskierbares Icon 192 px"))
    hintergrund = models.CharField(
        max_length=7,
        default=STANDARDHINTERGRUND,
        validators=[hex_farbe],
        verbose_name=_("Hintergrund für den Startbildschirm"),
        help_text=_(
            "Als „#rrggbb“. Gilt nur für das Icon auf dem Startbildschirm von Android. "
            "Das Gerät schneidet dessen Rand in seiner eigenen Form weg, deshalb braucht "
            "es einen deckenden Grund. Überall sonst bleibt das Logo durchsichtig."
        ),
    )
    betreiber = models.CharField(
        max_length=200,
        blank=True,
        verbose_name=_("Betreiber"),
        help_text=_(
            "Wer die Anwendung betreibt. Steht im Fuß jeder Seite und auf der Seite "
            "„Datenschutz“. Leer lassen geht."
        ),
    )
    betreiber_kontakt = models.CharField(
        max_length=200,
        blank=True,
        verbose_name=_("Kontakt"),
        help_text=_("E-Mail oder Telefon, steht hinter dem Namen."),
    )
    datenschutz_kontakt = models.CharField(
        max_length=200,
        blank=True,
        verbose_name=_("Anlaufstelle für den Datenschutz"),
        help_text=_("Wer Auskunft über die Daten gibt, falls das jemand anderes ist."),
    )
    rueckmeldeadresse = models.EmailField(
        blank=True,
        verbose_name=_("Adresse für Rückmeldungen"),
        help_text=_(
            "An diese Adresse geht „Problem oder Wunsch melden“ aus dem Kontomenü. Leer "
            "lassen heißt: kein solcher Eintrag."
        ),
    )
    datenschutztext = models.TextField(
        blank=True,
        default=datenschutztext_de,
        verbose_name=_("Datenschutztext (Deutsch)"),
        help_text=_(
            "Vorbelegt mit dem mitgelieferten Text. Ergänzen, ändern oder ganz ersetzen. "
            "Leer lassen nimmt die Seite „Datenschutz“ samt Verweis heraus, sobald auch das "
            "englische Feld leer ist. Steht nur dort ein Text, sehen ihn alle. Eine Leerzeile "
            "trennt Absätze, ein einfacher Zeilenumbruch bleibt stehen, „## “ beginnt eine "
            "Überschrift, „- “ einen Aufzählungspunkt, **so** schreibt fett."
        ),
    )
    datenschutztext_en = models.TextField(
        blank=True,
        default=datenschutztext_en,
        verbose_name=_("Datenschutztext (Englisch)"),
        help_text=_(
            "Für Konten in englischer Sprache, dasselbe Format. Leer lassen zeigt ihnen den "
            "deutschen Text."
        ),
    )
    impressum = models.TextField(
        blank=True,
        verbose_name=_("Impressum (Deutsch)"),
        help_text=_(
            "Steht auf einer eigenen Seite „Impressum“, verlinkt im Fuß jeder Seite und im "
            "Kontomenü der Zähl-App. Leer lassen heißt: kein Impressum, sobald auch das "
            "englische Feld leer ist. Steht nur dort ein Text, sehen ihn alle. Dasselbe Format "
            "wie beim Datenschutztext: eine Leerzeile trennt Absätze, ein einfacher Zeilenumbruch "
            "bleibt stehen, „## “ beginnt eine Überschrift, „- “ einen Aufzählungspunkt, **so** "
            "schreibt fett."
        ),
    )
    impressum_en = models.TextField(
        blank=True,
        verbose_name=_("Impressum (Englisch)"),
        help_text=_(
            "Für Konten in englischer Sprache, dasselbe Format. Leer lassen zeigt ihnen den "
            "deutschen Text."
        ),
    )
    geaendert_am = models.DateTimeField(auto_now=True, verbose_name=_("geändert am"))

    objects = ErscheinungsbildManager()

    #: What a copy takes along: everything except name, switch and timestamp.
    KOPIERTE_FELDER = (
        "anwendungsname",
        "logo",
        "icon_192",
        "apple_touch_icon",
        "favicon_32",
        "icon_maskable_512",
        "icon_maskable_192",
        "hintergrund",
        "betreiber",
        "betreiber_kontakt",
        "datenschutz_kontakt",
        "rueckmeldeadresse",
        "datenschutztext",
        "datenschutztext_en",
        "impressum",
        "impressum_en",
    )

    class Meta:
        verbose_name = _("Erscheinungsbild und Betreiber")
        verbose_name_plural = _("Erscheinungsbild und Betreiber")
        ordering = ["-aktiv", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["aktiv"],
                condition=models.Q(aktiv=True),
                name="ein_aktives_erscheinungsbild",
                violation_error_message=_("Es kann nur eine Fassung aktiv sein."),
            )
        ]

    def __str__(self) -> str:
        return str(self.anzeigename)

    def save(self, *args, **kwargs):
        """The same rule for every path past the form: only the switch may change."""
        felder = kwargs.get("update_fields")
        if self.pk and self.ist_standard and (felder is None or set(felder) - {"aktiv"}):
            if self._abweichend_vom_gespeicherten():
                raise ValidationError(
                    _(
                        "„Standard“ lässt sich nicht ändern. Bitte eine Kopie anlegen und die "
                        "ändern."
                    )
                )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        """The standard and the active version stay. Otherwise the application would be without."""
        if self.ist_standard:
            raise ValidationError(_("„Standard“ lässt sich nicht löschen."))
        # Against the database, not against the object: whoever just chose
        # another one may still be holding the old state.
        if Erscheinungsbild.objects.filter(pk=self.pk, aktiv=True).exists():
            raise ValidationError(_("Die aktive Fassung lässt sich nicht löschen. Erst wechseln."))
        return super().delete(*args, **kwargs)

    def natural_key(self) -> tuple[str]:
        return (self.name,)

    @property
    def ist_standard(self) -> bool:
        return self.name == STANDARD

    @property
    def anzeigename(self) -> Beschriftung:
        """The "Standard" row belongs to the application and speaks the account's language."""
        return _("Standard") if self.ist_standard else self.name

    def clean(self) -> None:
        """The standard stays as it was shipped. A copy is what gets changed."""
        if self.pk and self.ist_standard and self._abweichend_vom_gespeicherten():
            raise ValidationError(
                _("„Standard“ lässt sich nicht ändern. Bitte eine Kopie anlegen und die ändern.")
            )

    def _abweichend_vom_gespeicherten(self) -> bool:
        gespeichert = Erscheinungsbild.objects.filter(pk=self.pk).values(*self.KOPIERTE_FELDER)
        if not gespeichert:
            return False
        alt = gespeichert[0]
        return any(
            bytes(alt[feld] or b"") != bytes(getattr(self, feld) or b"")
            if feld.startswith(("logo", "icon", "apple", "favicon"))
            else alt[feld] != getattr(self, feld)
            for feld in self.KOPIERTE_FELDER
        )

    @transaction.atomic
    def aktivieren(self) -> None:
        """From now on everyone sees this version. The previous one steps back."""
        Erscheinungsbild.objects.filter(aktiv=True).exclude(pk=self.pk).update(aktiv=False)
        if not self.aktiv:
            self.aktiv = True
            self.save(update_fields=["aktiv"])

    def kopie(self, name: str) -> Erscheinungsbild:
        """A new version with everything this one has. It is not active yet."""
        neu = Erscheinungsbild(name=name)
        for feld in self.KOPIERTE_FELDER:
            setattr(neu, feld, getattr(self, feld))
        neu.full_clean()
        neu.save()
        return neu

    def kopiename(self) -> str:
        """A name that does not exist yet: "Copy of …", numbered if need be."""
        grund = str(_("Kopie von %(name)s") % {"name": self.anzeigename})
        name, nummer = grund, 1
        while Erscheinungsbild.objects.filter(name=name).exists():
            nummer += 1
            name = f"{grund} {nummer}"
        return name

    @classmethod
    def standard(cls) -> Erscheinungsbild:
        """The bundled version, created if missing."""
        return cls.objects.get_or_create(name=STANDARD)[0]

    def datenschutztext_fuer(self, sprache: str) -> str:
        """The text for this language ('in_sprache'). Empty means: no page."""
        return in_sprache(self.datenschutztext, self.datenschutztext_en, sprache)

    def impressum_fuer(self, sprache: str) -> str:
        """Like the privacy text: the account's language first, empty means no page."""
        return in_sprache(self.impressum, self.impressum_en, sprache)

    @property
    def hat_impressum(self) -> bool:
        """Whether the "Legal notice" page exists. Without text there is no link either."""
        return bool(self.impressum or self.impressum_en)

    @property
    def hat_datenschutzseite(self) -> bool:
        """Whether the "Privacy" page exists. Without text there is no link either."""
        return bool(self.datenschutztext or self.datenschutztext_en)

    @classmethod
    def aktuell_fuer(cls, request) -> Erscheinungsbild:
        """The active version, once per request.

        The privacy page and the legal notice read their text from it, and the
        context processor reads it again for name, logo and footer: twice the
        same row until 10/2026. Kept on the request, which ends with the page.
        """
        vorhanden = getattr(request, "_erscheinungsbild", None)
        if vorhanden is None:
            vorhanden = cls.aktuell()
            setattr(request, "_erscheinungsbild", vorhanden)
        return vorhanden

    @classmethod
    def aktuell(cls) -> Erscheinungsbild:
        """The active version. Every page asks for it, hence one query.

        If none is active, because the backup is older or the demo was reset,
        the standard takes over. That writes once, then never again.
        """
        aktiv = cls.objects.filter(aktiv=True).first()
        if aktiv is not None:
            return aktiv
        standard = cls.standard()
        standard.aktivieren()
        return standard

    @property
    def titel(self) -> str:
        """The name next to the logo. Empty means the shipped one, as with the logo itself."""
        return self.anwendungsname or STANDARDNAME

    @property
    def hat_logo(self) -> bool:
        return bool(self.logo)

    def bild(self, name: str) -> bytes:
        """The bytes for a name from 'images.GROESSEN' or 'images.MASKABLE'."""
        return bytes(getattr(self, name.replace("-", "_")) or b"")


class MailtextManager(models.Manager):
    def get_by_natural_key(self, art: str):
        return self.get(art=art)


class Mailtext(models.Model):
    """The wording of one mail, as the business that runs the application wants it.

    One row per mail ('Mailart'), with subject and text in German and English.
    **Empty means the shipped text** ('mail_texts.mitgeliefert'), as with the
    name of the application: whoever changes nothing keeps it, including the
    improvements of later versions. The administration shows the text in
    force and stores nothing for an unchanged one.

    Not on the appearance: eleven mails in two languages would bury logo and
    privacy text there. So the texts do not switch with the version. The rows
    arise when the administration lists them ('alle_anlegen'). A mail without
    a row goes out with the shipped text, after restoring an old backup too.

    'nennt_namen' decides whether the mail says who triggered it. Without it
    the placeholder 'wer' stays empty, and the text takes its branch for that.
    Per mail, because "deleted by …" may say less than "created by …".
    """

    art = models.CharField(
        max_length=40, unique=True, choices=Mailart.choices, verbose_name=_("Mail")
    )
    betreff = models.CharField(max_length=200, blank=True, verbose_name=_("Betreff (Deutsch)"))
    text = models.TextField(blank=True, verbose_name=_("Text (Deutsch)"))
    betreff_en = models.CharField(max_length=200, blank=True, verbose_name=_("Betreff (Englisch)"))
    text_en = models.TextField(blank=True, verbose_name=_("Text (Englisch)"))
    nennt_namen = models.BooleanField(
        default=True,
        verbose_name=_("nennt, wer es ausgelöst hat"),
        help_text=_(
            "Ohne Haken bleibt der Name weg, und der Text nimmt seinen Zweig ohne Namen. Wer "
            "etwas an seinem eigenen Konto tut, wird ohnehin nicht genannt."
        ),
    )
    geaendert_am = models.DateTimeField(auto_now=True, verbose_name=_("geändert am"))

    objects = MailtextManager()

    #: The fields per language: subject and text.
    FELDER = {"de": ("betreff", "text"), "en": ("betreff_en", "text_en")}

    # Created by Django at runtime. Only named here so pyright knows them.
    get_art_display: Callable[[], str]

    class Meta:
        verbose_name = _("Mailtext")
        verbose_name_plural = _("Mailtexte")

    def __str__(self) -> str:
        return self.get_art_display()

    def natural_key(self) -> tuple[str]:
        return (self.art,)

    @property
    def hat_urheber(self) -> bool:
        """Whether this mail can name someone. A password changed by oneself names nobody."""
        return "wer" in VARIABLEN[self.art]

    @property
    def angepasst(self) -> bool:
        """Whether anything of its own stands here, in either language."""
        return any(getattr(self, feld) for felder in self.FELDER.values() for feld in felder)

    def geltend(self, sprache: str) -> tuple[str, str]:
        """Subject and text in force for this language: the own where filled, else shipped.

        Each on its own: an own subject over the shipped text is allowed.
        """
        betreff_feld, text_feld = self.FELDER[sprache_von(sprache)]
        betreff, text = mitgeliefert(self.art, sprache)
        return getattr(self, betreff_feld) or betreff, getattr(self, text_feld) or text

    def ausfuellen(self, sprache: str, werte: Mapping[str, object]) -> tuple[str, str]:
        """Subject and text of a mail to someone in this language, the placeholders filled."""
        if not self.nennt_namen:
            werte = {**werte, "wer": ""}
        betreff, text = self.geltend(sprache)
        try:
            return betreffzeile(betreff, werte), ausgefuellt(text, werte)
        except TemplateSyntaxError:
            # A text that never went through 'clean()': an edited backup, or
            # a grammar narrowed since. The mail goes out with the shipped
            # text instead of every change to an account ending in a server
            # error, and the administration's preview shows that text too.
            logger.warning("Mail text %s is unreadable, the shipped text is used", self.art)
            betreff, text = mitgeliefert(self.art, sprache)
            return betreffzeile(betreff, werte), ausgefuellt(text, werte)

    def clean(self) -> None:
        """An own text has to be one the mail can be built from ('mail_texts.pruefen')."""
        fehler: dict[str, ValidationError] = {}
        for betreff_feld, text_feld in self.FELDER.values():
            for feld, mit_pflicht in ((betreff_feld, False), (text_feld, True)):
                if wert := getattr(self, feld):
                    try:
                        pruefen(wert, self.art, mit_pflicht=mit_pflicht)
                    except ValidationError as grund:
                        fehler[feld] = grund
        if fehler:
            raise ValidationError(fehler)

    @classmethod
    def fuer(cls, art: str) -> Mailtext:
        """The row of this mail, or an unsaved one with the shipped texts where there is none."""
        return cls.objects.filter(art=art).first() or cls(art=art)

    @classmethod
    def alle_anlegen(cls) -> None:
        """A row for every mail, so the administration lists all of them.

        'ignore_conflicts': two administrators opening the list at once would
        otherwise collide on the unique index.
        """
        vorhanden = set(cls.objects.values_list("art", flat=True))
        fehlend = [cls(art=art) for art in Mailart.values if art not in vorhanden]
        if fehlend:
            cls.objects.bulk_create(fehlend, ignore_conflicts=True)
