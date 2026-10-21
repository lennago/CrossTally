"""The product with its packs: create, edit, split, merge."""

from decimal import Decimal
from typing import cast

from django import forms
from django.db.models import Case, Value, When
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext

from django_project.form_fields import Dezimalfeld, auswahl, modellwahl
from django_project.labels import Beschriftung

from ..models import (
    FAKTOR_MINDESTENS,
    FAKTOR_NACHKOMMASTELLEN,
    FAKTOR_STELLEN_GESAMT,
    FAKTOR_ZU_KLEIN,
    SICHTBAR,
    Artikelstatus,
    Basiseinheit,
    Behaelter,
    Dimension,
    Erwartung,
    Gebinde,
    Hausartikel,
    Inventurart,
    Rezepturartikel,
    Ware,
    Warenfamilie,
    eingabe_zu_faktor,
    menge_kurz,
)
from ..ware_merge import NAMENSWAHL, UMBENENNEN, Dublette
from ..ware_unit_change import umrechnungen_danach, umrechnungsfaktor
from .base import EinheitenAuswahl, MitKlartextfehler, WarenAuswahl, hilfetext
from .filters import baumauswahl
from .hausartikel import ANTWORTEN, WIEDER_AKTIVIEREN
from .split_merge import AufteilenForm


