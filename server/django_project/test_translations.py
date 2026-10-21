"""The two own catalogs under 'server/locale/'.

German is the source language. The German catalog therefore only fills what
Django itself has not translated yet: Django 6.1 is new, and its German
catalog lags behind. 'LOCALE_PATHS' is read before the packages' catalogs,
so what stands here applies.

The English catalog, by contrast, carries the whole interface. Its msgid is
in each case the German sentence from code or template.

The tests that send every line through 'gettext' are the more important
ones: they catch someone adding to the '.po' and forgetting to translate.
The new line would then be in the file and not in the interface.
"""

import contextlib
import functools
import importlib.util
import json
import re
import shutil
import tempfile
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import translation
from django.utils.translation import gettext, ngettext, npgettext, pgettext

from .test_admin_site import benutzer

KATALOG = settings.BASE_DIR / "locale"


@functools.cache
def djangos_katalog() -> set[tuple[str, str]]:
    """What Django translates itself, from the mirror script under 'bin/'.

    Once per run: the script searches every '.mo' of Django for it, and the
    tests ask per entry of the mirror. Uncached, that cost just under 40
    seconds until 09/2026, half the suite.
    """
    pfad = settings.BASE_DIR / "bin" / "mirror_source_catalog.py"
    spec = importlib.util.spec_from_file_location("mirror_source_catalog", pfad)
    assert spec is not None and spec.loader is not None
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul.djangos_katalog()


def uebersetzt(kontext: str, quelle: str) -> str:
    """As the interface itself looks it up, with context or without."""
    return pgettext(kontext, quelle) if kontext else gettext(quelle)


#: Below it the mirror from 'bin/mirror_source_catalog.py' begins in the German catalog.
MARKE = "# --- From here on the mirror of the source language ---"


def eintraege(
    name: str, sprache: str = "de", nur_spiegel: bool = False
) -> dict[tuple[str, str], str]:
    """(context, msgid) to msgstr, without header and without plural forms.

    'nur_spiegel' reads only what the mirror script wrote. Above it stands what
    Django leaves open or should say differently, and that may overlap with
    Django's catalog.

    The context is the line's 'msgctxt', otherwise empty. It distinguishes two
    identical German words that diverge in English.
    """
    text = (KATALOG / sprache / "LC_MESSAGES" / name).read_text(encoding="utf-8")
    if nur_spiegel:
        text = text.split(MARKE)[1]
    muster = r'\n(?:msgctxt ((?:"[^"]*"\n)+))?msgid ((?:"[^"]*"\n)+)msgstr ((?:"[^"]*"\n)+)'
    gefunden = {}
    for roh_kontext, roh_id, roh_str in re.findall(muster, text):

        def entpackt(roh: str) -> str:
            return "".join(re.findall(r'"([^"]*)"', roh)).replace('\\"', '"').replace("\\n", "\n")

        if schluessel := entpackt(roh_id):
            gefunden[entpackt(roh_kontext), schluessel] = entpackt(roh_str)
    return gefunden


def mehrzahl(name: str, sprache: str = "de") -> dict[tuple[str, str, str], tuple[str, str]]:
    """(context, msgid, msgid_plural) to (singular, plural), the entries 'eintraege' leaves out.

    Both catalogs count two forms ('nplurals=2'). Until 10/2026 no test saw
    these entries, and the mirror skipped them.
    """
    text = (KATALOG / sprache / "LC_MESSAGES" / name).read_text(encoding="utf-8")
    muster = (
        r'\n(?:msgctxt ((?:"[^"]*"\n)+))?msgid ((?:"[^"]*"\n)+)msgid_plural ((?:"[^"]*"\n)+)'
        r'msgstr\[0\] ((?:"[^"]*"\n)+)msgstr\[1\] ((?:"[^"]*"\n)+)'
    )
    gefunden = {}
    for roh_kontext, roh_id, roh_plural, roh_eins, roh_viele in re.findall(muster, text):

        def entpackt(roh: str) -> str:
            return "".join(re.findall(r'"([^"]*)"', roh)).replace('\\"', '"').replace("\\n", "\n")

        gefunden[entpackt(roh_kontext), entpackt(roh_id), entpackt(roh_plural)] = (
            entpackt(roh_eins),
            entpackt(roh_viele),
        )
    return gefunden


