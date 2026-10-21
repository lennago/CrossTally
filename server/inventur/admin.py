"""Admin for the inventory workflow.

For looking things up and touching them up, not for daily work. That goes
through the web interface (managers) and the PWA (counters).

A closed result is not touched any more: it is the frozen state the PDF is
fed from. Only the note stays open.
"""

import re
from collections.abc import Callable, Iterable
from decimal import Decimal
from typing import TYPE_CHECKING

from django import forms
from django.contrib import admin
from django.contrib.admin.options import csrf_protect_m
from django.core.exceptions import ValidationError
from django.db import models, router, transaction
from django.db.models import Count
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from django_project.admin_changes import NurGeaendertesSchreiben
from django_project.admin_site import AuswahlMitBezuegen
from django_project.deletion import VerwaltungSperrtZuerst
from django_project.labels import Beschriftung
from django_project.mixin_bases import ModelAdminBasis
from django_project.validation import ganzzahl
from katalog.models import artikel_sperren, menge_kurz, menge_mit_vorzeichen
from organisation.models import Ort

from .models import (
    IST_DABEI,
    IST_GEZAEHLT,
    Inventur,
    Inventurergebnis,
    InventurInventurart,
    Inventurnotiz,
    Inventurstatus,
    Sollbestand,
    Sollzeile,
    Zaehlposition,
    zeilen_der_inventuren_sperren,
)


def _gehoert_zur_inventur(request) -> bool:
    """Whether the inventory admin is asking right now and not the result admin.

    When deleting an inventory the admin collects everything that goes along
    via CASCADE and asks for the delete permission for **every** model. A hard
    'False' on the result therefore also blocks deleting the inventory, with
    "no permission to delete the following object types". The path says who is
    asking: the delete page and the bulk action lie under the inventory admin.
    """
    return request.path.startswith(reverse("admin:inventur_inventur_changelist"))


class NurZurAnsicht(ModelAdminBasis):
    """Shown here, written on the website, where the rules of these rows hold.

    The target stock comes in through the upload and the page for the target
    by hand, only in preparation and not while the difference is typed by
    hand ('soll_darf_herein', 'soll_von_hand_speichern'). Its open lines are
    clarified on the inventory's page. Remarks are written on the check list
    of the website and the app ('vermerk_aendern'): under the inventory's
    lock, for articles of the house that the list can show, a difference only
    where it is typed by hand. The types change in preparation, together with
    their rows ('Inventur.arten_aendern'). Until 10/2026 the administration
    wrote all of it past them, in any status, a target in the middle of a
    count included.

    Deleting goes with the inventory: its delete page asks for the right to
    delete everything it takes along ('_gehoert_zur_inventur').
    """

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return _gehoert_zur_inventur(request)


def _kurz(
    modell: type[models.Model], feld: str, darstellung: Callable[[Decimal], str] = menge_kurz
):
    """A quantity on a page that only shows it, without the places its column stores.

    Django shows a read-only decimal with every one of them ("30,000"), and
    whoever knows the point as the separator reads thirty thousand. Its form
    field shows it short ('Dezimalfeld'), as the website does ('menge_kurz').
    """
    spalte = modell._meta.get_field(feld)
    if not isinstance(spalte, models.DecimalField):
        raise TypeError(f"Feld '{feld}' ist {type(spalte).__name__}, erwartet war DecimalField.")

    @admin.display(description=spalte.verbose_name, ordering=feld)
    def anzeige(verwaltung: admin.options.BaseModelAdmin, obj: models.Model) -> str:
        wert = getattr(obj, feld)
        return verwaltung.get_empty_value_display() if wert is None else darstellung(wert)

    return anzeige


class SollbestandInline(NurZurAnsicht, admin.TabularInline):
    """The target stocks of an inventory, for looking them up ('NurZurAnsicht').

    Each row names its house article, and that names its house: both fetched
    along ('get_queryset'), not once per row.
    """

    model = Sollbestand
    extra = 0
    fields = ("hausartikel", "menge_basiseinheit_kurz")
    readonly_fields = fields
    menge_basiseinheit_kurz = _kurz(Sollbestand, "menge_basiseinheit")

    def has_delete_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("hausartikel__haus")


