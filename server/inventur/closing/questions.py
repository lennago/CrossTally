"""The questions before closing: seven blocks, computed from the state at the call.

Nothing is written here. Every function reads the count positions and
expectations of the one house and says what would have to be asked. Why
the blocks are cut this way is on the package ('inventur/closing/__init__.py').
"""

from dataclasses import dataclass, field
from decimal import Decimal

from django.db.models import Count
from django.utils.translation import gettext_lazy as _

from django_project.labels import Beschriftung
from katalog.models import (
    GEFUEHRT,
    Artikel,
    Artikelstatus,
    Behaelter,
    Erwartung,
    ErwartungGebinde,
    Gebinde,
    Hausartikel,
    Inventurart,
    Rezepturartikel,
    Ware,
    menge_kurz,
)
from organisation.models import Ort

from ..models import Artentscheidung, Inventur
from ..services import Ergebniszeile, ergebniszeilen


@dataclass
class Wiederaufnahme:
    """An inactive or discontinued house article that was counted."""

    hausartikel: Hausartikel
    #: Why it cannot come back, otherwise empty. Only possible for discontinued ones.
    hinderung: Beschriftung = ""

    @property
    def schluessel(self) -> str:
        return str(self.hausartikel.pk)


@dataclass
class NeueErwartung:
    """Counted unexpectedly: an article or pack the location's counting list does not know."""

    ort: Ort
    artikel: Artikel
    #: Empty for weighed goods and for recipe articles.
    gebinde: Gebinde | None
    #: What was counted, for recognising it.
    menge: str
    #: Is the article already there and only the pack is missing, or the scale?
    artikel_schon_erwartet: bool
    #: Counted on the scale: when adding, weighing will be expected here from now on.
    gewogen: bool = False
    #: Why it cannot be added, otherwise empty.
    hinderung: Beschriftung = ""

    @property
    def schluessel(self) -> str:
        return f"{self.ort.pk}-{self.artikel.pk}-{self.gebinde.pk if self.gebinde else 0}"


@dataclass
class UngezaehltesGebinde:
    """An expected pack for which nothing was entered, or the scale on which nothing lay.

    Both are the same question: should the row still be at this location next
    time? For weighing there is no default that could be deleted. There the
    expectation itself carries the answer ('wiegen_erwartet').
    """

    #: Empty when it is about weighing.
    vorgabe: ErwartungGebinde | None
    ort: Ort
    artikel: Artikel
    #: Explicitly removed in the app instead of just left empty.
    in_der_app_geloescht: bool
    #: Only set for weighing.
    erwartung: Erwartung | None = None

    @property
    def schluessel(self) -> str:
        if self.vorgabe is None and self.erwartung is not None:
            return f"wiegen-{self.erwartung.pk}"
        return str(self.vorgabe.pk) if self.vorgabe else ""

    @property
    def bezeichnung(self) -> str:
        return str(self.vorgabe.gebinde.anzeigename) if self.vorgabe else str(_("Wiegen"))


@dataclass
class Artwechsel:
    """What a row needs to switch its article into another type.

    The same question as on the article's page: for this article only, or
    for every article of its product ('Hausartikel.inventurart_setzen').
    """

    hausartikel: Hausartikel
    #: How many further carried articles its product has, in every house. Only
    #: then is it asked whether the switch applies to them.
    weitere: int = 0
    #: What another house of the run decided for the whole product, as long as
    #: the article here still stands so. Then only "this article" is open.
    festgelegt: Artentscheidung | None = None
    #: What the form last said about it. The view enters it.
    geltung: str = ""

    @property
    def ware_gesperrt(self) -> bool:
        """Another house of the run has just decided for the whole product."""
        return self.festgelegt is not None

    @property
    def schluessel(self) -> str:
        return str(self.hausartikel.pk)


@dataclass
class UngezaehlterArtikel(Artwechsel):
    """An active article without any counted value."""

    sollbestand: Decimal | None = None
    #: The types it can switch to: every one but its own.
    andere_arten: list[Inventurart] = field(default_factory=list)
    #: What the form last said about it. The view enters it.
    antwort: str = ""


@dataclass
class GezaehltOhneSoll:
    """A counted article for which neither file nor hand brought a target."""

    hausartikel: Hausartikel
    ist_text: str
    #: What the form last said about it. The view enters it.
    antwort: str = ""
    wert: str = ""

    @property
    def schluessel(self) -> str:
        return str(self.hausartikel.pk)


