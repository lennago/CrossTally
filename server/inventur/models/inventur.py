"""The inventory itself: states, creating count positions, the effective target, the closing."""

from collections import defaultdict
from collections.abc import Callable, Collection
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from katalog.formats import HOECHSTMENGE
from katalog.models import (
    GEFUEHRT,
    Artikelstatus,
    Hausartikel,
    Inventurart,
    Rezepturartikel,
    Rezepturkomponente,
    Zaehlumfang,
    artikel_sperren,
)
from organisation.models import Haus, Ort

from .base import (
    Inventurstatus,
    stichtag_kurz,
    verteilt_ueber_rezepturen,
)

if TYPE_CHECKING:
    # Only for pyright: without the mypy plugin of the django-stubs, reverse
    # relations, '_id' columns and 'get_…_display' are unknown. Nothing of this
    # is loaded at runtime. The dependency direction of the apps stays as it is.
    from django.db.models.fields.related_descriptors import RelatedManager

    from .ergebnis import (
        Artentscheidung,
        Inventurergebnis,
        Inventurnotiz,
    )
    from .sollbestand import Sollbestand, Sollzeile
    from .zaehlung import Zaehlposition


def orte_und_artikel_sperren(haus_ids: Collection[int]) -> None:
    """Locks the locations and articles of these houses until the transaction ends.

    One order for every path that creates or changes count positions or what
    they hang on: the inventory, then the locations, then the articles, then
    the rows. A new row of the app locks its inventory, location and article
    in that order ('api.checks.zaehlbar_unter_sperre', 'api.serializers.counting.ort_sperren',
    'gesperrter_artikel'), merging or splitting a product its articles before
    its rows ('katalog.models.zaehlpositionen_sperren'), merging locations
    both locations by number ('katalog.ort_split_merge.zusammenfuehren').
    Whoever comes second waits, and nobody holds what an earlier step of the
    other one needs. Until 10/2026 several paths took an article or a row
    first and the locations after it, and two transactions could wait for
    each other until PostgreSQL broke one off: creating a run house by house
    against a product's articles across houses, the clarification that
    activated an article before it locked, the change of types that touched
    rows first, the closing that changed articles after it had locked its rows.

    All houses in one go: a run creates its inventories in one transaction,
    and house by house the articles would not come in one order. Locked are
    the houses' own articles and every article their expectations name, a
    recipe article belongs to no house. Without the key, so the foreign keys
    of other rows go on checking against them meanwhile. By number and
    without 'Meta.ordering', which joins area and house. The house of a
    location is asked in a subquery: a join in the locking query would lock
    the areas along. A no-op under SQLite.
    """
    from katalog.models import Erwartung  # noqa: PLC0415  (avoids a circular import)

    haeuser = sorted(set(haus_ids))
    list(
        Ort.objects.select_for_update(no_key=True)
        .filter(pk__in=Ort.objects.filter(bereich__haus_id__in=haeuser).values("pk"))
        .order_by("pk")
        .values_list("pk", flat=True)
    )
    artikel_sperren(
        {
            *Hausartikel.objects.filter(haus_id__in=haeuser).values_list("pk", flat=True),
            *Erwartung.objects.filter(ort__bereich__haus_id__in=haeuser).values_list(
                "artikel_id", flat=True
            ),
        }
    )


