"""The counting list of a location: order, packs and default quantities.

Plus splitting and merging locations, whereby the same expectations migrate,
and copying a location, whereby they are duplicated.
"""

from collections import defaultdict
from decimal import Decimal
from typing import cast

from django import forms
from django.core.validators import MinValueValidator
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext

from django_project.form_fields import Dezimalfeld, modellwahl
from django_project.validation import ganzzahl
from organisation.models import Bereich, Ort

from ..models import (
    Erwartung,
    Gebinde,
    Hausartikel,
    Inventurart,
    art_in_der_zaehlliste,
)
from .base import an_stelle
from .split_merge import AufteilenForm, ZusammenfuehrenForm


class ZaehllisteZeileForm(forms.Form):
    """A row of the counting list: which article is counted in which packs.

    The packs are not in a multiple selection field but per pack as a pair of
    tick and quantity field, because every chosen pack has a default quantity,
    and that has no place in a multiple selection. The fields only arise in
    the formset: which ones exist depends on the article.

    For weighable goods the tick **"Weigh"** follows: whether they go on the
    scale at this location ('Erwartung.wiegen_erwartet'). It belongs in the
    same cell as the packs: both answer *how* the article is counted here.
    """

    #: The name of the tick for weighing, present only for weighable goods.
    WIEGEN = "wiegen"

    erwartung = forms.ModelChoiceField(queryset=Erwartung.objects.none(), widget=forms.HiddenInput)

    @staticmethod
    def haken_name(gebinde_id: int) -> str:
        return f"gebinde_{gebinde_id}"

    @staticmethod
    def mengen_name(gebinde_id: int) -> str:
        return f"menge_{gebinde_id}"

    def clean(self) -> dict:
        """Every row needs a pack, or the scale where the product is weighed.

        Without both the article would get no row at all at this location in the
        next inventory: it would be in the counting list and missing from the
        app, and nobody would see why. Whoever no longer wants to count it here
        takes it out of the list. Until 10/2026 only weighable goods were
        checked. A row with nothing to tick (a recipe article, a product without
        an active pack) has nothing to choose here, so the check leaves it be.
        """
        super().clean()
        daten = self.cleaned_data
        haken = [name for name in self.fields if name.startswith("gebinde_")]
        if any(daten.get(name) for name in haken):
            return daten
        if self.WIEGEN in self.fields:
            if not daten.get(self.WIEGEN):
                self.add_error(
                    self.WIEGEN,
                    _("Ohne Gebinde und ohne Wiegen bekäme der Artikel hier keine Zeile."),
                )
        elif haken:
            self.add_error(None, _("Ohne Gebinde bekäme der Artikel hier keine Zeile."))
        return daten