@dataclass
class GezaehltAusserhalb(Artwechsel):
    """Counted itself, although no type of this inventory counts the article.

    Found on the shelf and added in the app: whoever did that has it in view
    after all. Taken into one of the inventory's types, it gets its rows
    from the next inventory of that type on.
    """

    ist_text: str = ""
    #: The types of this inventory it can go into.
    ziele: list[Inventurart] = field(default_factory=list)
    #: The type the form last chose, as text. The view enters it.
    antwort: str = ""

    @property
    def gesperrt(self) -> bool:
        """Only "this article" is open: another house decided for the product."""
        return self.ware_gesperrt


@dataclass
class FremderBehaelter:
    """Weighed in a container assigned neither to the article nor to this house."""

    #: Who carries the containers: the product or the recipe article.
    traeger: Ware | Rezepturartikel
    behaelter: Behaelter
    #: Where it otherwise stands, empty for a container without any house (legacy).
    haeuser: str
    #: How often it was on the scale for this article.
    wiegungen: int
    #: What the form last said about it. The view enters it.
    antwort: str = ""

    @property
    def schluessel(self) -> str:
        art = "w" if isinstance(self.traeger, Ware) else "z"
        return f"{art}{self.traeger.pk}-{self.behaelter.pk}"


@dataclass
class Klaerung:
    wiederaufnahmen: list[Wiederaufnahme] = field(default_factory=list)
    neue_erwartungen: list[NeueErwartung] = field(default_factory=list)
    ungezaehlte_gebinde: list[UngezaehltesGebinde] = field(default_factory=list)
    ungezaehlte_artikel: list[UngezaehlterArtikel] = field(default_factory=list)
    ohne_soll: list[GezaehltOhneSoll] = field(default_factory=list)
    ausserhalb: list[GezaehltAusserhalb] = field(default_factory=list)
    fremde_behaelter: list[FremderBehaelter] = field(default_factory=list)

    @property
    def leer(self) -> bool:
        """Whether there is nothing to decide.

        Counted articles another house of the run has just put into a type,
        for the whole product, do not count: that decision stands. A page
        with only those on it would hold up the closing without asking a
        question. They stay on the page where there is one: there "this
        article" can still be chosen.
        """
        return not (
            self.wiederaufnahmen
            or self.neue_erwartungen
            or self.ungezaehlte_gebinde
            or self.ungezaehlte_artikel
            or self.ohne_soll
            or any(not eintrag.gesperrt for eintrag in self.ausserhalb)
            or self.fremde_behaelter
        )


def klaeren(inventur: Inventur) -> Klaerung:
    """What is to be asked before closing, computed from the current state."""
    positionen = list(
        inventur.zaehlpositionen.select_related(
            "ort__bereich",
            "artikel__hausartikel__basiseinheit",
            "artikel__hausartikel__ware__basiseinheit",
            "artikel__hausartikel__inventurart",
            "artikel__rezepturartikel__basiseinheit",
            "gebinde",
        )
        .prefetch_related("wiegungen__behaelter")
        .order_by("ort__bereich__name", "ort__name", "sortierung", "artikel__name")
    )
    erwartungen = {
        (erwartung.ort_id, erwartung.artikel_id): erwartung
        for erwartung in Erwartung.objects.filter(
            ort_id__in={position.ort_id for position in positionen}
        ).prefetch_related("gebinde_vorgaben__gebinde")
    }
    # Computed once and passed to the blocks: every computation goes over all
    # count positions including weighings, and until 09/2026 four blocks each
    # asked for it on their own (measured: 58 queries for 427 rows).
    zeilen = ergebniszeilen(inventur)
    festgelegt = festgelegte_arten(inventur)
    return Klaerung(
        wiederaufnahmen=_wiederaufnahmen(zeilen),
        neue_erwartungen=_neue_erwartungen(inventur, positionen, erwartungen),
        ungezaehlte_gebinde=_ungezaehlte_gebinde(positionen, erwartungen),
        ungezaehlte_artikel=_ungezaehlte_artikel(zeilen, festgelegt),
        ohne_soll=_gezaehlt_ohne_soll(inventur, zeilen),
        ausserhalb=_ausserhalb(inventur, positionen, zeilen, festgelegt),
        fremde_behaelter=_fremde_behaelter(inventur, positionen),
    )


