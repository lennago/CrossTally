"""The article import: upload the file and confirm the plan row by row."""

from collections import defaultdict
from collections.abc import MutableMapping
from dataclasses import dataclass, field
from typing import Any, cast
from urllib.parse import urlencode

from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.forms import BoundField
from django.utils.translation import gettext_lazy as _

from django_project.form_fields import Dezimalfeld, auswahl, feld_als
from django_project.labels import Beschriftung
from django_project.validation import HoechstensGross
from organisation.models import Haus

from .. import formats, services
from ..formats import formatwahl
from ..models import (
    DIMENSION_JE_KENNUNG,
    FAKTOR_MINDESTENS,
    FAKTOR_NACHKOMMASTELLEN,
    FAKTOR_STELLEN_GESAMT,
    FAKTOR_ZU_KLEIN,
    GEFUEHRT,
    OHNE_WARENGRUPPE,
    OHNE_WARENGRUPPE_ANZEIGE,
    Artikelstatus,
    Hausartikel,
    Inventurart,
    Ware,
    Warenfamilie,
    braucht_faktor,
    einheit_gebunden,
    einheit_kurz,
)


class DateiImportForm(forms.Form):
    """A file and the system it comes from.

    Which file is expected and which extension it may have is said by the
    chosen format ('katalog.formats'). That is why 'clean' checks the
    extension only once the format is fixed, instead of with a validator on
    the field. What the subclass reads is said by 'DATEI': 'artikelstamm' or
    'sollbestand'.
    """

    DATEI = ""

    format = forms.ChoiceField(
        label=_("Format"),
        choices=formatwahl,
        initial=formats.STANDARD,
        help_text=_("Das System, aus dem die Datei kommt."),
    )
    datei = forms.FileField(label=_("Datei"), validators=[HoechstensGross()])

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["datei"].help_text = formats.hinweise(self.DATEI)

    def clean(self):
        super().clean()
        daten = self.cleaned_data
        datei, kennung = daten.get("datei"), daten.get("format")
        if datei is None or not kennung:
            return daten
        format = formats.format_holen(kennung)
        erlaubt = getattr(format, f"{self.DATEI}_endungen")
        endung = datei.name.rsplit(".", 1)[-1].lower() if "." in datei.name else ""
        if endung not in erlaubt:
            self.add_error(
                "datei",
                _("%(format)s erwartet hier %(endungen)s, nicht „%(endung)s“.")
                % {
                    "format": format.name,
                    "endungen": ", ".join(erlaubt),
                    "endung": endung or datei.name,
                },
            )
        daten["format"] = format
        return daten


class ArtikelImportForm(DateiImportForm):
    """Read in the article master of a house. The house comes from the URL.

    Exactly one file: the plan attributes its rows to this house. Per house
    there is thus room for exactly one export.
    """

    DATEI = "artikelstamm"


class NurGewaehlteOption(forms.Select):
    """A selection field that brings only its chosen option.

    The import plan has a selection of all products per row. With a thousand
    rows and nine hundred products that would be almost a million options,
    35 MB of HTML and one query per row. The page could not be used. The list
    therefore stands **once** on the page ('json_script', see
    'BaseImportFormSet.optionen'), and Tom Select fills every field from it
    ('components/select_script.html', 'data-optionen'). Without JavaScript
    the field shows only what is chosen. Then it stays with this choice.
    """

    def __init__(self, beschriftungen: dict[str, str], quelle: str, leer: str, attrs=None):
        super().__init__(attrs)
        self.beschriftungen = beschriftungen
        self.quelle = quelle
        self.leer = leer

    def build_attrs(self, base_attrs, extra_attrs=None):
        return super().build_attrs(base_attrs, extra_attrs) | {"data-optionen": self.quelle}

    def optgroups(self, name, value, attrs=None):
        gewaehlt = next((str(wert) for wert in value if wert not in ("", None)), "")
        optionen = [("", self.leer)]
        if gewaehlt in self.beschriftungen:
            optionen.append((gewaehlt, self.beschriftungen[gewaehlt]))
        eintraege = [
            self.create_option(name, wert, text, wert == gewaehlt, stelle, attrs=attrs)
            for stelle, (wert, text) in enumerate(optionen)
        ]
        return [(None, eintraege, 0)]


class GeteilteWarenwahl(forms.ModelChoiceField):
    """A product choice from a pool all rows share.

    'to_python' looks up in the pool instead of the database, otherwise
    submitting a plan would cost a query per row. The pool is set by the
    formset ('warenvorrat'). Without it the field behaves as usual.
    """

    vorrat: dict[str, Ware] | None = None

    def to_python(self, value):
        if self.vorrat is None or value in self.empty_values:
            return super().to_python(value)
        ware = self.vorrat.get(str(value))
        if ware is None:
            raise ValidationError(self.error_messages["invalid_choice"], code="invalid_choice")
        return ware


#: Where the options stand on the import page ('json_script').
WARENOPTIONEN = "ware-optionen"

#: The block for conflicts that have already been decided. No kind and no
#: procedure, only a place on the page, which is why the id stands here and
#: not in 'services'.
BEHOBEN = "behoben"

#: The rows of the plan in the form: 'plan-0-name'.
PLAN_PRAEFIX = "plan"