class WareForm(forms.ModelForm):
    """The product across all houses, including its conversions and whether it is weighed.

    The **conversions** are one field per foreign dimension ('faktor_<DIMENSION>'):
    what one unit of the product is in grams, millilitres or pieces. The
    field of the product's own dimension is hidden in the browser, because
    it hangs on the currently **selected** unit and not on the stored one.
    Containers belong to weighing and only show with the tick. Here the same
    rules stand as a check, without JavaScript and for everything that comes
    past the form.
    """

    FAKTORFELD = "faktor_"

    # The type hangs on the house articles. Here it is set for all of them at
    # once, as in the list. A model form only writes its own fields, so 'save'
    # sets it.
    inventurart = forms.ModelChoiceField(
        queryset=Inventurart.objects.none(),
        required=False,
        label=_("Inventurart"),
        empty_label=_("unverändert lassen"),
        help_text=_("Gilt für alle Artikel der Ware, in jedem Haus."),
    )

    class Meta:
        model = Ware
        fields = (
            "name",
            "basiseinheit",
            "warenfamilie",
            "abwiegbar",
            "behaelter",
        )
        widgets = {
            "basiseinheit": EinheitenAuswahl,
            "behaelter": forms.CheckboxSelectMultiple,
        }

    # With a comma, as it is typed. Technically: how many new units one old unit is.
    # From millilitres into pieces that is 0.0013… and needs every place.
    umrechnung = Dezimalfeld(
        required=False,
        localize=True,
        max_digits=FAKTOR_STELLEN_GESAMT,
        decimal_places=FAKTOR_NACHKOMMASTELLEN,
        min_value=FAKTOR_MINDESTENS,
        error_messages={"min_value": FAKTOR_ZU_KLEIN},
        label=_("Umrechnung beim Wechsel der Einheit"),
        help_text=_(
            "Nur beim Wechsel zwischen Stück, Gewicht und Volumen: wie viele der neuen Einheit "
            "ist eine der bisherigen? Darf leer bleiben, wenn die Ware schon eine Umrechnung in "
            "die neue Einheit hat. Die Umrechnungen der Ware rechnen dabei mit."
        ),
    )

    #: After 'clean()': with which factor the view converts after saving.
    #: 'None' means the unit stays.
    umrechnungsfaktor: Decimal | None = None

    #: After 'clean()': the conversions 'save()' writes, per foreign dimension.
    faktoren: dict[str, Decimal]

    #: What the conversion fields are called per dimension while no unit is
    #: chosen yet. With one, the script names them after it: "g je l",
    #: "kg je Stk.". The direction follows the rank of the dimensions
    #: ('RANG_JE_DIMENSION'), the model stores every factor the same way.
    FAKTORNAMEN = {
        Dimension.MASSE: _("Masse"),
        Dimension.VOLUMEN: _("Volumen"),
        Dimension.STUECK: _("Stück"),
        Dimension.PORTION: _("Portion"),
    }
    FAKTORHILFEN = {
        Dimension.MASSE: _(
            "Was ein Stück wiegt oder was ein Liter wiegt. Braucht die Waage und eine "
            "Familie, die in g oder kg summiert."
        ),
        Dimension.VOLUMEN: _(
            "Was ein Stück fasst, oder bei Gewichtsware das Gewicht je Liter. Braucht eine "
            "Familie, die in ml oder l summiert."
        ),
        Dimension.STUECK: _(
            "Was ein Stück wiegt oder fasst, bei Ware in kg oder Litern. Braucht eine "
            "Familie, die in Stück summiert."
        ),
        Dimension.PORTION: _(
            "Was eine Portion ist: 100 g Nüsse, ein Riegel, 0,3 l Limonade. Braucht eine "
            "Familie, die in Portionen summiert."
        ),
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        modellwahl(self, "behaelter").queryset = Behaelter.zuweisbar()
        self._inventurart_vorbereiten()
        if "basiseinheit" in self.fields:
            # No product is carried in portions. The unit belongs to the families.
            modellwahl(self, "basiseinheit").queryset = Basiseinheit.fuer_artikel()
        # The families in the order of the tree, subfamilies indented, as in
        # the family list. The queryset stays the judge of what is allowed.
        modellwahl(self, "warenfamilie").choices = [
            ("", modellwahl(self, "warenfamilie").empty_label),
            *baumauswahl(Warenfamilie.objects.all()),
        ]
        #: Remembered from the database: 'instance' already carries the new one after validation.
        self.bisherige_einheit = self.instance.basiseinheit if self.instance.pk else None
        self.faktoren = {}
        gespeichert = self.instance.faktor_eingaben() if self.instance.pk else {}
        for dimension in Dimension:
            # As precise as the column. An entry like "750 ml je Stk." needs
            # few places. But a factor that is not turned round comes back as
            # stored, after a change of unit with up to twelve places, and
            # saving the form again must not fail on it.
            self.fields[self.feldname(dimension)] = Dezimalfeld(
                required=False,
                localize=True,
                max_digits=FAKTOR_STELLEN_GESAMT,
                decimal_places=FAKTOR_NACHKOMMASTELLEN,
                min_value=FAKTOR_MINDESTENS,
                error_messages={"min_value": FAKTOR_ZU_KLEIN},
                initial=gespeichert.get(dimension),
                label=self.FAKTORNAMEN[dimension],
                help_text=self.FAKTORHILFEN[dimension],
            )
        danach = ["warenfamilie", "inventurart"]
        if self.bisherige_einheit is None:
            # A new product has nothing that could be converted.
            del self.fields["umrechnung"]
            self.order_fields(["name", "basiseinheit", *self.faktorfeldnamen(), *danach])
        else:
            self.order_fields(
                ["name", "basiseinheit", "umrechnung", *self.faktorfeldnamen(), *danach]
            )

    def _inventurart_vorbereiten(self) -> None:
        """The type of all articles, or nothing preselected where they differ.

        A product without articles has no type yet: its first article gets
        one in its own form. Then the field would only be a promise.
        """
        if not self.instance.pk or not self.instance.hausartikel.exists():
            del self.fields["inventurart"]
            return
        modellwahl(self, "inventurart").queryset = Inventurart.objects.all()
        if (gemeinsame := self.instance.inventurart) is not None:
            self.fields["inventurart"].initial = gemeinsame
        else:
            self.fields["inventurart"].help_text = _(
                "Die Artikel stehen in verschiedenen Arten. Eine Wahl hier gilt für alle, in "
                "jedem Haus."
            )

    @classmethod
    def feldname(cls, dimension: str) -> str:
        return f"{cls.FAKTORFELD}{dimension}"

    @classmethod
    def faktorfeldnamen(cls) -> list[str]:
        return [cls.feldname(dimension) for dimension in Dimension]

    def einheit(self, daten: dict) -> Basiseinheit | None:
        """The unit the checks compute with: here the chosen one."""
        return daten.get("basiseinheit")

    def clean(self):
        """Four rules in one order: unit change, factors, containers, mass for the scale.

        The unit change comes first because the factors are read in the new
        unit. Containers belong to weighing only. The scale needs grams, so a
        product in another dimension needs its mass factor, and a recipe with
        shares in percent keeps that factor as long as the product stands in
        it. 'has_error' keeps a field from carrying two messages for one gap.
        """
        super().clean()
        daten = self.cleaned_data
        self._einheitenwechsel_pruefen(daten)
        self._faktoren_uebernehmen(daten)
        abwiegbar = daten.get("abwiegbar")
        einheit = self.einheit(daten)

        if not abwiegbar and daten.get("behaelter"):
            self.add_error(
                "behaelter",
                _("Behälter gehören zum Wiegen. Ohne den Haken bleiben sie leer."),
            )

        # The scale delivers grams. For anything but mass it needs to know
        # what a unit weighs, and the field for that is the one place to say it.
        if (
            abwiegbar
            and einheit is not None
            and einheit.dimension != Dimension.MASSE
            and Dimension.MASSE not in self.faktoren
            and not self.has_error(self.feldname(Dimension.MASSE))
        ):
            self.add_error(
                self.feldname(Dimension.MASSE),
                _(
                    "Zum Wiegen braucht die Ware „%(faktor)s“: bei Liter die Dichte je Liter, "
                    "bei Stück das Gewicht eines Stücks."
                )
                % {"faktor": self.instance.faktor_beschriftung(Dimension.MASSE)},
            )
        # A recipe with shares in percent converts through this value. It
        # cannot go as long as the product stands in such a recipe.
        if (
            self.instance.pk
            and einheit is not None
            and einheit.dimension != Dimension.MASSE
            and Dimension.MASSE not in self.faktoren
            and not self.has_error(self.feldname(Dimension.MASSE))
            and (
                rezepturen := list(
                    self.instance.verwendet_in.filter(
                        rezepturartikel__anteilig=True
                    ).select_related("rezepturartikel")
                )
            )
        ):
            self.add_error(
                self.feldname(Dimension.MASSE),
                _(
                    "%(namen)s rechnet Anteile in Prozent über diesen Wert um. Er bleibt, "
                    "solange die Ware in der Rezeptur steht."
                )
                % {"namen": ", ".join(f"„{zeile.rezepturartikel.name}“" for zeile in rezepturen)},
            )
        return daten

    def _faktoren_uebernehmen(self, daten: dict) -> None:
        """Which conversions are going to be written, announced to the model.

        After a change of unit the stored ones are converted along and replace
        what is in the fields: two entries about the same thing could only
        contradict each other. Otherwise the fields count, and the one of the
        product's own dimension has to stay empty. The browser hides it, the
        check here is for everything else.
        """
        einheit = self.einheit(daten)
        if einheit is None:
            return
        if self.umrechnungsfaktor is not None and self.bisherige_einheit is not None:
            self.faktoren = umrechnungen_danach(
                self.instance, self.bisherige_einheit, einheit, self.umrechnungsfaktor
            )
        else:
            self.faktoren = {
                dimension: eingabe_zu_faktor(
                    einheit.dimension, dimension, daten[self.feldname(dimension)]
                )
                for dimension in Dimension
                if daten.get(self.feldname(dimension)) is not None
            }
            if einheit.dimension in self.faktoren:
                self.add_error(
                    self.feldname(einheit.dimension),
                    _("In %(einheit)s rechnet die Einheit selbst um. Dieses Feld bleibt leer.")
                    % {"einheit": einheit.kurz},
                )
                del self.faktoren[einheit.dimension]
        self.instance.geplante_faktoren = self.faktoren

    def save(self, commit=True):
        ware = super().save(commit=commit)
        if commit:
            ware.umrechnungen_setzen(self.faktoren)
            if art := self.cleaned_data.get("inventurart"):
                ware.inventurart_setzen(art)
        return ware

    def _einheitenwechsel_pruefen(self, daten: dict) -> None:
        """If the unit changes, it is fixed afterwards what to convert with.

        Without an entry the stored conversion of the product counts, the
        same as in 'einheit_wechseln'. The conversions of the product follow
        from it ('_faktoren_uebernehmen').
        """
        alt, neu = self.bisherige_einheit, self.einheit(daten)
        if alt is None or neu is None or alt.pk == neu.pk:
            return
        try:
            self.umrechnungsfaktor = umrechnungsfaktor(
                alt, neu, daten.get("umrechnung"), self.instance
            )
        except forms.ValidationError as fehler:
            for feld, meldungen in fehler.message_dict.items():
                for meldung in meldungen:
                    self.add_error(feld, meldung)


class WareEinstellungenForm(WareForm):
    """The product's settings on the page of a house article.

    Whoever maintains a house notices there that a product is named wrongly
    or belongs on the scale, and should not have to leave the page for it.
    What is changed here applies in every house nonetheless. The page says so.

    **Without the unit.** Changing it converts packs, recipes, the product's
    conversions and every house's factor ('ware_unit_change'). That is a
    process of its own and no side effect of a correction to a house article.
    It stays on the product's page.

    With the unit fixed the form knows itself which conversions there are and
    what they are called. On the product's page the script does that, because
    the unit can change there.
    """

    class Meta(WareForm.Meta):
        fields = ("name", "warenfamilie", "abwiegbar", "behaelter")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        del self.fields["umrechnung"]
        # The article's own type stands in its form, with the question whether
        # it applies to the product. Here it would be a second way.
        self.fields.pop("inventurart", None)
        eigene = self.instance.basiseinheit.dimension
        # Within its own dimension the unit computes itself.
        del self.fields[self.feldname(eigene)]
        for dimension in Dimension:
            if dimension != eigene:
                feld = self.fields[self.feldname(dimension)]
                feld.label = self.instance.faktor_beschriftung(dimension)

    def einheit(self, daten: dict) -> Basiseinheit | None:
        """The stored one: this form does not change it."""
        return self.instance.basiseinheit


class WareZusammenfuehrenForm(forms.Form):
    """Merges two products that mean the same.

    Arises regularly on import: if the designations differ between the
    houses, the import creates two products.

    Here stand only the obstacles that **cannot** be resolved: the same
    recipe, and "Not countable" on one side only. One more depends on who
    stays ('fehlendes_gewicht'): a share in percent of the absorbed product
    needs the weight per unit of the one that stays. The packs are no obstacle
    any more, and neither is a house that carries both since 10/2026. What
    overlaps is decided in the second step ('katalog/ware_merge.py'): which
    name a pack keeps, and per house which row stays.

    Different units are none any more since 09/2026 either: within a
    dimension the units compute themselves, across dimensions the field
    "Conversion" asks. The browser shows it only then
    ('static/js/ware_merge.js').
    """

    #: The choice which of the two stays.
    DIESER = "dieser"
    ANDERER = "anderer"

    quelle = forms.ModelChoiceField(
        queryset=Ware.objects.select_related("basiseinheit"),
        label=_("Zusammenführen mit"),
        help_text=_("Die zweite Ware, die dasselbe meint."),
    )
    # Who stays is not immaterial: from it come name, inventory type, product
    # family and everything about weighing. The other brings house articles,
    # packs and recipe rows and vanishes. Until 09/2026
    # the one from whose page one came always stayed. Whoever started from the
    # wrong one had to start over.
    bleibt = forms.ChoiceField(
        widget=forms.RadioSelect,
        # Without an entry the one from whose page the call comes stays, as before.
        required=False,
        initial=DIESER,
        label=_("Bleibt bestehen"),
        help_text=_(
            "Von der bleibenden Ware stammen Name, Inventurart, Warenfamilie und die Angaben "
            "zum Wiegen."
        ),
    )
    # With a comma, as it is typed: how many units of the remaining one a
    # unit of the absorbed one is. Asked only across dimensions.
    umrechnung = Dezimalfeld(
        required=False,
        localize=True,
        max_digits=FAKTOR_STELLEN_GESAMT,
        decimal_places=FAKTOR_NACHKOMMASTELLEN,
        min_value=FAKTOR_MINDESTENS,
        error_messages={"min_value": FAKTOR_ZU_KLEIN},
        label=_("Umrechnung"),
        help_text=_(
            "Nur, wenn die beiden in verschiedenen Dimensionen geführt werden (Stück, Gewicht, "
            "Volumen): wie viele Einheiten der bleibenden Ware ist eine Einheit der aufgelösten?"
        ),
    )

    #: After 'clean()': how many units of the target one unit of the source is.
    faktor: Decimal = Decimal("1")

    def __init__(self, *args, ziel: Ware, **kwargs):
        super().__init__(*args, **kwargs)
        #: The product from whose page the call comes, not necessarily the one that stays.
        self.ziel = ziel
        auswahl(self, "bleibt").choices = self.wer_bleibt(ziel)
        modellwahl(self, "quelle").queryset = Ware.objects.exclude(pk=ziel.pk).select_related(
            "basiseinheit"
        )
        feld = self.fields["quelle"]
        feld.widget = WarenAuswahl(attrs={"class": "form-select"}, choices=feld.widget.choices)

    def wer_bleibt(self, ziel: Ware) -> list[tuple[str, Beschriftung]]:
        """The two answers, naming the product that is dissolved."""
        return [
            (self.DIESER, _("„%(name)s“ (die gewählte Ware wird aufgelöst)") % {"name": ziel.name}),
            (
                self.ANDERER,
                _("die gewählte Ware („%(name)s“ wird aufgelöst)") % {"name": ziel.name},
            ),
        ]

    def clean_quelle(self) -> Ware:
        quelle = self.cleaned_data["quelle"]
        for meldung in self.konflikte(quelle, self.ziel):
            raise forms.ValidationError(meldung)
        return quelle

    def clean(self) -> dict:
        super().clean()
        daten = self.cleaned_data
        if daten.get("quelle") is None:
            return daten
        quelle, ziel = self.rollen()
        try:
            self.faktor = umrechnungsfaktor(
                quelle.basiseinheit, ziel.basiseinheit, daten.get("umrechnung")
            )
        except forms.ValidationError as fehler:
            for feld, meldungen in fehler.message_dict.items():
                for meldung in meldungen:
                    self.add_error(feld, meldung)
        if (meldung := self.fehlendes_gewicht(quelle, ziel)) is not None:
            self.add_error("quelle", meldung)
        return daten

    def rollen(self) -> tuple[Ware, Ware]:
        """Who is absorbed and who stays, as '(quelle, ziel)' by the choice in the form."""
        anderer = self.cleaned_data["quelle"]
        if self.cleaned_data.get("bleibt") == self.ANDERER:
            return self.ziel, anderer
        return anderer, self.ziel

    @staticmethod
    def konflikte(quelle: Ware, ziel: Ware) -> list[str]:
        """What stands in the way of a merge and cannot be resolved here.

        The same in both directions: the obstacles do not depend on who stays.
        """
        meldungen = []

        if quelle.ist_nicht_zaehlbar != ziel.ist_nicht_zaehlbar:
            meldungen.append(
                _(
                    "Nur eine der beiden gehört zu „Nicht zählbar“. Zusammengeführt wäre "
                    "eine Ware zugleich Ware und keine. Erst beide in dieselbe Richtung "
                    "bringen."
                )
            )

        ziel_rezepte = set(ziel.verwendet_in.values_list("rezepturartikel_id", flat=True))
        doppelte_rezepte = Rezepturartikel.objects.filter(
            komponenten__ware=quelle, pk__in=ziel_rezepte
        ).distinct()
        if namen := [artikel.name for artikel in doppelte_rezepte]:
            meldungen.append(
                _("Beide stecken in derselben Rezeptur: %(namen)s.") % {"namen": ", ".join(namen)}
            )

        return meldungen

    @staticmethod
    def fehlendes_gewicht(quelle: Ware, ziel: Ware) -> str | None:
        """The obstacle when the product that stays does not know what one unit weighs.

        The recipe rows of the absorbed product move to the one that stays. A
        share in percent becomes a quantity only through the weight per unit
        ('Rezepturkomponente.menge_je_einheit'), and without one the scale's
        fallback of a gram per unit would count, silently. The answer depends
        on who stays, so it is not among 'konflikte'.
        """
        if ziel.gramm_je_einheit is not None:
            return None
        rezepturen = list(
            quelle.verwendet_in.filter(rezepturartikel__anteilig=True).select_related(
                "rezepturartikel"
            )
        )
        if not rezepturen:
            return None
        return _(
            "„%(quelle)s“ steckt in der Rezeptur von %(namen)s, und die rechnet Anteile in "
            "Prozent über das Gewicht je Einheit. „%(ziel)s“ kennt keines. Trag dort erst "
            "„%(faktor)s“ ein, oder lass „%(quelle)s“ bestehen."
        ) % {
            "quelle": quelle.name,
            "ziel": ziel.name,
            "namen": ", ".join(f"„{zeile.rezepturartikel.name}“" for zeile in rezepturen),
            "faktor": ziel.faktor_beschriftung(Dimension.MASSE),
        }


class ArtikelWahl(forms.ModelChoiceField):
    """A product, offered as the article of the house that carries it."""

    def __init__(self, *args, beschriftungen: dict[int, str], **kwargs):
        super().__init__(*args, **kwargs)
        self.beschriftungen = beschriftungen

    def label_from_instance(self, obj) -> str:
        return self.beschriftungen.get(obj.pk, str(obj))


class HausartikelZusammenfuehrenForm(WareZusammenfuehrenForm):
    """The same merge, started from a house article: the other one is an article of the house.

    Whoever maintains a house knows the duplicate by its number and name there,
    not by the product behind it. Merged are the products, as on the product's
    page, and with them the rows of every house.
    """

    def __init__(self, *args, ziel: Ware, hausartikel: Hausartikel, **kwargs):
        super().__init__(*args, ziel=ziel, **kwargs)
        beschriftungen: dict[int, str] = {}
        andere = (
            Hausartikel.objects.filter(haus_id=hausartikel.haus_id, status__in=SICHTBAR)
            .exclude(ware_id=ziel.pk)
            .order_by("artikelnummer", "name")
        )
        # Carried rows first: a product the house carries is meant by its carried row.
        for artikel in sorted(andere, key=lambda a: a.status == Artikelstatus.EINGESTELLT):
            beschriftungen.setdefault(artikel.ware_id, self.beschriftung(artikel))
        # In the order of the articles, as the house knows them, not by product name.
        reihenfolge = Case(
            *(When(pk=pk, then=Value(stelle)) for stelle, pk in enumerate(beschriftungen)),
            default=Value(len(beschriftungen)),
        )
        self.fields["quelle"] = ArtikelWahl(
            queryset=Ware.objects.filter(pk__in=beschriftungen)
            .select_related("basiseinheit")
            .order_by(reihenfolge),
            beschriftungen=beschriftungen,
            widget=WarenAuswahl(attrs={"class": "form-select"}),
            label=_("Zusammenführen mit"),
            help_text=_(
                "Ein anderer Artikel dieses Hauses, der dasselbe meint. Zusammengeführt werden "
                "ihre Waren, und das gilt in jedem Haus."
            ),
        )

    def wer_bleibt(self, ziel: Ware) -> list[tuple[str, Beschriftung]]:
        """Chosen here is an article, and what goes is the product behind it."""
        return [
            (
                self.DIESER,
                _("„%(name)s“ (die Ware des gewählten Artikels wird aufgelöst)")
                % {"name": ziel.name},
            ),
            (
                self.ANDERER,
                _("die Ware des gewählten Artikels („%(name)s“ wird aufgelöst)")
                % {"name": ziel.name},
            ),
        ]

    @staticmethod
    def beschriftung(artikel: Hausartikel) -> str:
        nummer = artikel.artikelnummer or "–"
        if artikel.status == Artikelstatus.AKTIV:
            return f"{nummer} {artikel.name}"
        return f"{nummer} {artikel.name} ({artikel.get_status_display().lower()})"


class WareAufteilenForm(AufteilenForm):
    """One product becomes two: the previous one stays, next to it a new one arises.

    Distributed are the **house articles**. They are the only thing about a
    product that belongs to a house, and the reason for splitting is always a
    house that carries something different from the others. Everything else is
    updated by 'katalog/ware_split.py'.
    """

    satz: Ware
    name = forms.CharField(max_length=200, label=_("Name der bisherigen Ware"))
    neuer_name = forms.CharField(max_length=200, label=_("Name der neuen Ware"))
    wandern = forms.ModelMultipleChoiceField(
        queryset=Hausartikel.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label=_("Zur neuen Ware"),
    )
    gleiche_namen = _("Die beiden Waren brauchen verschiedene Namen.")

    def zur_wahl(self):
        return self.satz.hausartikel.select_related("haus", "warengruppe").order_by(
            "haus__name", "artikelnummer"
        )

    def namensvettern(self):
        return Ware.objects.exclude(pk=self.satz.pk)

    def vergeben(self, name: str) -> str:
        return _("Eine Ware „%(name)s“ gibt es schon.") % {"name": name}


class DublettenForm(forms.Form):
    """The second step for houses that carry both products: which expectations go over.

    Who stays needs no question: the row of the product that stays. Which
    product that is was chosen at the top. The row of the absorbed one is
    discontinued and gets a tick per expectation.
    """

    def __init__(self, *args, dubletten: list[Dublette], **kwargs):
        super().__init__(*args, **kwargs)
        self.dubletten = dubletten
        for dublette in dubletten:
            for erwartung in dublette.quelle.erwartet_an.all():
                self.fields[f"erwartung-{erwartung.pk}"] = forms.BooleanField(
                    required=False, initial=True, label=self.ortsangabe(erwartung)
                )

    @staticmethod
    def ortsangabe(erwartung: Erwartung) -> str:
        # 'str()': "Einzeln" is a lazy translation, and 'join' takes only text.
        gebinde = ", ".join(
            str(vorgabe.gebinde.anzeigename) for vorgabe in erwartung.gebinde_vorgaben.all()
        )
        ort = f"{erwartung.ort.bereich.name} · {erwartung.ort.name}"
        return f"{ort} ({gebinde})" if gebinde else ort

    def haeuser(self) -> list[dict]:
        """Per house both rows and the ticks for the expectations, for the template."""
        return [
            {
                "dublette": dublette,
                "erwartungen": [
                    self[f"erwartung-{erwartung.pk}"]
                    for erwartung in dublette.quelle.erwartet_an.all()
                ],
            }
            for dublette in self.dubletten
        ]

    def uebernehmen(self) -> dict[str, frozenset[int]]:
        return {
            dublette.kennung: frozenset(
                erwartung.pk
                for erwartung in dublette.quelle.erwartet_an.all()
                if self.cleaned_data.get(f"erwartung-{erwartung.pk}")
            )
            for dublette in self.dubletten
        }


class GebindeAufloesenForm(forms.Form):
    """The second step: what should happen with the contested packs.

    The fields only arise at runtime, one per contested pair. Which those are
    is known by 'ware_merge.gebindepaare'. Two kinds:

    - **same quantity, different name**: a choice which name stays. The two
      merge in any case, it is the same quantity.
    - **same name, different quantity**: a text field for the new name. They
      must **not** merge, otherwise what was counted would shift.

    And for an active pack that would merge into an inactive one of the
    target ('Gebindepaar.landet_inaktiv'): bring it back or leave it inactive,
    as the house article form asks. The answer names the pack it was given
    for. A page gone stale cannot switch on another one, the row asks again.
    """

    def __init__(self, *args, paare: list, ziel_gebinde: list[Gebinde], **kwargs):
        super().__init__(*args, **kwargs)
        self.paare = paare
        #: Every pack of the remaining product, the unpaired ones too.
        self.ziel_gebinde = ziel_gebinde
        for paar in paare:
            if paar.landet_inaktiv:
                self.fields[f"aktiv-{paar.kennung}"] = self._inaktiv_frage(paar)
            if paar.art == NAMENSWAHL:
                self.fields[f"name-{paar.kennung}"] = forms.ChoiceField(
                    choices=(
                        ("ziel", _("„%(name)s“ behalten") % {"name": paar.ziel.name}),
                        (
                            "quelle",
                            _("in „%(name)s“ umbenennen") % {"name": paar.quelle.name},
                        ),
                    ),
                    initial="ziel",
                    label=_("%(menge)s: welcher Name?") % {"menge": menge_kurz(paar.menge)},
                    widget=forms.RadioSelect,
                )
            elif paar.art == UMBENENNEN:
                self.fields[f"neu-{paar.kennung}"] = forms.CharField(
                    max_length=100,
                    initial=paar.vorschlag,
                    label=_("Neuer Name für „%(name)s“") % {"name": paar.quelle.name},
                    help_text=hilfetext(
                        _(
                            "„%(name)s“ steht dort schon für %(menge)s. Die beiden brauchen "
                            "unterscheidbare Namen."
                        ),
                        name=paar.ziel.name,
                        menge=menge_kurz(paar.ziel.faktor_in_basiseinheit),
                    ),
                    widget=forms.TextInput(attrs={"class": "form-control"}),
                )

    @staticmethod
    def _inaktiv_frage(paar) -> forms.ChoiceField:
        ziel = paar.ziel
        ware = ziel.ware
        saetze = (
            _("„%(gebinde)s“ gibt es bei „%(ware)s“ schon, aber inaktiv (%(menge)s %(einheit)s).")
            % {
                "gebinde": ziel.anzeigename,
                "ware": ware.name,
                "menge": menge_kurz(ziel.faktor_in_basiseinheit),
                "einheit": ware.basiseinheit.kurz,
            },
            _("Inaktive Gebinde bekommen in neuen Inventuren keine Zeile."),
            _("Bleibt es inaktiv, ruhen dort auch die Vorgaben von „%(quelle)s“.")
            % {"quelle": paar.quelle.ware.name},
            _("Wieder aktivieren gilt in allen Häusern, denn das Gebinde gehört der Ware."),
        )
        return forms.ChoiceField(
            label=" ".join(str(satz) for satz in saetze),
            choices=[(f"{wahl}:{ziel.pk}", text) for wahl, text in ANTWORTEN],
            widget=forms.RadioSelect,
            error_messages={
                "required": _("Bitte wählen: wieder aktivieren oder inaktiv lassen."),
                "invalid_choice": _("Das Gebinde hat sich inzwischen geändert. Bitte neu wählen."),
            },
        )

    def clean(self) -> dict:
        """Every new name has to be free in the whole target, not only at its partner.

        Taken is the name of every pack of the target and of every pack that
        moves over without a partner. 'ware_merge.zusammenfuehren' writes the
        packs one by one, so a name another pack gives up on the way is not
        free yet either. Until 10/2026 only the partner counted, and a name
        another pack carried ended as an error page at the unique index.
        """
        super().clean()
        daten = self.cleaned_data
        vergeben = {gebinde.name.casefold() for gebinde in self.ziel_gebinde}
        vergeben |= {paar.quelle.name.casefold() for paar in self.paare if paar.ziel is None}
        for paar in self.paare:
            if paar.art == NAMENSWAHL and daten.get(f"name-{paar.kennung}") == "quelle":
                # The target pack takes over the name of the source.
                feld, name = f"name-{paar.kennung}", paar.quelle.name
            elif paar.art == UMBENENNEN:
                feld, name = f"neu-{paar.kennung}", (daten.get(f"neu-{paar.kennung}") or "").strip()
            else:
                continue
            if not name:
                continue
            if name.casefold() in vergeben:
                self.add_error(feld, _("Diesen Namen trägt hier schon ein anderes Gebinde."))
            vergeben.add(name.casefold())
        return daten

    def namen(self) -> dict[str, str]:
        return {
            paar.kennung: self.cleaned_data[f"neu-{paar.kennung}"].strip()
            for paar in self.paare
            if paar.art == UMBENENNEN
        }

    def entscheidungen(self) -> dict[str, str]:
        return {
            paar.kennung: self.cleaned_data[f"name-{paar.kennung}"]
            for paar in self.paare
            if paar.art == NAMENSWAHL
        }

    def aktivieren(self) -> frozenset[int]:
        """The inactive packs of the target the answers bring back."""
        return frozenset(
            paar.ziel.pk
            for paar in self.paare
            if paar.landet_inaktiv
            and self.cleaned_data[f"aktiv-{paar.kennung}"].partition(":")[0] == WIEDER_AKTIVIEREN
        )


class GebindeForm(forms.ModelForm):
    """No 'aktiv': a pack created here is used.

    It can be set inactive on the product's detail page. There it stands next
    to the order, where one looks for it when counting.
    """

    class Meta:
        model = Gebinde
        fields = ("name", "ist_einzeln", "faktor_in_basiseinheit")
        field_classes = {"faktor_in_basiseinheit": Dezimalfeld}


class BaseGebindeFormSet(MitKlartextfehler):
    """Watches over the quantities that arise in one go.

    Two new rows with the same quantity are caught by Django's uniqueness
    check via the constraint, though with "Please correct the duplicate data
    for faktor_in_basiseinheit". Here stands the plain text.
    """

    def get_unique_error_message(self, unique_check) -> Beschriftung:
        if self._ohne_fremdschluessel(unique_check) == ("faktor_in_basiseinheit",):
            return _(
                "Zwei Zeilen stehen für dieselbe Menge. Beim Zählen ließen sie sich "
                "nicht unterscheiden."
            )
        if self._ohne_fremdschluessel(unique_check) == ("name",):
            return _("Jeder Gebindename darf bei einer Ware nur einmal vorkommen.")
        return super().get_unique_error_message(unique_check)

    def clean(self) -> None:
        """A pack count positions hang on is not deleted, and the page says what holds it.

        They belong to an inventory that is not closed yet and point to the
        pack via PROTECT. Until 10/2026 ticking such a pack ended in a server
        error. The product's articles are locked by the time this runs
        ('WareUpdateView.form_valid'), so a row the app created a moment ago
        is seen here, and one it creates afterwards finds the pack gone.
        """
        super().clean()
        meldungen = []
        for form in self.deleted_forms:
            gebinde = form.instance
            if gebinde.pk is None:
                continue
            inventuren = sorted(
                {
                    str(position.inventur)
                    for position in gebinde.zaehlpositionen.select_related("inventur__haus")
                }
            )
            if inventuren:
                meldungen.append(
                    ngettext(
                        "An „%(name)s“ hängen noch Zählpositionen von %(inventuren)s. Löschen "
                        "geht erst, wenn diese Inventur abgeschlossen oder gelöscht ist. Bis "
                        "dahin lässt sich das Gebinde auf der Seite der Ware auf inaktiv setzen.",
                        "An „%(name)s“ hängen noch Zählpositionen von %(inventuren)s. Löschen "
                        "geht erst, wenn diese Inventuren abgeschlossen oder gelöscht sind. Bis "
                        "dahin lässt sich das Gebinde auf der Seite der Ware auf inaktiv setzen.",
                        len(inventuren),
                    )
                    % {"name": gebinde.name, "inventuren": ", ".join(inventuren)}
                )
        if meldungen:
            raise forms.ValidationError(meldungen)


GebindeFormSet = cast(
    "type[BaseGebindeFormSet]",
    forms.inlineformset_factory(
        Ware,
        Gebinde,
        form=GebindeForm,
        formset=BaseGebindeFormSet,
        fk_name="ware",
        extra=1,
        can_delete=True,
    ),
)
