"""Count positions and their weighings: the only place where the PWA writes."""

from collections.abc import Mapping
from decimal import Decimal
from functools import cached_property
from typing import Any

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers
from rest_framework.exceptions import NotFound
from rest_framework.generics import get_object_or_404

from inventur.models import Inventur, Wiegung, Zaehlposition
from katalog.models import (
    Artikel,
    Behaelter,
    ErwartungGebinde,
    Gebinde,
    Hausartikel,
    arten_je_artikel,
    artikel_sperren,
)
from organisation.models import Ort

from ..checks import (
    NichtInDerZaehlung,
    behaelter_erlaubt,
    pruefe_nicht_aktiv,
    pruefe_zaehlbar,
    pruefe_zugeordnet,
    pruefen,
    zaehlbar_unter_sperre,
)
from ..master_data import einheit_von, gebinde_im_haus, ware_name
from .inventory import GebindeSerializer, OrtSerializer
from .numbers import Dezimalzahl, Modellserializer


def aktuelle_inventur(position: Zaehlposition) -> Inventur:
    """The inventory of a row as it stands in the database now, not as the request saw it.

    After a closing the status has changed, and a row may even be gone. The
    object in memory would still say "counting".
    """
    inventur = Inventur.objects.filter(pk=position.inventur_id).first()
    if inventur is None:
        raise NotFound(_("Diese Inventur gibt es nicht mehr."))
    return inventur


def positionen_geladen(queryset):
    """Everything a complete row needs, without a query per position."""
    return queryset.select_related(
        "ort__bereich",
        # The house's unit and the product's: the row converts from one into
        # the other.
        "artikel__hausartikel__basiseinheit",
        "artikel__hausartikel__ware__basiseinheit",
        "artikel__rezepturartikel__basiseinheit",
        "gebinde",
    ).prefetch_related("wiegungen__behaelter")


def gesperrte_zeile(position: Zaehlposition) -> Zaehlposition:
    """The row locked until the transaction ends, and read only after that, completely.

    What the view loaded before the lock may be stale. Merging or splitting
    a product hangs a row on another pack ('katalog.ware_merge',
    'katalog.ware_split'), another device removes it, a closing clears it
    away. Until 10/2026 a quantity or a weighing saved the row it had loaded
    before, whole: after merging it wrote back a pack that no longer existed
    (an error), after splitting the pack of the old product, which no longer
    fit the row's article. Locked by number alone, then read with what a
    complete row needs: PostgreSQL refuses to lock the nullable side of an
    outer join, and the pack is nullable. The weighings are not fetched
    along: they change in the same transaction, and a prefetch would go on
    showing them as they were. Called inside 'transaction.atomic()'.

    The state of the inventory is asked once more under the lock, and the
    row carries the inventory read there. The check in 'validate' ran
    before it. Locking the count locks every row first
    ('Inventur.sperre_setzen'): a write that gets its row after that reads
    the lock here and hears 409, as on a locked count, and one that held
    its row was waited for. Until 10/2026 a quantity checked a moment
    before the lock landed after it.
    """
    try:
        Zaehlposition.objects.select_for_update().values_list("pk", flat=True).get(pk=position.pk)
    except Zaehlposition.DoesNotExist:
        # The closing committed between the check and the lock and cleared
        # the rows away. It may also have held this row while it froze: it
        # locks every row before it reads ('zum_abschluss_sperren'). The
        # answer is the one the check would have given a moment later, not
        # a server error.
        raise NichtInDerZaehlung(aktuelle_inventur(position)) from None
    inventur = aktuelle_inventur(position)
    pruefe_zaehlbar(inventur)
    zeile = Zaehlposition.objects.filter(pk=position.pk)
    gelesen = positionen_geladen(zeile).prefetch_related(None).get()
    gelesen.inventur = inventur
    return gelesen


def ort_sperren(ort: Ort) -> Ort:
    """Locks the location of a new row until the transaction ends, or says that it is gone.

    Merging two locations deletes the one that goes, and it locks that one
    first ('katalog.ort_split_merge.zusammenfuehren'). A row that comes first
    is seen there: the merge stops at PROTECT and names the inventory on its
    form. One that comes later finds the location gone and says so in the
    app's words. Until 10/2026 such a row was written onto the location the
    merge was deleting, and its commit ended in a server error. By number
    and without 'Meta.ordering', which joins area and house and would lock
    them along. Called inside 'transaction.atomic()'.

    Back comes the location read after the lock, with its area: a new name
    of either locks it first as well ('organisation.models.zeilen_der_orte_sperren').
    Until 10/2026 the row went out with the location as the request had
    loaded it, under the old name.
    """
    gesperrt = (
        Ort.objects.select_for_update(no_key=True)
        .filter(pk=ort.pk)
        .order_by("pk")
        .values_list("pk", flat=True)
    )
    if not list(gesperrt):
        raise serializers.ValidationError({"ort": [ORT_WEG]})
    return Ort.objects.select_related("bereich").get(pk=ort.pk)


