"""Checking and deriving the images of the appearance.

**One** PNG is uploaded. Everything else arises from it: the logo of the
bar, the favicon, the icon for iOS and the four sizes the manifest of the
counting app names. That way operation needs no Chromium, and nobody has to
produce six files in the right size themselves.

**PNG only.** An SVG can contain script, and served from the same origin it
would run in the browser of the signed-in. Even if only administrators may
upload, a hijacked session is exactly the case the Content-Security-Policy
stands against everywhere else. A PNG, by contrast, can be verified byte by
byte, and every derived icon is PNG anyway.
"""

from __future__ import annotations

import io

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from PIL import Image, UnidentifiedImageError

#: The edge length of the largest derived size. Below it upscaling would be
#: needed, and an icon on the home screen would look mushy.
MINDESTKANTE = 512
#: Above that it is no longer a logo but a photo. Also slows down deriving.
HOECHSTKANTE = 4096
#: A logo of this size is generously dimensioned. The limit mainly stands
#: against files that grow beyond measure when unpacked.
HOECHSTGROESSE = 2 * 1024 * 1024

#: What arises from the upload: name and edge length. The names are the same
#: as those of the bundled files, because without an upload those are
#: referenced.
GROESSEN = {
    "logo": 512,
    "icon-192": 192,
    "apple-touch-icon": 180,
    "favicon-32": 32,
}
#: The edge-filling sizes for the home screen of Android. The motif sits
#: shrunk in the centre, because the device cuts the edge away in its own
#: shape (circle, squircle, teardrop). Reliably visible is only the inner
#: circle with 80 % diameter.
MASKABLE = {"icon-maskable-512": 512, "icon-maskable-192": 192}
#: How much of the edge the motif occupies in the edge-filling icon.
MOTIVANTEIL = 0.8

#: White. Only the edge-filling icon needs a background at all.
STANDARDHINTERGRUND = "#ffffff"


def gepruefte_vorlage(daten: bytes) -> Image.Image:
    """Accepts the upload or says what is wrong with it.

    The image itself is checked, not the file name. A renamed file shows up
    here, because Pillow reads the content.
    """
    if not daten:
        raise ValidationError(_("Die Datei ist leer."))
    if len(daten) > HOECHSTGROESSE:
        raise ValidationError(
            _("Das Bild ist größer als %(grenze)s MB.")
            % {"grenze": HOECHSTGROESSE // (1024 * 1024)}
        )

    try:
        # 'verify' reads the file to the end and thus makes it unusable.
        # It is therefore opened twice.
        Image.open(io.BytesIO(daten)).verify()
        bild = Image.open(io.BytesIO(daten))
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as fehler:
        # The bomb: a valid header that announces a billion pixels. Pillow
        # refuses to decode it, and until 10/2026 that was a server error.
        raise ValidationError(_("Die Datei ist kein lesbares Bild.")) from fehler

    if bild.format != "PNG":
        raise ValidationError(
            _("Erwartet wird ein PNG. Diese Datei ist ein %(format)s.")
            % {"format": bild.format or _("unbekanntes Format")}
        )
    if getattr(bild, "n_frames", 1) > 1:
        raise ValidationError(_("Ein bewegtes PNG geht nicht. Erwartet wird ein einzelnes Bild."))

    breite, hoehe = bild.size
    if breite != hoehe:
        raise ValidationError(
            _("Das Bild muss quadratisch sein. Dieses ist %(breite)s x %(hoehe)s Pixel groß.")
            % {"breite": breite, "hoehe": hoehe}
        )
    if breite < MINDESTKANTE:
        raise ValidationError(
            _("Das Bild ist kleiner als %(kante)s x %(kante)s Pixel.") % {"kante": MINDESTKANTE}
        )
    if breite > HOECHSTKANTE:
        raise ValidationError(
            _("Das Bild ist größer als %(kante)s x %(kante)s Pixel.") % {"kante": HOECHSTKANTE}
        )
    return bild


def hintergrund_als_rgba(farbe: str) -> tuple[int, int, int, int]:
    """A colour like "#ff8800", as Pillow needs it."""
    wert = farbe.lstrip("#")
    return (int(wert[0:2], 16), int(wert[2:4], 16), int(wert[4:6], 16), 255)


def abgeleitet(daten: bytes, hintergrund: str) -> dict[str, bytes]:
    """All sizes from a checked upload, each as PNG.

    The background only affects the edge-filling icons. The others keep their
    transparency, because in the bar and in the browser tab the logo sits on
    the page's background, and that changes with the light and dark theme.
    """
    bild = gepruefte_vorlage(daten).convert("RGBA")
    fertig = {name: _skaliert(bild, kante) for name, kante in GROESSEN.items()}
    fertig |= {name: _randfuellend(bild, kante, hintergrund) for name, kante in MASKABLE.items()}
    return fertig


def _skaliert(bild: Image.Image, kante: int) -> bytes:
    return _als_png(bild.resize((kante, kante), Image.Resampling.LANCZOS))


def _randfuellend(bild: Image.Image, kante: int, hintergrund: str) -> bytes:
    """The motif shrunk on an opaque background, centred."""
    blatt = Image.new("RGBA", (kante, kante), hintergrund_als_rgba(hintergrund))
    motiv = round(kante * MOTIVANTEIL)
    verkleinert = bild.resize((motiv, motiv), Image.Resampling.LANCZOS)
    rand = (kante - motiv) // 2
    # The image itself as the mask: that way its own transparency is kept,
    # and the background shows through instead of turning black.
    blatt.paste(verkleinert, (rand, rand), verkleinert)
    return _als_png(blatt)


def _als_png(bild: Image.Image) -> bytes:
    puffer = io.BytesIO()
    bild.save(puffer, format="PNG", optimize=True)
    return puffer.getvalue()
