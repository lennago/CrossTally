"""Reading in the target stock: what fits is written, the rest becomes a question."""

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from katalog.formats import (
    HOECHSTMENGE,
    STANDARD,
    Format,
    Sollbestandsdatei,
    Zeilenmeldungen,
    format_holen,
)
from katalog.models import (
    GEFUEHRT,
    Artikelstatus,
    Hausartikel,
    menge_kurz,
)

from ..models import (
    Inventur,
    Inventurstatus,
    Sollbestand,
    Sollgrund,
    Sollherkunft,
    Sollzeile,
)

#: Why no target comes in while the difference is typed in by hand.
OHNE_SOLL_VON_HAND = gettext_lazy(
    "Die Differenz wird bei dieser Inventur von Hand eingetragen. Ein Sollbestand kommt erst "
    "herein, wenn das auf der Seite der Inventur ausgeschaltet ist."
)

#: Why no target comes in once counting has begun.
NUR_IN_DER_VORBEREITUNG = gettext_lazy(
    "Der Sollbestand kommt in der Vorbereitung herein. Läuft die Zählung schon: "
    "„Zurück in die Vorbereitung“, dann die Datei. Die gezählten Mengen bleiben."
)


def soll_darf_herein(inventur: Inventur) -> None:
    """Locks the inventory until the target is written. In preparation only, not by hand.

    The page asked before, on the inventory as it had loaded it. Whoever
    switched the difference by hand on in between found no target yet and
    was allowed to ('differenz_von_hand_setzen'). The target came in all
    the same, and the inventory carried both, the target unseen (until
    10/2026). The switch locks the same row: one of the two waits and then
    sees the other. Without the key, as the closing and the app lock it.

    The status too: a start of counting in between let the file come in
    during the count and replace the target the apps already counted
    against (until 10/2026). The start writes the same row, so it waits or
    is seen here. Deleted meanwhile, the inventory is a 404.
    """
    gesperrt = get_object_or_404(Inventur.objects.select_for_update(no_key=True), pk=inventur.pk)
    if gesperrt.differenz_von_hand:
        raise ValidationError(OHNE_SOLL_VON_HAND)
    if gesperrt.status != Inventurstatus.VORBEREITUNG:
        raise ValidationError(NUR_IN_DER_VORBEREITUNG)


@dataclass
class Sollbestandbericht:
    """What became of the file."""

    #: Rows with a readable number and quantity.
    zeilen: int = 0
    #: Of those written as target stock: active articles that are due.
    zugeordnet: int = 0
    #: Of those left as a question: unknown, inactive, discontinued.
    offen: int = 0
    #: Annual goods in a monthly inventory: not due, but stored. Their target
    #: has a say as soon as someone adds them in the app.
    nicht_dran: int = 0
    #: Numbers from "Not countable". That the inventory management system
    #: carries a stock for them is known and not worth a message.
    nicht_zaehlbar: int = 0
    #: Inactive or discontinued articles the file lists with a stock of 0.
    #: Stored silently, without a message: the system agrees that nothing is
    #: there, so nobody has to answer "reactivate?" (decision of 2026-09-26).
    nicht_aktiv_leer: int = 0
    #: Target stocks by hand that the file replaced. The file applies, but
    #: whoever typed should learn that it is gone.
    von_hand_ersetzt: int = 0
    #: Rows of discontinued duplicates, added to the row they count as
    #: ('Hausartikel.zaehlt_als').
    zugerechnet: int = 0
    #: The house carries no article with a number: the file can hit nothing,
    #: the target comes by hand here. Without the hint every row would stand
    #: mute as "unknown".
    ohne_nummern: bool = False
    fehler: list[str] = field(default_factory=list)


def zu_viel_fuer_ein_soll(artikel: Hausartikel, summe: Decimal) -> str:
    """Why the stock of the duplicates was not added. For reading in and for the clarification."""
    return _(
        "%(nummer)s „%(name)s“: Mit den Dubletten wären es %(menge)s %(einheit)s, mehr als "
        "ein Sollbestand fasst. Ihr Bestand bleibt draußen."
    ) % {
        "nummer": artikel.artikelnummer,
        "name": artikel.name,
        "menge": menge_kurz(summe),
        "einheit": artikel.basiseinheit.kurz,
    }


