"""Step 1 of the import: plan without writing."""

from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal

import pandas as pd
from django.db.models import Count
from django.utils.translation import gettext as _

from django_project.labels import Beschriftung
from organisation.models import Haus

from ..formats import (
    NAME_LAENGE,
    NUMMER_LAENGE,
    STANDARD,
    Format,
    Zeilenmeldungen,
    format_holen,
    zitat,
)
from ..models import (
    EINZELN_NAME,
    GEBINDENAME_LAENGE,
    GEFUEHRT,
    OHNE_WARENGRUPPE,
    WARENGRUPPENNAME_LAENGE,
    Artikelstatus,
    Basiseinheit,
    Einheitenkennung,
    Gebinde,
    Hausartikel,
    Ware,
    braucht_faktor,
    einheit_kurz,
    menge_kurz,
    umrechnung,
)
from .base import (
    AKTIV,
    BEKANNT,
    EINGESTELLT,
    EINHEIT,
    EINSTELLEN,
    FEHLT,
    GEBINDE,
    GEBINDE_STELLEN,
    IGNORIEREN,
    KONFLIKT,
    NEU,
    UEBERGEHEN,
    UMBENANNT,
    WARENGRUPPE,
    Planzeile,
    _faktor,
    _text,
    freier_gebindename,
    menge_text,
)


class Namensregister:
    """Finds a product by its designation.

    First exactly, then regardless of case: an inventory management system
    does not always write the same product the same way, and whoever imports
    "COLA 0,33" means the existing "Cola 0,33".

    The whole stock is loaded instead of a query with the names from the
    file. On the first import those are over a thousand values in an 'IN',
    and a comparison in memory is cheaper anyway with a few hundred products.

    Also found are the products from "Not countable". That way the import
    recognises deposit and vouchers in every house as soon as one has ignored
    them.
    """

    def __init__(self):
        self.exakt: dict[str, int] = {}
        self.unscharf: dict[str, int] = {}
        #: The products from "Not countable".
        self.ignoriert: set[int] = set()
        #: For hints that name a found product.
        self.namen: dict[int, str] = {}
        #: The unit per product. If the house counts differently, the plan needs the conversion.
        self.einheit: dict[int, Basiseinheit] = {}
        einheiten = {einheit.pk: einheit for einheit in Basiseinheit.objects.all()}
        waren = Ware.objects.values_list(
            "pk", "name", "warenfamilie__sammelt_ignorierte", "basiseinheit_id"
        ).order_by("pk")
        for pk, name, ignoriert, einheit_id in waren:
            self.namen[pk] = name
            self.einheit[pk] = einheiten[einheit_id]
            self.exakt[name] = pk
            # The oldest row wins, so the choice stays reproducible.
            self.unscharf.setdefault(name.lower(), pk)
            if ignoriert:
                self.ignoriert.add(pk)

    def finden(self, name: str) -> int | None:
        return self.exakt.get(name) or self.unscharf.get(name.lower())


def einordnen(name: str, saetze: list[Hausartikel]) -> tuple[str, Hausartikel | None]:
    """How a row relates to what the house already has under its number.

    The yardstick is the model's uniqueness ('Hausartikel.pruefe_eindeutig'):
    house, number and designation together exist only once, in every status.
    If number and designation match, it **is** this row. Discontinued, it can
    be brought back, otherwise it is known. Only if none is called so does
    the partial index count: if the house carries the number under another
    name, it is renamed or reassigned. Everything else is new.

    Plan and form both ask here: 'art' and 'vorhanden_pk' come from outside
    on submit and are measured against this.
    """
    gleich = next((satz for satz in saetze if satz.name == name), None)
    if gleich is not None:
        return (EINGESTELLT if gleich.status == Artikelstatus.EINGESTELLT else BEKANNT), gleich
    gefuehrt = next((satz for satz in saetze if satz.status in GEFUEHRT), None)
    if gefuehrt is not None:
        return UMBENANNT, gefuehrt
    return NEU, None


def saetze_je_nummer(haus: Haus, nummern) -> dict[str, list[Hausartikel]]:
    """What the house has for these numbers, in every status and in one query.

    Asking per row cost over a thousand queries on the first import of a
    house.
    """
    saetze: dict[str, list[Hausartikel]] = defaultdict(list)
    for artikel in (
        Hausartikel.objects.filter(haus=haus, artikelnummer__in=nummern)
        .select_related("basiseinheit", "ware__basiseinheit", "warengruppe")
        .prefetch_related("ware__gebinde")
        .order_by("pk")
    ):
        saetze[artikel.artikelnummer].append(artikel)
    return saetze


