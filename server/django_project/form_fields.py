"""Hand out form fields with their real type.

'form.fields["x"]' is just a 'Field' for the type checker, without 'queryset',
'choices' or 'empty_label'. Instead of claiming with 'cast' at every place
what lies there, it is looked up once here: a typo in the field name or a
rebuilt field then shows up at the first call and not only when the
assignment silently goes nowhere.
"""

from decimal import Decimal

from django import forms
from django.db.models import QuerySet

from .decimals import dezimal_normalisieren


class Dezimalfeld(forms.DecimalField):
    """Accepts a comma as well as a point, in every language.

    Django's localized field accepts only the point in an English account, both
    in a German one. Whoever types "1,5" means one and a half in both. The input
    is therefore first put into the notation with a point ('dezimal_normalisieren'),
    then Django reads it as usual.

    The field is always localized, and Django then renders a text field: a
    stored value shows with the separator of the account's language. Until
    10/2026 a field without 'localize' became '<input type="number">', and there
    the browser decides about the comma. Chrome with an English interface
    refused "1,5" before the server ever saw it. 'inputmode' still brings up the
    number pad on a phone.
    """

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("localize", True)
        super().__init__(*args, **kwargs)

    def widget_attrs(self, widget):
        attrs = super().widget_attrs(widget)
        if isinstance(widget, forms.TextInput) and "inputmode" not in widget.attrs:
            attrs["inputmode"] = "decimal"
        return attrs

    def prepare_value(self, value):
        """A stored value without the trailing zeros of its column: "1", not "1,000".

        Whoever knows the point as the separator reads "1,000" as a thousand.
        The fixed-point format keeps 250 from becoming "2.5E+2". The separator
        of the account's language comes with 'localize'. What was typed is text
        and stays as it is.
        """
        if isinstance(value, Decimal) and value.is_finite():
            value = Decimal(f"{value.normalize():f}")
        return super().prepare_value(value)

    def to_python(self, value):
        if isinstance(value, str):
            value = dezimal_normalisieren(value)
        return super().to_python(value)


def feld_als[F: forms.Field](formular: forms.BaseForm, name: str, art: type[F]) -> F:
    feld = formular.fields[name]
    if not isinstance(feld, art):
        raise TypeError(f"Feld '{name}' ist {type(feld).__name__}, erwartet war {art.__name__}.")
    return feld


def modellwahl(formular: forms.BaseForm, name: str) -> forms.ModelChoiceField:
    """For the multiple choice too, because 'ModelMultipleChoiceField' inherits from it."""
    return feld_als(formular, name, forms.ModelChoiceField)


def auswahl(formular: forms.BaseForm, name: str) -> forms.ChoiceField:
    return feld_als(formular, name, forms.ChoiceField)


def wahlmenge(formular: forms.BaseForm, name: str) -> QuerySet:
    """What the field lets you choose from. Without a set it is built wrong, so better be loud."""
    menge = modellwahl(formular, name).queryset
    if menge is None:
        raise TypeError(f"Feld '{name}' hat keine Auswahlmenge.")
    return menge
