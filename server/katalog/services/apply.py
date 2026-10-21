"""Step 2 of the import: execute what is in the form."""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.translation import gettext as _

from organisation.models import Haus

from ..formats import STANDARD
from ..models import (
    EINZELN_NAME,
    FAKTOR_HOECHSTENS,
    FAKTOR_MINDESTENS,
    GEBINDE_HOECHSTMENGE,
    GEFUEHRT,
    OHNE_WARENGRUPPE,
    Artikelstatus,
    Basiseinheit,
    Dimension,
    Einheitenkennung,
    Gebinde,
    Hausartikel,
    Inventurart,
    Ware,
    Warenfamilie,
    Warengruppe,
    artikel_sperren,
    braucht_faktor,
    einheit_kurz,
    neue_art_ans_ende,
    umrechnung,
)
from .base import (
    AKTIV,
    AKTUALISIEREN,
    EINGESTELLT,
    EINHEIT,
    EINSTELLEN,
    ERSETZEN,
    FEHLT,
    FUEHREND,
    GEBINDE,
    GEBINDE_STELLEN,
    IGNORIEREN,
    INAKTIV,
    KONFLIKT,
    STATUS_JE_VORGEHEN,
    UEBERGEHEN,
    WARENGRUPPE,
    ZURUECKHOLEN,
    Importbericht,
    freier_gebindename,
    menge_text,
)
from .planning import saetze_je_nummer


@dataclass(frozen=True)
class Lauf:
    """What every row of one import shares: the house, the lookups, the source."""

    haus: Haus
    warengruppen: dict[str, Warengruppe]
    einheiten: dict[str, Basiseinheit]
    quelle: str
    #: Number and designation of every article the house carries, once: the
    #: twin check asked per row until 10/2026: six hundred queries, now one.
    vorhanden: set[tuple[str, str]]
    #: The type a new product's first article gets: the first one, as the
    #: rule says ('Inventurart.vorschlag_fuer'). Asked once instead of twice
    #: per row. A product that exists keeps asking its other articles.
    erste_art: int


@transaction.atomic
@neue_art_ans_ende()
def ausfuehren(zeilen: list[dict], haus: Haus, quelle: str = STANDARD) -> Importbericht:
    """Writes the confirmed plan.

    'zeilen' are the 'cleaned_data' of the form, not the original plan. What
    is there applies. 'quelle' is the id of the format the file came from.
    Every new row carries it. A row that changes its type goes to the end of
    it in the counting lists ('neue_art_ans_ende').

    **First the locks, then the plan against the stock, then the writing.**
    The form measured the plan before any lock. Measured once more under the
    locks of its articles, a plan that no longer fits writes nothing, and
    the report says which rows to read in anew ('_bestand_unter_der_sperre').
    """
    _artikel_zuerst(zeilen, haus)
    if meldungen := _bestand_unter_der_sperre(zeilen, haus):
        return Importbericht(zeilen=len(zeilen), uebergangen=len(zeilen), fehler=meldungen)
    bericht = Importbericht(zeilen=len(zeilen))
    warengruppen = _warengruppen_sicherstellen(zeilen, bericht)
    # Only the units a product may be carried in: the portion belongs to
    # families. The plan's field is hidden, and a changed one made a product
    # in portions until 10/2026.
    einheiten = {einheit.kennung: einheit for einheit in Basiseinheit.fuer_artikel()}
    nicht_zaehlbar = Warenfamilie.nicht_zaehlbar()
    lauf = Lauf(
        haus=haus,
        warengruppen=warengruppen,
        einheiten=einheiten,
        quelle=quelle,
        vorhanden=set(Hausartikel.objects.filter(haus=haus).values_list("artikelnummer", "name")),
        erste_art=Inventurart.erste().pk,
    )

    # First vacate all slots, then occupy: the product a row needs may only
    # be released by a later one.
    geraeumt = _platz_machen(zeilen, haus, bericht, lauf.vorhanden, einheiten)

    for zeile in zeilen:
        vorgehen = zeile["vorgehen"]
        if vorgehen == UEBERGEHEN or (vorgehen == ERSETZEN and id(zeile) not in geraeumt):
            bericht.uebergangen += 1
            continue

        if vorgehen == ZURUECKHOLEN:
            _zurueckholen(zeile, bericht)
            continue

        if zeile.get("art") == FEHLT:
            # Discontinued already when vacating: the row may release its
            # product for another row of this plan.
            if vorgehen == INAKTIV:
                _fehlenden_deaktivieren(zeile, bericht)
            continue

        status = status_der_zeile(zeile)
        if status == IGNORIEREN:
            # No product, so no packs either. What is in the field lapses.
            ware = _ignorierte_ware(zeile, einheiten, nicht_zaehlbar, bericht)
            if ware is not None:
                _anlegen(zeile, ware, lauf, bericht)
            continue

        ware, ware_neu = _ware_sichern(zeile, einheiten, bericht)
        if ware is None:
            continue

        if vorgehen == AKTUALISIEREN:
            geschrieben = _aktualisieren(zeile, ware, warengruppen, einheiten, bericht)
        else:
            geschrieben = _anlegen(
                zeile, ware, lauf, bericht, art_vorgabe=lauf.erste_art if ware_neu else None
            )
        # Only for a row that was written: until 10/2026 a row that was not
        # adopted for lack of the conversion changed its product's packs anyway.
        if geschrieben and schreibt_gebinde(zeile):
            bericht.gebinde_neu += _gebinde_uebernehmen(zeile, ware, einheiten, bericht)

    return bericht


