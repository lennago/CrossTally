"""Change the base unit of a product and convert everything that is in it.

The unit is more than a label: pack quantities and recipe quantities are "in
the base unit of the product". Whoever only switches the field reinterprets
them: the crate holds "20 kg" all of a sudden instead of "20 pieces" (that
is how it was until 09/2026). That is why a **factor** belongs to the
change: how many new units one old unit is.

- **Within a dimension** the unit knows it itself: a gram is 0.001 kg, a
  litre 1000 millilitres. Nobody is asked.
- **Across dimensions** only whoever knows the product knows: a piece
  weighs 0.125 kg. Often the product says it itself, because it carries a
  conversion into the new dimension ("750 ml je Stk."). Then that counts
  where nothing is entered (since 10/2026). Without either no change
  happens.

Converted is what is a **quantity in the product's unit**. What is a count
of packs stays: the input of a count position, the default quantity of the
counting list. There the pack computes, and its quantity is converted.
A **share in percent** of a weighed mix stays too ('Rezepturartikel.anteilig'):
it is no quantity in a unit. The way from the gram into the product goes
through its weight per unit, and that follows the new unit by itself.
The **conversions of the product** ('WareUmrechnung') scale along: what
one old unit was in grams is spread over 'faktor' new ones. If the
dimension changes, the old one becomes a foreign dimension whose factor is
known exactly, and a row for the new one goes, because there the unit
computes itself. Weighings are in grams and find their way into the new
unit through the mass row.

**Target stocks and frozen results stay**: they are in the house's unit,
and that does not change here (since 09/2026, before that they were
converted along). Instead the **conversion of every house article** is
updated: whoever counted in the product's unit so far keeps counting in it
and gets the factor for that. Whoever already carried one gets it
recomputed.
"""

from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.translation import gettext as _

from .models import (
    EINZELN_NAME,
    FAKTOR_HOECHSTENS,
    FAKTOR_NACHKOMMASTELLEN,
    GEBINDE_HOECHSTMENGE,
    Basiseinheit,
    Einheitenkennung,
    Gebinde,
    Hausartikel,
    Ware,
    eingabe_zu_faktor,
    ergebnisfaktor_zu_gross,
    faktor_gerundet,
    faktor_zu_eingabe,
    menge_kurz,
    referenz_kurz,
    umrechnung,
)

#: The precision of the columns. Below it the quantity would be lost when saving.
GEBINDE_STELLEN = Decimal("0.001")
REZEPTUR_STELLEN = Decimal("0.000001")
FAKTOR_STELLEN = Decimal(1).scaleb(-FAKTOR_NACHKOMMASTELLEN)
#: The first quantity a recipe row no longer holds: twelve digits, six of them
#: after the point ('Rezepturkomponente.menge_in_basiseinheit'). PostgreSQL
#: rejects more, and SQLite would store it.
REZEPTUR_HOECHSTMENGE = Decimal("1e6")


def _gerundet(wert: Decimal, stellen: Decimal) -> Decimal:
    return wert.quantize(stellen, rounding=ROUND_HALF_UP)


def umrechnungsfaktor(
    alt: Basiseinheit, neu: Basiseinheit, angabe: Decimal | None, ware: Ware | None = None
) -> Decimal:
    """How many new units one old unit is.

    Within a dimension computed from the units. An entry is then skipped, it
    could only be wrong. Across dimensions the entry counts, and without one
    what 'ware' already knows about the new dimension ('bekannter_faktor').
    Without either the change is refused. Merging passes no product: there
    both sides may carry a conversion, and the two could contradict each
    other.
    """
    if alt.dimension == neu.dimension:
        return alt.faktor_zur_referenz / neu.faktor_zur_referenz
    if angabe is not None and angabe > 0:
        return angabe
    if ware is not None and (bekannt := bekannter_faktor(ware, alt, neu)) is not None:
        return bekannt
    raise ValidationError(
        {
            "umrechnung": _(
                "Von %(alt)s nach %(neu)s wechselt die Dimension. Wie viele %(neu)s "
                "eine Einheit in %(alt)s ist, weiß nur, wer die Ware kennt: hier "
                "eintragen, oder vorher bei den Umrechnungen der Ware. Ohne das ließen "
                "sich Gebinde und Rezepturen nicht umrechnen."
            )
            % {"alt": alt.kurz, "neu": neu.kurz}
        }
    )