def uebersetzt_mehrzahl(kontext: str, eins: str, viele: str, anzahl: int) -> str:
    return npgettext(kontext, eins, viele, anzahl) if kontext else ngettext(eins, viele, anzahl)


class EigeneUebersetzungen(TestCase):
    def test_die_verwaltung_spricht_deutsch(self):
        """The places where English used to stand."""
        with translation.override("de"):
            self.assertEqual(gettext("Run"), "Ausführen")
            self.assertEqual(gettext("Reset password"), "Passwort zurücksetzen")
            self.assertEqual(gettext("Filter by %(field_name)s"), "Nach %(field_name)s filtern")

    def test_jede_zeile_der_datei_kommt_auch_an(self):
        """Otherwise the '.po' was extended and the translating forgotten."""
        with translation.override("de"):
            for (kontext, quelle), ziel in eintraege("django.po").items():
                with self.subTest(quelle[:60]):
                    self.assertEqual(uebersetzt(kontext, quelle), ziel)

    def test_jede_mehrzahl_kommt_auch_an(self):
        """Both forms, through 'ngettext' as the code asks for them."""
        gefunden = mehrzahl("django.po")
        self.assertGreater(len(gefunden), 0)
        with translation.override("de"):
            for (kontext, eins, viele), (ziel_eins, ziel_viele) in gefunden.items():
                with self.subTest(eins[:60]):
                    self.assertEqual(uebersetzt_mehrzahl(kontext, eins, viele, 1), ziel_eins)
                    self.assertEqual(uebersetzt_mehrzahl(kontext, eins, viele, 2), ziel_viele)

    def test_das_auswahlfeld_bekommt_seinen_katalog(self):
        """The widget's texts are in the JavaScript, not in the template.

        The admin fetches them through 'jsi18n'. That our lines land there is not
        a given: the view queries the packages' catalogs and takes 'LOCALE_PATHS'
        only in addition.
        """
        self.client.force_login(benutzer(is_staff=True, is_superuser=True))
        antwort = self.client.get(reverse("admin:jsi18n")).content.decode()
        for (_kontext, quelle), ziel in eintraege("djangojs.po").items():
            with self.subTest(quelle[:60]):
                # The catalog is JSON in the response, umlauts therefore as
                # '\uXXXX'. The comparison is thus made in the same notation.
                self.assertIn(json.dumps(ziel)[1:-1], antwort)

    def test_die_verwaltung_duzt(self):
        """Django's German catalog uses the formal "Sie". The application uses "du" (2026-09-23)."""
        with translation.override("de"):
            for quelle in (
                "Your password was changed.",
                "Please go to the following page and choose a new password:",
                "Hold down “Control”, or “Command” on a Mac, to select more than one.",
                "Please either submit a file or check the clear checkbox, not both.",
            ):
                with self.subTest(quelle):
                    text = gettext(quelle)
                    self.assertNotEqual(text, quelle)
                    self.assertNotRegex(text, r"\b(Sie|Ihr|Ihre|Ihnen)\b")

    def test_dasselbe_passwort_in_einem_wort(self):
        """Django's German catalog writes "das selbe" (on every password form until 10/2026)."""
        # Through a name: a literal would land in the catalog by 'makemessages'.
        quelle = "Enter the same password as before, for verification."
        with translation.override("de"):
            text = gettext(quelle)
        self.assertEqual(text, "Bitte dasselbe Passwort zur Bestätigung erneut eingeben.")

    def test_der_ordner_liegt_im_suchpfad(self):
        self.assertIn(Path(settings.BASE_DIR) / "locale", list(settings.LOCALE_PATHS))

    def test_die_benutzerseite_spricht_deutsch(self):
        konto = benutzer(is_staff=True, is_superuser=True)
        self.client.force_login(konto)
        antwort = self.client.get(reverse("user_edit", args=[konto.pk]))
        self.assertContains(antwort, "Vorname")
        self.assertNotContains(antwort, "First name")


