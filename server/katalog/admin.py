from collections.abc import Iterable
from typing import TYPE_CHECKING

from django.contrib import admin, messages
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from django_project.admin_site import DEZIMALFELDER, AuswahlMitBezuegen, NurUeberDieSeite
from django_project.deletion import VerwaltungSperrtZuerst
from django_project.mixin_bases import ModelAdminBasis

from .forms import BaseRezepturkomponenteFormSet, RezepturartikelAdminForm
from .models import (
    GEFUEHRT,
    SICHTBAR,
    Artikel,
    Artikelstatus,
    Basiseinheit,
    Behaelter,
    Erwartung,
    ErwartungGebinde,
    Gebinde,
    Hausartikel,
    Inventurart,
    Rezepturartikel,
    Rezepturkomponente,
    Ware,
    Warengruppe,
    artikel_sperren,
    menge_kurz,
)


def gebinde_sperren(waren: Iterable[int]) -> None:
    """To delete packs of these products: their articles, as the product page does.

    A new row of the app locks its article, not its pack
    ('api.serializers.counting.gesperrter_artikel'). The product page locks the
    product's articles before it checks the packs it deletes
    ('katalog.models.artikel_sperren'), the administration as well.
    """
    artikel_sperren(
        Hausartikel.objects.filter(ware_id__in=list(waren)).values_list("pk", flat=True)
    )


@admin.register(Artikel)
class ArtikelAdmin(NurUeberDieSeite, VerwaltungSperrtZuerst, admin.ModelAdmin):
    """The MTI core. Registered for 'autocomplete_fields', not for maintenance."""

    search_fields = ("name",)

    def has_add_permission(self, request):
        return False


if TYPE_CHECKING:

    class _GezaehlteWarengruppe(Warengruppe):
        """Product group as 'get_queryset' below delivers it, with the counted columns.
        Only for the type checker. At runtime the class would give a second model.
        """

        _anzahl: int
        _anzahl_aktiv: int


@admin.register(Warengruppe)
class WarengruppeAdmin(NurUeberDieSeite, admin.ModelAdmin):
    """'aktiv' is derived: the group lives as long as an article in it is active."""

    list_display = ("name", "anzahl_artikel", "anzahl_aktiv", "ist_aktiv")
    search_fields = ("name",)
    actions = ["deaktivieren", "einstellen"]

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .annotate(
                _anzahl=models.Count(
                    "hausartikel", filter=models.Q(hausartikel__status__in=SICHTBAR)
                ),
                _anzahl_aktiv=models.Count("hausartikel", filter=models.Q(hausartikel__aktiv=True)),
            )
        )

    @admin.display(description=_("Hausartikel"), ordering="_anzahl")
    def anzahl_artikel(self, obj: _GezaehlteWarengruppe) -> int:
        return obj._anzahl

    @admin.display(description=_("davon aktiv"), ordering="_anzahl_aktiv")
    def anzahl_aktiv(self, obj: _GezaehlteWarengruppe) -> int:
        return obj._anzahl_aktiv

    @admin.display(description=_("aktiv"), boolean=True, ordering="_anzahl_aktiv")
    def ist_aktiv(self, obj: _GezaehlteWarengruppe) -> bool:
        return bool(obj._anzahl_aktiv)

    @admin.action(description=_("Warengruppe inaktiv setzen (alle aktiven Hausartikel)"))
    def deaktivieren(self, request, queryset):
        jetzt = timezone.now()
        betroffen = sum(gruppe.deaktivieren(jetzt) for gruppe in queryset)
        self.message_user(
            request,
            _("%(betroffen)s Hausartikel in %(gruppen)s Warengruppe(n) auf inaktiv gesetzt.")
            % {"betroffen": betroffen, "gruppen": len(queryset)},
        )

    @admin.action(description=_("Warengruppe einstellen (alle geführten Hausartikel)"))
    def einstellen(self, request, queryset):
        betroffen = sum(gruppe.einstellen() for gruppe in queryset)
        self.message_user(
            request,
            _("%(betroffen)s Hausartikel in %(gruppen)s Warengruppe(n) eingestellt.")
            % {"betroffen": betroffen, "gruppen": len(queryset)},
        )


