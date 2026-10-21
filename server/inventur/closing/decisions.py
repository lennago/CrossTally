"""Check and apply the answers: freeze, then update the master data.

The questions come from 'questions.py'. Here stands what an answer may do,
what it causes and in which order that happens. Why freezing comes before
the answers and the target is the exception is explained by the package
('inventur/closing/__init__.py').
"""

from dataclasses import dataclass, field
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils.translation import gettext_lazy as _

from django_project.form_fields import Dezimalfeld
from katalog.models import (
    GANZE_WARE,
    Erwartung,
    ErwartungGebinde,
    Inventurart,
    geltung_fehlt,
    neue_art_ans_ende,
)

from ..models import (
    Artentscheidung,
    Inventur,
    Inventurstatus,
    Sollbestand,
    Sollherkunft,
)
from ..services.notes import bemerkungsstand_wie
from ..services.results import zaehlstand
from .questions import (
    Artwechsel,
    Klaerung,
    NeueErwartung,
    UngezaehltesGebinde,
    klaeren,
)

#: The answers in the fourth block. A switch is "art_" and the number of the type.
INAKTIV_SETZEN = "inaktiv"
EINSTELLEN = "einstellen"
WECHSEL_PRAEFIX = "art_"

#: The answers in the fifth block. Empty means: it stays with the missing target.
SOLL_NULL = "null"
SOLL_EIGENER_WERT = "wert"

#: The answers in the seventh block. Empty means: the assignment stays as it is.
DEM_ARTIKEL = "artikel"
DEM_HAUS = "haus"

#: Reads the own target value, with a comma as it is typed, and within the
#: limits of 'Sollbestand.menge_basiseinheit'. Negative too, as on the
#: maintenance page: that is how an inventory management system reports a
#: stock when more was sold than booked.
_SOLLFELD = Dezimalfeld(max_digits=12, decimal_places=3, localize=True)


def _mit_praefix(daten, praefix: str, *, strip: bool = False) -> dict[str, str]:
    """The answers of one block: field name without the prefix -> value, empty ones dropped.

    Typed values ('sollwert_') are trimmed, because a stray space is no value.
    """
    gefunden = {}
    for name, wert in daten.items():
        if not name.startswith(praefix):
            continue
        antwort = wert.strip() if strip else wert
        if antwort:
            gefunden[name.removeprefix(praefix)] = antwort
    return gefunden


@dataclass
class Entscheidungen:
    """The answers as they come from the form: keys, not objects."""

    wiederaufnehmen: set[str] = field(default_factory=set)
    aufnehmen: set[str] = field(default_factory=set)
    herausnehmen: set[str] = field(default_factory=set)
    #: Key of the house article -> INAKTIV_SETZEN, EINSTELLEN, "art_<pk>" or empty
    ungezaehlt: dict[str, str] = field(default_factory=dict)
    #: Key of the house article -> number of a type of this inventory it goes into
    in_art: dict[str, str] = field(default_factory=dict)
    #: Key of the house article -> NUR_DIESER or GANZE_WARE, for both switches
    geltung: dict[str, str] = field(default_factory=dict)
    #: Key of the house article -> SOLL_NULL, SOLL_EIGENER_WERT or empty
    soll: dict[str, str] = field(default_factory=dict)
    #: Key of the house article -> the own value as it was typed
    sollwerte: dict[str, str] = field(default_factory=dict)
    #: Key from article and container -> DEM_ARTIKEL, DEM_HAUS or empty
    behaelter: dict[str, str] = field(default_factory=dict)

    @classmethod
    def aus_formular(cls, daten) -> Entscheidungen:
        return cls(
            wiederaufnehmen=set(daten.getlist("wiederaufnehmen")),
            aufnehmen=set(daten.getlist("aufnehmen")),
            herausnehmen=set(daten.getlist("herausnehmen")),
            ungezaehlt=_mit_praefix(daten, "ungezaehlt_"),
            in_art=_mit_praefix(daten, "in_art_"),
            geltung=_mit_praefix(daten, "geltung_"),
            soll=_mit_praefix(daten, "soll_"),
            sollwerte=_mit_praefix(daten, "sollwert_", strip=True),
            behaelter=_mit_praefix(daten, "behaelter_"),
        )

    def sollmenge(self, schluessel: str) -> Decimal | None:
        """The target to be set for this article. 'None' means none.

        Raises 'ValidationError' if an own value was chosen but none (or
        nonsense) was entered.
        """
        antwort = self.soll.get(schluessel)
        if antwort == SOLL_NULL:
            return Decimal("0")
        if antwort == SOLL_EIGENER_WERT:
            return _SOLLFELD.clean(self.sollwerte.get(schluessel, ""))
        return None


