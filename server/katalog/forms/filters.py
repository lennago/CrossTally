"""Search, filter and sort of the catalog lists.

The skeleton is in 'django_project/list_filters.py'. Here stands what a
particular list filters, and the status filter that products and product
groups share.
"""

from django import forms
from django.db.models import Exists, OuterRef, Q, QuerySet, Value
from django.db.models.functions import NullIf
from django.utils.translation import gettext_lazy as _

from django_project.form_fields import auswahl
from django_project.labels import Beschriftung
from django_project.list_filters import ListenFilterForm, Mehrfachwahl, mit_aehnlichen
from django_project.validation import ganzzahl
from organisation.models import Haus

from ..family_tree import Familienknoten, familienbaum
from ..models import (
    GEFUEHRT,
    SICHTBAR,
    Artikelstatus,
    Behaelter,
    Hausartikel,
    Inventurart,
    Rezepturartikel,
    Rezepturkomponente,
    Ware,
    Warenfamilie,
    Warengruppe,
)

#: The status filter of the article lists. The empty choice means **carried**,
#: i.e. active or inactive. Whoever opens a list wants to see what the house
#: carries, today or next season. Discontinued is over and is only added by
#: whoever fetches it explicitly. Ignored ones are in no choice of the house
#: articles. For the products the product family filter decides about them.
NUR_AKTIVE = "aktiv"


NUR_INAKTIVE = "inaktiv"


NUR_EINGESTELLTE = "eingestellt"


ALLE_STATUS = "alle"


STATUS_AUSWAHL = (
    ("", _("Geführt")),
    (NUR_AKTIVE, _("Aktiv")),
    (NUR_INAKTIVE, _("Inaktiv")),
    (NUR_EINGESTELLTE, _("Eingestellt")),
    (ALLE_STATUS, _("Alle")),
)


#: Which article statuses a choice in the filter means.
STATUS_JE_AUSWAHL = {
    "": GEFUEHRT,
    NUR_AKTIVE: (Artikelstatus.AKTIV,),
    NUR_INAKTIVE: (Artikelstatus.INAKTIV,),
    NUR_EINGESTELLTE: (Artikelstatus.EINGESTELLT,),
    ALLE_STATUS: SICHTBAR,
}


def sammelstatus_bedingung(status: str, feld: str):
    """The condition for a choice in the status filter, for product and product group.

    Both stand like their liveliest house article ('AusHausartikeln.sammelstatus'):
    active as soon as one is active. Discontinued if all are discontinued.
    Otherwise inactive. Carried is what is not discontinued. 'feld' is the path
    from the house article to the row ('ware' or 'warengruppe').

    A row entirely **without** house articles counts as active, not inactive:
    that is how it arises through the form, and it would otherwise vanish from
    the list at the very moment someone creates it. Setting inactive is a
    decision, not a state of not-yet. Ignored ones do not count.

    'None' means: no restriction ("All").
    """
    if status == ALLE_STATUS:
        return None
    hausartikel = Hausartikel.objects.filter(**{feld: OuterRef("pk")}, status__in=SICHTBAR)
    hat_aktive = Exists(hausartikel.filter(status=Artikelstatus.AKTIV))
    hat_inaktive = Exists(hausartikel.filter(status=Artikelstatus.INAKTIV))
    hat_welche = Exists(hausartikel)
    return {
        NUR_AKTIVE: hat_aktive | ~hat_welche,
        NUR_INAKTIVE: hat_inaktive & ~hat_aktive,
        NUR_EINGESTELLTE: hat_welche & ~hat_aktive & ~hat_inaktive,
    }.get(status, hat_aktive | hat_inaktive | ~hat_welche)


#: The choice in a family list for products entirely without family.
OHNE_FAMILIE = "ohne"


OHNE_FAMILIE_NAME = _("Ohne Warenfamilie")


def familienbedingung(wahl: Mehrfachwahl, weg: str = "") -> Q:
    """The chosen families as a condition. 'weg' leads to the product ('ware__').

    Exactly the ticked families count. A tick on a family above sets the
    ones below in the menu ('multi_select.js'), and whoever then takes one
    of them out means exactly that.
    """
    bedingung = Q(**{f"{weg}warenfamilie_id__in": wahl.nummern()})
    if OHNE_FAMILIE in wahl:
        bedingung |= Q(**{f"{weg}warenfamilie__isnull": True})
    return bedingung


