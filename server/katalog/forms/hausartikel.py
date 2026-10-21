"""The house article of a house, including new packs and locations that arise on creation."""

from collections import defaultdict
from collections.abc import Callable
from decimal import Decimal
from typing import cast

from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Count
from django.utils.translation import gettext_lazy as _

from django_project.form_fields import Dezimalfeld, auswahl, modellwahl
from django_project.validation import ganzzahl
from organisation.models import Haus, Ort

from ..models import (
    FAKTOR_ZU_KLEIN,
    GANZE_WARE,
    GEFUEHRT,
    GELTUNG_FEHLT,
    NUR_DIESER,
    Artikelstatus,
    Basiseinheit,
    Gebinde,
    Hausartikel,
    Inventurart,
    Ware,
    Warengruppe,
    menge_kurz,
    neue_art_ans_ende,
)
from .base import EinheitenAuswahl, WarenAuswahl, an_stelle, hilfetext


class HausartikelForm(forms.ModelForm):
    """What a single house carries.

    The house comes from the URL and is not in the form, because a house
    article is always created from the house detail page.

    The **source** says which system carries the article, and thus what the
    number has to be. Preselected is what the house already carries: a house
    without an inventory management system gets "eigen" suggested, a cinema
    its system. Without an entry the preselection applies, so a call past the
    form does not fail on it.

    The product is either chosen or created right here: for the first house
    that carries a new product it does not exist yet, and a detour through a
    second form would be due on every addition.

    The unit is that of the **house**, as it carries the article. A new
    product arises in the same unit. For an existing product in another
    dimension the conversion is part of it. The browser only shows the field
    then ('static/js/house_unit.js'), the check happens here.
    """

    ware = forms.ModelChoiceField(
        queryset=Ware.objects.select_related("basiseinheit"),
        required=False,
        empty_label=_("-- neue Ware anlegen --"),
        label=_("Vorhandene Ware"),
        help_text=_("Dieselbe Ware über alle Häuser hinweg. An ihr hängen Einheit und Gebinde."),
        widget=WarenAuswahl,
    )
    neuer_ware = forms.CharField(
        max_length=200,
        required=False,
        label=_("Bezeichnung"),
        help_text=_(
            "Bleibt das Feld leer, gilt die Bezeichnung des Hausartikels. Die neue Ware wird in "
            "der Einheit dieses Hauses geführt."
        ),
    )
    warengruppe = forms.ModelChoiceField(
        queryset=Warengruppe.objects.all(),
        required=False,
        empty_label=_("-- neue Warengruppe anlegen --"),
        label=_("Vorhandene Warengruppe"),
        help_text=_("Die Kategorie der Warenwirtschaft, sie bleibt hausbezogen."),
    )
    neue_warengruppe = forms.CharField(
        max_length=100,
        required=False,
        label=_("Bezeichnung"),
        help_text=_("Genau wie in der Warenwirtschaft, denn daran erkennt der Import sie wieder."),
    )

    class Meta:
        model = Hausartikel
        # No 'status': what arises here is carried. Setting inactive and
        # discontinuing happens in the row or on the detail page. That is a
        # process of its own with its own consequences (number becomes free)
        # and no side effect of a correction to the designation. Only "Not
        # countable" as product acts on the status: ignored is what hangs there.
        fields = (
            "quelle",
            "artikelnummer",
            "name",
            "ean",
            "basiseinheit",
            "faktor_zur_ware",
            "inventurart",
        )
        field_classes = {"faktor_zur_ware": Dezimalfeld}
        # The model's lower limit has twelve places, and Django would name it as "1E-12".
        error_messages = {"faktor_zur_ware": {"min_value": FAKTOR_ZU_KLEIN}}
        widgets = {
            "basiseinheit": EinheitenAuswahl,
            # A text field, as for every other factor. A number field drops a
            # value written with a comma, and a German account would find the
            # stored factor missing when editing.
            "faktor_zur_ware": forms.TextInput(attrs={"inputmode": "decimal"}),
        }

    #: The answer to the question whether a new type applies to the product.
    NUR_DIESER = NUR_DIESER
    GANZE_WARE = GANZE_WARE

    geltung = forms.ChoiceField(
        choices=(),
        required=False,
        widget=forms.RadioSelect,
        label=_("Die neue Inventurart gilt für"),
    )

    def __init__(self, *args, haus: Haus | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.haus = haus or getattr(self.instance, "haus", None)
        modellwahl(self, "basiseinheit").queryset = Basiseinheit.fuer_artikel()
        # Empty means: like the product. Only a new product needs the entry.
        self.fields["basiseinheit"].required = False
        self.fields["faktor_zur_ware"].localize = True
        self.fields["faktor_zur_ware"].widget.is_localized = True
        self.fields["quelle"].required = False
        if self.instance.pk:
            self.fields["ware"].initial = self.instance.ware_id
            self.fields["warengruppe"].initial = self.instance.warengruppe_id
        elif self.haus is not None:
            self.fields["quelle"].initial = self._uebliche_quelle(self.haus)
        self._inventurart_vorbereiten()

    def _inventurart_vorbereiten(self) -> None:
        """Choosing the type, and the question whether a change applies to the product.

        When creating, an empty choice is the rule ('Inventurart.vorschlag_fuer'):
        the new house counts the product in the tab the others count it in.
        The product is only fixed when submitting. When editing, the question
        is only asked if the product has further articles.
        """
        feld = modellwahl(self, "inventurart")
        feld.queryset = Inventurart.objects.all()
        if not self.instance.pk:
            feld.required = False
            feld.empty_label = _("wie die übrigen Artikel der Ware")
            feld.help_text = hilfetext(
                _(
                    "Ohne Wahl gilt die Art, in der die Ware schon gezählt wird. Eine neue Ware "
                    "kommt in „%(erste)s“. Eine Wahl gilt nur für diesen Artikel."
                ),
                erste=Inventurart.erste().name,
            )
            del self.fields["geltung"]
            return
        feld.empty_label = None
        andere = self.weitere_artikel()
        if not andere:
            del self.fields["geltung"]
            return
        auswahl(self, "geltung").choices = [
            (self.NUR_DIESER, _("nur diesen Artikel")),
            (
                self.GANZE_WARE,
                _("alle Artikel der Ware, auch in %(haeuser)s")
                % {"haeuser": ", ".join(sorted({artikel.haus.name for artikel in andere}))},
            ),
        ]

    def weitere_artikel(self) -> list[Hausartikel]:
        """The product's other carried articles, in every house: what the question is about."""
        if not self.instance.pk:
            return []
        return list(self.instance.weitere_gefuehrte().select_related("haus"))

    def _uebliche_quelle(self, haus: Haus) -> str:
        """The source the house carries most often. Without articles the model's default."""
        haeufigste = (
            Hausartikel.objects.filter(haus=haus)
            .values("quelle")
            .annotate(anzahl=Count("pk"))
            .order_by("-anzahl", "quelle")
            .values_list("quelle", flat=True)
            .first()
        )
        return haeufigste or self.instance.quelle

    def clean(self):
        super().clean()
        daten = self.cleaned_data
        self._quelle_pruefen(daten)
        self._ware_pruefen(daten)
        self._umrechnung_pruefen(daten)
        self._warengruppe_pruefen(daten)
        self._teilindizes_pruefen(daten)
        self._eindeutigkeit_pruefen(daten)
        self._geltung_pruefen(daten)
        return daten

    def _geltung_pruefen(self, daten: dict) -> None:
        """A new type needs the answer for whom it applies, once the product has more articles.

        A default would decide silently: "only this one" splits the product
        across the tabs, "all" changes other houses unasked.
        """
        if "geltung" not in self.fields or daten.get("geltung"):
            return
        art = daten.get("inventurart")
        if art is not None and art.pk != self.instance.inventurart_id:
            self.add_error("geltung", GELTUNG_FEHLT)

    @property
    def fuer_ganze_ware(self) -> bool:
        return self.cleaned_data.get("geltung") == self.GANZE_WARE

    def _quelle_pruefen(self, daten: dict) -> None:
        """Without an entry the preselection. The model then checks the number by its source.

        The model check runs in 'ModelForm._post_clean' and sees 'quelle' only
        if it is in 'cleaned_data'. An empty field would otherwise mean "empty
        source" instead of "the usual one".
        """
        if not daten.get("quelle"):
            daten["quelle"] = self.fields["quelle"].initial or self.instance.quelle

    def _umrechnung_pruefen(self, daten: dict) -> None:
        """The same rule as 'Hausartikel.pruefe_einheit', only with the chosen product.

        The model would see the product only in 'save()'. Within a dimension an
        entry silently drops away: the field is hidden then, and without
        JavaScript a remainder might stand there that means nothing.
        """
        ware = daten.get("ware")
        einheit = daten.get("basiseinheit")
        if einheit is None:
            if ware is None:
                if not self.has_error("basiseinheit"):
                    self.add_error(
                        "basiseinheit",
                        _("Für eine neue Ware wird die Einheit gebraucht."),
                    )
                daten["faktor_zur_ware"] = None
                return
            einheit = daten["basiseinheit"] = ware.basiseinheit
        if ware is None:
            daten["faktor_zur_ware"] = None
            return
        # The model checks the same rule once more and should see the chosen
        # product in doing so, not the previous one.
        self.instance.ware = ware
        if ware.basiseinheit.dimension == einheit.dimension:
            daten["faktor_zur_ware"] = None
        elif daten.get("faktor_zur_ware") is None and not self.has_error("faktor_zur_ware"):
            self.add_error(
                "faktor_zur_ware",
                _(
                    "%(haus)s zählt in %(einheit)s, „%(ware)s“ wird in %(wareneinheit)s "
                    "geführt. Wie viele %(wareneinheit)s eine Einheit in %(einheit)s ist, "
                    "weiß nur, wer die Ware kennt."
                )
                % {
                    "haus": self.haus,
                    "einheit": einheit.kurz,
                    "ware": ware.name,
                    "wareneinheit": ware.basiseinheit.kurz,
                },
            )

    def _eindeutigkeit_pruefen(self, daten: dict) -> None:
        """House, number and designation together exist only once, also when creating.

        When editing the model checks itself ('Hausartikel.pruefe_eindeutig'). But
        a new row gets its house only in 'save()', and without a house the model
        check has nothing to compare, hence separately here.
        """
        if self.instance.pk or self.haus is None:
            return
        probe = Hausartikel(
            haus=self.haus,
            artikelnummer=daten.get("artikelnummer") or "",
            name=daten.get("name") or "",
        )
        try:
            probe.pruefe_eindeutig()
        except ValidationError as fehler:
            self.add_error("name", fehler.messages)

    def _warengruppe_pruefen(self, daten: dict) -> None:
        """As with the product: either choose or describe anew.

        Product groups otherwise only arise on import. For a house that carries a
        product first, the fitting group therefore often does not exist yet.
        """
        name = (daten.get("neue_warengruppe") or "").strip()
        if daten.get("warengruppe"):
            if name:
                self.add_error(
                    "warengruppe",
                    _(
                        "Entweder eine vorhandene Warengruppe wählen oder eine neue "
                        "beschreiben, nicht beides."
                    ),
                )
        elif not name:
            self.add_error("neue_warengruppe", _("Die neue Warengruppe braucht eine Bezeichnung."))
        elif Warengruppe.objects.filter(name=name).exists():
            self.add_error(
                "neue_warengruppe",
                _("„%(name)s“ gibt es bereits. Diese Warengruppe oben auswählen.") % {"name": name},
            )

    def _ware_pruefen(self, daten: dict) -> None:
        """Either choose an existing one or describe a new one."""
        if daten.get("ware"):
            if daten.get("neuer_ware"):
                self.add_error(
                    "ware",
                    _(
                        "Entweder eine vorhandene Ware wählen oder eine neue "
                        "beschreiben, nicht beides."
                    ),
                )
            return

        name = self.neuer_ware_name(daten)
        if not name:
            self.add_error("neuer_ware", _("Die neue Ware braucht eine Bezeichnung."))
        elif Ware.objects.filter(name=name).exists():
            self.add_error(
                "neuer_ware",
                _("„%(name)s“ gibt es bereits. Diese Ware oben auswählen.") % {"name": name},
            )

    def status_danach(self, daten: dict) -> str:
        """The status the article carries after saving (see '_status_angleichen')."""
        gewaehlt = daten.get("ware")
        if gewaehlt is not None and gewaehlt.ist_nicht_zaehlbar:
            return Artikelstatus.IGNORIERT
        if self.instance.status == Artikelstatus.IGNORIERT:
            return Artikelstatus.AKTIV
        return self.instance.status

    def _teilindizes_pruefen(self, daten: dict) -> None:
        """Mirrors the two partial indexes as form errors instead of an IntegrityError.

        They apply only to carried articles, active and inactive. A discontinued
        one may be edited further here without colliding with its successor. And
        an ignored one that is assigned to a real product becomes active again
        and therefore has to find the slot free.
        """
        status = self.status_danach(daten)
        if status == Artikelstatus.IGNORIERT and self.instance.pk:
            try:
                self.instance.pruefe_ignorierbar()
            except ValidationError as fehler:
                self.add_error("ware", fehler.messages)
        if self.haus is None or status not in GEFUEHRT:
            return

        andere = Hausartikel.objects.filter(haus=self.haus, status__in=GEFUEHRT).exclude(
            pk=self.instance.pk
        )
        if (nummer := daten.get("artikelnummer")) and andere.filter(artikelnummer=nummer).exists():
            self.add_error(
                "artikelnummer",
                _("In %(haus)s ist die Nummer %(nummer)s bereits vergeben.")
                % {"haus": self.haus, "nummer": nummer},
            )
        if (ware := daten.get("ware")) and andere.filter(ware=ware).exists():
            self.add_error(
                "ware",
                _("%(haus)s führt „%(name)s“ bereits.") % {"haus": self.haus, "name": ware.name},
            )

    @staticmethod
    def neuer_ware_name(daten: dict) -> str:
        return (daten.get("neuer_ware") or daten.get("name") or "").strip()

    def save(self, commit=True):
        if self.haus is not None:
            self.instance.haus = self.haus
        self.instance.ware = self.cleaned_data.get("ware") or self._ware_anlegen()
        self.instance.warengruppe = self.cleaned_data.get("warengruppe") or (
            Warengruppe.objects.create(name=self.cleaned_data["neue_warengruppe"].strip())
        )
        # Around the save too: the article's own type is written there already,
        # and the counting lists should see it change.
        with neue_art_ans_ende():
            artikel = super().save(commit)
            if commit and self.instance.pk:
                # Also without the question: then the product's discontinued rows follow along.
                artikel.inventurart_setzen(artikel.inventurart, ganze_ware=self.fuer_ganze_ware)
        return artikel

    def _ware_anlegen(self) -> Ware:
        return Ware.objects.create(
            name=self.neuer_ware_name(self.cleaned_data),
            basiseinheit=self.cleaned_data["basiseinheit"],
        )


#: The two answers to an inactive pack a typed row lands on ('NeuesGebindeForm').
WIEDER_AKTIVIEREN = "aktivieren"
INAKTIV_LASSEN = "inaktiv"
ANTWORTEN = (
    (WIEDER_AKTIVIEREN, _("Wieder aktivieren")),
    (INAKTIV_LASSEN, _("Inaktiv lassen")),
)


class NeuesGebindeForm(forms.Form):
    """A fixed pack that arises together with the article.

    No ModelForm: the pack hangs on the product, which may not even exist yet
    when filling in.

    A row whose name or quantity the product already carries in an
    **inactive** pack asks first (decided 2026-10-19): bring that pack back or
    leave it inactive. Until it is answered nothing is saved. Until 10/2026 the
    row silently landed on the inactive pack, and a location that named it got
    no row in new inventories. Each answer names the pack it was given for
    ("aktivieren:<pk>"). A page that has gone stale meanwhile can therefore not
    switch on another pack: its answer is no longer among the choices, and the
    row asks again. A pack that has become active meanwhile asks nothing.
    """

    #: The field with the answer. It exists only in a row that needs it.
    ENTSCHEIDUNG = "entscheidung"

    name = forms.CharField(
        max_length=100,
        required=False,
        label=_("Gebinde"),
        widget=forms.TextInput(attrs={"placeholder": _("Kasten, Karton, Stück(e) …")}),
    )
    faktor_in_basiseinheit = Dezimalfeld(
        max_digits=10,
        decimal_places=3,
        min_value=Decimal("0.001"),
        required=False,
        label=_("Menge je Gebinde"),
    )

    def __init__(self, *args, ware: Ware | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        #: The inactive pack of the product the typed row lands on. The page
        #: highlights the row and asks.
        self.inaktives_gebinde: Gebinde | None = None
        if self.is_bound and ware is not None:
            self._nach_inaktivem_fragen(ware)

    @property
    def auffaellig(self) -> bool:
        """The row stands out on the page ('components/formset_rows.html')."""
        return self.inaktives_gebinde is not None

    def _getippt(self) -> tuple[str, Decimal | None]:
        """Name and quantity as typed, read before the form is cleaned.

        The question has to be a field before the cleaning, or its answer
        would not be read. What cannot be read counts as missing here, and the
        cleaning that follows reports it.
        """
        name = (self["name"].data or "").strip()
        feld = self.fields["faktor_in_basiseinheit"]
        try:
            faktor = feld.clean(self["faktor_in_basiseinheit"].data)
        except ValidationError:
            faktor = None
        return name, faktor

    def _nach_inaktivem_fragen(self, ware: Ware) -> None:
        name, faktor = self._getippt()
        if not name or faktor is None:
            return
        gebinde = Gebinde.vorhandenes(ware, name, faktor)
        if gebinde is None or gebinde.aktiv:
            return
        self.inaktives_gebinde = gebinde
        self.fields[self.ENTSCHEIDUNG] = forms.ChoiceField(
            label=self._erklaerung(ware, name, gebinde),
            choices=[(f"{wahl}:{gebinde.pk}", text) for wahl, text in ANTWORTEN],
            widget=forms.RadioSelect,
            error_messages={
                "required": _("Bitte wählen: wieder aktivieren oder inaktiv lassen."),
                "invalid_choice": _("Das Gebinde hat sich inzwischen geändert. Bitte neu wählen."),
            },
        )

    @staticmethod
    def _erklaerung(ware: Ware, name: str, gebinde: Gebinde) -> str:
        """Why the row asks. A hit by quantity says under which name the quantity is taken."""
        werte = {
            "gebinde": gebinde.anzeigename,
            "ware": ware.name,
            "name": name,
            "menge": menge_kurz(gebinde.faktor_in_basiseinheit),
            "einheit": ware.basiseinheit.kurz,
        }
        if gebinde.name == name:
            text = _(
                "„%(gebinde)s“ gibt es bei „%(ware)s“ schon, aber inaktiv (%(menge)s %(einheit)s)."
            )
        else:
            text = _(
                "Die Menge %(menge)s %(einheit)s gehört bei „%(ware)s“ zum inaktiven Gebinde "
                "„%(gebinde)s“. „%(name)s“ wird so oder so nicht angelegt. Wieder aktivieren holt "
                "„%(gebinde)s“ zurück."
            )
        saetze = (
            text % werte,
            _("Inaktive Gebinde bekommen in neuen Inventuren keine Zeile."),
            # The pack belongs to the product: switching it on acts in every
            # house that carries the product, not only in this one.
            _("Wieder aktivieren gilt in allen Häusern, denn das Gebinde gehört der Ware."),
        )
        return " ".join(str(satz) for satz in saetze)

    def clean(self) -> dict:
        super().clean()
        daten = self.cleaned_data
        daten["name"] = name = (daten.get("name") or "").strip()
        faktor = daten.get("faktor_in_basiseinheit")
        if name and faktor is None and not self.has_error("faktor_in_basiseinheit"):
            self.add_error(
                "faktor_in_basiseinheit", _("Ohne Menge je Gebinde lässt sich nicht umrechnen.")
            )
        if faktor is not None and not name:
            self.add_error("name", _("Das Gebinde braucht einen Namen."))
        return daten


class BaseNeueGebindeFormSet(forms.BaseFormSet):
    """The typed pack rows, and what their rows answered about inactive packs.

    'ware' is the product whose packs a row can hit. Without it (a new product,
    or one in "Not countable") nothing is asked.
    """

    def __init__(self, *args, ware: Ware | None = None, **kwargs):
        self.ware = ware
        super().__init__(*args, **kwargs)

    def get_form_kwargs(self, index):
        return super().get_form_kwargs(index) | {"ware": self.ware}

    def _gefragte(self) -> list[NeuesGebindeForm]:
        return [form for form in self.forms if form.inaktives_gebinde is not None]

    @property
    def fragen_offen(self) -> bool:
        """A row still waits for its answer, or for a new one after the page went stale."""
        return any(form.has_error(NeuesGebindeForm.ENTSCHEIDUNG) for form in self._gefragte())

    def _antworten(self) -> list[tuple[Gebinde, str]]:
        """The answers that hold, each with its pack. The choice is bound to it."""
        antworten = []
        for form in self._gefragte():
            wert = getattr(form, "cleaned_data", {}).get(NeuesGebindeForm.ENTSCHEIDUNG)
            if wert and (gebinde := form.inaktives_gebinde) is not None:
                antworten.append((gebinde, wert.partition(":")[0]))
        return antworten

    @property
    def entscheidungen(self) -> dict[int, str]:
        """Per inactive pack the answer of its row: 'WIEDER_AKTIVIEREN' or 'INAKTIV_LASSEN'."""
        return {gebinde.pk: wahl for gebinde, wahl in self._antworten()}

    @property
    def inaktive_namen(self) -> list[str]:
        """The typed names that land on an inactive pack. The location selection marks them."""
        return self._namen(lambda gebinde: True)

    @property
    def inaktiv_bleibende_namen(self) -> list[str]:
        """Those of them whose row answered "leave inactive": a location gets no row from them."""
        antworten = self.entscheidungen
        return self._namen(lambda gebinde: antworten.get(gebinde.pk) == INAKTIV_LASSEN)

    def _namen(self, passt: Callable[[Gebinde], bool]) -> list[str]:
        namen = []
        for form in self._gefragte():
            name = getattr(form, "cleaned_data", {}).get("name")
            if name and (gebinde := form.inaktives_gebinde) is not None and passt(gebinde):
                namen.append(name)
        return namen

    def clean(self) -> None:
        super().clean()
        namen = [name for name, _ in self.gebinde]
        if len(namen) != len(set(namen)):
            raise forms.ValidationError(_("Jeder Gebindename darf nur einmal vorkommen."))

        mengen = [menge for _, menge in self.gebinde]
        if len(mengen) != len(set(mengen)):
            raise forms.ValidationError(
                _(
                    "Jede Menge je Gebinde darf nur einmal vorkommen. Zwei Namen für "
                    "dieselbe Menge lassen sich beim Zählen nicht unterscheiden."
                )
            )
        self._antworten_pruefen()

    def _antworten_pruefen(self) -> None:
        """Two rows can lead to the same inactive pack, one by name and one by quantity.

        Then they have to answer alike: the pack is switched on or left once.
        """
        gewaehlt: dict[int, set[str]] = defaultdict(set)
        namen = {}
        for gebinde, wahl in self._antworten():
            gewaehlt[gebinde.pk].add(wahl)
            namen[gebinde.pk] = gebinde.anzeigename
        for pk, antworten in gewaehlt.items():
            if len(antworten) > 1:
                raise forms.ValidationError(
                    _(
                        "Zwei Zeilen führen zum inaktiven Gebinde „%(gebinde)s“. Beide gleich "
                        "wählen."
                    )
                    % {"gebinde": namen[pk]}
                )

    @property
    def gebinde(self) -> list[tuple[str, Decimal]]:
        """The filled-in rows as (name, factor)."""
        zeilen = []
        for form in self.forms:
            daten = getattr(form, "cleaned_data", {})
            if daten.get("name") and daten.get("faktor_in_basiseinheit") is not None:
                zeilen.append((daten["name"], daten["faktor_in_basiseinheit"]))
        return zeilen


NeueGebindeFormSet = cast(
    "type[BaseNeueGebindeFormSet]",
    forms.formset_factory(NeuesGebindeForm, formset=BaseNeueGebindeFormSet, extra=1),
)


class OrtErwartungForm(forms.Form):
    """A row of the location selection: is the article expected here, and in which packs.

    The packs are available by name, not by primary key: the newly created
    ones do not exist yet when submitting.
    """

    ort = forms.ModelChoiceField(queryset=Ort.objects.none(), widget=forms.HiddenInput)
    erwartet = forms.BooleanField(
        required=False,
        label=_("hier erwartet"),
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    gebinde = forms.MultipleChoiceField(
        choices=(),
        required=False,
        label=_("Gebinde-Vorgaben"),
        # The class is inherited by every single choice. It also lands on the
        # enclosing element. But the template renders that itself instead of
        # outputting the field as a whole, otherwise an empty box would stand there.
        widget=forms.CheckboxSelectMultiple(attrs={"class": "form-check-input"}),
    )

    def __init__(self, *args, braucht_gebinde: bool = False, inaktiv_bleibend=(), **kwargs):
        super().__init__(*args, **kwargs)
        #: Whether a ticked location needs a pack: not for weighable goods,
        #: there the scale stands in ('HausartikelCreateView._erwartungen_anlegen').
        self.braucht_gebinde = braucht_gebinde
        #: Typed names whose pack stays inactive, as their row answered. Like in
        #: the counting list they give the article no row and do not count as a pack.
        self.inaktiv_bleibend = set(inaktiv_bleibend)

    def clean(self) -> dict:
        """A location without a pack would give the article no row there, as in the counting list.

        Until 10/2026 such a location was taken as it was. A pack that stays
        inactive does not count either (decided 2026-10-18 for the counting
        list): new inventories give it no row.
        """
        super().clean()
        daten = self.cleaned_data
        gewaehlt = daten.get("gebinde") or []
        # A ticked pack without a tick on the location is no input one would
        # have to reject, because it says the same.
        if gewaehlt:
            daten["erwartet"] = True
        if not (daten.get("erwartet") and self.braucht_gebinde):
            return daten
        if not gewaehlt:
            self.add_error("gebinde", _("Ohne Gebinde bekäme der Artikel hier keine Zeile."))
        elif set(gewaehlt) <= self.inaktiv_bleibend:
            self.add_error(
                "gebinde",
                _(
                    "Ohne aktives Gebinde bekäme der Artikel hier keine Zeile. Ein Gebinde, "
                    "das inaktiv bleibt, zählt dabei nicht."
                ),
            )
        return daten


class BaseOrtErwartungFormSet(forms.BaseFormSet):
    """Per location of the house one fixed row, none for adding.

    'inaktive' are the typed names that land on an inactive pack of the
    product. The template marks them, as the counting list marks such a
    default. 'inaktiv_bleibend' are those of them whose row answered "leave
    inactive" ('OrtErwartungForm.clean').
    """

    def __init__(
        self,
        *args,
        haus: Haus,
        gebindenamen=(),
        braucht_gebinde=False,
        inaktive=(),
        inaktiv_bleibend=(),
        **kwargs,
    ):
        self.orte = list(haus.alle_orte().select_related("bereich"))
        self.gebindenamen = list(gebindenamen)
        self.braucht_gebinde = braucht_gebinde
        self.inaktive = list(inaktive)
        self.inaktiv_bleibend = list(inaktiv_bleibend)
        kwargs.setdefault("initial", [{"ort": ort.pk} for ort in self.orte])
        super().__init__(*args, **kwargs)

    def get_form_kwargs(self, index):
        return super().get_form_kwargs(index) | {
            "braucht_gebinde": self.braucht_gebinde,
            "inaktiv_bleibend": self.inaktiv_bleibend,
        }

    def add_fields(self, form, index):
        super().add_fields(form, index)
        modellwahl(form, "ort").queryset = Ort.objects.filter(pk__in=[ort.pk for ort in self.orte])
        auswahl(form, "gebinde").choices = [(name, name) for name in self.gebindenamen]
        # For the label of the row. 'ort' itself is a hidden field.
        form.ort_objekt = self._ort_der_zeile(form, index)

    def _ort_der_zeile(self, form, index) -> Ort | None:
        """The location a row writes to: the one it sent, not the one at its position.

        Between showing the page and sending it, a location may have come or
        gone, and the positions shift. Until 10/2026 a row that came back
        stood under the name of another location, and saving wrote its ticks
        onto the one it had sent.
        """
        if not form.is_bound:
            return an_stelle(self.orte, index)
        nummer = ganzzahl(form["ort"].data)
        return next((ort for ort in self.orte if ort.pk == nummer), None)

    def clean(self) -> None:
        """A location twice would be two places of one article there. The database refuses that.

        Only a sent page that was changed gets there. Until 10/2026 that ended
        on the error page.
        """
        super().clean()
        gesehen: set[int] = set()
        for form in self.forms:
            ort = getattr(form, "cleaned_data", {}).get("ort")
            if ort is None:
                continue
            if ort.pk in gesehen:
                raise forms.ValidationError(
                    _("Ein Ort steht doppelt in der Liste. Bitte die Seite neu laden.")
                )
            gesehen.add(ort.pk)

    @property
    def erwartungen(self) -> list[tuple[Ort, list[str]]]:
        """The ticked locations including the pack names expected there."""
        gewaehlt = []
        for form in self.forms:
            daten = getattr(form, "cleaned_data", {})
            if daten.get("erwartet") and daten.get("ort"):
                gewaehlt.append((daten["ort"], list(daten.get("gebinde") or ())))
        return gewaehlt


OrtErwartungFormSet = cast(
    "type[BaseOrtErwartungFormSet]",
    forms.formset_factory(OrtErwartungForm, formset=BaseOrtErwartungFormSet, extra=0),
)