def _einheit_der_zeile(
    zeile: dict,
    ware: Ware,
    einheiten: dict[str, Basiseinheit],
    bericht: Importbericht,
) -> tuple[Basiseinheit | None, Decimal | None]:
    """The house's unit and the factor to the product, or nothing if it is missing.

    A unit that does not exist is the product's: the normalisation knows only
    the five, and whoever changes the id in the form means nothing in
    particular. Within a dimension the factor stays empty, the unit computes
    itself. If it is missing across dimensions, it is reported and nothing
    created: the form does not let the case through in any status
    ('schreibt_hausartikel'), here it stands for the paths past it.
    """
    einheit = einheiten.get(zeile["einheit"]) or ware.basiseinheit
    wareneinheit = ware.basiseinheit
    if not braucht_faktor(einheit.dimension, wareneinheit.dimension):
        return einheit, None
    faktor = umrechnung(einheit, wareneinheit, zeile.get("faktor_zur_ware"))
    if faktor is None:
        bericht.fehler.append(
            _(
                "%(nummer)s: Das Haus zählt in %(haus)s, „%(name)s“ wird in %(ware)s geführt. "
                "Ohne die Umrechnung wurde die Zeile nicht übernommen."
            )
            % {
                "nummer": zeile["artikelnummer"],
                "name": ware.name,
                "ware": wareneinheit.kurz,
                "haus": einheit.kurz,
            }
        )
        return None, None
    return einheit, faktor


def _gebinde_uebernehmen(
    zeile: dict,
    ware: Ware,
    einheiten: dict[str, Basiseinheit],
    bericht: Importbericht,
) -> int:
    """Bring the pack field into the product's unit and then reconcile.

    The field is in the row's unit, the product carries its quantities in its
    own. If the house counts in a different dimension, only **additions** are
    made: the plan may then not have been able to show the product's packs
    ('_gebinde_vorbelegen'), and what it did not show the field must not
    retire.

    The same holds for a product the field was not filled for ('gebinde_von'):
    one chosen by hand, a new one, or a row from past the form. Its packs also
    keep their names, and the report says so. The form sends a row with a
    product chosen by hand back with that product's packs first. Until 10/2026
    adopting retired its packs in every house when the field held only the
    file's quantities, and a product chosen instead of the suggested one took
    over the other's packs.
    """
    einheit = einheiten.get(zeile["einheit"]) or ware.basiseinheit
    wareneinheit = ware.basiseinheit
    faktor = umrechnung(einheit, wareneinheit, zeile.get("faktor_zur_ware"))
    if faktor is None:
        bericht.fehler.append(
            _("%(nummer)s: ohne Umrechnung nach %(ware)s bleiben die Gebinde, wie sie sind.")
            % {"nummer": zeile["artikelnummer"], "ware": wareneinheit.kurz}
        )
        return 0
    gebinde = _gebinde_in_ware(zeile, faktor, wareneinheit, bericht)
    fremd = zeile.get("gebinde_von") != ware.pk
    angelegt, behalten = _gebinde_sichern(
        ware,
        gebinde,
        bericht,
        nur_ergaenzen=einheit.dimension != wareneinheit.dimension,
        fremd=fremd,
    )
    if fremd and behalten:
        bericht.fehler.append(
            _(
                "%(nummer)s: Das Feld zeigte nicht die Gebinde von „%(ware)s“. Sie bleiben, "
                "wie sie sind. Aus dem Feld kam nur dazu, was ihr fehlte."
            )
            % {"nummer": zeile["artikelnummer"], "ware": ware.name}
        )
    return angelegt


