"""Result rows for display: computed while counting, frozen after closing."""

from dataclasses import dataclass
from decimal import Decimal

from django.db.models import Count, Max

from katalog.models import (
    GEFUEHRT,
    Basiseinheit,
    Hausartikel,
    Rezepturkomponente,
    nummer_sortierschluessel,
)

from ..models import (
    Inventur,
    Inventurergebnis,
    Inventurstatus,
    Zaehlposition,
)
from .display import differenz_klasse, menge_text, menge_text_mit_vorzeichen


@dataclass
class Ergebniszeile:
    """An article with actual, target and difference, computed or frozen.

    The field names are those of the 'Inventurergebnis'. That way the same
    template shows the running count and the closed state without knowing
    where the numbers come from.
    """

    hausartikel: Hausartikel
    gesamtmenge_basiseinheit: Decimal
    sollbestand: Decimal | None
    differenz: Decimal | None
    notiz: str = ""
    #: At closing the target stock counts as the result. Until then the row
    #: stays as it was counted. The mark only takes effect there.
    soll_statt_ist: bool = False
    #: Whether anything has been entered for this article at all. A row without
    #: a count stands with actual 0. Exactly so the closing freezes it.
    gezaehlt: bool = True
    #: After closing the frozen result behind the row. It carries the unit the
    #: house counted in then, and that stays when the house counts in another.
    ergebnis: Inventurergebnis | None = None

    @property
    def einheit(self) -> Basiseinheit:
        """The house's unit: that is how the target comes, that is how the result goes back.

        After closing the unit the result was frozen in, not the article's of today.
        """
        if self.ergebnis is not None:
            return self.ergebnis.einheit
        return self.hausartikel.basiseinheit

    @property
    def faktor_zur_ware_wirksam(self) -> Decimal | None:
        """How many units of the product one unit of the row is: frozen, or the article's.

        None for a frozen row without a way into the product
        ('Inventurergebnis.faktor_zur_ware_wirksam').
        """
        if self.ergebnis is not None:
            return self.ergebnis.faktor_zur_ware_wirksam
        return self.hausartikel.faktor_zur_ware_wirksam

    def in_wareneinheit(self, menge: Decimal | None) -> Decimal | None:
        """A quantity of the row in the product's unit, for sums across families and houses."""
        faktor = self.faktor_zur_ware_wirksam
        return None if menge is None or faktor is None else menge * faktor

    @property
    def artikelnummer(self) -> str:
        """Sort and search field. The check list sorts over attributes, not over SQL."""
        return self.hausartikel.artikelnummer

    @property
    def nummernschluessel(self) -> tuple | None:
        """The number as the column sorts it, None for an own article without one (last)."""
        nummer = self.hausartikel.artikelnummer
        return nummer_sortierschluessel(nummer) if nummer else None

    @property
    def warengruppe(self) -> str:
        return str(self.hausartikel.warengruppe.anzeigename)

    @property
    def warenfamilie(self) -> str:
        """Empty without a family. That way "Without product family" sorts to the end."""
        familie = self.hausartikel.ware.warenfamilie
        return str(familie.anzeigename) if familie else ""

    @property
    def differenz_gerundet(self) -> Decimal | None:
        """The difference as the row shows it and closing freezes it, in the unit's places.

        A difference by hand stands in its field as typed ('differenz_eingabe').
        Summed that way, three rows of -0.4 pieces made -1 at their family
        while every row and the closed sheet said 0 (until 10/2026).
        """
        return None if self.differenz is None else self.einheit.runden(self.differenz)

    @property
    def auffaellig(self) -> bool:
        """Whether the row should catch the eye: something is missing or there is too much.

        By the difference as the row shows it. One that rounds to 0 stands as
        "0" in green and is no deviation, on the website as in the app (until
        10/2026 the row was marked and stood under "Only with deviation").
        """
        return bool(self.differenz_gerundet)

    @property
    def ist_text(self) -> str:
        return menge_text(self.einheit.runden(self.gesamtmenge_basiseinheit))

    @property
    def soll_text(self) -> str:
        if self.sollbestand is None:
            return ""
        return menge_text(self.einheit.runden(self.sollbestand))

    @property
    def differenz_text(self) -> str:
        gerundet = self.differenz_gerundet
        return "" if gerundet is None else menge_text_mit_vorzeichen(gerundet)

    @property
    def differenz_klasse(self) -> str:
        gerundet = self.differenz_gerundet
        return "" if gerundet is None else differenz_klasse(gerundet)

    @property
    def differenz_eingabe(self) -> str:
        """The difference by hand as its field shows it: as typed, without a plus.

        Not rounded to the unit: whoever typed half a piece should find it
        again, and the field reads back what it shows ('Dezimalfeld').
        """
        return "" if self.differenz is None else menge_text(self.differenz)