def familiensuche(begriff: str, weg: str = "", *, aehnlich: bool = False) -> Q:
    """The families whose name matches, with everything below them.

    Whoever types "drinks" means the beers under "bottled drinks" too, as with
    a tick in the filter. The name of the family below need not repeat the word.
    'weg' leads to the product ('ware__'). With 'aehnlich' the name only has to
    sound like the term (pg_trgm, see 'mit_aehnlichen').
    """
    vergleich = "trigram_word_similar" if aehnlich else "icontains"
    nummern = list(
        Warenfamilie.objects.filter(**{f"name__{vergleich}": begriff}).values_list("pk", flat=True)
    )
    unten = Warenfamilie.mit_nachkommen(nummern) if nummern else set()
    return Q(**{f"{weg}warenfamilie_id__in": unten})


def familien_geordnet(queryset) -> list[Warenfamilie]:
    """The families in the order of the tree: by path, "Not countable" first.

    The path is built from the parents, which are fetched once here instead
    of once per family.
    """
    familien = list(queryset.select_related("uebergeordnet"))
    alle = {familie.pk: familie for familie in Warenfamilie.objects.all()}
    for familie in familien:
        _eltern_einsetzen(familie, alle)
    return sorted(familien, key=lambda f: (not f.sammelt_ignorierte, f.anzeigepfad.casefold()))


def _eltern_einsetzen(familie: Warenfamilie, alle: dict[int, Warenfamilie]) -> None:
    """Attaches the fetched parents up the chain, so the path costs no query."""
    gesehen = {familie.pk}
    aktuell = familie
    while aktuell.uebergeordnet_id is not None and aktuell.uebergeordnet_id not in gesehen:
        oben = alle.get(aktuell.uebergeordnet_id)
        if oben is None:
            break
        gesehen.add(oben.pk)
        aktuell.uebergeordnet = oben
        aktuell = oben


def mit_vorfahren(familien) -> set[int]:
    """The numbers of these families and of every family above them."""
    eltern = dict(Warenfamilie.objects.values_list("pk", "uebergeordnet_id"))
    gefunden: set[int] = set()
    for nummer in familien.values_list("pk", flat=True):
        aktuell: int | None = nummer
        while aktuell is not None and aktuell not in gefunden:
            gefunden.add(aktuell)
            aktuell = eltern.get(aktuell)
    return gefunden


#: One step of indentation in a selection list. Ordinary spaces would be
#: collapsed when displayed, these are not.
EINZUG = "\u00a0" * 4


def baumbeschriftung(familie: Warenfamilie) -> str:
    """The family's name, indented by its depth and with a branch mark: "    └ Biere"."""
    tiefe = familie.tiefe
    return f"{EINZUG * tiefe}{'└ ' if tiefe else ''}{familie.anzeigename}"


#: In the product list: products whose articles stand in different types.
GEMISCHT = "gemischt"


def artenwahl(gemischt: bool = False) -> list[tuple[str, Beschriftung]]:
    """The choices of the type filter, in the order of the types.

    The products additionally offer "mixed": whoever wants their articles
    to agree finds the ones that do not.
    """
    return [
        ("", _("Jede Inventurart")),
        *((str(pk), name) for pk, name in Inventurart.objects.values_list("pk", "name")),
        *([(GEMISCHT, _("Gemischt"))] if gemischt else []),
    ]


def waren_der_art(wert: str) -> Q:
    """The products with an article in this type, or with articles in several ('GEMISCHT').

    A mixed product stands under each of its types: it is counted there.
    'Exists' instead of a join, because a join would multiply the counted
    columns of the list.
    """
    sichtbar = Hausartikel.objects.filter(ware=OuterRef("pk"), status__in=SICHTBAR)
    if wert == GEMISCHT:
        # Two visible articles of the product in different types.
        anderer = Hausartikel.objects.filter(ware=OuterRef("ware"), status__in=SICHTBAR).exclude(
            inventurart_id=OuterRef("inventurart_id")
        )
        return Q(Exists(sichtbar.filter(Exists(anderer))))
    art = ganzzahl(wert)
    if art is None:
        return Q()
    return Q(Exists(sichtbar.filter(inventurart_id=art)))