@dataclass
class Abschlussbericht:
    eingefroren: int
    wieder_aktiv: int = 0
    aufgenommen: int = 0
    herausgenommen: int = 0
    inaktiv_gesetzt: int = 0
    eingestellt: int = 0
    art_gewechselt: int = 0
    in_die_art: int = 0
    soll_gesetzt: int = 0
    behaelter_zugeordnet: int = 0


#: Why closing did not happen when the numbers on the page were stale.
NEUE_WERTE = _(
    "Nicht abgeschlossen: in der Zähl-App wurde inzwischen weitergezählt. Die neuen Werte "
    "stehen jetzt da. Bitte prüfen und dann abschließen."
)


#: Someone else changed what the remarks freeze since the check list was loaded.
NEUE_BEMERKUNGEN = _(
    "Nicht abgeschlossen: inzwischen wurde anderswo eine Notiz, „Soll zählt“ oder eine "
    "Differenz von Hand geändert, etwa in der Zähl-App. Die Seite zeigt jetzt, was dort "
    "steht. Bitte prüfen und dann abschließen."
)


class NeueWerte(ValidationError):
    """Something arrived from the app after the check list was last seen.

    Raised once the inventory and all its count positions are locked
    ('Inventur.zum_abschluss_sperren'), so nothing slips in between the
    comparison and the freezing. A quantity or a weighing that still held
    its row was waited for and counts in the comparison. One that comes later
    waits until the result is frozen and hears 409. A new, revived or removed
    row waits for the inventory's lock ('api.checks.zaehlbar_unter_sperre').
    The views compare early as well, for the message, but only this
    comparison is final. Until 10/2026 only the inventory was locked, and a
    quantity could still land between the comparison and the reading.
    """


class NeueBemerkungen(NeueWerte):
    """Like 'NeueWerte', for notes, ticks and differences by hand ('bemerkungsstand')."""