class BaseZaehllisteFormSet(forms.BaseFormSet):
    """Per expectation one fixed row, none for adding.

    Which packs are available depends on the row's article. That is why every
    row builds its fields itself. Recipe articles have no packs, there the
    cell stays empty.

    **One section per inventory type** (wish of 2026-10-09), in the order of
    the types, within it by 'sortierung'. The app counts tab by tab, so the
    order only means something within a type. The section follows from the
    article ('art_in_der_zaehlliste') and is not stored. 'abschnitte' hands
    the rows to the template grouped.
    """

    def __init__(self, *args, ort: Ort, **kwargs):
        self.ort = ort
        erwartungen = list(
            Erwartung.objects.filter(ort=ort)
            .select_related(
                "artikel__hausartikel__ware__basiseinheit",
                "artikel__rezepturartikel__basiseinheit",
            )
            # With the pack: a default on an inactive one stands in its row as text.
            .prefetch_related("gebinde_vorgaben__gebinde")
        )
        self.abschnitt_je_artikel = art_in_der_zaehlliste(
            ort.bereich.haus_id, (satz.artikel_id for satz in erwartungen)
        )
        # After the sections: those create the first type where none exists.
        self.arten = list(Inventurart.objects.all())
        folge = {art.pk: stelle for stelle, art in enumerate(self.arten)}
        self.erwartungen = sorted(
            erwartungen,
            key=lambda satz: (folge[self.abschnitt_je_artikel[satz.artikel_id]], satz.sortierung),
        )
        #: The stored order as the page shows it, and per row its section.
        #: The view moves by these, so a button stays within its type.
        self.folge = [satz.pk for satz in self.erwartungen]
        self.abschnitt_je_erwartung = {
            satz.pk: self.abschnitt_je_artikel[satz.artikel_id] for satz in self.erwartungen
        }
        # The packs of all products of the list in one query. Asked per row
        # there would be as many as rows.
        waren = {
            artikel.ware_id
            for satz in self.erwartungen
            if isinstance(artikel := satz.artikel.konkret(), Hausartikel)
        }
        self.gebinde_je_ware: dict[int, list[Gebinde]] = defaultdict(list)
        for gebinde in Gebinde.objects.filter(ware_id__in=waren, aktiv=True).order_by("sortierung"):
            self.gebinde_je_ware[gebinde.ware_id].append(gebinde)
        kwargs.setdefault("initial", [self._vorbelegung(satz) for satz in self.erwartungen])
        super().__init__(*args, **kwargs)
        if self.is_bound:
            self.erwartungen = self._zeilenfolge_aus_den_daten()

    def _zeilenfolge_aus_den_daten(self) -> list:
        """Which expectation every submitted row means, by its field, not its position.

        The rows are numbered in the order of that time when the page loads.
        Since then the order in the database may have changed: dragging saves it
        at once in the background, and a second tab can do the same. If the
        mapping went by position, the packs of one product would land on the
        other, or saving would have to be rejected although nothing wrong was
        sent. The field sent along says it unambiguously.

        A number that does not (any longer) exist at this location stays as
        None. The row's field then rejects it as invalid.
        """
        nach_pk = {satz.pk: satz for satz in self.erwartungen}
        folge = []
        for index in range(self.total_form_count()):
            zahl = ganzzahl(self.data.get(f"{self.prefix}-{index}-erwartung"))
            folge.append(nach_pk.get(zahl) if zahl is not None else None)
        return folge

    @staticmethod
    def _vorbelegung(erwartung) -> dict:
        vorbelegt = {
            "erwartung": erwartung.pk,
            ZaehllisteZeileForm.WIEGEN: erwartung.wiegen_erwartet,
        }
        for vorgabe in erwartung.gebinde_vorgaben.all():
            menge = vorgabe.standard_menge
            vorbelegt[ZaehllisteZeileForm.haken_name(vorgabe.gebinde_id)] = True
            # Saved with three decimal places. The field would otherwise show
            # "2,500" where "2,5" is meant.
            vorbelegt[ZaehllisteZeileForm.mengen_name(vorgabe.gebinde_id)] = (
                None if menge is None else menge.normalize()
            )
        return vorbelegt

    def add_fields(self, form, index):
        """What the template reads from every row, and why it is attached here.

        The expectation ('erwartung_objekt'), its section ('abschnitt'), the packs
        to choose from ('gebindeauswahl'), one tick and one quantity field per pack
        ('gebindezeilen'), the defaults on inactive packs ('inaktive_vorgaben') and
        the scale tick ('wiegen_haken'). All of it is built per row because the
        packs depend on the row's product. 'zaehlliste.html' and 'zaehlliste.js'
        read these names, so a rename here is a rename there.

        A default on an inactive pack gets no row in new inventories any more
        ('Inventur.zaehlpositionen_anlegen'). It is shown, but as text and not
        as a tick: it stays for the case the pack comes back, and saving the
        list leaves it alone ('speichern').
        """
        super().add_fields(form, index)
        modellwahl(form, "erwartung").queryset = Erwartung.objects.filter(ort=self.ort)
        erwartung = an_stelle(self.erwartungen, index)
        form.erwartung_objekt = erwartung
        form.abschnitt = (
            None if erwartung is None else self.abschnitt_je_artikel.get(erwartung.artikel_id)
        )
        form.gebindeauswahl = list(self._gebindeauswahl(erwartung))
        form.inaktive_vorgaben = (
            []
            if erwartung is None
            else [v for v in erwartung.gebinde_vorgaben.all() if not v.gebinde.aktiv]
        )

        # The rows the template iterates: per pack its tick and its quantity
        # field, in the order maintained on the product.
        form.gebindezeilen = []
        for gebinde in form.gebindeauswahl:
            haken = ZaehllisteZeileForm.haken_name(gebinde.pk)
            menge = ZaehllisteZeileForm.mengen_name(gebinde.pk)
            form.fields[haken] = forms.BooleanField(
                required=False,
                label=gebinde.anzeigename,
                widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
            )
            form.fields[menge] = Dezimalfeld(
                required=False,
                localize=True,
                max_digits=10,
                decimal_places=3,
                validators=[MinValueValidator(Decimal("0"))],
                label=_("Standardmenge %(gebinde)s") % {"gebinde": gebinde.anzeigename},
                widget=forms.TextInput(
                    attrs={
                        "class": "form-control form-control-sm",
                        "inputmode": "decimal",
                        "placeholder": _("Menge"),
                        "size": "6",
                    }
                ),
            )
            form.gebindezeilen.append((gebinde, form[haken], form[menge]))

        form.wiegen_haken = None
        if self._wird_gewogen(erwartung):
            # 'data-wiegen' is how the menu above the column finds the scale
            # ticks of all rows ('zaehlliste.js'). The name carries the row
            # number, so the script could not go by it.
            form.fields[ZaehllisteZeileForm.WIEGEN] = forms.BooleanField(
                required=False,
                label=_("Wiegen"),
                widget=forms.CheckboxInput(
                    attrs={"class": "form-check-input", "data-wiegen": True}
                ),
            )
            form.wiegen_haken = form[ZaehllisteZeileForm.WIEGEN]

    @property
    def mit_abschnitten(self) -> bool:
        """Headings only where there is more than one type. With one there is nothing to divide."""
        return len(self.arten) > 1

    def abschnitte(self) -> list[tuple[Inventurart, list]]:
        """The rows per type, only types with rows, in the order of the types.

        A row whose expectation is gone (a sent number that no longer exists)
        is left out. 'clean()' reports it for the whole list.
        """
        zeilen: dict[int, list] = defaultdict(list)
        for form in self.forms:
            if form.erwartung_objekt is None:
                continue
            zeilen[form.abschnitt or self.arten[0].pk].append(form)
        return [(art, zeilen[art.pk]) for art in self.arten if zeilen[art.pk]]

    @staticmethod
    def _wird_gewogen(erwartung) -> bool:
        """Only weighable goods. A weighed recipe article has nothing but the scale."""
        if erwartung is None:
            return False
        return erwartung.artikel.konkret().abwiegbare_ware

    def _gebindeauswahl(self, erwartung) -> list[Gebinde]:
        if erwartung is None:
            return []
        artikel = erwartung.artikel.konkret()
        if not isinstance(artikel, Hausartikel):
            return []
        return self.gebinde_je_ware.get(artikel.ware_id, [])

    def _weggefallene_melden(self) -> None:
        """A row whose expectation is gone is an error of the whole list.

        A second window removed the row meanwhile. Until 10/2026 the page
        rendered it empty, with buttons that led nowhere, and the error of its
        hidden field stood nowhere. Nothing is saved: the person reloads and
        sees what is left.
        """
        if any(form.erwartung_objekt is None for form in self.forms):
            raise forms.ValidationError(
                _("Eine Zeile wurde inzwischen entfernt. Bitte die Seite neu laden."),
                code="weggefallen",
            )

    def clean(self) -> None:
        """Every expectation may stand in only one row.

        The browser interface never sends that twice. If it did, the last row
        would silently win, and the ticks of the first would be lost. Better to
        reject then.
        """
        self._weggefallene_melden()
        super().clean()
        if any(self.errors):
            return
        gesehen = set()
        for form in self.forms:
            gemeint = getattr(form, "cleaned_data", {}).get("erwartung")
            if gemeint is None:
                continue
            if gemeint.pk in gesehen:
                raise forms.ValidationError(
                    _("Ein Artikel stand zweimal in der Zählliste. Bitte die Seite neu laden.")
                )
            gesehen.add(gemeint.pk)

    def verworfene_zeilen(self) -> list:
        """The rows 'speichern' leaves out although something was typed in them.

        A row button (arrow, ✕) and the saving in the background act even if a
        row does not hold up. Whoever has changed such a row should hear that
        the change did not arrive. A row that was invalid as it came from the
        database (without pack, and without scale where it is weighed) is not
        reported: nothing of the person's was lost there.
        """
        # A row whose expectation is gone has nothing left to name.
        zeilen = [form for form in self.forms if form.erwartung_objekt is not None]
        if self.non_form_errors():
            return [form for form in zeilen if form.has_changed()]
        return [form for form in zeilen if form.errors and form.has_changed()]

    def speichern(self) -> None:
        """Reconciles the defaults per row, tick and quantity in one go ('vorgaben_abgleichen').

        **Only the rows that hold up.** The formset as a whole may be invalid
        while a row button is pressed: a row can stand without pack and scale
        in the database (taken in before 10/2026, or its only pack went
        inactive), and nobody has touched it. That row must not keep the ✕ of
        another row from working, and it must not keep the valid rows from
        being saved either. A row with an error keeps its stored state. A
        duplicate row (the same expectation twice) stops everything: the last
        one would otherwise silently win.
        """
        if self.non_form_errors():
            return
        for form in self.forms:
            if form.errors:
                continue
            daten = getattr(form, "cleaned_data", {})
            erwartung = daten.get("erwartung")
            if erwartung is None:
                continue
            # The loaded row with its prefetched pack defaults, not the fresh
            # instance the field fetched: that cost a query per row (until 10/2026).
            geladen = getattr(form, "erwartung_objekt", None)
            if geladen is not None and geladen.pk == erwartung.pk:
                erwartung = geladen

            gewuenscht = {
                gebinde.pk: daten.get(ZaehllisteZeileForm.mengen_name(gebinde.pk))
                for gebinde in form.gebindeauswahl
                if daten.get(ZaehllisteZeileForm.haken_name(gebinde.pk))
            }
            angeboten = {gebinde.pk for gebinde in form.gebindeauswahl}
            erwartung.vorgaben_abgleichen(gewuenscht, angeboten)

            if form.wiegen_haken is not None:
                wiegen = bool(daten.get(ZaehllisteZeileForm.WIEGEN))
                if wiegen != erwartung.wiegen_erwartet:
                    erwartung.wiegen_erwartet = wiegen
                    erwartung.save(update_fields=["wiegen_erwartet"])