def planen(
    tabelle: pd.DataFrame, haus: Haus, format: Format | None = None
) -> tuple[list[Planzeile], list[str]]:
    """Says for every row of the file what would happen. Writes nothing.

    Back come the plan and the messages about rows that do not even get into
    it, subtotals for instance that carry no article number. Without this
    list they would vanish without comment.

    Plus what is **not** in the file: see '_fehlende_ergaenzen'.

    Into the plan comes only what needs a decision. What the house already
    carries under number **and** designation and where the file says nothing
    different stays out. On the second import that would otherwise be over a
    thousand rows among which the ten new ones go under. Likewise what is
    already not countable there: exactly for that "Not countable" exists. Of
    both a message with the count remains.
    """
    format = format or format_holen(STANDARD)
    gruppen, meldungen = _zeilen_gruppieren(tabelle, format)
    saetze = saetze_je_nummer(haus, gruppen)
    # Which product the house carries under which number, for the partial index.
    belegt = dict(
        Hausartikel.objects.filter(haus=haus, status__in=GEFUEHRT).values_list(
            "ware_id", "artikelnummer"
        )
    )
    register = Namensregister()
    einheiten = {einheit.kennung: einheit for einheit in Basiseinheit.objects.all()}

    plan = []
    nicht_zaehlbar = 0
    unveraendert = 0
    dubletten = 0
    #: Products an earlier row of this plan already occupies as carried.
    vergeben: dict[str, str] = {}
    for nummer, zeilen in gruppen.items():
        art, alt = einordnen(zeilen[0]["name"], saetze.get(nummer, []))
        if alt is not None and alt.status == Artikelstatus.IGNORIERT:
            nicht_zaehlbar += 1
            continue
        satz = _planzeile(nummer, zeilen, register)
        if alt is not None and art == BEKANNT:
            if not _als_konflikt(satz, alt, zeilen, einheiten):
                unveraendert += 1
                continue
        elif alt is not None and art == EINGESTELLT:
            if alt.zaehlt_als_id is not None:
                # Discontinued on purpose as a duplicate ('ware_merge'). The
                # system still lists the number, and "bring back?" would stand
                # there on every import.
                dubletten += 1
                continue
            _als_eingestellt(satz, alt, saetze[nummer], haus, belegt)
        elif satz.einheit not in einheiten and satz.ware_pk not in register.ignoriert:
            # Should not happen: the normalisation falls back to pieces, and the
            # five ids are in the database. Stays for the case that someone
            # removes a unit by hand.
            meldungen.append(
                _("%(nummer)s „%(name)s“: unbekannte Einheit „%(einheit)s“, übergangen.")
                % {"nummer": nummer, "name": satz.name, "einheit": satz.einheit}
            )
            continue
        elif alt is not None:
            _als_umbenannt(satz, alt)
        else:
            _als_neu(satz, haus, register, belegt, vergeben)
        plan.append(satz)
    _gebinde_vorbelegen(plan, register, einheiten)
    _umrechnung_anmerken(plan, register, einheiten)
    plan += _fehlende_ergaenzen(gruppen, haus, meldungen, format.kennung)
    _ausgelassenes_melden(meldungen, haus, unveraendert, nicht_zaehlbar, dubletten)
    return plan, meldungen


def _ausgelassenes_melden(
    meldungen: list[str], haus: Haus, unveraendert: int, nicht_zaehlbar: int, dubletten: int = 0
) -> None:
    """What stays out of the plan leaves its count at the top of the messages."""
    if dubletten:
        meldungen.insert(
            0,
            _(
                "%(anzahl)s Nummern sind eingestellte Dubletten und zählen als ein anderer "
                "Artikel. Sie stehen nicht im Plan."
            )
            % {"anzahl": dubletten},
        )
    if unveraendert:
        meldungen.insert(
            0,
            _("%(anzahl)s Artikel führt %(haus)s schon genau so. Sie stehen nicht im Plan.")
            % {"anzahl": unveraendert, "haus": haus},
        )
    if nicht_zaehlbar:
        meldungen.insert(
            0,
            _(
                "%(anzahl)s nicht zählbare Artikel übersprungen. Ihre Waren gehören zu "
                "„Nicht zählbar“."
            )
            % {"anzahl": nicht_zaehlbar},
        )


#: From when on a product group looks suspicious: if more of it is missing,
#: the export was probably filtered and not half the group delisted.
FEHLENDE_WARNSCHWELLE = 0.5


def _fehlende_ergaenzen(
    gruppen: dict[str, list[dict]], haus: Haus, meldungen: list[str], quelle: str
) -> list[Planzeile]:
    """What the house carries from this source and the file no longer knows.

    Only articles of the same source: an own article is in no file of an
    inventory management system, and an article from another system is not
    either.

    Compeso exports per chosen product group everything in it. A number
    missing from the file is thus delisted there. But that applies **only**
    to the product groups someone selected. Whoever knows nothing changed in
    one leaves it out of the export, and its articles must notice nothing of
    it. A product group without a single row in the file therefore stays
    untouched.

    The yardstick is the article number alone. If it is in the file, the row
    from it already holds the row, as a conflict or under a new designation.

    Preselected is "Leave". An accidentally incomplete export should clear
    nothing away only because someone waves the plan through.
    """
    if not gruppen:
        # An empty file does not say that everything is delisted.
        return []
    in_datei = {
        zeile["warengruppe"] or OHNE_WARENGRUPPE for zeilen in gruppen.values() for zeile in zeilen
    }
    offen = (
        Hausartikel.objects.filter(haus=haus, status__in=GEFUEHRT, quelle=quelle)
        .exclude(artikelnummer__in=gruppen)
        .select_related("warengruppe", "basiseinheit", "ware")
        .order_by("warengruppe__name", "artikelnummer")
    )
    plan: list[Planzeile] = []
    unberuehrt: dict[str, int] = {}
    for artikel in offen:
        name = artikel.warengruppe.name
        if name not in in_datei:
            unberuehrt[name] = unberuehrt.get(name, 0) + 1
            continue
        plan.append(_als_fehlend(artikel))
    if unberuehrt:
        meldungen.insert(
            0,
            _(
                "%(anzahl)s Artikel stehen nicht in der Datei, ihre Warengruppe aber "
                "auch nicht. Sie bleiben unberührt: %(namen)s."
            )
            % {
                "anzahl": sum(unberuehrt.values()),
                "namen": ", ".join(sorted(unberuehrt)),
            },
        )
    _luecken_melden(plan, haus, in_datei, meldungen)
    return plan


