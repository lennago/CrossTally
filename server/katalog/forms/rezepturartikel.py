"""Recipe articles and their recipe.

The same rules apply on the site and in the administration.
"""

from typing import cast

from django import forms
from django.contrib.admin.widgets import AutocompleteMixin
from django.utils.translation import gettext_lazy as _

from django_project.form_fields import Dezimalfeld, modellwahl
from django_project.labels import Beschriftung
from django_project.mixin_bases import ModellformularBasis

from ..models import (
    ANTEILE_NUR_BEIM_WIEGEN,
    Behaelter,
    Dimension,
    Rezepturartikel,
    Rezepturkomponente,
    Ware,
)
from .base import EinheitenAuswahl, MitKlartextfehler


class RezepturartikelRegeln(ModellformularBasis):
    """What applies to every recipe article, whichever door it arises through.

    The unit decides about the construction: in pieces it is counted, in g the
    whole container goes on the scale and is distributed only through the
    recipe. Only then are containers meaningful.

    The container check stands here and not in 'Rezepturartikel.clean': a
    multiple assignment is in a table of its own and is saved only after the
    model. There the check would still see the old state.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        modellwahl(self, "basiseinheit").queryset = Rezepturartikel.waehlbare_einheiten()
        modellwahl(self, "basiseinheit").empty_label = None
        if not self.instance.pk:
            self.fields["basiseinheit"].initial = (
                Rezepturartikel.waehlbare_einheiten().filter(dimension=Dimension.STUECK).first()
            )

    def clean(self):
        super().clean()
        daten = self.cleaned_data
        einheit = daten.get("basiseinheit")
        if einheit and einheit.dimension != Dimension.MASSE and daten.get("behaelter"):
            self.add_error(
                "behaelter",
                _("In %(einheit)s wird abgezählt, nicht gewogen. Behälter bleiben hier leer.")
                % {"einheit": einheit.kurz},
            )
        # The model says the same. Here so the message stands at the box, not
        # at the top of the form.
        if einheit and einheit.dimension != Dimension.MASSE and daten.get("anteilig"):
            self.add_error("anteilig", ANTEILE_NUR_BEIM_WIEGEN % {"einheit": einheit.kurz})
        return daten


class RezepturartikelForm(RezepturartikelRegeln, forms.ModelForm):
    class Meta:
        model = Rezepturartikel
        fields = ("name", "basiseinheit", "behaelter", "anteilig")
        widgets = {
            "basiseinheit": EinheitenAuswahl,
            "behaelter": forms.CheckboxSelectMultiple,
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # "Without container" is available everywhere anyway. As a box it would
        # be a decision that does not exist.
        modellwahl(self, "behaelter").queryset = Behaelter.zuweisbar()


class RezepturartikelAdminForm(RezepturartikelRegeln, forms.ModelForm):
    """The same rules in the admin, because there the second path leads to the same model.

    What the admin shows is determined by its fieldsets. Here stand only the
    fields the rules hang on.
    """

    class Meta:
        model = Rezepturartikel
        fields = ("name", "basiseinheit", "behaelter", "anteilig")


class BaseRezepturkomponenteFormSet(MitKlartextfehler):
    """A recipe article consists of products, not of house-related articles.

    With shares in percent the rows have to add up to 100. Only here are they
    all together, and the parent row is already saved when this runs
    ('InlineFormsetMixin'), so 'instance.anteilig' is what was just chosen.
    """

    #: The products to choose from, read once for all rows ('add_fields').
    _warenwahl: list | None = None

    def add_fields(self, form, index):
        super().add_fields(form, index)
        feld = modellwahl(form, "ware")
        feld.queryset = Ware.objects.echte().select_related("basiseinheit")
        # The administration searches ('autocomplete_fields'), its widget
        # wrapped in the buttons for adding. It shows only the chosen product
        # and needs Django's own choices.
        if isinstance(getattr(feld.widget, "widget", feld.widget), AutocompleteMixin):
            return
        # One list for every row and the empty row of the script. Each row read
        # all products anew (measured on 2026-10-18: six times on a recipe with
        # five ingredients). A sent row is still checked against the queryset.
        if self._warenwahl is None:
            self._warenwahl = list(feld.iterator(feld))
        feld.choices = self._warenwahl

    def clean(self):
        super().clean()
        if not self.instance.anteilig or any(self.errors):
            return
        Rezepturartikel.anteile_pruefen(
            form.cleaned_data["menge_in_basiseinheit"]
            for form in self.forms
            if form.cleaned_data and not form.cleaned_data.get("DELETE")
        )

    def get_unique_error_message(self, unique_check) -> Beschriftung:
        """Plain text instead of "Please correct the duplicate data for ware".

        In the interface a chosen product vanishes from the other rows. One gets
        here without JavaScript or through a second browser tab.

        'unique_check' still contains the foreign key here. Only
        'BaseInlineFormSet' in the superclass strips it.
        """
        if self._ohne_fremdschluessel(unique_check) == ("ware",):
            return _(
                "Jede Ware darf nur einmal in der Rezeptur stehen. "
                "Die Mengen gehören in eine Zeile."
            )
        return super().get_unique_error_message(unique_check)


RezepturkomponenteFormSet = cast(
    "type[BaseRezepturkomponenteFormSet]",
    forms.inlineformset_factory(
        Rezepturartikel,
        Rezepturkomponente,
        formset=BaseRezepturkomponenteFormSet,
        fields=("ware", "menge_in_basiseinheit"),
        field_classes={"menge_in_basiseinheit": Dezimalfeld},
        # Shares per gram are written with a comma in this country: "0,6". Django
        # accepts that only with localised fields. And for that swaps the <input
        # type="number"> for a text field, because the browser insists on a point there.
        localized_fields=("menge_in_basiseinheit",),
        extra=1,
        can_delete=True,
    ),
)