def _gebinde_in_ware(
    zeile: dict, faktor: Decimal, wareneinheit: Basiseinheit, bericht: Importbericht
) -> dict[str, Decimal]:
    """The pack field in the product's unit. What the column does not hold is reported and left out.

    The field checks its quantities in the row's unit. In the product's they
    can grow past the column ("Sack = 20000" in kg is 20,000,000 g) or two of
    them can round to the same quantity. Until 10/2026 the first ended under
    PostgreSQL in a numeric overflow and the second at the unique index, both
    on the error page, and the whole import was rolled back.
    """
    gebinde: dict[str, Decimal] = {}
    for name, menge in zeile["gebinde"].items():
        in_ware = (menge * faktor).quantize(GEBINDE_STELLEN, rounding=ROUND_HALF_UP)
        werte = {
            "nummer": zeile["artikelnummer"],
            "name": name,
            "ware": wareneinheit.kurz,
            "menge": menge_text(in_ware),
        }
        if in_ware < GEBINDE_STELLEN:
            bericht.fehler.append(
                _(
                    "%(nummer)s: „%(name)s“ fasste in %(ware)s weniger als %(menge)s. So klein "
                    "lässt sich keine Menge führen. Das Gebinde wurde übergangen."
                )
                % (werte | {"menge": menge_text(GEBINDE_STELLEN)})
            )
        elif in_ware >= GEBINDE_HOECHSTMENGE:
            bericht.fehler.append(
                _(
                    "%(nummer)s: „%(name)s“ fasste in %(ware)s %(menge)s. Ein Gebinde fasst "
                    "höchstens %(grenze)s. Das Gebinde wurde übergangen."
                )
                % (werte | {"grenze": menge_text(GEBINDE_HOECHSTMENGE - GEBINDE_STELLEN)})
            )
        elif (gleich := next((n for n, m in gebinde.items() if m == in_ware), None)) is not None:
            bericht.fehler.append(
                _(
                    "%(nummer)s: „%(name)s“ fasste in %(ware)s dasselbe wie „%(gleich)s“ "
                    "(%(menge)s). Das Gebinde wurde übergangen."
                )
                % (werte | {"gleich": gleich})
            )
        else:
            gebinde[name] = in_ware
    return gebinde


def schreibt_hausartikel(zeile: dict) -> bool:
    """Whether adopting the row creates or changes a house article.

    Everything but "Leave" and "Bring back" and a row missing from the
    file: those leave the row as it is. A discontinued or not countable new
    row is created too, and the conversion applies to it as to an active
    one ('Hausartikel.pruefe_einheit'). Until 10/2026 the form asked for it
    only where the row ends up carried, and adopting then dropped the row.
    """
    vorgehen = zeile.get("vorgehen")
    return vorgehen not in (None, "", UEBERGEHEN, ZURUECKHOLEN) and zeile.get("art") != FEHLT


def schreibt_gebinde(zeile: dict) -> bool:
    """Whether adopting the row reconciles the packs of its product.

    The same steps as 'ausfuehren': not where no house article is written,
    not for one that becomes not countable, and on a conflict only if the
    packs are among what "Change" adopts. The form asks here which rows
    come back with the packs of a product chosen by hand.
    """
    if not schreibt_hausartikel(zeile) or status_der_zeile(zeile) == IGNORIEREN:
        return False
    return uebernimmt(zeile, GEBINDE)


def uebernimmt(zeile: dict, teil: str) -> bool:
    """Whether "Change" adopts this part from the file.

    Only a conflict knows the choice. Everywhere else the row applies as a
    whole. What is not adopted stands as a conflict again on the next import.
    """
    if zeile.get("art") != KONFLIKT or zeile.get("uebernehmen") is None:
        return True
    return teil in zeile["uebernehmen"]


def status_der_zeile(zeile: dict) -> str:
    """What the row of a creating row arises with.

    For a new article the procedure itself says it. "Replace" also creates a
    new one and carries the same choice in 'status_neu'.
    """
    if zeile["vorgehen"] == ERSETZEN:
        return zeile.get("status_neu") or AKTIV
    return zeile["vorgehen"]


def belegt_platz(zeile: dict) -> bool:
    """Whether the row has a carried row in the end, with number and product in the house."""
    vorgehen = zeile.get("vorgehen")
    # Whoever is missing from the file keeps their slot until discontinued.
    if zeile.get("art") == FEHLT:
        return vorgehen != EINSTELLEN
    if vorgehen in (AKTUALISIEREN, ZURUECKHOLEN):
        return True
    return vorgehen in (*FUEHREND, ERSETZEN) and status_der_zeile(zeile) in FUEHREND


def _artikel_zuerst(zeilen: list[dict], haus: Haus) -> None:
    """Locks every article the plan may write, in one call and by number, before it writes.

    The plan writes its rows in the order of the file, and every write holds
    its article until the end. A renamed article or pack locks the rows that
    show it as well ('django_project/renaming.py'), a pack all the articles
    of its product. A closing of the same house locks its inventory, the
    house's locations and its articles by number, then the rows
    ('Inventur.zum_abschluss_sperren'). In the order of the file the two
    could each hold what the other waited for, and PostgreSQL broke one off
    (until 10/2026). Locked first and by number, the import keeps the order
    every path keeps: a closing waits at its first article until the import
    is through, or the import until the closing is. Locked are the house's
    articles under the plan's numbers (vacating finds them by number), the
    rows' own, and every article of the products the rows name.
    """
    nummern = {zeile["vorhanden_pk"] for zeile in zeilen if zeile.get("vorhanden_pk")}
    artikelnummern = {zeile["artikelnummer"] for zeile in zeilen if zeile.get("artikelnummer")}
    waren = {zeile["ware"].pk for zeile in zeilen if zeile.get("ware") is not None}
    nummern |= set(
        Hausartikel.objects.filter(haus=haus, artikelnummer__in=artikelnummern).values_list(
            "pk", flat=True
        )
    )
    if waren:
        nummern |= set(Hausartikel.objects.filter(ware_id__in=waren).values_list("pk", flat=True))
    if nummern:
        artikel_sperren(nummern)


