"""The master data of an inventory, as the counting app stores it for offline use.

No serializers: the article list arises from a few queries as finished
dictionaries. A serializer per article would query per row.
"""

from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Q

from katalog.models import (
    GEFUEHRT,
    Artikelstatus,
    Behaelter,
    Hausartikel,
    Inventurart,
    Rezepturartikel,
    Zaehlumfang,
)

#: Three places, like every quantity of the API ('serializers.zahl').
GEBINDE_STELLEN = Decimal("0.001")


def ware_name(artikel) -> tuple[str, str]:
    """Display name and the house's designation. Both are on the article.

    Displayed is the **product**. The house's designation rides along for the
    check list. Unlike 'Artikel.name_im_haus' it comes here even when it
    matches the product name. The check list puts both columns side by side,
    and an empty one would look like an error.
    """
    konkret = artikel.konkret()
    if isinstance(konkret, Hausartikel):
        return konkret.ware.name, konkret.name
    # A recipe article belongs to no house and has only its own name.
    return konkret.name, ""


def einheit_von(artikel):
    """The base unit this article is counted in.

    It is not on the article core. For the house article it is the house's
    unit: in it the target comes in, in it the result goes back. For the
    recipe article it is its own.
    """
    return artikel.konkret().basiseinheit


def _behaelter_ids(artikel, ohne) -> list[int]:
    """The assigned containers plus "without container". Come from the prefetch, without a query."""
    ids = [behaelter.pk for behaelter in artikel.behaelter.all()]
    if ohne.pk not in ids:
        ids.append(ohne.pk)
    return ids


#: How a container relates to the inventory's house. The app groups its selection by it.
IM_HAUS = "dieses"
ANDERSWO = "anderes"


def behaelter_des_hauses(haus) -> list[dict]:
    """The containers that can go on the scale in this house, including their tare.

    The app offers them in three groups, in this order: what is **assigned** to
    the article (its 'behaelter' list, plus "Without container"), then the
    containers of **this house**, finally those of **other houses**: the bowl one
    borrows. 'haus' says which of the two latter groups a container belongs to.
    Empty means: it has no house and stands only with its articles. The server
    checks the same rule when weighing ('Behaelter.zur_wahl_fuer').

    All containers, not only those with a house: which ones an article has
    assigned only its entry knows, and there are a few dozen.
    """
    eintraege = []
    for behaelter in Behaelter.objects.prefetch_related("haeuser"):
        haeuser = {h.pk for h in behaelter.haeuser.all()}
        lage = IM_HAUS if haus.pk in haeuser else ANDERSWO if haeuser else ""
        eintraege.append(
            {
                "id": behaelter.pk,
                "name": str(behaelter.anzeigename),
                "tara": behaelter.tara,
                "haus": lage,
            }
        )
    return eintraege


def artikel_des_hauses(haus, umfang: Zaehlumfang) -> list[dict]:
    """Everything that can be counted in this house, for the offline storage.

    House articles of the house that are active, and the recipe articles whose
    recipe can be resolved here. The check runs against the sets of the active
    and the carried products of the house instead of through
    'Rezepturartikel.verfuegbar_in' per article. Otherwise each recipe article
    would add a query.

    Plus **inactive** house articles of a type this inventory counts: the
    shelf sometimes still holds seasonal goods that are
    currently not in the range, and they should be countable. They can be
    reactivated before closing. They come after all active ones. Likewise a
    recipe article whose ingredient is inactive here: it counts as inactive,
    its share lands on the inactive row when resolving, and that then appears
    in the clarification. An inactive row holds number and product in the
    house, so there is no active one next to it, and nothing can appear twice.

    At the very end **discontinued** ones whose slot is still free: nobody in
    the house carries their number or their product ('Hausartikel.blockiert_durch').
    On the shelf also appears what was delisted long ago, and without them the
    only option was to take some similar article. Whose number has been
    reassigned stays out: there the new row is available, and two entries for
    the same product could not be told apart. Ignored ones the app never offers.
    """
    passt = umfang.hausartikel()
    hausartikel = (
        Hausartikel.objects.filter(haus=haus)
        .filter(
            Q(status=Artikelstatus.AKTIV)
            | (Q(status__in=[Artikelstatus.INAKTIV, Artikelstatus.EINGESTELLT]) & passt)
        )
        .select_related("basiseinheit", "ware__basiseinheit")
        .prefetch_related("ware__gebinde", "ware__behaelter")
        .order_by("name")
    )

    # Fetch "Without container" once instead of via 'waehlbare_behaelter()' per
    # product. The method queries every time and would give a query per article.
    ohne = Behaelter.ohne_behaelter()

    eintraege = []
    aktive_waren = set()
    gefuehrte_waren = set()
    eingestellte = []
    for artikel in hausartikel:
        if artikel.status == Artikelstatus.EINGESTELLT:
            eingestellte.append(artikel)
            continue
        gefuehrte_waren.add(artikel.ware_id)
        if artikel.aktiv:
            aktive_waren.add(artikel.ware_id)
        eintraege.append(_hausartikel_eintrag(artikel, ohne))
    eintraege.extend(
        _hausartikel_eintrag(artikel, ohne) for artikel in _mit_freiem_platz(haus, eingestellte)
    )

    eintraege.extend(_rezepturartikel_eintraege(haus, umfang, aktive_waren, gefuehrte_waren, ohne))
    return sorted(
        eintraege,
        key=lambda eintrag: (not eintrag["aktiv"], eintrag["eingestellt"], eintrag["name"]),
    )


