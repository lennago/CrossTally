"""Make two products one, including their packs.

The import creates separate products per house for deviating designations.
Here they are united again. The difficult part is the **packs**: they hang
on the product and may carry neither the same name nor the same quantity
twice there.

That both carry a "Flasche" is the normal case and no problem, because it
is the same product. Compared is therefore **name and quantity**:

- **both equal**: the same pack, it merges without asking.
- **same quantity, different name**: also the same pack, only named
  differently. Here someone has to say which name stays.
- **same name, different quantity**: precisely **not** the same. The two
  must not merge, otherwise counted quantities would shift. One of the two
  needs a new name.

An active pack that would merge into an **inactive** one of the target is
asked about as well: inactive packs get no row in new inventories, so the
source's places would count nothing there any more (decided 2026-10-20, as
the house article form asks about an inactive pack).

What merges takes its references along: location defaults, count positions
and the places a discontinued or ignored row remembers point to the pack
that stays afterwards.

If the two carry different units, a **factor** converts the source into the
target's unit before comparing: pack quantities, the quantities in recipe
rows and the conversion of every house. Within a dimension the units know
it, across dimensions the form says it. The houses of the source keep
counting in their unit afterwards, since 09/2026 that hangs on the house
article. A share in percent stays as it is: it goes through the weight per
unit of the product that stays, so that one has to know it
('WareZusammenfuehrenForm.fehlendes_gewicht').

A house that carries **both** products has the same thing twice, under two
numbers ('Dublette'). The row of the product that stays stays. That of the
absorbed product is discontinued and counts as it from then on
('Hausartikel.zaehlt_als'): its target stock is added to the staying row when
read in. Which product stays is chosen once, at the top of the form, and that
answers the question for every house. Nothing changes about the staying row:
inactive or discontinued, it stays so. Expectations go over where someone
ticks them. Where the staying row is expected at the same location already,
packs and expected quantities are added up.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Prefetch
from django.utils import timezone

from django_project.labels import Beschriftung
from organisation.models import Haus

from .models import (
    GEFUEHRT,
    Artikelstatus,
    Einheitenkennung,
    Erwartung,
    ErwartungGebinde,
    Gebinde,
    Hausartikel,
    Rezepturkomponente,
    Ware,
    artikel_sperren,
    menge_kurz,
    neue_art_ans_ende,
    plaetze_vereinen,
    umrechnung,
    zaehlpositionen_sperren,
)
from .ware_unit_change import (
    FAKTOR_STELLEN,
    GEBINDE_STELLEN,
    _gebinde_umrechnen,
    _rezepturen_umrechnen,
    haeuser_pruefen,
)

#: Both entries equal: the same pack, nothing to decide.
GLEICH = "gleich"
#: Same quantity under another name: it merges, but which name stays?
NAMENSWAHL = "namenswahl"
#: Same name, different quantity: two remain, one has to be renamed.
UMBENENNEN = "umbenennen"


@dataclass
class Gebindepaar:
    """A pack of the article to be absorbed and its counterpart in the target."""

    quelle: Gebinde
    ziel: Gebinde | None
    art: str

    @property
    def kennung(self) -> str:
        """The field name in the form: the number of the source pack."""
        return str(self.quelle.pk)

    @property
    def braucht_entscheidung(self) -> bool:
        return self.art in (NAMENSWAHL, UMBENENNEN) or self.landet_inaktiv

    @property
    def landet_inaktiv(self) -> bool:
        """An active pack of the source would merge into an inactive one of the target.

        Its location defaults and running rows would then hang on a pack that
        gets no row in new inventories. Until 10/2026 the preview said only
        "same pack, merges", and the next inventory had no row for it. The
        person decides: bring it back, in every house, or leave it inactive.
        """
        return (
            self.ziel is not None
            and self.art in (GLEICH, NAMENSWAHL)
            and self.quelle.aktiv
            and not self.ziel.aktiv
        )

    #: The source's quantity in the target's unit. With the same unit the same.
    menge: Decimal = Decimal("0")
    #: The loose single unit of a piece good, absorbed into a product that is
    #: not counted in pieces: there it is an ordinary pack ('_gebinde_umrechnen').
    wird_gebinde: bool = False

    @property
    def vorschlag(self) -> str:
        """A name that makes the two distinguishable: the quantity added.

        "Flasche" and "Flasche" say nothing. "Flasche (0,7)" and "Flasche" do.
        It can be changed in the form anyway.
        """
        return f"{self.quelle.name} ({menge_kurz(self.menge)})"


def gebindepaare(quelle: Ware, ziel: Ware, faktor: Decimal = Decimal("1")) -> list[Gebindepaar]:
    """What would become of every pack of the source when merging.

    Compared in the target's unit: 'faktor' says how many of those one unit
    of the source is.
    """
    ziel_gebinde = list(ziel.gebinde.all())
    nach_name = {gebinde.name.casefold(): gebinde for gebinde in ziel_gebinde}
    nach_menge = {gebinde.faktor_in_basiseinheit: gebinde for gebinde in ziel_gebinde}
    stueck = ziel.basiseinheit.kennung == Einheitenkennung.STUECK

    paare = []
    for gebinde in quelle.gebinde.all().order_by("sortierung"):
        menge = (gebinde.faktor_in_basiseinheit * faktor).quantize(
            GEBINDE_STELLEN, rounding=ROUND_HALF_UP
        )
        gleicher_name = nach_name.get(gebinde.name.casefold())
        gleiche_menge = nach_menge.get(menge)
        wird_gebinde = gebinde.ist_einzeln and not stueck

        if gleicher_name is not None and gleicher_name == gleiche_menge:
            paare.append(Gebindepaar(gebinde, gleicher_name, GLEICH, menge, wird_gebinde))
        elif gleiche_menge is not None:
            paare.append(Gebindepaar(gebinde, gleiche_menge, NAMENSWAHL, menge, wird_gebinde))
        elif gleicher_name is not None:
            paare.append(Gebindepaar(gebinde, gleicher_name, UMBENENNEN, menge, wird_gebinde))
        else:
            paare.append(Gebindepaar(gebinde, None, "", menge, wird_gebinde))
    return paare


def _hausartikel_umhaengen(quelle: Ware, ziel: Ware, faktor: Decimal) -> int:
    """The houses' rows onto the target, each with its conversion to it.

    Via 'update()', not via 'save()': a change of product otherwise clears
    the defaults onto packs of the old product ('_fremde_gebinde_aufraeumen'),
    and exactly those move along here.

    The inventory type comes from the remaining product, as its name does,
    as long as its articles agree on one. Otherwise each row keeps its own:
    which of the remaining types would be right is then not to be said.

    The frozen results of the rows come along, in the unit they were frozen
    in, and convert into the target as the rows do ('ergebnisse_nachziehen').
    A conversion that would outgrow its column stops the merge before one is
    written ('haeuser_pruefen').
    """
    art = ziel.inventurart
    anzahl = 0
    artikel_der_quelle = list(quelle.hausartikel.select_related("basiseinheit", "haus"))
    haeuser_pruefen(artikel_der_quelle, quelle.basiseinheit, ziel.basiseinheit, faktor)
    for artikel in artikel_der_quelle:
        bisher = umrechnung(artikel.basiseinheit, quelle.basiseinheit, artikel.faktor_zur_ware)
        if artikel.basiseinheit.dimension == ziel.basiseinheit.dimension or bisher is None:
            neu = None
        else:
            neu = (bisher * faktor).quantize(FAKTOR_STELLEN, rounding=ROUND_HALF_UP)
        anzahl += Hausartikel.objects.filter(pk=artikel.pk).update(
            ware=ziel,
            faktor_zur_ware=neu,
            inventurart_id=art.pk if art is not None else artikel.inventurart_id,
        )
        artikel.ware, artikel.faktor_zur_ware = ziel, neu
        artikel.ergebnisse_nachziehen(quelle.basiseinheit, faktor)
    return anzahl


def verschmelzen(quelle: Gebinde, ziel: Gebinde) -> None:
    """Turn everything that points to the one pack onto the other.

    A duplicate cannot arise in the process: packs hang on the product, and a
    house article has only one. The same expectation thus cannot already
    carry both. It is checked nonetheless, so a future path at this point
    does not silently lose data.
    """
    vorhanden = set(
        ErwartungGebinde.objects.filter(gebinde=ziel).values_list("erwartung_id", flat=True)
    )
    ErwartungGebinde.objects.filter(gebinde=quelle, erwartung_id__in=vorhanden).delete()
    ErwartungGebinde.objects.filter(gebinde=quelle).update(gebinde=ziel)

    # With timestamp: 'auto_now' does not apply on 'update()', and without it
    # the counting app would keep the number of a pack that no longer exists.
    # Via the reverse relation: the catalog does not know the inventory
    # ('organisation' <- 'katalog' <- 'inventur'), and an import from there
    # would reverse the direction.
    quelle.zaehlpositionen.update(gebinde=ziel, zuletzt_geaendert=timezone.now())
    quelle.delete()


def _hinten_anhaengen(gebinde: Gebinde, ziel: Ware, stelle: int) -> None:
    """Moved packs come to the end of the list.

    The position is unique per product. The old number is almost always
    already taken in the target.
    """
    gebinde.ware = ziel
    gebinde.sortierung = stelle
    # The name belongs saved along: for a renamed pack the new one is already
    # on the row, and without it, it would run into the target's name index.
    gebinde.save(update_fields=["ware", "sortierung", "name"])


@dataclass
class Dublette:
    """A house that carries both products: the same thing under two numbers."""

    haus: Haus
    #: The carried row of the product that stays. It stays.
    ziel: Hausartikel
    #: That of the absorbed product. It is discontinued and counts as 'ziel'.
    quelle: Hausartikel

    @property
    def kennung(self) -> str:
        """The field names in the form: the number of the house."""
        return str(self.haus.pk)


def dubletten(quelle: Ware, ziel: Ware) -> list[Dublette]:
    """The houses that carry both products, with both rows and their expectations.

    Carried means active or inactive: both occupy the product in the house,
    and after merging one product could be carried there only once.
    """
    erwartungen = Erwartung.objects.select_related("ort__bereich").prefetch_related(
        "gebinde_vorgaben__gebinde"
    )
    je_haus: dict[int, dict[int, Hausartikel]] = defaultdict(dict)
    for artikel in (
        Hausartikel.objects.filter(ware__in=[quelle, ziel], status__in=GEFUEHRT)
        .select_related("haus")
        .prefetch_related(Prefetch("erwartet_an", queryset=erwartungen))
    ):
        je_haus[artikel.haus_id][artikel.ware_id] = artikel
    beide = [zeilen for zeilen in je_haus.values() if {quelle.pk, ziel.pk} <= zeilen.keys()]
    return sorted(
        (
            Dublette(haus=zeilen[ziel.pk].haus, ziel=zeilen[ziel.pk], quelle=zeilen[quelle.pk])
            for zeilen in beide
        ),
        key=lambda dublette: dublette.haus.name,
    )


@dataclass
class Zusammenlegung:
    """An expectation of the discontinued row at a location where the staying one stands too.

    Its packs are added only after the packs have merged: before that the
    two may point to two packs that become one.
    """

    erwartung: Erwartung
    #: (pack number, expected quantity) of the discontinued row.
    vorgaben: list[tuple[int, Decimal | None]] = field(default_factory=list)
    wiegen: bool = False


def _dubletten_aufloesen(
    quelle: Ware, ziel: Ware, uebernehmen: dict[str, frozenset[int]], bericht: Bericht
) -> list[Zusammenlegung]:
    """Per house the row of the absorbed product is discontinued and counts as the staying one.

    Before the rows move, because afterwards two carried rows of one product
    would stand in the house. Expectations that go over to a location without
    the staying row move as a whole, with position and packs. Where it stands
    already, what has to be added waits for the packs ('_vorgaben_addieren').
    Whatever is not taken over goes with discontinuing, as always.
    """
    offen = []
    for dublette in dubletten(quelle, ziel):
        bleibt, geht = dublette.ziel, dublette.quelle
        gewaehlt = uebernehmen.get(dublette.kennung)
        if gewaehlt is None:
            # Without a word from outside every expectation goes over.
            gewaehlt = frozenset(erwartung.pk for erwartung in geht.erwartet_an.all())

        schon_da = {erwartung.ort_id: erwartung for erwartung in bleibt.erwartet_an.all()}
        for erwartung in geht.erwartet_an.all():
            if erwartung.pk not in gewaehlt:
                continue
            bericht.erwartungen += 1
            if (vorhanden := schon_da.get(erwartung.ort_id)) is not None:
                offen.append(
                    Zusammenlegung(
                        vorhanden,
                        [
                            (v.gebinde_id, v.standard_menge)
                            for v in erwartung.gebinde_vorgaben.all()
                        ],
                        erwartung.wiegen_erwartet,
                    )
                )
                continue
            # Via 'update()': the row keeps its position and packs. Those may
            # still hang on the other product, and the merge makes them one.
            Erwartung.objects.filter(pk=erwartung.pk).update(artikel=bleibt)

        # Duplicates of an earlier merge counted as the row that now goes. They
        # count as the staying one from here on: a chain would end at a row
        # that is no longer carried, and their stock would become a question
        # with every upload ('inventur.services.Zuordner').
        Hausartikel.objects.filter(zaehlt_als=geht).update(zaehlt_als=bleibt)
        geht.zaehlt_als = bleibt
        geht.einstellen()
        bericht.eingestellt += 1
    return offen


def _ignorierte_paare(quelle: Ware, ziel: Ware) -> list[tuple[int, int]]:
    """Two products in "Not countable" may each remember a carried row in one house.

    Back out, both would be carried, and the house would carry the same
    product twice. As with carried duplicates the row of the absorbed
    product counts as the staying one from now on and comes back
    discontinued ('_dubletten_aufloesen'). Ignored rows have no expectations
    any more, so nothing has to be asked. Until 10/2026 the way back ran into
    the unique index and ended on the error page.

    Back come the pairs (staying, absorbed): the remembered places of the
    absorbed row go to the staying one once the packs have merged
    ('_plaetze_uebernehmen').
    """
    erinnert = Hausartikel.objects.filter(
        ware__in=[quelle, ziel], status=Artikelstatus.IGNORIERT, status_vorher__in=GEFUEHRT
    )
    je_haus: dict[int, dict[int, Hausartikel]] = defaultdict(dict)
    for artikel in erinnert:
        je_haus[artikel.haus_id][artikel.ware_id] = artikel
    paare = []
    for zeilen in je_haus.values():
        if not {quelle.pk, ziel.pk} <= zeilen.keys():
            continue
        bleibt, geht = zeilen[ziel.pk], zeilen[quelle.pk]
        Hausartikel.objects.filter(zaehlt_als=geht).update(zaehlt_als=bleibt)
        Hausartikel.objects.filter(pk=geht.pk).update(
            status_vorher=Artikelstatus.EINGESTELLT, zaehlt_als=bleibt
        )
        paare.append((bleibt.pk, geht.pk))
    return paare


def _plaetze_uebernehmen(bleibt: int, geht: int) -> None:
    """The remembered places of the absorbed ignored row go to the staying one.

    The absorbed row comes back discontinued, and a discontinued row comes
    back without places ('Hausartikel._plaetze_wiederherstellen'). As the
    expectations of a carried duplicate go over ('_dubletten_aufloesen'),
    its places come along on the staying row's way back. Until 10/2026 they
    were lost. Its packs are already those of the target.
    """
    erinnert = dict(
        Hausartikel.objects.filter(pk__in=[bleibt, geht]).values_list("pk", "erwartungen_vorher")
    )
    if not erinnert.get(geht):
        return
    vereint = plaetze_vereinen(erinnert.get(bleibt) or [], erinnert[geht])
    Hausartikel.objects.filter(pk=bleibt).update(erwartungen_vorher=vereint)
    Hausartikel.objects.filter(pk=geht).update(erwartungen_vorher=[])


def _vorgaben_addieren(offen: list[Zusammenlegung], abbild: dict[int, int]) -> None:
    """The packs of the discontinued row onto the staying one's expectation at the same location.

    The same pack twice is the same thing twice: the expected quantities add
    up. 'abbild' says which pack became which when merging.
    """
    for zusammenlegung in offen:
        erwartung = zusammenlegung.erwartung
        bestehend = {v.gebinde_id: v for v in ErwartungGebinde.objects.filter(erwartung=erwartung)}
        for bisher, menge in zusammenlegung.vorgaben:
            gebinde_id = abbild.get(bisher, bisher)
            vorgabe = bestehend.get(gebinde_id)
            if vorgabe is None:
                bestehend[gebinde_id] = ErwartungGebinde.objects.create(
                    erwartung=erwartung, gebinde_id=gebinde_id, standard_menge=menge
                )
            elif menge is not None:
                vorgabe.standard_menge = (vorgabe.standard_menge or Decimal("0")) + menge
                vorgabe.save(update_fields=["standard_menge"])
        if zusammenlegung.wiegen and not erwartung.wiegen_erwartet:
            Erwartung.objects.filter(pk=erwartung.pk).update(wiegen_erwartet=True)


@dataclass
class Bericht:
    """What happened, for the message after merging."""

    hausartikel: int = 0
    rezepturzeilen: int = 0
    umgezogen: int = 0
    verschmolzen: int = 0
    #: Inactive packs of the target an answer brought back, by name.
    reaktiviert: list[Beschriftung] = field(default_factory=list)
    #: Inactive packs of the target that stay inactive with what merged into them.
    inaktiv: list[Beschriftung] = field(default_factory=list)
    #: Duplicates discontinued, one per house that carried both.
    eingestellt: int = 0
    #: Expectations of the discontinued rows taken over.
    erwartungen: int = 0


@transaction.atomic
@neue_art_ans_ende()
def zusammenfuehren(
    quelle: Ware,
    ziel: Ware,
    *,
    namen: dict[str, str] | None = None,
    entscheidungen: dict[str, str] | None = None,
    faktor: Decimal = Decimal("1"),
    uebernehmen: dict[str, frozenset[int]] | None = None,
    aktivieren: frozenset[int] = frozenset(),
) -> Bericht:
    """Absorbs 'quelle' into 'ziel'. The source vanishes.

    'namen' carries the new designations of the packs that have to be renamed
    (key is the number of the source pack as text), 'entscheidungen' for the
    same quantity under another name which of the two names stays ('quelle'
    or 'ziel'). 'faktor' says how many units of the target one unit of the
    source is ('umrechnungsfaktor'). 'uebernehmen' says per house that carries
    both (key is the number of the house as text) which expectations of the
    discontinued row go over. A house it does not name takes all of them.
    'aktivieren' names the inactive packs of the target that an active one
    merges into and that come back active ('Gebindepaar.landet_inaktiv'). The
    others stay inactive.

    An article that takes on the type of the target goes to the end of it in
    the counting lists ('neue_art_ans_ende').

    The count positions of running inventories that hang on the source, by
    article or by pack, are locked first and touched last: their product,
    their pack or its name changes, and the app has to fetch them anew
    ('zaehlpositionen_sperren'). So are those on a pack of the target that
    takes over the source's name. Until 10/2026 they kept their timestamp,
    and the app showed the old name. A row the app creates on such a pack
    locks its article, one of the target's ('api.serializers.counting.gesperrter_artikel').
    Locked here before the rows, with the source's in one call by number,
    it waits and reads the new name, or it is among the rows locked. Until
    10/2026 a row created meanwhile kept the old name.
    """
    namen = namen or {}
    entscheidungen = entscheidungen or {}
    bericht = Bericht()
    paare = gebindepaare(quelle, ziel, faktor)
    umbenannt = [
        paar.ziel
        for paar in paare
        if paar.ziel is not None
        and paar.art == NAMENSWAHL
        and entscheidungen.get(paar.kennung) == "quelle"
    ]
    artikel = list(quelle.hausartikel.all())
    if umbenannt:
        artikel_sperren(
            [*(satz.pk for satz in artikel), *ziel.hausartikel.values_list("pk", flat=True)]
        )
    # One call, so all rows are locked in one order, by number.
    zeilen = zaehlpositionen_sperren([*artikel, *quelle.gebinde.all(), *umbenannt])

    zusammenlegen = _dubletten_aufloesen(quelle, ziel, uebernehmen or {}, bericht)
    ignorierte = _ignorierte_paare(quelle, ziel)
    umgezogen = list(quelle.hausartikel.values_list("pk", flat=True))
    bericht.hausartikel = _hausartikel_umhaengen(quelle, ziel, faktor)
    if faktor != 1 or quelle.basiseinheit_id != ziel.basiseinheit_id:
        # First into the target's unit, then move. The errors in the process
        # name the target's unit, because the quantities would stand in it.
        # With a factor of one across dimensions (a piece is a litre) the
        # quantities stay, but the loose single unit still has to become a
        # pack: litres know none.
        _gebinde_umrechnen(quelle, faktor, ziel.basiseinheit)
        _rezepturen_umrechnen(quelle, faktor, ziel.basiseinheit)
    # Past the form: the status of the re-attached rows follows the target.
    ziel.hausartikel_angleichen()
    bericht.rezepturzeilen = Rezepturkomponente.objects.filter(ware=quelle).update(ware=ziel)

    naechste = max((gebinde.sortierung for gebinde in ziel.gebinde.all()), default=0) + 1
    #: Which pack of the source became which of the target.
    abbild: dict[int, int] = {}
    for paar in paare:
        if paar.ziel is not None and paar.art in (GLEICH, NAMENSWAHL):
            if paar.art == NAMENSWAHL and entscheidungen.get(paar.kennung) == "quelle":
                paar.ziel.name = paar.quelle.name
                paar.ziel.save(update_fields=["name"])
            if paar.landet_inaktiv:
                if paar.ziel.pk in aktivieren:
                    paar.ziel.aktiv_setzen(True)
                    bericht.reaktiviert.append(paar.ziel.anzeigename)
                else:
                    bericht.inaktiv.append(paar.ziel.anzeigename)
            abbild[paar.quelle.pk] = paar.ziel.pk
            verschmelzen(paar.quelle, paar.ziel)
            bericht.verschmolzen += 1
            continue

        if paar.art == UMBENENNEN:
            paar.quelle.name = namen[paar.kennung]
        _hinten_anhaengen(paar.quelle, ziel, naechste)
        naechste += 1
        bericht.umgezogen += 1

    _vorgaben_addieren(zusammenlegen, abbild)
    # The way back of a discontinued or ignored row finds the pack its own merged into.
    Hausartikel.plaetze_umschreiben(Hausartikel.objects.filter(pk__in=umgezogen), gebinde=abbild)
    for bleibt, geht in ignorierte:
        _plaetze_uebernehmen(bleibt, geht)
    quelle.delete()
    if zeilen is not None:
        # 'auto_now' does not apply on 'update()'.
        zeilen.update(zuletzt_geaendert=timezone.now())
    return bericht