def _bestand_unter_der_sperre(zeilen: list[dict], haus: Haus) -> list[str]:
    """Why the plan no longer fits the stock, read once its articles are locked ('_artikel_zuerst').

    The form measured it before any lock ('BaseImportFormSet._bestand_pruefen').
    Between that and the locks someone may have deleted, discontinued or
    brought back a row the plan changes, given a new row's number to another
    carried one, or deleted a product a row chose. Until 10/2026 adopting
    wrote the plan regardless: a discontinued row was renamed as if it were
    carried, and a deleted row, a taken number or a deleted product ended in
    a server error. Asked for the rows that write ('_wie_geplant'). Back
    come the messages, the first one saying that nothing was adopted. Empty
    while the plan fits.
    """
    schreibend = [zeile for zeile in zeilen if zeile.get("vorgehen") not in (None, "", UEBERGEHEN)]
    saetze = saetze_je_nummer(haus, {zeile["artikelnummer"] for zeile in schreibend})
    meldungen = [
        _(
            "Diese Zeile passt nicht mehr zu dem, was %(haus)s unter %(nummer)s hat. Bitte die "
            "Datei neu einlesen."
        )
        % {"haus": haus, "nummer": zeile["artikelnummer"]}
        for zeile in schreibend
        if not _wie_geplant(zeile, saetze.get(zeile["artikelnummer"], []))
    ]
    gewaehlt = {zeile["ware"].pk for zeile in schreibend if zeile.get("ware") is not None}
    noch_da = set(Ware.objects.filter(pk__in=gewaehlt).values_list("pk", flat=True))
    meldungen += [
        _(
            "%(nummer)s: Die gewählte Ware „%(ware)s“ gibt es nicht mehr. Bitte die Datei "
            "neu einlesen."
        )
        % {"nummer": zeile["artikelnummer"], "ware": zeile["ware"].name}
        for zeile in schreibend
        if zeile.get("ware") is not None and zeile["ware"].pk not in noch_da
    ]
    if not meldungen:
        return []
    return [
        _("Übernommen wurde nichts. Seit dem Einlesen hat sich der Bestand von %(haus)s geändert.")
        % {"haus": haus},
        *meldungen,
    ]


def _wie_geplant(zeile: dict, saetze: list[Hausartikel]) -> bool:
    """Whether what the house has under the row's number is still what the plan found there.

    The row a plan row concerns still stands under its number, carried or
    discontinued as the plan found it. A new row finds no carried one there
    under another designation: the house carries a number once. A twin by
    designation is left to the steps, which report it row by row as for
    every path past the form ('_zwilling_melden').
    """
    if (pk := zeile.get("vorhanden_pk")) is None:
        return not any(satz.status in GEFUEHRT and satz.name != zeile["name"] for satz in saetze)
    satz = next((satz for satz in saetze if satz.pk == pk), None)
    return satz is not None and (satz.status in GEFUEHRT) == (zeile.get("art") != EINGESTELLT)


def _platz_machen(
    zeilen: list[dict],
    haus: Haus,
    bericht: Importbericht,
    vorhanden: set[tuple[str, str]],
    einheiten: dict[str, Basiseinheit],
) -> set[int]:
    """Discontinues whoever "Replace" and "Bring back" take the number from.

    And what is missing from the file and is discontinued: this row too may
    only release its product for another row of the same plan.

    Back come the rows (by 'id') whose replacing may go on. Where the new row
    would be a twin or lacks its conversion, the old one stays as it is.
    Otherwise the number would stand without a carried article in the end.
    """
    geraeumt: set[int] = set()
    for zeile in zeilen:
        if zeile.get("art") == FEHLT and zeile["vorgehen"] == EINSTELLEN:
            artikel = Hausartikel.objects.get(pk=zeile["vorhanden_pk"])
            artikel.einstellen()
            bericht.artikel_fehlend_eingestellt.append(f"{artikel.artikelnummer} {artikel.name}")
        elif zeile["vorgehen"] == ERSETZEN:
            if _zwilling_melden(zeile, haus, bericht, vorhanden):
                continue
            # Nor without the conversion: the new row would not arise, and
            # until 10/2026 the number then stood without a carried article.
            gewaehlt = zeile.get("ware")
            if gewaehlt is not None:
                einheit = _einheit_der_zeile(zeile, gewaehlt, einheiten, bericht)[0]
                if einheit is None:
                    continue
            Hausartikel.objects.get(pk=zeile["vorhanden_pk"]).einstellen()
            bericht.artikel_ersetzt.append(zeile["artikelnummer"])
            geraeumt.add(id(zeile))
        elif zeile["vorgehen"] == ZURUECKHOLEN:
            for inhaber in Hausartikel.objects.filter(
                haus=haus, artikelnummer=zeile["artikelnummer"], status__in=GEFUEHRT
            ).exclude(pk=zeile["vorhanden_pk"]):
                inhaber.einstellen()
                bericht.artikel_ersetzt.append(zeile["artikelnummer"])
    return geraeumt