def baumauswahl(queryset) -> list[tuple[int, str]]:
    """The families of 'queryset' as choices of a selection list, in the order of the tree.

    A family below another stands indented under it, as in the family list.
    "Not countable" stands last: it belongs to no tree. Tom Select shows the
    text of an option and nothing else, hence the indentation in the text.
    """
    familien = familien_geordnet(queryset)
    familien.sort(key=lambda familie: familie.sammelt_ignorierte)
    return [(familie.pk, baumbeschriftung(familie)) for familie in familien]


def familieneintraege(familien) -> list[tuple[str, Beschriftung]]:
    """The families for a multiple selection, in the order of the tree."""
    return [(str(familie.pk), familie.anzeigename) for familie in familien]


def familienstruktur(familien) -> dict[str, tuple[int, str]]:
    """Per family its depth and the family above it, for the indented menu.

    A family whose parent is not in the list stands at the top there:
    the list of a house only carries what the house uses.
    """
    dabei = {familie.pk for familie in familien}
    struktur = {}
    for familie in familien:
        oben = familie.uebergeordnet_id
        struktur[str(familie.pk)] = (
            familie.tiefe if oben in dabei else 0,
            str(oben) if oben in dabei else "",
        )
    return struktur


class MitFamilienbaum(ListenFilterForm):
    """Grouped by family, the list shows the tree of the families.

    At the top the families without a family above them, inside them their
    subfamilies, and the entries only in the family they belong to directly.
    A grouping by anything else stays a flat list of blocks.
    """

    def familie_von(self, objekt) -> Warenfamilie | None:
        """The family an entry belongs to directly, for the tree."""
        raise NotImplementedError

    def familienbaum(self, objekte, knoten: type[Familienknoten] = Familienknoten) -> list:
        """The entries in the tree of their families. 'knoten' adds what a page needs per family."""
        return familienbaum(objekte, self.familie_von, knoten=knoten)

    def gruppen(self, objekte) -> list | None:
        if (gliederung := self.gliederung) is None:
            return None
        if gliederung[0] != "familie":
            return super().gruppen(objekte)
        return self.familienbaum(objekte)


#: The filter "Weighing", the same for the product list and the house articles.
ABGEWOGEN = "ja"
NICHT_ABGEWOGEN = "nein"
ABWIEGEN_WAHL = (
    ("", _("Ob abgewogen oder nicht")),
    (ABGEWOGEN, _("Nur abgewogene")),
    (NICHT_ABGEWOGEN, _("Nur nicht abgewogene")),
)


def nach_abwiegen(queryset, daten: dict, feld: str):
    """Only weighed or only not weighed products, all where nothing is chosen."""
    if (wiegen := daten.get("abwiegbar")) in (ABGEWOGEN, NICHT_ABGEWOGEN):
        return queryset.filter(**{feld: wiegen == ABGEWOGEN})
    return queryset