def behaelter_sperren(behaelter: Behaelter) -> None:
    """Locks the container of a weighing until the transaction ends, or says that it is gone.

    Deleting a container locks it first, on the website and in the
    administration ('django_project.deletion.zum_loeschen_sperren'). A
    weighing that comes first is seen there and holds the container
    (PROTECT). One that comes later finds it gone and says so in the app's
    words. Until 10/2026 such a weighing was written onto a container being
    deleted, and one of the two commits ended in a server error. After the
    row, the last in the order every path locks in. Without the key, so the
    foreign keys of other weighings go on checking against it meanwhile. By
    number and without 'Meta.ordering'. Called inside 'transaction.atomic()'.
    """
    gesperrt = (
        Behaelter.objects.select_for_update(no_key=True)
        .filter(pk=behaelter.pk)
        .order_by("pk")
        .values_list("pk", flat=True)
    )
    if not list(gesperrt):
        raise serializers.ValidationError({"behaelter": [BEHAELTER_WEG]})


def gesperrter_artikel(artikel: Artikel) -> Artikel:
    """The article of a new row locked until the transaction ends, and read only after that.

    What the request loaded before may be stale: merging a product hangs the
    article on the product that stays and deletes the packs that merge,
    splitting hangs it on the new product. Both lock the article first
    ('katalog.models.artikel_sperren'). A row that comes first is re-hung
    along with the others, one that comes later reads the article as it ends
    up. The concrete form with its product, fresh. Called inside
    'transaction.atomic()'.
    """
    if not artikel_sperren([artikel.pk]):
        raise serializers.ValidationError({"artikel": [ARTIKEL_WEG]})
    return (
        Artikel.objects.select_related("hausartikel__ware", "rezepturartikel")
        .get(pk=artikel.pk)
        .konkret()
    )


#: What a weighing hears for a row that does not exist, and for one of an
#: inventory the account does not count in ('checks.pruefe_zugeordnet').
UNBEKANNTE_ZEILE = _("Diese Zählposition ist hier nicht (mehr) bekannt.")

#: What a quantity hears for a row another device removed, before or while it was sent.
ZEILE_ENTFERNT = _("Die Zeile wurde inzwischen entfernt.")

#: The same for a new row: an inventory that does not exist and one the
#: account does not count in get the same answer.
NICHT_ZUGEORDNET = _("Für diese Inventur bist du nicht als zählend eingetragen.")

#: What a new row hears for a pack that no longer exists: merged into
#: another one when two products became one, deleted on the website, or set
#: inactive. The app then fetches its master data anew, and the pack to
#: choose is there.
GEBINDE_WEG = _(
    "Dieses Gebinde gibt es nicht mehr, denn die Ware wurde inzwischen geändert. "
    "Bitte wähle das Gebinde neu."
)

#: The same for an article that was deleted on the website.
ARTIKEL_WEG = _("Diesen Artikel gibt es nicht mehr.")

#: What a weighing hears for a container deleted on the website or in the
#: administration. The app then fetches its master data anew.
BEHAELTER_WEG = _(
    "Diesen Behälter gibt es nicht mehr, denn er wurde inzwischen gelöscht. "
    "Bitte wähle den Behälter neu."
)

#: The same for a location: merged into another one, or deleted on the website.
ORT_WEG = _(
    "Diesen Ort gibt es nicht mehr, denn er wurde inzwischen zusammengeführt oder gelöscht. "
    "Bitte wähle den Ort neu."
)

#: What a new row hears for a location or an article of another house: what
#: a number that does not exist hears ('ZaehlpositionAnlegenSerializer._im_haus').
#: Not its name, or a counter would read the locations and articles of every
#: house over the numbers. Not a sentence of its own either: until 10/2026 one
#: told which numbers exist in other houses.
ORT_FREMD = ORT_WEG
ARTIKEL_FREMD = ARTIKEL_WEG

#: What a number hears on a row the scale fills, entered there or brought
#: along when the row is created or revived.
GEWOGEN = _(
    "Diese Ware wird gewogen. Die Menge kommt über die Wiegungen, "
    "damit von jedem Behälter seine eigene Tara abgeht."
)


def pruefe_gewogen(position: Zaehlposition) -> None:
    """A weighing needs a row the scale fills. Asked before the row's lock and once more under it.

    Under the lock because the row may stop weighing in between: a merge
    hangs its article on a product that is not weighed, the product goes off
    the scale, a recipe article moves to pieces. A bowl on such a row adds
    nothing ('menge_in_basiseinheit'). Until 10/2026 it was asked only
    before the lock. As a list: DRF wraps the messages only from 'validate'.
    """
    if not position.wird_gewogen:
        raise serializers.ValidationError(
            {
                "zaehlposition": [
                    _("„%(name)s“ wird nicht gewogen.") % {"name": position.artikel.name}
                ]
            }
        )


def pruefe_behaelter(position: Zaehlposition, behaelter: Behaelter) -> None:
    """The container has to be one the app offers for the row, before the lock and under it.

    By the same rule the model knows ('behaelter_erlaubt'): a wrong tare
    goes unnoticed by everyone counting. Under the lock because the row's
    product may have changed in between, or lost the container. Until
    10/2026 the bowl landed in a container the row no longer offered.
    """
    if not behaelter_erlaubt(position, behaelter):
        raise serializers.ValidationError(
            {
                "behaelter": [
                    _(
                        "„%(behaelter)s“ steht für „%(artikel)s“ nicht zur Wahl. Er ist "
                        "weder dem Artikel noch einem Haus zugeordnet."
                    )
                    % {"behaelter": behaelter.anzeigename, "artikel": position.artikel.name}
                ]
            }
        )