def festgelegte_arten(inventur: Inventur) -> dict[int, Artentscheidung]:
    """Number of the product -> what another house of the run decided for all its articles.

    If two houses decided for the same product (possible before the lock),
    the later one applies. Whether it still holds is decided per row
    ('_noch_so'): if someone has since switched the article by hand, that
    is the newer decision, and the old one locks nothing any more.
    """
    entscheidungen = (
        Artentscheidung.objects.filter(inventur__in=inventur.lauf().exclude(pk=inventur.pk))
        .select_related("inventur__haus", "inventurart")
        .order_by("inventur__abgeschlossen_am", "pk")
    )
    return {entscheidung.ware_id: entscheidung for entscheidung in entscheidungen}


def _noch_so(
    festgelegt: dict[int, Artentscheidung], hausartikel: Hausartikel
) -> Artentscheidung | None:
    """The decision of another house about this product, as long as the article here follows it."""
    entscheidung = festgelegt.get(hausartikel.ware_id)
    if entscheidung is not None and entscheidung.inventurart_id == hausartikel.inventurart_id:
        return entscheidung
    return None


def _weitere(anzahl: dict[int, int], hausartikel: Hausartikel) -> int:
    """The product's carried articles besides this one. A discontinued one is none of them."""
    eigener = 1 if hausartikel.status in GEFUEHRT else 0
    return max(anzahl.get(hausartikel.ware_id, 0) - eigener, 0)


def weitere_je_ware(ware_ids) -> dict[int, int]:
    """Per product the number of its carried articles, in every house, in one query."""
    return dict(
        Hausartikel.objects.filter(ware_id__in=set(ware_ids), status__in=GEFUEHRT)
        .order_by()
        .values_list("ware_id")
        .annotate(anzahl=Count("pk"))
    )


def _wiederaufnahmen(zeilen: list[Ergebniszeile]) -> list[Wiederaufnahme]:
    """Not active but counted, even only as an ingredient: 'gezaehlt' already knows the recipes.

    The rows are ordered by article number, so these too. The slot is asked
    about only for discontinued ones. Those are few, and only they can have
    lost it.
    """
    wieder = []
    for zeile in zeilen:
        artikel = zeile.hausartikel
        if not zeile.gezaehlt or artikel.status not in (
            Artikelstatus.INAKTIV,
            Artikelstatus.EINGESTELLT,
        ):
            continue
        hinderung = ""
        if artikel.status == Artikelstatus.EINGESTELLT and (belegt := artikel.blockiert_durch()):
            hinderung = _(
                "Das Haus führt inzwischen %(nummer)s „%(name)s“, mit derselben Nummer "
                "oder derselben Ware."
            ) % {"nummer": belegt.artikelnummer, "name": belegt.name}
        wieder.append(Wiederaufnahme(artikel, hinderung))
    return wieder


def _neue_erwartungen(inventur, positionen, erwartungen) -> list[NeueErwartung]:
    """What was counted where nobody expected it: the candidates for the counting list.

    A row is a find only if it is active, unexpected, holds a quantity and, when
    weighed, more than empty bowls. A find whose article is already expected at
    this location is none: it is the same row seen twice.
    """
    neue = []
    for position in positionen:
        if not position.aktiv or not position.unerwartet:
            continue
        if (menge := position.menge_in_basiseinheit()) is None:
            continue
        if position.wird_gewogen and not any(w.brutto_gramm for w in position.wiegungen.all()):
            # Only zeros weighed: there was nothing here. That is a count, not a
            # find that would belong in the counting list.
            continue
        erwartung = erwartungen.get((position.ort_id, position.artikel_id))
        artikel = position.artikel.konkret()
        # Only for weighable goods is the scale a question of the location. A
        # recipe article has nothing else, with it its weighing is expected too.
        gewogen = position.gebinde_id is None and isinstance(artikel, Hausartikel)
        if erwartung is not None and (
            (gewogen and erwartung.wiegen_erwartet)
            or (position.gebinde_id is None and not gewogen)
            or any(v.gebinde_id == position.gebinde_id for v in erwartung.gebinde_vorgaben.all())
        ):
            # Expected by now: someone has already extended the counting list.
            continue

        # Discontinued or not countable is expected nowhere any more. The quantity
        # goes into the result, the row gets no counting list any more.
        if isinstance(artikel, Hausartikel) and artikel.ist_ausgeschieden:
            continue
        hinderung = ""
        if isinstance(artikel, Rezepturartikel) and not artikel.aufloesbar_in(inventur.haus_id):
            hinderung = _(
                "Eine Rezepturkomponente hat in diesem Haus keinen geführten Hausartikel mehr."
            )
        neue.append(
            NeueErwartung(
                ort=position.ort,
                artikel=artikel,
                gebinde=position.gebinde,
                menge=_menge_text(position, artikel, menge),
                artikel_schon_erwartet=erwartung is not None,
                hinderung=hinderung,
                gewogen=gewogen,
            )
        )
    return neue