#: The most rows a plan may have. Every row travels back with the submit, and
#: the server takes only so many fields and bytes per request
#: ('DATA_UPLOAD_MAX_NUMBER_FIELDS' and 'DATA_UPLOAD_MAX_MEMORY_SIZE' in the
#: settings, sized for this). Room for the whole article master of a large
#: site in its first import. Until 10/2026 a plan of more than about 1,450 new
#: articles was shown, and Django refused it when it was sent back, after
#: someone had gone through all of it.
HOECHSTENS_PLANZEILEN = 3_000

#: The most fields a row of the plan sends back: the eleven hidden ones
#: ('ImportZeileForm.FESTE_FELDER'), product, name of a new one, conversion,
#: packs, type and procedure, and at most three more, the parts a conflict
#: adopts. The settings count on it, a test reads it off the page.
FELDER_JE_PLANZEILE = 20

#: Bytes per row the submit may carry beyond what the plan wrote: a name for a
#: new product, a decision, the line breaks a browser sends as two characters.
RESERVE_JE_PLANZEILE = 300


def plan_zu_gross(initial: list[dict]) -> str | None:
    """Why this plan could not be sent back in one piece, or None.

    Asked before the plan is shown. The server refuses a submit beyond its
    limits as a whole, and without the plan: the person would have gone
    through every row for nothing. So the row count is checked against
    'HOECHSTENS_PLANZEILEN', and the bytes as the browser sends them against
    the limit of the settings. A row can carry long names and hints, so the
    count alone does not bound them.

    Splitting the file along its product groups is safe: a product group
    without a row in the file stays untouched ('_fehlende_ergaenzen').
    """
    rat = _(
        "Verteile die Warengruppen bitte auf mehrere Exporte und lies sie nacheinander ein. "
        "Eine Warengruppe, die in einer Datei fehlt, bleibt dabei unberührt."
    )
    if len(initial) > HOECHSTENS_PLANZEILEN:
        grund = _(
            "Der Plan hätte %(anzahl)s Zeilen. In einem Zug lassen sich höchstens "
            "%(hoechstens)s übernehmen."
        ) % {"anzahl": len(initial), "hoechstens": HOECHSTENS_PLANZEILEN}
        return " ".join([str(grund), str(rat)])
    grenze = settings.DATA_UPLOAD_MAX_MEMORY_SIZE
    if grenze is not None and _rumpfgroesse(initial) > grenze:
        grund = _("Der Plan wäre zu groß, um ihn in einem Zug zu übernehmen.")
        return " ".join([str(grund), str(rat)])
    return None


def _rumpfgroesse(initial: list[dict]) -> int:
    """About the bytes the browser sends for these rows, encoded as it encodes them.

    Each row with its reserve for what is typed and chosen while checking.
    Every field counts, also an empty one the page leaves out.
    """
    paare = [
        (f"{PLAN_PRAEFIX}-{stelle}-{name}", "" if wert is None else str(wert))
        for stelle, zeile in enumerate(initial)
        for name, werte in zeile.items()
        for wert in (werte if isinstance(werte, list) else [werte])
    ]
    return len(urlencode(paare)) + RESERVE_JE_PLANZEILE * len(initial)