def pruefe_ohne_anzahl(position: Zaehlposition) -> None:
    """A row the scale fills takes no number, before the lock and once more under it.

    A recipe article moved from pieces to grams in between makes its row one
    the scale fills. A number that landed there anyway counted as counted
    and added nothing (until 10/2026).
    """
    if position.wird_gewogen:
        raise serializers.ValidationError({"eingabemenge": [GEWOGEN]})


def zugeordnet_zuerst(
    serializer: serializers.Serializer, data, feld: str, unbekannt
) -> Inventur | None:
    """The field that leads to the inventory, checked alone and before every other field.

    Field by field, a number that does not exist failed along with the other
    fields, one of an inventory the account does not count in only after
    them. With a second wrong field the two answers differed, and the
    difference told a stranger which numbers exist (until 10/2026). Checked
    first and alone, a stranger hears what a number that does not exist
    hears, whatever else came along ('checks.pruefe_zugeordnet'). What is no
    mapping at all is left to DRF's own answer. Back comes the inventory, the
    caller may narrow the other fields to its house.
    """
    if not isinstance(data, Mapping):
        return None
    eingabe = serializer.fields[feld]
    try:
        wert = eingabe.run_validation(eingabe.get_value(data))
    except serializers.ValidationError as fehler:
        raise serializers.ValidationError({feld: fehler.detail}) from None
    inventur = wert if isinstance(wert, Inventur) else wert.inventur
    pruefe_zugeordnet(inventur, serializer.context["request"].user, {feld: [unbekannt]})
    return inventur