def _als_fehlend(artikel: Hausartikel) -> Planzeile:
    """A carried row of the house as a plan row, without anything from the file."""
    satz = Planzeile(
        artikelnummer=artikel.artikelnummer,
        name=artikel.name,
        warengruppe=artikel.warengruppe.name,
        einheit=artikel.basiseinheit.kennung,
        art=FEHLT,
        vorgehen=UEBERGEHEN,
        vorhanden_pk=artikel.pk,
        ware_pk=artikel.ware_id,
        inventurart=artikel.inventurart_id,
    )
    if artikel.status == Artikelstatus.INAKTIV:
        satz.hinweise.append(_("Steht schon auf inaktiv."))
    return satz


def _luecken_melden(
    plan: list[Planzeile], haus: Haus, in_datei: set[str], meldungen: list[str]
) -> None:
    """Warns where conspicuously much of a product group is missing.

    Then the export was more likely filtered than a house having delisted
    half the group. Counted against the whole carried stock of the group in
    this house, not against the rows of the file: the file also knows
    articles the house does not carry at all yet.
    """
    fehlend: dict[str, int] = {}
    for satz in plan:
        fehlend[satz.warengruppe] = fehlend.get(satz.warengruppe, 0) + 1
    if not fehlend:
        return
    bestand = dict(
        Hausartikel.objects.filter(haus=haus, status__in=GEFUEHRT, warengruppe__name__in=in_datei)
        .values_list("warengruppe__name")
        .annotate(anzahl=Count("pk"))
    )
    for name, anzahl in sorted(fehlend.items()):
        gesamt = bestand.get(name, anzahl)
        if anzahl <= gesamt * FEHLENDE_WARNSCHWELLE:
            continue
        meldungen.insert(
            0,
            _(
                "In „%(gruppe)s“ fehlen %(anzahl)s von %(gesamt)s Artikeln. Bitte "
                "nachsehen, ob die Warengruppe vollständig exportiert wurde."
            )
            % {"gruppe": name, "anzahl": anzahl, "gesamt": gesamt},
        )


def _planzeile(nummer: str, zeilen: list[dict], register: Namensregister) -> Planzeile:
    """What the file says about a number, still without a look at the stock."""
    mengen = _gebinde_aus_zeilen(zeilen)
    satz = Planzeile(
        artikelnummer=nummer,
        name=zeilen[0]["name"],
        warengruppe=zeilen[0]["warengruppe"] or OHNE_WARENGRUPPE,
        einheit=zeilen[0]["einheit"],
        gebinde={gebindename: faktoren[0] for gebindename, faktoren in mengen.items()},
        ware_pk=register.finden(zeilen[0]["name"]),
    )
    for gebindename, faktoren in mengen.items():
        if len(faktoren) > 1:
            gefunden = "; ".join(menge_text(faktor) for faktor in faktoren)
            satz.hinweise.append(
                _("Gebinde „%(name)s“ steht in der Datei mit mehreren Mengen: %(mengen)s.")
                % {"name": zitat(gebindename), "mengen": gefunden}
            )
    # The same number under two product groups or units: the first row applies,
    # but that must not stay silent. A product group that is only in the second
    # row would otherwise drop away without anyone seeing it.
    for feld, vorlage in (
        ("warengruppe", _("Die Nummer steht in der Datei unter mehreren Warengruppen: %(werte)s.")),
        ("einheit", _("Die Nummer steht in der Datei mit mehreren Einheiten: %(werte)s.")),
    ):
        werte = list(dict.fromkeys(zeile[feld] for zeile in zeilen if zeile[feld]))
        if len(werte) > 1:
            satz.hinweise.append(
                vorlage % {"werte": ", ".join(werte)} + " " + _("Es gilt die erste.")
            )
    # Likewise a second designation. Its packs stand in the field all the same.
    # Until 10/2026 it dropped away without a word.
    if len(namen := list(dict.fromkeys(zeile["name"] for zeile in zeilen if zeile["name"]))) > 1:
        satz.hinweise.append(
            _("Die Nummer steht in der Datei unter mehreren Bezeichnungen: %(werte)s.")
            % {"werte": ", ".join(f"„{name}“" for name in namen)}
            + " "
            + _("Es gilt die erste.")
        )
    _einzeln_ergaenzen(satz)
    satz.gebinde_datei = dict(satz.gebinde)
    return satz


def _einzeln_ergaenzen(satz: Planzeile) -> None:
    """Counted goods are also counted singly, whether the file says so or not.

    Compeso knows only the purchase packaging per article. But on the shelf
    stands an opened one, and what stands singly from it wants to be counted.
    The house has entered that by hand so far: 98 of 100 piece goods with a
    larger pack carry the loose piece next to it.

    Only for piece goods. A litre in a bottle is a pack itself, and a gram
    as a pack would be nonsense.
    """
    if satz.einheit != Einheitenkennung.STUECK or not satz.gebinde:
        return
    if Decimal(1) in satz.gebinde.values():
        return
    satz.gebinde[EINZELN_NAME] = Decimal(1)


