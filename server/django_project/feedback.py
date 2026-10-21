"""The link behind "Report a problem or suggestion": a mail with what a report needs.

Whoever reports a problem rarely knows the version, and "it does not work"
says little without the page. So the mail comes prefilled with both, along
with the account, the time and the browser. The person sends it from their
own mail program and sees everything before. The application stores nothing,
which is why there is no form with a list of its own (decision of 2026-10-16).

The address comes from the appearance and starts empty ('Erscheinungsbild').
The counting app builds the same mail ('app/src/services/feedback.js'), with
the connection and the waiting entries on top.
"""

from urllib.parse import quote

from django.conf import settings
from django.http import HttpRequest
from django.utils import formats, timezone
from django.utils.translation import gettext as _

from organisation.models import STANDARDNAME

#: The start of every subject, in every language and whatever the application
#: is called in the appearance. The recipient's mail filter holds on to it.
BETREFF_MARKE = f"[{STANDARDNAME}]"


def _seite(request: HttpRequest) -> str:
    """The page, without a secret from its address.

    The confirmation link of a new e-mail address and the reset link carry a
    token that is still alive while the page is open. On those pages the mail
    names only the address's name. In the administration it names the page
    below its path and leaves out the path itself ('ADMIN_URL'): that one is
    meant to stay unknown, and a mail is easily forwarded (until 10/2026 it
    stood in every one). Elsewhere the query is left out too: a search term
    says nothing about the problem.
    """
    treffer = request.resolver_match
    if treffer is not None and {"token", "uidb64"} & set(treffer.kwargs):
        return treffer.view_name
    verwaltung = "/" + str(settings.ADMIN_URL)
    if request.path_info.startswith(verwaltung):
        return f"{_('Verwaltung')}: /{request.path_info.removeprefix(verwaltung)}"
    return request.build_absolute_uri(request.path)


def rueckmeldung_link(request: HttpRequest, adresse: str) -> str:
    """The 'mailto:' address for the account menu. Empty without an address or an account."""
    konto = getattr(request, "user", None)
    if not adresse or konto is None or not konto.is_authenticated:
        return ""
    angaben = (
        (_("Version"), settings.APP_VERSION),
        (_("Seite"), _seite(request)),
        (_("Konto"), konto.get_username()),
        (_("Zeit"), formats.date_format(timezone.localtime(), "SHORT_DATETIME_FORMAT")),
        (_("Browser"), request.headers.get("user-agent", "")),
    )
    zeilen = [
        _("Was ist passiert, oder was wünschst du dir?"),
        "",
        "",
        "",
        _("Technische Angaben, bitte stehen lassen:"),
        *(f"{name}: {wert}" for name, wert in angaben),
    ]
    betreff = f"{BETREFF_MARKE} {_('Rückmeldung von der Website')}"
    # RFC 6068: line breaks as CRLF, and in subject and body nothing left unencoded.
    text = "\r\n".join(zeilen)
    return (
        f"mailto:{quote(adresse, safe='@')}"
        f"?subject={quote(betreff, safe='')}&body={quote(text, safe='')}"
    )