class WiegungSerializer(Modellserializer):
    """A vessel on the scale. The server subtracts the tare, per row.

    Several per count position are the normal case: the same product often lies
    in several bowls at one location, and each brings its own tare.
    """

    netto = serializers.SerializerMethodField()
    # Without the uniqueness validator a ModelSerializer otherwise attaches: an
    # id that already exists is no error but the same weighing once more, see
    # 'create'.
    kennung = serializers.UUIDField(required=False, allow_null=True)

    class Meta:
        model = Wiegung
        fields = (
            "id",
            "zaehlposition",
            "brutto_gramm",
            "behaelter",
            "netto",
            "erfasst_am",
            "kennung",
        )
        read_only_fields = ("id", "netto", "erfasst_am")
        extra_kwargs = {
            # In the app's words: closing clears the rows, and a weighing from
            # a dead spot then meets this instead of DRF's "Invalid pk".
            "zaehlposition": {"error_messages": {"does_not_exist": UNBEKANNTE_ZEILE}},
        }

    def get_netto(self, wiegung: Wiegung) -> str:
        """What remains after subtracting **this** tare, in the product's unit."""
        einheit = einheit_von(wiegung.zaehlposition.artikel)
        return str(einheit.runden(wiegung.netto()))

    def get_fields(self):
        """A correction stays on the weighing's own row, and a row sent along is not even read.

        Read, it told whether a number exists: one that does not got 400, any
        other was dropped ('update'). Over a weighing of its own a counter
        learned so which rows exist, in every inventory (until 10/2026).
        """
        felder = super().get_fields()
        if self.instance is not None:
            felder["zaehlposition"] = serializers.PrimaryKeyRelatedField(read_only=True)
        return felder

    def to_internal_value(self, data):
        # A new weighing: the row's inventory before any other field, as for
        # a new row. A correction stays on the weighing's own row ('validate').
        if self.instance is None:
            zugeordnet_zuerst(self, data, "zaehlposition", UNBEKANNTE_ZEILE)
        return super().to_internal_value(data)

    def validate(self, daten: dict) -> dict:
        """A weighing needs a row in the count, a counter of this inventory and a fitting vessel.

        The row has to be weighable, and the vessel one the app offers for it, by
        the same rule the model knows ('behaelter_erlaubt'): a wrong tare would go
        unnoticed by everyone counting.
        """
        # When correcting, the row is the weighing's own ('update' drops a sent
        # one). Checked against a sent row, lock and preparation of the own
        # inventory could be bypassed with a row of another one (until 10/2026).
        position = self.instance.zaehlposition if self.instance else daten.get("zaehlposition")
        if position is None:
            raise serializers.ValidationError(_("Zu welcher Zählposition gehört die Wiegung?"))
        # The assignment before the state: the state names the inventory.
        pruefe_zugeordnet(
            position.inventur, self.context["request"].user, {"zaehlposition": UNBEKANNTE_ZEILE}
        )
        pruefe_zaehlbar(position.inventur)
        pruefe_gewogen(position)
        behaelter = daten.get("behaelter") or getattr(self.instance, "behaelter", None)
        if behaelter is not None:
            pruefe_behaelter(position, behaelter)
        return daten

    def create(self, daten: dict) -> Wiegung:
        """A weighing that already exists comes back instead of arising once more.

        After a dead spot the app sends the same job again. Whether the response
        was lost or only the reload failed it does not know. It is recognised by
        the id the device assigns. Without it a second bowl at 900 g could not be
        told apart from the same one.

        A row marked as deleted comes back to life in the process, as when
        creating a row: what was weighed in it before no longer applies. Not
        for a bowl it already holds: that one comes once more after its row
        was removed, and it hears what a correction hears there ('save').

        It comes back only where creating it would work, for an article the
        app would offer now ('pruefe_nicht_aktiv'). The article is read with
        the row, under its lock ('gesperrte_zeile'). Ignoring, merging and
        splitting a product lock every row of the articles they change,
        removed ones too ('katalog.models.zaehlpositionen_sperren'). A
        revival that holds its row first is seen by them, and an ignore then
        refuses. One that comes later reads the article as they left it.
        Until 10/2026 a bowl brought back the row of an ignored article, or
        of a discontinued one whose slot a successor holds, and the closing
        froze it.
        """
        kennung = daten.get("kennung")
        position = daten["zaehlposition"]
        if kennung and (vorhanden := Wiegung.objects.filter(kennung=kennung).first()):
            # The same id on another row is no repetition but an error of the
            # device. The server must not return it: the app would then lay it
            # over its own row.
            if vorhanden.zaehlposition_id != position.pk:
                raise serializers.ValidationError(
                    {
                        "kennung": _(
                            "Diese Kennung gehört schon zu einer Wiegung an einer anderen "
                            "Zählposition."
                        )
                    }
                )
            # Another device removed the row after the first send. Until
            # 10/2026 the answer was 201, and the bowl stayed on the removed
            # row, where nobody saw it again.
            if not position.aktiv:
                raise NotFound(ZEILE_ENTFERNT)
            # The same bowl once more, with its latest values: a weighing
            # corrected in a dead spot goes out as the rewritten job. Until
            # 10/2026 the first weight stayed, and the app took that as the
            # answer without a word.
            geaendert = [
                feld
                for feld in ("brutto_gramm", "behaelter")
                if feld in daten and getattr(vorhanden, feld) != daten[feld]
            ]
            for feld in geaendert:
                setattr(vorhanden, feld, daten[feld])
            if geaendert:
                vorhanden.save(update_fields=geaendert)
            return vorhanden
        if not position.aktiv:
            try:
                pruefe_nicht_aktiv(position.artikel, position.inventur)
            except serializers.ValidationError as fehler:
                # As lists, as from 'validate': DRF wraps the messages only there.
                raise serializers.ValidationError(serializers.as_serializer_error(fehler)) from None
            position.wiegungen.all().delete()
            position.aktiv = True
            position.eingabemenge = None
        return super().create(daten)

    def update(self, instance: Wiegung, daten: dict) -> Wiegung:
        """On which row weighing happened is fixed. Only the weight is corrected.

        Otherwise a weighing could be moved to another count position afterwards,
        and the **old** one would stay untouched: its 'zuletzt_geaendert' would
        not move, the quantity would be gone there, and every other device would
        keep showing it on the next sync. Whoever weighed at the wrong location
        deletes the row and weighs again. Nothing is lost that anyone else needs.

        The weighing is the one read under the lock of its row ('save'), and
        only what is corrected is written.
        """
        daten.pop("zaehlposition", None)
        daten.pop("kennung", None)
        for feld, wert in daten.items():
            setattr(instance, feld, wert)
        instance.save(update_fields=list(daten))
        return instance

    def save(self, **kwargs):
        """Saves the weighing and touches the count position.

        The PWA syncs via 'Zaehlposition.zuletzt_geaendert'. Without the touch a
        new weighing would stay invisible to every other device.

        Under a lock of the row, as with the quantity: the closing deletes the
        rows, and a weighing whose check passed just before must not arrive
        afterwards. 'position.save()' on a deleted row would create it anew, in
        a closed inventory. The closing locks every row before it reads
        ('Inventur.zum_abschluss_sperren'): a weighing that holds its row first
        is frozen with it, one that comes later waits and hears 409.

        The row is the one read under the lock ('gesperrte_zeile'), and only
        what the weighing changes on it is written. Whether it comes back to
        life is decided by its state now: a row another device removed a moment
        ago takes its old weighings with it. A correction does not bring it
        back: it hears what a quantity hears, 404 with the reason. Until 10/2026
        it got 200, and the corrected bowl stayed on the removed row, where
        nobody saw it again. What the row weighs has to fit into a result
        ('Zaehlposition.wiegung_pruefen'). Checked after writing, so a
        corrected and a repeated weighing count with their new weight. A
        refusal takes the weighing back with the transaction. The container
        is locked after the row ('behaelter_sperren'). Whether the row still
        weighs and offers the container is asked once more after both locks
        ('pruefe_gewogen', 'pruefe_behaelter').

        A correction reads its weighing once more under that lock. The view
        loaded it before, and another device may have deleted it since, or a
        new bowl that revived the row took the old ones along. Gone, it hears
        what a correction of a weighing gone before hears, 404. Until 10/2026
        the weighing was saved as loaded, whole: one deleted a moment ago was
        inserted again under its number, next to the bowl that replaced it.
        """
        if self.instance is not None:
            # When correcting, the row is the weighing's own, as in 'validate'.
            position = self.instance.zaehlposition
        else:
            position = self.validated_data["zaehlposition"]
        with transaction.atomic():
            position = gesperrte_zeile(position)
            if self.instance is not None:
                if not position.aktiv:
                    raise NotFound(ZEILE_ENTFERNT)
                frisch = get_object_or_404(
                    position.wiegungen.select_related("behaelter"), pk=self.instance.pk
                )
                frisch.zaehlposition = position
                self.instance = frisch
            behaelter = self.validated_data.get("behaelter") or getattr(
                self.instance, "behaelter", None
            )
            if behaelter is not None:
                behaelter_sperren(behaelter)
            pruefe_gewogen(position)
            if behaelter is not None:
                pruefe_behaelter(position, behaelter)
            if self.instance is None:
                kwargs["zaehlposition"] = position
            war_aktiv = position.aktiv
            wiegung = super().save(**kwargs)
            try:
                position.wiegung_pruefen(wiegung)
            except DjangoValidationError as fehler:
                # As lists: DRF wraps the messages only for errors from 'validate'.
                raise serializers.ValidationError(fehler.message_dict) from fehler
            position.gezaehlt_am = timezone.now()
            felder = ["gezaehlt_am", "zuletzt_geaendert"]
            if position.aktiv != war_aktiv:
                # Brought back to life by 'create', without a number.
                felder += ["aktiv", "eingabemenge"]
            position.save(update_fields=felder)
        return wiegung