def _als_neu(
    satz: Planzeile,
    haus: Haus,
    register: Namensregister,
    belegt: dict[int, str],
    vergeben: dict[str, str],
) -> None:
    """The house has never seen the number under this designation."""
    # The product belongs to "Not countable". The suggestion is obvious, the
    # decision stays in the plan. Deposit and vouchers need no packs.
    if satz.ware_pk in register.ignoriert:
        satz.vorgehen = IGNORIEREN
        satz.gebinde = {}
        satz.hinweise.append(_("Die Ware gehört zu „Nicht zählbar“, deshalb vorgeschlagen."))
        return

    # No count per pack means: the number is created but without content.
    # That is a dead entry. Discontinued it occupies no slot and yet does not
    # come back on every import.
    if not satz.gebinde:
        satz.vorgehen = EINSTELLEN
        satz.hinweise.append(_("In der Datei steht keine Anzahl je Gebinde."))
        return

    satz.vorgehen = AKTIV
    _ware_schon_gefuehrt(satz, haus, register, belegt, vergeben)


def _ware_schon_gefuehrt(
    satz: Planzeile,
    haus: Haus,
    register: Namensregister,
    belegt: dict[int, str],
    vergeben: dict[str, str],
) -> None:
    """The partial index allows only one carried row per product per house.

    The same designation under another number is no conflict as a house
    article, as a product it is, because the plan finds it by name. It then
    does not preselect it: the row needs a product of its own, and the name
    for it can only be assigned by whoever keeps the two apart.
    """
    ware = satz.ware_pk
    schluessel = f"pk:{ware}" if ware is not None else f"name:{satz.name.lower()}"
    if ware is not None and ware in belegt:
        wo = _("%(haus)s führt „%(name)s“ bereits unter %(nummer)s") % {
            "haus": haus,
            "name": register.namen[ware],
            "nummer": belegt[ware],
        }
    elif schluessel in vergeben:
        wo = _("Dieselbe Ware belegt in dieser Datei schon %(nummer)s") % {
            "nummer": vergeben[schluessel]
        }
    else:
        vergeben[schluessel] = satz.artikelnummer
        return
    satz.ware_pk = None
    satz.hinweise.append(
        _(
            "%(wo)s, und ein Haus führt jede Ware nur einmal. Sind es zwei verschiedene "
            "Waren, braucht diese hier eine eigene Ware. Bitte benennen. Ist die "
            "Nummer überholt, wird sie eingestellt angelegt und die Ware ausgewählt. "
            "Beim Übernehmen kommt die Zeile dann mit den Gebinden der Ware zur Prüfung "
            "zurück."
        )
        % {"wo": wo}
    )


def _als_eingestellt(
    satz: Planzeile,
    alt: Hausartikel,
    saetze: list[Hausartikel],
    haus: Haus,
    belegt: dict[int, str],
) -> None:
    """Exactly this article lies discontinued. It can be brought back.

    A twin must not arise. What the row offers is the model's path:
    reactivate the existing row.
    """
    satz.art = EINGESTELLT
    satz.vorgehen = UEBERGEHEN
    satz.vorhanden_pk = alt.pk
    satz.ware_pk = alt.ware_id
    satz.inventurart = alt.inventurart_id
    satz.gebinde = {}
    inhaber = next((anderer for anderer in saetze if anderer.status in GEFUEHRT), None)
    if inhaber is not None:
        satz.hinweise.append(
            _("Die Nummer führt zurzeit „%(name)s“. Beim Zurückholen wird dieser Satz eingestellt.")
            % {"name": inhaber.name}
        )
    nummer = belegt.get(alt.ware_id)
    if nummer is not None and nummer != satz.artikelnummer:
        satz.hinweise.append(
            _(
                "%(haus)s führt die Ware „%(name)s“ inzwischen unter %(nummer)s. "
                "Zurückholen lässt sich dieser Artikel erst, wenn sie dort frei ist."
            )
            % {"haus": haus, "name": alt.ware.name, "nummer": nummer}
        )


def _faktor_der_zeile(
    satz: Planzeile, register: Namensregister, einheiten: dict[str, Basiseinheit]
) -> Decimal | None:
    """How many units of the product one unit of the row is, as far as it is fixed.

    Within a dimension the units compute. Across dimensions what the house's
    row already carries applies ('faktor_zur_ware'). Otherwise 'None': the
    entry only comes from the form.
    """
    haus = einheiten.get(satz.einheit)
    ware = register.einheit.get(satz.ware_pk or 0)
    if haus is None or ware is None:
        return None
    return umrechnung(haus, ware, satz.faktor_zur_ware)


def _im_haus(mengen: dict[Decimal, str], faktor: Decimal) -> dict[Decimal, str]:
    """The pack quantities of the product in the house's unit, to three places."""
    return {
        (menge / faktor).quantize(GEBINDE_STELLEN, rounding=ROUND_HALF_UP): name
        for menge, name in mengen.items()
    }


def _einheit_uebernehmen(satz: Planzeile, alt: Hausartikel) -> None:
    """What the house's row carries for the unit keeps applying as long as the file confirms it."""
    if alt.basiseinheit.kennung == satz.einheit:
        satz.faktor_zur_ware = alt.faktor_zur_ware


def _abweichung_anmerken(
    satz: Planzeile,
    feld: Beschriftung,
    aus_datei: Beschriftung,
    bisher: Beschriftung,
    gezeigt: Beschriftung | None = None,
) -> bool:
    """Whether the file says something different about a field, with the hint for it.

    Compared against the stored name, shown is the display name. For the
    catch-all group the two differ.
    """
    if aus_datei == bisher:
        return False
    satz.hinweise.append(
        _("%(feld)s bisher „%(bisher)s“, in der Datei „%(datei)s“.")
        % {"feld": feld, "bisher": gezeigt or bisher, "datei": aus_datei}
    )
    return True