ZaehllisteFormSet = cast(
    "type[BaseZaehllisteFormSet]",
    forms.formset_factory(ZaehllisteZeileForm, formset=BaseZaehllisteFormSet, extra=0),
)


def _andere_orte(ort: Ort):
    """With whom the name can collide: the same area, without the location itself."""
    return Ort.objects.filter(bereich=ort.bereich).exclude(pk=ort.pk)


def _ortsname_vergeben(ort: Ort, name: str) -> str:
    # The location name is unique per area.
    return _("Im Bereich %(bereich)s gibt es „%(name)s“ schon.") % {
        "bereich": ort.bereich.name,
        "name": name,
    }


class OrtAufteilenForm(AufteilenForm):
    """A location becomes two: the old one stays, next to it a new one arises in the same area."""

    satz: Ort
    name = forms.CharField(max_length=100, label=_("Name des bisherigen Orts"))
    neuer_name = forms.CharField(max_length=100, label=_("Name des neuen Orts"))
    wandern = forms.ModelMultipleChoiceField(
        queryset=Erwartung.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label=_("Zum neuen Ort"),
    )
    gleiche_namen = _("Die beiden Orte brauchen verschiedene Namen.")

    def zur_wahl(self):
        return (
            Erwartung.objects.filter(ort=self.satz)
            # The location including area and house: the selection labels every row
            # via 'Erwartung.__str__', and that names the location. Without the
            # prefetch three queries per article would be added (measured: 147 for 47).
            .select_related(
                "ort__bereich__haus",
                "artikel__hausartikel__ware",
                "artikel__rezepturartikel",
            )
            .prefetch_related("gebinde_vorgaben__gebinde")
            .order_by("sortierung")
        )

    def namensvettern(self):
        return _andere_orte(self.satz)

    def vergeben(self, name: str) -> str:
        return _ortsname_vergeben(self.satz, name)


