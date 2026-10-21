"""Maintain the inventory types: the name, and where the articles go when one is deleted."""

from django import forms
from django.utils.translation import gettext_lazy as _

from django_project.form_fields import modellwahl

from ..models import Inventurart


class InventurartForm(forms.ModelForm):
    """The name only. The order is set in the list, the articles on the article or the product.

    There the decision is due anyway: whoever maintains an article sees its
    type, and product and product group set many at once. A selection of
    hundreds of articles here would be a second way to the same thing.
    """

    class Meta:
        model = Inventurart
        fields = ("name",)


class InventurartLoeschenForm(forms.Form):
    """Into which type the articles of the deleted one move.

    Every house article has a type. Without the choice the deletion would
    either fail on them or leave them without one.
    """

    nachfolger = forms.ModelChoiceField(
        queryset=Inventurart.objects.none(),
        label=_("Ihre Artikel kommen nach"),
        empty_label=None,
    )

    def __init__(self, *args, art: Inventurart, **kwargs):
        super().__init__(*args, **kwargs)
        modellwahl(self, "nachfolger").queryset = Inventurart.objects.exclude(pk=art.pk)