@transaction.atomic
def abschliessen(
    inventur: Inventur,
    entscheidungen: Entscheidungen,
    *,
    gesehen: str | None = None,
    bemerkungen: str | None = None,
) -> Abschlussbericht:
    """Freeze and then apply what was answered, or nothing at all.

    If an answer fails, everything rolls back, the freezing too: a half-closed
    inventory with half-changed master data would be worse than a second
    round on the page.

    The inventory and its rows are locked first: two requests pressing the
    button almost at once must not both freeze, and what the app writes in
    the meantime either lands before the comparison or waits and hears 409.
    'gesehen' is the count state the check list showed ('zaehlstand').
    Compared under the locks, so what arrives from the app after that is not
    frozen unseen (until 10/2026 the views compared before any lock, and then
    the closing locked only the inventory). 'bemerkungen' is the same for
    notes, ticks and differences by hand ('bemerkungsstand'). They lock the
    inventory before they write ('services.notes'), so they too land before
    the comparison or wait until the result is frozen. Until 10/2026 they
    were frozen unseen.
    """
    inventur = inventur.zum_abschluss_sperren()
    # The status as it stands under the lock, not as the page loaded it: a
    # second tab may have closed the inventory or taken it back into
    # preparation in between (until 10/2026 a closing from preparation went
    # through). A second click hears "already closed", not "new values".
    if inventur.status == Inventurstatus.ABGESCHLOSSEN:
        raise ValidationError(_("Diese Inventur ist bereits abgeschlossen."))
    if inventur.status != Inventurstatus.ZAEHLUNG:
        raise ValidationError(
            _("%(inventur)s steht nicht in der Zählung. Abgeschlossen wird nur von dort.")
            % {"inventur": inventur}
        )
    if gesehen is not None and gesehen != zaehlstand(inventur):
        raise NeueWerte(NEUE_WERTE)
    if bemerkungen is not None and bemerkungen != bemerkungsstand_wie(inventur, bemerkungen):
        raise NeueBemerkungen(NEUE_BEMERKUNGEN)
    klaerung = klaeren(inventur)
    _pruefen(klaerung, entscheidungen)

    # Before freezing, unlike everything that follows: the target belongs in the result.
    soll_von_hand = _soll_setzen(inventur, klaerung, entscheidungen)

    bericht = Abschlussbericht(
        eingefroren=inventur.abschliessen(soll_von_hand=soll_von_hand),
        soll_gesetzt=len(soll_von_hand),
    )

    for eintrag in klaerung.wiederaufnahmen:
        if eintrag.schluessel in entscheidungen.wiederaufnehmen:
            eintrag.hausartikel.aktivieren()
            bericht.wieder_aktiv += 1

    for eintrag in klaerung.neue_erwartungen:
        if eintrag.schluessel in entscheidungen.aufnehmen:
            _aufnehmen(eintrag)
            bericht.aufgenommen += 1

    for eintrag in klaerung.ungezaehlte_gebinde:
        if eintrag.schluessel in entscheidungen.herausnehmen:
            _herausnehmen(eintrag)
            bericht.herausgenommen += 1

    # One frame for every switch of a type: each one on its own compared the
    # counting lists of all houses before and after, fifteen queries per
    # switch inside the transaction that holds the inventory locked
    # (measured 2026-10-09). In one frame they move to the end together.
    with neue_art_ans_ende():
        _ungezaehlte_beantworten(inventur, klaerung, entscheidungen, bericht)
        for eintrag in klaerung.ausserhalb:
            ziel = _ziel(eintrag.ziele, entscheidungen.in_art.get(eintrag.schluessel))
            if ziel is None:
                continue
            _art_wechseln(inventur, eintrag, ziel, entscheidungen)
            bericht.in_die_art += 1

    bericht.behaelter_zugeordnet = _behaelter_zuordnen(inventur, klaerung, entscheidungen)
    return bericht


def _ungezaehlte_beantworten(
    inventur: Inventur, klaerung: Klaerung, entscheidungen: Entscheidungen, bericht
) -> None:
    """The fourth block: per article one answer, on the row, a switch perhaps for its product."""
    for eintrag in klaerung.ungezaehlte_artikel:
        antwort = entscheidungen.ungezaehlt.get(eintrag.schluessel, "")
        if antwort == INAKTIV_SETZEN:
            eintrag.hausartikel.deaktivieren()
            bericht.inaktiv_gesetzt += 1
        elif antwort == EINSTELLEN:
            eintrag.hausartikel.einstellen()
            bericht.eingestellt += 1
        elif (ziel := _wechselziel(eintrag.andere_arten, antwort)) is not None:
            _art_wechseln(inventur, eintrag, ziel, entscheidungen)
            bericht.art_gewechselt += 1


def _aufnehmen(eintrag: NeueErwartung) -> None:
    """Article, pack or scale into the location's counting list.

    Newly expected things go to the end, because 'Sortiert' assigns the
    position itself. A newly added article is only expected the way it was
    found: if the box lay on the shelf, no weighing row arises next time.
    Goods that are not weighed get no scale ('Erwartung.anlegen'). Until
    10/2026 they did, and the scale stood ready for the day the product went
    onto it.
    """
    erwartung = Erwartung.objects.filter(ort=eintrag.ort, artikel_id=eintrag.artikel.pk).first()
    if erwartung is None:
        erwartung = _neu_in_der_liste(eintrag)
        if erwartung is None:
            return
    if eintrag.gebinde is not None:
        ErwartungGebinde.objects.get_or_create(erwartung=erwartung, gebinde=eintrag.gebinde)
    elif eintrag.gewogen and eintrag.artikel.abwiegbare_ware and not erwartung.wiegen_erwartet:
        erwartung.wiegen_erwartet = True
        erwartung.save(update_fields=["wiegen_erwartet"])


