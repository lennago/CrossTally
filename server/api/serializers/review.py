"""The check list: what closing would freeze, with note and difference by hand."""

from decimal import Decimal

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from inventur.services import NOTIZ_HOECHSTLAENGE, NOTIZ_ZU_LANG, Ergebniszeile

from .numbers import Dezimalzahl


def zahl(wert: Decimal | None) -> str | None:
    """A number as the app reads it: with a point and without trailing zeros."""
    return None if wert is None else f"{wert.normalize():f}"


class PrueflisteZeileSerializer(serializers.Serializer):
    """One article of the check list, as 'services.ergebniszeilen' computes it.

    The quantities in the house's unit, rounded like the website shows them.
    A difference typed by hand comes as typed. Expects 'von_hand' in the
    context. The note comes along in full: the app shows it only on request.
    """

    hausartikel = serializers.IntegerField(source="hausartikel.pk")
    nummer = serializers.CharField(source="hausartikel.artikelnummer")
    #: The maintained name of the product, as when counting.
    name = serializers.CharField(source="hausartikel.ware.name")
    #: The house's designation, for the search.
    name_im_haus = serializers.CharField(source="hausartikel.name")
    #: The id of the house's unit. The app has the text for it.
    einheit = serializers.CharField(source="einheit.kennung")
    ist = serializers.SerializerMethodField()
    soll = serializers.SerializerMethodField()
    differenz = serializers.SerializerMethodField()
    gezaehlt = serializers.BooleanField()
    aktiv = serializers.BooleanField(source="hausartikel.aktiv")
    notiz = serializers.CharField()
    #: "Target counts": at closing the target stands as the result.
    soll_statt_ist = serializers.BooleanField()
    #: Whether the row deviates, on the difference rounded like the unit, as
    #: the website decides it. The app takes it instead of a rule of its own:
    #: a difference by hand of 0.4 pieces is none (until 10/2026 the app
    #: counted it, the website did not).
    auffaellig = serializers.BooleanField()

    def get_ist(self, zeile: Ergebniszeile) -> str | None:
        return zahl(zeile.einheit.runden(zeile.gesamtmenge_basiseinheit))

    def get_soll(self, zeile: Ergebniszeile) -> str | None:
        return None if zeile.sollbestand is None else zahl(zeile.einheit.runden(zeile.sollbestand))

    def get_differenz(self, zeile: Ergebniszeile) -> str | None:
        if zeile.differenz is None:
            return None
        if self.context.get("von_hand"):
            return zahl(zeile.differenz)
        return zahl(zeile.einheit.runden(zeile.differenz))


class VermerkSerializer(serializers.Serializer):
    """What the app changes on a row of the check list, one field at a time.

    The note, the difference by hand, or "target counts". A field that does
    not come along stays as stored ('vermerk_aendern'). The difference is in
    the house's unit, with a point, like every number of the API. Empty or
    null clears it.
    """

    #: At most as long as on the website ('NOTIZ_HOECHSTLAENGE'), without the spaces around it.
    notiz = serializers.CharField(
        required=False,
        allow_blank=True,
        trim_whitespace=True,
        max_length=NOTIZ_HOECHSTLAENGE,
        error_messages={"max_length": NOTIZ_ZU_LANG},
    )
    differenz = Dezimalzahl(max_digits=12, decimal_places=3, required=False, allow_null=True)
    soll_statt_ist = serializers.BooleanField(required=False)
    #: What the device showed when the person typed. Given, the server does
    #: not overwrite a different stored value but answers 409 with the row
    #: ('vermerk_aendern'). An app from before 10/2026 sends neither.
    notiz_vorher = serializers.CharField(
        required=False, allow_blank=True, allow_null=True, trim_whitespace=True
    )
    differenz_vorher = Dezimalzahl(max_digits=12, decimal_places=3, required=False, allow_null=True)

    def validate(self, attrs: dict) -> dict:
        if not attrs.keys() - {"notiz_vorher", "differenz_vorher"}:
            raise serializers.ValidationError(
                _("Erwartet wird „notiz“, „differenz“ oder „soll_statt_ist“.")
            )
        return attrs
