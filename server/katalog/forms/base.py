"""What several form modules of the catalog share."""

from django import forms
from django.utils.html import conditional_escape
from django.utils.safestring import SafeString, mark_safe

from django_project.labels import Beschriftung


def hilfetext(vorlage: Beschriftung, **werte: Beschriftung) -> SafeString:
    """A help text with stored names in it, the names escaped.

    Help texts are HTML: crispy and Django's own field templates render them
    with '|safe'. A name someone typed (an inventory type, a pack) would
    otherwise reach the page as markup, a button inside the page's form
    included. Only the names are escaped, as 'format_html' does it, so the
    text keeps its '%(name)s' placeholders and with them its catalogue entry.
    """
    return mark_safe(vorlage % {name: conditional_escape(wert) for name, wert in werte.items()})


def an_stelle[T](folge: list[T], index: int | None) -> T | None:
    """The row for the form, or none: the empty template form has no index,
    and appended rows reach beyond the prefilled ones.
    """
    return folge[index] if index is not None and index < len(folge) else None


class EinheitenAuswahl(forms.Select):
    """Selection list that writes its dimension onto every option.

    Two forms follow it without reloading the page: the recipe article shows
    containers only with a mass unit, the product the field "grams per unit"
    only with all **others**. With mass a unit weighs exactly what it is
    called. Which unit has which dimension the browser does not know. Here it
    is told, instead of the script having to guess names or numbers.
    """

    def create_option(self, name, value, *args, **kwargs):
        option = super().create_option(name, value, *args, **kwargs)
        einheit = getattr(value, "instance", None)
        if einheit is not None:
            option["attrs"]["data-dimension"] = einheit.dimension
        return option


class WarenAuswahl(forms.Select):
    """Selection list of the products that writes unit and dimension onto every option.

    The form of the house article shows the conversion to the product only if
    the house counts in a different dimension than the chosen product. That
    hangs on the currently **chosen** product, so the browser decides, and for
    that it needs the dimension of every option. The queryset loads the unit
    along ('select_related'), otherwise every option would cost a query.
    """

    def create_option(self, name, value, *args, **kwargs):
        option = super().create_option(name, value, *args, **kwargs)
        ware = getattr(value, "instance", None)
        if ware is not None:
            option["attrs"]["data-dimension"] = ware.basiseinheit.dimension
            option["attrs"]["data-einheit"] = ware.basiseinheit.kurz
        return option


class MitKlartextfehler(forms.BaseInlineFormSet):
    """Formset that can set its own messages for violated uniqueness."""

    def _ohne_fremdschluessel(self, unique_check) -> tuple[str, ...]:
        return tuple(feld for feld in unique_check if feld != self.fk.name)