@admin.register(Basiseinheit)
class BasiseinheitAdmin(admin.ModelAdmin):
    """Reference values from the data migration, not master data."""

    list_display = ("kennung", "name", "dimension", "nachkommastellen", "faktor_zur_referenz")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class NurZuweisbareBehaelter(ModelAdminBasis):
    """Hides "without container" from the assignment.

    It is available at every weighing anyway. As a tickable entry it would be
    a decision that does not exist. Applies in the admin as on the site.
    """

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        if db_field.name == "behaelter":
            kwargs["queryset"] = Behaelter.zuweisbar()
        return super().formfield_for_manytomany(db_field, request, **kwargs)


class GebindeInline(admin.TabularInline):
    model = Gebinde
    fk_name = "ware"
    extra = 0
    formfield_overrides = DEZIMALFELDER
    fields = ("name", "ist_einzeln", "faktor_in_basiseinheit", "aktiv")


@admin.register(Ware)
class WareAdmin(NurUeberDieSeite, NurZuweisbareBehaelter, VerwaltungSperrtZuerst, admin.ModelAdmin):
    """The cross-house identity of a product.

    Here hang base unit and the packs: everything that applies the same for
    the same product in every house. The inventory type hangs on the house
    article.
    """

    filter_horizontal = ("behaelter",)
    list_display = (
        "name",
        "basiseinheit",
        "abwiegbar",
        "ist_aktiv",
        "haeuser",
        "gebindeliste",
    )
    list_filter = ("basiseinheit",)
    list_select_related = ("basiseinheit",)
    search_fields = ("name", "hausartikel__artikelnummer", "hausartikel__name")
    inlines = [GebindeInline]
    actions = ["deaktivieren", "einstellen"]

    def get_queryset(self, request):
        # 'select_related' for the autocomplete too: it takes its label from
        # 'str(obj)', and that now contains the base unit.
        return (
            super()
            .get_queryset(request)
            .select_related("basiseinheit")
            .prefetch_related("gebinde", "hausartikel__haus")
        )

    def get_readonly_fields(self, request, obj=None):
        # The unit changes through the page: 'ware_unit_change' converts packs,
        # recipes and the factors of the houses along. Here it would change
        # alone, and the numbers would no longer fit (until 10/2026 possible).
        felder = super().get_readonly_fields(request, obj)
        return (*felder, "basiseinheit") if obj is not None else felder

    def vor_dem_loeschen_sperren(self, nummern):
        # Its packs go with the product.
        gebinde_sperren(nummern)

    def inline_sperren(self, inline, eltern, nummern):
        if inline.model is Gebinde:
            gebinde_sperren([eltern.pk])
        else:
            super().inline_sperren(inline, eltern, nummern)

    @admin.display(description=_("Häuser"))
    def haeuser(self, obj: Ware) -> str:
        namen = sorted({artikel.haus.name for artikel in obj.hausartikel.all()})
        return ", ".join(namen) or "—"

    @admin.display(description=_("Gebinde"))
    def gebindeliste(self, obj: Ware) -> str:
        return ", ".join(str(gebinde.anzeigename) for gebinde in obj.gebinde.all()) or "—"

    @admin.display(description=_("aktiv"), boolean=True)
    def ist_aktiv(self, obj: Ware) -> bool:
        # Without an extra query: the house articles are already loaded for 'haeuser'.
        return any(artikel.aktiv for artikel in obj.hausartikel.all())

    @admin.action(description=_("Ware inaktiv setzen (alle aktiven Hausartikel)"))
    def deaktivieren(self, request, queryset):
        jetzt = timezone.now()
        betroffen = sum(artikel.deaktivieren(jetzt) for artikel in queryset)
        self.message_user(
            request,
            _("%(betroffen)s Hausartikel in %(waren)s Ware(n) auf inaktiv gesetzt.")
            % {"betroffen": betroffen, "waren": len(queryset)},
        )

    @admin.action(description=_("Ware einstellen (alle geführten Hausartikel)"))
    def einstellen(self, request, queryset):
        betroffen = sum(ware.einstellen() for ware in queryset)
        self.message_user(
            request,
            _("%(betroffen)s Hausartikel in %(waren)s Ware(n) eingestellt.")
            % {"betroffen": betroffen, "waren": len(queryset)},
        )


