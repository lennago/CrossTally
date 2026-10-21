"""What is pending: the inventories of the account, with rows, master data and check list."""

from django.db.models import Count, Exists, OuterRef
from django.utils import timezone
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from inventur.models import IST_DABEI, IST_GEZAEHLT, Inventur, Sollbestand, Sollzeile
from inventur.services import ergebniszeilen
from katalog.models import Inventurart

from ..master_data import artikel_des_hauses, behaelter_des_hauses
from ..permissions import IstAktiverBenutzer
from ..serializers import (
    ArtikelSerializer,
    BehaelterSerializer,
    InventurSerializer,
    OrtSerializer,
    PrueflisteZeileSerializer,
    ZaehlpositionSerializer,
    mitgeben,
    positionen_geladen,
)
from .base import seit_wann


def mit_sollkennzeichen(queryset):
    """Whether a target came in, as one annotation instead of two queries per row.

    'Inventur.hat_sollbestand' asks the database twice. The list of
    inventories is fetched by every device every twenty seconds, and until
    10/2026 it grew by two queries per inventory.
    """
    return queryset.annotate(
        hat_soll=Exists(Sollbestand.objects.filter(inventur=OuterRef("pk")))
        | Exists(Sollzeile.objects.filter(inventur=OuterRef("pk")))
    )


class InventurViewSet(viewsets.ReadOnlyModelViewSet):
    """What is pending for this user and what is to be counted in it.

    Listed is only what is **running**: an inventory in preparation is not
    released yet, a closed one is frozen. Both thus also vanish from the PWA.
    It clears away what the list no longer names.
    """

    serializer_class = InventurSerializer
    permission_classes = [IstAktiverBenutzer]

    def get_queryset(self):
        return mit_sollkennzeichen(
            Inventur.zaehlbare_fuer(self.request.user)
            .select_related("haus")
            .prefetch_related("inventurarten")
            .annotate(
                anzahl_positionen=Count("zaehlpositionen", filter=IST_DABEI, distinct=True),
                anzahl_gezaehlt=Count("zaehlpositionen", filter=IST_GEZAEHLT, distinct=True),
            )
            .distinct()
        )

    def retrieve(self, request, *args, **kwargs):
        """The inventory including its count positions.

        With '?geaendert_seit=' only the rows come along that have changed since
        then. That way the PWA fetches the difference after an interruption
        instead of the whole list. The header data always comes along, otherwise
        it would not know whether the inventory has been closed in the meantime.
        """
        inventur = self.get_object()
        positionen = positionen_geladen(inventur.zaehlpositionen.all())
        if (zeitpunkt := seit_wann(request)) is not None:
            # On sync the rows **marked as deleted** come along: only by that does the
            # app recognise that a row was deleted, and clears it from its storage.
            # On the first load they would only be ballast.
            positionen = positionen.filter(zuletzt_geaendert__gt=zeitpunkt)
        else:
            positionen = positionen.filter(aktiv=True)

        sichtbar = list(positionen)
        return Response(
            self.get_serializer(inventur).data
            | {
                "zaehlpositionen": ZaehlpositionSerializer(
                    sichtbar, many=True, context=mitgeben(sichtbar)
                ).data
            }
        )

    @action(detail=True, methods=["get"])
    def stammdaten(self, request, *args, **kwargs):
        """Locations, articles and containers of the house. Load once -> keep offline.

        Without that no unexpected position could be created: the PWA would have
        to guess which articles the house carries and which packs exist for them.
        The list changes rarely and is the natural candidate for the offline
        storage.
        """
        inventur = self.get_object()
        orte = inventur.haus.alle_orte().select_related("bereich").order_by("bereich__name", "name")
        return Response(
            {
                "orte": OrtSerializer(orte, many=True).data,
                "artikel": ArtikelSerializer(
                    artikel_des_hauses(inventur.haus, inventur.zaehlumfang), many=True
                ).data,
                # All types by name: the search names the type of an article
                # this inventory does not count.
                "inventurarten": [
                    {"id": art.pk, "name": art.name} for art in Inventurart.objects.all()
                ],
                # All containers, including "Without container": which belong to an
                # article is said by its 'behaelter' list. Here stand the tare and
                # how the container relates to the house.
                "behaelter": BehaelterSerializer(
                    behaelter_des_hauses(inventur.haus), many=True
                ).data,
            }
        )

    @action(detail=True, methods=["get"])
    def pruefliste(self, request, *args, **kwargs):
        """What closing would freeze, per article: actual, target, difference and note.

        The same rows as the check list on the website ('services.ergebniszeilen'),
        and for everyone who counts here, counters included: whoever sees where
        the count deviates can count again before anyone closes. Computed fresh
        on every call, from the counts of every device.
        """
        inventur = self.get_object()
        return Response(
            {
                "inventur": inventur.pk,
                "differenz_von_hand": inventur.differenz_von_hand,
                "hat_sollbestand": bool(inventur.hat_soll),
                "stand": timezone.now(),
                "zeilen": PrueflisteZeileSerializer(
                    ergebniszeilen(inventur),
                    many=True,
                    context={"von_hand": inventur.differenz_von_hand},
                ).data,
            }
        )