def _menge_text(position, artikel, menge: Decimal) -> str:
    if position.gebinde_id is not None:
        return f"{menge_kurz(position.eingabemenge)} × {position.gebinde.anzeigename}"
    # For the house article the house's unit, the quantity is in it.
    einheit = artikel.basiseinheit
    text = f"{menge_kurz(einheit.runden(menge))} {einheit.kurz}"
    return _("%(menge)s gewogen") % {"menge": text} if position.wird_gewogen else text


def _ungezaehlte_gebinde(positionen, erwartungen) -> list[UngezaehltesGebinde]:
    """Expected rows that stayed empty, pack by pack, the scale as its own entry.

    Only expected rows: an unexpected one empty is simply nothing. A scale that
    stayed empty counts when the expectation asked for weighing here.
    """
    ungezaehlt = []
    for position in positionen:
        if position.unerwartet:
            continue
        erwartung = erwartungen.get((position.ort_id, position.artikel_id))
        if erwartung is None:
            continue
        if position.gebinde_id is None:
            if _waage_blieb_leer(position, erwartung):
                ungezaehlt.append(
                    UngezaehltesGebinde(
                        None,
                        ort=position.ort,
                        artikel=position.artikel.konkret(),
                        in_der_app_geloescht=not position.aktiv,
                        erwartung=erwartung,
                    )
                )
            continue
        # A deleted row does not count, even if a number is still in it.
        if position.aktiv and position.eingabemenge is not None:
            continue
        vorgabe = next(
            (v for v in erwartung.gebinde_vorgaben.all() if v.gebinde_id == position.gebinde_id),
            None,
        )
        if vorgabe is None:
            continue
        # Location and article come from the position. They are already loaded there.
        ungezaehlt.append(
            UngezaehltesGebinde(
                vorgabe,
                ort=position.ort,
                artikel=position.artikel.konkret(),
                in_der_app_geloescht=not position.aktiv,
            )
        )
    return ungezaehlt


def _waage_blieb_leer(position, erwartung) -> bool:
    """An expected weighing row without a weighing, only where weighing can be deselected.

    That is weighable goods that **also have a pack** here. Without a pack the
    weighing is the article's only row at this location, and taking it out
    would take the article out of the count list. That is done in the count
    list itself. No block asks it: the fourth one only lists an article that
    was counted nowhere, and it offers inactive, discontinue or another type.
    Only an active pack counts: an inactive one gets no row in the next
    inventory ('Inventur.zaehlpositionen_anlegen'), and without the scale the
    article would have none left there.
    """
    if not erwartung.wiegen_erwartet or not any(
        vorgabe.gebinde.aktiv for vorgabe in erwartung.gebinde_vorgaben.all()
    ):
        return False
    if not isinstance(position.artikel.konkret(), Hausartikel) or not position.wird_gewogen:
        return False
    return not (position.aktiv and position.wiegungen.all())


def _ungezaehlte_artikel(
    zeilen: list[Ergebniszeile], festgelegt: dict[int, Artentscheidung]
) -> list[UngezaehlterArtikel]:
    """Not counted at all: set inactive, discontinue, or switch into another type.

    A switch asks, as on the article's page, whether it applies to the
    article or to its whole product.
    """
    gefragt = [
        zeile
        for zeile in zeilen
        if not zeile.gezaehlt and not zeile.soll_statt_ist and zeile.hausartikel.aktiv
    ]
    if not gefragt:
        return []
    alle_arten = list(Inventurart.objects.all())
    anzahl = weitere_je_ware(zeile.hausartikel.ware_id for zeile in gefragt)
    eintraege = []
    for zeile in gefragt:
        artikel = zeile.hausartikel
        eintraege.append(
            UngezaehlterArtikel(
                hausartikel=artikel,
                sollbestand=zeile.sollbestand,
                andere_arten=[art for art in alle_arten if art.pk != artikel.inventurart_id],
                weitere=_weitere(anzahl, artikel),
                festgelegt=_noch_so(festgelegt, artikel),
            )
        )
    return eintraege