class ZaehlpositionSerializer(Modellserializer):
    """A row as the PWA displays it."""

    ort = OrtSerializer(read_only=True)
    artikel_id = serializers.IntegerField(read_only=True)
    artikel = serializers.SerializerMethodField()
    artikel_im_haus = serializers.SerializerMethodField()
    einheit = serializers.SerializerMethodField()
    gebinde = serializers.SerializerMethodField()
    wiegungen = WiegungSerializer(many=True, read_only=True)
    wird_gewogen = serializers.BooleanField(read_only=True)
    menge_basiseinheit = serializers.SerializerMethodField()
    erwartete_menge = serializers.SerializerMethodField()
    inventurart = serializers.SerializerMethodField()

    class Meta:
        model = Zaehlposition
        fields = (
            "id",
            "inventur_id",
            "ort",
            "artikel_id",
            "artikel",
            "artikel_im_haus",
            "einheit",
            "gebinde",
            "wird_gewogen",
            "wiegungen",
            "eingabemenge",
            "erwartete_menge",
            "inventurart",
            "sortierung",
            "menge_basiseinheit",
            "gezaehlt_am",
            "unerwartet",
            # If 'false' stands here, the row is deleted: the app clears it from
            # its storage. This is only delivered on sync.
            "aktiv",
            "zuletzt_geaendert",
        )
        read_only_fields = fields

    def get_artikel(self, position: Zaehlposition) -> str:
        return ware_name(position.artikel)[0]

    def get_artikel_im_haus(self, position: Zaehlposition) -> str:
        return ware_name(position.artikel)[1]

    def get_einheit(self, position: Zaehlposition) -> str:
        """The id. The app has the text for it, see 'ArtikelSerializer.einheit'."""
        return einheit_von(position.artikel).kennung

    def get_gebinde(self, position: Zaehlposition) -> dict | None:
        """The pack with its quantity in the house's unit, as in the master data.

        The row stands in the house's unit ('einheit'). Until 10/2026 the pack
        came with the product's factor: a carton of 8 kg in a house counting
        in g read as 8, and the app summed a waiting count with it.
        """
        if position.gebinde is None:
            return None
        artikel = position.artikel.konkret()
        if isinstance(artikel, Hausartikel):
            return self._gebinde.to_representation(gebinde_im_haus(artikel, position.gebinde))
        return self._gebinde.to_representation(position.gebinde)

    @cached_property
    def _gebinde(self) -> GebindeSerializer:
        """One pack serializer for every row of an answer, not one per row.

        A model serializer builds its fields anew for every instance. Once per row
        that took more than half of a full load: 120 ms became 266 ms for 810 rows.
        The list shares this serializer as its child, so the fields are built once.
        """
        return GebindeSerializer()

    def get_erwartete_menge(self, position: Zaehlposition) -> str | None:
        """The usual refill stock of this pack at this location.

        An **input aid**, not a target value: it says what normally stands here,
        so that it stands out when counting if something is far off. The
        assignment comes from the context ('erwartete_mengen'), so no query is
        incurred per row.
        """
        schluessel = (position.ort_id, position.artikel_id, position.gebinde_id)
        menge = self.context.get("erwartete_mengen", {}).get(schluessel)
        return None if menge is None else str(menge)

    def get_inventurart(self, position: Zaehlposition) -> int | None:
        """The tab the row stands in: the type it is counted in.

        It is on the row and not only in the master data, because the app
        fetches those once per inventory and the type can change in the
        meantime. Like the expected quantities from the context ('reiter').
        """
        return self.context.get("reiter", {}).get((position.inventur_id, position.artikel_id))

    def get_menge_basiseinheit(self, position: Zaehlposition) -> str | None:
        """What the input yields in the product's unit. This is computed by the server.

        The PWA should not rebuild that: pack factor, tare and the conversion via
        the reference unit are rules of the catalog, and two implementations would
        drift apart. Rounded to the decimal places of the unit, the same precision
        that also goes into the export.
        """
        menge = position.menge_in_basiseinheit()
        if menge is None:
            return None
        return str(einheit_von(position.artikel).runden(menge))


class Mengenkonflikt(Exception):
    """Someone else counted the row without the app knowing.

    No 'ValidationError': the response is 409 and brings the row as it stands
    now. DRF would turn every value in it into text, even an empty quantity
    into a "None". The view therefore builds the response itself.
    """


def gleiche_menge(links: Decimal | None, rechts: Decimal | None) -> bool:
    """A "3" and a "3.000" are the same quantity, empty equals only empty."""
    if links is None or rechts is None:
        return links is None and rechts is None
    return links == rechts