def _aufgezaehlt(mengen: dict[Decimal, str]) -> str:
    """Packs as a list in a sentence: "Kasten = 24; Flasche = 1"."""
    return "; ".join(f"{name} = {menge_text(menge)}" for menge, name in mengen.items())


def _mengen_vergleichen(
    satz: Planzeile, alt: Hausartikel, zeilen: list[dict], einheiten: dict[str, Basiseinheit]
) -> bool:
    """Whether the file names a count per pack the product does not carry, with the hints.

    Measured against the **quantities** of the product, not against the pack
    names: "Kiste" of 24 is stored if the product knows a "Kasten" of 24.
    Measured in the unit of the file: the product's quantities are converted
    there. If that does not work because the dimension changes and the factor
    only comes from the form, nothing can be compared. What the file brings
    then counts as not stored and is added along with the conversion.

    Only active packs count (decision of 2026-10-18). If the file carries the
    quantity of an inactive one, the row becomes a conflict. Until then an
    inactive pack counted as stored, and no import ever brought it back. The
    field shows it under its own name and the hint says so ('_gebindefeld'),
    and "Change" brings it back ('_gebinde_sichern').
    """
    ware = alt.ware
    haus = einheiten.get(satz.einheit)
    faktor = umrechnung(haus, ware.basiseinheit, satz.faktor_zur_ware) if haus else None
    if faktor is None:
        hinterlegt = {}
        bekannt = str(
            _("Mengen der Ware in %(einheit)s lassen sich erst mit der Umrechnung vergleichen")
            % {"einheit": ware.basiseinheit.kurz}
        )
    else:
        hinterlegt = _im_haus(
            {g.faktor_in_basiseinheit: str(g.anzeigename) for g in ware.gebinde.all() if g.aktiv},
            faktor,
        )
        bekannt = _aufgezaehlt(hinterlegt)
    unbekannt = [
        (name, menge)
        for name, mengen in _gebinde_aus_zeilen(zeilen).items()
        for menge in mengen
        if menge not in hinterlegt
    ]
    satz.hinweise += [
        _(
            "In der Datei steht „%(name)s = %(menge)s“. Diese Menge ist bei „%(ware)s“ "
            "nicht hinterlegt (%(bekannt)s)."
        )
        % {
            "name": zitat(name),
            "menge": menge_text(menge),
            "ware": ware.name,
            "bekannt": bekannt or _("keine Gebinde"),
        }
        for name, menge in unbekannt
    ]
    return bool(unbekannt)


def _als_konflikt(
    satz: Planzeile, alt: Hausartikel, zeilen: list[dict], einheiten: dict[str, Basiseinheit]
) -> bool:
    """The article is carried. Does the file say something different about it?

    Three things count: the product group (it controls booking in WinTICKET),
    the unit and the count per pack ('_mengen_vergleichen'). If the dimension
    changes, the count cannot be compared. The unit itself is then the
    conflict.
    """
    _einheit_uebernehmen(satz, alt)
    gruppe = alt.warengruppe
    abweichungen = []
    if _abweichung_anmerken(
        satz, _("Warengruppe"), satz.warengruppe, gruppe.name, gruppe.anzeigename
    ):
        abweichungen.append(WARENGRUPPE)
    if _abweichung_anmerken(satz, _("Einheit"), einheit_kurz(satz.einheit), alt.basiseinheit.kurz):
        abweichungen.append(EINHEIT)
    if _mengen_vergleichen(satz, alt, zeilen, einheiten):
        abweichungen.append(GEBINDE)
    if not abweichungen:
        return False

    satz.abweichungen = abweichungen
    satz.art = KONFLIKT
    satz.vorgehen = UEBERGEHEN
    satz.vorhanden_pk = alt.pk
    satz.ware_pk = alt.ware_id
    satz.inventurart = alt.inventurart_id
    if alt.status == Artikelstatus.INAKTIV:
        satz.hinweise.append(_("Der vorhandene Satz ist inaktiv. „Ändern“ lässt ihn das auch."))
    return True


def _als_umbenannt(satz: Planzeile, alt: Hausartikel) -> None:
    """The house carries the number under another designation.

    Rename or reassignment? That cannot be derived from the export, so
    whoever sits in front decides. For the reassignment what the new row
    would arise with is already ready.

    Without a product of the new name the old row's product is preselected,
    right for "Rename". A reassigned number mostly belongs to another one,
    which is why the row says so (decision of 2026-10-19).
    """
    satz.art = UMBENANNT
    satz.vorgehen = UEBERGEHEN
    satz.status_neu = AKTIV if satz.gebinde else EINSTELLEN
    satz.vorhanden_pk = alt.pk
    satz.inventurart = alt.inventurart_id
    if satz.ware_pk is None:
        satz.ware_pk = alt.ware_id
        _einheit_uebernehmen(satz, alt)
        satz.hinweise.append(
            _(
                "Vorgewählt ist die Ware des bisherigen Satzes. Für „Ersetzen“ bitte die Ware "
                "prüfen."
            )
        )
    if alt.status == Artikelstatus.INAKTIV:
        satz.hinweise.append(_("Der vorhandene Satz ist inaktiv. „Umbenennen“ lässt ihn das auch."))

    for feld, aus_datei, bisher, gezeigt in (
        (_("Bezeichnung"), satz.name, alt.name, None),
        (_("Warengruppe"), satz.warengruppe, alt.warengruppe.name, alt.warengruppe.anzeigename),
        (_("Einheit"), einheit_kurz(satz.einheit), alt.basiseinheit.kurz, None),
    ):
        if aus_datei:
            _abweichung_anmerken(satz, feld, aus_datei, bisher, gezeigt)