def _artikel_geladen(pks) -> dict[int, Hausartikel]:
    """The house articles for the rows, including unit. One query for all."""
    return {
        artikel.pk: artikel
        for artikel in Hausartikel.objects.filter(pk__in=pks)
        .select_related(
            "basiseinheit",
            "ware__basiseinheit",
            "ware__warenfamilie__leiteinheit",
            "warengruppe",
            # The closing names the type of every uncounted article. Without
            # it that was one query per row of the block (measured 2026-10-09).
            "inventurart",
        )
        # The products' factors are needed by the sum per family in the check list.
        .prefetch_related("ware__umrechnungen")
    }


def betroffene_hausartikel(inventur: Inventur) -> set[int]:
    """Every house article a count position of this inventory points to.

    The still empty ones too. In the check list an article should be there as
    soon as it is to be counted at all, and not only once someone touched it.
    Recipe articles do not point to a house article but through their recipe
    to products. Those are resolved, as at closing, onto the carried house
    article of **this** house.
    """
    # Rows marked as deleted are deleted: they belong neither in the scope
    # nor in the sums.
    positionen = Zaehlposition.objects.filter(inventur=inventur, aktiv=True)
    betroffen = set(
        positionen.filter(artikel__hausartikel__isnull=False).values_list("artikel_id", flat=True)
    )

    waren = Rezepturkomponente.objects.filter(
        rezepturartikel__zaehlpositionen__inventur=inventur,
        rezepturartikel__zaehlpositionen__aktiv=True,
    ).values_list("ware_id", flat=True)
    if waren:
        betroffen |= set(
            Hausartikel.objects.filter(
                haus_id=inventur.haus_id, status__in=GEFUEHRT, ware_id__in=set(waren)
            ).values_list("pk", flat=True)
        )
    return betroffen


def zaehlstand(inventur: Inventur) -> str:
    """An id of what is stored as counted for an inventory.

    The check view compares it with the one it got when loading and says if
    something has come in from the counting app in the meantime. Every input
    there touches 'zuletzt_geaendert', a weighing and a deletion (which only
    marks as deleted) too. The count catches a row that really vanishes. One
    query, so the page can check often.
    """
    werte = Zaehlposition.objects.filter(inventur=inventur).aggregate(
        juengste=Max("zuletzt_geaendert"), anzahl=Count("pk")
    )
    juengste = werte["juengste"]
    return f"{werte['anzahl']}-{juengste.isoformat() if juengste else 'leer'}"


def ergebniszeilen(inventur: Inventur) -> list[Ergebniszeile]:
    """Actual, target and difference per house article, matching the inventory's state.

    Before closing it is computed so the difference is visible **while**
    counting. Afterwards the frozen rows come, because a closed state must not
    move any more.
    """
    if inventur.status == Inventurstatus.ABGESCHLOSSEN:
        return _sortiert(_eingefrorene_zeilen(inventur))
    return _sortiert(_gerechnete_zeilen(inventur))


def ergebniszeile(inventur: Inventur, hausartikel_id: int) -> Ergebniszeile | None:
    """The row of one article, computed for it alone. None where it is not in the list.

    The app changes one remark at a time and wants its row back. Until
    10/2026 every row was computed for that, as much work as the whole list.
    Only this article's positions are summed, and the recipe articles': a
    portion counted somewhere may land on it.
    """
    if inventur.status == Inventurstatus.ABGESCHLOSSEN:
        zeilen = _eingefrorene_zeilen(inventur, nur=hausartikel_id)
    else:
        zeilen = _gerechnete_zeilen(inventur, nur=hausartikel_id)
    return zeilen[0] if zeilen else None


def ist_betroffen(inventur: Inventur, hausartikel_id: int) -> bool:
    """'betroffene_hausartikel' for one article, without listing the others."""
    positionen = Zaehlposition.objects.filter(inventur=inventur, aktiv=True)
    if positionen.filter(artikel__hausartikel=hausartikel_id).exists():
        return True
    ware_id = (
        Hausartikel.objects.filter(pk=hausartikel_id, haus_id=inventur.haus_id, status__in=GEFUEHRT)
        .values_list("ware_id", flat=True)
        .first()
    )
    if ware_id is None:
        return False
    return Rezepturkomponente.objects.filter(
        ware_id=ware_id,
        rezepturartikel__zaehlpositionen__inventur=inventur,
        rezepturartikel__zaehlpositionen__aktiv=True,
    ).exists()