class DeutschAlsAusgangssprache(SimpleTestCase):
    """German stays German, even when the server is set to English.

    Django sets the catalog of 'LANGUAGE_CODE' as the fallback for every other
    language ('DjangoTranslation._add_fallback'). If it is set to English, a
    German sentence missing from the German catalog would come out in English.
    That is why every entry stands there as its own translation
    ('bin/mirror_source_catalog.py').
    """

    def test_jeder_eintrag_steht_auch_im_deutschen_katalog(self):
        """Except those Django translates itself: those precisely must not stand here."""
        deutsch = eintraege("django.po")
        fehlend = [
            quelle
            for quelle in eintraege("django.po", "en")
            if quelle not in deutsch and quelle not in djangos_katalog()
        ]
        self.assertEqual(
            fehlend, [], "bin/mirror_source_catalog.py laufen lassen, dann compilemessages"
        )

    def test_jede_mehrzahl_steht_auch_im_deutschen_katalog(self):
        """The mirror writes the plural entries too, each form as its own translation."""
        deutsch = mehrzahl("django.po")
        englisch = mehrzahl("django.po", "en")
        self.assertGreater(len(englisch), 0)
        fehlend = [eins for eins in englisch if eins not in deutsch]
        self.assertEqual(fehlend, [], "bin/mirror_source_catalog.py laufen lassen")
        # Only the mirrored ones: above the mark stand real translations of
        # what Django leaves open ("entry" to "Eintrag").
        for schluessel in englisch:
            with self.subTest(schluessel[1][:60]):
                self.assertEqual(deutsch[schluessel], schluessel[1:])

    def test_der_spiegel_ueberdeckt_djangos_uebersetzungen_nicht(self):
        """The account inherits "email address" from Django. Mirrored, German would show English.

        That is how "First name" stood on the German user page until 09/2026: our
        own catalog wins against Django's, and the mirror carried the English
        msgid as its own translation.
        """
        doppelt = [
            quelle
            for quelle in eintraege("django.po", nur_spiegel=True)
            if quelle in djangos_katalog()
        ]
        self.assertEqual(doppelt, [], "Diese Einträge übersetzt Django schon.")
        with translation.override("de"):
            self.assertEqual(gettext("email address"), "E-Mail-Adresse")
            self.assertEqual(gettext("first name"), "Vorname")
            self.assertEqual(gettext("last name"), "Nachname")

    # 'LOCALE_PATHS' is set to its own value here. It changes nothing, but makes
    # Django forget the loaded catalogs: they hang on the fallback, and that
    # comes from 'LANGUAGE_CODE'. Without this the state from an earlier test
    # would still apply.
    @override_settings(LANGUAGE_CODE="en", LOCALE_PATHS=settings.LOCALE_PATHS)
    def test_deutsch_bleibt_deutsch_auf_einem_englischen_server(self):
        """The case that went wrong without the mirror."""
        with translation.override("de"):
            self.assertEqual(gettext("Monatsinventur"), "Monatsinventur")
            self.assertEqual(gettext("Häuser"), "Häuser")


class ModellfelderTragenBeschriftungen(SimpleTestCase):
    """Every field of our own apps has a translatable 'verbose_name'.

    Without one Django derives the label from the field name, and that then
    stands in German in every language: in the forms of the website
    ("Beschreibung", "Menge in basiseinheit") and everywhere in the
    administration. That was 97 fields until 09/2026.
    """

    EIGENE = ("accounts", "organisation", "katalog", "inventur", "demo")

    def test_jedes_feld_hat_eine_uebersetzbare_beschriftung(self):
        from django.apps import apps  # noqa: PLC0415
        from django.utils.functional import Promise  # noqa: PLC0415

        ohne = []
        for modell in apps.get_models():
            if modell._meta.app_label not in self.EIGENE:
                continue
            for feld in [*modell._meta.fields, *modell._meta.many_to_many]:
                if feld.auto_created:
                    continue
                if not isinstance(feld.verbose_name, Promise):
                    ohne.append(f"{modell._meta.label}.{feld.name}")
        self.assertEqual(ohne, [], "verbose_name=_() fehlt")