class Zuordner:
    """Knows what the house carries for an article number, for all rows of a file.

    The rows are collected and written in one go ('schreiben'). Row by row a
    count sheet of 700 numbers cost over 4000 queries (measured on
    2026-09-27), each a round trip to the database.
    """

    def __init__(self, inventur: Inventur):
        self.inventur = inventur
        #: What 'schreiben' stores, keyed like the uniqueness of the two
        #: tables. A later row for the same key replaces the earlier one.
        self.sollbestaende: dict[int, Sollbestand] = {}
        self.sollzeilen: dict[str, Sollzeile] = {}
        # With the units: a duplicate converts its stock into the unit of the
        # row it counts as, and otherwise that would cost queries per row.
        artikel = list(
            Hausartikel.objects.filter(haus=inventur.haus).select_related(
                "basiseinheit", "ware__basiseinheit"
            )
        )
        #: The articles due in this inventory, by their type.
        umfang = inventur.zaehlumfang
        self.dran = {satz.pk for satz in artikel if umfang.umfasst(satz)}
        #: Every row by its number in the database, and the carried ones among
        #: them: a duplicate counts as one of those, possibly along a chain.
        self.alle_je_pk = {satz.pk: satz for satz in artikel}
        self.gefuehrt_je_pk = {satz.pk: satz for satz in artikel if satz.status in GEFUEHRT}
        #: What duplicates add to the row they count as, in its unit, and
        #: what the file called them. A reassigned number has no line of its
        #: own in the file: without the name its target would claim a file
        #: without saying what stood there.
        self.zugerechnet: dict[int, Decimal] = {}
        self.zugerechnet_name: dict[int, str] = {}
        self.gefuehrt: dict[str, Hausartikel] = {}
        self.ignoriert: set[str] = set()
        self.eingestellt: dict[str, list[Hausartikel]] = {}
        for satz in artikel:
            # An own article has no number. No file names it.
            if not satz.artikelnummer:
                continue
            if satz.status in GEFUEHRT:
                self.gefuehrt[satz.artikelnummer] = satz
            elif satz.status == Artikelstatus.IGNORIERT:
                self.ignoriert.add(satz.artikelnummer)
            else:
                self.eingestellt.setdefault(satz.artikelnummer, []).append(satz)

    def zuordnen(self, nummer: str, menge: Decimal, name: str, bericht: Sollbestandbericht):
        """A row to its place: target stock, open question or silently skipped."""
        if (artikel := self.gefuehrt.get(nummer)) is not None:
            dran = artikel.pk in self.dran
            # What is not due is stored nonetheless and is no question even when
            # inactive: it only has a say once someone adds it
            # ('Inventur.wirksame_sollbestaende').
            if artikel.aktiv or not dran:
                self._speichern(artikel, menge, name)
                if dran:
                    bericht.zugeordnet += 1
                else:
                    bericht.nicht_dran += 1
                return
            if menge == 0:
                self._still_speichern(artikel, menge, name, bericht)
                return
            self._offen(nummer, menge, name, Sollgrund.INAKTIV, artikel, bericht)
            return
        if nummer in self.ignoriert:
            bericht.nicht_zaehlbar += 1
            return
        alter = self._eingestellter(nummer, name)
        if alter is None:
            self._offen(nummer, menge, name, Sollgrund.UNBEKANNT, None, bericht)
        elif (ziel := self._zaehlt_als(alter)) is not None:
            self._zurechnen(alter, ziel, menge, name, bericht)
        elif menge == 0:
            self._still_speichern(alter, menge, name, bericht)
        else:
            self._offen(nummer, menge, name, Sollgrund.EINGESTELLT, alter, bericht)

    def _zaehlt_als(self, alter: Hausartikel) -> Hausartikel | None:
        """The carried row a discontinued duplicate counts as, if its stock can go there.

        Only into the same product, otherwise the units would not convert. And
        not into an inactive row that is due: its own number becomes a
        question then ("reactivate?"), and a target next to it would answer
        that before anyone was asked.

        Along a chain: a duplicate may count as a row that was itself
        discontinued by a later merge and counts as a third. Merging bends
        such references since 10/2026, stocks from before still carry them.
        """
        ziel = alter
        gesehen = {alter.pk}
        while ziel is not None and ziel.pk not in self.gefuehrt_je_pk:
            naechster = ziel.zaehlt_als_id
            if naechster is None or naechster in gesehen:
                return None
            gesehen.add(naechster)
            ziel = self.alle_je_pk.get(naechster)
        if ziel is None or ziel.pk == alter.pk or ziel.ware_id != alter.ware_id:
            return None
        if not ziel.aktiv and ziel.pk in self.dran:
            return None
        return ziel

    def _zurechnen(
        self,
        alter: Hausartikel,
        ziel: Hausartikel,
        menge: Decimal,
        name: str,
        bericht: Sollbestandbericht,
    ) -> None:
        """The stock of the duplicate onto the row it counts as, in that row's unit.

        Added up in 'schreiben': the row's own number may come before or after
        it in the file. The first designation the file gives is kept for a row
        that has no line of its own there.
        """
        im_ziel = ziel.in_hauseinheit(alter.in_wareneinheit(menge))
        self.zugerechnet[ziel.pk] = self.zugerechnet.get(ziel.pk, Decimal("0")) + im_ziel
        self.zugerechnet_name.setdefault(ziel.pk, name)
        bericht.zugerechnet += 1

    def schreiben(self, bericht: Sollbestandbericht) -> None:
        """Stores what was collected. A row that already exists takes the new values.

        What duplicates add goes onto the target their row already has. On an
        upload that is only what this file gave it, the old targets are gone by
        then. Matching again ('sollzeilen_erneut_zuordnen') leaves the stored
        ones in place, and until 10/2026 the duplicate's stock replaced them.

        A sum the column does not hold (a duplicate in a far bigger unit)
        stays out, and the report says so. Until 10/2026 it ended on the
        error page under PostgreSQL.
        """
        gespeichert = self._gespeicherte_ziele()
        for pk, dazu in self.zugerechnet.items():
            artikel = self.gefuehrt_je_pk[pk]
            bisher = self.sollbestaende.get(pk) or gespeichert.get(pk)
            # Converted from another unit, the sum may carry more places than the column.
            summe = (dazu + (bisher.menge_basiseinheit if bisher else Decimal("0"))).quantize(
                Decimal("0.001"), rounding=ROUND_HALF_UP
            )
            if abs(summe) >= HOECHSTMENGE:
                bericht.fehler.append(zu_viel_fuer_ein_soll(artikel, summe))
                continue
            if pk not in self.sollbestaende:
                if bisher is not None:
                    self._speichern(
                        artikel, bisher.menge_basiseinheit, bisher.name_in_datei, bisher.herkunft
                    )
                else:
                    self._speichern(artikel, Decimal("0"), self.zugerechnet_name.get(pk, ""))
            self.sollbestaende[pk].menge_basiseinheit = summe
        self.zugerechnet = {}
        self.zugerechnet_name = {}
        Sollbestand.objects.bulk_create(
            self.sollbestaende.values(),
            update_conflicts=True,
            unique_fields=["inventur", "hausartikel"],
            update_fields=["menge_basiseinheit", "name_in_datei", "herkunft"],
        )
        Sollzeile.objects.bulk_create(
            self.sollzeilen.values(),
            update_conflicts=True,
            unique_fields=["inventur", "artikelnummer"],
            update_fields=["menge_basiseinheit", "name", "grund", "hausartikel"],
        )
        self.sollbestaende = {}
        self.sollzeilen = {}

    def _gespeicherte_ziele(self) -> dict[int, Sollbestand]:
        """The stored targets of the rows that duplicates add to and this file gave nothing."""
        ohne = [pk for pk in self.zugerechnet if pk not in self.sollbestaende]
        if not ohne:
            return {}
        return {
            soll.hausartikel_id: soll
            for soll in self.inventur.sollbestaende.filter(hausartikel_id__in=ohne)
        }

    def _speichern(
        self,
        artikel: Hausartikel,
        menge: Decimal,
        name: str,
        herkunft: str = Sollherkunft.DATEI,
    ) -> None:
        self.sollbestaende[artikel.pk] = Sollbestand(
            inventur=self.inventur,
            hausartikel=artikel,
            menge_basiseinheit=menge,
            name_in_datei=name,
            herkunft=herkunft,
        )

    def _still_speichern(self, artikel, menge, name, bericht: Sollbestandbericht) -> None:
        """An inactive or discontinued article with a stock of 0 is no question.

        The inventory management system still lists the article, but with
        nothing on stock. That matches the house: it took the article out of
        the range. Asking "reactivate?" would only make someone click away a
        row per discontinued article on every upload. The target is stored
        anyway, as for annual goods in the monthly inventory: if someone counts
        the article after all, its target 0 stands ready, and the difference
        with it ('Inventur.wirksame_sollbestaende').
        """
        self._speichern(artikel, menge, name)
        bericht.nicht_aktiv_leer += 1

    def _eingestellter(self, nummer: str, name: str) -> Hausartikel | None:
        """The discontinued row the row means, if there is exactly one.

        A number may carry several discontinued rows (its history). If the file
        names the designation, it decides. Otherwise only if there is exactly
        one. If it stays open, the number counts as unknown.
        """
        kandidaten = self.eingestellt.get(nummer, [])
        if name:
            passende = [k for k in kandidaten if k.name.casefold() == name.casefold()]
            if len(passende) == 1:
                return passende[0]
        return kandidaten[0] if len(kandidaten) == 1 else None

    def _offen(self, nummer, menge, name, grund, artikel, bericht) -> None:
        self.sollzeilen[nummer] = Sollzeile(
            inventur=self.inventur,
            artikelnummer=nummer,
            menge_basiseinheit=menge,
            name=name,
            grund=grund,
            hausartikel=artikel,
        )
        bericht.offen += 1