def _gebinde_vorbelegen(
    plan: list[Planzeile], register: Namensregister, einheiten: dict[str, Basiseinheit]
) -> None:
    """Puts into the pack field what the product carries and adds what the file brings.

    Until 09/2026 the field stayed empty as soon as the article had a
    product. The file would otherwise have overwritten maintained quantities
    and silently turned "Kasten = 20" into "Kasten = 24". Only then nobody
    saw what the product carries at all either, and a wrong name could only
    be fixed in product maintenance.

    Now everything is there. What is right stays untouched when adopting, and
    whoever changes or strikes a line means it. Matching goes by
    **quantity**, not by name, see '_gebinde_sichern'.

    For the whole plan at once: the packs of all affected products come from
    one query.

    The field is in the **unit of the row**, i.e. the house's. The product
    carries its quantities in its own, and they are converted. If the
    dimension changes and the factor only comes from the form, the product's
    quantities stay out and stand next to it as a hint. Adopting is then
    only additive ('_gebinde_sichern').

    The row notes whose packs the field shows ('gebinde_von'). Only for that
    product is the field everything it carries. A product chosen by hand
    comes back with its own packs first ('BaseImportFormSet'), and so does a
    row whose factor was missing here: its field showed none of the product's
    packs. Until 10/2026 adopting such a row renamed a pack the field had
    never shown, or switched an inactive one on, once a converted quantity
    happened to match it.

    The inactive packs come along for '_gebindefeld': their names are taken,
    and a quantity of theirs that the file carries goes into the field under
    their name, with a hint.
    """
    betroffen = [
        satz
        for satz in plan
        if satz.ware_pk is not None
        and satz.vorgehen != IGNORIEREN
        and satz.art not in (EINGESTELLT, FEHLT)
    ]
    if not betroffen:
        return
    gefuehrt: dict[int | None, dict[Decimal, str]] = {}
    inaktiv: dict[int | None, dict[Decimal, str]] = {}
    for ware_id, name, faktor, aktiv in (
        Gebinde.objects.filter(ware_id__in={satz.ware_pk for satz in betroffen})
        .order_by("ware_id", "sortierung")
        .values_list("ware_id", "name", "faktor_in_basiseinheit", "aktiv")
    ):
        (gefuehrt if aktiv else inaktiv).setdefault(ware_id, {})[faktor] = name

    for satz in betroffen:
        faktor = _faktor_der_zeile(satz, register, einheiten)
        satz.gebinde, hinweise = _feld_der_ware(
            satz.gebinde,
            gefuehrt.get(satz.ware_pk, {}),
            inaktiv.get(satz.ware_pk, {}),
            faktor,
            register.namen[satz.ware_pk or 0],
            register.einheit[satz.ware_pk or 0],
            satz.einheit,
        )
        satz.hinweise += hinweise
        # Without the factor the field holds only the file's quantities. Adopting
        # then leaves the product's packs as they are ('_gebinde_sichern', 'fremd'),
        # and the form shows them once the factor is typed ('BaseImportFormSet').
        satz.gebinde_von = satz.ware_pk if faktor is not None else None
        haus = einheiten.get(satz.einheit)
        ware = register.einheit.get(satz.ware_pk or 0)
        quer = (
            haus is not None and ware is not None and braucht_faktor(haus.dimension, ware.dimension)
        )
        satz.gebinde_faktor = satz.faktor_zur_ware if satz.gebinde_von and quer else None


def gebindefeld_fuer(
    ware: Ware, datei: dict[str, Decimal], einheit: str, faktor_zur_ware: Decimal | None
) -> tuple[dict[str, Decimal], list[str]]:
    """The pack field for a product chosen in the form, as the plan would have shown it.

    The plan fills the field for the product it preselected. A product chosen
    by hand finds the file's quantities there, or another product's packs,
    and adopting would retire its own. So the row comes back with this field
    first ('BaseImportFormSet._gebinde_nachfuellen'). 'datei' is what the file
    brought ('Planzeile.gebinde_datei').
    """
    haus = Basiseinheit.objects.filter(kennung=einheit).first()
    gefuehrt: dict[Decimal, str] = {}
    inaktiv: dict[Decimal, str] = {}
    for menge, name, aktiv in ware.gebinde.order_by("sortierung").values_list(
        "faktor_in_basiseinheit", "name", "aktiv"
    ):
        (gefuehrt if aktiv else inaktiv)[menge] = name
    faktor = umrechnung(haus, ware.basiseinheit, faktor_zur_ware) if haus is not None else None
    return _feld_der_ware(datei, gefuehrt, inaktiv, faktor, ware.name, ware.basiseinheit, einheit)