class ImportZeileForm(forms.Form):
    """A row of the import plan before it is written.

    Article number, designation, product group and unit are fixed, because
    they come from the inventory management system. Number and designation
    moreover decide about the identity of the row: by them the next import
    recognises it, and a designation changed here would come back as a new
    article every time. All four ride along as hidden fields so executing
    works without server state. Editable is what this application itself is
    responsible for: product, inventory scope and packs.
    """

    #: The house's row the row concerns. The formset sets it when validating.
    satz: Hausartikel | None = None

    #: What rides along hidden: what the file says, and what the plan found in the stock.
    FESTE_FELDER = (
        "artikelnummer",
        "name",
        "warengruppe",
        "einheit",
        "art",
        "vorhanden_pk",
        "hinweise",
        "abweichungen",
        "gebinde_von",
        "gebinde_faktor",
        "gebinde_datei",
    )

    artikelnummer = forms.CharField(widget=forms.HiddenInput)
    name = forms.CharField(max_length=200, label=_("Bezeichnung"), widget=forms.HiddenInput)
    warengruppe = forms.CharField(required=False, widget=forms.HiddenInput)
    einheit = forms.CharField(required=False, widget=forms.HiddenInput)
    art = forms.ChoiceField(
        choices=[(art, art) for art in services.VORGEHEN_JE_ART], widget=forms.HiddenInput
    )
    vorhanden_pk = forms.IntegerField(required=False, widget=forms.HiddenInput)
    # What stood out when planning, one hint per row. It rides along because
    # it would otherwise be missing after an error on submit. And for a
    # conflict only there stands what it consists of.
    hinweise = forms.CharField(required=False, widget=forms.HiddenInput)
    # What a conflict consists of, comma-separated. For display only: the
    # choice 'uebernehmen' arises from it. It is not measured anew. Adopting a
    # part in which the file says nothing different changes nothing.
    abweichungen = forms.CharField(required=False, widget=forms.HiddenInput)
    # The product whose packs stand in the field, and what the file brought for
    # them. Only for that product does the field say everything it carries.
    # For another one the formset builds the field anew from the file's packs
    # ('BaseImportFormSet._gebinde_nachfuellen').
    gebinde_von = forms.IntegerField(required=False, widget=forms.HiddenInput)
    gebinde_faktor = Dezimalfeld(
        required=False,
        max_digits=FAKTOR_STELLEN_GESAMT,
        decimal_places=FAKTOR_NACHKOMMASTELLEN,
        widget=forms.HiddenInput,
    )
    gebinde_datei = forms.CharField(required=False, widget=forms.HiddenInput)

    ware = GeteilteWarenwahl(
        queryset=Ware.objects.select_related("basiseinheit", "warenfamilie"),
        required=False,
        empty_label=_("-- neu anlegen --"),
        label=_("Ware"),
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    neuer_ware = forms.CharField(
        max_length=200,
        required=False,
        label=_("Name des neuen"),
        widget=forms.TextInput(
            attrs={
                "class": "form-control form-control-sm",
                "placeholder": _("wie die Bezeichnung"),
            }
        ),
    )
    # The types are rows. Their choices come from the formset, once for all rows.
    # Empty is the rule: as the product's other articles, a new product into
    # the first type ('Inventurart.vorschlag_fuer'). It follows the product
    # even if someone chooses another one in the row.
    inventurart = forms.ChoiceField(
        choices=(),
        required=False,
        label=_("Inventurart"),
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    gebinde = forms.CharField(
        required=False,
        label=_("Gebinde"),
        help_text=_("„Name = Menge“, je Zeile oder mit Semikolon getrennt."),
        widget=forms.Textarea(attrs={"rows": 2, "class": "form-control form-control-sm"}),
    )
    vorgehen = forms.ChoiceField(
        choices=services.VORGEHEN_NEU,
        label=_("Vorgehen"),
        widget=forms.RadioSelect(attrs={"class": "btn-check", "autocomplete": "off"}),
    )
    status_neu = forms.ChoiceField(
        choices=services.VORGEHEN_NEU,
        required=False,
        label=_("Der neue Artikel wird"),
        widget=forms.RadioSelect(attrs={"class": "btn-check", "autocomplete": "off"}),
    )
    uebernehmen = forms.MultipleChoiceField(
        choices=services.TEILE,
        required=False,
        label=_("Aus der Datei übernehmen"),
        widget=forms.CheckboxSelectMultiple(attrs={"class": "btn-check", "autocomplete": "off"}),
    )
    # Only if the house counts in a different dimension than the chosen
    # product. The script otherwise hides the field, the form skips an
    # entry it does not need.
    faktor_zur_ware = Dezimalfeld(
        required=False,
        localize=True,
        max_digits=FAKTOR_STELLEN_GESAMT,
        decimal_places=FAKTOR_NACHKOMMASTELLEN,
        min_value=FAKTOR_MINDESTENS,
        error_messages={"min_value": FAKTOR_ZU_KLEIN},
        label=_("Umrechnung zur Ware"),
        widget=forms.TextInput(
            attrs={"class": "form-control form-control-sm", "inputmode": "decimal"}
        ),
    )

    def __init__(
        self,
        *args,
        warenvorrat: dict[str, Ware] | None = None,
        warennamen: dict[str, str] | None = None,
        artenwahl: list[tuple[str, Beschriftung]] | None = None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        if artenwahl is None:
            artenwahl = ImportZeileForm.artenauswahl()
        auswahl(self, "inventurart").choices = artenwahl
        # A new article knows other procedures than one that already exists. An
        # unknown kind is rejected by the field itself. Until then the procedures
        # for new apply.
        auswahl(self, "vorgehen").choices = services.VORGEHEN_JE_ART.get(
            self.zeilenart, services.VORGEHEN_NEU
        )
        # Available is only what the row really deviates in.
        auswahl(self, "uebernehmen").choices = [
            (teil, text) for teil, text in services.TEILE if teil in self.abweichungsliste
        ]
        if self.zeilenart == services.KONFLIKT:
            # A conflict comes without a preselection. Whoever chooses nothing reads what lacks.
            self.fields["vorgehen"].error_messages["required"] = _(
                "Bitte entscheiden: lassen oder ändern."
            )
        self.warenvorrat = warenvorrat
        if warenvorrat is not None:
            feld = feld_als(self, "ware", GeteilteWarenwahl)
            feld.vorrat = warenvorrat
            # The names come from the formset, built once for all rows. Built
            # here, a plan of 600 rows named 860 products 600 times over: twelve
            # seconds for the preview (measured 2026-10-09).
            if warennamen is None:
                warennamen = {pk: str(ware) for pk, ware in warenvorrat.items()}
            feld.widget = NurGewaehlteOption(
                warennamen,
                WARENOPTIONEN,
                str(feld.empty_label or ""),
                attrs={"class": "form-select form-select-sm"},
            )

    @staticmethod
    def artenauswahl() -> list[tuple[str, Beschriftung]]:
        return [
            ("", _("wie bei der Ware")),
            *((str(pk), name) for pk, name in Inventurart.objects.values_list("pk", "name")),
        ]

    @property
    def zeilenart(self) -> str:
        """The value of 'art', bound or unbound. It determines block and procedure."""
        return str(self["art"].value() or services.NEU)

    @property
    def feste_felder(self) -> list[BoundField]:
        """The hidden fields of the row that carry a value. An empty one stays off the page.

        Missing from the submit, a field reads as empty, so nothing is lost. A
        new article carried five empty ones: of seventeen fields a row then
        sends twelve, and the submit of a first import shrinks by a fifth
        (measured 10/2026).
        """
        return [self[name] for name in self.FESTE_FELDER if self[name].value() not in (None, "")]

    @property
    def einheit_anzeige(self) -> Beschriftung:
        """The short form of the unit. Carried along is the id, it is no text."""
        return einheit_kurz(str(self["einheit"].value() or ""))

    @property
    def einheit_dimension(self) -> str:
        """The dimension of the unit, for the script: it compares it with the product's."""
        return str(DIMENSION_JE_KENNUNG.get(str(self["einheit"].value() or ""), ""))

    @property
    def warengruppenname(self) -> str:
        """The plan is grouped by it. Empty does not exist, see '_planzeile'."""
        return str(self["warengruppe"].value() or "") or OHNE_WARENGRUPPE

    @property
    def abweichungsliste(self) -> list[str]:
        return [teil for teil in str(self["abweichungen"].value() or "").split(",") if teil]

    @property
    def teile_waehlbar(self) -> bool:
        """With a single deviation there is nothing to choose. "Change" then means it."""
        return len(self.abweichungsliste) > 1

    @property
    def nur_bestand(self) -> bool:
        """The row changes nothing about the product, it only shows it.

        A discontinued row comes back as it was, and one missing from the file
        keeps what it has. In both cases there is nothing to choose but the
        procedure.
        """
        return self.zeilenart in (services.EINGESTELLT, services.FEHLT)

    @property
    def ist_offener_konflikt(self) -> bool:
        """A conflict nobody has decided yet. It holds up the adoption."""
        return self.zeilenart == services.KONFLIKT and self["vorgehen"].value() not in dict(
            services.VORGEHEN_KONFLIKT
        )

    @property
    def hinweiszeilen(self) -> list[str]:
        return [zeile for zeile in str(self["hinweise"].value() or "").splitlines() if zeile]

    @property
    def warenname(self) -> str:
        """The chosen product as text. Where it is fixed, the row shows no selection field."""
        ware = (self.warenvorrat or {}).get(str(self["ware"].value() or ""))
        return str(ware) if ware is not None else ""

    def clean_gebinde(self) -> dict:
        gebinde, fehler = services.gebinde_lesen(self.cleaned_data["gebinde"])
        mengen = list(gebinde.values())
        if len(mengen) != len(set(mengen)):
            fehler.append(
                _(
                    "Jede Menge darf nur einmal vorkommen. Zwei Namen für dieselbe Menge "
                    "lassen sich beim Zählen nicht unterscheiden."
                )
            )
        if fehler:
            raise forms.ValidationError(fehler)
        return gebinde

    def clean_gebinde_datei(self) -> dict:
        """The plan wrote it. What does not read back is left out, as if the file lacked it."""
        gebinde, _fehler = services.gebinde_lesen(self.cleaned_data["gebinde_datei"])
        return gebinde

    def clean(self):
        super().clean()
        daten = self.cleaned_data
        if daten.get("vorgehen") == services.ERSETZEN and not daten.get("status_neu"):
            self.add_error("status_neu", _("Bitte wählen, womit der neue Artikel entsteht."))
        if daten.get("art") == services.KONFLIKT:
            self._teile_pruefen(daten)
        if daten.get("ware") and daten.get("neuer_ware"):
            self.add_error(
                "neuer_ware",
                _("Entweder einen vorhandenen wählen oder einen neuen benennen, nicht beides."),
            )
        self._umrechnung_pruefen(daten)
        return daten

    def _teile_pruefen(self, daten: dict) -> None:
        """What "Change" adopts on a conflict, and whether it thereby resolves it too."""
        if not self.teile_waehlbar:
            daten["uebernehmen"] = self.abweichungsliste
        if daten.get("vorgehen") != services.AKTUALISIEREN:
            return
        if not daten.get("uebernehmen"):
            self.add_error("uebernehmen", _("Bitte wählen, was übernommen wird, oder „Lassen“."))

    def _umrechnung_pruefen(self, daten: dict) -> None:
        """If the house counts in another dimension than the chosen product, the factor is needed.

        The same rule as on the house article ('pruefe_einheit'), in every
        status. Within a dimension an entry silently drops away. Asked
        wherever the row writes a house article ('schreibt_hausartikel'):
        "Leave" and "Bring back" need nothing. Nor a conflict that keeps the
        row's unit although the file names another: the question is about the
        file's unit, and the row keeps its own factor
        ('services.faktor_behalten'). Until 10/2026 the plan refused "Change"
        until a factor was typed for the file's unit, and that one then
        stood on the kept unit.
        """
        ware = daten.get("ware")
        dimension = DIMENSION_JE_KENNUNG.get(daten.get("einheit") or "")
        if (
            ware is None
            or dimension is None
            or not braucht_faktor(dimension, ware.basiseinheit.dimension)
        ):
            daten["faktor_zur_ware"] = None
            return
        behaelt_einheit = (
            daten.get("art") == services.KONFLIKT
            and services.EINHEIT in self.abweichungsliste
            and not services.uebernimmt(daten, services.EINHEIT)
        )
        if (
            services.schreibt_hausartikel(daten)
            and daten.get("faktor_zur_ware") is None
            and not behaelt_einheit
        ):
            self.add_error(
                "faktor_zur_ware",
                _(
                    "Das Haus zählt in %(haus)s, „%(name)s“ wird in %(ware)s geführt. Wie "
                    "viele %(ware)s ein %(haus)s ist, weiß nur, wer die Ware kennt."
                )
                % {
                    "name": ware.name,
                    "ware": ware.basiseinheit.kurz,
                    "haus": einheit_kurz(daten["einheit"]),
                },
            )

    @property
    def warenschluessel(self) -> str | None:
        """The product this row occupies, as a key for the comparison.

        A chosen product counts by its primary key, a new one by its name: two
        rows with the same new name mean the same product.
        """
        daten = getattr(self, "cleaned_data", {})
        # Whoever comes back or is missing from the file brings their product
        # from the stock. The row's selection then does not count.
        if self.satz is not None and (
            daten.get("vorgehen") == services.ZURUECKHOLEN or daten.get("art") == services.FEHLT
        ):
            return f"pk:{self.satz.ware_id}"
        if gewaehlt := daten.get("ware"):
            return f"pk:{gewaehlt.pk}"
        if daten.get("name"):
            return f"name:{services.warename(daten).lower()}"
        return None


@dataclass
class Warengruppenblock:
    """A product group within a block of the plan.

    'nummer' is the id for the script. The name is no good for that: it comes
    from the inventory management system and may contain anything an
    attribute selector does not tolerate.
    """

    nummer: int
    name: str
    zeilen: list = field(default_factory=list)

    @property
    def anzeige(self) -> str | Beschriftung:
        return OHNE_WARENGRUPPE_ANZEIGE if self.name == OHNE_WARENGRUPPE else self.name


@dataclass
class Planblock:
    """A block of the plan, grouped by product groups."""

    kennung: str
    titel: Beschriftung
    #: The kind of the rows in it. It says which procedures the bulk button offers.
    art: str
    gruppen: list[Warengruppenblock] = field(default_factory=list)

    @property
    def anzahl(self) -> int:
        return sum(len(gruppe.zeilen) for gruppe in self.gruppen)

    @property
    def vorgehen(self) -> list[tuple[str, Beschriftung]]:
        """What the button in the header of a product group offers.

        Not "Replace": that demands a second decision per row, and a bulk button
        cannot make that.
        """
        return [
            (wert, text)
            for wert, text in services.VORGEHEN_JE_ART.get(self.art, ())
            if wert != services.ERSETZEN
        ]


class BaseImportFormSet(forms.BaseFormSet):
    """The import plan as a whole.

    Sorts the rows for display into the blocks that stand one below the other
    in the form: at the top what would arise new (one block per status),
    below it what would concern an existing row.

    And watches over no product being carried twice in the house. Per house
    the partial index allows only one active or inactive house article per
    product. Two rows can contest that with each other, and a row with what
    is already in the database. Without this check both would only end when
    writing, as a server error.
    """

    #: The blocks in the order of the page. For a new article the procedure
    #: decides about the block, otherwise the kind of the row. The ids are
    #: therefore their values, and the script gets by without a table.
    BLOECKE_NEU = (
        (services.AKTIV, _("Wird aktiv angelegt")),
        (services.INAKTIV, _("Wird inaktiv angelegt")),
        (services.EINSTELLEN, _("Wird eingestellt angelegt")),
        (services.IGNORIEREN, _("Wird nicht gezählt (nicht zählbar)")),
    )
    BLOECKE_BESTAND = (
        (services.EINGESTELLT, _("Eingestellt, lässt sich zurückholen")),
        (services.KONFLIKT, _("Konflikte mit dem Bestand")),
        (BEHOBEN, _("Behobene Konflikte")),
        (services.UMBENANNT, _("Nummer mit anderer Bezeichnung")),
        (services.FEHLT, _("Fehlt in der Datei")),
    )

    #: Which kind sits in which block. The four for creating all carry the
    #: same, because there the procedure decides about the block.
    ART_JE_BLOCK = {
        services.AKTIV: services.NEU,
        services.INAKTIV: services.NEU,
        services.EINSTELLEN: services.NEU,
        services.IGNORIEREN: services.NEU,
        BEHOBEN: services.KONFLIKT,
    }

    #: The house imported into. Without it only the comparison with the
    #: database drops away, the one within the plan remains.
    haus: Haus | None = None

    def __init__(self, data=None, *args, **kwargs):
        # All products once, for all rows: for displaying the chosen one, for
        # checking on submit and as a pool for the selection in the browser.
        self.warenvorrat = {
            str(ware.pk): ware
            for ware in Ware.objects.select_related("basiseinheit", "warenfamilie")
        }
        self.warennamen = {pk: str(ware) for pk, ware in self.warenvorrat.items()}
        self.artenwahl = ImportZeileForm.artenauswahl()
        # A copy that can be written to: a row with a product chosen by hand
        # comes back with that product's packs in the field ('_gebinde_nachfuellen'),
        # and a bound form shows what its data holds. The rows share this object.
        self.beschreibbar: MutableMapping[str, Any] | None = None if data is None else data.copy()
        super().__init__(self.beschreibbar, *args, **kwargs)

    def get_form_kwargs(self, index):
        return super().get_form_kwargs(index) | {
            "warenvorrat": self.warenvorrat,
            "warennamen": self.warennamen,
            "artenwahl": self.artenwahl,
        }

    @property
    def optionen(self) -> list[dict]:
        """The selection of products, once for the whole page."""
        return [
            {
                "value": pk,
                "text": str(ware),
                # For the conversion per row: the script compares the dimension
                # of the product with that of the unit in the file.
                "dimension": ware.basiseinheit.dimension,
                "einheit": str(ware.basiseinheit.kurz),
            }
            for pk, ware in sorted(self.warenvorrat.items(), key=lambda paar: str(paar[1]))
        ]

    def clean(self) -> None:
        super().clean()
        if any(self.errors):
            return

        self._bestand_pruefen()
        if any(self.errors):
            # If the plan no longer fits the stock, only reading in anew helps. And
            # 'add_error' has already taken the row's procedure away.
            return
        self._neue_namen_pruefen()
        gesehen: dict[str, str] = {}
        belegt = self._belegte_waren()
        for form in self.forms:
            daten = form.cleaned_data
            self._nicht_zaehlbar_pruefen(form)
            if not services.belegt_platz(daten) or not (ware := form.warenschluessel):
                continue
            # Whoever comes back or is missing from the file has no selection
            # field. The message then stands at the procedure.
            ohne_wahl = daten["vorgehen"] == services.ZURUECKHOLEN or daten["art"] == services.FEHLT
            feld = "vorgehen" if ohne_wahl else "ware"
            if (erste := gesehen.get(ware)) is not None:
                form.add_error(
                    feld,
                    _(
                        "Dieselbe Ware wird schon von %(erste)s geführt. Ein Haus führt sie "
                        "nur einmal. Hier entweder eine eigene Ware benennen oder "
                        "die Zeile eingestellt anlegen."
                    )
                    % {"erste": erste},
                )
                continue
            gesehen[ware] = daten["artikelnummer"]
            inhaber = belegt.get(int(ware[3:])) if ware.startswith("pk:") else None
            if inhaber is not None and inhaber.pk != daten.get("vorhanden_pk"):
                form.add_error(
                    feld,
                    _("%(haus)s führt diese Ware schon unter %(nummer)s „%(name)s“.")
                    % {
                        "haus": self.haus,
                        "nummer": inhaber.artikelnummer,
                        "name": inhaber.name,
                    },
                )
        # Both last: they mark the procedure or the packs, which the checks above still read.
        self._einheitenwechsel_pruefen()
        self._gebinde_nachfuellen()

    def _gebinde_nachfuellen(self) -> None:
        """A product chosen by hand comes back with its own packs in the field, to be checked first.

        The field shows the packs of the product the plan suggested
        ('gebinde_von'), or only the file's quantities. Adopting reconciles the
        chosen product's packs with it: what is missing goes inactive, in
        every house, and what stands there is added. For another product that
        would retire its packs and hand it those of the suggested one (until
        10/2026). So the row comes back with the field the plan would have
        shown for the chosen product, and only the next submit writes it.

        The same for a factor other than the one the field was converted with
        ('gebinde_faktor'): read back with it, every pack of the product would
        arise a second time with a slightly different quantity. And a new
        product gets only what the file brings, not the suggested one's packs
        (both until 10/2026).
        """
        if self.beschreibbar is None:
            return
        for form in self.forms:
            daten = form.cleaned_data
            ware = daten.get("ware")
            if not services.schreibt_gebinde(daten):
                continue
            if ware is None:
                self._feld_der_datei(form)
                continue
            if ware.pk == daten.get("gebinde_von") and daten.get("faktor_zur_ware") == daten.get(
                "gebinde_faktor"
            ):
                continue
            feld, hinweise = services.gebindefeld_fuer(
                ware,
                daten.get("gebinde_datei") or {},
                daten.get("einheit") or "",
                daten.get("faktor_zur_ware"),
            )
            faktor = daten.get("faktor_zur_ware")
            if feld == daten.get("gebinde"):
                # The field already says what the plan would have shown. Nothing to check.
                daten["gebinde_von"], daten["gebinde_faktor"] = ware.pk, faktor
                continue
            neu_berechnet = ware.pk == daten.get("gebinde_von")
            self.beschreibbar[form.add_prefix("gebinde")] = services.gebinde_text(feld)
            self.beschreibbar[form.add_prefix("gebinde_von")] = str(ware.pk)
            self.beschreibbar[form.add_prefix("gebinde_faktor")] = (
                "" if faktor is None else str(faktor)
            )
            meldung = (
                _(
                    "Die Umrechnung ist eine andere als im Plan. Im Feld stehen jetzt die "
                    "Gebinde von „%(ware)s“ mit ihr umgerechnet. Bitte prüfen und erneut "
                    "übernehmen."
                )
                if neu_berechnet
                else _(
                    "Gewählt ist „%(ware)s“. Im Feld stehen jetzt ihre Gebinde und die aus "
                    "der Datei. Bitte prüfen und erneut übernehmen."
                )
            )
            form.add_error("gebinde", [meldung % {"ware": ware.name}, *hinweise])

    def _feld_der_datei(self, form: ImportZeileForm) -> None:
        """A new product arises: the field holds only what the file brings."""
        assert self.beschreibbar is not None
        daten = form.cleaned_data
        if daten.get("gebinde_von") is None:
            return
        datei = daten.get("gebinde_datei") or {}
        self.beschreibbar[form.add_prefix("gebinde")] = services.gebinde_text(datei)
        self.beschreibbar[form.add_prefix("gebinde_von")] = ""
        self.beschreibbar[form.add_prefix("gebinde_faktor")] = ""
        if datei == daten.get("gebinde"):
            daten["gebinde_von"] = daten["gebinde_faktor"] = None
            return
        form.add_error(
            "gebinde",
            _(
                "Angelegt wird eine neue Ware. Im Feld stehen jetzt nur die Gebinde aus der "
                "Datei. Bitte prüfen und erneut übernehmen."
            ),
        )

    def _bestand_pruefen(self) -> None:
        """'art', 'vorhanden_pk' and the designation come from the form, from outside.

        They are measured against what the house has **now**, with the same rule
        as when planning ('services.einordnen'). That holds two things: a
        tampered form does not rename or discontinue the article of another
        house, and what someone else created between planning and submitting does
        not arise as a twin, because house, number and designation together exist
        only once, in every status.
        """
        nummern: set[str] = set()
        for form in self.forms:
            nummer = form.cleaned_data["artikelnummer"]
            if nummer in nummern:
                form.add_error(
                    "vorgehen",
                    _("%(nummer)s steht mehrfach im Plan.") % {"nummer": nummer},
                )
            nummern.add(nummer)
        if self.haus is None:
            return

        saetze = services.saetze_je_nummer(self.haus, nummern)
        for form in self.forms:
            daten = form.cleaned_data
            art, satz = services.einordnen(daten["name"], saetze.get(daten["artikelnummer"], []))
            gefuehrt = satz is not None and satz.status in GEFUEHRT
            if daten["art"] == services.FEHLT:
                # This row comes from the stock, not from the file. It has to
                # find its carried row under number and designation still
                # exactly like that.
                art = services.FEHLT if art == services.BEKANNT and gefuehrt else None
            elif art == services.BEKANNT:
                # Not countable does not even get into the plan.
                art = services.KONFLIKT if gefuehrt else None
            if art != daten["art"] or getattr(satz, "pk", None) != daten.get("vorhanden_pk"):
                form.add_error(
                    "vorgehen",
                    _(
                        "Diese Zeile passt nicht mehr zu dem, was %(haus)s unter "
                        "%(nummer)s hat. Bitte die Datei neu einlesen."
                    )
                    % {"haus": self.haus, "nummer": daten["artikelnummer"]},
                )
                continue
            form.satz = satz

    def _einheitenwechsel_pruefen(self) -> None:
        """A row that would adopt the file's unit waits while an inventory holds numbers in the old.

        The rule of the house article ('Hausartikel.pruefe_einheitenwechsel'):
        target, open target stock line and difference by hand are numbers in
        the house's unit, and a new one would read them anew. Leave or adopt
        the rest, then change after closing.
        """
        for form in self.forms:
            daten = form.cleaned_data
            satz = form.satz
            if (
                satz is None
                or daten.get("vorgehen") != services.AKTUALISIEREN
                or not services.uebernimmt(daten, services.EINHEIT)
                or daten.get("einheit") == satz.basiseinheit.kennung
            ):
                continue
            if inventuren := satz.einheit_gebunden_in():
                form.add_error(
                    "vorgehen", einheit_gebunden(satz.name, inventuren, satz.basiseinheit)
                )

    def _neue_namen_pruefen(self) -> None:
        """A new product needs a name that does not exist yet.

        The same designation under another number is fine as a house article.
        But the **product** is found by name: if it stayed, the row would
        silently land on the existing product, the one the plan precisely did
        not preselect because the house already carries it under another number.
        """
        vorhandene = {ware.name.lower(): ware for ware in self.warenvorrat.values()}
        for form in self.forms:
            daten = form.cleaned_data
            if (
                daten.get("ware")
                or daten["art"] == services.FEHLT
                or daten["vorgehen"] in (services.UEBERGEHEN, services.ZURUECKHOLEN)
            ):
                continue
            if (ware := vorhandene.get(services.warename(daten).lower())) is not None:
                form.add_error(
                    "neuer_ware",
                    _(
                        "Eine Ware „%(name)s“ gibt es schon. Ist es dieselbe, "
                        "oben auswählen. Sonst braucht die neue einen eigenen Namen."
                    )
                    % {"name": ware.name},
                )

    @staticmethod
    def _nicht_zaehlbar_pruefen(form) -> None:
        """Ignored is the product, and thus in every house.

        A product from "Not countable" therefore cannot be created active, and a
        product a house really carries cannot be ignored.
        """
        daten = form.cleaned_data
        vorgehen = daten["vorgehen"]
        # A missing row chooses no product, it keeps its own.
        if (gewaehlt := daten.get("ware")) is None or daten["art"] == services.FEHLT:
            return
        if vorgehen in (services.UEBERGEHEN, services.ZURUECKHOLEN):
            return
        ignorieren = (
            vorgehen != services.AKTUALISIEREN
            and services.status_der_zeile(daten) == services.IGNORIEREN
        )
        if gewaehlt.ist_nicht_zaehlbar and services.belegt_platz(daten):
            form.add_error(
                "ware",
                _(
                    "„%(name)s“ gehört zu „Nicht zählbar“. Entweder „Nicht zählbar“ wählen "
                    "oder eine andere Ware wählen."
                )
                % {"name": gewaehlt.name},
            )
        elif ignorieren and not gewaehlt.ist_nicht_zaehlbar:
            if gewaehlt.hausartikel.exclude(status=Artikelstatus.IGNORIERT).exists():
                form.add_error(
                    "ware",
                    _(
                        "„%(name)s“ wird als Ware geführt. Nicht zählbar ist die Ware in "
                        "jedem Haus. Hier deshalb eine neue Ware benennen."
                    )
                    % {"name": gewaehlt.name},
                )
                return
            # The same rule as on the product's button: in a recipe it does not work.
            try:
                gewaehlt.pruefe_familienwechsel(Warenfamilie.nicht_zaehlbar())
            except ValidationError as fehler:
                form.add_error("ware", [f"„{gewaehlt.name}“ {t}" for t in fehler.messages])

    def _belegte_waren(self) -> dict[int, Hausartikel]:
        """Per product the row that occupies it in the house, without those vacating their slot."""
        if self.haus is None:
            return {}
        # Whoever vacates their slot here no longer counts as occupant.
        ersetzt = set()
        zurueck = set()
        for form in self.forms:
            daten = form.cleaned_data
            if daten.get("vorgehen") == services.ERSETZEN:
                ersetzt.add(daten["vorhanden_pk"])
            elif (
                daten.get("art") == services.FEHLT and daten.get("vorgehen") == services.EINSTELLEN
            ):
                ersetzt.add(daten["vorhanden_pk"])
            elif daten.get("vorgehen") == services.ZURUECKHOLEN:
                # Whoever currently carries the number is discontinued in the process.
                zurueck.add(daten["artikelnummer"])
        return {
            artikel.ware_id: artikel
            for artikel in Hausartikel.objects.filter(haus=self.haus, status__in=GEFUEHRT)
            .exclude(pk__in=ersetzt)
            .exclude(artikelnummer__in=zurueck)
        }

    @property
    def offene_konflikte(self) -> int:
        return sum(form.ist_offener_konflikt for form in self.forms)

    def bloecke(self) -> list[Planblock]:
        """The rows per block and within it per product group.

        Blocks for the stock are only there if they contain something. The four
        for creating are always there: between them the rows migrate in the
        browser, and an empty block is a target there. For the same reason the
        two blocks for conflicts are only there together, because a conflict
        comes without preselection and migrates to the resolved ones with the
        decision.

        The same consideration applies one level down: where rows can migrate,
        all involved blocks keep the same product groups, the empty ones too. The
        script then only moves instead of building tables. Empty product groups
        it hides.
        """
        zeilen: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
        for form in self.forms:
            zeilen[self._blockname(form)][form.warengruppenname].append(form)
        konflikte = (services.KONFLIKT, BEHOBEN)
        mit_konflikten = any(zeilen[kennung] for kennung in konflikte)
        gezeigt = [
            Planblock(kennung, titel, self.ART_JE_BLOCK.get(kennung, kennung))
            for kennung, titel in self.BLOECKE_NEU
        ]
        gezeigt += [
            Planblock(kennung, titel, self.ART_JE_BLOCK.get(kennung, kennung))
            for kennung, titel in self.BLOECKE_BESTAND
            if zeilen[kennung] or (mit_konflikten and kennung in konflikte)
        ]
        self._gruppen_fuellen(gezeigt, zeilen)
        return gezeigt

    @staticmethod
    def _blockname(form) -> str:
        """In which block the row stands. For a new one the procedure says it."""
        art = form.zeilenart
        if art == services.KONFLIKT:
            return services.KONFLIKT if form.ist_offener_konflikt else BEHOBEN
        if art != services.NEU:
            return art
        vorgehen = form["vorgehen"].value()
        return vorgehen if vorgehen in dict(BaseImportFormSet.BLOECKE_NEU) else services.AKTIV

    def _gruppen_fuellen(
        self, gezeigt: list[Planblock], zeilen: dict[str, dict[str, list]]
    ) -> None:
        """Distributes the rows onto the product groups and numbers them.

        The number applies to the whole page: the same product group has the
        same in every block, otherwise the script would not find its counterpart
        when migrating.
        """
        namen = sorted({name for gruppen in zeilen.values() for name in gruppen})
        nummern = {name: nummer for nummer, name in enumerate(namen, start=1)}
        # Where rows migrate to, all blocks need the same product groups.
        gemeinsam: dict[str, list[str]] = defaultdict(list)
        for block in gezeigt:
            if block.art in (services.NEU, services.KONFLIKT):
                gemeinsam[block.art] += zeilen[block.kennung]
        for block in gezeigt:
            vorrat = gemeinsam.get(block.art) or list(zeilen[block.kennung])
            block.gruppen = [
                Warengruppenblock(nummern[name], name, zeilen[block.kennung][name])
                for name in sorted(set(vorrat))
            ]


# Django's default takes at most 2,000 rows back and shows only those again
# ('absolute_max'). The plan refuses more before it is shown ('plan_zu_gross').
ImportFormSet = cast(
    "type[BaseImportFormSet]",
    forms.formset_factory(
        ImportZeileForm,
        formset=BaseImportFormSet,
        extra=0,
        max_num=HOECHSTENS_PLANZEILEN,
        absolute_max=HOECHSTENS_PLANZEILEN,
    ),
)
