"""Filter and sort: the inventory list, the evaluation and the check list."""

from django import forms
from django.db.models import QuerySet
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from django_project.columns import Spaltensortierung
from django_project.labels import Beschriftung
from django_project.list_filters import ListenFilterForm, Mehrfachwahl
from katalog.forms.filters import (
    OHNE_FAMILIE as OHNE_FAMILIE_WAHL,
)
from katalog.forms.filters import (
    MitFamilienbaum,
    familien_geordnet,
    familieneintraege,
    familienstruktur,
)
from katalog.models import Inventurart, Warenfamilie
from organisation.models import Haus

from ..evaluation import OHNE_FAMILIE, Familienauswahl
from ..models import (
    Inventur,
    Inventurstatus,
)


class InventurFilterForm(ListenFilterForm):
    """Status, house and type over the GET parameters of the inventory list.

    Sorted only by cut-off date, in both directions: the list combines the
    inventories of a run (its cut-off date) into one block, and a sort by
    house would tear exactly these blocks apart.
    """

    SORTIERUNGEN = {
        "neueste": (_("Neueste zuerst"), ("-stichtag", "haus__name")),
        "aelteste": (_("Älteste zuerst"), ("stichtag", "haus__name")),
    }
    STANDARD_SORTIERUNG = "neueste"
    AUSWAHLFELDER = ("status", "haus", "inventurart", "sortierung")
    FILTERFELDER = ("status", "haus", "inventurart")

    status = forms.ChoiceField(
        choices=(("", _("Jeder Status")), *Inventurstatus.choices),
        required=False,
        label=_("Status"),
    )
    haus = forms.ModelChoiceField(
        queryset=Haus.objects.all(),
        required=False,
        empty_label=_("Alle Häuser"),
        label=_("Haus"),
    )
    # The inventories that count this type, among others perhaps.
    inventurart = forms.ModelChoiceField(
        queryset=Inventurart.objects.all(),
        required=False,
        empty_label=_("Jede Art"),
        label=_("Inventurart"),
    )
    sortierung = forms.ChoiceField(
        choices=[(schluessel, titel) for schluessel, (titel, _) in SORTIERUNGEN.items()],
        required=False,
        label=_("Sortierung"),
    )

    def anwenden(self, queryset: QuerySet[Inventur]) -> QuerySet[Inventur]:
        """No column headers: the list has no cut-off date column, it is in the run's header.

        Any other sort would tear the runs apart, so it stays with the choice
        between newest and oldest first.
        """
        daten = self.daten
        schluessel = daten.get("sortierung") or self.STANDARD_SORTIERUNG
        return self.filtern(queryset, daten).order_by(*self.SORTIERUNGEN[schluessel][1])

    def filtern(self, queryset: QuerySet[Inventur], daten: dict) -> QuerySet[Inventur]:
        if status := daten.get("status"):
            queryset = queryset.filter(status=status)
        if haus := daten.get("haus"):
            queryset = queryset.filter(haus=haus)
        if art := daten.get("inventurart"):
            queryset = queryset.filter(inventurarten=art)
        return queryset