def _feld_der_ware(
    datei: dict[str, Decimal],
    gefuehrt: dict[Decimal, str],
    inaktiv: dict[Decimal, str],
    faktor: Decimal | None,
    warenname: str,
    wareneinheit: Basiseinheit,
    einheit: str,
) -> tuple[dict[str, Decimal], list[str]]:
    """The field for one row and its hints: the product's packs in the row's unit, then the file's.

    'gefuehrt' are the product's active packs in its own unit, 'inaktiv' the
    inactive ones, 'faktor' how many units of the product one unit of the row
    is. Without it across dimensions the product's packs cannot be shown in
    the row's unit. They stay out, and the hint names them. Nor can a name
    from the file be judged then: the product's pack of that name may hold
    the very same quantity. Adopting compares in the product's unit
    ('_gebinde_sichern').
    """
    hinweise: list[str] = []
    if faktor is None:
        if gefuehrt:
            hinweise.append(
                _(
                    "„%(name)s“ führt in %(einheit)s: %(gebinde)s. Diese Gebinde bleiben. "
                    "Hier stehen Mengen in %(haus)s, sie kommen dazu."
                )
                % {
                    "name": warenname,
                    "einheit": wareneinheit.kurz,
                    "gebinde": _aufgezaehlt(gefuehrt),
                    "haus": einheit_kurz(einheit),
                }
            )
        return _gebindefeld(datei, {}, {}, hinweise), hinweise
    feld = _gebindefeld(datei, _im_haus(gefuehrt, faktor), _im_haus(inaktiv, faktor), hinweise)
    return feld, hinweise


def _umrechnung_anmerken(
    plan: list[Planzeile], register: Namensregister, einheiten: dict[str, Basiseinheit]
) -> None:
    """Where the house counts in a different dimension than the product, the factor is part of it.

    The hint stands on every row that chooses such a product, even if the
    house's row already carries the factor. Then it is prefilled, and
    whoever changes the row sees what it refers to.
    """
    for satz in plan:
        if satz.vorgehen == IGNORIEREN or satz.art in (EINGESTELLT, FEHLT):
            continue
        haus = einheiten.get(satz.einheit)
        ware = register.einheit.get(satz.ware_pk or 0)
        if haus is None or ware is None or not braucht_faktor(haus.dimension, ware.dimension):
            continue
        satz.hinweise.append(
            _(
                "Das Haus zählt in %(haus)s, „%(name)s“ wird in %(ware)s geführt. Bitte die "
                "Umrechnung angeben: wie viele %(ware)s ist ein %(haus)s?"
            )
            % {
                "name": register.namen[satz.ware_pk or 0],
                "ware": ware.kurz,
                "haus": haus.kurz,
            }
        )
        if satz.faktor_zur_ware is not None:
            satz.hinweise[-1] += " " + _("Hinterlegt ist %(faktor)s.") % {
                "faktor": menge_kurz(satz.faktor_zur_ware)
            }


def _gebindefeld(
    datei: dict[str, Decimal],
    gefuehrt: dict[Decimal, str],
    inaktiv: dict[Decimal, str],
    hinweise: list[str],
) -> dict[str, Decimal]:
    """The carried packs first, behind them the quantities that are only in the file.

    The name from the file counts only for a new quantity. If the product
    already gives it to another one, the pack gets its own ("Kasten 20").
    Otherwise "Change" would rewrite the existing one, and for every house at
    that. But a house with a deviating size gets a second pack. An inactive
    pack holds its name as well, though the field does not show it.

    A quantity the file carries for an inactive pack goes into the field under
    the pack's own name, and the hint at the row says that adopting brings it
    back, in every house (decided on 2026-10-18: if the file carries it, the
    import brings it back). Whoever does not want that takes the line out, as
    on the house article form the person decides ('NeuesGebindeForm'). Until
    10/2026 the quantity stood in the field under the file's name like a new
    one, and adopting brought the pack back without a word, renamed if the
    file called it differently. The loose piece the plan adds by itself
    ('_einzeln_ergaenzen') is not carried by the file. If the product has it
    inactive, someone switched it off, and it stays out with a hint. A file
    that names its own loose piece "Einzeln" is read the same way.
    """
    feld = {name: menge for menge, name in gefuehrt.items()}
    namen = set(feld) | set(inaktiv.values())
    for aus_datei, menge in datei.items():
        if menge in gefuehrt:
            continue
        if (ruhend := inaktiv.get(menge)) is not None:
            if aus_datei == EINZELN_NAME:
                hinweise.append(_inaktive_menge(aus_datei, ruhend, menge, im_feld=False))
            elif ruhend not in feld:
                feld[ruhend] = menge
                hinweise.append(_inaktive_menge(aus_datei, ruhend, menge, im_feld=True))
            continue
        name = aus_datei
        if name in namen:
            name = freier_gebindename(aus_datei, menge, namen)
            if aus_datei in inaktiv.values():
                vorlage = _(
                    "„%(datei)s“ gibt es bei der Ware schon, aber inaktiv und mit einer anderen "
                    "Menge. Vorgeschlagen ist deshalb „%(name)s“ als eigenes Gebinde."
                )
            else:
                vorlage = _(
                    "„%(datei)s“ gibt es bei der Ware schon mit einer anderen Menge. "
                    "Vorgeschlagen ist deshalb „%(name)s“ als eigenes Gebinde."
                )
            hinweise.append(vorlage % {"datei": aus_datei, "name": name})
        feld[name] = menge
        namen.add(name)
    return feld


def _inaktive_menge(aus_datei: str, name: str, menge: Decimal, *, im_feld: bool) -> str:
    """What becomes of the quantity of an inactive pack, said at the row.

    'im_feld': it stands in the field under the pack's name, and adopting
    brings the pack back. Otherwise it stays out. The plan reports it like
    the house article form ('NeuesGebindeForm'): a hit by quantity names the
    inactive pack the quantity belongs to.
    """
    werte = {"datei": zitat(aus_datei), "name": name, "menge": menge_text(menge)}
    if aus_datei == name:
        satz = _("„%(name)s = %(menge)s“ führt die Ware nur inaktiv.")
    else:
        satz = _("„%(datei)s = %(menge)s“ hat die Menge des inaktiven Gebindes „%(name)s“.")
    if im_feld:
        weg = _(
            "Im Feld steht es als „%(name)s = %(menge)s“, und Übernehmen holt es zurück, in "
            "allen Häusern. Wer das nicht will, nimmt die Zeile aus dem Feld."
        )
    else:
        weg = _(
            "Es bleibt so und steht deshalb nicht im Feld. Wer es wieder braucht, trägt "
            "„%(name)s = %(menge)s“ ein. Beim Übernehmen ist es dann wieder aktiv, in allen "
            "Häusern."
        )
    return f"{satz % werte} {weg % werte}"


