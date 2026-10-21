"""Where the app writes its count positions and weighings."""

from http import HTTPStatus

from django.db import transaction
from django.http import Http404
from django.utils.translation import gettext_lazy as _
from rest_framework import mixins, viewsets
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response

from django_project.validation import ganzzahl
from inventur.models import Inventur, Wiegung, Zaehlposition

from ..checks import zaehlbar_unter_sperre
from ..permissions import IstAktiverBenutzer
from ..serializers import (
    ZEILE_ENTFERNT,
    Mengenkonflikt,
    WiegungSerializer,
    ZaehlpositionAnlegenSerializer,
    ZaehlpositionEingabeSerializer,
    ZaehlpositionSerializer,
    gesperrte_zeile,
    mitgeben,
    positionen_geladen,
)
from .base import seit_wann


class ZaehlpositionViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """Where the PWA writes its count positions.

    Changing means: enter a quantity. Weighings go through 'WiegungViewSet',
    remarks on an article through 'pruefliste_zeile'.
    Creating means: something stands here that nobody expected.

    **Deleting is not real**: the row is marked as deleted ('aktiv'). It thus
    vanishes from the list at once, but it is still there, and because its
    'zuletzt_geaendert' is set anew in the process, every other counting app
    learns of the deletion on the next sync too. A row that simply went
    missing would never arrive there. At closing all vanish anyway.

    **The list wants a filter**, '?inventur=' or '?geaendert_seit=' ('list').
    Until 10/2026 it also answered without one, with every row of every
    inventory the account counts in.
    """

    permission_classes = [IstAktiverBenutzer]

    def get_queryset(self):
        """Only positions from inventories the user is assigned to.

        The **list** names only what is in the count, like the list of
        inventories. A single row stays reachable in every status, so a write
        attempt gets the answer "counting happens only during the count" instead
        of a 404, which would look to the app like a deleted row.
        """
        inventuren = (
            Inventur.zaehlbare_fuer(self.request.user)
            if self.action == "list"
            else Inventur.fuer_zaehler(self.request.user)
        )
        queryset = positionen_geladen(Zaehlposition.objects.filter(inventur__in=inventuren))

        if (inventur_id := self.request.query_params.get("inventur")) is not None:
            if (nummer := ganzzahl(inventur_id)) is None:
                raise ValidationError({"inventur": _("Erwartet wird eine Nummer.")})
            queryset = queryset.filter(inventur_id=nummer)
        if (zeitpunkt := seit_wann(self.request)) is not None:
            # As in the inventory detail: on sync rows marked as deleted come along
            # so the app learns of the deletion. Without a point in time they are
            # only ballast.
            queryset = queryset.filter(zuletzt_geaendert__gt=zeitpunkt)
        else:
            queryset = queryset.filter(aktiv=True)
        return queryset.order_by("zuletzt_geaendert", "pk")

    def get_object(self):
        """A row marked as deleted says so, instead of "not found".

        Device A counts offline, device B removes the row. A's quantity then
        met a bare 404, and whoever read the entry under "not accepted" did not
        know why (until 10/2026). The number itself stays in that entry.
        """
        try:
            return super().get_object()
        except Http404:
            # Django's 404 at this point. DRF turns it into its own only later.
            kennung = str(self.kwargs.get("pk", ""))
            weg = Zaehlposition.objects.filter(
                pk=ganzzahl(kennung),
                aktiv=False,
                inventur__in=Inventur.fuer_zaehler(self.request.user),
            )
            if weg.exists():
                raise NotFound(ZEILE_ENTFERNT) from None
            # Gone for good (closing cleared it) or never this person's: in
            # the app's words, not Django's "No Zaehlposition matches …".
            raise NotFound(_("Diese Zählposition ist hier nicht (mehr) bekannt.")) from None

    def list(self, request, *args, **kwargs):
        """Like the default, only with the suggested values in the luggage, and never all at once.

        Without '?inventur=' or '?geaendert_seit=' the answer carried every
        row of every inventory the account counts in, for an account that
        counts in every house a few hundred kilobytes at once (until 10/2026).
        Nobody needs that: the app loads the rows with their inventory
        ('inventuren/<pk>/') and afterwards only what changed. A 400 and no
        pages: the app wants an inventory whole anyway, and pages would be one
        more state for it to keep. An empty point in time is none, as in
        'seit_wann'. An empty number is a wrong one and is told so by
        'get_queryset'.
        """
        parameter = request.query_params
        if parameter.get("inventur") is None and not parameter.get("geaendert_seit"):
            # A text, as the filters' own answers in 'get_queryset'.
            raise ValidationError(
                {
                    "detail": _(
                        "Erwartet wird „inventur“ oder „geaendert_seit“. Alle Zeilen aller "
                        "Inventuren auf einmal gibt es nicht."
                    )
                }
            )
        return Response(self._mit_vorgaben(list(self.filter_queryset(self.get_queryset())), True))

    def retrieve(self, request, *args, **kwargs):
        return Response(self._mit_vorgaben([self.get_object()], False))

    def update(self, request, *args, **kwargs):
        """Like the default, only that a foreign value becomes a question instead of a loss.

        The 409 response brings the row as it stands **now**. The app stores it
        and asks the person what should happen with their number (see
        'ZaehlpositionEingabeSerializer').
        """
        try:
            return super().update(request, *args, **kwargs)
        except Mengenkonflikt:
            return Response(
                {
                    "konflikt": "eingabemenge",
                    "detail": _("Hier hat inzwischen jemand anderes gezählt."),
                    "aktuell": self._mit_vorgaben([self.get_object()], False),
                },
                status=HTTPStatus.CONFLICT,
            )

    def _mit_vorgaben(self, positionen, viele):
        """The suggested values come from **one** query over all rows."""
        kontext = {**self.get_serializer_context(), **mitgeben(positionen)}
        daten = positionen if viele else positionen[0]
        return ZaehlpositionSerializer(daten, many=viele, context=kontext).data

    def get_serializer_class(self):
        if self.action == "create":
            return ZaehlpositionAnlegenSerializer
        if self.action in ("update", "partial_update"):
            return ZaehlpositionEingabeSerializer
        return ZaehlpositionSerializer

    def perform_destroy(self, instance):
        """Mark as deleted instead of deleting, and only while counting is running."""
        with transaction.atomic():
            # Under the inventory's lock: a closing in between has cleared the
            # rows away, and this one would otherwise fail as a server error.
            zaehlbar_unter_sperre(instance.inventur)
            instance.aktiv = False
            # 'zuletzt_geaendert' hangs on 'auto_now' and only applies on 'save'.
            instance.save(update_fields=["aktiv", "zuletzt_geaendert"])