class VorlagenOhneFestenText(SimpleTestCase):
    """Text in a template goes through 'translate' or 'blocktranslate'.

    What stands next to it ("seit", "bearbeiten", a 'data-rueckfrage') the
    person sees in German in every language. The check found forty such places
    in 09/2026, among them the confirmations of the status buttons and every
    page title when editing.
    """

    #: Attributes whose value the person reads.
    ATTRIBUTE = re.compile(
        r'\b(?:title|placeholder|aria-label|alt|data-rueckfrage|data-einmal)="([^"]*)"'
    )
    #: A word with three letters. A character like "×" or "·" is none.
    WORT = re.compile(r"[A-Za-zÄÖÜäöüß]{3,}")

    def prosa(self, vorlage: Path) -> list[str]:
        text = vorlage.read_text(encoding="utf-8")
        text = re.sub(r"\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}", " ", text, flags=re.S)
        text = re.sub(r"\{#.*?#\}", " ", text, flags=re.S)
        text = re.sub(
            r"\{%\s*blocktranslate\b.*?\{%\s*endblocktranslate\s*%\}", " ", text, flags=re.S
        )
        text = re.sub(r"<(script|style)\b.*?</\1>", " ", text, flags=re.S | re.I)
        text = re.sub(r"\{%\s*translate\b.*?%\}", " ", text)
        # The width of a form page is a class in a block of its own
        # ('components/form_page.html', block 'breite'), not a word.
        text = re.sub(r"\{%\s*block\s+breite\s*%\}.*?\{%\s*endblock\b.*?%\}", " ", text, flags=re.S)
        text = re.sub(r"&\w+;", " ", text)
        gefunden = []
        for attribut in self.ATTRIBUTE.findall(text):
            wert = re.sub(r"\{%.*?%\}|\{\{.*?\}\}", "", attribut)
            if self.WORT.search(wert):
                gefunden.append(attribut)
        text = re.sub(r"\{%.*?%\}|\{\{.*?\}\}", " ", text, flags=re.S)
        text = re.sub(r"<[^>]*>", " ", text)
        for zeile in text.split("\n"):
            if self.WORT.search(zeile):
                gefunden.append(zeile.strip())
        return gefunden

    def test_kein_text_steht_neben_der_uebersetzung(self):
        from .test_csp import eigene_vorlagen  # noqa: PLC0415

        # The help exists per language as a whole page and does not go through gettext.
        # The application name and the version are not sentences.
        gefunden = {
            str(vorlage.relative_to(settings.BASE_DIR)): funde
            for vorlage in eigene_vorlagen()
            if "help" not in vorlage.parts and (funde := self.prosa(vorlage))
        }
        self.assertEqual(gefunden, {})


class SkripteOhneEigeneTexte(SimpleTestCase):
    """What is in 'static/js/' bypasses gettext.

    The website's scripts therefore get their visible words as a 'data'
    attribute on their own <script> tag, where '{% translate %}' applies
    ('components/script.html' and the '*_script.html' next to it). A German
    sentence in the script itself would reappear on an English interface.

    A visible text is recognised by a space and a capital first letter, or by
    an umlaut. A selector ("input, select"), a class list ("alert alert-warning
    py-2") and a technical identifier without spaces ("DOMContentLoaded",
    "Content-Type") thus drop out, a sentence does not. Also exempt are
    comments and 'throw new Error(…)': that lands in the console, not in the
    interface.
    """

    #: A sentence for people: a space and a capitalised word, or an umlaut.
    PROSA = re.compile(r"(?=.*\s)(?=.*[A-ZÄÖÜ][a-zäöüß]{2})|[äöüÄÖÜß„“]")

    #: Anything shorter is an abbreviation, not a sentence.
    MINDESTLAENGE = 4

    def texte(self, quelle: str):
        """The strings of a file, without comments and without templates."""
        ohne_block = re.sub(r"/\*.*?\*/", "", quelle, flags=re.S)
        for zeile in quelle.splitlines():
            if re.match(r"\s*//", zeile) or zeile not in ohne_block:
                continue
            if "throw new Error(" in zeile:
                continue
            for doppelt, schraeg in re.findall(r'"([^"]*)"|`([^`]*)`', zeile):
                # A '${…}' is inserted content. What stands around it counts.
                yield re.sub(r"\$\{[^}]*\}", "", doppelt or schraeg).strip()

    def test_kein_sichtbarer_text_steht_im_skript(self):
        gefunden = [
            f"{pfad.name}: {text}"
            for pfad in sorted((settings.BASE_DIR / "static" / "js").glob("*.js"))
            for text in self.texte(pfad.read_text(encoding="utf-8"))
            if len(text) >= self.MINDESTLAENGE and self.PROSA.search(text)
        ]
        self.assertEqual(
            gefunden,
            [],
            "Diese Texte gehören als 'data'-Attribut an das <script>-Tag, "
            "damit sie übersetzt werden.",
        )

    def test_der_test_erkennt_einen_rueckfall(self):
        """Otherwise it would go unnoticed if the expression no longer caught anything."""
        self.assertEqual(list(self.texte('  melden("Notizen gesichert");')), ["Notizen gesichert"])
        self.assertTrue(self.PROSA.search("Notizen gesichert"))
        self.assertFalse(self.PROSA.search("input, select, textarea"))
        self.assertFalse(self.PROSA.search("DOMContentLoaded"))


