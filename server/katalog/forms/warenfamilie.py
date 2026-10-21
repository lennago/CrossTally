"""Product families: create including products, split, merge."""

from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Case, Q, QuerySet, When
from django.utils.translation import gettext_lazy as _

from django_project.form_fields import Dezimalfeld, modellwahl, wahlmenge
from django_project.validation import ganzzahl

from ..models import (
    FAKTOR_MINDESTENS,
    FAKTOR_NACHKOMMASTELLEN,
    FAKTOR_STELLEN_GESAMT,
    FAKTOR_ZU_KLEIN,
    Ware,
    Warenfamilie,
    eingabe_zu_faktor,
)
from ..warenfamilie_split_merge import Fehlstelle, fehlende_faktoren
from .filters import baumauswahl
from .split_merge import AufteilenForm, ZusammenfuehrenForm


def faktorfeld(ware: Ware, dimension: str) -> Dezimalfeld:
    """The field for a factor a product still lacks: "„Name“: g je Stk."."""
    return Dezimalfeld(
        max_digits=FAKTOR_STELLEN_GESAMT,
        decimal_places=FAKTOR_NACHKOMMASTELLEN,
        min_value=FAKTOR_MINDESTENS,
        localize=True,
        label=_("„%(name)s“: %(faktor)s")
        % {"name": ware.name, "faktor": ware.faktor_beschriftung(dimension)},
        error_messages={
            "required": _("Ohne diesen Faktor lässt sich die Ware nicht summieren."),
            "min_value": FAKTOR_ZU_KLEIN,
        },
    )


def faktorfeldname(ware: Ware, dimension: str) -> str:
    return f"faktor_{ware.pk}_{dimension}"


