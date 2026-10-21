"""The Markdown files render where the project lead reads them, and that renderer glues.

A line that begins with bold text or a code span, after a line that ends in a
word, loses the space between the two ("mit**mehreren Häusern**"). Until
10/2026, 32 lines did. Checked are the files that outsiders read and the one
README among the templates.
"""

from django.conf import settings
from django.test import SimpleTestCase

WURZEL = settings.BASE_DIR.parent

#: What is checked: the public documents at the root and the help README.
DATEIEN = (
    "README.md",
    "README.en.md",
    "DEPLOYMENT.md",
    "DEPLOYMENT.en.md",
    "DEMO.md",
    "DEMO.en.md",
    "THIRD_PARTY_NOTICES.md",
    "THIRD_PARTY_NOTICES.en.md",
    "server/templates/help/README.md",
)


def geklebte_zeilen(text: str) -> list[int]:
    """Line numbers that start with '**' or '`' right after a line ending in a letter or digit."""
    treffer = []
    im_code = False
    vorige = ""
    for nummer, zeile in enumerate(text.split("\n"), 1):
        if zeile.startswith("```"):
            im_code = not im_code
        elif not im_code and zeile.startswith(("**", "`")) and vorige.rstrip()[-1:].isalnum():
            treffer.append(nummer)
        vorige = zeile
    return treffer


class MarkdownUmbrueche(SimpleTestCase):
    def test_keine_zeile_beginnt_mit_einer_auszeichnung_nach_einem_wort(self):
        for name in DATEIEN:
            datei = WURZEL / name
            if not datei.exists():
                # In the image the repository root is not there.
                self.skipTest(f"{name} liegt hier nicht")
            with self.subTest(datei=name):
                self.assertEqual(geklebte_zeilen(datei.read_text(encoding="utf-8")), [])

    def test_der_test_erkennt_den_fall(self):
        self.assertEqual(geklebte_zeilen("Gedacht für Unternehmen mit\n**mehreren** Häusern"), [2])
        self.assertEqual(geklebte_zeilen("Ein Satz.\n**Fett** am Anfang"), [])
        self.assertEqual(geklebte_zeilen("```\ncode mit\n`span`\n```"), [])


def entschluesselnde_befehle(text: str) -> list[str]:
    """The shell commands in the code blocks that run 'backup decrypt', continued lines joined."""
    befehle = []
    im_code = False
    befehl = ""
    for zeile in text.split("\n"):
        if zeile.startswith("```"):
            im_code = not im_code
            continue
        if not im_code:
            continue
        befehl += zeile.strip().removesuffix("\\") + " "
        if not zeile.rstrip().endswith(("\\", "|")):
            if "backup decrypt" in befehl:
                befehle.append(" ".join(befehl.split()))
            befehl = ""
    return befehle


class KeinKlartextImLog(SimpleTestCase):
    """What a container started with 'docker compose run' prints, Docker writes into its log.

    The instructions decrypted the dump there and piped it on to 'psql', and
    the readable dump stood in the log until the container was removed (until
    10/2026). Now they decrypt with 'docker exec', whose output reaches only
    the pipe, or throw the result away inside the container.
    """

    def test_kein_entschluesseltes_abbild_aus_einem_run_container(self):
        for name in ("DEPLOYMENT.md", "DEPLOYMENT.en.md"):
            datei = WURZEL / name
            if not datei.exists():
                self.skipTest(f"{name} liegt hier nicht")
            befehle = entschluesselnde_befehle(datei.read_text(encoding="utf-8"))
            with self.subTest(datei=name):
                self.assertEqual(len(befehle), 3)
                for befehl in befehle:
                    self.assertTrue(
                        befehl.startswith("docker exec ") or "decrypt > /dev/null" in befehl,
                        befehl,
                    )

    def test_nach_dem_abbild_mit_psql_ist_jeder_abgemeldet(self):
        """Only there: before a new major version the dump was taken just before."""
        for name in ("DEPLOYMENT.md", "DEPLOYMENT.en.md"):
            datei = WURZEL / name
            if not datei.exists():
                self.skipTest(f"{name} liegt hier nicht")
            with self.subTest(datei=name):
                text = datei.read_text(encoding="utf-8")
                self.assertEqual(text.count("manage.py sign_out_everyone"), 1)

    def test_der_test_erkennt_den_fall(self):
        alt = (
            "```bash\ndocker compose run --rm web manage.py backup decrypt \\\n"
            "    < abbild.sql.gz.enc |\n    docker compose exec -T db psql\n```"
        )
        self.assertEqual(
            entschluesselnde_befehle(alt),
            [
                "docker compose run --rm web manage.py backup decrypt < abbild.sql.gz.enc | "
                "docker compose exec -T db psql"
            ],
        )
