"""What the app reads about an inventory: the master data it keeps offline, and the inventory."""

from rest_framework import serializers

from inventur.models import Inventur, Inventurstatus
from katalog.models import Gebinde
from organisation.models import Ort

# --------------------------------------------------------------------------- #
# Master data: load once, keep offline
# --------------------------------------------------------------------------- #


class BehaelterSerializer(serializers.Serializer):
    """A container of the master data, computed in 'master_data.behaelter_des_hauses'."""

    id = serializers.IntegerField()
    name = serializers.CharField()
    tara = serializers.DecimalField(max_digits=10, decimal_places=3)
    #: "dieses", "anderes" or empty: how the container relates to the inventory's house.
    haus = serializers.CharField(allow_blank=True)


class GebindeSerializer(serializers.ModelSerializer):
    """The pack as the app stores it.

    The name rides along as it is stored. If 'ist_einzeln' is set, it is
    meaningless: then the app shows its own word for the loose single unit.
    For a house article the quantity comes in the house's unit, in the master
    data as at every row ('gebinde_im_haus').
    """

    #: Without the model's ten digits: converted into the house's unit, a pack
    #: the catalog accepts grows past them. 10,000 kg in a house that counts
    #: in g are ten million. Until 10/2026 such a pack broke the master data
    #: and every answer with its row, the inventory's too, and the app could
    #: no longer load the inventory.
    faktor_in_basiseinheit = serializers.DecimalField(
        max_digits=None, decimal_places=3, read_only=True
    )

    class Meta:
        model = Gebinde
        fields = ("id", "name", "ist_einzeln", "faktor_in_basiseinheit", "sortierung")


class OrtSerializer(serializers.ModelSerializer):
    bereich = serializers.CharField(source="bereich.name", read_only=True)

    class Meta:
        model = Ort
        fields = ("id", "name", "bereich")


class ArtikelSerializer(serializers.Serializer):
    """A countable article of the house: house article as well as recipe article.

    Both come from the same list because there is no difference when counting:
    there is a name, a unit and either packs or a scale.
    """

    id = serializers.IntegerField()
    #: The maintained name of the product, because that is on the shelf when counting.
    name = serializers.CharField()
    #: The house's designation, empty for the recipe article.
    name_im_haus = serializers.CharField(default="")
    artikelnummer = serializers.CharField(default="")
    #: The id of the base unit, not its text. Translation happens in the app,
    #: because the row lies offline and outlives a language switch.
    einheit = serializers.CharField()
    wird_gewogen = serializers.BooleanField()
    #: A composite article that exists only in this app. Its quantity is only
    #: distributed through the recipe at closing. A sum in its own unit says
    #: nothing when counting.
    ist_rezepturartikel = serializers.BooleanField(default=False)
    #: Inactive in the house but on the shelf: countable, and at the back in the search.
    aktiv = serializers.BooleanField(default=True)
    #: Delisted but found on the shelf. Applies only as long as number and product
    #: are free in the house. At the very back in the search, 'aktiv' is false then.
    eingestellt = serializers.BooleanField(default=False)
    #: The type the article is counted in ('master_data'). The app needs it for
    #: rows it creates itself: it sorts them into its tabs by it, and names it
    #: in the search where this inventory does not count it.
    inventurart = serializers.IntegerField(allow_null=True, default=None)
    gebinde = GebindeSerializer(many=True)
    behaelter = serializers.ListField(child=serializers.IntegerField())


# --------------------------------------------------------------------------- #
# Inventory, read-only
# --------------------------------------------------------------------------- #


class InventurSerializer(serializers.ModelSerializer):
    """What is pending. Nothing can be changed here, that is management's business."""

    haus = serializers.CharField(source="haus.name", read_only=True)
    haus_id = serializers.IntegerField(read_only=True)
    bezeichnung = serializers.CharField(source="__str__", read_only=True)
    #: The tabs of the app, in their order: what this inventory counts.
    inventurarten = serializers.SerializerMethodField()
    arten_anzeige = serializers.CharField(read_only=True)
    status_anzeige = serializers.CharField(source="get_status_display", read_only=True)
    zaehlbar = serializers.SerializerMethodField()
    anzahl_positionen = serializers.IntegerField(read_only=True)
    anzahl_gezaehlt = serializers.IntegerField(read_only=True)
    #: Whether the difference is typed in per article ('Inventur.differenz_von_hand').
    differenz_von_hand = serializers.BooleanField(read_only=True)
    hat_sollbestand = serializers.SerializerMethodField()
    #: Counting stopped, only the check list is open ('Inventur.sperre_setzen').
    gesperrt = serializers.BooleanField(read_only=True)

    class Meta:
        model = Inventur
        fields = (
            "id",
            "bezeichnung",
            "haus",
            "haus_id",
            "inventurarten",
            "arten_anzeige",
            "stichtag",
            "status",
            "status_anzeige",
            "zaehlbar",
            "anzahl_positionen",
            "anzahl_gezaehlt",
            "differenz_von_hand",
            "hat_sollbestand",
            "gesperrt",
        )
        read_only_fields = fields

    def get_zaehlbar(self, inventur: Inventur) -> bool:
        """Whether counting is allowed **now**. Not yet in preparation, not while locked."""
        return inventur.status == Inventurstatus.ZAEHLUNG and not inventur.gesperrt

    def get_inventurarten(self, inventur: Inventur) -> list[dict]:
        return [{"id": art.pk, "name": art.name} for art in inventur.inventurarten.all()]

    def get_hat_sollbestand(self, inventur: Inventur) -> bool:
        """Whether a target came in. Only without one the difference can be typed in by hand.

        From the annotation where the view brought it along ('mit_sollkennzeichen'),
        otherwise asked: two queries, which a list must not pay per row.
        """
        annotiert = getattr(inventur, "hat_soll", None)
        return inventur.hat_sollbestand if annotiert is None else bool(annotiert)