class SaetzeHaengenNichtZusammen(SimpleTestCase):
    """A translation must not carry the rest of an earlier version glued to its end.

    Four English messages did until 10/2026 ("… stays empty.dimensions belong
    here."): a sentence that jumps to another topic without a space. Found by
    a full stop followed directly by a letter, except in file names and "e.g.".
    """

    #: What legitimately follows a full stop without a space.
    ERLAUBT = re.compile(r"(?<=\be)\.g\.|\.(?:md|env|py|js|css|html|org|com|sqlite3)\b")

    def test_kein_satzrest_haengt_am_ende(self):
        for sprache in ("de", "en"):
            text = (KATALOG / sprache / "LC_MESSAGES" / "django.po").read_text(encoding="utf-8")
            for roh in re.findall(r'msgstr(?:\[\d\])? ((?:"[^"\n]*"\n)+)', text):
                wert = "".join(re.findall(r'"([^"]*)"', roh))
                bereinigt = self.ERLAUBT.sub(" ", wert)
                with self.subTest(sprache=sprache, text=wert[:60]):
                    self.assertNotRegex(bereinigt, r"[.!?][A-Za-z]")


class EnglischeOberflaeche(TestCase):
    """The English catalog carries everything the interface says."""

    def test_jede_zeile_der_datei_kommt_auch_an(self):
        """Otherwise the '.po' was extended and the translating forgotten."""
        with translation.override("en"):
            for (kontext, quelle), ziel in eintraege("django.po", "en").items():
                with self.subTest(quelle[:60]):
                    self.assertEqual(uebersetzt(kontext, quelle), ziel)

    def test_kein_eintrag_bleibt_leer(self):
        """An empty msgstr falls back to German and would go unnoticed."""
        offen = [
            quelle
            for (_kontext, quelle), ziel in eintraege("django.po", "en").items()
            if not ziel.strip()
        ]
        self.assertEqual(offen, [], f"{len(offen)} Einträge ohne Übersetzung")

    def test_eine_seite_spricht_englisch(self):
        """The end-to-end check: template, model and message in one response."""
        konto = benutzer(is_staff=True, is_superuser=True)
        konto.sprache = "en"
        konto.save(update_fields=["sprache"])
        self.client.force_login(konto)
        antwort = self.client.get(reverse("haus_list"))
        self.assertEqual(antwort["Content-Language"], "en")
        self.assertContains(antwort, "New site")
        self.assertNotContains(antwort, "Neues Haus")

    def test_ein_skript_bekommt_seinen_text_uebersetzt(self):
        """The end-to-end check for 'components/*_script.html': the attribute on the tag."""
        konto = benutzer(is_staff=True, is_superuser=True)
        konto.sprache = "en"
        konto.save(update_fields=["sprache"])
        self.client.force_login(konto)
        # 'select_script.html' hangs in the skeleton, so it is on every page.
        antwort = self.client.get(reverse("haus_list"))
        self.assertContains(antwort, 'data-wert="Type to search …"')


