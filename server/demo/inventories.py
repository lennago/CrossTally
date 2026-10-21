"""The inventories of the demo stock: four closed runs and one running.

The running run is the actual purpose. In Kino Nord everything is counted
but not closed yet, and the count is set so that at closing **every** one of
the seven blocks of the clarification has something to ask. Kino Ost is at
the beginning, there the progress while counting can be observed.

The closed runs before it are really counted and frozen via
'Inventur.abschliessen', instead of being written as finished results. That
way the numbers are computed and not claimed, and the closing path runs
along with every demo build.

Counting uses a randomness that hangs on cut-off date, house, location and
article. The same build thus yields the same stock.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from uuid import uuid4

from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from accounts.models import CustomUser
from api.checks import behaelter_erlaubt, pruefe_nicht_aktiv, pruefe_zaehlbar, pruefen
from inventur.models import Inventur, Wiegung, Zaehlposition
from inventur.services import (
    inventuren_anlegen,
    soll_von_hand_speichern,
    sollbestand_einlesen,
    vermerk_aendern,
)
from katalog.models import (
    Basiseinheit,
    Behaelter,
    Hausartikel,
    Inventurart,
    Rezepturartikel,
    Ware,
)
from organisation.models import Haus, Ort

from .catalog import BECHERINVENTUR, MONATSINVENTUR
from .files import datei
from .houses import (
    ARTIKEL_NORD,
    ARTIKEL_OST,
    NORD,
    OST,
    SPAETER_AUSGESCHIEDEN,
    SPAETER_UMGESTELLT,
    SUED,
    nummer_neu_vergeben,
)
from .names_en import Namen

#: How many closed monthly runs precede the running one.
ABGESCHLOSSENE_MONATE = 3


# --------------------------------------------------------------------------- #
# The targeted cases in Kino Nord
# --------------------------------------------------------------------------- #

#: Articles on which nothing at all was entered. At closing they are in the
#: block "Not counted at all".
NICHT_GEZAEHLT = ("101505", "101612")

#: Individual packs at a location that stayed open although the article is
#: otherwise counted. That is the block "Expected, but nothing entered".
OFFENE_GEBINDE = (("101001", "Kasten"), ("101201", "Karton"))


@dataclass(frozen=True)
class Nachtrag:
    """What was added in the app although it was not expected there."""

    nummer: str
    ort: str
    menge: str
    gebinde: str = ""


#: Every entry serves one block of the clarification: the iced tea, the
#: guacamole and the gum the block "No longer active, but counted" (the old
#: cones get there as an expected row, see 'houses.NEU_VERGEBEN'), the
#: salsa sauce the block "Counted unexpectedly", the white wine the block
#: "Counted, but in no type of this inventory". Each goes through the API's
#: checks ('_nachtragen'): the guacamole and the gum are still active when
#: counted and leave the range afterwards ('houses.SPAETER_AUSGESCHIEDEN').
NACHTRAEGE = (
    Nachtrag("101013", "Theke links", "18", "Einzeln"),
    Nachtrag("101205", "Kühlschrank Theke", "2", "Karton"),
    Nachtrag("101307", "Süßwarenregal", "14", "Einzeln"),
    Nachtrag("101203", "Theke links", "3", "Karton"),
    # Annual goods, added in the month. They get no row there, but through
    # the app's search they very well do. From that the block "Counted, but
    # in no type of this inventory" arises. Kino Ost counts the same wine
    # monthly, so the question asks for whom the answer applies.
    Nachtrag("101016", "Getränkelager", "2", "Karton"),
)

#: The cup inventory of Kino Nord is counted but not closed. At closing the
#: large reusable cup, left open, offers the switch into another type, and
#: the disposable cup added in the app the way into the cup inventory, for
#: this article or also for Kino Ost's.
BECHER_OFFEN = ("101615",)
BECHER_NACHTRAEGE = (Nachtrag("101607", "Getränkelager", "1", "Karton"),)

#: The cup inventory has no file: the reusable cups stand in no stock of the
#: house. Their difference comes by hand, from the inventory management
#: system, per number with a note where there is one. The uncounted cup is
#: missing entirely, and the third one is still waiting for its difference.
BECHER_DIFFERENZEN = (
    ("101614", "-12", "Spülmaschine war noch voll"),
    ("101615", "-30", ""),
)

#: "Target counts" in the check of Kino Nord: the cola syrup sits in the
#: dispenser and cannot be counted before the next change of the container.
#: At closing its target stands as the result. Number and note.
SOLL_ZAEHLT = (("101006", "Sirup steckt in der Anlage"),)

#: A weighing in a vessel assigned only to the other house. In the app that
#: is possible when someone brings the bowl along. At closing the block
#: "Weighed in a foreign container" asks about it.
FREMDE_WIEGUNG = ("101301", "Süßwarenregal", "Schale blau", "980")

#: Products whose difference runs in opposite directions in the same run. What
#: is missing in one house lies in excess in the other: a movement of goods
#: between the houses. Product -> quantity in its base unit missing in Kino Nord.
GEGENLAEUFIG = {"Cola 0,5 l": "48", "Nachochips": "6"}

#: Which file carries the target stock of a house. The key is the German house
#: name of the blueprint, not the stored one.
SOLLDATEI = {NORD: "sollbestand_kino_nord.csv", OST: "sollbestand_kino_ost.csv"}

#: A target entered by hand in the preparation, per house the numbers and
#: quantities. The file does not know this article. In Kino Nord it stays open
#: and appears in the clarification under "Active, but without target", in
#: Kino Ost someone entered it on the maintenance page: there it appears under
#: "With target" with the note "by hand".
SOLL_VON_HAND = {OST: (("102308", "14"),)}

#: The ice cream workshop has no file. Its target comes entirely by hand, per
#: product the quantity in the house's unit (kilos). That way the maintenance
#: page shows nothing but rows "by hand" there, and the clarification has
#: nothing to ask.
SOLL_VON_HAND_EIGEN = {
    SUED: (("Eis Vanille", "12.5"), ("Eis Schokolade", "9"), ("Sahne", "20"), ("Zucker", "35"))
}

#: How much the target stock deviates from the count when nothing else applies.
#: Most articles match, otherwise the result list would be unreadable.
ABWEICHUNGSANTEIL = 0.35


# --------------------------------------------------------------------------- #
# Cut-off dates
# --------------------------------------------------------------------------- #


def monatsende(tag: date) -> date:
    """The last day of the month this day lies in."""
    erster_naechster = (tag.replace(day=28) + timedelta(days=4)).replace(day=1)
    return erster_naechster - timedelta(days=1)


def vormonatsende(tag: date) -> date:
    return tag.replace(day=1) - timedelta(days=1)


@dataclass(frozen=True)
class Stichtage:
    """The days the inventories of the demo hang on."""

    #: Being counted right now.
    laufend: date
    #: Closed, the most recent first.
    abgeschlossen: list[date]
    #: The closed annual inventory: a month-end on the 31st of December among
    #: the runs, otherwise the year end before all of them.
    jahr: date
    #: The annual inventory that is still being prepared.
    vorbereitung: date
    #: The cup inventory of Kino Nord, a week before the running month-end.
    woche: date


def stichtage(heute: date) -> Stichtage:
    """All cut-off dates of the demo, derived from the day of the build.

    The anchor is the end of the previous month. That way the demo does not
    age: whoever builds it in March gets an inventory from February. The closed
    annual inventory lies on the last turn of the year before it, the prepared
    one on the year end the running cut-off date falls into. A house has one
    inventory per day. In January the running run lies on that year end
    itself, so the prepared one moves on to the next.
    """
    laufend = vormonatsende(monatsende(heute))
    abgeschlossen = []
    tag = laufend
    for _monat in range(ABGESCHLOSSENE_MONATE):
        tag = vormonatsende(tag)
        abgeschlossen.append(tag)
    vorbereitung = date(laufend.year, 12, 31)
    if vorbereitung == laufend:
        vorbereitung = date(laufend.year + 1, 12, 31)
    # A month-end on the 31st of December is the annual inventory: that run
    # counts every type, and no older one is made up. Until 10/2026 a build
    # from January to April had a turn of the year with a monthly run only,
    # and the annual one lay up to 26 months back.
    jahreswechsel = [tag for tag in (laufend, *abgeschlossen) if (tag.month, tag.day) == (12, 31)]
    jahr = jahreswechsel[0] if jahreswechsel else date(abgeschlossen[-1].year - 1, 12, 31)
    return Stichtage(
        laufend=laufend,
        abgeschlossen=abgeschlossen,
        jahr=jahr,
        vorbereitung=vorbereitung,
        woche=laufend - timedelta(days=7),
    )


def _abschlusszeitpunkt(stichtag: date) -> datetime:
    """In the evening of the cut-off date. Counting happens after closing time."""
    return timezone.make_aware(datetime.combine(stichtag, time(hour=22)))


def _zaehlzeitpunkt(stichtag: date) -> datetime:
    """An hour before closing: when the rows of this run were counted.

    Hung on the cut-off date, not on the clock (until 10/2026 every row said
    the build's moment), so a build with '--date' looks the same every time.
    """
    return _abschlusszeitpunkt(stichtag) - timedelta(hours=1)


def _angelegt_am(inventuren, stichtag: date, heute: date) -> None:
    """Created two weeks before the cut-off date, never after the build's day.

    'erstellt_am' is 'auto_now_add' and said the build's moment (until
    10/2026): an inventory "created" after it was closed.
    """
    tag = min(stichtag - timedelta(days=14), heute)
    zeitpunkt = timezone.make_aware(datetime.combine(tag, time(hour=10)))
    Inventur.objects.filter(pk__in=[inventur.pk for inventur in inventuren]).update(
        erstellt_am=zeitpunkt
    )


# --------------------------------------------------------------------------- #
# Counting
# --------------------------------------------------------------------------- #


class Zaehler:
    """Enters the quantities for an inventory.

    The containers are loaded once per house. The question of what may be
    weighed in hangs on product and house and is the same for every row.
    """

    def __init__(self, inventur: Inventur, namen: Namen, zeitpunkt: datetime):
        self.inventur = inventur
        self.namen = namen
        #: When the rows of this run were counted ('_zaehlzeitpunkt').
        self.zeitpunkt = zeitpunkt
        # The blueprints name the packs in German, stored they are in the
        # language of the stock. The comparison happens on the stored side.
        self.offene_gebinde = {(nummer, namen(g)) for nummer, g in OFFENE_GEBINDE}
        self.ohne_behaelter = Behaelter.objects.get(immer_erlaubt=True)
        self.im_haus = list(Behaelter.objects.filter(haeuser=inventur.haus))
        # The key names the model too: product and recipe article have their own
        # number ranges, and on the second build the number of a product coincided
        # with that of a recipe article. The spray cream then got the bowls of the
        # confectionery mix, and the randomness hit a different vessel.
        self.je_ware: dict[tuple[str, int], list[Behaelter]] = {}
        for behaelter in Behaelter.objects.prefetch_related("waren", "rezepturartikel"):
            for ware in behaelter.waren.all():
                self.je_ware.setdefault(("ware", ware.pk), []).append(behaelter)
            for artikel in behaelter.rezepturartikel.all():
                self.je_ware.setdefault(("rezepturartikel", artikel.pk), []).append(behaelter)

    def zaehlen(
        self, orte: list[str] | None = None, aussparen: bool = False, ohne: tuple[str, ...] = ()
    ) -> int:
        """Enters what stands at the named locations. Without an entry everywhere.

        'aussparen' leaves the gaps that become the questions at closing. They
        belong only in the running inventory of Kino Nord. 'ohne' leaves out
        these article numbers.
        """
        positionen = list(
            self.inventur.zaehlpositionen.filter(aktiv=True).select_related(
                "ort", "gebinde", "artikel__hausartikel__ware", "artikel__rezepturartikel"
            )
        )
        gezaehlt = 0
        for position in positionen:
            if orte is not None and position.ort.name not in orte:
                continue
            if aussparen and self._ausgespart(position):
                continue
            artikel = position.artikel.konkret()
            if isinstance(artikel, Hausartikel) and artikel.artikelnummer in ohne:
                continue
            self._eintragen(position)
            gezaehlt += 1
        return gezaehlt

    def _ausgespart(self, position: Zaehlposition) -> bool:
        artikel = position.artikel.konkret()
        if not isinstance(artikel, Hausartikel):
            return False
        gebinde = position.gebinde.name if position.gebinde_id else ""
        return (
            artikel.artikelnummer in NICHT_GEZAEHLT
            or (artikel.artikelnummer, gebinde) in self.offene_gebinde
        )

    def _eintragen(self, position: Zaehlposition) -> None:
        artikel = position.artikel.konkret()
        zufall = self._zufall(position, artikel)
        if position.gebinde_id is not None:
            position.eingabemenge = self._anzahl(position.gebinde.name, zufall)
            self._gezaehlt(position)
            return
        if isinstance(artikel, Rezepturartikel) and artikel.basiseinheit.dimension == "STUECK":
            position.eingabemenge = Decimal(zufall.randint(4, 40))
            self._gezaehlt(position)
            return
        self._wiegen(position, artikel, zufall)

    def _gezaehlt(self, position: Zaehlposition) -> None:
        """Saves the row the way the API does: whole, so 'zuletzt_geaendert' moves.

        'auto_now' applies on 'save()' without 'update_fields' only. Until 10/2026
        the demo wrote past it, and the app would not have fetched the rows.
        """
        position.gezaehlt_am = self.zeitpunkt
        position.save()

    def _wiegen(self, position: Zaehlposition, artikel, zufall: random.Random) -> None:
        schluessel = (
            ("ware", artikel.ware.pk)
            if isinstance(artikel, Hausartikel)
            else ("rezepturartikel", artikel.pk)
        )
        # Sorted so the same randomness hits the same vessel: the order from
        # the database is no promise.
        moeglich = sorted(
            self.je_ware.get(schluessel, []) + self.im_haus, key=lambda satz: satz.name
        ) or [self.ohne_behaelter]
        for _wiegung in range(zufall.randint(1, 3)):
            behaelter = zufall.choice(moeglich)
            brutto = behaelter.tara + Decimal(zufall.randint(120, 2400))
            _wiegung_anlegen(position, behaelter, brutto, self.zeitpunkt)
        self._gezaehlt(position)

    def _anzahl(self, gebinde: str, zufall: random.Random) -> Decimal:
        """How much of a pack usually stands there."""
        spannen = {
            "Einzeln": (0, 48),
            "Kasten": (0, 6),
            "Karton": (0, 5),
            "Sack": (0, 4),
            "Eimer": (0, 3),
            "Kanister": (0, 3),
            "Glas": (0, 4),
            "Beutel": (0, 6),
        }
        je_name = {self.namen(name): spanne for name, spanne in spannen.items()}
        von, bis = je_name.get(gebinde, (0, 10))
        return Decimal(zufall.randint(von, bis))

    def _zufall(self, position: Zaehlposition, artikel) -> random.Random:
        """A randomness that hangs on the row and not on the run.

        The primary key is no good for that: it is a different one on the next
        build, and the demo should yield the same thing twice. The types go in
        by their place in the order, for the same reason.
        """
        kennzeichen = artikel.artikelnummer if isinstance(artikel, Hausartikel) else artikel.name
        gebinde = position.gebinde.name if position.gebinde_id else "-"
        arten = "-".join(str(art.reihenfolge) for art in self.inventur.inventurarten.all())
        return random.Random(
            f"{self.inventur.stichtag}|{arten}|{self.inventur.haus.name}"
            f"|{position.ort.name}|{kennzeichen}|{gebinde}"
        )


# --------------------------------------------------------------------------- #
# Additions and target stocks
# --------------------------------------------------------------------------- #


def nachtraege_eintragen(inventur: Inventur, namen: Namen, zeitpunkt: datetime) -> int:
    """What was added in the app, including the weighing in the foreign vessel."""
    artikel = _nachtragen(inventur, NACHTRAEGE, namen, zeitpunkt)
    nummer, ortsname, behaeltername, brutto = FREMDE_WIEGUNG
    ortsname, behaeltername = namen(ortsname), namen(behaeltername)
    position = (
        Zaehlposition.objects.filter(
            inventur=inventur,
            artikel_id=artikel[nummer].pk,
            ort__name=ortsname,
            gebinde__isnull=True,
        )
        .order_by("pk")
        .first()
    )
    if position is not None:
        behaelter = Behaelter.objects.get(name=behaeltername)
        _wiegung_anlegen(position, behaelter, Decimal(brutto), zeitpunkt)
        position.gezaehlt_am = zeitpunkt
        position.save()
    return len(NACHTRAEGE)


def _wiegung_anlegen(
    position: Zaehlposition, behaelter: Behaelter, brutto: Decimal, zeitpunkt: datetime
) -> None:
    """A weighing as the app sends it, under the rule the API applies to the vessel."""
    assert behaelter_erlaubt(position, behaelter), f"„{behaelter}“ steht hier nicht zur Wahl"
    wiegung = Wiegung.objects.create(
        zaehlposition=position, brutto_gramm=brutto, behaelter=behaelter, kennung=uuid4()
    )
    # 'erfasst_am' is 'auto_now_add': dated like the count, past the automatic.
    Wiegung.objects.filter(pk=wiegung.pk).update(erfasst_am=zeitpunkt)


def _nachtragen(
    inventur: Inventur, eintraege, namen: Namen, zeitpunkt: datetime
) -> dict[str, Hausartikel]:
    """Rows added in the app, as unexpected ones. Returns the articles by number.

    Through the API's checks, as the app's request would go: a rule that
    changes there should break the build, not leave the demo showing a case
    that no longer exists.
    """
    orte = {ort.name: ort for ort in inventur.haus.alle_orte()}
    artikel = {
        satz.artikelnummer: satz
        for satz in Hausartikel.objects.filter(haus=inventur.haus).select_related("ware")
    }
    pruefe_zaehlbar(inventur)
    for eintrag in eintraege:
        satz = artikel[eintrag.nummer]
        pruefe_nicht_aktiv(satz, inventur)
        ort = orte[namen(eintrag.ort)]
        gebinde = satz.ware.gebinde.filter(name=namen(eintrag.gebinde)).first()
        # When the running run is the annual inventory (a month-end on the
        # 31st of December), the annual goods already have their rows. The app
        # would offer "change" there, not a second row.
        if Zaehlposition.objects.filter(
            inventur=inventur, ort=ort, artikel_id=satz.pk, gebinde=gebinde
        ).exists():
            continue
        position = Zaehlposition(
            inventur=inventur,
            ort=ort,
            artikel_id=satz.pk,
            gebinde=gebinde,
            eingabemenge=Decimal(eintrag.menge),
            gezaehlt_am=zeitpunkt,
            unerwartet=True,
        )
        pruefen(position)
        position.save()
    return artikel


def spaeter_ausscheiden(inventur: Inventur, namen: Namen, arten: dict[str, Inventurart]) -> None:
    """The status changes after the count. Only so do counted rows of non-active articles arise.

    Through 'status_setzen', as the article's page does it: 'gueltig_bis' lands
    after 'gezaehlt_am', and a discontinued article leaves the counting lists.
    Then the house's system reassigns the cones' number
    ('houses.nummer_neu_vergeben'), which locks the old row at closing.
    """
    status = {plan.nummer: plan.status for plan in ARTIKEL_NORD}
    for nummer in SPAETER_AUSGESCHIEDEN:
        satz = Hausartikel.objects.get(haus=inventur.haus, artikelnummer=nummer)
        satz.status_setzen(status[nummer])
    nummer_neu_vergeben(inventur.haus, namen, arten)


def einheiten_umstellen(namen: Namen) -> None:
    """Before the running run Kino Ost changes to bags ('houses.SPAETER_UMGESTELLT').

    Through the model's check, as the article's page does it: no inventory
    holds numbers in the old unit any more, the closed runs are closed. Their
    results keep the kg they were frozen in.
    """
    plaene = {plan.nummer: plan for plan in ARTIKEL_OST}
    for nummer in SPAETER_UMGESTELLT:
        satz = Hausartikel.objects.get(haus__name=namen(OST), artikelnummer=nummer)
        plan = plaene[nummer]
        satz.basiseinheit = Basiseinheit.objects.get(name=plan.einheit)
        satz.faktor_zur_ware = Decimal(plan.faktor) if plan.faktor else None
        satz.full_clean()
        satz.save()


def sollbestaende_aus_zaehlung(inventur: Inventur, namen: Namen) -> int:
    """The target stock of a closed inventory, derived from the actual stock.

    For the old runs there is no file. A target that arises from the actual
    stock is still what the case yields: mostly it matches, sometimes not, and
    for two products the houses run against each other. It is entered by hand,
    through the same service as the maintenance page.
    """
    gegenlaeufig = {
        ware.pk: Decimal(menge)
        for ware, menge in (
            (Ware.objects.filter(name=namen(name)).first(), menge)
            for name, menge in GEGENLAEUFIG.items()
        )
        if ware is not None
    }
    vorzeichen = Decimal("1") if inventur.haus.name == namen(NORD) else Decimal("-1")
    waren = dict(Hausartikel.objects.filter(haus=inventur.haus).values_list("pk", "ware_id"))
    warennamen = dict(Ware.objects.values_list("pk", "name"))
    mengen: dict[int, Decimal | None] = {}
    for hausartikel_id, menge in inventur.mengen_je_hausartikel().items():
        # The randomness hangs on cut-off date and product, not on the primary
        # key: that is a different one on the next build. And **not** on the
        # house: the same deviation in both houses does not run against itself.
        # Opposite are only the products that should be.
        ware_id = waren.get(hausartikel_id, 0)
        zufall = random.Random(f"{inventur.stichtag}|{warennamen.get(ware_id, ware_id)}")
        soll = menge
        versatz = gegenlaeufig.get(ware_id)
        if versatz is not None:
            soll = menge + versatz * vorzeichen
        elif zufall.random() < ABWEICHUNGSANTEIL:
            soll = menge + Decimal(zufall.randint(-6, 6))
        mengen[hausartikel_id] = max(soll, Decimal("0"))
    # Through the service of the maintenance page: there was no file for these
    # runs, so the target is "by hand", as it would be. Until 10/2026 the rows
    # claimed a file that never existed.
    return soll_von_hand_speichern(inventur, mengen).eingetragen


def sollbestand_hochladen(inventur: Inventur, dateiname: str, namen: Namen) -> int:
    """Reads in one of the bundled files, as the upload would."""
    with datei(dateiname, namen.sprache).open("rb") as quelle:
        bericht = sollbestand_einlesen(quelle, inventur)
    return bericht.zugeordnet


def sollbestand_von_hand(inventur: Inventur, namen: Namen) -> int:
    """Enters the target the blueprint provides by hand for this house.

    Through the service of the maintenance page, not past it: that way the
    entry carries the same origin as one from the interface.
    """
    eintraege = next(
        (
            eintraege
            for name, eintraege in SOLL_VON_HAND.items()
            if namen(name) == inventur.haus.name
        ),
        (),
    )
    eigene = next(
        (
            eintraege
            for name, eintraege in SOLL_VON_HAND_EIGEN.items()
            if namen(name) == inventur.haus.name
        ),
        (),
    )
    if not eintraege and not eigene:
        return 0
    artikel = dict(
        Hausartikel.objects.filter(
            haus=inventur.haus, aktiv=True, artikelnummer__in=[nummer for nummer, _m in eintraege]
        ).values_list("artikelnummer", "pk")
    )
    # Own articles have no number. They are meant through their product.
    ohne_nummer = dict(
        Hausartikel.objects.filter(
            haus=inventur.haus, aktiv=True, ware__name__in=[namen(name) for name, _m in eigene]
        ).values_list("ware__name", "pk")
    )
    mengen: dict[int, Decimal | None] = {
        artikel[nummer]: Decimal(menge) for nummer, menge in eintraege
    }
    mengen |= {ohne_nummer[namen(name)]: Decimal(menge) for name, menge in eigene}
    return soll_von_hand_speichern(inventur, mengen).eingetragen


# --------------------------------------------------------------------------- #
# Runs
# --------------------------------------------------------------------------- #


@dataclass
class Laufbericht:
    """A row of the overview the command prints at the end."""

    stichtag: date
    #: The types in words, as the inventory names them: "Monatsinventur + Becherinventur".
    art: str
    status: str
    #: What makes up this run, in words. For a counted one the number of rows,
    #: in preparation the read-in target stock.
    hinweis: str


def gezaehlt_text(anzahl: int) -> str:
    """What a counted run reports, with one row as one row."""
    return ngettext("%(anzahl)s Zeile gezählt", "%(anzahl)s Zeilen gezählt", anzahl) % {
        "anzahl": anzahl
    }


def laeufe_anlegen(
    haeuser: dict[str, Haus],
    konten: dict[str, CustomUser],
    heute: date,
    namen: Namen,
    inventurarten: dict[str, Inventurart],
) -> list[Laufbericht]:
    """All inventories of the demo, from the oldest run to the running one.

    The annual inventories count every type, the monthly ones the monthly
    inventory. The running run counts the cup inventory on top: in Kino Nord
    the beverage store then has two tabs, Kino Ost carries no cups and has
    one. The cup inventory alone can only be counted in Kino Nord. If a
    month-end falls on the 31st of December, that run is the annual inventory.
    Between the closed runs and the running one Kino Ost changes the unit
    of its coffee beans ('einheiten_umstellen').
    """
    beteiligt = [haeuser[NORD], haeuser[OST]]
    tage = stichtage(heute)
    alle = list(inventurarten.values())
    monat = [inventurarten[MONATSINVENTUR]]
    becher = inventurarten[BECHERINVENTUR]
    berichte = []

    if tage.jahr not in (tage.laufend, *tage.abgeschlossen):
        berichte.append(_abgeschlossener_lauf(beteiligt, konten, tage.jahr, alle, namen, heute))
    for stichtag in reversed(tage.abgeschlossen):
        arten = alle if stichtag == tage.jahr else monat
        berichte.append(_abgeschlossener_lauf(beteiligt, konten, stichtag, arten, namen, heute))
    einheiten_umstellen(namen)
    laufende_arten = alle if tage.laufend == tage.jahr else [*monat, becher]
    berichte.append(
        _laufender_lauf(
            beteiligt, konten, tage.laufend, laufende_arten, namen, heute, inventurarten
        )
    )
    # After the running monthly inventory, although it is older: the counting
    # app lists in the order of creation, and the month should come first
    # there. The end-to-end tests walk into the first inventory of Kino Nord.
    berichte.append(_becherinventur(haeuser[NORD], konten, tage.woche, namen, becher, heute))
    berichte.append(
        _vorbereiteter_lauf(beteiligt, haeuser[SUED], konten, tage.vorbereitung, namen, alle, heute)
    )
    return sorted(berichte, key=lambda bericht: bericht.stichtag)


def _solldatei(haus: Haus, namen: Namen) -> str | None:
    """The file for the house, found through its German name from the blueprint.

    A house without an inventory management system has none: there the target
    comes by hand.
    """
    return next((datei for name, datei in SOLLDATEI.items() if namen(name) == haus.name), None)


def _vorbereiteter_lauf(
    haeuser: list[Haus],
    eigene: Haus,
    konten: dict[str, CustomUser],
    stichtag: date,
    namen: Namen,
    arten: list[Inventurart],
    heute: date,
) -> Laufbericht:
    """The next annual inventory, still in preparation.

    Without it two things could not be looked at at all: the target stock
    upload and the clarification of what it leaves open. Both exist only
    before the start of the count, because afterwards the counting apps hold
    their rows.

    The ice cream workshop takes part here: it has no file, its target is by
    hand, and so the maintenance page can be seen with nothing but rows "by
    hand".
    """
    eingelesen = von_hand = 0
    angelegt = inventuren_anlegen(
        [*haeuser, eigene],
        arten,
        stichtag,
        angelegt_von=konten["leitung"],
        zaehlende=_zaehlende(konten),
    )
    _angelegt_am(angelegt, stichtag, heute)
    for inventur in angelegt:
        if (dateiname := _solldatei(inventur.haus, namen)) is not None:
            eingelesen += sollbestand_hochladen(inventur, dateiname, namen)
        von_hand += sollbestand_von_hand(inventur, namen)
    return Laufbericht(
        stichtag,
        angelegt[0].arten_anzeige,
        _("Vorbereitung"),
        _("%(eingelesen)s Sollbestände eingelesen, %(von_hand)s von Hand")
        % {"eingelesen": eingelesen, "von_hand": von_hand},
    )


def _zaehlende(konten: dict[str, CustomUser]) -> list[CustomUser]:
    """Who counts besides the manager, in every house of a run.

    Set when creating, as the form does with the box for all houses
    ('InventurAnlegenForm'). The manager creates and counts along by that.
    The administrator needs no entry.
    """
    return [konten["zaehler"]]


def _abgeschlossener_lauf(
    haeuser: list[Haus],
    konten: dict[str, CustomUser],
    stichtag: date,
    arten: list[Inventurart],
    namen: Namen,
    heute: date,
) -> Laufbericht:
    positionen = 0
    angelegt = inventuren_anlegen(
        haeuser, arten, stichtag, angelegt_von=konten["leitung"], zaehlende=_zaehlende(konten)
    )
    _angelegt_am(angelegt, stichtag, heute)
    for inventur in angelegt:
        inventur.zaehlung_starten()
        positionen += Zaehler(inventur, namen, _zaehlzeitpunkt(stichtag)).zaehlen()
        # A target comes in only in preparation ('soll_darf_herein'). Back
        # there, as the page has it: the counted quantities stay.
        inventur.zurueck_in_vorbereitung()
        sollbestaende_aus_zaehlung(inventur, namen)
        inventur.zaehlung_starten()
        inventur.abschliessen(zeitpunkt=_abschlusszeitpunkt(stichtag))
    return Laufbericht(
        stichtag,
        angelegt[0].arten_anzeige,
        _("abgeschlossen"),
        gezaehlt_text(positionen),
    )


def _becherinventur(
    haus: Haus,
    konten: dict[str, CustomUser],
    stichtag: date,
    namen: Namen,
    becher: Inventurart,
    heute: date,
) -> Laufbericht:
    """The cup inventory alone, a week before the running month-end.

    Only Kino Nord carries reusable cups, so only it can count this type on
    its own. Counted and waiting for closing: there the switch into another
    type and the way into this one can be seen. Without a target stock it
    takes the difference by hand ('_differenz_von_hand'). Locked, as the
    check begins: in the app it opens on its check list.
    """
    positionen = 0
    angelegt = inventuren_anlegen(
        [haus], [becher], stichtag, angelegt_von=konten["leitung"], zaehlende=_zaehlende(konten)
    )
    _angelegt_am(angelegt, stichtag, heute)
    for inventur in angelegt:
        inventur.zaehlung_starten()
        zeitpunkt = _zaehlzeitpunkt(stichtag)
        positionen += Zaehler(inventur, namen, zeitpunkt).zaehlen(ohne=BECHER_OFFEN)
        _nachtragen(inventur, BECHER_NACHTRAEGE, namen, zeitpunkt)
        _differenz_von_hand(inventur, namen)
        inventur.sperre_setzen(True)
    return Laufbericht(
        stichtag,
        becher.name,
        _("Zählung läuft"),
        gezaehlt_text(positionen),
    )


def soll_zaehlt_setzen(inventur: Inventur, namen: Namen) -> None:
    """'SOLL_ZAEHLT', as a manager sets it in the check list or in the app.

    Through the service, and only where a target has a say: without one the
    app refuses the mark, and the demo should not show what nobody could set.
    """
    soll = inventur.wirksame_sollbestaende()
    for nummer, notiz in SOLL_ZAEHLT:
        artikel = Hausartikel.objects.get(haus=inventur.haus, artikelnummer=nummer)
        assert artikel.pk in soll, f"{nummer} hat in {inventur} kein Soll"
        vermerk_aendern(inventur, artikel.pk, soll_statt_ist=True, text=namen(notiz))


def _differenz_von_hand(inventur: Inventur, namen: Namen) -> None:
    """The differences of 'BECHER_DIFFERENZEN', as the check list and the app write them.

    Through the services: the switch refuses where a target stock came in,
    and a rule that changes there should break the build.
    """
    inventur.differenz_von_hand_setzen(True)
    artikel = dict(
        Hausartikel.objects.filter(haus=inventur.haus).values_list("artikelnummer", "pk")
    )
    for nummer, differenz, notiz in BECHER_DIFFERENZEN:
        vermerk_aendern(inventur, artikel[nummer], differenz=Decimal(differenz), text=namen(notiz))


def _laufender_lauf(
    haeuser: list[Haus],
    konten: dict[str, CustomUser],
    stichtag: date,
    arten: list[Inventurart],
    namen: Namen,
    heute: date,
    inventurarten: dict[str, Inventurart],
) -> Laufbericht:
    """Kino Nord is fully counted and waits for the check, Kino Ost is starting.

    'inventurarten' are all types by their German name, for the row the
    house's system creates after the count ('spaeter_ausscheiden').
    """
    positionen = 0
    angelegt = inventuren_anlegen(
        haeuser, arten, stichtag, angelegt_von=konten["leitung"], zaehlende=_zaehlende(konten)
    )
    _angelegt_am(angelegt, stichtag, heute)
    zeitpunkt = _zaehlzeitpunkt(stichtag)
    for inventur in angelegt:
        dateiname = _solldatei(inventur.haus, namen)
        assert dateiname is not None, "die Kinos haben eine Datei"
        # Before the start: afterwards no file comes in ('soll_darf_herein').
        sollbestand_hochladen(inventur, dateiname, namen)
        inventur.zaehlung_starten()
        if inventur.haus.name == namen(NORD):
            positionen += Zaehler(inventur, namen, zeitpunkt).zaehlen(aussparen=True)
            nachtraege_eintragen(inventur, namen, zeitpunkt)
            spaeter_ausscheiden(inventur, namen, inventurarten)
            soll_zaehlt_setzen(inventur, namen)
        else:
            zaehler = Zaehler(inventur, namen, zeitpunkt)
            positionen += zaehler.zaehlen(orte=_begonnene_orte(inventur))
    return Laufbericht(
        stichtag,
        angelegt[0].arten_anzeige,
        _("Zählung läuft"),
        gezaehlt_text(positionen),
    )


def _begonnene_orte(inventur: Inventur) -> list[str]:
    """The first location of the house in the blueprint's order. Kino Ost has counted nothing more.

    By number, not by name: the locations arise in the order in which counting
    happens, and that should be the same in every language.
    """
    ort = Ort.objects.filter(bereich__haus=inventur.haus).order_by("pk").first()
    return [ort.name] if ort is not None else []