def _zurueckholen(zeile: dict, bericht: Importbericht) -> None:
    """Reactivate the discontinued row, on the model's path and with its check."""
    artikel = Hausartikel.objects.get(pk=zeile["vorhanden_pk"])
    try:
        artikel.aktivieren()
    except ValidationError as fehler:
        bericht.fehler.append(" ".join(fehler.messages))
        return
    bericht.artikel_zurueckgeholt.append(f"{artikel.artikelnummer} {artikel.name}")


def _fehlenden_deaktivieren(zeile: dict, bericht: Importbericht) -> None:
    """An article the file no longer knows goes to inactive.

    Inactive keeps number, place in the counting list and product. Whoever
    still stands on the shelf after all can keep being counted.
    """
    artikel = Hausartikel.objects.get(pk=zeile["vorhanden_pk"])
    artikel.deaktivieren()
    bericht.artikel_fehlend_inaktiv.append(f"{artikel.artikelnummer} {artikel.name}")


def _warengruppen_sicherstellen(
    zeilen: list[dict], bericht: Importbericht
) -> dict[str, Warengruppe]:
    """Missing product groups arise by themselves when executing.

    They are not editable in the form: the name comes from the inventory
    management system and is recognised by it on the next import.
    """
    namen = {zeile["warengruppe"] or OHNE_WARENGRUPPE for zeile in zeilen}
    vorhanden = {gruppe.name: gruppe for gruppe in Warengruppe.objects.filter(name__in=namen)}
    for name in sorted(namen - set(vorhanden)):
        vorhanden[name] = Warengruppe.objects.create(name=name)
        bericht.warengruppen_neu.append(name)
    return vorhanden


def warename(zeile: dict) -> str:
    """What a newly arising product should be called.

    Left empty means: the designation of the house article. An own name is
    worthwhile where the house-internal one deviates from the product: "Cola
    0,33 EK" here, "Cola 0,33 l" as the cross-house identity.
    """
    return (zeile.get("neuer_ware") or "").strip() or zeile["name"]


def _ignorierte_ware(
    zeile: dict,
    einheiten: dict[str, Basiseinheit],
    nicht_zaehlbar: Warenfamilie,
    bericht: Importbericht,
) -> Ware | None:
    """The product in "Not countable" an ignored row hangs on.

    The chosen or the one of the same name, otherwise a new one. A product a
    house really carries is not ignored along with it: that would concern
    every house, and the form already rejects the case. Here it remains a
    message. What 'pruefe_familienwechsel' otherwise forbids (a product in a
    recipe) applies here too: the import is no way around the rule.
    """
    ware = zeile.get("ware") or (Ware.objects.filter(name=warename(zeile)).first())
    if ware is None:
        einheit = einheiten.get(zeile["einheit"]) or Basiseinheit.objects.get(
            dimension=Dimension.STUECK, faktor_zur_referenz=1
        )
        ware = Ware.objects.create(
            name=warename(zeile), basiseinheit=einheit, warenfamilie=nicht_zaehlbar
        )
        bericht.ware_neu.append(ware.name)
        return ware
    if ware.warenfamilie_id == nicht_zaehlbar.pk:
        return ware
    if ware.hausartikel.exclude(status=Artikelstatus.IGNORIERT).exists():
        bericht.fehler.append(
            _(
                "%(nummer)s: „%(name)s“ wird als Ware geführt. Die Zeile wurde nicht auf "
                "nicht zählbar gesetzt. Dafür braucht sie eine eigene Bezeichnung."
            )
            % {"nummer": zeile["artikelnummer"], "name": ware.name}
        )
        return None
    try:
        ware.pruefe_familienwechsel(nicht_zaehlbar)
    except ValidationError as fehler:
        grund = " ".join(fehler.messages)
        bericht.fehler.append(
            _("%(nummer)s: „%(name)s“ %(grund)s")
            % {"nummer": zeile["artikelnummer"], "name": ware.name, "grund": grund}
        )
        return None
    nicht_zaehlbar.aufnehmen(ware)
    return ware


def art_der_zeile(zeile: dict) -> int | None:
    """The chosen type as a number. From the form it comes as text, past it as a number."""
    art = zeile.get("inventurart")
    return int(art) if art not in (None, "") else None