class InventurImNamen(admin.ModelAdmin):
    """For the lists that name their inventory in every row.

    The inventory is called after its types. 'list_select_related' fetches
    the house, the types need a prefetch of their own, otherwise every row asks.
    """

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("inventur__inventurarten")


class InventurartInline(NurZurAnsicht, admin.TabularInline):
    """The types an inventory counts, its tabs in the app, for looking them up.

    An inline instead of a field: the relation has a model of its own, so
    that a type cannot be deleted while an inventory counts it. Changed here,
    a type came without its count positions and went without removing them
    (until 10/2026). The page of the inventory in preparation does both
    ('Inventur.arten_aendern').
    """

    model = InventurInventurart
    extra = 0

    def has_delete_permission(self, request, obj=None):
        return False


if TYPE_CHECKING:

    class _GezaehlteInventur(Inventur):
        """Inventory as 'get_queryset' below delivers it, with the counted columns.
        Only for the type checker. At runtime the class would give a second model.
        """

        positionen: int
        gezaehlt: int


class InventurAdminForm(forms.ModelForm):
    """The inventory's form in the administration, checked against the house it only shows."""

    def validate_constraints(self):
        """One inventory a day per house, although the house is no field here.

        Django checks a constraint only where each of its columns is a field
        of the form, and the house is shown, not sent ('readonly_fields').
        Moving the date onto a day with another inventory of the house ended
        at the database's unique index, a server error (until 10/2026). So the
        constraints over house and date are checked here once more, with the
        house as the inventory holds it.
        """
        super().validate_constraints()
        andere = {feld.name for feld in self.instance._meta.fields} - {"haus", "stichtag"}
        try:
            self.instance.validate_constraints(exclude=andere)
        except ValidationError as fehler:
            self.add_error(None, fehler)