@admin.register(Hausartikel)
class HausartikelAdmin(NurUeberDieSeite, VerwaltungSperrtZuerst, admin.ModelAdmin):
    """What a single house carries."""

    list_display = (
        "artikelnummer",
        "name",
        "quelle",
        "haus",
        "ware",
        "warengruppe",
        "inventurart",
        "status",
        "gueltig_bis",
    )
    list_filter = ("haus", "quelle", "status", "warengruppe", "inventurart")
    # Including unit: 'Ware.__str__' names it in every row.
    list_select_related = (
        "haus",
        "ware__basiseinheit",
        "warengruppe",
        "basiseinheit",
        "inventurart",
    )
    search_fields = ("artikelnummer", "name", "ean")
    autocomplete_fields = ("ware",)
    # 'gueltig_bis' follows the status, see 'Hausartikel._status_angleichen'.
    readonly_fields = ("gueltig_bis", "erstellt_am", "geaendert_am")
    actions = ["deaktivieren", "einstellen"]
    fieldsets = (
        (None, {"fields": ("haus", "quelle", "artikelnummer", "name")}),
        (
            _("Zuordnung"),
            {
                "fields": (
                    "ware",
                    "warengruppe",
                    "inventurart",
                    "basiseinheit",
                    "faktor_zur_ware",
                ),
                "description": _(
                    "Die Ware stellt die Identität über alle Häuser her und trägt die "
                    "Gebinde. Warengruppe und Inventurart bleiben hausbezogen. Die Einheit "
                    "ist die des Hauses, meist die der Ware. Zählt das Haus in einer "
                    "anderen Dimension, gehört die Umrechnung dazu."
                ),
            },
        ),
        (
            _("Gültigkeit"),
            {
                "fields": ("status", "gueltig_bis"),
                "description": _(
                    "Inaktiv: gerade nicht im Sortiment, kommt wieder. Eingestellt: "
                    "vorbei, Nummer und Ware sind frei. Nicht zählbar folgt der Ware "
                    "„Nicht zählbar“ und lässt sich hier nicht wählen. Das Datum setzt "
                    "sich mit dem Status."
                ),
            },
        ),
        (
            _("Weiteres"),
            {"fields": ("ean", "erstellt_am", "geaendert_am"), "classes": ("collapse",)},
        ),
    )

    def formfield_for_choice_field(self, db_field, request, **kwargs):
        """The family "Not countable" is not available. The description above says so too.

        The status follows the family of the product ('_status_angleichen').
        Chosen, it would be silently turned back.
        """
        if db_field.name == "status":
            kwargs["choices"] = [
                (wert, name)
                for wert, name in db_field.get_choices(include_blank=False)
                if wert != Artikelstatus.IGNORIERT
            ]
        return super().formfield_for_choice_field(db_field, request, **kwargs)

    def get_readonly_fields(self, request, obj=None):
        # An ignored row only shows its status: the value would otherwise be in
        # no choice, and the form would reject it when saving. The inventory
        # type changes through the page ('inventurart_setzen'), which moves the
        # expectation to the end of its new section. Here it would stay put.
        felder = super().get_readonly_fields(request, obj)
        if obj is not None:
            felder = (*felder, "inventurart")
        if obj is not None and obj.status == Artikelstatus.IGNORIERT:
            return (*felder, "status")
        return felder

    @admin.action(description=_("Ausgewählte Hausartikel auf inaktiv setzen"))
    def deaktivieren(self, request, queryset):
        """Sets status and date together, because individually it would violate the constraint."""
        jetzt = timezone.now()
        anzahl = queryset.filter(status=Artikelstatus.AKTIV).update(
            status=Artikelstatus.INAKTIV, gueltig_bis=jetzt, geaendert_am=jetzt
        )
        self.message_user(
            request,
            _("%(anzahl)s Hausartikel auf inaktiv gesetzt.") % {"anzahl": anzahl},
            messages.SUCCESS,
        )

    @admin.action(description=_("Ausgewählte Hausartikel einstellen"))
    def einstellen(self, request, queryset):
        """Individually instead of via 'update()': discontinued drops out of all counting lists."""
        betroffen = queryset.filter(status__in=GEFUEHRT)
        anzahl = 0
        for artikel in betroffen.select_related("ware"):
            artikel.einstellen()
            anzahl += 1
        self.message_user(
            request,
            _(
                "%(anzahl)s Hausartikel eingestellt. Artikelnummern und Waren sind damit "
                "im jeweiligen Haus wieder frei."
            )
            % {"anzahl": anzahl},
            messages.SUCCESS,
        )