class WareFilterForm(MitFamilienbaum):
    """Search, filter and sort of the product list.

    The search also goes over the house articles: whoever has an article
    number from the inventory management system at hand finds the product it
    belongs to with it.
    """

    SPALTEN = {
        "name": (_("Bezeichnung"), ("name",)),
        "einheit": (_("Einheit"), ("basiseinheit__kennung",)),
        "familie": (_("Warenfamilie"), ("warenfamilie__name",)),
        # By the first type of its articles ('art_folge' in the view).
        "art": (_("Inventurart"), ("art_folge",)),
        "abwiegen": (_("Abwiegen"), ("abwiegbar",)),
    }
    STANDARD_SPALTE = "name"
    ZWEITSCHLUESSEL = ("name",)
    GLIEDERUNGEN = (("familie", _("Nach Warenfamilie"), ("warenfamilie__name",)),)
    AUSWAHLFELDER = ("status", "inventurart", "abwiegbar")
    FILTERFELDER = ("q", "status", "inventurart", "abwiegbar")

    #: The three states per house in the filter "Carried in".
    HAUS_EGAL = ""
    HAUS_MIT = "mit"
    HAUS_OHNE = "ohne"
    HAUS_ZUSTAENDE = ((HAUS_EGAL, _("egal")), (HAUS_MIT, _("muss")), (HAUS_OHNE, _("nicht")))

    q = forms.CharField(
        required=False,
        label=_("Suche"),
        widget=forms.TextInput(
            attrs={
                "placeholder": _("Bezeichnung, Artikelnummer, EAN, Warengruppe oder Familie"),
                "type": "search",
            }
        ),
    )
    status = forms.ChoiceField(choices=STATUS_AUSWAHL, required=False, label=_("Status"))
    # Both choices are in the address, one number per entry. If the entry is
    # missing entirely, the default applies: for the families all except "Not countable".
    warengruppe = forms.MultipleChoiceField(required=False, label=_("Warengruppe"))
    familie = forms.MultipleChoiceField(required=False, label=_("Warenfamilie"))
    # The types are rows. Their choices come in '__init__'.
    inventurart = forms.ChoiceField(choices=(), required=False, label=_("Inventurart"))
    abwiegbar = forms.ChoiceField(choices=ABWIEGEN_WAHL, required=False, label=_("Abwiegen"))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        auswahl(self, "inventurart").choices = artenwahl(gemischt=True)
        familien = familien_geordnet(Warenfamilie.objects.all())
        ignoriert = tuple(str(familie.pk) for familie in familien if familie.sammelt_ignorierte)
        # All except "Not countable", because there lies what is no product.
        self.familienwahl = Mehrfachwahl(
            self,
            "familie",
            [*familieneintraege(familien), (OHNE_FAMILIE, OHNE_FAMILIE_NAME)],
            vorgabe=[
                *(str(familie.pk) for familie in familien if not familie.sammelt_ignorierte),
                OHNE_FAMILIE,
            ],
            vorgabe_text=_("Alle außer Nicht zählbar"),
            kursiv=(*ignoriert, OHNE_FAMILIE),
            struktur=familienstruktur(familien),
        )
        self.gruppenwahl = Mehrfachwahl(
            self,
            "warengruppe",
            [(str(gruppe.pk), gruppe.name) for gruppe in Warengruppe.objects.order_by("name")],
            vorgabe_text=_("Alle Warengruppen"),
        )
        self.mehrfachwahlen = (self.familienwahl, self.gruppenwahl)
        self.haeuser = list(Haus.objects.order_by("name"))
        for haus in self.haeuser:
            self.fields[self.hausfeld(haus)] = forms.ChoiceField(
                choices=self.HAUS_ZUSTAENDE, required=False, widget=forms.HiddenInput
            )

    @staticmethod
    def hausfeld(haus: Haus) -> str:
        return f"haus_{haus.pk}"

    # -- Houses ------------------------------------------------------------

    @property
    def haus_wahl(self) -> list[tuple[str, Haus, str]]:
        """(field name, house, state) per house."""
        return [
            (self.hausfeld(haus), haus, self.daten.get(self.hausfeld(haus)) or self.HAUS_EGAL)
            for haus in self.haeuser
        ]

    @property
    def haeuser_text(self) -> Beschriftung:
        teile = []
        for _kennung, haus, zustand in self.haus_wahl:
            if zustand == self.HAUS_MIT:
                teile.append(f"+{haus.name}")
            elif zustand == self.HAUS_OHNE:
                teile.append(f"−{haus.name}")
        return " ".join(teile) or _("Alle Häuser")

    @property
    def ist_eingeschraenkt(self) -> bool:
        return super().ist_eingeschraenkt or any(zustand for _, _, zustand in self.haus_wahl)

    def gruppenname(self, objekt, schluessel: str) -> Beschriftung:
        return objekt.warenfamilie.anzeigename if objekt.warenfamilie else OHNE_FAMILIE_NAME

    def familie_von(self, objekt) -> Warenfamilie | None:
        return objekt.warenfamilie

    def relevanz(self, begriff: str) -> tuple:
        """The product's name first, then what its houses call it, then their numbers.

        'Exists' instead of a join, as in 'filtern': a product with two
        house articles would otherwise stand in two tiers and thus twice in
        the list.
        """
        im_haus = Hausartikel.objects.filter(ware=OuterRef("pk"))
        return (
            Q(name__istartswith=begriff),
            Q(name__icontains=begriff) | Exists(im_haus.filter(name__icontains=begriff)),
            Exists(im_haus.filter(Q(artikelnummer__icontains=begriff) | Q(ean__icontains=begriff))),
        )

    def filtern(self, queryset: QuerySet[Ware], daten: dict) -> QuerySet[Ware]:
        # A product is active as long as a house article in it is active. That
        # is a property without a column. 'Exists' instead of a join, because a
        # join would multiply the annotated count of the list.
        queryset = self.nach_status(queryset, daten)
        queryset = queryset.filter(familienbedingung(self.familienwahl))
        queryset = self.nach_haeusern(queryset, daten)
        if begriff := daten.get("q", "").strip():
            # Product group and family as well, like the house article list:
            # whoever types "drinks" wants the whole shelf, not one product.
            genau = (
                Q(name__icontains=begriff)
                | Q(hausartikel__name__icontains=begriff)
                | Q(hausartikel__artikelnummer__icontains=begriff)
                | Q(hausartikel__ean__icontains=begriff)
                | Q(hausartikel__warengruppe__name__icontains=begriff)
                | familiensuche(begriff)
            )
            queryset = mit_aehnlichen(
                queryset,
                genau,
                lambda: (
                    Q(name__trigram_word_similar=begriff)
                    | Q(hausartikel__name__trigram_word_similar=begriff)
                    | Q(hausartikel__warengruppe__name__trigram_word_similar=begriff)
                    | familiensuche(begriff, aehnlich=True)
                ),
            ).distinct()
        if self.gruppenwahl.ist_eingeschraenkt:
            # 'Exists' instead of a join: a product in two chosen groups would
            # otherwise appear twice, and the annotated numbers would multiply.
            queryset = queryset.filter(
                Exists(
                    Hausartikel.objects.filter(
                        ware=OuterRef("pk"),
                        warengruppe_id__in=self.gruppenwahl.nummern(),
                    )
                )
            )
        if art := daten.get("inventurart"):
            queryset = queryset.filter(waren_der_art(art))
        return nach_abwiegen(queryset, daten, "abwiegbar")

    @staticmethod
    def nach_status(queryset: QuerySet[Ware], daten: dict) -> QuerySet[Ware]:
        """A product stands like its liveliest house article ('Ware.warenstatus').

        Active as soon as one house carries it actively. Discontinued if all its
        rows are discontinued. Otherwise inactive. Carried is what is not
        discontinued.

        A product entirely **without** house articles counts as active, not
        inactive: that is how it arises through the form, and it would
        otherwise vanish from the list at the very moment someone creates it.
        Setting inactive is a decision, not a state of not-yet.

        Products from "Not countable" have no such status. Whether they appear
        is decided by the family filter alone.
        """
        bedingung = sammelstatus_bedingung(daten.get("status") or "", "ware")
        if bedingung is None:
            return queryset
        return queryset.filter(bedingung | Q(warenfamilie__sammelt_ignorierte=True))

    def nach_haeusern(self, queryset: QuerySet[Ware], daten: dict):
        """Per house: must the product be carried there, must it not, or does it not matter.

        "Carried" in the sense of the status filter: whoever searches for
        inactive products means the houses that carry them inactive. If anything
        at all is chosen, the product has to be in at least one house that is not
        excluded. So "A and B do not matter, C not" means: in A or B, but not in C.
        """
        mit = [haus.pk for feld, haus, zustand in self.haus_wahl if zustand == self.HAUS_MIT]
        ohne = [haus.pk for feld, haus, zustand in self.haus_wahl if zustand == self.HAUS_OHNE]
        if not (mit or ohne):
            return queryset
        status = (
            *STATUS_JE_AUSWAHL.get(daten.get("status") or "", GEFUEHRT),
            Artikelstatus.IGNORIERT,
        )
        hausartikel = Hausartikel.objects.filter(ware=OuterRef("pk"), status__in=status)
        for haus_pk in mit:
            queryset = queryset.filter(Exists(hausartikel.filter(haus_id=haus_pk)))
        for haus_pk in ohne:
            queryset = queryset.filter(~Exists(hausartikel.filter(haus_id=haus_pk)))
        if not mit:
            queryset = queryset.filter(Exists(hausartikel.exclude(haus_id__in=ohne)))
        return queryset