class MitFaktorfeldern(forms.Form):
    """A form that asks for the factors products still lack, one field each.

    The fields only arise once the choice is fixed: before that nobody knows
    which are needed. Whoever needs them calls 'faktorfelder_anlegen' with
    the missing places ('Fehlstelle').
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        #: Product and dimension for every factor that is still missing.
        self.fehlstellen: list[Fehlstelle] = []

    def faktorfelder_anlegen(self, fehlstellen: list[Fehlstelle]) -> None:
        self.fehlstellen = fehlstellen
        for ware, dimension in fehlstellen:
            self.fields[faktorfeldname(ware, dimension)] = faktorfeld(ware, dimension)

    @property
    def faktorfelder(self) -> list:
        return [self[faktorfeldname(ware, dimension)] for ware, dimension in self.fehlstellen]

    def faktoren(self) -> dict[tuple[int, str], Decimal]:
        """Per product and dimension that lacked its factor: the entry, as the model stores it.

        Entered may be the other way round ("kg je Stk."), see 'kehrwert'.
        """
        faktoren = {}
        for ware, dimension in self.fehlstellen:
            wert = self.cleaned_data.get(faktorfeldname(ware, dimension))
            if wert is not None:
                faktoren[ware.pk, dimension] = eingabe_zu_faktor(
                    ware.basiseinheit.dimension, dimension, wert
                )
        return faktoren

    def faktoren_schreiben(self) -> None:
        """Writes the entered factors to the products, before anything hangs on them."""
        je_ware: dict[int, dict[str, Decimal]] = {}
        for (nummer, dimension), wert in self.faktoren().items():
            je_ware.setdefault(nummer, {})[dimension] = wert
        for ware in Ware.objects.filter(pk__in=je_ware):
            ware.umrechnungen_setzen(ware.faktoren() | je_ware[ware.pk])


class WarenfamilieForm(MitFaktorfeldern, forms.ModelForm):
    """A family including its products, all in one step.

    The products are here because the family can decide nothing without them:
    without products there is nothing to sum, and no lead unit. Any unit
    may be the lead unit. A product of another dimension has to carry its
    factor to it ('WareUmrechnung'), otherwise the sum could not be formed.
    Available are only products **without** a family, because a product
    belongs in exactly one.

    It also works without products: then the lead unit stays empty, and the
    first product later determines it.

    A family may stand below another one ('uebergeordnet'). Then its
    products are summed there too, in that family's unit, so they need
    their factor into that dimension as well. Whatever is missing, the form
    asks for it in one go ('MitFaktorfeldern') instead of sending someone
    to every product: placing "beers" under a family in litres would
    otherwise take one visit per beer.

    Created from a family's page as a new family **below** it
    ('oberfamilie'), the products of that family are up for choice as well,
    first in the list: they move down into the new family. So are the other
    families below it ('unterfamilien'): ticked, they move under the new
    one. That is how a family is split into subfamilies and levels, without
    a detour past "without family".
    """

    ware = forms.ModelMultipleChoiceField(
        queryset=Ware.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label=_("Waren"),
        help_text=_("Zur Wahl stehen nur Waren, die noch keiner Familie angehören."),
    )
    unterfamilien = forms.ModelMultipleChoiceField(
        queryset=Warenfamilie.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label=_("Unterfamilien übernehmen"),
        help_text=_(
            "Diese Familien stehen bisher neben der neuen. Angehakt stehen sie danach unter ihr, "
            "mit allem, was zu ihnen gehört."
        ),
    )

    class Meta:
        model = Warenfamilie
        fields = ("name", "beschreibung", "uebergeordnet", "leiteinheit")
        widgets = {"beschreibung": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, oberfamilie: Warenfamilie | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        #: The family this one is created below, with its products up for choice.
        self.oberfamilie = oberfamilie
        frei = Q(warenfamilie__isnull=True)
        if self.instance.pk:
            # When editing the own products belong to it, otherwise they would
            # drop out when saving.
            frei |= Q(warenfamilie=self.instance)
            self.fields["ware"].initial = self.instance.waren.all()
        elif oberfamilie is not None:
            frei |= Q(warenfamilie=oberfamilie)
            self.fields["uebergeordnet"].initial = oberfamilie.pk
        if oberfamilie is not None and not self.instance.pk:
            modellwahl(self, "unterfamilien").queryset = oberfamilie.unterfamilien.order_by("name")
        else:
            del self.fields["unterfamilien"]
        waren = Ware.objects.filter(frei).select_related("basiseinheit")
        if oberfamilie is not None:
            # The products of the family above first: they are what someone
            # came here to move down.
            waren = waren.annotate(
                von_oben=Case(When(warenfamilie=oberfamilie, then=0), default=1)
            ).order_by("von_oben", "name")
        else:
            waren = waren.order_by("name")
        modellwahl(self, "ware").queryset = waren

        # The script presets the unit of the first chosen product. Which
        # unit a product carries stands as an attribute on every box.
        self.einheit_je_ware = {ware.pk: ware.basiseinheit_id for ware in waren}
        #: The products of the families below, summed here as well.
        self.waren_darunter: list[Ware] = []
        if self.instance.pk:
            self.waren_darunter = self._waren_unter(self.instance.nachkommen_nummern())

        # Above it may stand any real family except itself and the ones below it.
        oben = Warenfamilie.objects.exclude(sammelt_ignorierte=True)
        if self.instance.pk:
            oben = oben.exclude(pk__in=[self.instance.pk, *self.instance.nachkommen_nummern()])
        modellwahl(self, "uebergeordnet").queryset = oben
        # In the order of the tree, a family below another indented under it,
        # so the list reads like the family list. The queryset stays the
        # judge of what is allowed.
        modellwahl(self, "uebergeordnet").choices = [
            ("", _("Keine, steht für sich")),
            *baumauswahl(oben),
        ]

        # Any unit: a family in litres may hold nothing but bottles in
        # pieces. What a product lacks to be summed there, the form asks for.
        self.fields["leiteinheit"].required = False
        if self.instance.pk and self.instance.sammelt_ignorierte:
            # "Not countable" belongs to the application: name and description
            # come from the account's language, not from the database.
            for feld, wert in (
                ("name", self.instance.anzeigename),
                ("beschreibung", self.instance.anzeigebeschreibung),
            ):
                self.fields[feld].disabled = True
                self.initial[feld] = str(wert)
            self.fields["uebergeordnet"].disabled = True

        if self.is_bound:
            self.faktorfelder_anlegen(self._fehlstellen_laut_eingabe())

    @staticmethod
    def _waren_unter(nummern) -> list[Ware]:
        """The products of these families, with what the factor checks need."""
        return list(
            Ware.objects.filter(warenfamilie_id__in=nummern)
            .select_related("basiseinheit")
            .prefetch_related("umrechnungen")
            .order_by("name")
        )

    def _waren_der_uebernommenen(self, nummern) -> list[Ware]:
        """The products of the families taken over, and of everything below them."""
        nummern = [zahl for wert in nummern if (zahl := ganzzahl(wert)) is not None]
        if "unterfamilien" not in self.fields or not nummern:
            return []
        erlaubt = set(wahlmenge(self, "unterfamilien").values_list("pk", flat=True))
        gewaehlt = [nummer for nummer in nummern if nummer in erlaubt]
        return self._waren_unter(Warenfamilie.mit_nachkommen(gewaehlt)) if gewaehlt else []

    # -- The missing factors ----------------------------------------------

    def _fehlstellen_laut_eingabe(self) -> list[Fehlstelle]:
        """What the posted choice would leave unsummable, before validation.

        Read from the raw data, because the fields have to exist before the
        form validates. A nonsensical value simply yields no field, and the
        validation says what is wrong with it.
        """
        if self.instance.pk and self.instance.sammelt_ignorierte:
            return []
        leiteinheit = wahlmenge(self, "leiteinheit").filter(pk=self._nummer("leiteinheit")).first()
        if leiteinheit is None:
            return []
        oben_nummer = self._nummer("uebergeordnet")
        oben = (
            wahlmenge(self, "uebergeordnet").filter(pk=oben_nummer).first()
            if oben_nummer is not None
            else None
        )
        nummern = [zahl for wert in self._liste("ware") if (zahl := ganzzahl(wert)) is not None]
        gewaehlt = list(
            wahlmenge(self, "ware")
            .filter(pk__in=nummern)
            .select_related("basiseinheit")
            .prefetch_related("umrechnungen")
        )
        return self.instance.fehlende_faktoren(
            leiteinheit,
            [
                *gewaehlt,
                *self.waren_darunter,
                *self._waren_der_uebernommenen(self._liste("unterfamilien")),
            ],
            uebergeordnet=oben,
        )

    def _liste(self, feld: str) -> list:
        """The posted values of a box list, from a QueryDict or a plain dictionary (tests)."""
        listen = getattr(self.data, "getlist", None)
        return list((listen(feld) if listen is not None else self.data.get(feld)) or [])

    def _nummer(self, feld: str) -> int | None:
        return ganzzahl(self.data.get(feld))

    # -- Validation --------------------------------------------------------

    def clean(self) -> dict:
        """Lead unit and products have to fit together, except in "Not countable".

        Nothing is summed there. Instead every product that switches in or out
        means that its house articles become ignored or active again. Whether
        that works is said by 'Ware.pruefe_familienwechsel'.
        """
        super().clean()
        daten = self.cleaned_data
        waren = daten.get("ware")
        leiteinheit = daten.get("leiteinheit")

        if self.instance.sammelt_ignorierte:
            daten["leiteinheit"] = None
            daten["uebergeordnet"] = None
            gewaehlt = {ware.pk for ware in waren or []}
            bisher = set(self.instance.waren.values_list("pk", flat=True))
            wechsel = [(ware, self.instance) for ware in waren or [] if ware.pk not in bisher]
            wechsel += [(ware, None) for ware in self.instance.waren.exclude(pk__in=gewaehlt)]
            for ware, familie in wechsel:
                try:
                    ware.pruefe_familienwechsel(familie)
                except ValidationError as fehler:
                    for text in fehler.messages:
                        self.add_error(
                            "ware",
                            _("„%(name)s“: %(text)s") % {"name": ware.name, "text": text},
                        )
            return daten

        uebernommen = self._waren_der_uebernommenen(
            [familie.pk for familie in daten.get("unterfamilien") or []]
        )
        im_baum = [*(waren or []), *self.waren_darunter, *uebernommen]
        if not im_baum:
            # Without products there is nothing to sum and nothing to choose.
            daten["leiteinheit"] = None
            return daten

        if leiteinheit is None:
            self.add_error(
                "leiteinheit", _("Bitte die Einheit wählen, in der die Summe stehen soll.")
            )
        return daten

    def save(self, commit=True):
        if commit:
            # The factors first: the products are checked against the family
            # as soon as they hang on it.
            self.faktoren_schreiben()
        familie = super().save(commit=commit)
        if commit:
            self.zuordnen(familie)
            for unter in self.cleaned_data.get("unterfamilien") or []:
                unter.uebergeordnet = familie
                unter.save(update_fields=["uebergeordnet"])
        return familie

    def zuordnen(self, familie: Warenfamilie) -> None:
        """The chosen products in, the deselected ones out.

        Saved product by product instead of via 'update()': that way 'save()'
        updates the house articles where "Not countable" is involved.
        """
        gewaehlt = {ware.pk for ware in self.cleaned_data.get("ware") or []}
        for ware in familie.waren.exclude(pk__in=gewaehlt):
            ware.warenfamilie = None
            ware.save(update_fields=["warenfamilie"])
        for ware in Ware.objects.filter(pk__in=gewaehlt).exclude(warenfamilie=familie):
            ware.warenfamilie = familie
            ware.save(update_fields=["warenfamilie"])


def _andere_familien(familie: Warenfamilie) -> QuerySet[Warenfamilie]:
    """With whom the name can collide: every other family."""
    return Warenfamilie.objects.exclude(pk=familie.pk)


def _familienname_vergeben(name: str) -> str:
    return _("Eine Familie „%(name)s“ gibt es schon.") % {"name": name}


class WarenfamilieAufteilenForm(AufteilenForm):
    """A family becomes two: the old one stays, next to it a new one arises.

    The lead unit is updated by 'warenfamilie_split_merge.aufteilen' for
    both halves.
    """

    satz: Warenfamilie
    name = forms.CharField(max_length=100, label=_("Name der bisherigen Familie"))
    neuer_name = forms.CharField(max_length=100, label=_("Name der neuen Familie"))
    wandern = forms.ModelMultipleChoiceField(
        queryset=Ware.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label=_("Zur neuen Familie"),
    )
    gleiche_namen = _("Die beiden Familien brauchen verschiedene Namen.")

    def zur_wahl(self):
        return self.satz.waren.select_related("basiseinheit").order_by("name")

    def namensvettern(self):
        return _andere_familien(self.satz)

    def vergeben(self, name: str) -> str:
        return _familienname_vergeben(name)


class WarenfamilieZusammenfuehrenForm(MitFaktorfeldern, ZusammenfuehrenForm):
    """A second family is absorbed into this one.

    If an added product lacks its factor to the lead unit of the remaining
    family or of a family above it, the form asks for it, one field per
    product and dimension ('warenfamilie_split_merge.fehlende_faktoren').
    The fields only arise once the choice is fixed: before that nobody
    knows which are needed.
    """

    ziel: Warenfamilie
    quelle = forms.ModelChoiceField(
        queryset=Warenfamilie.objects.none(),
        label=_("Aufgehen lassen"),
        empty_label=_("Familie wählen"),
    )
    name = forms.CharField(max_length=100, label=_("Name der zusammengeführten Familie"))

    def __init__(self, *args, ziel: Warenfamilie, **kwargs):
        super().__init__(*args, ziel=ziel, **kwargs)
        # Also for an empty family without a unit: it takes over that of the
        # absorbed one, and the families above it sum along.
        if (quelle := self._gewaehlte_quelle()) is not None:
            self.faktorfelder_anlegen(fehlende_faktoren(ziel, quelle))

    def kandidaten(self):
        return Warenfamilie.echte().exclude(pk=self.ziel.pk)

    def namensvettern(self):
        return _andere_familien(self.ziel)

    def vergeben(self, name: str) -> str:
        return _familienname_vergeben(name)

    def _gewaehlte_quelle(self) -> Warenfamilie | None:
        pk = ganzzahl(self.data.get("quelle")) if self.is_bound else None
        if pk is None:
            return None
        return wahlmenge(self, "quelle").filter(pk=pk).first()