def _ware_sichern(
    zeile: dict, einheiten: dict[str, Basiseinheit], bericht: Importbericht
) -> tuple[Ware | None, bool]:
    """Fetch the chosen product or create one with the name. Says whether it is new."""
    if (gewaehlt := zeile.get("ware")) is not None:
        return gewaehlt, False

    einheit = einheiten.get(zeile["einheit"])
    if einheit is None:
        bericht.fehler.append(
            _("%(nummer)s: unbekannte Einheit „%(einheit)s“, übersprungen.")
            % {"nummer": zeile["artikelnummer"], "einheit": zeile["einheit"]}
        )
        return None, False

    # 'get_or_create': two rows of the same run may point to the same new
    # product. The name is unique, a second 'create' would run into an
    # IntegrityError.
    ware, neu = Ware.objects.get_or_create(
        name=warename(zeile),
        defaults={"basiseinheit": einheit},
    )
    if neu:
        bericht.ware_neu.append(ware.name)
    # An existing product keeps its unit. The row brings its own, and the
    # house's row carries it ('_anlegen').
    return ware, neu


def _anlegen(
    zeile: dict,
    ware: Ware,
    lauf: Lauf,
    bericht: Importbericht,
    art_vorgabe: int | None = None,
) -> bool:
    """Creates the row's house article. Says whether it arose."""
    status = STATUS_JE_VORGEHEN[status_der_zeile(zeile)]
    if _zwilling_melden(zeile, lauf.haus, bericht, lauf.vorhanden):
        return False
    einheit, faktor = _einheit_der_zeile(zeile, ware, lauf.einheiten, bericht)
    if einheit is None:
        return False
    Hausartikel.objects.create(
        haus=lauf.haus,
        name=zeile["name"],
        artikelnummer=zeile["artikelnummer"],
        quelle=lauf.quelle,
        ware=ware,
        warengruppe=lauf.warengruppen[zeile["warengruppe"] or OHNE_WARENGRUPPE],
        status=status,
        basiseinheit=einheit,
        faktor_zur_ware=faktor,
        # Empty: the rule decides ('Inventurart.vorschlag_fuer'). For a new
        # product the caller already knows the answer.
        inventurart_id=art_der_zeile(zeile) or art_vorgabe,
    )
    lauf.vorhanden.add((zeile["artikelnummer"], zeile["name"]))
    beschriftung = f"{zeile['artikelnummer']} {zeile['name']}"
    bericht.artikel_neu.append(beschriftung)
    if status == Artikelstatus.INAKTIV:
        bericht.artikel_inaktiv.append(beschriftung)
    elif status == Artikelstatus.EINGESTELLT:
        bericht.artikel_eingestellt.append(beschriftung)
    elif status == Artikelstatus.IGNORIERT:
        bericht.artikel_ignoriert.append(beschriftung)
    return True


def _zwilling_melden(
    zeile: dict, haus: Haus, bericht: Importbericht, vorhanden: set[tuple[str, str]]
) -> bool:
    """House, number and designation together exist only once.

    The model's rule ('Hausartikel.pruefe_eindeutig'). The form already checks
    it. Here it stands for the paths past it, because 'create' calls no
    'clean'. 'vorhanden' holds every pair the house carries, so only a twin
    costs a query: the one that names its status.
    """
    if (zeile["artikelnummer"], zeile["name"]) not in vorhanden:
        return False
    doppelt = Hausartikel.objects.filter(
        haus=haus, artikelnummer=zeile["artikelnummer"], name=zeile["name"]
    ).first()
    if doppelt is None:
        return False
    bericht.fehler.append(
        _(
            "%(nummer)s „%(name)s“ gibt es in %(haus)s schon (%(status)s) und wurde nicht "
            "noch einmal angelegt."
        )
        % {
            "nummer": zeile["artikelnummer"],
            "name": zeile["name"],
            "haus": haus,
            "status": doppelt.get_status_display().lower(),
        }
    )
    return True