def _rezepturartikel_eintraege(haus, umfang, aktive_waren, gefuehrte_waren, ohne) -> list[dict]:
    """The recipe articles the house can resolve, with the tab of their first due ingredient.

    An ingredient's types are those of the articles the house carries for it,
    usually one. One query for all instead of one per recipe article. A recipe
    article with an inactive ingredient counts as inactive and is offered
    only if one ingredient is due, like an inactive house article.
    """
    folge = {
        pk: stelle for stelle, pk in enumerate(Inventurart.objects.values_list("pk", flat=True))
    }
    arten_je_ware: dict[int, set[int]] = {}
    for ware_id, art_id in Hausartikel.objects.filter(haus=haus, status__in=GEFUEHRT).values_list(
        "ware_id", "inventurart_id"
    ):
        arten_je_ware.setdefault(ware_id, set()).add(art_id)
    rezepturartikel = Rezepturartikel.objects.select_related("basiseinheit").prefetch_related(
        "komponenten", "behaelter"
    )
    eintraege = []
    for artikel in rezepturartikel:
        zutaten = list(artikel.komponenten.all())
        komponenten = {k.ware_id for k in zutaten}
        if not komponenten or not komponenten <= gefuehrte_waren:
            continue
        aktiv = komponenten <= aktive_waren
        arten = sorted(
            set().union(*(arten_je_ware[ware] for ware in komponenten)),
            key=lambda art: folge[art],
        )
        reiter = umfang.reiter(arten)
        if not aktiv and reiter is None:
            continue
        eintraege.append(
            {
                "id": artikel.pk,
                "name": artikel.name,
                "name_im_haus": "",
                "artikelnummer": "",
                "einheit": artikel.basiseinheit.kennung,
                "wird_gewogen": artikel.wird_gewogen,
                "ist_rezepturartikel": True,
                "aktiv": aktiv,
                "eingestellt": False,
                "inventurart": reiter if reiter is not None else arten[0],
                "gebinde": [],
                "behaelter": _behaelter_ids(artikel, ohne) if artikel.wird_gewogen else [],
            }
        )
    return eintraege


def _mit_freiem_platz(haus, eingestellte: list) -> list:
    """The discontinued rows that could be reactivated: 'blockiert_durch' for all.

    Against **all** carried rows of the house, not only those of the list: an
    inactive one not due in this inventory holds number and product just the
    same. Asking per row would cost a query per discontinued article.

    If two discontinued rows of the same product or number are free, only the
    one discontinued last comes: only one can come back anyway, and in the
    search they would appear under the same name.
    """
    if not eingestellte:
        return []
    belegt = Hausartikel.objects.filter(haus=haus, status__in=GEFUEHRT).values_list(
        "artikelnummer", "ware_id"
    )
    # The empty number of the own articles occupies nothing.
    nummern = {nummer for nummer, _ in belegt if nummer}
    waren = {ware for _, ware in belegt}
    frei = []
    for artikel in sorted(eingestellte, key=lambda a: (a.gueltig_bis, a.pk), reverse=True):
        if (artikel.artikelnummer and artikel.artikelnummer in nummern) or artikel.ware_id in waren:
            continue
        if artikel.artikelnummer:
            nummern.add(artikel.artikelnummer)
        waren.add(artikel.ware_id)
        frei.append(artikel)
    return frei


def _hausartikel_eintrag(artikel, ohne) -> dict:
    ware = artikel.ware
    return {
        "id": artikel.pk,
        "name": ware.name,
        "name_im_haus": artikel.name,
        "artikelnummer": artikel.artikelnummer,
        # The house's unit, and the packs in it: the app shows "Crate of 20 pcs",
        # and that has to fit the unit it counts in.
        "einheit": artikel.basiseinheit.kennung,
        "wird_gewogen": ware.abwiegbar,
        "ist_rezepturartikel": False,
        "aktiv": artikel.aktiv,
        "eingestellt": artikel.status == Artikelstatus.EINGESTELLT,
        "inventurart": artikel.inventurart_id,
        "gebinde": [gebinde_im_haus(artikel, g) for g in ware.gebinde.all() if g.aktiv],
        "behaelter": _behaelter_ids(ware, ohne) if ware.abwiegbar else [],
    }


def gebinde_im_haus(artikel, gebinde) -> dict:
    """A pack of the product, its quantity in the house's unit.

    For the master data and for every row ('ZaehlpositionSerializer'): both
    stand next to the house's unit. The quantity is on the pack in the
    product's unit. If the house counts differently, the app would see "Box
    of 16 g" next to a product in kg.
    Three places, as every quantity of the API, not the unit's: a house in
    pieces with a box of 19.2 pieces read "Box of 19" until 10/2026. The
    app only shows the number, the server computes with the exact one.
    """
    return {
        "id": gebinde.pk,
        "name": gebinde.name,
        "ist_einzeln": gebinde.ist_einzeln,
        "faktor_in_basiseinheit": artikel.in_hauseinheit(gebinde.faktor_in_basiseinheit).quantize(
            GEBINDE_STELLEN, rounding=ROUND_HALF_UP
        ),
        "sortierung": gebinde.sortierung,
    }
