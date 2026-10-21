"""The two-step article import per house: read file, show plan, adopt."""

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.utils.translation import gettext as _
from django.views.generic import (
    FormView,
)

from django_project.views import (
    meldungen_gekuerzt,
)
from organisation.models import Haus

from ..formats import FORMATE, STANDARD, Lesefehler
from ..forms import (
    HOECHSTENS_PLANZEILEN,
    PLAN_PRAEFIX,
    ArtikelImportForm,
    ImportFormSet,
    plan_zu_gross,
)
from ..services import KONFLIKT, ausfuehren, planen


class HausartikelImportView(FormView):
    """Read in the article master of a house, in two steps.

    The first step reads the file and shows the plan. Nothing is written. The
    second takes the filled-in form and creates what is there. Both hang on
    the same address, distinguished by the hidden field 'schritt'. That way
    the way back on an error stays short.
    """

    form_class = ArtikelImportForm
    template_name = "katalog/import.html"
    plan_template = "katalog/import_plan.html"

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.haus = get_object_or_404(Haus, pk=kwargs["haus_pk"])

    def get_context_data(self, **kwargs):
        # The help names the limit of a plan ('templates/help/<sprache>/import.html').
        return super().get_context_data(**kwargs) | {
            "haus": self.haus,
            "hoechstens_planzeilen": HOECHSTENS_PLANZEILEN,
        }

    def post(self, request, *args, **kwargs):
        if request.POST.get("schritt") == "uebernehmen":
            return self.uebernehmen(request)
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        """Step 1: read and show the plan."""
        try:
            tabelle = form.cleaned_data["format"].artikelstamm_lesen(
                form.cleaned_data["datei"], self.haus.name
            )
        except Lesefehler as fehler:
            form.add_error("datei", str(fehler))
            return self.form_invalid(form)

        format = form.cleaned_data["format"]
        plan, uebergangen = planen(tabelle, self.haus, format)
        initial = self._initial(plan)
        if grund := plan_zu_gross(initial):
            # Before the plan is shown: sent back, the server would refuse it
            # whole ('plan_zu_gross'). The messages about skipped rows come
            # again with the smaller files.
            form.add_error("datei", grund)
            return self.form_invalid(form)
        meldungen_gekuerzt(
            self.request, uebergangen, _("… und %(anzahl)s weitere übergangene Zeilen.")
        )
        if not plan:
            # Either the file was empty, or the house already carries everything
            # exactly so. The message above says that then.
            messages.info(self.request, _("In der Datei steht nichts, was zu entscheiden wäre."))
            return self.form_invalid(form)

        return self.plan_zeigen(ImportFormSet(prefix=PLAN_PRAEFIX, initial=initial), format.kennung)

    @staticmethod
    def _initial(plan) -> list[dict]:
        return [
            {
                "artikelnummer": satz.artikelnummer,
                "warengruppe": satz.warengruppe,
                "einheit": satz.einheit,
                "art": satz.art,
                "vorhanden_pk": satz.vorhanden_pk,
                "name": satz.name,
                "ware": satz.ware_pk,
                "inventurart": satz.inventurart,
                "gebinde": satz.gebindetext,
                "gebinde_von": satz.gebinde_von,
                "gebinde_faktor": satz.gebinde_faktor,
                "gebinde_datei": satz.gebinde_dateitext,
                # What the house's row already carries, as the hint says
                # ('_umrechnung_anmerken'). The field's packs were converted with it.
                "faktor_zur_ware": satz.faktor_zur_ware,
                # On a conflict nothing is preselected: "Leave" would look like
                # decided, and the row would be there again on the next import.
                "vorgehen": "" if satz.art == KONFLIKT else satz.vorgehen,
                "status_neu": satz.status_neu,
                "hinweise": "\n".join(satz.hinweise),
                "abweichungen": ",".join(satz.abweichungen),
                # With "Change" everything is meant at first. Deselected is what should stay.
                "uebernehmen": list(satz.abweichungen),
            }
            for satz in plan
        ]

    def plan_zeigen(self, formset, kennung: str):
        """The plan, including the format's id: it rides along as a hidden field.

        Between the two steps lies no server state, and the new rows should know
        which system they came from.
        """
        return self.response_class(
            request=self.request,
            template=[self.plan_template],
            context=self.get_context_data(formset=formset, format=kennung),
            using=self.template_engine,
        )

    def uebernehmen(self, request):
        """Step 2: write what is in the form."""
        formset = ImportFormSet(request.POST, prefix=PLAN_PRAEFIX)
        formset.haus = self.haus
        # Not believed but looked up: a foreign id would otherwise fall as
        # source into every new row.
        kennung = request.POST.get("format", "")
        if kennung not in FORMATE:
            kennung = STANDARD
        if not formset.is_valid():
            messages.error(
                request, _("Bitte die markierten Zeilen prüfen. Es wurde nichts angelegt.")
            )
            if offen := formset.offene_konflikte:
                messages.error(
                    request,
                    _(
                        "Offene Konflikte: %(offen)s. Übernommen wird erst, wenn über jeden "
                        "entschieden ist."
                    )
                    % {"offen": offen},
                )
            return self.plan_zeigen(formset, kennung)

        bericht = ausfuehren([form.cleaned_data for form in formset.forms], self.haus, kennung)
        self._melden(bericht)
        return redirect(self.haus.get_absolute_url())

    def _melden(self, bericht) -> None:
        messages.success(
            self.request,
            _(
                "%(artikel_neu)s Artikel neu angelegt (davon %(artikel_inaktiv)s inaktiv, "
                "%(artikel_eingestellt)s eingestellt, %(artikel_ignoriert)s nicht zählbar), "
                "%(artikel_geaendert)s geändert, %(artikel_ersetzt)s ersetzt, "
                "%(artikel_zurueckgeholt)s zurückgeholt, %(uebergangen)s gelassen. Von den "
                "in der Datei fehlenden stehen %(artikel_fehlend_inaktiv)s jetzt auf inaktiv "
                "und %(artikel_fehlend_eingestellt)s auf eingestellt. Dazu "
                "%(ware_neu)s Waren, %(warengruppen_neu)s Warengruppen und "
                "%(gebinde_neu)s Gebinde. Von den Gebinden heißen %(gebinde_umbenannt)s "
                "jetzt anders, %(gebinde_stillgelegt)s stehen auf inaktiv und "
                "%(gebinde_reaktiviert)s wieder auf aktiv."
            )
            % {
                "artikel_neu": len(bericht.artikel_neu),
                "artikel_inaktiv": len(bericht.artikel_inaktiv),
                "artikel_eingestellt": len(bericht.artikel_eingestellt),
                "artikel_ignoriert": len(bericht.artikel_ignoriert),
                "artikel_geaendert": len(bericht.artikel_geaendert),
                "artikel_ersetzt": len(bericht.artikel_ersetzt),
                "artikel_zurueckgeholt": len(bericht.artikel_zurueckgeholt),
                "uebergangen": bericht.uebergangen,
                "artikel_fehlend_inaktiv": len(bericht.artikel_fehlend_inaktiv),
                "artikel_fehlend_eingestellt": len(bericht.artikel_fehlend_eingestellt),
                "ware_neu": len(bericht.ware_neu),
                "warengruppen_neu": len(bericht.warengruppen_neu),
                "gebinde_neu": bericht.gebinde_neu,
                "gebinde_umbenannt": bericht.gebinde_umbenannt,
                "gebinde_stillgelegt": bericht.gebinde_stillgelegt,
                "gebinde_reaktiviert": bericht.gebinde_reaktiviert,
            },
        )
        meldungen_gekuerzt(self.request, bericht.fehler, _("… und %(anzahl)s weitere Hinweise."))