class Inventur(models.Model):
    """An inventory of a house on a cut-off date.

    Without a designation of its own: cut-off date, house and types describe
    it completely, and a freely typed name would sooner or later drift away
    from that. There is one per house and day. Thus the cut-off date is at the
    same time the key of the cross-house run.

    What it counts are its inventory types, each a tab in the app. A monthly
    inventory counts one, an annual one all. Which house articles belong to a
    type says the article ('Hausartikel.inventurart').
    """

    haus = models.ForeignKey(
        Haus, on_delete=models.PROTECT, related_name="inventuren", verbose_name=_("Haus")
    )
    inventurarten = models.ManyToManyField(
        Inventurart,
        through="InventurInventurart",
        related_name="inventuren",
        verbose_name=_("Inventurarten"),
    )
    stichtag = models.DateField(default=timezone.localdate, verbose_name=_("Stichtag"))
    status = models.CharField(
        max_length=15,
        choices=Inventurstatus.choices,
        default=Inventurstatus.VORBEREITUNG,
        verbose_name=_("Status"),
    )
    erstellt_am = models.DateTimeField(auto_now_add=True, verbose_name=_("erstellt am"))
    abgeschlossen_am = models.DateTimeField(
        null=True, blank=True, verbose_name=_("abgeschlossen am")
    )
    zaehlende = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name="inventuren",
        blank=True,
        verbose_name=_("Zählende"),
        help_text=_("Wer in dieser Inventur über die Zähl-App zählen darf."),
        db_table="inventur_zuordnung",
    )
    #: The difference comes from the inventory management system and is typed in
    #: per article ('Inventurnotiz.differenz'). Only without a target stock, see
    #: 'differenz_von_hand_setzen'.
    differenz_von_hand = models.BooleanField(
        default=False,
        verbose_name=_("Differenz von Hand"),
        help_text=_(
            "Ohne Sollbestand: Die Differenz aus der Warenwirtschaft wird je Artikel "
            "eingetragen, in der Prüfliste oder in der Zähl-App."
        ),
    )
    #: Counting stops, checking goes on ('sperre_setzen'). No fourth status: the
    #: app sees only inventories in the count, and closing needs the count too.
    gesperrt = models.BooleanField(
        default=False,
        verbose_name=_("Gesperrt"),
        help_text=_(
            "In der Zähl-App ist nur noch die Prüfliste offen. Neue Zählungen nimmt der "
            "Server nicht an."
        ),
    )

    # Created by Django at runtime. Only named here so pyright knows them.
    haus_id: int
    get_status_display: Callable[[], str]
    sollbestaende: RelatedManager[Sollbestand]
    sollzeilen: RelatedManager[Sollzeile]
    zaehlpositionen: RelatedManager[Zaehlposition]
    notizen: RelatedManager[Inventurnotiz]
    ergebnisse: RelatedManager[Inventurergebnis]
    artentscheidungen: RelatedManager[Artentscheidung]
    # Entered by 'services.inventuren_anlegen', so the message does not have to recount.
    anzahl_positionen: int

    #: What the name reads beyond the house. Lists that name many inventories
    #: prefetch it ('django_project.admin_site.fuer_den_namen').
    NAME_BRAUCHT = ("inventurarten",)

    class Meta:
        verbose_name = _("Inventur")
        verbose_name_plural = _("Inventuren")
        ordering = ["-stichtag", "haus__name"]
        constraints = [
            # One a day: what else is to be counted becomes another tab of the
            # same inventory. Two of them would count the same shelf twice.
            models.UniqueConstraint(
                fields=["haus", "stichtag"],
                name="eine_inventur_je_haus_und_tag",
                violation_error_message=_(
                    "Für dieses Haus gibt es an diesem Tag bereits eine Inventur."
                ),
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status=Inventurstatus.ABGESCHLOSSEN, abgeschlossen_am__isnull=False)
                    | (
                        ~models.Q(status=Inventurstatus.ABGESCHLOSSEN)
                        & models.Q(abgeschlossen_am__isnull=True)
                    )
                ),
                name="abgeschlossen_am_genau_bei_abschluss",
                violation_error_message=_(
                    "Der Abschlusszeitpunkt steht genau dann, wenn die Inventur abgeschlossen ist."
                ),
            ),
        ]

    def __str__(self) -> str:
        return f"{stichtag_kurz(self.stichtag)} {self.haus.name} {self.arten_anzeige}"

    def get_absolute_url(self) -> str:
        return reverse("inventur_detail", kwargs={"pk": self.pk})

    def arten(self) -> models.QuerySet[Inventurart]:
        """The types in their order, read once per object.

        Name, title, scope and the clarification all ask for them, up to seven
        times on one page (measured on 2026-10-18). The first call fills
        Django's prefetch cache, the same place a view's
        'prefetch_related("inventurarten")' fills. 'set()' in 'arten_aendern'
        empties it again, as every change through the relation does.
        """
        if "inventurarten" not in getattr(self, "_prefetched_objects_cache", {}):
            models.prefetch_related_objects([self], "inventurarten")
        return self.inventurarten.all()

    @property
    def arten_anzeige(self) -> str:
        """The types as words, in their order: "Monatsinventur + Becherinventur"."""
        return " + ".join(art.name for art in self.arten())

    @property
    def zaehlumfang(self) -> Zaehlumfang:
        """What is due in this inventory ('katalog.models.Zaehlumfang'), the types in order."""
        return Zaehlumfang(tuple(art.pk for art in self.arten()))

    # -- The run over several houses --------------------------------------

    def lauf(self) -> models.QuerySet[Inventur]:
        """All inventories of the same cut-off date, this one included.

        That replaces the former "parent inventory": it would have carried
        nothing that does not stand here anyway.
        """
        return Inventur.objects.filter(stichtag=self.stichtag)

    @property
    def gehoert_zu_lauf(self) -> bool:
        """Whether other houses run the same inventory on the same day."""
        return self.lauf().exclude(pk=self.pk).exists()

    def lauf_url(self) -> str:
        """The overall view of the run. With only one house it leads back here."""
        return reverse("inventur_lauf", kwargs={"stichtag": self.stichtag.isoformat()})

    # -- Who may count here -----------------------------------------------

    def darf_zaehlen(self, benutzer) -> bool:
        """Whether this user may count in this inventory.

        The assignment is **explicit**, for managers too: whoever works in a
        house does not automatically count every inventory there, and a running
        inventory should be able to say who is on it. Closing clears it
        ('abschliessen'): who counted is not kept. Only administrators count
        everywhere, because they have to be able to step in if in doubt without
        first entering themselves.

        The house assignment ('CustomUser.haeuser') is unaffected: it says who is
        *suggested*, not who may count.
        """
        return benutzer.is_superuser or self.zaehlende.filter(pk=benutzer.pk).exists()

    def alle_zaehlenden(self):
        """Who may count here: the entered ones and administrators.

        The admins are in no assignment ('darf_zaehlen' lets them through
        without), but belong in every listing: otherwise a list looks empty
        although someone could count.
        """
        from django.contrib.auth import get_user_model  # noqa: PLC0415

        return (
            get_user_model()
            .objects.filter(models.Q(inventuren=self) | models.Q(is_superuser=True, is_active=True))
            .distinct()
            .order_by("last_name", "first_name")
        )

    @staticmethod
    def eintragbare_zaehlende():
        """Who can be entered as a counter: active accounts, and no administrators.

        Administrators count everywhere anyway ('darf_zaehlen'). A box for
        them would be a sham decision: unticked it would look as if they may
        not. The website offers this choice and so does the administration
        ('ZaehlendeForm', 'InventurAdmin').
        """
        from django.contrib.auth import get_user_model  # noqa: PLC0415

        return get_user_model().objects.filter(is_active=True, is_superuser=False)

    def vorgeschlagene_zaehlende(self):
        """Who is assigned to the house: the suggestion when assigning."""
        return self.haus.mitarbeitende.filter(is_active=True).order_by("last_name", "first_name")

    @classmethod
    def fuer_zaehler(cls, benutzer) -> models.QuerySet[Inventur]:
        """The inventories this user may count in."""
        if benutzer.is_superuser:
            return cls.objects.all()
        return cls.objects.filter(zaehlende=benutzer)

    @classmethod
    def zaehlbare_fuer(cls, benutzer) -> models.QuerySet[Inventur]:
        """What this user can count on **now**.

        Only the running count: in preparation the target stock is not in place
        yet, and the count positions can still change. That is why "Start
        counting" only then releases the inventory. Closed ones are frozen. The
        same line is drawn by 'pruefe_zaehlbar' when writing.
        """
        return cls.fuer_zaehler(benutzer).filter(status=Inventurstatus.ZAEHLUNG)

    # -- States -----------------------------------------------------------

    @property
    def laeuft(self) -> bool:
        return self.status != Inventurstatus.ABGESCHLOSSEN

    def zaehlung_starten(self) -> None:
        """From preparation into the count."""
        if self.status != Inventurstatus.VORBEREITUNG:
            raise ValidationError(_("Gezählt wird nur aus der Vorbereitung heraus."))
        self.status = Inventurstatus.ZAEHLUNG
        self.save(update_fields=["status"])

    @property
    def hat_sollbestand(self) -> bool:
        """Whether a target came in at all: from file or by hand, matched or still asked."""
        return self.sollbestaende.exists() or self.sollzeilen.exists()

    def differenz_von_hand_setzen(self, an: bool) -> None:
        """Whether the difference is typed in per article instead of computed.

        Not every house has a file with the stock. Some see the difference only
        in their inventory management system, once the counted quantities are
        in there. Typed in here, it reaches the sheet and the evaluation like a
        computed one. Up to 10/2026 it went into the notes and stayed text.

        **Only without a target**: with one the difference computes itself, and
        two differences per article could only contradict each other. While it
        is on, no target comes in ('InventurDetailView.post', 'SollPflegeView').
        Switching off keeps what was typed. It counts again once switched on.
        Allowed as long as the inventory runs: the managers switch in the count
        too, on the website as in the counting app.
        """
        with transaction.atomic():
            frisch = self._frisch_und_gesperrt()
            if not frisch.laeuft:
                raise ValidationError(_("Eine abgeschlossene Inventur ändert sich nicht mehr."))
            if an and frisch.hat_sollbestand:
                raise ValidationError(
                    _(
                        "Mit Sollbestand rechnet sich die Differenz selbst. Von Hand wird sie "
                        "nur ohne Sollbestand eingetragen."
                    )
                )
            self.differenz_von_hand = an
            self.save(update_fields=["differenz_von_hand"])

    def _frisch_und_gesperrt(self) -> Inventur:
        """The row as it stands now, locked until the transaction ends.

        The object may be stale: another request can have closed the
        inventory or read in a target since it was loaded. Checked is the
        fresh row, as 'abschliessen' does it, and the lock keeps a closing or
        an upload waiting until the switch is written (a no-op under SQLite).
        Until 10/2026 the switches trusted the object and wrote without a lock.
        Without the key, as every other writer locks it: 'FOR UPDATE' would
        hold off the key checks of a transaction that touches the inventory's
        rows twice, a merge for instance, until the switch commits.
        """
        return Inventur.objects.select_for_update(no_key=True).get(pk=self.pk)

    def zurueck_in_vorbereitung(self) -> None:
        """Back out of the count. The counted quantities stay.

        It only turns the status. Whoever started too early should not pay for it
        by the inputs vanishing. From the closing there is no way back: the frozen
        results hang on it, and positions like target stocks have long been
        cleared away by then.
        """
        with transaction.atomic():
            if self._frisch_und_gesperrt().status != Inventurstatus.ZAEHLUNG:
                raise ValidationError(_("Zurücknehmen lässt sich nur eine laufende Zählung."))
            self.status = Inventurstatus.VORBEREITUNG
            # The preparation holds the counts back anyway. Started again, the
            # count should be open, and not stay closed by a lock nobody sees
            # any more.
            self.gesperrt = False
            self.save(update_fields=["status", "gesperrt"])

    def sperre_setzen(self, an: bool) -> None:
        """Lock the count or open it again. Closing works either way.

        Locked is when the counting is done and the check begins: in the
        counting app only the check list stays open, with its notes and the
        difference by hand. A new count would move the numbers someone is
        just checking. Whatever a phone still sends from a dead spot waits
        there until the lock opens ('api/checks.py', 'pruefe_zaehlbar').

        Only in the count: in preparation nobody counts anyway, and a closed
        inventory does not change any more.

        Locking waits for what is being counted at this moment. It locks
        the inventory and then every row by number, in the closing's order
        ('zum_abschluss_sperren'). A quantity or a weighing locks only its
        row while it writes. One that holds it now is finished first, and
        one that comes later reads the lock under its row
        ('api.serializers.counting.gesperrte_zeile') and waits on the phone.
        Until 10/2026 only the inventory was locked, which those writes never
        touch: a count checked a moment before the lock landed after it,
        while the check had begun. Opening locks only the inventory, because
        no write waits for that.
        """
        with transaction.atomic():
            frisch = self.zum_abschluss_sperren() if an else self._frisch_und_gesperrt()
            if frisch.status != Inventurstatus.ZAEHLUNG:
                raise ValidationError(_("Sperren und öffnen lässt sich nur eine laufende Zählung."))
            self.gesperrt = an
            self.save(update_fields=["gesperrt"])

    # -- Count positions --------------------------------------------------

    def zaehlpositionen_anlegen(
        self, *, wiederbeleben: bool = False, wiederbeleben_fuer: Collection[int] = ()
    ) -> int:
        """Generates the expected positions from the house's expectations.

        Per expectation one position arises per stored pack, and for a weighable
        product another one for weighing, provided it is weighed at this location
        ('Erwartung.wiegen_erwartet'): in storage the box stands, and a weighing
        row would stay open there at every inventory. A recipe article gets
        exactly one. It has no packs, it is counted in its own unit.

        An **inactive** pack gets no position, even where a counting list names
        it (decided 2026-10-18). The product is no longer carried in it, and the
        app does not offer it either ('api/master_data.py'). What is left on the
        shelf is counted in one of the active packs, through the search if need
        be. Its default stays and counts again once the pack is active again.
        Until 10/2026 every inventory gave it a row.

        What actually turns up when counting is decided on site: the PWA creates
        further positions for unexpected things. 'ignore_conflicts' makes the call
        repeatable without duplicating what exists.

        An **inactive** house article gets no position although it stays expected
        at its location: it is currently not in the range, and a row that stays
        empty at every inventory would only be in the way. If it is on the shelf
        after all, the app adds it as unexpected. The same applies to a recipe
        article whose ingredient is inactive in the house, because it hangs on it.

        **What belongs to a type this inventory does not count gets no row**
        (since 2026-09-20 for the annual goods, since 2026-10-08 per type). A
        row that stays empty month after month would be in the way in the app
        and would reappear at closing every time. Its **target stock is stored
        nonetheless**: if it is counted after all, it has a say
        ('wirksame_sollbestaende'). It is counted through the app's search and
        then arises as an **unexpected** row. A recipe article is due as soon
        as one ingredient is.

        'wiederbeleben' brings back what an earlier change of the types marked
        as deleted, as long as nothing was entered there ('arten_aendern').
        Without it a type that goes and comes back would stay without rows. The
        index keeps a row marked as deleted in its place, so a new one cannot
        arise beside it. 'wiederbeleben_fuer' does the same for these articles
        only: one that comes back into the range in the clarification of the
        target ('sollzeile_aktivieren'). Rows the counters removed in the app
        for another article stay removed.

        Back comes how many rows arose or came back, not how many the
        expectations name (until 10/2026 the messages counted every row of the
        inventory as new).

        **The house's locations and articles are locked first**
        ('_orte_und_artikel_sperren'), in a transaction of its own or inside
        the caller's.
        """
        with transaction.atomic():
            self._orte_und_artikel_sperren()
            return self._zaehlpositionen_anlegen(
                wiederbeleben=wiederbeleben, wiederbeleben_fuer=wiederbeleben_fuer
            )

    def _orte_und_artikel_sperren(self) -> None:
        """Locks the house's locations and articles ('orte_und_artikel_sperren')."""
        orte_und_artikel_sperren([self.haus_id])

    def _zaehlpositionen_anlegen(
        self, *, wiederbeleben: bool, wiederbeleben_fuer: Collection[int]
    ) -> int:
        # The count position points to the inventory, so its module comes after
        # this one. The import therefore stands in the body, as in 'katalog/models/'.
        from .zaehlung import Zaehlposition  # noqa: PLC0415

        aktive, dran = self._aktive_und_faellige_waren()
        rezepturen = self._rezepturen_der_erwarteten()

        def zeile(erwartung, gebinde) -> Zaehlposition:
            return Zaehlposition(
                inventur=self,
                ort=erwartung.ort,
                artikel_id=erwartung.artikel_id,
                gebinde=gebinde,
                sortierung=erwartung.sortierung,
            )

        neu = []
        for erwartung in self._erwartungen_im_umfang():
            artikel = erwartung.artikel.konkret()
            if isinstance(artikel, Rezepturartikel):
                zutaten = rezepturen.get(artikel.pk, set())
                if not zutaten or not zutaten <= aktive:
                    continue
                if not zutaten & dran:
                    continue
                neu.append(zeile(erwartung, None))
                continue
            neu.extend(
                zeile(erwartung, vorgabe.gebinde)
                for vorgabe in erwartung.gebinde_vorgaben.all()
                if vorgabe.gebinde.aktiv
            )
            if artikel.abwiegbare_ware and erwartung.wiegen_erwartet:
                neu.append(zeile(erwartung, None))

        vorher = self.zaehlpositionen.filter(aktiv=True).count()
        Zaehlposition.objects.bulk_create(neu, ignore_conflicts=True)
        if wiederbeleben or wiederbeleben_fuer:
            self._geloeschte_wiederbeleben(neu, None if wiederbeleben else set(wiederbeleben_fuer))
        return self.zaehlpositionen.filter(aktiv=True).count() - vorher

    def _erwartungen_im_umfang(self) -> list:
        """The house's expectations that get rows: recipe articles, and active articles due here."""
        from katalog.models import Erwartung  # noqa: PLC0415  (avoids a circular import)

        return list(
            Erwartung.objects.filter(ort__bereich__haus=self.haus)
            .filter(
                models.Q(artikel__hausartikel__isnull=True)
                | (
                    models.Q(artikel__hausartikel__aktiv=True)
                    & self.zaehlumfang.hausartikel("artikel__hausartikel")
                )
            )
            .select_related("artikel__hausartikel__ware", "artikel__rezepturartikel")
            .prefetch_related("gebinde_vorgaben__gebinde")
        )

    def _aktive_und_faellige_waren(self) -> tuple[set[int], set[int]]:
        """The products the house carries actively, and those of them due here, in one query.

        An ingredient's type is that of the article the house carries for it. A
        recipe article is due as soon as **one** ingredient is
        ('Zaehlumfang.rezepturartikel').
        """
        aktive: set[int] = set()
        dran: set[int] = set()
        arten = self.zaehlumfang.arten
        for ware_id, art_id in Hausartikel.objects.filter(haus=self.haus, aktiv=True).values_list(
            "ware_id", "inventurart_id"
        ):
            aktive.add(ware_id)
            if art_id in arten:
                dran.add(ware_id)
        return aktive, dran

    def _rezepturen_der_erwarteten(self) -> dict[int, set[int]]:
        """The recipes of all expected recipe articles, in one query instead of one per row."""
        rezepturen: dict[int, set[int]] = defaultdict(set)
        for rezepturartikel_id, ware_id in Rezepturkomponente.objects.filter(
            rezepturartikel__erwartet_an__ort__bereich__haus=self.haus
        ).values_list("rezepturartikel_id", "ware_id"):
            rezepturen[rezepturartikel_id].add(ware_id)
        return rezepturen

    def _geloeschte_wiederbeleben(self, erwartet: list, artikel: set[int] | None) -> None:
        """Rows marked as deleted that are expected again come back, if nothing was entered.

        With 'artikel' only the rows of these articles.
        """
        schluessel = {
            (z.ort_id, z.artikel_id, z.gebinde_id)
            for z in erwartet
            if artikel is None or z.artikel_id in artikel
        }
        geloescht = self.zaehlpositionen.filter(
            aktiv=False, eingabemenge__isnull=True, wiegungen__isnull=True
        ).values_list("pk", "ort_id", "artikel_id", "gebinde_id")
        zurueck = [
            pk
            for pk, *schluessel_der_zeile in geloescht
            if tuple(schluessel_der_zeile) in schluessel
        ]
        if zurueck:
            self.zaehlpositionen.filter(pk__in=zurueck).update(
                aktiv=True, zuletzt_geaendert=timezone.now()
            )

    @transaction.atomic
    def arten_aendern(self, arten) -> tuple[int, int]:
        """Changes the types of an inventory in preparation. Returns rows added and removed.

        A type that comes along gets its rows as at creation, and what an
        earlier removal marked as deleted comes back. A type that goes takes
        its uncounted rows along, marked as deleted: an app that held them
        learns of it on the next sync. Counted is counted, those rows stay and
        come up at closing.

        Only in preparation: during the count the apps already hold their tabs.
        """
        if self.status != Inventurstatus.VORBEREITUNG:
            raise ValidationError(_("Die Arten lassen sich nur in der Vorbereitung ändern."))
        # Before any row is touched: taking rows out locks them, and a merge
        # of products locks its articles before its rows (until 10/2026 the
        # two could wait for each other, 'orte_und_artikel_sperren').
        orte_und_artikel_sperren([self.haus_id])
        arten = list(arten)
        neu = {art.pk for art in arten}
        if not neu:
            raise ValidationError(_("Eine Inventur zählt mindestens eine Inventurart."))
        alt = set(self.inventurarten.values_list("pk", flat=True))
        if neu == alt:
            return 0, 0
        self.inventurarten.set(arten)
        entfernt = self._nicht_mehr_dran_entfernen() if alt - neu else 0
        dazu = self.zaehlpositionen_anlegen(wiederbeleben=True) if neu - alt else 0
        return dazu, entfernt

    def _nicht_mehr_dran_entfernen(self) -> int:
        """Marks the uncounted rows of articles no longer due as deleted. Returns how many."""
        umfang = self.zaehlumfang
        nicht_dran = (
            models.Q(artikel__hausartikel__isnull=False)
            & ~umfang.hausartikel("artikel__hausartikel")
        ) | (
            models.Q(artikel__rezepturartikel__isnull=False)
            & ~umfang.rezepturartikel(self.haus_id, "artikel")
        )
        return (
            self.zaehlpositionen.filter(
                aktiv=True, eingabemenge__isnull=True, wiegungen__isnull=True
            )
            .filter(nicht_dran)
            .update(aktiv=False, zuletzt_geaendert=timezone.now())
        )

    # -- Closing ----------------------------------------------------------

    def zum_abschluss_sperren(self) -> Inventur:
        """The inventory and every one of its count positions, locked until the transaction ends.

        First the inventory: two closings take turns, and creating, reviving
        or removing a row waits for it ('api.checks.zaehlbar_unter_sperre').
        Then the rows: a quantity or a weighing from the app locks only its
        own row while it writes. One that holds a row now is waited for, so
        what it wrote is compared and frozen. One that comes later waits until
        the result is frozen, finds its row gone and hears 409. Only after both
        is anything compared or read. Until 10/2026 only the inventory was
        locked, and a quantity or a weighing between reading and deleting got
        its 200 or 201 and was missing from the result. Merging and splitting
        a product lock the rows they re-hang in the same order, before they
        change anything ('katalog.models.zaehlpositionen_sperren').

        By number and not by 'Meta.ordering': that joins the nullable pack, and
        PostgreSQL refuses to lock the nullable side of an outer join. A no-op
        under SQLite. Back comes the fresh row of the inventory.

        The inventory 'FOR NO KEY UPDATE', as every write of the app locks it
        ('api.checks.zaehlbar_unter_sperre'). A merge that holds rows checks
        their keys against the inventory at its commit. Under 'FOR UPDATE' the
        closing, waiting for those rows, held the inventory against that
        check, and PostgreSQL broke one of the two off (until 10/2026).

        Between the two the house's locations and articles: the answers of the
        closing change articles (activate, set inactive, discontinue, another
        type), and a merge of products locks its articles before its rows.
        Locked only after the rows, the closing and a merge could wait for each
        other (until 10/2026, 'orte_und_artikel_sperren').
        """
        frisch = Inventur.objects.select_for_update(no_key=True).get(pk=self.pk)
        orte_und_artikel_sperren([self.haus_id])
        list(self.zaehlpositionen.select_for_update().order_by("pk").values_list("pk", flat=True))
        return frisch

    @transaction.atomic
    def abschliessen(self, zeitpunkt=None, soll_von_hand: dict[int, Decimal] | None = None) -> int:
        """Freezes a result per house article and clears the working data away.

        The count positions, the target stocks and the assignment of the
        counters vanish in the process. The troubleshooting over the locations
        happens **before** closing. What
        remains is per house article a row with actual, a copy of the target and
        the difference.

        Frozen is every article that was counted **or** had a target: an article
        with target and without count is the interesting case, it is missing
        entirely. Plus every one a **note** hangs on. Otherwise closing would lose
        of all things what someone wrote down on purpose.

        Where "Target counts" is set in the check view, **the target** becomes the
        result and the difference zero. Meant for the case that a count was
        recognisably off and the target stock is the better information. Counted
        stays what was counted: this does not touch the count positions, it only
        takes effect here when freezing.

        'soll_von_hand' are the target stocks someone set specifically in the
        clarification before closing. **They always apply**, even where
        'wirksame_sollbestaende' would not let the file's target have a say:
        annual goods counted in the monthly inventory only as an ingredient of a
        portion. Whoever answers a question decides. If the answer were left
        lying, the row would freeze without a difference although the page reports
        "target set" (that is how it was until 09/2026).
        """
        # The result points to the inventory, its module comes after this one.
        from .ergebnis import Inventurergebnis  # noqa: PLC0415

        # Read fresh and locked, do not trust the object: two requests that
        # both loaded the open state must not both freeze, because the second
        # would run into the partial index of the results. The rows are locked
        # as well, before anything is read ('zum_abschluss_sperren').
        frisch = self.zum_abschluss_sperren()
        if frisch.status == Inventurstatus.ABGESCHLOSSEN:
            raise ValidationError(_("Diese Inventur ist bereits abgeschlossen."))

        jetzt = zeitpunkt or timezone.now()
        soll = self.wirksame_sollbestaende() | (soll_von_hand or {})
        ergebnisse = self._ergebnisse(self.mengen_je_hausartikel(), soll, jetzt)
        Inventurergebnis.objects.bulk_create(ergebnisse)

        self.zaehlpositionen.all().delete()
        self.sollbestaende.all().delete()
        # The open questions about the target file too: after closing nobody
        # answers them any more, and in the administration they would otherwise
        # stand under "Open target rows" forever.
        self.sollzeilen.all().delete()
        self.notizen.all().delete()
        # Who counted is not kept (wish of 2026-10-13). Nobody counts any more,
        # and the assignment would only say who once could have.
        self.zaehlende.clear()
        self.status = Inventurstatus.ABGESCHLOSSEN
        self.abgeschlossen_am = jetzt
        self.save(update_fields=["status", "abgeschlossen_am"])
        return len(ergebnisse)

    def vermerke(
        self, nur: int | None = None, *, mit_zeile: Collection[int] = ()
    ) -> dict[int, tuple[str, bool, Decimal | None]]:
        """Note, tick and difference by hand per house article, as far as one says something.

        A difference by hand counts only while the inventory takes it. Switched
        off, it stays stored but a row with nothing else in it is no remark.
        With 'nur' only this article's.

        Nor does a remark on an article in "Not countable" say anything, unless
        the article has its row anyway: counted, or with a target ('mit_zeile').
        Moving the product there deletes its remarks
        ('katalog.hausartikel_status.aufraeumen'), and since 10/2026 none is
        taken for it ('services.notes'). One written before still lay there,
        and the check list showed a row for it and the closing froze one, for
        something that is not a product. Both ask here, with what each counted,
        so the page shows what the closing freezes.
        """
        von_hand = self.differenz_von_hand
        notizen = self.notizen if nur is None else self.notizen.filter(hausartikel_id=nur)
        return {
            hausartikel_id: (text, uebernehmen, differenz if von_hand else None)
            for hausartikel_id, text, uebernehmen, differenz, status in notizen.values_list(
                "hausartikel_id", "text", "soll_statt_ist", "differenz", "hausartikel__status"
            )
            if (text or uebernehmen or (von_hand and differenz is not None))
            and (status != Artikelstatus.IGNORIERT or hausartikel_id in mit_zeile)
        }

    def _ergebnisse(self, ist: dict, soll: dict, jetzt) -> list:
        """One frozen row per article that was counted, had a target or carries a remark.

        Where "Target counts" is set, the target becomes the result and
        the difference zero. Without a target there is nothing to adopt: the
        mark then runs into nothing instead of freezing a zero.

        **With the difference by hand** the typed one is frozen and the target
        stays empty: there is none, and one computed back from the difference
        would be a number nobody counted or booked.

        Every row freezes the unit its numbers are in, the house's of today,
        with the factor to the product ('Inventurergebnis.einheit').

        A number the result does not hold stops the closing and names the
        article. Mostly it is a typo in the count. Until 10/2026 PostgreSQL
        ended the closing with an error page, and SQLite closed and could not
        read the inventory afterwards.
        """
        # The result points to the inventory, its module comes after this one.
        from .ergebnis import Inventurergebnis  # noqa: PLC0415

        vermerke = self.vermerke(mit_zeile=set(ist) | set(soll))
        ergebnisse = []
        zu_gross = []
        artikel_ids = sorted(set(ist) | set(soll) | set(vermerke))
        artikel_je_pk = Hausartikel.objects.select_related("basiseinheit").in_bulk(artikel_ids)
        for hausartikel_id in artikel_ids:
            artikel = artikel_je_pk[hausartikel_id]
            # Rounded once, to the places of the house's unit, and the
            # difference from the rounded numbers: what the check list showed
            # ('services.results'). Until 10/2026 each was rounded on its own,
            # and the sheet read "actual 3, target 3, difference -1".
            einheit = artikel.basiseinheit
            menge = einheit.runden(ist.get(hausartikel_id, Decimal("0")))
            text, uebernehmen, von_hand = vermerke.get(hausartikel_id, ("", False, None))
            if self.differenz_von_hand:
                sollmenge, differenz = None, None if von_hand is None else einheit.runden(von_hand)
            else:
                sollmenge = soll.get(hausartikel_id)
                if sollmenge is not None:
                    sollmenge = einheit.runden(sollmenge)
                if uebernehmen and sollmenge is not None:
                    menge = sollmenge
                differenz = None if sollmenge is None else menge - sollmenge
            if (wert := _zu_gross(menge, sollmenge, differenz)) is not None:
                zu_gross.append(
                    _(
                        "„%(name)s“: %(menge)s sind mehr, als ein Ergebnis fasst. "
                        "Bitte die Zählung prüfen."
                    )
                    % {"name": artikel.name, "menge": einheit.mit_menge(wert)}
                )
                continue
            ergebnisse.append(
                Inventurergebnis(
                    inventur=self,
                    hausartikel_id=hausartikel_id,
                    gesamtmenge_basiseinheit=_eingefroren(menge),
                    sollbestand=_eingefroren(sollmenge),
                    differenz=_eingefroren(differenz),
                    notiz=text,
                    eingefroren_am=jetzt,
                    einheit_id=artikel.basiseinheit_id,
                    faktor_zur_ware=artikel.faktor_zur_ware,
                )
            )
        if zu_gross:
            raise ValidationError(zu_gross)
        return ergebnisse

    def selbst_gezaehlt(self, nur: int | None = None) -> set[int]:
        """The house articles for which a row **of their own** was counted.

        Not through a recipe: the collection cup that rides along as an ingredient
        of a portion is thus not counted. A hundred more stand on the shelf that
        nobody has looked at. With 'nur' only this article is asked about.
        """
        positionen = self.zaehlpositionen.filter(aktiv=True, artikel__hausartikel__isnull=False)
        if nur is not None:
            positionen = positionen.filter(artikel__hausartikel=nur)
        return set(
            positionen.filter(
                models.Q(eingabemenge__isnull=False) | models.Q(wiegungen__isnull=False)
            ).values_list("artikel_id", flat=True)
        )

    def sollbestaende_im_umfang(self):
        """The target stocks of the articles due in this inventory, by its types.

        More is stored: annual goods in the monthly inventory too, and the 0
        of an inactive or discontinued article. Where it is about the
        preparation (how many have their target, where does the designation
        deviate), only what is also to be counted matters, and an article out
        of the range is not.
        """
        return self.sollbestaende.filter(
            models.Q(hausartikel__aktiv=True) & self.zaehlumfang.hausartikel("hausartikel")
        )

    def wirksame_sollbestaende(self, nur: int | None = None) -> dict[int, Decimal]:
        """The target per house article, as far as it has a say in this inventory.

        **Every** row of the file that can be matched to a carried article is read
        in and stored. But only the target of an article that is due in this
        inventory may have a say, or that was **counted itself** anyway because
        someone added it in the app. Without the restriction all annual goods
        would stand in the check list with actual 0 and full difference in the
        monthly inventory and freeze that way. Without the stored target the added
        article would freeze without a difference although the file would have
        known it.

        The same applies to an **inactive** article whose row still lies as a
        question ('Sollzeile'): if it was counted, its quantity counts as the
        target. A 'Sollbestand' set in the meantime takes precedence. And an
        article that became inactive or discontinued after reading in only has a
        say if it was counted itself: otherwise its target lapses (decision of
        2026-09-23). It would otherwise stand in the result with actual 0 and full
        difference although someone took it out of the range on purpose.

        Computed from the current state. If an article changes its type in the
        middle of the inventory, its target has a say from then on. With 'nur'
        only this article's target is looked up.
        """
        gezaehlt = self.selbst_gezaehlt(nur=nur)
        sollbestaende = self.sollbestaende
        sollzeilen = self.sollzeilen
        if nur is not None:
            sollbestaende = sollbestaende.filter(hausartikel_id=nur)
            sollzeilen = sollzeilen.filter(hausartikel_id=nur)
        # Whether due, asked in the same query: a second one would cost every
        # page that shows the target.
        dran = models.ExpressionWrapper(
            self.zaehlumfang.hausartikel("hausartikel"), output_field=models.BooleanField()
        )
        soll = {
            hausartikel_id: menge
            for hausartikel_id, menge, ist_dran, aktiv in sollbestaende.annotate(
                dran=dran
            ).values_list("hausartikel_id", "menge_basiseinheit", "dran", "hausartikel__aktiv")
            if (aktiv and ist_dran) or hausartikel_id in gezaehlt
        }
        for hausartikel_id, menge in sollzeilen.filter(hausartikel_id__in=gezaehlt).values_list(
            "hausartikel_id", "menge_basiseinheit"
        ):
            soll.setdefault(hausartikel_id, menge)
        return soll

    def mengen_je_hausartikel(self, nur: int | None = None) -> dict[int, Decimal]:
        """Sums the counted quantities onto the house articles of this house.

        House articles count directly. A recipe article is distributed over its
        recipe onto products and from there onto the **carried** house article of
        this house. That is why resolving needs the house. The partial index
        allows only one carried row per product, so the mapping is unambiguous,
        even if it is inactive: its share then stands in the check list, and the
        clarification before closing offers to reactivate it. Only where the
        product is discontinued or not carried at all does the share lapse,
        because it could be booked nowhere.

        The recipes are resolved **collectively**: 'mengen_je_ware' queries per
        call, and in this loop that would be one query per count position. The
        check view recomputes on every refresh.

        With 'nur' only this article's positions are read, and those of every
        recipe article: a portion counted somewhere may land on it.
        """
        gezaehlt: dict[int, Decimal] = {}
        je_rezepturartikel: dict[int, Decimal] = {}

        positionen = self.zaehlpositionen.filter(aktiv=True)
        if nur is not None:
            positionen = positionen.filter(
                models.Q(artikel__hausartikel=nur)
                | models.Q(artikel__rezepturartikel__isnull=False)
            )
        positionen = positionen.select_related(
            "artikel__hausartikel__basiseinheit",
            "artikel__hausartikel__ware__basiseinheit",
            "artikel__rezepturartikel__basiseinheit",
            "gebinde",
        ).prefetch_related("wiegungen__behaelter")
        for position in positionen:
            # 'menge_in_basiseinheit' knows all three paths: pack times factor,
            # sum of the weighings, bare number. Uncounted gives None.
            if (menge := position.menge_in_basiseinheit()) is None:
                continue
            artikel = position.artikel.konkret()
            if isinstance(artikel, Rezepturartikel):
                # First collect the quantity in the recipe article's unit. Onto
                # the products it is distributed by 'verteilt_ueber_rezepturen' in one go.
                je_rezepturartikel[artikel.pk] = (
                    je_rezepturartikel.get(artikel.pk, Decimal("0")) + menge
                )
                continue
            gezaehlt[artikel.pk] = gezaehlt.get(artikel.pk, Decimal("0")) + menge

        nach_ware = verteilt_ueber_rezepturen(je_rezepturartikel)
        if nach_ware:
            # The shares are in the product's unit. The house article brings
            # them into its house's, because that is where the result stands.
            aufloesung = Hausartikel.objects.filter(
                haus=self.haus, status__in=GEFUEHRT, ware_id__in=nach_ware
            ).select_related("basiseinheit", "ware__basiseinheit")
            if nur is not None:
                aufloesung = aufloesung.filter(pk=nur)
            for artikel in aufloesung:
                gezaehlt[artikel.pk] = gezaehlt.get(artikel.pk, Decimal("0")) + (
                    artikel.in_hauseinheit(nach_ware[artikel.ware_id])
                )
        return gezaehlt