class HausartikelFilterForm(MitFamilienbaum):
    """Search, filter and sort of the house articles of a house.

    Product groups and product families are restricted to those actually used
    in the house, because across houses there are twice as many, and the
    others would have no hit here anyway. Both are multiple selections as in
    the product list. "Not countable" is not available: this list shows
    ignored house articles in no setting.

    The same form filters the tab "Select articles" of the counting list.
    """

    SPALTEN = {
        # An own article has no number. As NULL ('nummer_oder_leer', see
        # 'filtern') it comes last in both directions ('columns.py'), the own
        # ones among each other by their product, as decided on 2026-09-23.
        # Until 10/2026 the empty text stood in front of every number.
        "nummer": (_("Nr."), ("nummer_oder_leer", "ware__name")),
        "name": (_("Bezeichnung"), ("name",)),
        "warengruppe": (_("Warengruppe"), ("warengruppe__name",)),
        # The product is called the same across houses, the house row is not:
        # sorted by it the same products stand together, even if the houses
        # named them differently.
        "ware": (_("Ware"), ("ware__name",)),
        "familie": (_("Warenfamilie"), ("ware__warenfamilie__name",)),
        "einheit": (_("Einheit"), ("basiseinheit__kennung",)),
        "art": (_("Inventurart"), ("inventurart__reihenfolge", "inventurart__name")),
        "abwiegen": (_("Abwiegen"), ("ware__abwiegbar",)),
        "geaendert": (_("Geändert"), ("geaendert_am",)),
    }
    STANDARD_SPALTE = "nummer"
    ZWEITSCHLUESSEL = ("nummer_oder_leer", "ware__name")
    GLIEDERUNGEN = (
        ("warengruppe", _("Nach Warengruppe"), ("warengruppe__name",)),
        ("familie", _("Nach Warenfamilie"), ("ware__warenfamilie__name",)),
    )
    AUSWAHLFELDER = ("status", "inventurart", "abwiegbar")
    FILTERFELDER = ("q", "status", "inventurart", "abwiegbar")

    q = forms.CharField(
        required=False,
        label=_("Suche"),
        widget=forms.TextInput(
            attrs={
                "placeholder": _("Nummer, Bezeichnung, Ware, Warengruppe, Familie oder EAN"),
                "type": "search",
            }
        ),
    )
    warengruppe = forms.MultipleChoiceField(required=False, label=_("Warengruppe"))
    familie = forms.MultipleChoiceField(required=False, label=_("Warenfamilie"))
    status = forms.ChoiceField(choices=STATUS_AUSWAHL, required=False, label=_("Status"))
    # The type hangs on the house article itself.
    inventurart = forms.ChoiceField(choices=(), required=False, label=_("Inventurart"))
    # Weighing hangs on the product, not on the house row. Filtering
    # therefore goes through the product.
    abwiegbar = forms.ChoiceField(choices=ABWIEGEN_WAHL, required=False, label=_("Abwiegen"))

    def __init__(self, *args, haus: Haus, **kwargs):
        super().__init__(*args, **kwargs)
        auswahl(self, "inventurart").choices = artenwahl()
        self.haus = haus
        im_haus = Q(hausartikel__haus=haus, hausartikel__status__in=SICHTBAR)
        gruppen = Warengruppe.objects.filter(im_haus).distinct().order_by("name")
        # The families the house uses, and the ones above them: whoever
        # chooses "bottled drinks" finds the beers of the house under it.
        im_haus = Warenfamilie.objects.filter(
            waren__hausartikel__haus=haus,
            waren__hausartikel__status__in=SICHTBAR,
        ).distinct()
        familien = familien_geordnet(
            Warenfamilie.objects.filter(pk__in=mit_vorfahren(im_haus)).exclude(
                sammelt_ignorierte=True
            )
        )
        self.gruppenwahl = Mehrfachwahl(
            self,
            "warengruppe",
            [(str(gruppe.pk), gruppe.name) for gruppe in gruppen],
            vorgabe_text=_("Alle Warengruppen"),
        )
        self.familienwahl = Mehrfachwahl(
            self,
            "familie",
            [*familieneintraege(familien), (OHNE_FAMILIE, OHNE_FAMILIE_NAME)],
            vorgabe_text=_("Alle Warenfamilien"),
            kursiv=(OHNE_FAMILIE,),
            struktur=familienstruktur(familien),
        )
        self.mehrfachwahlen = (self.gruppenwahl, self.familienwahl)

    def gruppenname(self, objekt, schluessel: str) -> Beschriftung:
        if schluessel == "familie":
            familie = objekt.ware.warenfamilie
            return familie.anzeigename if familie else OHNE_FAMILIE_NAME
        return objekt.warengruppe.anzeigename

    def familie_von(self, objekt) -> Warenfamilie | None:
        return objekt.ware.warenfamilie

    def relevanz(self, begriff: str) -> tuple:
        """Both names count as the designation: the house's and the product's.

        Whoever searches knows the one or the other. Then number and EAN.
        Product group and family come last: they bring a whole shelf.
        """
        return (
            Q(name__istartswith=begriff) | Q(ware__name__istartswith=begriff),
            Q(name__icontains=begriff) | Q(ware__name__icontains=begriff),
            Q(artikelnummer__icontains=begriff) | Q(ean__icontains=begriff),
        )

    def filtern(self, queryset: QuerySet[Hausartikel], daten: dict) -> QuerySet[Hausartikel]:
        if begriff := daten.get("q", "").strip():
            # The search goes over everything in the row: whoever types "Getränke"
            # means the product group, and whoever knows the product's name, not
            # that of the house row. All via forward relations, hence without
            # 'distinct'.
            genau = (
                Q(name__icontains=begriff)
                | Q(artikelnummer__icontains=begriff)
                | Q(ean__icontains=begriff)
                | Q(ware__name__icontains=begriff)
                | Q(warengruppe__name__icontains=begriff)
                | familiensuche(begriff, "ware__")
            )
            queryset = mit_aehnlichen(
                queryset,
                genau,
                lambda: (
                    Q(name__trigram_word_similar=begriff)
                    | Q(ware__name__trigram_word_similar=begriff)
                    | Q(warengruppe__name__trigram_word_similar=begriff)
                    | familiensuche(begriff, "ware__", aehnlich=True)
                ),
            )
        # Only where really restricted: the default "all" as a condition would
        # exclude a group the house only still carries ignored and which is
        # therefore not available.
        if self.gruppenwahl.ist_eingeschraenkt:
            queryset = queryset.filter(warengruppe_id__in=self.gruppenwahl.nummern())
        if self.familienwahl.ist_eingeschraenkt:
            queryset = queryset.filter(familienbedingung(self.familienwahl, "ware__"))
        # Without an entry the carried ones. Ignored ones are in no choice.
        queryset = queryset.filter(
            status__in=STATUS_JE_AUSWAHL.get(daten.get("status") or "", GEFUEHRT)
        )
        if (art := ganzzahl(daten.get("inventurart"))) is not None:
            queryset = queryset.filter(inventurart_id=art)
        queryset = nach_abwiegen(queryset, daten, "ware__abwiegbar")
        # What the number column and the secondary key sort by, see 'SPALTEN'.
        return queryset.annotate(nummer_oder_leer=NullIf("artikelnummer", Value("")))