def _aktualisieren(
    zeile: dict,
    ware: Ware,
    warengruppen: dict[str, Warengruppe],
    einheiten: dict[str, Basiseinheit],
    bericht: Importbericht,
) -> bool:
    """For "Change" and "Rename": the carried row takes over what the plan row says.

    Name and product always, the type where the row names one, product group
    and unit as far as a conflict chose them ('uebernimmt'). A twin under the
    new name leaves the row as it was, and so do a missing conversion and a
    change of unit while an open inventory holds numbers in the old one. Says
    whether it was written.
    """
    artikel = Hausartikel.objects.select_related("basiseinheit", "ware__basiseinheit").get(
        pk=zeile["vorhanden_pk"]
    )
    #: The unit of the product the row carried so far: its factor refers to it.
    bisher = artikel.ware.basiseinheit
    artikel.name = zeile["name"]
    if (doppelt := artikel.gleichnamiger()) is not None:
        bericht.fehler.append(
            _(
                "%(nummer)s „%(name)s“ gibt es in %(haus)s schon (%(status)s). Der "
                "vorhandene Satz blieb, wie er war."
            )
            % {
                "nummer": artikel.artikelnummer,
                "name": zeile["name"],
                "haus": artikel.haus,
                "status": doppelt.get_status_display().lower(),
            }
        )
        return False
    artikel.ware = ware
    if not _einheit_setzen(artikel, zeile, ware, einheiten, bericht, bisher):
        return False
    try:
        # The form already refuses it. Here for the paths past it. A new
        # product whose factor a frozen result could not hold is asked here
        # too: refused only by 'save()', it took the whole import back with
        # an error page (until 10/2026). So is a product in "Not countable"
        # for an article counted in a running inventory: saving refuses that
        # under the rows' lock.
        artikel.pruefe_einheitenwechsel()
        artikel.pruefe_ergebnisfaktoren()
        if ware.ist_nicht_zaehlbar:
            artikel.pruefe_ignorierbar()
    except ValidationError as fehler:
        bericht.fehler.append(f"{artikel.artikelnummer}: {' '.join(fehler.messages)}")
        return False
    if uebernimmt(zeile, WARENGRUPPE):
        artikel.warengruppe = warengruppen[zeile["warengruppe"] or OHNE_WARENGRUPPE]
    if (art := art_der_zeile(zeile)) is not None:
        artikel.inventurart_id = art
    artikel.save(
        update_fields=[
            "name",
            "ware",
            "warengruppe",
            "basiseinheit",
            "faktor_zur_ware",
            "inventurart",
            "geaendert_am",
        ]
    )
    bericht.artikel_geaendert.append(f"{artikel.artikelnummer} {artikel.name}")
    return True


def _einheit_setzen(
    artikel: Hausartikel,
    zeile: dict,
    ware: Ware,
    einheiten: dict[str, Basiseinheit],
    bericht: Importbericht,
    bisher: Basiseinheit,
) -> bool:
    """Unit and factor of the row that is changed. False if the conversion is missing.

    If the unit is adopted, it comes from the row. Otherwise it stays, the
    product possibly not ('faktor_behalten'). 'bisher' is the unit of the
    product the row carried so far.
    """
    if uebernimmt(zeile, EINHEIT):
        einheit, faktor = _einheit_der_zeile(zeile, ware, einheiten, bericht)
        if einheit is None:
            return False
        artikel.basiseinheit = einheit
        artikel.faktor_zur_ware = faktor
        return True
    if not braucht_faktor(artikel.basiseinheit.dimension, ware.basiseinheit.dimension):
        artikel.faktor_zur_ware = None
        return True
    faktor = faktor_behalten(
        artikel, bisher, ware, einheiten.get(zeile["einheit"]), zeile.get("faktor_zur_ware")
    )
    if faktor is None:
        bericht.fehler.append(
            _(
                "%(nummer)s: Das Haus zählt in %(haus)s, „%(name)s“ wird in %(ware)s "
                "geführt. Ohne die Umrechnung blieb der Satz, wie er war."
            )
            % {
                "nummer": artikel.artikelnummer,
                "name": ware.name,
                "ware": ware.basiseinheit.kurz,
                "haus": einheit_kurz(artikel.basiseinheit.kennung),
            }
        )
        return False
    artikel.faktor_zur_ware = faktor
    return True


def faktor_behalten(
    artikel: Hausartikel,
    bisher: Basiseinheit,
    ware: Ware,
    datei: Basiseinheit | None,
    getippt: Decimal | None,
) -> Decimal | None:
    """The factor of a row that keeps its unit, to the product it carries afterwards.

    The plan asks for the factor against the file's unit. Where that is the
    row's own, the typed one applies as it is. Where the file names another
    unit, the row keeps its own factor, converted into the unit of the
    product: the person adopted something else, not a new weight. Only
    without one does the typed factor count, converted into the row's unit
    and only within the dimension of the file's unit. Until 10/2026 the
    factor typed for the file's unit landed unconverted on the kept one,
    "kg per ml" as "kg per l", and every frozen result in that unit followed
    it ('ergebnisse_nachziehen'): 10 kg became 0.01 kg.
    """
    eigene, ziel = artikel.basiseinheit, ware.basiseinheit
    if getippt and datei is not None and datei.pk == eigene.pk:
        return getippt
    kandidaten = []
    if artikel.faktor_zur_ware and bisher.dimension == ziel.dimension:
        kandidaten.append(
            artikel.faktor_zur_ware * bisher.faktor_zur_referenz / ziel.faktor_zur_referenz
        )
    if getippt and datei is not None and datei.dimension == eigene.dimension:
        kandidaten.append(getippt * eigene.faktor_zur_referenz / datei.faktor_zur_referenz)
    for faktor in kandidaten:
        gerundet = faktor.quantize(FAKTOR_MINDESTENS, rounding=ROUND_HALF_UP)
        # What the column holds: six places before the point, twelve after.
        if FAKTOR_MINDESTENS <= gerundet < FAKTOR_HOECHSTENS:
            return gerundet
    return None


