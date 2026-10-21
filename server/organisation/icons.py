"""The addresses of the icons: `/icons/<name>.png`.

They **always** answer. If an own logo is uploaded, its derived sizes come,
otherwise the bundled file is referenced. Only because of that can the
manifest of the counting app point to them, because that is created at
build time and does not know the later operation. Without this fixed
address an own logo would require an own fork and an own image.

Open without signing in, like the stylesheet and the rest of the extras: a
browser tab shows its icon on the login page too.
"""

from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect
from django.templatetags.static import static
from django.views.decorators.http import require_safe

from django_project.validation import ganzzahl

from .images import GROESSEN, MASKABLE
from .models import Erscheinungsbild

#: Where to refer as long as nobody has uploaded an own logo. The logo itself
#: then stays an SVG and thus sharp at every size. The four icons of the app
#: live with the build of the counting app and not under '/static/'.
PLATZHALTER = {
    "logo": lambda: static("logo.svg"),
    "favicon-32": lambda: static("favicon-32.png"),
    "apple-touch-icon": lambda: static("apple-touch-icon.png"),
    "icon-192": lambda: "/app/icons/icon-192.png",
    "icon-512": lambda: "/app/icons/icon-512.png",
    "icon-maskable-192": lambda: "/app/icons/icon-maskable-192.png",
    "icon-maskable-512": lambda: "/app/icons/icon-maskable-512.png",
}

#: The largest icon is the source image itself. Another field for it would
#: be the same sequence of numbers a second time in the database.
FELD_JE_NAME = {name: name for name in [*GROESSEN, *MASKABLE]} | {"icon-512": "logo"}

#: One day for an uploaded image. The pages attach the timestamp as a
#: parameter, so a new logo is a new address and visible at once.
EIN_TAG = 24 * 60 * 60
#: The reference to the placeholder, by contrast, holds only briefly. It carries
#: no timestamp, and cached for a day it would survive the first upload.
KURZ = 5 * 60


def _fassung(nummer: str | None) -> Erscheinungsbild | None:
    pk = ganzzahl(nummer)
    if pk is None:
        return None
    return Erscheinungsbild.objects.filter(pk=pk).first()


@require_safe
def icon(request: HttpRequest, name: str) -> HttpResponse:
    if name not in PLATZHALTER:
        raise Http404(name)

    # Without an entry the active version. The administration names the
    # version it is showing for its preview. A logo is nothing secret.
    erscheinungsbild = _fassung(request.GET.get("von")) or Erscheinungsbild.aktuell()
    if daten := erscheinungsbild.bild(FELD_JE_NAME[name]):
        antwort = HttpResponse(daten, content_type="image/png")
        antwort["Cache-Control"] = f"public, max-age={EIN_TAG}"
        # The timestamp is enough as an identifier: changes happen only through
        # the administration, and there the whole row is always rewritten.
        antwort["ETag"] = f'"{erscheinungsbild.geaendert_am.timestamp():.0f}"'
        return antwort

    verweis = HttpResponseRedirect(PLATZHALTER[name]())
    verweis["Cache-Control"] = f"public, max-age={KURZ}"
    return verweis
