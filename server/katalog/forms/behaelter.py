"""Maintain containers: tare, houses and what is weighed in them."""

from django import forms
from django.utils.translation import gettext_lazy as _

from django_project.form_fields import Dezimalfeld, modellwahl

from ..models import Behaelter, Dimension, Rezepturartikel, Ware


class BehaelterForm(forms.ModelForm):
    """A vessel with its tare and what is weighed in it.

    The assignment is on the product or the recipe article, not here. But
    there are so few products for weighing that both are more conveniently
    maintained from the container. Django does not write reverse relations
    by itself, so 'save' does that.

    "Without container" is the special case from the data migration: it is
    available everywhere and has tare 0. Changing that would shift every
    weighing that used it. The field is therefore locked.
    """

    ware = forms.ModelMultipleChoiceField(
        queryset=Ware.objects.none(),
        required=False,
        label=_("Abgewogene Waren"),
        help_text=_("Waren, die in diesem Behälter auf die Waage kommen."),
    )
    rezepturartikel = forms.ModelMultipleChoiceField(
        queryset=Rezepturartikel.objects.none(),
        required=False,
        label=_("Rezepturartikel"),
        help_text=_("Vorbereitete Ware, die als Ganzes in diesem Behälter gewogen wird."),
    )

    class Meta:
        model = Behaelter
        fields = ("name", "tara", "haeuser")
        field_classes = {"tara": Dezimalfeld}
        widgets = {"haeuser": forms.CheckboxSelectMultiple}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        #: What the selections offer. 'save' keeps every assignment outside them.
        self.waren_zur_wahl = (
            # With unit: the label of every product names it ('__str__'), and
            # without the prefetch every row of the selection would ask separately.
            Ware.objects.echte()
            .filter(abwiegbar=True)
            .select_related("basiseinheit")
            .order_by("name")
        )
        self.rezepturartikel_zur_wahl = (
            Rezepturartikel.objects.filter(basiseinheit__dimension=Dimension.MASSE)
            .select_related("basiseinheit")
            .order_by("name")
        )
        modellwahl(self, "ware").queryset = self.waren_zur_wahl
        modellwahl(self, "rezepturartikel").queryset = self.rezepturartikel_zur_wahl

        if self.instance.pk:
            self.fields["ware"].initial = self.instance.waren.all()
            self.fields["rezepturartikel"].initial = self.instance.rezepturartikel.all()
        if self.instance.immer_erlaubt:
            # Its name comes from the account's language, not from the database.
            self.fields["name"].disabled = True
            self.initial["name"] = str(self.instance.anzeigename)
            self.fields["tara"].disabled = True
            self.fields["tara"].help_text = _(
                "Bei „Ohne Behälter“ fest 0, denn die Waage ist dann genullt."
            )
            # It applies everywhere. An assignment would have nothing to add and
            # would conversely suggest it applied only there.
            for name in ("haeuser", "ware", "rezepturartikel"):
                self.fields[name].disabled = True
                self.fields[name].help_text = _(
                    "„Ohne Behälter“ steht bei jeder Wiegung zur Wahl. "
                    "Eine eigene Zuordnung braucht er nicht."
                )

    def save(self, commit=True):
        """Writes the selection and keeps every assignment the selection does not offer.

        Offered are only weighable products outside "Not countable" and recipe
        articles in a mass. A product that went into "Not countable", or whose
        weighing was switched off, keeps its container that way and finds it
        again on its way back. Until 10/2026 'set()' dropped it silently, as
        soon as someone changed only the tare. The house form keeps the
        assignments of managers the same way.
        """
        behaelter = super().save(commit)
        if not commit:
            return behaelter
        angeboten = self.waren_zur_wahl.values("pk")
        behaelter.waren.set(
            [*self.cleaned_data["ware"], *behaelter.waren.exclude(pk__in=angeboten)]
        )
        angeboten = self.rezepturartikel_zur_wahl.values("pk")
        behaelter.rezepturartikel.set(
            [
                *self.cleaned_data["rezepturartikel"],
                *behaelter.rezepturartikel.exclude(pk__in=angeboten),
            ]
        )
        return behaelter