def _zeilen_gruppieren(
    tabelle: pd.DataFrame, format: Format
) -> tuple[dict[str, list[dict]], list[str]]:
    """Bundle rows per article number.

    Within a file a number occurs several times when the article is
    purchased in different packs. What a number is, the format knows: it
    normalises it as it stands on the house article.

    A product group longer than its column stays out with a message. It
    rides along hidden, nobody could correct it in the plan, and until
    10/2026 adopting it ended on the error page under PostgreSQL. So do an
    article number and a designation longer than theirs: the number ended
    on the error page as well, the designation held up the whole plan with
    an error in a hidden field. A pack name longer than its column likewise:
    adopting refused it, and until 10/2026 the plan copied it into the
    fields and hints of its row first.

    A message quotes a cell short, and past a hundred rows they are only
    counted ('Zeilenmeldungen'). Until 10/2026 every message carried the
    whole cell: a number of a megabyte in five hundred rows of a workbook of
    a few kilobytes made a gigabyte of messages.
    """
    gruppen: dict[str, list[dict]] = {}
    fehler = Zeilenmeldungen()
    for zeile in tabelle.to_dict("records"):
        nummer = format.artikelnummer(_text(zeile["artikelnummer"]))
        if nummer is None:
            fehler.melden(
                _("„%(wert)s“ ist keine Artikelnummer, wie %(format)s sie führt.")
                % {"wert": zitat(_text(zeile["artikelnummer"])), "format": format.name}
            )
            continue
        if len(nummer) > NUMMER_LAENGE:
            fehler.melden(
                _("„%(nummer)s“: Die Artikelnummer ist länger als %(laenge)s Zeichen, übergangen.")
                % {"nummer": zitat(nummer), "laenge": NUMMER_LAENGE}
            )
            continue
        if len(_text(zeile["name"])) > NAME_LAENGE:
            fehler.melden(
                _("%(nummer)s: Die Bezeichnung ist länger als %(laenge)s Zeichen, übergangen.")
                % {"nummer": nummer, "laenge": NAME_LAENGE}
            )
            continue
        if len(_text(zeile["warengruppe"])) > WARENGRUPPENNAME_LAENGE:
            fehler.melden(
                _(
                    "%(nummer)s „%(name)s“: Die Warengruppe ist länger als %(laenge)s Zeichen, "
                    "übergangen."
                )
                % {
                    "nummer": nummer,
                    "name": zitat(_text(zeile["name"])),
                    "laenge": WARENGRUPPENNAME_LAENGE,
                }
            )
            continue
        if len(_text(zeile["gebinde"])) > GEBINDENAME_LAENGE:
            fehler.melden(
                _(
                    "%(nummer)s „%(name)s“: Der Name des Gebindes ist länger als %(laenge)s "
                    "Zeichen, übergangen."
                )
                % {
                    "nummer": nummer,
                    "name": zitat(_text(zeile["name"])),
                    "laenge": GEBINDENAME_LAENGE,
                }
            )
            continue
        gruppen.setdefault(nummer, []).append(
            {
                "name": _text(zeile["name"]),
                "warengruppe": _text(zeile["warengruppe"]),
                "einheit": _text(zeile["einheit"]),
                "gebinde": _text(zeile["gebinde"]),
                "faktor": _faktor(zeile["anzahl"]),
            }
        )
    return gruppen, fehler.liste()


def _gebinde_aus_zeilen(zeilen: list[dict]) -> dict[str, list[Decimal]]:
    """Per pack name all quantities from the file, without repetitions.

    More than one quantity means: the file contradicts itself already. That
    comes into the plan as a hint. Offered is the first.
    """
    gefunden: dict[str, list[Decimal]] = {}
    for zeile in zeilen:
        if not zeile["gebinde"] or zeile["faktor"] is None:
            continue
        faktoren = gefunden.setdefault(zeile["gebinde"], [])
        if zeile["faktor"] not in faktoren:
            faktoren.append(zeile["faktor"])
    return gefunden


def als_daten(satz: Planzeile) -> dict:
    """A plan row as the form would return it unchanged.

    The path past the form: for tests and for the day a checked list should
    be loaded without asking.
    """
    return {
        "artikelnummer": satz.artikelnummer,
        "warengruppe": satz.warengruppe,
        "einheit": satz.einheit,
        "art": satz.art,
        "vorhanden_pk": satz.vorhanden_pk,
        "name": satz.name,
        "ware": Ware.objects.filter(pk=satz.ware_pk).first(),
        "neuer_ware": satz.neuer_ware,
        "inventurart": satz.inventurart,
        "gebinde": dict(satz.gebinde),
        "gebinde_datei": dict(satz.gebinde_datei),
        "gebinde_von": satz.gebinde_von,
        "gebinde_faktor": satz.gebinde_faktor,
        "vorgehen": satz.vorgehen,
        "status_neu": satz.status_neu,
        "uebernehmen": list(satz.abweichungen),
        "faktor_zur_ware": satz.faktor_zur_ware,
    }