def bekannter_faktor(ware: Ware, alt: Basiseinheit, neu: Basiseinheit) -> Decimal | None:
    """New units per old unit, from the product's conversion into the new dimension, if any.

    Read as the form shows the conversion ('faktor_zu_eingabe'), not as the
    column holds it. "750 ml je Stk." is stored as 0.001333333333 pieces per
    millilitre, and its reciprocal would leave the product with
    750.0000001875 ml per piece after the change. Read back as the 750 the
    form shows and divided in full precision, it stays 750.
    """
    gespeichert = ware.faktoren().get(neu.dimension)
    if gespeichert is None:
        return None
    gezeigt = faktor_zu_eingabe(alt.dimension, neu.dimension, gespeichert)
    referenzmenge = eingabe_zu_faktor(alt.dimension, neu.dimension, gezeigt)
    return referenzmenge / neu.faktor_zur_referenz


def umrechnungen_danach(
    ware: Ware, alt: Basiseinheit, neu: Basiseinheit, faktor: Decimal
) -> dict[str, Decimal]:
    """The product's conversions after the change, per foreign dimension.

    Every stored factor is per old unit and now spreads over 'faktor' new
    ones. The row of the new dimension goes: there the unit computes
    itself. If the dimension changes, the old one is foreign from now on,
    and one new unit is 1/'faktor' old units, in its reference unit.

    A conversion that outgrows its column stops the change before one is
    written: a piece of 1 mg makes a million pieces per kg. Until 10/2026
    PostgreSQL refused it with a numeric overflow, an error page, and
    SQLite stored it. Rounded is only what the column can hold
    ('faktor_gerundet'): 999999 ml per piece over an entry of 1e-12 kg per
    piece raised 'InvalidOperation' while rounding, an error page as well,
    before this check could speak (until 10/2026).
    """
    danach = {
        dimension: faktor_gerundet(wert / faktor)
        for dimension, wert in ware.faktoren().items()
        if dimension != neu.dimension
    }
    if alt.dimension != neu.dimension:
        danach[alt.dimension] = faktor_gerundet(alt.faktor_zur_referenz / faktor)
    if fehler := [
        _(
            "Die Umrechnung der Ware wäre danach %(menge)s %(referenz)s je %(einheit)s. Eine "
            "Umrechnung fasst höchstens %(grenze)s."
        )
        % {
            "menge": menge_kurz(wert),
            "referenz": referenz_kurz(dimension),
            "einheit": neu.kurz,
            "grenze": menge_kurz(FAKTOR_HOECHSTENS - FAKTOR_STELLEN),
        }
        for dimension, wert in danach.items()
        if wert >= FAKTOR_HOECHSTENS
    ]:
        raise ValidationError({"basiseinheit": fehler})
    return danach


def _gebinde_umrechnen(ware: Ware, faktor: Decimal, neu: Basiseinheit) -> list[Gebinde]:
    """The quantities per pack, checked before one is written.

    Three things can go wrong: a quantity becomes zero when rounding to three
    places, two packs fall on the same quantity, or one grows past the
    column (a sack of 20,000 kg is 20,000,000 g). Each would be stopped by the
    database, but with a message that tells nobody anything, and until
    10/2026 the third ended on the error page under PostgreSQL.

    The loose single unit exists only for piece goods and only as exactly
    one. Leaving pieces, it becomes an ordinary pack under its name: the
    database would otherwise refuse 0.125 kg as the single unit, and until
    10/2026 that ended in an error page. Back in pieces, the pack that
    carries its name and holds one becomes the single unit again. Returned
    are the packs that stopped being the single unit, so the page can say so.
    """
    gebinde = list(ware.gebinde.all())
    fehler = []
    neue_mengen: dict[Decimal, Gebinde] = {}
    ehemals_einzeln = []
    stueck = neu.kennung == Einheitenkennung.STUECK
    for satz in gebinde:
        menge = _gerundet(satz.faktor_in_basiseinheit * faktor, GEBINDE_STELLEN)
        einzeln = stueck and menge == 1 and (satz.ist_einzeln or satz.name == EINZELN_NAME)
        if satz.ist_einzeln and not einzeln:
            ehemals_einzeln.append(satz)
        satz.ist_einzeln = einzeln
        if menge < GEBINDE_STELLEN:
            fehler.append(
                _(
                    "Gebinde „%(name)s“ fasste danach weniger als %(menge)s %(einheit)s. "
                    "So klein lässt sich keine Menge führen."
                )
                % {
                    "name": satz.name,
                    "menge": menge_kurz(GEBINDE_STELLEN),
                    "einheit": neu.kurz,
                }
            )
        elif menge >= GEBINDE_HOECHSTMENGE:
            fehler.append(
                _(
                    "Gebinde „%(name)s“ fasste danach %(menge)s %(einheit)s. Ein Gebinde fasst "
                    "höchstens %(grenze)s %(einheit)s."
                )
                % {
                    "name": satz.name,
                    "menge": menge_kurz(menge),
                    "grenze": menge_kurz(GEBINDE_HOECHSTMENGE - GEBINDE_STELLEN),
                    "einheit": neu.kurz,
                }
            )
        elif menge in neue_mengen:
            fehler.append(
                _(
                    "Die Gebinde „%(eines)s“ und „%(anderes)s“ fassten danach beide "
                    "%(menge)s %(einheit)s und ließen sich nicht mehr unterscheiden."
                )
                % {
                    "eines": neue_mengen[menge].name,
                    "anderes": satz.name,
                    "menge": menge_kurz(menge),
                    "einheit": neu.kurz,
                }
            )
        else:
            neue_mengen[menge] = satz
        satz.faktor_in_basiseinheit = menge
    if fehler:
        raise ValidationError({"basiseinheit": fehler})

    # Per product the quantity is unique. If the quantities grow, the largest
    # first. Otherwise a small one on its way up would hit the old value of a large one.
    gebinde.sort(key=lambda satz: satz.faktor_in_basiseinheit, reverse=faktor > 1)
    for satz in gebinde:
        satz.save(update_fields=["faktor_in_basiseinheit", "ist_einzeln"])
    return ehemals_einzeln


