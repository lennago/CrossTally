"""The target stock: read in from a file, or by hand per article."""

from dataclasses import dataclass
from decimal import Decimal

from django import forms

from django_project.form_fields import Dezimalfeld
from katalog.forms import DateiImportForm

from ..services import Pflegezeile, Sollpflege, menge_text


class SollbestandImportForm(DateiImportForm):
    """The stock list of the inventory management system at the cut-off date.

    Single-stage: per article number one number comes, so the report
    afterwards is enough. A check form as with the article import would only
    be needed where master data arises.
    """

    DATEI = "sollbestand"


@dataclass
class Pflegefeld:
    """A row of the maintenance page with its two fields, for the template."""

    zeile: Pflegezeile
    soll: forms.BoundField
    frei: forms.BoundField
    #: Whether the row is editable: without target always, with target only after release.
    offen: bool


class SollPflegeForm(forms.Form):
    """The target per article by hand: enter, change, remove.

    Per article two fields: the value ('soll_<pk>') and the release
    ('frei_<pk>'). A target that is already in place is **locked**: its field
    is 'disabled', and for such a field Django takes only the initial value,
    whatever arrives in the form. The "Edit" button lifts that in the browser
    and sets the release. Only with it does the form read the value. A slip
    thus needs two steps, and a stray submit changes nothing that was not
    explicitly released.

    The quantity is in the house's unit, with a comma, as it is typed.
    Negative too: that is how an inventory management system reports a stock
    when more was booked than delivered, and the file accepts that as well.
    """

    def __init__(self, pflege: Sollpflege, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.pflege = pflege
        self.ohne: list[Pflegefeld] = []
        self.mit: list[Pflegefeld] = []
        for zeile in pflege.zeilen:
            pk = zeile.hausartikel.pk
            freigegeben = self.is_bound and self.data.get(f"frei_{pk}") == "1"
            offen = zeile.soll is None or freigegeben
            self.fields[f"soll_{pk}"] = Dezimalfeld(
                max_digits=12,
                decimal_places=3,
                localize=True,
                required=False,
                disabled=not offen,
                initial=menge_text(zeile.soll.menge_basiseinheit) if zeile.soll else None,
                label=zeile.hausartikel.name,
                widget=forms.TextInput(
                    attrs={
                        "class": "form-control form-control-sm text-end",
                        "inputmode": "decimal",
                        "data-soll": "",
                        # The field sits in a table cell without a <label>.
                        "aria-label": zeile.hausartikel.name,
                    }
                ),
            )
            self.fields[f"frei_{pk}"] = forms.BooleanField(
                required=False,
                initial=freigegeben,
                widget=forms.HiddenInput(attrs={"data-freigabe": ""}),
            )
            feld = Pflegefeld(zeile, self[f"soll_{pk}"], self[f"frei_{pk}"], offen)
            (self.mit if zeile.soll is not None else self.ohne).append(feld)

    def eingaben(self) -> dict[int, Decimal | None]:
        """What is to be written, per house article. Only open rows, only changes.

        An article without target and without input stays without. A released one
        with an empty field loses its target ('None').
        """
        daten = self.cleaned_data
        eingaben: dict[int, Decimal | None] = {}
        for feld in self.ohne + self.mit:
            if not feld.offen:
                continue
            pk = feld.zeile.hausartikel.pk
            menge = daten.get(f"soll_{pk}")
            if feld.zeile.soll is None and menge is None:
                continue
            eingaben[pk] = menge
        return eingaben