class WarengruppeFilterForm(ListenFilterForm):
    """Search and status filter of the product group list.

    The status is the same as with the products: a group stands like its
    liveliest house article, and by default the list shows what is carried.
    """

    SPALTEN = {
        "name": (_("Warengruppe"), ("name",)),
        "artikel": (_("Hausartikel"), ("anzahl_artikel",)),
        "aktiv": (_("davon aktiv"), ("anzahl_aktiv",)),
    }
    STANDARD_SPALTE = "name"
    ZWEITSCHLUESSEL = ("name",)
    AUSWAHLFELDER = ("status",)
    FILTERFELDER = ("q", "status")

    q = forms.CharField(
        required=False,
        label=_("Suche"),
        widget=forms.TextInput(attrs={"placeholder": _("Name der Warengruppe"), "type": "search"}),
    )
    status = forms.ChoiceField(choices=STATUS_AUSWAHL, required=False, label=_("Status"))

    def relevanz(self, begriff: str) -> tuple:
        return (Q(name__istartswith=begriff),)

    def filtern(self, queryset: QuerySet[Warengruppe], daten: dict) -> QuerySet[Warengruppe]:
        if begriff := daten.get("q", "").strip():
            queryset = queryset.filter(name__icontains=begriff)
        bedingung = sammelstatus_bedingung(daten.get("status") or "", "warengruppe")
        return queryset if bedingung is None else queryset.filter(bedingung)