def _rezepturen_umrechnen(ware: Ware, faktor: Decimal, neu: Basiseinheit) -> None:
    """The quantities per unit of a recipe article, not the shares in percent.

    A share becomes a quantity only at runtime, through the product's weight
    per unit ('Rezepturkomponente.menge_je_einheit'), and that already
    follows the new unit. Converted along as well, 50 % became 50,000 % from
    kg to g, and every weighed mix with the product distributed a thousand
    times as much (until 10/2026).

    Checked before one is written, as the packs: a quantity that rounds to
    nothing, or one that outgrows its column (2,000 kg are 2,000,000 g).
    Until 10/2026 PostgreSQL refused the second with a numeric overflow, an
    error page, and SQLite stored it.
    """
    fehler = []
    zeilen = list(
        ware.verwendet_in.filter(rezepturartikel__anteilig=False).select_related("rezepturartikel")
    )
    for zeile in zeilen:
        zeile.menge_in_basiseinheit = menge = _gerundet(
            zeile.menge_in_basiseinheit * faktor, REZEPTUR_STELLEN
        )
        if menge < REZEPTUR_STELLEN:
            fehler.append(
                _(
                    "In der Rezeptur von „%(name)s“ bliebe von dieser Ware in %(einheit)s "
                    "nichts übrig."
                )
                % {"name": zeile.rezepturartikel.name, "einheit": neu.kurz}
            )
        elif menge >= REZEPTUR_HOECHSTMENGE:
            fehler.append(
                _(
                    "In der Rezeptur von „%(name)s“ stünden von dieser Ware danach %(menge)s "
                    "%(einheit)s. Eine Rezeptur fasst höchstens %(grenze)s %(einheit)s."
                )
                % {
                    "name": zeile.rezepturartikel.name,
                    "menge": menge_kurz(menge),
                    "grenze": menge_kurz(REZEPTUR_HOECHSTMENGE - REZEPTUR_STELLEN),
                    "einheit": neu.kurz,
                }
            )
    if fehler:
        raise ValidationError({"basiseinheit": fehler})
    for zeile in zeilen:
        zeile.save(update_fields=["menge_in_basiseinheit"])


def haeuser_pruefen(
    artikel: list[Hausartikel], alt: Basiseinheit, neu: Basiseinheit, faktor: Decimal
) -> None:
    """Refuses the change before a conversion of a house is written that outgrows its column.

    What one unit of a house is in the product's unit grows with 'faktor':
    a piece of 1,500 kg is 1,500,000 g, and the column holds six places
    before the point. So do the factors of the frozen results in another
    unit than the house's, which 'Hausartikel.ergebnisse_nachziehen'
    converts the same way, from 'alt' into 'neu'. Until 10/2026 PostgreSQL
    refused such a number with a numeric overflow, an error page, and SQLite
    stored it.
    """
    if faktor <= 1:
        # Up to one nothing grows: the stored factors fit, and a unit is at
        # most a thousand of another.
        return
    fehler = []
    grenze = menge_kurz(FAKTOR_HOECHSTENS - FAKTOR_STELLEN)
    for satz in artikel:
        einheit = satz.basiseinheit
        menge = _danach(einheit, alt, neu, satz.faktor_zur_ware, faktor)
        if menge is not None and menge >= FAKTOR_HOECHSTENS:
            fehler.append(
                _(
                    "„%(name)s“ in %(haus)s: 1 %(hauseinheit)s wäre danach %(menge)s "
                    "%(einheit)s. Eine Umrechnung fasst höchstens %(grenze)s."
                )
                % {
                    "name": satz.name,
                    "haus": satz.haus.name,
                    "hauseinheit": einheit.kurz,
                    "menge": menge_kurz(menge),
                    "einheit": neu.kurz,
                    "grenze": grenze,
                }
            )
        for ergebnis in satz.inventurergebnisse.exclude(einheit_id=einheit.pk).select_related(
            "einheit", "inventur__haus"
        ):
            menge = _danach(ergebnis.einheit, alt, neu, ergebnis.faktor_zur_ware, faktor)
            if menge is not None and menge >= FAKTOR_HOECHSTENS:
                fehler.append(
                    ergebnisfaktor_zu_gross(
                        satz.name, ergebnis.inventur, ergebnis.einheit.kurz, menge, neu.kurz
                    )
                )
    if fehler:
        raise ValidationError({"basiseinheit": fehler})