@admin.register(Inventur)
class InventurAdmin(VerwaltungSperrtZuerst, NurGeaendertesSchreiben, admin.ModelAdmin):
    """An inventory to look up, and its date and counters to touch up while it runs.

    It arises on the website with its rows, types and counters
    ('inventuren_anlegen') and moves through its states by their own steps:
    starting the count, the lock, closing, back into preparation. Until
    10/2026 the administration offered all of it past them: a new inventory
    without rows, the status set to "closed" without a closing and without
    results, and the house switched under its rows, which ended in a server
    error at its target stock. Now house and status are only shown, and a
    closed inventory as a whole: its date belongs to the record its results
    form.

    **Written under the lock and only where the page changed something**
    ('NurGeaendertesSchreiben'), as the count positions below. Until 10/2026
    the inventory was saved whole, as the page had shown it: counters the
    website assigned while the page was open went out again. A closing
    between opening the page and saving it is said on the page, and nothing
    is written ('nicht_mehr_zu_aendern').
    """

    form = InventurAdminForm
    list_display = (
        "stichtag",
        "haus",
        "arten_anzeige",
        "status",
        "anzahl_positionen",
        "anzahl_gezaehlt",
    )
    list_filter = ("status", "inventurarten", "haus")
    list_select_related = ("haus",)
    date_hierarchy = "stichtag"
    # The two switches are set on the inventory's page and in the counting
    # app, where their rules hold ('differenz_von_hand_setzen',
    # 'sperre_setzen'). Here they would be written past them: a tick here
    # with a target read in made closing ignore every target (until 10/2026).
    # House and status likewise, see above.
    readonly_fields = (
        "haus",
        "status",
        "erstellt_am",
        "abgeschlossen_am",
        "differenz_von_hand",
        "gesperrt",
    )
    # Who may count stands explicitly here, as on the website. Administrators
    # count without an entry and are not offered ('formfield_for_manytomany').
    filter_horizontal = ("zaehlende",)
    inlines = (InventurartInline, SollbestandInline)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        if obj is not None and not obj.laeuft:
            return False
        return super().has_change_permission(request, obj)

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        """The counters the website offers: active accounts, no administrators.

        Until 10/2026 the administration offered every account, and an
        administrator or a deactivated account could be entered here past the
        website's choice ('Inventur.eintragbare_zaehlende').
        """
        if db_field.name == "zaehlende":
            kwargs["queryset"] = Inventur.eintragbare_zaehlende()
        return super().formfield_for_manytomany(db_field, request, **kwargs)

    def vor_dem_schreiben_sperren(self, daten, nummer: int | None) -> None:
        """The inventory without the key, as the closing, the app and the website lock it."""
        _inventuren_sperren([nummer])

    def nicht_mehr_zu_aendern(self, nummer: int) -> Beschriftung | None:
        """Closed meanwhile: its date and its people belong to the record its results form."""
        if Inventur.objects.filter(pk=nummer, status=Inventurstatus.ABGESCHLOSSEN).exists():
            return _(
                "Die Inventur ist abgeschlossen und steht nur noch zur Ansicht. "
                "Gespeichert wurde nichts."
            )
        return None

    def vor_dem_loeschen_sperren(self, nummern: list[int]) -> None:
        """The inventory without the key, as the website's delete page locks it, then its rows.

        With 'FOR UPDATE' a merge of products that touched rows of the
        inventory twice waited for it while the delete waited for the rows,
        and PostgreSQL broke one of the two off ('InventurDeleteView'). The
        delete takes the full lock itself, at its end. A row the app creates
        meanwhile locks the inventory first and waits, or is among the rows
        Django collects. A quantity or a weighing locks only its row, so the
        rows come next ('zeilen_der_inventuren_sperren').
        """
        _inventuren_sperren(nummern)
        zeilen_der_inventuren_sperren(nummern)

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("haus")
            .prefetch_related("inventurarten")
            .annotate(
                positionen=Count("zaehlpositionen", filter=IST_DABEI, distinct=True),
                gezaehlt=Count("zaehlpositionen", filter=IST_GEZAEHLT, distinct=True),
            )
        )

    @admin.display(description=_("Inventurarten"))
    def arten_anzeige(self, obj: Inventur) -> str:
        return obj.arten_anzeige

    @admin.display(description=_("Positionen"), ordering="positionen")
    def anzahl_positionen(self, obj: _GezaehlteInventur) -> int:
        return obj.positionen

    @admin.display(description=_("davon gezählt"), ordering="gezaehlt")
    def anzahl_gezaehlt(self, obj: _GezaehlteInventur) -> int:
        return obj.gezaehlt


def _inventuren_sperren(nummern: Iterable[int | None]) -> None:
    """These inventories without the key, by number, until the transaction ends.

    Without the key, as the closing locks them and the app does for a row it
    creates, revives or removes ('Inventur.zum_abschluss_sperren',
    'api.checks.zaehlbar_unter_sperre'). A closing then waits until a change
    is written and freezes it, or the change waits and finds its row gone. A
    merge that touched rows twice still checks their keys against the
    inventory at its commit.
    """
    list(
        Inventur.objects.select_for_update(no_key=True)
        .filter(pk__in=sorted({nummer for nummer in nummern if nummer is not None}))
        .order_by("pk")
        .values_list("pk", flat=True)
    )


def _zeilen_sperren(nummern: Iterable[int]) -> None:
    """These count positions until the transaction ends, by number.

    'FOR UPDATE', as the app, the closing and a merge lock a row
    ('api.serializers.counting.gesperrte_zeile'). By number and not by
    'Meta.ordering': that joins the nullable pack, and PostgreSQL refuses to
    lock the nullable side of an outer join.
    """
    list(
        Zaehlposition.objects.select_for_update()
        .filter(pk__in=sorted(set(nummern)))
        .order_by("pk")
        .values_list("pk", flat=True)
    )