def sollbestand_einlesen(
    datei, inventur: Inventur, format: Format | None = None
) -> Sollbestandbericht:
    """Reads the target stock file in the chosen format. The last file applies.

    What stood before drops away: target stocks as well as open rows. A
    corrected file should apply without a remainder of the old one staying.
    What the file looks like is known by the format ('katalog.formats'). Here
    begins what is the same for all: duplicate numbers and the matching.

    Matched is what the house **actively** carries and what is due by the
    types of this inventory. Everything else has a reason, and that is recorded:
    inactive and discontinued articles become a question, unknown numbers too
    ('Sollzeile'). **Annual goods in the monthly inventory are stored but have
    no say** as long as nobody adds them in the app (see
    'Inventur.wirksame_sollbestaende'). The same goes for an inactive or
    discontinued article whose stock in the file is 0: stored silently, no
    question. Only numbers from "Not countable" are counted and skipped.

    Refused while the difference is typed in by hand and outside the
    preparation ('soll_darf_herein').

    **The file is read first, with no lock and no transaction open.** A
    count sheet of a few thousand rows takes its time, and until 10/2026 it
    was read under the inventory's lock: everyone who writes to the
    inventory waited meanwhile, the switches and the check list's remarks
    too. Whatever changed while it was read is seen under the lock: the
    status and the difference by hand are checked there ('soll_darf_herein').
    """
    if format is None:
        format = format_holen(STANDARD)
    gelesen = format.sollbestand_lesen(datei)
    with transaction.atomic():
        return _einlesen(inventur, gelesen)