def _gezaehlt_ohne_soll(inventur: Inventur, zeilen: list[Ergebniszeile]) -> list[GezaehltOhneSoll]:
    """Counted, but without target.

    Only if a target stock exists at all, from the file or by hand. Without
    one no article has a target, and the question would be on every one.
    "Counted" as everywhere: also through a recipe, also an entered zero.
    """
    if not inventur.sollbestaende.exists():
        return []
    return [
        GezaehltOhneSoll(hausartikel=zeile.hausartikel, ist_text=zeile.ist_text)
        for zeile in zeilen
        if zeile.gezaehlt and zeile.sollbestand is None
    ]


def _ausserhalb(
    inventur: Inventur,
    positionen,
    zeilen: list[Ergebniszeile],
    festgelegt: dict[int, Artentscheidung],
) -> list[GezaehltAusserhalb]:
    """Counted themselves, although no type of this inventory counts them.

    The counterpart of the switch in the fourth block: an article of another
    type gets no row here, but can be added in the app. Only own rows, not
    through a recipe: the collection cup that rides along as an ingredient of
    a monthly counted portion is therefore not yet monthly goods, and the
    question would otherwise reappear every month. Inactive ones are here
    nonetheless: whether the row becomes active again is asked by the first
    block.
    """
    selbst_gezaehlt = {
        position.artikel_id
        for position in positionen
        if position.aktiv and position.menge_in_basiseinheit() is not None
    }
    umfang = inventur.zaehlumfang
    gezaehlt = [
        zeile
        for zeile in zeilen
        if zeile.hausartikel.pk in selbst_gezaehlt and not umfang.umfasst(zeile.hausartikel)
    ]
    if not gezaehlt:
        return []
    ziele = list(inventur.arten())
    anzahl = weitere_je_ware(zeile.hausartikel.ware_id for zeile in gezaehlt)
    return [
        GezaehltAusserhalb(
            hausartikel=zeile.hausartikel,
            ist_text=zeile.ist_text,
            ziele=ziele,
            weitere=_weitere(anzahl, zeile.hausartikel),
            festgelegt=_noch_so(festgelegt, zeile.hausartikel),
        )
        for zeile in gezaehlt
    ]


def _fremde_behaelter(inventur: Inventur, positionen) -> list[FremderBehaelter]:
    """Where weighing happened in a container that belongs neither to the article nor to the house.

    One question per article and container, no matter at how many locations.
    Assigned is the product, not the row. The weighings are already prefetched
    on the positions. Only the assignments of the found containers are queried.
    """
    gefunden: dict[tuple[str, int, int], FremderBehaelter] = {}
    for position in positionen:
        if not position.aktiv:
            continue
        artikel = position.artikel.konkret()
        traeger = artikel.ware if isinstance(artikel, Hausartikel) else artikel
        for wiegung in position.wiegungen.all():
            # A 0 means "nothing to weigh". Which vessel was chosen says
            # nothing about the assignment.
            if wiegung.behaelter.immer_erlaubt or not wiegung.brutto_gramm:
                continue
            schluessel = (type(traeger).__name__, traeger.pk, wiegung.behaelter_id)
            if schluessel not in gefunden:
                gefunden[schluessel] = FremderBehaelter(traeger, wiegung.behaelter, "", 0)
            gefunden[schluessel].wiegungen += 1
    if not gefunden:
        return []

    behaelter = {
        satz.pk: satz
        for satz in Behaelter.objects.filter(
            pk__in={eintrag.behaelter.pk for eintrag in gefunden.values()}
        ).prefetch_related("haeuser", "waren", "rezepturartikel")
    }
    fremde = []
    for eintrag in gefunden.values():
        satz = behaelter[eintrag.behaelter.pk]
        haeuser = list(satz.haeuser.all())
        zugeordnet = (
            satz.waren.all() if isinstance(eintrag.traeger, Ware) else satz.rezepturartikel.all()
        )
        if eintrag.traeger in zugeordnet or inventur.haus in haeuser:
            continue
        eintrag.haeuser = ", ".join(sorted(haus.name for haus in haeuser))
        fremde.append(eintrag)
    return sorted(fremde, key=lambda e: (e.traeger.name, str(e.behaelter.anzeigename)))