@admin.register(Zaehlposition)
class ZaehlpositionAdmin(
    VerwaltungSperrtZuerst, NurGeaendertesSchreiben, AuswahlMitBezuegen, InventurImNamen
):
    """The working data of a running inventory.

    Vanish at closing. What is still of interest then is in the inventory
    result.

    To remove a row, untick 'aktiv' instead of deleting it: a row marked as
    deleted no longer counts anywhere, but it is still there, and only by that
    does the counting app recognise on the next sync that it is gone. A
    deleted row never reaches it.

    **Written under the lock and only where the page changed something.** The
    counting app writes the same rows, and merging or splitting a product
    hangs them on other packs. Until 10/2026 the administration read a row
    without a lock and saved it whole, as its page had shown it: a quantity
    the app sent in the meantime went back, a row the app had removed came
    back from the list, and after a closing in between the row landed in the
    closed inventory once more. Now a sent form locks in the order of every
    path before Django reads ('vor_dem_schreiben_sperren'), and only the
    columns the person changed are written ('NurGeaendertesSchreiben').
    """

    list_display = (
        "inventur",
        "ort",
        "artikel",
        "gebinde",
        "eingabemenge",
        "unerwartet",
        "aktiv",
    )
    list_editable = ("aktiv",)
    list_filter = ("aktiv", "unerwartet", "inventur__status", "inventur")
    # Up to the house: 'Ort.__str__' names it, and the list would otherwise query per row.
    list_select_related = (
        "inventur__haus",
        "ort__bereich__haus",
        "artikel",
        "gebinde__ware",
    )
    search_fields = ("artikel__name", "ort__name")
    # Pack and location as search fields too: as a selection list the form
    # wrote out each of the 1100 packs with its product. Measured 1226 queries per call.
    autocomplete_fields = ("artikel", "gebinde", "ort")
    # The timestamp moves with every change: the app fetches the row by it,
    # and the review sees a new state ('zaehlstand').
    immer_mitschreiben = ("zuletzt_geaendert",)

    def vor_dem_schreiben_sperren(self, daten, nummer: int | None) -> None:
        """Inventory, location, article, then the row: the order every path locks in.

        A new row hangs on the inventory, location and article the form names,
        and they are locked without the key, as the app locks them for one
        ('api.serializers.counting.ort_sperren', 'gesperrter_artikel'). An
        existing row locks its inventory, a location or an article the form
        gives it anew, and then itself. Its inventory is read before, without a
        lock: it is what is to be locked. Everything else Django reads after.
        """
        gesendet = {feld: ganzzahl(daten.get(feld)) for feld in ("inventur", "ort", "artikel")}
        bisher = {}
        if nummer is not None:
            bisher = (
                Zaehlposition.objects.filter(pk=nummer)
                .order_by("pk")
                .values("inventur_id", "ort_id", "artikel_id")
                .first()
            ) or {}
        _inventuren_sperren([bisher.get("inventur_id"), gesendet["inventur"]])
        if gesendet["ort"] is not None and gesendet["ort"] != bisher.get("ort_id"):
            list(
                Ort.objects.select_for_update(no_key=True)
                .filter(pk=gesendet["ort"])
                .order_by("pk")
                .values_list("pk", flat=True)
            )
        if gesendet["artikel"] is not None and gesendet["artikel"] != bisher.get("artikel_id"):
            artikel_sperren([gesendet["artikel"]])
        if nummer is not None:
            _zeilen_sperren([nummer])

    @csrf_protect_m
    def changelist_view(self, request, extra_context=None):
        """Saving the list locks its rows before Django reads them, all in one transaction.

        Django reads the rows of a sent list without a lock and saves them in
        a transaction of its own afterwards. Here the inventories of the rows
        are locked without the key, then the rows, and then Django reads.
        """
        if request.method == "POST" and "_save" in request.POST and self.list_editable:
            with transaction.atomic(using=router.db_for_write(self.model)):
                nummern = self._zeilen_der_liste(request)
                _inventuren_sperren(
                    Zaehlposition.objects.filter(pk__in=nummern).values_list(
                        "inventur_id", flat=True
                    )
                )
                _zeilen_sperren(nummern)
                return super().changelist_view(request, extra_context)
        return super().changelist_view(request, extra_context)

    def _zeilen_der_liste(self, request) -> list[int]:
        """The rows a sent list names, found the way Django finds them ('form-<n>-id')."""
        vorne = self.get_changelist_formset(request).get_default_prefix()
        muster = re.compile(rf"{re.escape(vorne)}-\d+-{self.opts.pk.name}$")
        nummern = (
            ganzzahl(wert) for schluessel, wert in request.POST.items() if muster.match(schluessel)
        )
        return [nummer for nummer in nummern if nummer is not None]

    def vor_dem_loeschen_sperren(self, nummern: list[int]) -> None:
        """The inventories of these rows without the key, then the rows, before Django looks.

        Deleting a row takes its weighings along, and Django collects them
        first. A weighing of the app locks the row before it writes
        ('api.serializers.counting.gesperrte_zeile'), so it is either among the
        ones collected or finds its row gone. Until 10/2026 one could arrive
        between the look and the delete, and the delete ended at its foreign
        key.
        """
        _inventuren_sperren(
            Zaehlposition.objects.filter(pk__in=nummern).values_list("inventur_id", flat=True)
        )
        _zeilen_sperren(nummern)