def _einlesen(inventur: Inventur, gelesen: Sollbestandsdatei) -> Sollbestandbericht:
    """What 'sollbestand_einlesen' does with the rows read, under the inventory's lock."""
    soll_darf_herein(inventur)
    von_hand = inventur.sollbestaende.filter(herkunft=Sollherkunft.HAND).count()
    inventur.sollbestaende.all().delete()
    inventur.sollzeilen.all().delete()
    bericht = Sollbestandbericht(fehler=gelesen.fehler, von_hand_ersetzt=von_hand)
    zuordner = Zuordner(inventur)
    bericht.ohne_nummern = not (zuordner.gefuehrt or zuordner.eingestellt or zuordner.ignoriert)
    # Number -> row in which it first stood.
    gesehen: dict[str, int] = {}
    # Like the reader's refusals: the first hundred in words, the rest counted.
    doppelt = Zeilenmeldungen()

    for zeile in gelesen.zeilen:
        if zeile.nummer in gesehen:
            # Which of the two quantities is right nobody knows here. Silently
            # taking the last would look as if read in. So keep the first and
            # say so.
            doppelt.melden(
                _(
                    "Zeile %(zeile)s: Artikelnummer %(nummer)s steht schon in "
                    "Zeile %(erste)s. Es gilt die erste."
                )
                % {"zeile": zeile.stelle, "nummer": zeile.nummer, "erste": gesehen[zeile.nummer]}
            )
            continue
        gesehen[zeile.nummer] = zeile.stelle

        bericht.zeilen += 1
        zuordner.zuordnen(zeile.nummer, zeile.menge, zeile.name, bericht)
    meldungen = doppelt.liste()
    if doppelt.weitere:
        # The count leads, as the reader's does: the page shows only the first ten.
        bericht.fehler.insert(0, meldungen.pop(0))
    bericht.fehler += meldungen
    zuordner.schreiben(bericht)
    return bericht