class ZaehlpositionEingabeSerializer(Modellserializer):
    """Counting: the number, nothing more.

    Location, article and pack stay as they are. They describe *what* is
    counted, and that is decided in the preparation. Whoever wants to count
    something else creates a new position. Weighed goods come in through the
    weighings, one per vessel.

    **'eingabemenge_vorher' is the quantity the device had in front of it when
    opening the form.** If something else is on the server by now, someone else
    counted, and whoever writes now knew nothing of it. Then it is not silently
    overwritten but asked back with 409. Whoever saw the foreign number and
    changed it anyway sends it along as 'vorher' and thereby overwrites
    deliberately. If the new value is already on the server, the own send
    arrived twice after a dead spot. That is no conflict. An app without the
    field (before 09/2026) writes as before without the check.
    """

    eingabemenge_vorher = Dezimalzahl(
        max_digits=12, decimal_places=3, allow_null=True, required=False, write_only=True
    )

    class Meta:
        model = Zaehlposition
        fields = ("eingabemenge", "eingabemenge_vorher")

    def validate(self, daten: dict) -> dict:
        position = self.instance
        assert position is not None, "nur zum Ändern gedacht, angelegt wird anderswo"
        pruefe_zaehlbar(position.inventur)
        pruefe_ohne_anzahl(position)
        return daten

    def to_representation(self, instance: Zaehlposition) -> dict:
        """Back comes the complete row, with a freshly computed quantity.

        The PWA stores the response as it comes and thus also has the new
        'zuletzt_geaendert' state for the next sync. The suggested quantity has to
        come along: the PWA replaces its row with this one, and without it the
        input aid would be gone after the first count.
        """
        return vollstaendig(instance, self.context)

    def update(self, instance: Zaehlposition, daten: dict) -> Zaehlposition:
        """The number onto the row as it stands under the lock. Back comes that row.

        Compared and written under a lock of the row: two devices writing at
        the same moment would otherwise both see the old value and silently
        overwrite each other after all. The row is read only once it is locked
        ('gesperrte_zeile'), and only the number and its two timestamps are
        written: a merge or a split may have hung it on another pack in the
        meantime, and the answer carries that pack. A row another device
        removed in between says so, as the view does for one removed before.
        Whether the row takes a number is asked once more on the row as it
        stands now ('pruefe_ohne_anzahl').
        """
        with transaction.atomic():
            position = gesperrte_zeile(instance)
            if not position.aktiv:
                raise NotFound(ZEILE_ENTFERNT)
            pruefe_ohne_anzahl(position)
            jetzt = position.eingabemenge
            neu = daten.get("eingabemenge", jetzt)
            if (
                "eingabemenge_vorher" in daten
                and not gleiche_menge(jetzt, daten["eingabemenge_vorher"])
                and not gleiche_menge(jetzt, neu)
            ):
                raise Mengenkonflikt
            position.eingabemenge = neu
            # Only here 'gezaehlt_am' gets a value: before, nobody counted, and a
            # timestamp without a quantity would claim the opposite.
            position.gezaehlt_am = None if position.eingabemenge is None else timezone.now()
            pruefen(position)
            position.save(update_fields=["eingabemenge", "gezaehlt_am", "zuletzt_geaendert"])
        return position