def _sortiert(zeilen: list[Ergebniszeile]) -> list[Ergebniszeile]:
    """By number as the app and the inventory system order it, own articles without one last."""
    return sorted(
        zeilen,
        key=lambda zeile: (
            zeile.nummernschluessel is None,
            zeile.nummernschluessel or (),
            zeile.hausartikel.name.casefold(),
        ),
    )


def _eingefrorene_zeilen(inventur: Inventur, nur: int | None = None) -> list[Ergebniszeile]:
    ergebnisse = Inventurergebnis.objects.filter(inventur=inventur)
    if nur is not None:
        ergebnisse = ergebnisse.filter(hausartikel_id=nur)
    ergebnisse = (
        ergebnisse.select_related(
            "einheit",
            "hausartikel__basiseinheit",
            "hausartikel__ware__basiseinheit",
            "hausartikel__ware__warenfamilie__leiteinheit",
            "hausartikel__warengruppe",
        )
        # As in '_artikel_geladen': the sum per family needs the products' factors.
        .prefetch_related("hausartikel__ware__umrechnungen")
    )
    return [
        Ergebniszeile(
            hausartikel=ergebnis.hausartikel,
            gesamtmenge_basiseinheit=ergebnis.gesamtmenge_basiseinheit,
            sollbestand=ergebnis.sollbestand,
            differenz=ergebnis.differenz,
            notiz=ergebnis.notiz,
            ergebnis=ergebnis,
        )
        for ergebnis in ergebnisse
    ]


def _gerechnete_zeilen(inventur: Inventur, nur: int | None = None) -> list[Ergebniszeile]:
    """The same computation closing freezes, only live. With 'nur' for one article.

    Listed is the whole scope of the inventory: what was counted, what has a
    target, what carries a remark and what there is a count position for at
    all. The last group stands with actual 0 and carries 'gezaehlt = False'.
    Without target and without remark closing does not freeze them, but when
    checking one wants to see that nothing has arrived there yet.

    With the difference by hand the typed one stands there, empty until
    someone types it, and no target, as at closing ('Inventur._ergebnisse').
    """
    ist = inventur.mengen_je_hausartikel(nur=nur)
    von_hand = inventur.differenz_von_hand
    soll = {} if von_hand else inventur.wirksame_sollbestaende(nur=nur)
    # The remark of an article in "Not countable" only where its row stands
    # anyway, as at closing ('Inventur.vermerke'). With the difference by hand
    # the closing knows the target and this page does not. The same comes out:
    # the target of an ignored article counts only where it was counted
    # itself ('wirksame_sollbestaende'), and so does one set while closing.
    vermerke = inventur.vermerke(nur=nur, mit_zeile=set(ist) | set(soll))
    if nur is None:
        pks = set(ist) | set(soll) | set(vermerke) | betroffene_hausartikel(inventur)
    else:
        pks = {nur} & (set(ist) | set(soll) | set(vermerke))
        if not pks and ist_betroffen(inventur, nur):
            pks = {nur}
    artikel = _artikel_geladen(pks)

    zeilen = []
    for pk in artikel:
        # Rounded once, to the places of the house's unit, and the difference
        # from the rounded numbers: what the row shows, and what the closing
        # freezes ('Inventur._ergebnisse'). Until 10/2026 each number was
        # rounded on its own, and a row read "actual 3, target 3, difference -1".
        einheit = artikel[pk].basiseinheit
        menge = einheit.runden(ist.get(pk, Decimal("0")))
        sollmenge = soll.get(pk)
        if sollmenge is not None:
            sollmenge = einheit.runden(sollmenge)
        text, uebernehmen, eingetragen = vermerke.get(pk, ("", False, None))
        if von_hand:
            # As typed: the field shows it again ('differenz_eingabe').
            differenz = eingetragen
        elif sollmenge is not None:
            differenz = menge - sollmenge
        else:
            differenz = None
        zeilen.append(
            Ergebniszeile(
                hausartikel=artikel[pk],
                gesamtmenge_basiseinheit=menge,
                sollbestand=sollmenge,
                differenz=differenz,
                notiz=text,
                soll_statt_ist=uebernehmen,
                gezaehlt=pk in ist,
            )
        )
    return zeilen
