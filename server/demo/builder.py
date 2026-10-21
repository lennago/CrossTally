"""Builds the demo stock and clears it away again.

The build goes through the application's services, not past them: the
inventories arise with 'inventuren_anlegen', the target stock comes from a
file through 'sollbestand_einlesen', and closing happens with
'Inventur.abschliessen'. That is the reason this app lies in the repo and
not as a finished database: if a model or a service changes, the build
breaks visibly, and the test in 'demo/tests.py' reports it in the CI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import ngettext_lazy, npgettext_lazy

from accounts.models import CustomUser
from inventur.models import Inventur
from katalog.models import (
    Artikel,
    Behaelter,
    Erwartung,
    Gebinde,
    Hausartikel,
    Inventurart,
    Rezepturartikel,
    Ware,
    Warenfamilie,
    Warengruppe,
)
from organisation.models import STANDARD, Bereich, Erscheinungsbild, Haus, Ort

from . import branding, catalog, houses, inventories, users
from .files import beschreibungen
from .names_en import Namen
from .state import ist_demobestand, kennzeichen_entfernen, kennzeichnen


@dataclass
class Aufbaubericht:
    """What has arisen. The command prints it, the test checks it."""

    #: In which language the invented names arose.
    sprache: str = "de"
    haeuser: list[str] = field(default_factory=list)
    konten: list[str] = field(default_factory=list)
    waren: int = 0
    hausartikel: int = 0
    rezepturartikel: int = 0
    erwartungen: int = 0
    laeufe: list[inventories.Laufbericht] = field(default_factory=list)
    dateien: dict[str, str] = field(default_factory=dict)


def bestand_vorhanden() -> bool:
    """Whether something already stands in this database that the demo would overwrite.

    An account with one of the demo names counts too: otherwise the build
    ended behind an existing 'admin' in a bare IntegrityError (09/2026).
    """
    return (
        Haus.objects.exists()
        or Ware.objects.exists()
        or CustomUser.objects.filter(
            username__in=[kennung for kennung, *_ in users.KONTEN]
        ).exists()
    )


def eigener_bestand() -> bool:
    """Whether this database holds anything the demo did not bring, so it belongs to someone.

    The marker says what is the demo ('state.py'). Without it every account,
    house, product, article, inventory and article group is someone's own,
    and so are a family, a container or a version of the appearance beyond
    those of the migrations. That is a database in use, or a server set up
    for operation with its first administrator. The demo builds nothing
    beside it, because its accounts carry the password from DEMO.md, and
    '--reset' clears nothing of it away. Until 10/2026 it did both, as long
    as no account bore a demo name.

    Not counted is what every fresh database holds: the base units, "Ohne
    Behälter", "Nicht zählbar", the first inventory type and the standard
    appearance. Whoever deletes everything in the demo keeps the marker and
    may still reset.
    """
    if ist_demobestand():
        return False
    eigenes = (
        CustomUser.objects.all(),
        Haus.objects.all(),
        Ware.objects.all(),
        Artikel.objects.all(),
        Inventur.objects.all(),
        Warengruppe.objects.all(),
        Warenfamilie.objects.filter(sammelt_ignorierte=False),
        Behaelter.objects.filter(immer_erlaubt=False),
        Erscheinungsbild.objects.exclude(name=STANDARD),
    )
    return any(saetze.exists() for saetze in eigenes)


#: What a reset would delete, each with its count: the model and the wording.
#: 'ngettext_lazy' with the key name picks the form when the count is filled
#: in, and 'makemessages' sees the literals. Until 10/2026 the wording was
#: German in the English build too.
ZAEHLWORTE = (
    (Haus, ngettext_lazy("%(anzahl)s Haus", "%(anzahl)s Häuser", "anzahl")),
    (Ware, ngettext_lazy("%(anzahl)s Ware", "%(anzahl)s Waren", "anzahl")),
    # With a context: the pages use the same words without a plural form.
    (
        Hausartikel,
        npgettext_lazy("Demo-Bestand", "%(anzahl)s Artikel", "%(anzahl)s Artikel", "anzahl"),
    ),
    (Inventur, ngettext_lazy("%(anzahl)s Inventur", "%(anzahl)s Inventuren", "anzahl")),
    (CustomUser, ngettext_lazy("%(anzahl)s Konto", "%(anzahl)s Konten", "anzahl")),
)


def was_dasteht() -> str:
    """What a reset would delete, in numbers.

    The demo writes into the database from 'DATABASE_URL', and during
    development that is the same one the real master data lies in. Whoever
    wants to delete should see beforehand what is being talked about.
    """
    teile = [str(wort % {"anzahl": modell.objects.count()}) for modell, wort in ZAEHLWORTE]
    # Own versions of the appearance are not counted, but they would be gone.
    # Whoever uploaded a logo in the demo should read it beforehand.
    if Erscheinungsbild.objects.exclude(name=STANDARD).exists():
        teile.append(_("das hochgeladene Logo"))
    return ", ".join(teile)


def datenbank() -> str:
    """Which database is currently meant, as it stands in the settings."""
    angaben = settings.DATABASES["default"]
    return str(angaben.get("NAME") or angaben.get("ENGINE", "unbekannt"))


@transaction.atomic
def leeren() -> None:
    """Clears away what the demo creates. The master data of the migration stays.

    The order follows the references: first the inventories, then the counting
    lists, then the articles. 'Artikel' combines house and recipe articles,
    which is why it comes at the end of the article rows.
    """
    kennzeichen_entfernen()
    branding.logo_entfernen()
    Inventur.objects.all().delete()
    Erwartung.objects.all().delete()
    Gebinde.objects.all().delete()
    Hausartikel.objects.all().delete()
    Rezepturartikel.objects.all().delete()
    Artikel.objects.all().delete()
    # After the inventories and the house articles: both hold their type
    # ('PROTECT'). Afterwards there is the one of the migration again, as in
    # a database set up fresh.
    Inventurart.objects.all().delete()
    Inventurart.erste()
    Ware.objects.all().delete()
    Warengruppe.objects.all().delete()
    Warenfamilie.objects.filter(sammelt_ignorierte=False).delete()
    Behaelter.objects.filter(immer_erlaubt=False).delete()
    Ort.objects.all().delete()
    Bereich.objects.all().delete()
    Haus.objects.all().delete()
    CustomUser.objects.filter(username__in=[kennung for kennung, *_ in users.KONTEN]).delete()


@transaction.atomic
def aufbauen(heute: date | None = None, sprache: str = "de") -> Aufbaubericht:
    """The whole demo stock, from the catalog to the running inventory.

    'sprache' decides in which language the invented names arise. They are
    data and do not go through gettext, see 'demo/names_en.py'. The blueprints
    stay German, translation only happens when creating.
    """
    heute = heute or timezone.localdate()
    namen = Namen(sprache)
    bericht = Aufbaubericht(sprache=sprache, dateien=beschreibungen(sprache))
    # The marker in the same transaction as the stock: it arrives with it or
    # not at all. Until the end no other connection sees either of them.
    kennzeichnen()

    haeuser = houses.haeuser_anlegen(namen)
    bericht.haeuser = [haus.name for haus in haeuser.values()]
    konten = users.konten_anlegen(haeuser, namen)
    bericht.konten = list(konten)

    familien = catalog.familien_anlegen(namen)
    behaelter = catalog.behaelter_anlegen(haeuser, namen)
    waren = catalog.waren_anlegen(familien, behaelter, namen)
    catalog.behaelter_je_ware_zuordnen(waren, behaelter)
    inventurarten = catalog.inventurarten_anlegen(namen)
    rezepturartikel = catalog.rezepturartikel_anlegen(waren, behaelter, namen)
    bericht.waren = len(waren)
    bericht.rezepturartikel = len(rezepturartikel)

    warengruppen = houses.warengruppen_anlegen(namen)
    ausgeschieden = houses.ausgeschieden_am(heute)
    for hausname, artikelplan, ortsplan in (
        (houses.NORD, houses.ARTIKEL_NORD, houses.ORTE_NORD),
        (houses.OST, houses.ARTIKEL_OST, houses.ORTE_OST),
        (houses.SUED, houses.ARTIKEL_SUED, houses.ORTE_SUED),
    ):
        artikel = houses.artikel_anlegen(
            haeuser[hausname], artikelplan, waren, warengruppen, ausgeschieden, namen, inventurarten
        )
        orte = houses.orte_anlegen(haeuser[hausname], ortsplan, namen)
        bericht.hausartikel += len(artikel)
        bericht.erwartungen += houses.zaehllisten_anlegen(
            orte, ortsplan, artikel, rezepturartikel, namen
        )

    bericht.laeufe = inventories.laeufe_anlegen(haeuser, konten, heute, namen, inventurarten)
    catalog.versehentlich_ignorieren(namen)
    # The cones' new number arises only in the running run ('houses.NEU_VERGEBEN').
    bericht.hausartikel = Hausartikel.objects.count()
    return bericht