def _neu_in_der_liste(eintrag: NeueErwartung) -> Erwartung | None:
    """Creates the place, or returns the one the counting list's page created in the same moment.

    A savepoint per attempt: that page may take the same article into the
    same list ('ZaehllisteView'), and then its place wins and this one only
    adds what it found. Or it took another article, whose place at the end is
    the one this one counted on, and the second attempt takes the next one.
    Until 10/2026 the closing ended in a server error then and froze nothing.
    None where the place arose here, as wanted.
    """

    def anlegen() -> None:
        with transaction.atomic():
            Erwartung.anlegen(
                eintrag.ort,
                eintrag.artikel,
                wiegen=eintrag.gewogen,
                vorgaben=[(eintrag.gebinde.pk, None)] if eintrag.gebinde is not None else [],
            )

    try:
        anlegen()
        return None
    except IntegrityError:
        vorhanden = Erwartung.objects.filter(ort=eintrag.ort, artikel_id=eintrag.artikel.pk).first()
        if vorhanden is not None:
            return vorhanden
    anlegen()
    return None


def _herausnehmen(eintrag: UngezaehltesGebinde) -> None:
    """The pack loses its default. The scale is on the expectation itself."""
    if eintrag.vorgabe is not None:
        eintrag.vorgabe.delete()
    elif eintrag.erwartung is not None:
        eintrag.erwartung.wiegen_erwartet = False
        eintrag.erwartung.save(update_fields=["wiegen_erwartet"])


def _behaelter_zuordnen(
    inventur: Inventur, klaerung: Klaerung, entscheidungen: Entscheidungen
) -> int:
    """Assigns the foreign containers as answered and says how many there were."""
    zugeordnet = 0
    for eintrag in klaerung.fremde_behaelter:
        antwort = entscheidungen.behaelter.get(eintrag.schluessel)
        if antwort == DEM_ARTIKEL:
            eintrag.traeger.behaelter.add(eintrag.behaelter)
        elif antwort == DEM_HAUS:
            eintrag.behaelter.haeuser.add(inventur.haus)
        else:
            continue
        zugeordnet += 1
    return zugeordnet


def _ziel(arten: list[Inventurart], nummer: str | None) -> Inventurart | None:
    """The type with this number, if it is one the row offers."""
    return next((art for art in arten if str(art.pk) == nummer), None)


def _wechselziel(arten: list[Inventurart], antwort: str) -> Inventurart | None:
    """The type a switch in the fourth block goes to ("art_<pk>"), if the row offers it."""
    if not antwort.startswith(WECHSEL_PRAEFIX):
        return None
    return _ziel(arten, antwort.removeprefix(WECHSEL_PRAEFIX))


def _ganze_ware(eintrag: Artwechsel, entscheidungen: Entscheidungen) -> bool:
    """Whether the switch applies to the whole product. Only where it has further articles."""
    return eintrag.weitere > 0 and entscheidungen.geltung.get(eintrag.schluessel) == GANZE_WARE


def _art_wechseln(
    inventur: Inventur, eintrag: Artwechsel, art: Inventurart, entscheidungen: Entscheidungen
) -> None:
    """The article into the type, or its whole product, as answered.

    For the whole product it is recorded for the run: the other houses see it
    on their question and cannot reverse it for the product
    ('Artentscheidung'). For the article alone there is nothing to record,
    it concerns this house only.
    """
    ganze_ware = _ganze_ware(eintrag, entscheidungen)
    eintrag.hausartikel.inventurart_setzen(art, ganze_ware=ganze_ware)
    if ganze_ware:
        Artentscheidung.objects.update_or_create(
            inventur=inventur, ware_id=eintrag.hausartikel.ware_id, defaults={"inventurart": art}
        )