class RezepturartikelFilterForm(ListenFilterForm):
    """Search and status filter of the recipe article list.

    The search also goes over the ingredients: whoever types "Nachos" wants to
    see the portions they are in. The status derives from the house articles
    of the ingredients ('Rezepturartikel.status_je_artikel'). Unlike the other
    lists this one shows **all** by default, because a freshly created recipe
    article has no recipe yet, would count as not resolvable and would
    otherwise vanish at the very moment someone creates it.
    """

    SPALTEN = {
        "name": (_("Artikel"), ("name",)),
        "einheit": (_("Einheit"), ("basiseinheit__kennung",)),
    }
    STANDARD_SPALTE = "name"
    ZWEITSCHLUESSEL = ("name",)
    AUSWAHLFELDER = ("status",)
    FILTERFELDER = ("q", "status")

    STATUS = {
        NUR_AKTIVE: Artikelstatus.AKTIV,
        NUR_INAKTIVE: Artikelstatus.INAKTIV,
        NUR_EINGESTELLTE: Artikelstatus.EINGESTELLT,
    }

    q = forms.CharField(
        required=False,
        label=_("Suche"),
        widget=forms.TextInput(attrs={"placeholder": _("Name oder Zutat"), "type": "search"}),
    )
    status = forms.ChoiceField(
        choices=(
            ("", _("Alle")),
            (NUR_AKTIVE, _("Aktiv (alle Zutaten aktiv)")),
            (NUR_INAKTIVE, _("Inaktiv (eine Zutat inaktiv)")),
            (NUR_EINGESTELLTE, _("Nirgends auflösbar")),
        ),
        required=False,
        label=_("Status"),
    )

    def __init__(self, *args, status_je_artikel: dict[int, str], **kwargs):
        super().__init__(*args, **kwargs)
        self.status_je_artikel = status_je_artikel

    def relevanz(self, begriff: str) -> tuple:
        """The own name before the ingredients."""
        return (Q(name__istartswith=begriff), Q(name__icontains=begriff))

    def filtern(
        self, queryset: QuerySet[Rezepturartikel], daten: dict
    ) -> QuerySet[Rezepturartikel]:
        if begriff := daten.get("q", "").strip():
            queryset = queryset.filter(
                Q(name__icontains=begriff)
                | Exists(
                    Rezepturkomponente.objects.filter(
                        rezepturartikel=OuterRef("pk"), ware__name__icontains=begriff
                    )
                )
            )
        if (gesucht := self.STATUS.get(daten.get("status") or "")) is not None:
            queryset = queryset.filter(
                pk__in=[pk for pk, status in self.status_je_artikel.items() if status == gesucht]
            )
        return queryset