class ZaehlpositionAnlegenSerializer(Modellserializer):
    """What is added when counting: an article nobody expected here.

    Always 'unerwartet', since everything that was expected has been in the list
    since the inventory was created. The marker is therefore no field of the
    input but a finding.
    """

    class Meta:
        model = Zaehlposition
        fields = ("id", "inventur", "ort", "artikel", "gebinde", "eingabemenge")
        extra_kwargs = {
            # A number that does not exist sounds like an inventory the account
            # does not count in ('checks.pruefe_zugeordnet').
            "inventur": {"error_messages": {"does_not_exist": NICHT_ZUGEORDNET}},
            # In the app's words and not DRF's "Invalid pk": the location, the
            # article or the pack of a job that waited out a dead spot may be
            # gone by now.
            "ort": {"error_messages": {"does_not_exist": ORT_WEG}},
            "artikel": {"error_messages": {"does_not_exist": ARTIKEL_WEG}},
            "gebinde": {"error_messages": {"does_not_exist": GEBINDE_WEG}},
        }

    def to_internal_value(self, data):
        # The assignment before anything else: the state names the
        # inventory, the other fields would tell which numbers exist.
        if (inventur := zugeordnet_zuerst(self, data, "inventur", NICHT_ZUGEORDNET)) is not None:
            self._im_haus(inventur)
        return super().to_internal_value(data)

    def _im_haus(self, inventur: Inventur) -> None:
        """Location and article only of the inventory's house: another house's number is unknown.

        Such a number fails as one that does not exist, field by field
        beside the others ('ORT_WEG', 'ARTIKEL_WEG'). Until 10/2026 it heard
        a sentence of its own, after the fields and only where they all
        passed. Both told a counter which numbers exist in other houses. A
        recipe article belongs to no house.
        """
        for name, im_haus in (
            ("ort", Ort.objects.filter(bereich__haus_id=inventur.haus_id)),
            (
                "artikel",
                Artikel.objects.filter(
                    Q(hausartikel__haus_id=inventur.haus_id) | Q(rezepturartikel__isnull=False)
                ),
            ),
        ):
            if isinstance(feld := self.fields[name], serializers.RelatedField):
                feld.queryset = im_haus

    def validate(self, daten: dict) -> dict:
        """The state of the inventory, then what the article and the pack allow.

        Location and article of another house do not get here
        ('_im_haus'). Until 10/2026 only the model's rule refused them
        ('Zaehlposition.clean'), under the lock of the foreign rows and with
        their names, and a discontinued article of another house even gave
        its successor. Over the numbers a counter of one house read every
        house. What the own house says about its articles stays: it helps
        whoever counts here.

        A number for a row the scale fills is refused, as when counting
        ('GEWOGEN'), for a revived row too. Until 10/2026 it was stored: the
        row counted as counted and added nothing. The app never sends one.

        A new or a revived row never stands on an inactive pack, as none
        arises there when the inventory is created ('aktuelles_gebinde'). A
        row that already exists answers as it always did ('dublette'): the
        first send of a job created it, and the app goes on with it.
        """
        inventur = daten["inventur"]
        pruefe_zaehlbar(inventur)
        artikel = daten["artikel"].konkret()
        pruefe_nicht_aktiv(artikel, inventur)
        if daten.get("eingabemenge") is not None:
            pruefe_ohne_anzahl(Zaehlposition(artikel=artikel, gebinde=daten.get("gebinde")))

        vorhanden = vorhandene_zeile(daten)
        if vorhanden is not None and vorhanden.aktiv:
            raise dublette(vorhanden)
        if (gebinde := daten.get("gebinde")) is not None and not gebinde.aktiv:
            raise serializers.ValidationError({"gebinde": [GEBINDE_WEG]})
        if vorhanden is not None:
            # Deleted here means marked as deleted: the same row is still there and
            # is revived. Creating a second one would not work anyway, because the
            # partial index allows only one per location, article and pack.
            self.wiederbeleben = vorhanden
        return daten

    def create(self, daten: dict) -> Zaehlposition:
        """The row as the catalog stands under the lock of its location and article.

        Not as 'validate' saw it: a merge of the location may have committed
        in between ('ort_sperren'), or a merge or a split of the product
        ('gesperrter_artikel'). A pack that merged is gone, and the answer
        says so in the app's words ('GEBINDE_WEG'). One that moved to the
        product that stays still fits, and the row lands on it. After a split
        the pack belongs to the other product, and the model says that. The
        article's state is checked once more, as the app would offer it now:
        merging may have discontinued it in a house that carried both. That
        holds for a removed row that comes back too: until 10/2026 it was
        revived before this check, on the article the merge had just
        discontinued. Until then too the row was written as loaded, onto a
        pack that was being deleted, and its commit ended in a server error.
        A number is refused once more where the row as it stands now is one
        the scale fills, a revived one too ('pruefe_ohne_anzahl').
        """
        with transaction.atomic():
            # 'validate' checked the inventory before any lock. Checked once
            # more on the locked row: a closing that commits in between is
            # seen here, and the row does not land in a closed inventory.
            zaehlbar_unter_sperre(daten["inventur"])
            # What the row hangs on, before any row: merging locations locks
            # the location first, merging or splitting a product the article.
            ort = ort_sperren(daten["ort"])
            artikel = gesperrter_artikel(daten["artikel"])
            try:
                pruefe_nicht_aktiv(artikel, daten["inventur"])
            except serializers.ValidationError as fehler:
                # As lists, as from 'validate': DRF wraps the messages only there.
                raise serializers.ValidationError(serializers.as_serializer_error(fehler)) from None
            if (wieder := getattr(self, "wiederbeleben", None)) is not None:
                # The row exists: read under its own lock, it carries the pack
                # a merge or a split left it ('_wiederbeleben').
                return self._wiederbeleben(wieder, daten)
            daten = {
                **daten,
                "ort": ort,
                "artikel": artikel,
                "gebinde": aktuelles_gebinde(daten.get("gebinde")),
            }
            position = Zaehlposition(**daten, unerwartet=True)
            if position.eingabemenge is not None:
                pruefe_ohne_anzahl(position)
            position.gezaehlt_am = None if position.eingabemenge is None else timezone.now()
            try:
                pruefen(position)
                with transaction.atomic():
                    position.save()
            except serializers.ValidationError, IntegrityError:
                # Two devices create the same row at the same moment. 'validate' did
                # not find it yet, the other request was faster. Depending on exactly
                # when, 'full_clean' catches it or only the partial index. Both mean:
                # the row already exists. Without its number the app would discard the
                # quantity as rejected instead of going to the existing row.
                vorhanden = vorhandene_zeile(daten)
                if vorhanden is None or not vorhanden.aktiv:
                    raise
                raise dublette(vorhanden) from None
            return position

    @staticmethod
    def _wiederbeleben(geloescht: Zaehlposition, daten: dict) -> Zaehlposition:
        """A row marked as deleted comes back with the new value.

        What stood in it before no longer applies, since it was deleted. The old
        weighings therefore go with it. Otherwise a quantity would count that
        nobody expects any more. Whether the row was expected stays as it was: one
        from the counting list that someone deleted and recreated is no unexpected
        find, and the clarification before closing asks about that.

        Read once more under its lock, and only what reviving changes is
        written, as for a quantity ('gesperrte_zeile'). A row another device
        revived in the meantime is the row that already exists.
        """
        position = gesperrte_zeile(geloescht)
        if position.aktiv:
            raise dublette(position)
        if position.gebinde is not None and not position.gebinde.aktiv:
            # Set inactive since 'validate' looked ('aktuelles_gebinde').
            raise serializers.ValidationError({"gebinde": [GEBINDE_WEG]})
        if daten.get("eingabemenge") is not None:
            pruefe_ohne_anzahl(position)
        position.wiegungen.all().delete()
        position.aktiv = True
        position.eingabemenge = daten.get("eingabemenge")
        position.gezaehlt_am = None if position.eingabemenge is None else timezone.now()
        pruefen(position)
        position.save(update_fields=["aktiv", "eingabemenge", "gezaehlt_am", "zuletzt_geaendert"])
        return position

    def to_representation(self, instance: Zaehlposition) -> dict:
        """Back comes the complete row. The PWA stores it as it came."""
        return vollstaendig(instance, self.context)