def _soll_setzen(
    inventur: Inventur, klaerung: Klaerung, entscheidungen: Entscheidungen
) -> dict[int, Decimal]:
    """Creates the target stocks chosen by hand and returns them per house article.

    They come back because they **always apply** ('Inventur.abschliessen'): an
    ordinary 'Sollbestand' had no say for annual goods counted only as an
    ingredient, and the answer would have no effect. 'update_or_create',
    because for exactly this case a row from the file may already be there.
    The question was asked because it has no say, and the answer takes
    precedence.
    """
    gesetzt: dict[int, Decimal] = {}
    for eintrag in klaerung.ohne_soll:
        if (menge := entscheidungen.sollmenge(eintrag.schluessel)) is None:
            continue
        Sollbestand.objects.update_or_create(
            inventur=inventur,
            hausartikel=eintrag.hausartikel,
            defaults={"menge_basiseinheit": menge, "herkunft": Sollherkunft.HAND},
        )
        gesetzt[eintrag.hausartikel.pk] = menge
    return gesetzt


def _pruefen(klaerung: Klaerung, entscheidungen: Entscheidungen) -> None:
    """What cannot be carried out speaks up before anything is written."""
    fehler = []
    for eintrag in klaerung.wiederaufnahmen:
        if eintrag.schluessel in entscheidungen.wiederaufnehmen and eintrag.hinderung:
            fehler.append(f"„{eintrag.hausartikel.name}“: {eintrag.hinderung}")
    for eintrag in klaerung.neue_erwartungen:
        if eintrag.schluessel in entscheidungen.aufnehmen and eintrag.hinderung:
            fehler.append(
                _("„%(artikel)s“ an %(ort)s: %(hinderung)s")
                % {
                    "artikel": eintrag.artikel.name,
                    "ort": eintrag.ort.name,
                    "hinderung": eintrag.hinderung,
                }
            )
    fehler += _art_fehler(klaerung, entscheidungen)
    for eintrag in klaerung.ohne_soll:
        try:
            entscheidungen.sollmenge(eintrag.schluessel)
        except ValidationError as ausnahme:
            fehler.append(
                _("Sollbestand für „%(name)s“: %(meldungen)s")
                % {
                    "name": eintrag.hausartikel.name,
                    "meldungen": " ".join(ausnahme.messages),
                }
            )
    if fehler:
        raise ValidationError(fehler)


def _art_fehler(klaerung: Klaerung, entscheidungen: Entscheidungen) -> list[str]:
    """Switches of the type that are not possible, also because another house decided.

    The page offers only what works. Checked nonetheless: a second window
    can be older than the run, and a call past the page sends what it likes.
    A switch for a product with further articles needs the answer for whom
    it applies, as on the article's page.
    """
    fehler = []
    wechsel: list[Artwechsel] = []
    for eintrag in klaerung.ungezaehlte_artikel:
        antwort = entscheidungen.ungezaehlt.get(eintrag.schluessel, "")
        if not antwort.startswith(WECHSEL_PRAEFIX):
            continue
        if _wechselziel(eintrag.andere_arten, antwort) is None:
            fehler.append(
                _("„%(name)s“: unbekannte Inventurart zum Wechseln.")
                % {"name": eintrag.hausartikel.name}
            )
            continue
        wechsel.append(eintrag)
    for eintrag in klaerung.ausserhalb:
        nummer = entscheidungen.in_art.get(eintrag.schluessel)
        if not nummer:
            continue
        if _ziel(eintrag.ziele, nummer) is None:
            fehler.append(
                _("„%(name)s“: diese Inventur zählt die gewählte Art nicht.")
                % {"name": eintrag.hausartikel.name}
            )
            continue
        wechsel.append(eintrag)
    for eintrag in wechsel:
        name = eintrag.hausartikel.name
        geltung = entscheidungen.geltung.get(eintrag.schluessel)
        if geltung_fehlt(eintrag.weitere, geltung):
            fehler.append(
                _(
                    "„%(name)s“: Gilt die neue Inventurart nur für diesen Artikel oder für alle "
                    "Artikel der Ware?"
                )
                % {"name": name}
            )
        elif _ganze_ware(eintrag, entscheidungen) and eintrag.festgelegt is not None:
            fehler.append(
                _(
                    "%(haus)s hat „%(name)s“ in diesem Lauf für die ganze Ware nach „%(art)s“ "
                    "gesetzt. Das gilt auch hier. Für diesen Artikel allein geht es."
                )
                % {
                    "name": name,
                    "haus": eintrag.festgelegt.inventur.haus.name,
                    "art": eintrag.festgelegt.inventurart.name,
                }
            )
    return fehler
