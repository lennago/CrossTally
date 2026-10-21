"""The common base for splitting and merging: location, product family, product.

Three subjects can be split, two of them also merged here, and the forms
resembled each other row by row until 09/2026: two names that must be
neither empty nor equal nor taken, plus the boxes for what migrates over.
Here the rule stands once. The subclass creates the fields, because
label, length and selection belong to its subject, and says who counts as
a namesake.

The product handles its merging itself ('WareZusammenfuehrenForm'): there
not necessarily the product of the page stays, and it asks no name.
"""

from django import forms
from django.db.models import QuerySet

from django_project.form_fields import modellwahl
from django_project.labels import Beschriftung


class AufteilenForm(forms.Form):
    """One row becomes two: the old one stays, next to it a new one arises.

    Rendered as two columns (left what stays, right what goes), and a click
    moves a row over ('split.js'). Without JavaScript everything stays on the
    left, and the chosen rows are highlighted. The form gets the same data in
    both cases: 'name', 'neuer_name' and the boxes 'wandern'.
    """

    #: The message if both halves would get the same name.
    gleiche_namen: Beschriftung

    def __init__(self, *args, satz, **kwargs):
        super().__init__(*args, **kwargs)
        self.satz = satz
        self.fields["name"].initial = satz.name
        modellwahl(self, "wandern").queryset = self.zur_wahl()

    def zur_wahl(self) -> QuerySet:
        """What can migrate over to the new row."""
        raise NotImplementedError

    def namensvettern(self) -> QuerySet:
        """Who can contest the name. The row itself does not count."""
        raise NotImplementedError

    def vergeben(self, name: str) -> str:
        """The message if 'name' already belongs to someone."""
        raise NotImplementedError

    def clean(self) -> dict:
        super().clean()
        daten = self.cleaned_data
        name, neuer_name = daten.get("name", "").strip(), daten.get("neuer_name", "").strip()
        if not name or not neuer_name:
            return daten
        if name == neuer_name:
            self.add_error("neuer_name", self.gleiche_namen)
        andere = self.namensvettern()
        if andere.filter(name=name).exists():
            self.add_error("name", self.vergeben(name))
        if andere.filter(name=neuer_name).exists():
            self.add_error("neuer_name", self.vergeben(neuer_name))
        daten["name"], daten["neuer_name"] = name, neuer_name
        return daten


class ZusammenfuehrenForm(forms.Form):
    """A second row is absorbed into this one: the choice 'quelle' and the name afterwards.

    The name of the absorbed row may be taken over. It is deleted before the
    remaining one is renamed and therefore does not count as taken.
    """

    def __init__(self, *args, ziel, **kwargs):
        super().__init__(*args, **kwargs)
        self.ziel = ziel
        self.fields["name"].initial = ziel.name
        modellwahl(self, "quelle").queryset = self.kandidaten()

    def kandidaten(self) -> QuerySet:
        """What can be absorbed into 'ziel'."""
        raise NotImplementedError

    def namensvettern(self) -> QuerySet:
        """Who can contest the name. The remaining row does not count."""
        raise NotImplementedError

    def vergeben(self, name: str) -> str:
        """The message if 'name' already belongs to someone."""
        raise NotImplementedError

    def clean(self) -> dict:
        super().clean()
        daten = self.cleaned_data
        name = daten.get("name", "").strip()
        if not name:
            return daten
        andere = self.namensvettern()
        if (quelle := daten.get("quelle")) is not None:
            andere = andere.exclude(pk=quelle.pk)
        if andere.filter(name=name).exists():
            self.add_error("name", self.vergeben(name))
        daten["name"] = name
        return daten
