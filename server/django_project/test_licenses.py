"""The license texts of the libraries under 'static/vendor/'.

MIT and Apache-2.0 require that the license text is passed on with the code.
Whoever adds a library or rebuilds 'bin/vendor.sh' does not see that a text
is missing. The site runs anyway. That is why it is checked here. For the
counting app the build itself does that ('app/bin/licenses.mjs').
"""

from django.conf import settings
from django.test import SimpleTestCase

VENDOR = settings.BASE_DIR / "static" / "vendor"
UEBERSICHT = settings.BASE_DIR.parent / "THIRD_PARTY_NOTICES.md"


class FremdeBibliotheken(SimpleTestCase):
    def test_jede_bibliothek_hat_ihren_lizenztext(self):
        ordner = sorted(eintrag for eintrag in VENDOR.iterdir() if eintrag.is_dir())
        self.assertTrue(ordner, "Unter 'static/vendor/' liegt nichts. Falscher Pfad?")
        for bibliothek in ordner:
            with self.subTest(bibliothek=bibliothek.name):
                texte = list(bibliothek.glob("LICENSE*.txt"))
                self.assertTrue(texte, f"Kein LICENSE*.txt in {bibliothek}")
                for text in texte:
                    self.assertIn("Copyright", text.read_text(encoding="utf-8"), text.name)

    def test_popper_steckt_im_buendel_von_bootstrap(self):
        # 'bootstrap.bundle.min.js' contains Popper, but its header names only Bootstrap.
        self.assertTrue((VENDOR / "bootstrap" / "LICENSE-popper.txt").is_file())

    def test_die_uebersicht_nennt_jede_bibliothek(self):
        if not UEBERSICHT.exists():
            # In the image the repository root is not there.
            self.skipTest("THIRD_PARTY_NOTICES.md liegt hier nicht")
        uebersicht = UEBERSICHT.read_text(encoding="utf-8")
        for bibliothek in VENDOR.iterdir():
            if bibliothek.is_dir():
                with self.subTest(bibliothek=bibliothek.name):
                    self.assertIn(f"static/vendor/{bibliothek.name}/", uebersicht)