class WiegungViewSet(viewsets.ModelViewSet):
    """A vessel on the scale, as often as the product occurs at a location.

    **The list wants a filter**, '?zaehlposition=' ('list'). Until 10/2026 it
    also answered without one, with every weighing of every inventory the
    account counts in.
    """

    serializer_class = WiegungSerializer
    permission_classes = [IstAktiverBenutzer]

    def list(self, request, *args, **kwargs):
        """The weighings of one row, never all at once.

        Nobody needs all of them: every row brings its weighings along
        ('wiegungen'), and the app creates, corrects and removes them one by
        one. A 400 and no pages, as for the count positions
        ('ZaehlpositionViewSet.list'). An empty number is a wrong one and is
        told so by 'get_queryset'.
        """
        if request.query_params.get("zaehlposition") is None:
            # A text, as the filter's own answer in 'get_queryset'.
            raise ValidationError(
                {
                    "detail": _(
                        "Erwartet wird „zaehlposition“. Alle Wiegungen aller Inventuren auf "
                        "einmal gibt es nicht."
                    )
                }
            )
        return super().list(request, *args, **kwargs)

    def get_queryset(self):
        """Only weighings from inventories the user is assigned to."""
        inventuren = Inventur.fuer_zaehler(self.request.user)
        # 'netto' computes via the concrete article and both units.
        queryset = Wiegung.objects.filter(zaehlposition__inventur__in=inventuren).select_related(
            "behaelter",
            "zaehlposition__artikel__hausartikel__basiseinheit",
            "zaehlposition__artikel__hausartikel__ware__basiseinheit",
            "zaehlposition__artikel__rezepturartikel__basiseinheit",
        )

        if (position := self.request.query_params.get("zaehlposition")) is not None:
            if (nummer := ganzzahl(position)) is None:
                raise ValidationError({"zaehlposition": _("Erwartet wird eine Nummer.")})
            queryset = queryset.filter(zaehlposition_id=nummer)
        return queryset

    def perform_destroy(self, instance):
        """Deleting touches the count position too, otherwise nobody notices.

        If no weighing remains afterwards, the position is uncounted again: a
        zero would be the claim that the shelf is empty. The row is the one
        read under its lock, and only its timestamps are written, as for a new
        weighing ('WiegungSerializer.save').
        """
        with transaction.atomic():
            # Under the inventory's lock: after a closing in between the row is
            # gone, and 'save' would otherwise insert it into the closed inventory.
            # Then the row's, in the closing's order: inventory first.
            zaehlbar_unter_sperre(instance.zaehlposition.inventur)
            position = gesperrte_zeile(instance.zaehlposition)
            instance.delete()
            position.gezaehlt_am = position.gezaehlt_am if position.wiegungen.exists() else None
            position.save(update_fields=["gezaehlt_am", "zuletzt_geaendert"])
