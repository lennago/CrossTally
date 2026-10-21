from django import forms
from django.contrib.admin.widgets import AdminTextareaWidget
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from django_project.form_fields import modellwahl

from .images import GROESSEN, HOECHSTGROESSE, MASKABLE, STANDARDHINTERGRUND, abgeleitet
from .mail_texts import bereinigt, mitgeliefert
from .models import Bereich, Erscheinungsbild, Haus, Mailtext, Ort, hex_farbe

#: The columns a logo fills. "icon-192" becomes "icon_192".
FELDER = tuple(name.replace("-", "_") for name in [*GROESSEN, *MASKABLE])


def logo_hoechstens(datei) -> None:
    """The limit and the sentence of the image check ('gepruefte_vorlage'), before any read.

    Until 10/2026 'clean' read the whole upload into memory, however large,
    and only then did the check of the image measure it.
    """
    if datei.size > HOECHSTGROESSE:
        raise ValidationError(
            _("Das Bild ist größer als %(grenze)s MB."),
            code="zu_gross",
            params={"grenze": HOECHSTGROESSE // (1024 * 1024)},
        )


class HausForm(forms.ModelForm):
    """House including the counters assigned to it.

    The assignment lives as a ManyToMany on the user model, here it is
    maintained from the other side. The user model comes through
    'get_user_model()' instead of an import, because 'accounts' depends on
    'organisation', not the other way round.

    Only counters are available. Managers are entered individually per
    inventory. They do not automatically count everywhere since the assignment
    hangs on the inventory.

    What stands here is a **suggestion**: who may count in an inventory is
    decided by its own assignment ('Inventur.zaehlende'). This list only
    determines who is suggested there.
    """

    mitarbeitende = forms.ModelMultipleChoiceField(
        queryset=Haus.objects.none(),
        required=False,
        label=_("Zähler"),
        help_text=_(
            "Wer hier üblicherweise zählt. Bei Inventuren des Hauses werden diese Leute "
            "vorgeschlagen."
        ),
        widget=forms.SelectMultiple(attrs={"class": "form-select", "size": 8}),
    )

    class Meta:
        model = Haus
        fields = ("name",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        feld = modellwahl(self, "mitarbeitende")
        feld.queryset = (
            get_user_model()
            .objects.filter(is_staff=False, is_superuser=False)
            .order_by("last_name", "first_name")
        )
        if self.instance.pk:
            feld.initial = self.instance.mitarbeitende.all()

    def save(self, commit=True):
        haus = super().save(commit)
        if commit:
            # 'set' would also remove the assignments of managers, who are not
            # even available for selection here. Hence only the counters.
            zaehler = haus.mitarbeitende.filter(is_staff=False, is_superuser=False)
            haus.mitarbeitende.remove(*zaehler)
            haus.mitarbeitende.add(*self.cleaned_data["mitarbeitende"])
        return haus


class BereichForm(forms.ModelForm):
    """Area form without a house field, because the house comes from the URL.

    As with the location, 'haus' is not in the form, which is why Django
    cannot check the UniqueConstraint itself. 'clean_name' does that.
    """

    class Meta:
        model = Bereich
        fields = ("name",)

    def __init__(self, *args, haus: Haus | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.haus = haus or getattr(self.instance, "haus", None)

    def clean_name(self) -> str:
        name = self.cleaned_data["name"]
        if self.haus is None:
            return name
        doppelt = Bereich.objects.filter(haus=self.haus, name__iexact=name).exclude(
            pk=self.instance.pk
        )
        if doppelt.exists():
            raise forms.ValidationError(
                # Named placeholders and no f-string: the translation may change the
                # order, and translation happens only here.
                _("„%(name)s“ gibt es in %(haus)s bereits.") % {"name": name, "haus": self.haus}
            )
        return name


class OrtForm(forms.ModelForm):
    """A location belongs to an area, the area to the house.

    Only the areas of the house from which the form was opened are available.
    Otherwise a location could be hung into a foreign house.

    Django checks the UniqueConstraint 'ortsname_je_bereich_eindeutig' only
    when both fields are in the form. 'bereich' is in it, but the comparison
    should ignore case. Hence 'clean' instead of relying on the database, which
    would otherwise deliver an IntegrityError instead of a form error.
    """

    class Meta:
        model = Ort
        fields = ("name", "bereich")

    def __init__(self, *args, haus: Haus | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.haus = haus or getattr(getattr(self.instance, "bereich", None), "haus", None)
        feld = modellwahl(self, "bereich")
        # With the house: every choice names it ('Bereich.__str__').
        feld.queryset = (
            Bereich.objects.filter(haus=self.haus).select_related("haus")
            if self.haus
            else Bereich.objects.none()
        )
        feld.empty_label = None

    def clean(self):
        super().clean()
        daten = self.cleaned_data
        name, bereich = daten.get("name"), daten.get("bereich")
        if not name or bereich is None:
            return daten
        doppelt = (
            Ort.objects.filter(bereich=bereich, name__iexact=name)
            .exclude(pk=self.instance.pk)
            .exists()
        )
        if doppelt:
            self.add_error(
                "name",
                _("„%(name)s“ gibt es in %(bereich)s bereits.")
                % {"name": name, "bereich": bereich.name},
            )
        return daten


class ErscheinungsbildForm(forms.ModelForm):
    """One PNG up, all sizes from it.

    The field is not named like a column: a file is uploaded, bytes are stored.
    The content is checked and not the file name, see 'images.gepruefte_vorlage'.
    If the field stays empty, the existing logo stays. For resetting there is
    the tick next to it. The size is checked at the field ('logo_hoechstens').
    """

    datei = forms.FileField(
        required=False,
        label=_("Logo"),
        help_text=_(
            "Ein quadratisches PNG, mindestens 512 x 512 Pixel. Daraus entstehen das Logo "
            "der Leiste, das Bild im Browsertab und die Icons für den Startbildschirm. "
            "Leer lassen ändert am vorhandenen Logo nichts."
        ),
        validators=[logo_hoechstens],
    )
    entfernen = forms.BooleanField(
        required=False,
        label=_("Logo entfernen"),
        help_text=_("Setzt auf das mitgelieferte Logo zurück."),
    )

    class Meta:
        model = Erscheinungsbild
        fields = (
            "name",
            "anwendungsname",
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # The model's check would only run after 'clean', and by then the colour
        # would already have landed in the image. On the standard, which the
        # administration opens for reading only, the field does not exist.
        if "hintergrund" in self.fields:
            self.fields["hintergrund"].validators.append(hex_farbe)

    def clean(self):
        super().clean()
        daten = self.cleaned_data
        if daten.get("entfernen") and daten.get("datei"):
            raise forms.ValidationError(
                _("Entweder ein neues Logo hochladen oder das vorhandene entfernen.")
            )
        datei = daten.get("datei")
        if datei is not None:
            # Checks format, aspect ratio and size. If it fails, the reason stands
            # on the field and not as a server error on an empty page.
            try:
                farbe = daten.get("hintergrund") or STANDARDHINTERGRUND
                self._bilder = abgeleitet(datei.read(), farbe)
            except ValidationError as fehler:
                self.add_error("datei", fehler)
        return daten

    def save(self, commit=True):
        erscheinungsbild = super().save(commit=False)
        if self.cleaned_data.get("entfernen"):
            for name in FELDER:
                setattr(erscheinungsbild, name, b"")
        for name, bild in getattr(self, "_bilder", {}).items():
            setattr(erscheinungsbild, name.replace("-", "_"), bild)
        if commit:
            erscheinungsbild.save()
        return erscheinungsbild


class MailtextForm(forms.ModelForm):
    """Shows the text in force and stores nothing for an unchanged one.

    An empty field means the shipped text ('Mailtext'). The form nevertheless
    shows that text in the field, so whoever changes it starts from it and not
    from nothing. A text that comes back as shipped is stored empty again:
    then a later improvement of the shipped one reaches it too. Emptying a
    field returns to the shipped text the same way.
    """

    class Meta:
        model = Mailtext
        fields = ("betreff", "text", "betreff_en", "text_en", "nennt_namen")
        # The administration's own widget, as wide as the privacy text. A plain
        # 'Textarea' stood at half the width, and every condition broke its line.
        widgets = {
            "text": AdminTextareaWidget(attrs={"rows": 16}),
            "text_en": AdminTextareaWidget(attrs={"rows": 16}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for feld, wert in self.mitgelieferte().items():
            if feld in self.fields and not getattr(self.instance, feld):
                self.initial[feld] = wert

    def mitgelieferte(self) -> dict[str, str]:
        """The shipped subject and text per field, for this mail."""
        werte = {}
        for sprache, felder in Mailtext.FELDER.items():
            werte |= dict(zip(felder, mitgeliefert(self.instance.art, sprache), strict=True))
        return werte

    def _eigener(self, feld: str) -> str:
        wert = bereinigt(self.cleaned_data.get(feld) or "")
        return "" if wert == self.mitgelieferte()[feld] else wert

    def clean_betreff(self) -> str:
        return self._eigener("betreff")

    def clean_text(self) -> str:
        return self._eigener("text")

    def clean_betreff_en(self) -> str:
        return self._eigener("betreff_en")

    def clean_text_en(self) -> str:
        return self._eigener("text_en")