class DuKleinGeschrieben(SimpleTestCase):
    """The application says "du", in lower case. Eight texts wrote "Du" until 10/2026.

    Checked are the German source texts of the catalog, the privacy text and
    the shipped mail texts.
    A capital is fine where a sentence, a heading or a list item begins.
    """

    WORT = re.compile(r"\b(Du|Dich|Dir|Dein(?:e|en|em|er|es)?)\b")
    DAVOR = re.compile(r"(?:^|[.!?:]\s+|\*\*|^- |^#+ )$")

    def grosse(self, text: str) -> list[str]:
        treffer = []
        for zeile in text.split("\n"):
            for m in self.WORT.finditer(zeile):
                if not self.DAVOR.search(zeile[: m.start()]):
                    treffer.append(zeile.strip()[:70])
        return treffer

    def test_im_katalog(self):
        text = (KATALOG / "en" / "LC_MESSAGES" / "django.po").read_text(encoding="utf-8")
        quellen = re.findall(r'^msgid ((?:"[^"\n]*"\n)+)', text, re.M)
        saetze = ["".join(re.findall(r'"([^"]*)"', roh)) for roh in quellen]
        self.assertEqual([satz for satz in saetze if self.grosse(satz)], [])

    def test_im_datenschutztext(self):
        text = (settings.BASE_DIR / "templates" / "privacy" / "de.txt").read_text(encoding="utf-8")
        self.assertEqual(self.grosse(text), [])

    def test_in_den_mailtexten(self):
        """The shipped mails ('organisation/mail_texts.py'), one file per mail."""
        ordner = settings.BASE_DIR / "templates" / "mails" / "de"
        dateien = sorted(ordner.glob("*.txt"))
        self.assertTrue(dateien)
        for datei in dateien:
            with self.subTest(datei=datei.name):
                self.assertEqual(self.grosse(datei.read_text(encoding="utf-8")), [])

    def test_der_test_erkennt_den_fall(self):
        self.assertEqual(self.grosse("Hallo, Dein Konto ist da."), ["Hallo, Dein Konto ist da."])
        self.assertEqual(self.grosse("Du kannst gehen. Dein Konto bleibt."), [])
        self.assertEqual(self.grosse("- **Dein Konto:** alles"), [])


class KatalogVollstaendig(SimpleTestCase):
    """Every sentence in code and templates stands in the catalog, and nothing else does.

    'makemessages' into a folder of its own, then its ids against the committed
    catalog. Until 10/2026 nothing checked that: a changed German sentence ran
    through in German on an English account until someone remembered the
    command, and two help texts did exactly that on 2026-10-13.
    """

    @skipUnless(shutil.which("xgettext"), "gettext is not installed")
    def test_makemessages_aendert_nichts(self):
        # In a copy without the 'locale' folder: with it present, 'makemessages'
        # attributes every file to it and writes into the committed catalog.
        with tempfile.TemporaryDirectory() as ordner:
            kopie = Path(ordner) / "server"
            shutil.copytree(
                settings.BASE_DIR,
                kopie,
                ignore=shutil.ignore_patterns(
                    ".venv", "pwa", "data", "staticfiles", "locale", "__pycache__", "*.sqlite3"
                ),
            )
            ausgabe = Path(ordner) / "locale"
            with override_settings(LOCALE_PATHS=[ausgabe]), contextlib.chdir(kopie):
                call_command("makemessages", locale=["en"], verbosity=0)
            text = (ausgabe / "en" / "LC_MESSAGES" / "django.po").read_text(encoding="utf-8")
        im_code = set(re.findall(r'\nmsgid ((?:"[^"]*"\n)+)', text))
        im_katalog = set(
            re.findall(
                r'\nmsgid ((?:"[^"]*"\n)+)',
                (KATALOG / "en" / "LC_MESSAGES" / "django.po").read_text(encoding="utf-8"),
            )
        )
        self.assertEqual(im_code - im_katalog, set(), "im Code, aber nicht im Katalog")
        self.assertEqual(im_katalog - im_code, set(), "im Katalog, aber nirgends im Code")