class RezepturkomponenteInline(admin.TabularInline):
    """The recipe of a recipe article.

    The quantity is to be given in the base unit of the respective product.
    It is therefore both in the selection (via 'Ware.__str__') and as a
    column of its own for already saved rows.
    """

    model = Rezepturkomponente
    formset = BaseRezepturkomponenteFormSet
    extra = 1
    formfield_overrides = DEZIMALFELDER
    autocomplete_fields = ("ware",)
    fields = ("ware", "menge_in_basiseinheit", "einheit")
    readonly_fields = ("einheit",)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("ware__basiseinheit")

    @admin.display(description=_("Einheit"))
    def einheit(self, obj: Rezepturkomponente) -> str:
        if obj.ware_id is None:
            return _("— erst auswählen —")
        if obj.rezepturartikel.anteilig:
            return "%"
        return obj.ware.basiseinheit.kurz


@admin.register(Rezepturartikel)
class RezepturartikelAdmin(
    NurUeberDieSeite, NurZuweisbareBehaelter, VerwaltungSperrtZuerst, admin.ModelAdmin
):
    """Composite articles that exist only in this app.

    Without product and without packs. The unit says how counting happens:
    counted in pieces or weighed as a whole. In the second case the
    containers apply. Whether it is active and which tab it counts in derive
    from the components.
    """

    form = RezepturartikelAdminForm
    list_display = ("name", "basiseinheit", "komponenten_kurz")
    list_filter = ("basiseinheit",)
    search_fields = ("name",)
    readonly_fields = ("erstellt_am", "geaendert_am")
    filter_horizontal = ("behaelter",)
    inlines = [RezepturkomponenteInline]
    fieldsets = (
        (None, {"fields": ("name", "basiseinheit", "behaelter", "anteilig")}),
        (_("Weiteres"), {"fields": ("erstellt_am", "geaendert_am"), "classes": ("collapse",)}),
    )

    def get_queryset(self, request):
        # Up to the base unit: 'Rezepturkomponente.__str__' names it too.
        return (
            super()
            .get_queryset(request)
            .select_related("basiseinheit")
            .prefetch_related("komponenten__ware__basiseinheit")
        )

    @admin.display(description=_("Rezeptur"))
    def komponenten_kurz(self, obj: Rezepturartikel) -> str:
        return ", ".join(str(komponente) for komponente in obj.komponenten.all()) or "—"