class OrtZusammenfuehrenForm(ZusammenfuehrenForm):
    """A second location of the same area is absorbed into this one.

    Available are only locations of **the same area**. Rejected is a location
    count positions still hang on: they belong to an inventory that is not
    closed yet, point to the location via PROTECT, and deleting it would tear
    the running count apart.
    """

    ziel: Ort
    quelle = forms.ModelChoiceField(
        queryset=Ort.objects.none(),
        label=_("Aufgehen lassen"),
        empty_label=_("Ort wählen"),
    )
    name = forms.CharField(max_length=100, label=_("Name des zusammengeführten Orts"))

    def kandidaten(self):
        return _andere_orte(self.ziel).order_by("name")

    def namensvettern(self):
        return _andere_orte(self.ziel)

    def vergeben(self, name: str) -> str:
        return _ortsname_vergeben(self.ziel, name)

    def clean_quelle(self) -> Ort:
        quelle = self.cleaned_data["quelle"]
        inventuren = sorted(
            {
                str(position.inventur)
                for position in quelle.zaehlpositionen.select_related("inventur__haus")
            }
        )
        if inventuren:
            raise forms.ValidationError(
                ngettext(
                    "An „%(quelle)s“ hängen noch Zählpositionen von %(inventuren)s. Zusammenführen "
                    "geht erst, wenn diese Inventur abgeschlossen oder gelöscht ist.",
                    "An „%(quelle)s“ hängen noch Zählpositionen von %(inventuren)s. Zusammenführen "
                    "geht erst, wenn diese Inventuren abgeschlossen oder gelöscht sind.",
                    len(inventuren),
                )
                % {"quelle": quelle.name, "inventuren": ", ".join(inventuren)}
            )
        return quelle


class OrtKopierenForm(forms.Form):
    """A location arises a second time: in which area, and what it is called there.

    Available is **every** area, also those of other houses. The name only
    has to be free in the chosen area, so the copy in another area may be
    called like the original.
    """

    bereich = forms.ModelChoiceField(
        queryset=Bereich.objects.none(),
        label=_("Bereich der Kopie"),
        empty_label=_("Bereich wählen"),
    )
    name = forms.CharField(max_length=100, label=_("Name der Kopie"))

    def __init__(self, *args, ort: Ort, **kwargs):
        super().__init__(*args, **kwargs)
        self.ort = ort
        modellwahl(self, "bereich").queryset = Bereich.objects.select_related("haus").order_by(
            "haus__name", "name"
        )

    def clean(self) -> dict:
        super().clean()
        daten = self.cleaned_data
        name, bereich = daten.get("name", "").strip(), daten.get("bereich")
        if not name or bereich is None:
            return daten
        if Ort.objects.filter(bereich=bereich, name=name).exists():
            self.add_error(
                "name",
                _("Im Bereich %(bereich)s gibt es „%(name)s“ schon.")
                % {"bereich": bereich.name, "name": name},
            )
        daten["name"] = name
        return daten