def _danach(
    einheit: Basiseinheit,
    alt: Basiseinheit,
    neu: Basiseinheit,
    bisher: Decimal | None,
    faktor: Decimal,
) -> Decimal | None:
    """What one unit of a house or a result is in the product's new unit, as it is written.

    None where nothing is written: the units compute it themselves, or the
    old conversion is unknown.
    """
    menge = umrechnung(einheit, alt, bisher)
    if menge is None or einheit.dimension == neu.dimension:
        return None
    return faktor_gerundet(menge * faktor)


def hausartikel_nachziehen(
    ware: Ware, alt: Basiseinheit, neu: Basiseinheit, faktor: Decimal
) -> None:
    """The conversion of every house to the product after the product changed its unit.

    The house keeps counting in its unit. What one unit of the house was in
    the old unit of the product, times 'faktor', it is in the new one.
    Within a dimension the units compute themselves, then the field stays
    empty. The inventory's rows stay untouched: they are in the house's unit.
    The frozen results keep their numbers too, only their factor follows
    ('Hausartikel.ergebnisse_nachziehen'). Checked before one is written
    ('haeuser_pruefen').
    """
    artikel_der_ware = list(ware.hausartikel.select_related("basiseinheit", "haus"))
    haeuser_pruefen(artikel_der_ware, alt, neu, faktor)
    for artikel in artikel_der_ware:
        bisher = umrechnung(artikel.basiseinheit, alt, artikel.faktor_zur_ware)
        if bisher is None:
            # A row without a factor across dimensions: the model does not allow
            # that. If it is there anyway, it gets none now.
            continue
        if artikel.basiseinheit.dimension == neu.dimension:
            artikel.faktor_zur_ware = None
        else:
            artikel.faktor_zur_ware = faktor_gerundet(bisher * faktor)
        artikel.save(update_fields=["faktor_zur_ware"])
        artikel.ergebnisse_nachziehen(alt, faktor)


@transaction.atomic
def mengen_umrechnen(ware: Ware, faktor: Decimal, alt: Basiseinheit) -> list[Gebinde]:
    """Bring everything that is in the product's unit into the new one with 'faktor'.

    The product itself already carries the new unit. The form saves it, and
    the messages here name it. 'alt' is the previous one, the houses'
    factors hang on it. If something fails, the caller rolls back the
    transaction including the product. Returned are the packs that were the
    loose single unit and are ordinary packs now ('_gebinde_umrechnen').
    """
    if faktor == 1 and alt.pk == ware.basiseinheit_id:
        return []
    ehemals_einzeln = _gebinde_umrechnen(ware, faktor, ware.basiseinheit)
    _rezepturen_umrechnen(ware, faktor, ware.basiseinheit)
    hausartikel_nachziehen(ware, alt, ware.basiseinheit, faktor)
    return ehemals_einzeln


@transaction.atomic
def einheit_wechseln(ware: Ware, neu: Basiseinheit, angabe: Decimal | None = None) -> Decimal:
    """The whole change in one go, for every path past the form.

    Returns the factor that was computed with.
    """
    alt = ware.basiseinheit
    if alt.pk == neu.pk:
        return Decimal("1")
    faktor = umrechnungsfaktor(alt, neu, angabe, ware)
    ware.geplante_faktoren = umrechnungen_danach(ware, alt, neu, faktor)
    ware.basiseinheit = neu
    ware.full_clean()
    ware.save(update_fields=["basiseinheit"])
    ware.umrechnungen_setzen(ware.geplante_faktoren)
    mengen_umrechnen(ware, faktor, alt)
    return faktor
