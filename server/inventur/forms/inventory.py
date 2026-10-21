"""Creating a run of inventories, and the types of one in preparation."""

from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from django_project.form_fields import modellwahl
from django_project.labels import Beschriftung
from katalog.models import Inventurart, arten_je_haus
from organisation.models import Haus

from ..models import (
    Inventur,
    stichtag_kurz,
)


class HausWahl(forms.CheckboxSelectMultiple):
    """The houses, each with the types it carries active articles in ('data-arten').

    The browser greys out a house without an article of the chosen types
    ('static/js/inventory_add.js'). The form says the same in 'clean'.
    """

    arten_je_haus: dict[int, set[int]]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.arten_je_haus = {}

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        pk = getattr(value, "value", value)
        arten = self.arten_je_haus.get(pk, set()) if isinstance(pk, int) else set()
        option["attrs"]["data-arten"] = " ".join(str(art) for art in sorted(arten))
        return option


def ohne_artikel_der_arten(haeuser, arten, je_haus: dict[int, set[int]]) -> list[str]:
    """The houses that carry no active article of these types, as names."""
    gewaehlt = {art.pk for art in arten}
    return sorted(haus.name for haus in haeuser if not je_haus.get(haus.pk, set()) & gewaehlt)


class InventurAnlegenForm(forms.Form):
    """A run: the types, a cut-off date, one or more houses.

    Per chosen house an inventory of its own arises, with all chosen types.
    There is deliberately no "parent inventory", its bracket *is* the
    cut-off date.

    **First the types, then the houses**: a house is only available if it
    carries an active article of one of the chosen types. Its inventory would
    otherwise have nothing to count. With the monthly and the cup inventory
    every house can be chosen, with the cup inventory alone only those with
    cups. The browser greys out the others, 'clean' says the same for
    everything that comes past it.

    A house has one inventory per day ('eine_inventur_je_haus_und_tag'). That
    should be caught as a form error and not as an IntegrityError. That is
    why the check stands here although the database enforces it anyway.

    **Counters for every house at once** only with the box: often the same
    people count everywhere, and then one selection beats one per inventory.
    Without the box they are assigned per inventory afterwards, as before.
    The list shows each person's houses, the suggestion of the assignment
    page ('ZaehlendeForm') only works for one house.
    """

    inventurarten = forms.ModelMultipleChoiceField(
        queryset=Inventurart.objects.all(),
        widget=forms.CheckboxSelectMultiple,
        label=_("Inventurarten"),
        help_text=_(
            "Jede gewählte Art wird ein Reiter in der Zähl-App. "
            "Für eine Jahresinventur alle wählen."
        ),
    )
    stichtag = forms.DateField(
        initial=timezone.localdate,
        label=_("Stichtag"),
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        help_text=_("Der Tag, für den gezählt wird, nicht zwingend der Tag der Eingabe."),
    )
    haeuser = forms.ModelMultipleChoiceField(
        queryset=Haus.objects.all(),
        widget=HausWahl,
        label=_("Häuser"),
        help_text=_(
            "Je Haus entsteht eine Inventur mit ihren eigenen Zählpositionen. Wählbar ist ein "
            "Haus, das einen aktiven Artikel einer gewählten Art führt."
        ),
    )
    zaehlende_fuer_alle = forms.BooleanField(
        required=False,
        label=_("Zählende gleich hier festlegen, für alle gewählten Häuser"),
        help_text=_("Ohne Haken werden sie danach an jeder Inventur einzeln zugeordnet."),
    )
    zaehlende = forms.ModelMultipleChoiceField(
        queryset=None,
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label=_("Zählende"),
        help_text=_(
            "Du zählst ohnehin mit. Die Gewählten sehen die Inventur in der Zähl-App, sobald "
            "die Zählung beginnt."
        ),
    )

    def __init__(self, *args, benutzer=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.je_haus = arten_je_haus()
        if isinstance(widget := self.fields["haeuser"].widget, HausWahl):
            widget.arten_je_haus = self.je_haus
        # The first type is the everyday one, the monthly inventory unless
        # renamed.
        if (erste := Inventurart.objects.first()) is not None:
            self.fields["inventurarten"].initial = [erste.pk]

        # Whoever creates counts along anyway ('inventuren_anlegen'), and
        # administrators may count everywhere: as on the assignment page.
        personen = Inventur.eintragbare_zaehlende()
        if benutzer is not None:
            personen = personen.exclude(pk=benutzer.pk)
        modellwahl(self, "zaehlende").queryset = personen.order_by("last_name", "first_name")
        self.fuehrungskraefte = set(personen.filter(is_staff=True).values_list("pk", flat=True))
        self.haeuser_von: dict[int, list[str]] = {}
        zuordnungen = Haus.objects.filter(mitarbeitende__isnull=False).order_by("name")
        for person, haus in zuordnungen.values_list("mitarbeitende", "name"):
            self.haeuser_von.setdefault(person, []).append(haus)

    def zaehlende_gruppen(self) -> list[tuple[Beschriftung, list, Beschriftung]]:
        """The boxes of the counters, managers first, each with the houses of the person."""
        leitung, weitere = [], []
        for eintrag in self["zaehlende"]:
            # The value of a model field is a 'ModelChoiceIteratorValue'.
            wert = eintrag.data["value"]
            nummer = int(getattr(wert, "value", wert))
            haeuser = ", ".join(self.haeuser_von.get(nummer, []))
            (leitung if nummer in self.fuehrungskraefte else weitere).append((eintrag, haeuser))
        return [
            (_("Führungskräfte"), leitung, _("Außer dir gibt es keine Führungskraft.")),
            (_("Weitere Benutzer"), weitere, _("Sonst gibt es niemanden.")),
        ]

    def clean(self) -> dict:
        super().clean()
        daten = self.cleaned_data
        # Without the box a ticked name counts for nothing: then the counters
        # are assigned per inventory, and that should not start with a guess.
        if not daten.get("zaehlende_fuer_alle"):
            daten["zaehlende"] = []
        haeuser, stichtag, arten = (
            daten.get("haeuser"),
            daten.get("stichtag"),
            daten.get("inventurarten"),
        )
        if not haeuser or not stichtag or not arten:
            return daten
        if ohne := ohne_artikel_der_arten(haeuser, arten, self.je_haus):
            self.add_error(
                "haeuser",
                _(
                    "Ohne aktiven Artikel der gewählten Arten: %(haeuser)s. Es wurde nichts "
                    "angelegt."
                )
                % {"haeuser": ", ".join(ohne)},
            )
        belegt = Inventur.objects.filter(haus__in=haeuser, stichtag=stichtag)
        if namen := sorted(inventur.haus.name for inventur in belegt.select_related("haus")):
            self.add_error(
                "haeuser",
                _(
                    "Am %(stichtag)s gibt es dort schon eine Inventur: %(haeuser)s. Weitere Arten "
                    "kommen in der Vorbereitung dazu. Es wurde nichts angelegt."
                )
                % {"stichtag": stichtag_kurz(stichtag), "haeuser": ", ".join(namen)},
            )
        return daten


class InventurArtenForm(forms.Form):
    """Which types an inventory in preparation counts.

    Forgotten when creating, a type comes along here instead of deleting and
    creating anew: counters and target stock would otherwise be gone. The
    same rule as when creating: the house needs an active article of one of
    the types.
    """

    inventurarten = forms.ModelMultipleChoiceField(
        queryset=Inventurart.objects.all(),
        widget=forms.CheckboxSelectMultiple,
        label=_("Inventurarten"),
        help_text=_(
            "Eine neue Art bekommt ihre Zählpositionen. Bei einer abgewählten fallen die "
            "ungezählten weg, gezählte bleiben."
        ),
    )

    def __init__(self, *args, inventur: Inventur, **kwargs):
        super().__init__(*args, **kwargs)
        self.inventur = inventur
        self.fields["inventurarten"].initial = [art.pk for art in inventur.arten()]

    def clean_inventurarten(self):
        arten = self.cleaned_data["inventurarten"]
        if ohne_artikel_der_arten([self.inventur.haus], arten, arten_je_haus()):
            raise forms.ValidationError(
                _("%(haus)s führt keinen aktiven Artikel der gewählten Arten.")
                % {"haus": self.inventur.haus.name}
            )
        return arten