class BehaelterFilterForm(ListenFilterForm):
    """Search of the container list, over the name and over what it is used for."""

    SPALTEN = {
        "name": (_("Behälter"), ("name",)),
        "tara": (_("Tara"), ("tara",)),
    }
    STANDARD_SPALTE = "name"
    ZWEITSCHLUESSEL = ("name",)
    FILTERFELDER = ("q",)

    q = forms.CharField(
        required=False,
        label=_("Suche"),
        widget=forms.TextInput(
            attrs={"placeholder": _("Name, Haus, Ware oder Rezepturartikel"), "type": "search"}
        ),
    )

    def relevanz(self, begriff: str) -> tuple:
        """The own name before the houses and products it is used for."""
        return (Q(name__istartswith=begriff), Q(name__icontains=begriff))

    def filtern(self, queryset: QuerySet[Behaelter], daten: dict) -> QuerySet[Behaelter]:
        if begriff := daten.get("q", "").strip():
            # 'Exists' instead of a join: a bucket for three matching products
            # would otherwise appear three times.
            queryset = queryset.filter(
                Q(name__icontains=begriff)
                | Exists(Haus.objects.filter(behaelter=OuterRef("pk"), name__icontains=begriff))
                | Exists(Ware.objects.filter(behaelter=OuterRef("pk"), name__icontains=begriff))
                | Exists(
                    Rezepturartikel.objects.filter(
                        behaelter=OuterRef("pk"), name__icontains=begriff
                    )
                )
            )
        return queryset