@admin.register(Sollbestand)
class SollbestandAdmin(NurZurAnsicht, InventurImNamen):
    """The target stocks of running inventories, to look up ('NurZurAnsicht')."""

    list_display = ("inventur", "hausartikel", "menge_basiseinheit_kurz", "herkunft")
    list_filter = ("inventur", "herkunft")
    list_select_related = ("inventur__haus", "hausartikel__haus")
    search_fields = ("hausartikel__name", "hausartikel__artikelnummer")
    fields = ("inventur", "hausartikel", "menge_basiseinheit_kurz", "herkunft", "name_in_datei")
    readonly_fields = ("menge_basiseinheit_kurz",)
    menge_basiseinheit_kurz = _kurz(Sollbestand, "menge_basiseinheit")


@admin.register(Sollzeile)
class SollzeileAdmin(NurZurAnsicht, InventurImNamen):
    """What the target stock file left open. Clarified on the inventory's page ('NurZurAnsicht')."""

    list_display = (
        "inventur",
        "artikelnummer",
        "name",
        "menge_basiseinheit_kurz",
        "grund",
        "hausartikel",
    )
    list_filter = ("grund", "inventur")
    list_select_related = ("inventur__haus", "hausartikel__haus")
    search_fields = ("artikelnummer", "name")
    fields = list_display
    readonly_fields = ("menge_basiseinheit_kurz",)
    menge_basiseinheit_kurz = _kurz(Sollzeile, "menge_basiseinheit")


@admin.register(Inventurnotiz)
class InventurnotizAdmin(NurZurAnsicht, InventurImNamen):
    """Notes of a running inventory, to look up ('NurZurAnsicht').

    Working data like target stock and count position: they migrate into the
    result row at closing and vanish here.
    """

    # With the difference by hand: a row that keeps only that one looks like
    # an empty note otherwise.
    list_display = ("inventur", "hausartikel", "text", "differenz_kurz", "geaendert_am")
    list_filter = ("inventur",)
    list_select_related = ("inventur__haus", "hausartikel__haus")
    search_fields = ("hausartikel__name", "hausartikel__artikelnummer", "text")
    fields = ("inventur", "hausartikel", "text", "soll_statt_ist", "differenz_kurz")
    readonly_fields = ("differenz_kurz",)
    differenz_kurz = _kurz(Inventurnotiz, "differenz", menge_mit_vorzeichen)


@admin.register(Inventurergebnis)
class InventurergebnisAdmin(AuswahlMitBezuegen, InventurImNamen):
    """Frozen: everything read-only except the note."""

    list_display = (
        "inventur",
        "hausartikel",
        "gesamtmenge_basiseinheit",
        "sollbestand",
        "differenz",
        "einheit",
    )
    list_filter = ("inventur",)
    list_select_related = ("inventur__haus", "hausartikel__haus", "einheit")
    search_fields = ("hausartikel__name", "hausartikel__artikelnummer")
    readonly_fields = (
        "inventur",
        "hausartikel",
        "gesamtmenge_basiseinheit",
        "sollbestand",
        "differenz",
        "einheit",
        "faktor_zur_ware",
        "eingefroren_am",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        """Only with the inventory, not individually, otherwise gaps would open in the state."""
        return _gehoert_zur_inventur(request)

    def get_queryset(self, request):
        return super().get_queryset(request).filter(inventur__status=Inventurstatus.ABGESCHLOSSEN)