def _gebinde_sichern(
    ware: Ware,
    gebinde: dict[str, Decimal],
    bericht: Importbericht,
    nur_ergaenzen: bool = False,
    fremd: bool = False,
) -> tuple[int, int]:
    """Reconcile the pack field against the product: create, rename, retire.

    Matching goes by **quantity**, not by name. The quantity is unique per
    product, the name is only incidentally, and exactly that should be
    changeable: "Kiste(n)" becomes "Kasten" without a second pack arising and
    the counted rows staying on the old one.

    What is no longer in the field becomes **inactive**, not deleted. Count
    positions hang on a pack, and an inventory from the year before last
    should stay readable. An inactive one comes back as soon as its quantity
    is in the field again: the plan puts it there under its own name when
    the file carries it, with a hint ('_gebindefeld'), and the report counts it.

    Packs hang on the product and apply across all houses. The field
    therefore shows everything the product carries ('_gebinde_vorbelegen').
    Whoever takes something away here takes it away from all houses. The
    quantities here are already in the product's unit. With 'nur_ergaenzen'
    nothing is retired. With 'fremd' the field was not filled for this
    product: nothing is retired, and its packs stay as they are. They keep
    their names, and an inactive one stays inactive, for nobody saw it in
    the field.

    Back come the packs created and those that stayed as they were because
    only additions were made: the active ones the field did not name, and
    with 'fremd' the inactive ones it named.
    """
    gefuehrt = {satz.faktor_in_basiseinheit: satz for satz in Gebinde.objects.filter(ware=ware)}
    # The names of the product's packs, from the same query: asking the
    # database per name cost two queries per pack until 10/2026.
    namen = {satz.name: satz for satz in gefuehrt.values()}

    def belegt(name: str, ausser: Gebinde) -> bool:
        satz = namen.get(name)
        return satz is not None and satz is not ausser

    angelegt = 0
    behalten = 0
    for im_feld, menge in gebinde.items():
        vorhanden = gefuehrt.pop(menge, None)
        if vorhanden is None:
            _gebinde_anlegen(ware, im_feld, menge, namen, bericht)
            angelegt += 1
            continue
        if fremd:
            if not vorhanden.aktiv:
                behalten += 1
            continue
        name, einzeln = im_feld, _ist_einzeln(ware, im_feld, menge)
        if vorhanden.name != name and belegt(name, ausser=vorhanden):
            bericht.fehler.append(
                _(
                    "„%(ware)s“: „%(name)s“ führt die Ware schon mit einer anderen Menge. "
                    "„%(bisher)s“ heißt weiter so."
                )
                % {"ware": ware.name, "name": name, "bisher": vorhanden.name}
            )
            continue
        if (vorhanden.name, vorhanden.ist_einzeln, vorhanden.aktiv) == (name, einzeln, True):
            continue
        if vorhanden.name != name:
            bericht.gebinde_umbenannt += 1
            namen.pop(vorhanden.name, None)
            namen[name] = vorhanden
        if not vorhanden.aktiv:
            bericht.gebinde_reaktiviert += 1
        vorhanden.name = name
        vorhanden.ist_einzeln = einzeln
        vorhanden.aktiv = True
        vorhanden.save(update_fields=["name", "ist_einzeln", "aktiv"])

    for verschwunden in gefuehrt.values():
        if not verschwunden.aktiv:
            continue
        if nur_ergaenzen or fremd:
            behalten += 1
            continue
        verschwunden.aktiv = False
        verschwunden.save(update_fields=["aktiv"])
        bericht.gebinde_stillgelegt += 1
    return angelegt, behalten


def _gebinde_anlegen(
    ware: Ware, name: str, menge: Decimal, namen: dict[str, Gebinde], bericht: Importbericht
) -> None:
    """A quantity the product does not carry yet becomes a pack, under a name that is free.

    The plan proposes a free name itself ('_gebindefeld'). Here it is needed
    where the plan could not show the product's packs, or where someone
    typed a taken one. Until 10/2026 the pack was skipped if the name with
    its quantity was taken as well, and the quantity from the file was lost.
    """
    bezeichnung = name
    if bezeichnung in namen:
        bezeichnung = freier_gebindename(name, menge, namen)
        bericht.fehler.append(
            _(
                "„%(ware)s“: „%(name)s“ gibt es schon mit einer anderen Menge. "
                "Angelegt wurde „%(neu)s“."
            )
            % {"ware": ware.name, "name": name, "neu": bezeichnung}
        )
    namen[bezeichnung] = Gebinde.objects.create(
        ware=ware,
        name=bezeichnung,
        ist_einzeln=_ist_einzeln(ware, name, menge),
        faktor_in_basiseinheit=menge,
    )


def _ist_einzeln(ware: Ware, name: str, menge: Decimal) -> bool:
    """Whether this pack is the loose single unit.

    There is one only for piece goods and only for exactly one. A one-litre
    bottle rightly carries its name. The import assigns the name itself
    ('scripts.normalize'), whoever enters it by hand means the same.
    """
    return (
        name == EINZELN_NAME and menge == 1 and ware.basiseinheit.kennung == Einheitenkennung.STUECK
    )