@admin.register(Gebinde)
class GebindeAdmin(NurUeberDieSeite, VerwaltungSperrtZuerst, admin.ModelAdmin):
    """The counted packagings of a product.

    Weighing is not here but a tick on the product. There was only one of it
    per product and it carried no quantity of its own.
    """

    list_display = ("ware", "name", "ist_einzeln", "umrechnung", "sortierung", "aktiv")
    list_filter = ("aktiv", "ist_einzeln", "ware__basiseinheit")
    list_select_related = ("ware", "ware__basiseinheit")
    search_fields = ("name", "ware__name")
    autocomplete_fields = ("ware",)

    def vor_dem_loeschen_sperren(self, nummern):
        gebinde_sperren(Gebinde.objects.filter(pk__in=nummern).values_list("ware_id", flat=True))

    @admin.display(description=_("Umrechnung"))
    def umrechnung(self, obj: Gebinde) -> str:
        einheit = obj.ware.basiseinheit.kurz
        return f"1 × {menge_kurz(obj.faktor_in_basiseinheit)} {einheit}"


@admin.register(Behaelter)
class BehaelterAdmin(NurUeberDieSeite, VerwaltungSperrtZuerst, admin.ModelAdmin):
    """Vessels with their tare in grams.

    One of them is "without container" with tare 0 from the data migration. It
    is available everywhere and must neither be deleted nor have its tare
    changed, otherwise every weighing that used it would shift.
    """

    list_display = ("name", "tara", "immer_erlaubt")
    search_fields = ("name",)
    filter_horizontal = ("haeuser",)

    def get_readonly_fields(self, request, obj=None):
        return ("tara",) if obj is not None and obj.immer_erlaubt else ()

    def has_delete_permission(self, request, obj=None):
        return not (obj is not None and obj.immer_erlaubt)


@admin.register(Inventurart)
class InventurartAdmin(NurUeberDieSeite, admin.ModelAdmin):
    """The inventory types. The website maintains them, with their order and on the articles.

    Registered for the selection of the inventory and the house article, which point to them.
    """

    list_display = ("name", "reihenfolge")
    search_fields = ("name",)


class ErwartungGebindeInline(AuswahlMitBezuegen, admin.TabularInline):
    model = ErwartungGebinde
    extra = 1
    # No position: the order of the packs is on the product.
    fields = ("gebinde", "standard_menge")

    def get_formset(self, request, obj=None, **kwargs):
        # Pass the expectation through for formfield_for_foreignkey.
        request._erwartung = obj  # pyright: ignore[reportAttributeAccessIssue]
        return super().get_formset(request, obj, **kwargs)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        """Offer only the packs of the expected house article.

        The packs hang on the product, the expectation on the article core.
        Hence the descent via 'konkret()'. Recipe articles have no packs, there
        the selection stays empty.
        """
        if db_field.name == "gebinde":
            kwargs["queryset"] = self._gebindeauswahl(getattr(request, "_erwartung", None))
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    @staticmethod
    def _gebindeauswahl(erwartung):
        if erwartung is None:
            # New expectation: save first, then assign packs.
            return Gebinde.objects.none()
        artikel = erwartung.artikel.konkret()
        if not isinstance(artikel, Hausartikel):
            return Gebinde.objects.none()
        return Gebinde.objects.filter(ware_id=artikel.ware_id, aktiv=True)


@admin.register(Erwartung)
class ErwartungAdmin(NurUeberDieSeite, AuswahlMitBezuegen, admin.ModelAdmin):
    """Which article usually lies at which location.

    'sortierung' is deliberately not in the form: the position is appended at
    the back automatically when creating and should be changed by drag later.
    Entered by hand it would easily run into 'sortierung_je_ort_eindeutig'.
    """

    list_display = ("ort", "haus", "sortierung", "artikel")
    list_filter = ("ort__bereich__haus", "ort")
    list_select_related = ("ort", "ort__bereich__haus", "artikel")
    search_fields = ("artikel__name", "ort__name")
    autocomplete_fields = ("artikel", "ort")
    exclude = ("sortierung",)
    inlines = [ErwartungGebindeInline]

    @admin.display(description=_("Haus"), ordering="ort__bereich__haus__name")
    def haus(self, obj: Erwartung):
        return obj.ort.haus
