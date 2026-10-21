"""The product group: only its name, everything else derives from the house articles."""

from django import forms

from ..models import (
    Warengruppe,
)


class WarengruppeForm(forms.ModelForm):
    """Only the name. Whether the group is active is said by its house articles."""

    class Meta:
        model = Warengruppe
        fields = ("name",)
