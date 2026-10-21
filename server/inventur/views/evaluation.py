"""The report across closed inventories."""

from django.views.generic import TemplateView

from ..evaluation import (
    abgeschlossene,
    familien_ueber_inventuren,
)
from ..forms import (
    AuswertungForm,
)
from ..pdf import als_pdf


class AuswertungView(TemplateView):
    """Differences over several inventories: the view across houses and months.

    If the same deviation repeats for the same product, a movement of goods
    is usually missing. A one-off outlier was more likely a miscount. That is
    why the **differences** stand here, summed per product and grouped by
    product family.

    Actual and target only come along if exactly one inventory per house is
    in the selection. Otherwise stocks would be added up over time, and that
    is no number but a misunderstanding.
    """

    template_name = "inventur/evaluation.html"

    def get_context_data(self, **kwargs):
        form = AuswertungForm(self.request.GET or None)
        # 'daten' drops only the faulty field: a title over the limit must not
        # throw the period and the houses out as well.
        daten = form.daten
        inventuren = list(
            abgeschlossene(haeuser=daten.get("haeuser"), von=daten.get("von"), bis=daten.get("bis"))
        )
        nur_mehrere_haeuser = daten.get("nur_mehrere_haeuser", False)
        bloecke, bestaende_sinnvoll = familien_ueber_inventuren(
            inventuren, nur_mehrere_haeuser=nur_mehrere_haeuser, familien=form.familien
        )
        return super().get_context_data(**kwargs) | {
            "form": form,
            "inventuren": inventuren,
            "bloecke": bloecke,
            "bestaende_sinnvoll": bestaende_sinnvoll,
            "anzahl_waren": sum(block.anzahl for block in bloecke),
            "nur_mehrere_haeuser": nur_mehrere_haeuser,
            "familien_text": form.familien_text,
            "titel": form.pdf_titel,
            **self.kopf_des_blatts(inventuren),
        }

    @staticmethod
    def kopf_des_blatts(inventuren) -> dict:
        """Period and houses instead of every single inventory.

        One inventory per house and month quickly makes fifty entries over a
        year, and the header would be longer than the first table. Named is what
        is actually in the selection, not the filter: an empty "cut-off date
        from" says nothing on paper, the earliest cut-off date does.
        """
        stichtage = [inventur.stichtag for inventur in inventuren]
        return {
            "stichtag_von": min(stichtage, default=None),
            "stichtag_bis": max(stichtage, default=None),
            "hausnamen": sorted({inventur.haus.name for inventur in inventuren}),
        }

    def get(self, request, *args, **kwargs):
        kontext = self.get_context_data(**kwargs)
        if request.GET.get("pdf") != "1":
            return self.render_to_response(kontext)
        return als_pdf(request, "pdf/evaluation.html", kontext, kontext["form"].pdf_dateiname)
