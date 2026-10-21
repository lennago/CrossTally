"""Logo, name, legal notice and feedback address for trying out: what one enters oneself.

The demo shows the shipped logo, as a fresh operation sees it, and no legal
notice. Uploading in the administration is something one tries oneself, and
for that a fitting image lies ready here. It is the tally marks of the
placeholder in turquoise ('images/logo_demo.svg' next to it, rendered from
that). That way one sees at a glance after uploading that it worked. The
version gets an invented legal notice along, so the page and its link appear,
and an address for feedback, so the account menu offers "Report a problem or
suggestion".

'logo_setzen' goes **through the same form** an administrator fills in. The
tests upload the image that way. If something changes about check or
derivation that it no longer passes, the test breaks instead of the image
silently going stale.
"""

from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile

from organisation.forms import ErscheinungsbildForm
from organisation.models import STANDARD, Erscheinungsbild

#: The version that arises when trying out. The standard stays as it is.
FASSUNG = "Demo"

ORDNER = Path(__file__).resolve().parent / "images"
LOGO = ORDNER / "logo_demo.png"

#: The background of the icon on the home screen, matching the image. DEMO.md names it.
HINTERGRUND = "#0f766e"

#: The name next to the logo that the version gets. DEMO.md names it too.
ANWENDUNGSNAME = "Kino-Inventur"

#: The legal notice of the version, invented like everything here. One line
#: per item of the address, as an address is written.
IMPRESSUM = (
    "## Angaben zum Betrieb\n\n"
    "Beispielkino GmbH\nMusterweg 1\n10101 Musterstadt\n\n"
    "## Kontakt\n\n"
    "E-Mail: post@example.org"
)
IMPRESSUM_EN = (
    "## About the business\n\n"
    "Beispielkino GmbH\nMusterweg 1\n10101 Musterstadt\n\n"
    "## Contact\n\n"
    "E-mail: post@example.org"
)

#: Where "Report a problem or suggestion" writes to. Under a reserved domain,
#: so a mail sent while trying out reaches nobody. DEMO.md names it.
RUECKMELDEADRESSE = "rueckmeldung@example.org"


def logo_setzen() -> None:
    """Uploads the image, writes legal notice and address, as someone would in the administration.

    The path there: copy the standard, edit the copy, choose it. Exactly so
    here, through the same form.
    """
    fassung = Erscheinungsbild.objects.filter(name=FASSUNG).first()
    if fassung is None:
        fassung = Erscheinungsbild.standard().kopie(FASSUNG)
    formular = ErscheinungsbildForm(
        {
            "name": fassung.name,
            "anwendungsname": ANWENDUNGSNAME,
            "hintergrund": HINTERGRUND,
            # What the version otherwise carries stays: the form writes every field.
            "betreiber": fassung.betreiber,
            "betreiber_kontakt": fassung.betreiber_kontakt,
            "datenschutz_kontakt": fassung.datenschutz_kontakt,
            "rueckmeldeadresse": RUECKMELDEADRESSE,
            "datenschutztext": fassung.datenschutztext,
            "datenschutztext_en": fassung.datenschutztext_en,
            "impressum": IMPRESSUM,
            "impressum_en": IMPRESSUM_EN,
        },
        {"datei": SimpleUploadedFile(LOGO.name, LOGO.read_bytes(), "image/png")},
        instance=fassung,
    )
    if not formular.is_valid():
        raise ValidationError(formular.errors.as_text())
    formular.save().aktivieren()


def logo_entfernen() -> None:
    """On reset the own versions go too, the standard applies again.

    Otherwise the fresh demo would wear the face of the last try-out.
    """
    Erscheinungsbild.standard().aktivieren()
    for fassung in Erscheinungsbild.objects.filter(aktiv=False).exclude(name=STANDARD):
        fassung.delete()
