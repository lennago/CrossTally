"""The command 'manage.py demo'.

The options have English names, as with every Django command. The output
speaks the demo's language: German without an entry, English with
'--language en', because whoever builds the English demo reads the
overview in English too.

**Only where nothing else stands, and only on purpose on a server.** The
accounts of the demo carry the password from DEMO.md, and anyone who read it
could sign in with them. So the command builds next to nothing that is not
the demo's ('builder.eigener_bestand'), and '--reset' clears away only a
demo. Without 'DEBUG' it asks for '--demo-server' as well: a server in
operation starts with an empty database, before its first account, and
nothing in the data tells it from a fresh one during development. Until
10/2026 the demo built in both, and '--reset' emptied a database in use.
"""

from datetime import date, datetime

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import formats, translation
from django.utils.translation import gettext as _

from demo import branding, builder, users
from demo.files import ordner
from demo.names_en import Namen


class Command(BaseCommand):
    help = (
        "Baut einen erfundenen Bestand auf, an dem sich die ganze Anwendung "
        "ausprobieren lässt. Einzelheiten stehen in DEMO.md."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "-r",
            "--reset",
            action="store_true",
            help=(
                "Löscht Häuser, Artikel, Inventuren und die Demo-Konten vorher. Nur in einer "
                "Datenbank, die schon die Demo trägt."
            ),
        )
        parser.add_argument(
            "-d",
            "--date",
            metavar="YYYY-MM-DD",
            help=(
                "Der Tag, von dem aus gerechnet wird. Ohne Angabe heute. Die "
                "Inventuren liegen davor, die laufende am Ende des Vormonats."
            ),
        )
        parser.add_argument(
            "--noinput",
            action="store_true",
            help="Fragt beim Zurücksetzen nicht nach.",
        )
        parser.add_argument(
            "--demo-server",
            action="store_true",
            help=(
                "Baut auch ohne DEBUG, auf einem Server, der nur die Demo zeigen soll. Ihre "
                "Konten haben das Passwort aus DEMO.md."
            ),
        )
        parser.add_argument(
            "-l",
            "--language",
            choices=[kuerzel for kuerzel, _name in settings.LANGUAGES],
            help=(
                "In welcher Sprache die erfundenen Namen und die Ausgabe entstehen. "
                "Ohne Angabe die des Servers (LANGUAGE_CODE). Häuser, Waren, Orte und "
                "die Importdateien ziehen mit, die Konten ebenso."
            ),
        )

    def handle(self, *args, **optionen) -> None:
        sprache = optionen.get("language") or settings.LANGUAGE_CODE
        with translation.override(sprache):
            heute = self._stichtag(optionen.get("date"))
            self._pruefen(optionen)
            if builder.bestand_vorhanden():
                self._bestaetigen(optionen["noinput"])
                builder.leeren()

            bericht = builder.aufbauen(heute, sprache)
            # The overview is the actual yield of the command. Only whoever
            # explicitly does not want it ('--verbosity 0') does not get it.
            if optionen["verbosity"]:
                self._ausgeben(bericht)

    @staticmethod
    def _stichtag(text: str | None) -> date | None:
        if not text:
            return None
        try:
            return datetime.strptime(text, "%Y-%m-%d").date()
        except ValueError as fehler:
            raise CommandError(
                _("„%(text)s“ ist kein Datum der Form YYYY-MM-DD.") % {"text": text}
            ) from fehler

    @staticmethod
    def _pruefen(optionen: dict) -> None:
        """Where the demo does not belong, it says so before it touches anything.

        Someone's own stock first: no switch makes that the demo's, and its
        message names what stands there. Then a server without 'DEBUG', where
        '--demo-server' has to say that it is meant to show the demo. Last a
        demo already built, which only '--reset' builds anew.
        """
        if builder.eigener_bestand():
            raise CommandError(
                _(
                    "In „%(datenbank)s“ steht ein eigener Bestand (%(was)s) und keine Demo. Die "
                    "Demo legt sich nicht daneben, denn ihre Konten haben das Passwort aus "
                    "DEMO.md. Auch --reset löscht nur eine Demo und keinen eigenen Bestand.\n"
                    "Für einen Blick neben der eigenen Arbeit eine eigene Datei nehmen:\n"
                    '  DATABASE_URL="sqlite:///demo.sqlite3" uv run manage.py migrate\n'
                    '  DATABASE_URL="sqlite:///demo.sqlite3" uv run manage.py demo'
                )
                % {"datenbank": builder.datenbank(), "was": builder.was_dasteht()}
            )
        if not settings.DEBUG and not optionen["demo_server"]:
            raise CommandError(
                _(
                    "DEBUG ist aus, so läuft ein Server im Betrieb. Die Demo legt Konten mit dem "
                    "Passwort aus DEMO.md an, und wer es kennt, meldet sich damit an.\n"
                    "Soll dieser Server nur die Demo zeigen, dann mit --demo-server. Zum "
                    "Ausprobieren auf dem eigenen Rechner gehört DEBUG=True in die server/.env."
                )
            )
        if builder.bestand_vorhanden() and not optionen["reset"]:
            raise CommandError(
                _(
                    "In „%(datenbank)s“ steht schon die Demo (%(was)s). Neu aufgebaut wird sie "
                    "mit --reset."
                )
                % {"datenbank": builder.datenbank(), "was": builder.was_dasteht()}
            )

    def _bestaetigen(self, ohne_nachfrage: bool) -> None:
        """Before deleting there is a question, and with numbers.

        Only a demo gets this far ('_pruefen'). What was tried out in it goes
        too, though: a house set up, a product merged, an uploaded logo.
        """
        if ohne_nachfrage:
            return
        self.stdout.write(
            self.style.WARNING(
                _(
                    "In „%(datenbank)s“ stehen %(was)s.\n"
                    "Alles davon wird gelöscht, dazu die Konten %(konten)s."
                )
                % {
                    "datenbank": builder.datenbank(),
                    "was": builder.was_dasteht(),
                    "konten": ", ".join(konto[0] for konto in users.KONTEN),
                }
            )
        )
        antworten = {"ja", "j", "yes", "y"}
        try:
            antwort = input(_("Wirklich löschen? [ja/nein] "))
        except EOFError:
            # No terminal, no answer: a cron job or a pipe. Until 10/2026 that was a traceback.
            antwort = ""
        if antwort.strip().lower() not in antworten:
            raise CommandError(_("Abgebrochen, es wurde nichts geändert."))

    def _ausgeben(self, bericht: builder.Aufbaubericht) -> None:
        schreiben = self.stdout.write
        schreiben(
            self.style.SUCCESS(
                _("Der Demo-Bestand steht (Sprache: %(sprache)s).") % {"sprache": bericht.sprache}
            )
        )
        schreiben("")
        schreiben(_("Häuser:        %(haeuser)s") % {"haeuser": ", ".join(bericht.haeuser)})
        schreiben(
            _(
                "Bestand:       %(waren)s Waren, %(hausartikel)s Artikel, "
                "%(rezepturartikel)s Rezepturartikel, %(erwartungen)s Zeilen in den Zähllisten"
            )
            % {
                "waren": bericht.waren,
                "hausartikel": bericht.hausartikel,
                "rezepturartikel": bericht.rezepturartikel,
                "erwartungen": bericht.erwartungen,
            }
        )
        schreiben("")
        schreiben(
            _("Konten (Passwort, außer für %(eingeladen)s: %(passwort)s)")
            % {"eingeladen": users.EINGELADEN, "passwort": users.PASSWORT}
        )
        namen = Namen(bericht.sprache)
        # As wide as the longest account name, plus a gap.
        kennungen = max(len(konto[0]) for konto in users.KONTEN) + 2
        for kennung, vorname, nachname, leitung, admin in users.KONTEN:
            if kennung == users.EINGELADEN:
                rolle = _("wartet auf die Einladung, ohne Passwort")
            elif admin:
                rolle = _("Administrator")
            elif leitung:
                rolle = _("Führungskraft")
            else:
                rolle = _("zählt nur")
            wer = f"{namen(vorname)} {namen(nachname)}"
            schreiben(f"  {kennung:{kennungen}}{wer:18}{rolle}")
        schreiben("")
        schreiben(_("Inventuren"))
        # As wide as the longest name: an annual inventory names all its types.
        breite = max((len(lauf.art) for lauf in bericht.laeufe), default=0) + 2
        for lauf in bericht.laeufe:
            tag = formats.date_format(lauf.stichtag, "SHORT_DATE_FORMAT")
            schreiben(f"  {tag:12}{lauf.art:{breite}}{lauf.status:18}{lauf.hinweis}")
        schreiben("")
        schreiben(_("Dateien zum Ausprobieren in %(ordner)s") % {"ordner": ordner(bericht.sprache)})
        for name, zweck in bericht.dateien.items():
            schreiben(f"  {name:28} {zweck}")
        schreiben("")
        schreiben(_("Ein Logo zum Hochladen in der Verwaltung: %(pfad)s") % {"pfad": branding.LOGO})
        schreiben("")
        schreiben(_("Was sich damit anschauen lässt, steht in DEMO.md."))