class AuswertungForm(ListenFilterForm):
    """Which closed inventories are compared, and which product families.

    Meant for tracking down deviations that repeat: a forgotten movement of
    goods between two houses shows up as a plus there and a minus here, and a
    miscount remains a one-off outlier.

    The families are a multiple selection as in the article lists: whoever
    wants to print only the drinks leaves the rest out. Exactly the ticked
    families count. The menu sets the families below a ticked one.

    The PDF may carry its own title ('titel'). A report narrowed to the drinks
    of one summer is filed under that name and not under the generic heading.
    The page itself keeps its heading, only the sheet changes.
    """

    #: The heading of the sheet when nobody gives one.
    STANDARD_TITEL = _("Differenzen über Inventuren")

    von = forms.DateField(
        required=False,
        label=_("Stichtag ab"),
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
    )
    bis = forms.DateField(
        required=False,
        label=_("Stichtag bis"),
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
    )
    haeuser = forms.ModelMultipleChoiceField(
        queryset=Haus.objects.all(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label=_("Häuser"),
        help_text=_("Ohne Auswahl zählen alle Häuser mit."),
    )
    nur_mehrere_haeuser = forms.BooleanField(
        required=False,
        label=_("Nur Waren, die in mehreren Häusern gezählt wurden"),
        help_text=_(
            "Für die Suche nach Warenbewegungen: was nur ein Haus führt, kann "
            "nicht zwischen Häusern verrutscht sein."
        ),
    )
    familie = forms.MultipleChoiceField(required=False, label=_("Warenfamilien"))
    titel = forms.CharField(
        required=False,
        max_length=100,
        label=_("Titel des PDF"),
        help_text=_("Steht über dem PDF. Leer bleibt es bei „Differenzen über Inventuren“."),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["titel"].widget.attrs["placeholder"] = self.STANDARD_TITEL
        familien = familien_geordnet(Warenfamilie.objects.exclude(sammelt_ignorierte=True))
        self.familienwahl = Mehrfachwahl(
            self,
            "familie",
            [*familieneintraege(familien), (OHNE_FAMILIE_WAHL, OHNE_FAMILIE)],
            vorgabe_text=_("Alle Warenfamilien"),
            kursiv=(OHNE_FAMILIE_WAHL,),
            struktur=familienstruktur(familien),
        )
        self.mehrfachwahlen = (self.familienwahl,)

    def clean(self) -> dict:
        super().clean()
        daten = self.cleaned_data
        von, bis = daten.get("von"), daten.get("bis")
        if von and bis and von > bis:
            self.add_error("bis", _("Das Ende liegt vor dem Anfang."))
        return daten

    @property
    def familien(self) -> Familienauswahl:
        """The chosen families, or nothing for all.

        'None' among the numbers stands for the products without a family.
        """
        if not self.familienwahl.ist_eingeschraenkt:
            return None
        gewaehlt: set[int | None] = set(self.familienwahl.nummern())
        if OHNE_FAMILIE_WAHL in self.familienwahl:
            gewaehlt.add(None)
        return gewaehlt

    @property
    def pdf_titel(self) -> str:
        """The heading of the sheet: the given one, or the default if empty.

        A title over the limit is missing from 'daten' and the sheet falls back
        to the default, while the error stays at the field.
        """
        return (self.daten.get("titel") or "").strip() or str(self.STANDARD_TITEL)

    @property
    def pdf_dateiname(self) -> str:
        """The file is named after the given title, so two sheets do not save as the same name.

        Without a title the generic name stays as before. So it does for a
        title of nothing but punctuation, which leaves no slug behind: the
        file would otherwise be called ".pdf".
        """
        return slugify(self.daten.get("titel") or "") or "auswertung-differenzen"

    @property
    def familien_text(self) -> str:
        """What was chosen, for the head of the sheet, each with its path. Empty if everything."""
        if not self.familienwahl.ist_eingeschraenkt:
            return ""
        gewaehlt = set(self.familienwahl.gewaehlt)
        namen = [
            familie.anzeigepfad
            for familie in familien_geordnet(
                Warenfamilie.objects.filter(pk__in=self.familienwahl.nummern())
            )
            if str(familie.pk) in gewaehlt
        ]
        if OHNE_FAMILIE_WAHL in gewaehlt:
            namen.append(str(OHNE_FAMILIE))
        return ", ".join(namen)


class PrueflisteFilterForm(MitFamilienbaum):
    """Search and sort of the check list.

    Unlike the other filter forms this one works over a **Python list**: the
    rows are computed ('services.Ergebniszeile') and thus stand in no table
    against which `order_by` could be directed. 'anwenden' therefore sorts
    itself, and the fields in 'SPALTEN' are attribute paths of the row instead
    of columns.

    Grouping by product group or product family expands and collapses the
    groups individually. In them the number comes first.
    """

    SPALTEN = {
        # Numbers as numbers ('nummer_sortierschluessel'), not as text.
        "nummer": (_("Nr."), ("nummernschluessel",)),
        "name": (_("Bezeichnung"), ("hausartikel.ware.name",)),
        "warengruppe": (_("Warengruppe"), ("warengruppe",)),
        "ist": (_("Ist"), ("gesamtmenge_basiseinheit",)),
        "soll": (_("Soll"), ("sollbestand",)),
        "differenz": (_("Diff."), ("differenz",)),
    }
    STANDARD_SPALTE = "nummer"
    ZWEITSCHLUESSEL = ("nummernschluessel",)
    GLIEDERUNGEN = (
        ("warengruppe", _("Nach Warengruppe"), ("warengruppe",)),
        ("familie", _("Nach Warenfamilie"), ("warenfamilie",)),
    )
    AUSWAHLFELDER = ("abweichung",)
    FILTERFELDER = ("q", "abweichung")

    NUR_ABWEICHUNG = "abweichung"
    NUR_OFFEN = "offen"
    NUR_NOTIZ = "notiz"

    @property
    def ist_eingeschraenkt(self) -> bool:
        """Grouping and sorting are remembered with the view, so the reset shows for them too."""
        return (
            super().ist_eingeschraenkt
            or self.gliederung is not None
            or bool(self.daten.get("sortierung"))
        )

    q = forms.CharField(
        required=False,
        label=_("Suche"),
        widget=forms.TextInput(
            attrs={"placeholder": _("Nummer, Bezeichnung oder Warengruppe"), "type": "search"}
        ),
    )
    abweichung = forms.ChoiceField(
        choices=(
            ("", _("Alle Artikel")),
            (NUR_ABWEICHUNG, _("Nur mit Abweichung")),
            (NUR_OFFEN, _("Nur noch nicht gezählte")),
            (NUR_NOTIZ, _("Nur mit Notiz")),
        ),
        required=False,
        label=_("Zeigen"),
    )

    def anwenden(self, zeilen: list) -> list:
        """Filter and sort over attributes instead of SQL."""
        gefiltert = self.filtern(zeilen, self.daten)
        if (gliederung := self.gliederung) is not None:
            return Spaltensortierung(
                "gruppe", {"gruppe": ("", gliederung[2])}, "gruppe", self.ZWEITSCHLUESSEL
            ).sortieren(gefiltert)
        spalten = self.spalten
        sortiert = spalten.sortieren(gefiltert)
        if spalten.ruht:
            # Stable: within a tier the order of the columns stays.
            begriff = self.suchbegriff.casefold()
            sortiert.sort(key=lambda zeile: self._rang_der_zeile(zeile, begriff))
        return sortiert

    def relevanz(self, begriff: str) -> tuple:
        """The rows are computed, so the tiers are too ('_rang_der_zeile').

        What stands here only says that this list knows tiers at all.
        """
        return ("bezeichnung beginnt", "bezeichnung enthält", "nummer")

    @staticmethod
    def _rang_der_zeile(zeile, begriff: str) -> int:
        """The same tiers as in the house article list, over attributes."""
        namen = (zeile.hausartikel.ware.name.casefold(), zeile.hausartikel.name.casefold())
        if any(name.startswith(begriff) for name in namen):
            return 0
        if any(begriff in name for name in namen):
            return 1
        if begriff in str(zeile.artikelnummer).casefold():
            return 2
        return 3

    def gruppenname(self, objekt, schluessel: str) -> Beschriftung:
        if schluessel == "familie":
            return objekt.warenfamilie or OHNE_FAMILIE
        return objekt.warengruppe

    def familie_von(self, objekt) -> Warenfamilie | None:
        return objekt.hausartikel.ware.warenfamilie

    def filtern(self, zeilen: list, daten: dict) -> list:
        if begriff := daten.get("q", "").strip().casefold():
            zeilen = [zeile for zeile in zeilen if begriff in self._suchtext(zeile)]
        auswahl = daten.get("abweichung")
        if auswahl == self.NUR_ABWEICHUNG:
            zeilen = [zeile for zeile in zeilen if zeile.auffaellig]
        elif auswahl == self.NUR_OFFEN:
            zeilen = [zeile for zeile in zeilen if not zeile.gezaehlt]
        elif auswahl == self.NUR_NOTIZ:
            zeilen = [zeile for zeile in zeilen if zeile.notiz]
        return zeilen

    @staticmethod
    def _suchtext(zeile) -> str:
        """Both names: the search is sometimes for the product, sometimes for the house's name."""
        return (
            f"{zeile.artikelnummer} {zeile.hausartikel.ware.name} "
            f"{zeile.hausartikel.name} {zeile.warengruppe}"
        ).casefold()
