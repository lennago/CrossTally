"""Who may count in an inventory."""

from django import forms
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext_lazy as _

from django_project.form_fields import modellwahl
from django_project.labels import Beschriftung

from ..models import Inventur
from ..services import InventurAbgeschlossen


class ZaehlendeForm(forms.Form):
    """Who may count in this inventory.

    Three groups but **one** selection: at the top the house's staff as a
    suggestion, below them the managers and finally all others. The managers
    stand on their own because they are a different question from "who helps
    from elsewhere?". Usually exactly one of them takes part, and under
    "further users" they went under in the list. The house assignment thus
    only suggests, it does not decide. Selection is explicit, otherwise every
    manager would silently count everywhere.

    Administrators are not available: they may count everywhere anyway
    ('Inventur.darf_zaehlen'), and a box for that would be a sham decision:
    unticked it would look as if they may not.
    """

    zaehlende = forms.ModelMultipleChoiceField(
        queryset=None,
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label=_("Zählende"),
    )

    def __init__(self, *args, inventur, **kwargs):
        super().__init__(*args, **kwargs)
        self.inventur = inventur
        benutzer = Inventur.eintragbare_zaehlende()
        modellwahl(self, "zaehlende").queryset = benutzer.order_by("last_name", "first_name")

        # The template renders the entries individually and needs per box the
        # information which group it belongs to. 'ModelChoiceField' does not
        # provide that.
        self.im_haus = set(inventur.vorgeschlagene_zaehlende().values_list("pk", flat=True))
        self.fuehrungskraefte = set(benutzer.filter(is_staff=True).values_list("pk", flat=True))

    def gruppen(self) -> list[tuple[Beschriftung, list, Beschriftung]]:
        """The boxes, separated into suggestion, managers and rest.

        The house assignment takes precedence: a manager who belongs to this
        house stands at the top with their people and not a second time below.

        The third value is the sentence for an **empty** group. It belongs here
        and not in the template: there it could only be guessed from the position
        in the loop, and in the '{% empty %}' branch 'forloop' already points to
        the outer loop. The distinction silently went wrong.
        """
        vorschlag, leitung, weitere = [], [], []
        for eintrag in self["zaehlende"]:
            # The value of a model field is a 'ModelChoiceIteratorValue'.
            # The number is inside, a direct int() does not work.
            wert = eintrag.data["value"]
            nummer = int(getattr(wert, "value", wert))
            if nummer in self.im_haus:
                vorschlag.append(eintrag)
            elif nummer in self.fuehrungskraefte:
                leitung.append(eintrag)
            else:
                weitere.append(eintrag)
        return [
            (
                _("Dem Haus %(haus)s zugeordnet") % {"haus": self.inventur.haus.name},
                vorschlag,
                _("Diesem Haus ist niemand zugeordnet. Das geschieht beim Benutzer."),
            ),
            (
                _("Führungskräfte"),
                leitung,
                _("Alle Führungskräfte stehen schon oben bei ihrem Haus."),
            ),
            (_("Weitere Benutzer"), weitere, _("Sonst gibt es niemanden.")),
        ]

    def speichern(self) -> int:
        """Enters the chosen ones, as long as the inventory runs.

        Read on the locked row, as the switches do: the page looked at the
        status before, and a closing in between cleared the assignment
        ('Inventur.abschliessen'). Until 10/2026 the chosen ones came back
        into the closed inventory although who counted is not kept. Locked
        without the key, as the closing locks it: one of the two waits. An
        inventory deleted in between is not found, as on any of its pages.
        """
        gewaehlt = self.cleaned_data["zaehlende"]
        with transaction.atomic():
            gesperrt = get_object_or_404(
                Inventur.objects.select_for_update(no_key=True), pk=self.inventur.pk
            )
            if not gesperrt.laeuft:
                raise InventurAbgeschlossen(gesperrt)
            gesperrt.zaehlende.set(gewaehlt)
        return len(gewaehlt)