def arten_des_laufs(inventuren) -> str:
    """The types of these inventories as words, each once, in their order.

    A run counts the same in every house as long as nobody changed the types
    of one house in preparation. Then the run names all of them. Reads
    'inventurarten.all()', so a prefetch saves the query per house.
    """
    arten = {art.pk: art for inventur in inventuren for art in inventur.inventurarten.all()}
    return " + ".join(
        art.name for art in sorted(arten.values(), key=lambda art: (art.reihenfolge, art.name))
    )


class InventurInventurart(models.Model):
    """That an inventory counts this type: one of its tabs.

    Its own model instead of the table Django builds for 'inventurarten':
    only so can the type be protected ('PROTECT'). Its name is part of the
    inventory's, and a deleted type would silently take a tab out of every
    inventory that counted it, also out of the closed ones.
    """

    inventur = models.ForeignKey(Inventur, on_delete=models.CASCADE, verbose_name=_("Inventur"))
    inventurart = models.ForeignKey(
        Inventurart, on_delete=models.PROTECT, verbose_name=_("Inventurart")
    )

    # Created by Django at runtime. Only named here so pyright knows them.
    inventur_id: int
    inventurart_id: int

    class Meta:
        db_table = "inventur_inventurart"
        verbose_name = _("Inventurart der Inventur")
        verbose_name_plural = _("Inventurarten der Inventur")
        constraints = [
            models.UniqueConstraint(
                fields=["inventur", "inventurart"], name="eine_inventurart_je_inventur_einmal"
            )
        ]

    def __str__(self) -> str:
        return f"{self.inventur_id}: {self.inventurart}"


def _zu_gross(*werte: Decimal | None) -> Decimal | None:
    """The first number the result does not hold: twelve digits, three of them after the point."""
    return next((wert for wert in werte if wert is not None and abs(wert) >= HOECHSTMENGE), None)


def _eingefroren(wert: Decimal | None) -> Decimal | None:
    """A quantity rounded to the three places of the result, half up.

    Rounded here and not by the database: SQLite keeps the number as REAL and
    rounds half to even on the way back, PostgreSQL's 'numeric(12, 3)' rounds
    half away from zero. The numbers come rounded to the places of the house's
    unit already ('_ergebnisse'), so this only catches a unit with more places
    than the result keeps. Until 10/2026 a weighing of 1234.5 g froze as
    1.234 kg locally and as 1.235 kg in production.
    """
    return None if wert is None else wert.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