def aktuelles_gebinde(gebinde: Gebinde | None) -> Gebinde | None:
    """The pack of a new row as it stands now, read after its article was locked.

    Merging deletes the packs that merge only after it locked the article
    ('gesperrter_artikel'). Gone here means: merged or deleted, and no row
    may point to it. 400 in the app's words, the app then fetches its master
    data anew. An inactive pack counts as gone: the product is no longer
    carried in it, no row arises on it when an inventory is created
    ('Inventur.zaehlpositionen_anlegen'), and the app does not offer it
    ('api/master_data.py'). Until 10/2026 a job from a dead spot created a
    row on a pack set inactive meanwhile.
    """
    if gebinde is None:
        return None
    if (aktuell := Gebinde.objects.filter(pk=gebinde.pk, aktiv=True).first()) is None:
        raise serializers.ValidationError({"gebinde": [GEBINDE_WEG]})
    return aktuell


def vorhandene_zeile(daten: Mapping[str, Any]) -> Zaehlposition | None:
    """The row that already exists for location, article and pack in the inventory."""
    return Zaehlposition.objects.filter(
        inventur=daten["inventur"],
        ort=daten["ort"],
        artikel=daten["artikel"],
        gebinde=daten.get("gebinde"),
    ).first()


def dublette(vorhanden: Zaehlposition) -> serializers.ValidationError:
    """The response to a row that already exists: 400 with its number.

    With the number the PWA can switch to 'change' instead of losing the
    input. When resubmitting from the offline storage that is exactly the
    normal case. The number arrives as '["12"]'. The list stands here itself:
    DRF wraps error values in lists only from 'validate', not from 'create'.
    There '"12"' would arrive otherwise, and the app would read the "1" from it.
    """
    return serializers.ValidationError(
        {"vorhanden_id": [vorhanden.pk], "detail": [_("Diese Zeile gibt es hier schon.")]}
    )


def vollstaendig(position: Zaehlposition, kontext: Mapping[str, Any]) -> dict:
    """A single row including suggested quantity and position in the counting list."""
    return ZaehlpositionSerializer(position, context={**kontext, **mitgeben([position])}).data


def mitgeben(positionen) -> dict:
    """What the serializer draws from the context instead of querying per row."""
    return {
        "erwartete_mengen": erwartete_mengen(positionen),
        "reiter": reiter_je_inventur(positionen),
    }


def reiter_je_inventur(positionen) -> dict[tuple[int, int], int | None]:
    """The tab of every row by inventory and article, each inventory with its own house and types.

    The list of rows answers '?geaendert_seit=' without '?inventur=' for
    every inventory the account counts in. Until 10/2026 all of them got
    the tabs of the first one: a recipe article of another house was read
    with the ingredients of the wrong house, and the same article shared one
    tab in two inventories.
    """
    je_inventur: dict[int, list[Zaehlposition]] = {}
    for position in positionen:
        je_inventur.setdefault(position.inventur_id, []).append(position)
    return {
        (inventur_id, artikel_id): reiter
        for inventur_id, zeilen in je_inventur.items()
        for artikel_id, reiter in reiter_der_artikel(zeilen).items()
    }


def reiter_der_artikel(positionen) -> dict[int, int | None]:
    """Per article of these rows, all of one inventory, the type whose tab it stands in.

    The first type of the inventory among its own: a house article has one,
    a recipe article those of its ingredients. Where the inventory counts
    none of them, its own (the first by order). The app then shows the row
    in its first tab and names the type. Three queries for all rows of an
    inventory.
    """
    positionen = list(positionen)
    if not positionen:
        return {}
    inventur = positionen[0].inventur
    umfang = inventur.zaehlumfang
    arten = arten_je_artikel(inventur.haus_id, {position.artikel_id for position in positionen})
    return {
        artikel_id: umfang.reiter(eigene) or (eigene[0] if eigene else None)
        for artikel_id, eigene in arten.items()
    }


def erwartete_mengen(positionen) -> dict[tuple, object]:
    """The suggested values for these rows, in one query.

    Keyed like the count position itself (location, article, pack), so the
    serializer only looks up.
    """
    orte = {position.ort_id for position in positionen}
    if not orte:
        return {}
    vorgaben = ErwartungGebinde.objects.filter(
        erwartung__ort_id__in=orte, standard_menge__isnull=False
    ).select_related("erwartung")
    return {
        (vorgabe.erwartung.ort_id, vorgabe.erwartung.artikel_id, vorgabe.gebinde_id): (
            vorgabe.standard_menge
        )
        for vorgabe in vorgaben
    }
