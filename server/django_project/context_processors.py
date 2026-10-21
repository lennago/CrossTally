"""What every page knows without its view having to pass it."""

from django.conf import settings

from accounts.models import Differenzfarben
from demo.state import ist_demobestand
from organisation.models import Erscheinungsbild

from .feedback import rueckmeldung_link
from .mail import mail_moeglich


def version(request) -> dict[str, str]:
    """The running version. 'base.html' shows it in the account menu."""
    return {"app_version": settings.APP_VERSION}


def sprachen(request) -> dict[str, list[tuple[str, str]]]:
    """The languages the bars switch between.

    From the settings and not from a list of our own: 'LANGUAGES' already says
    which ones there are, and two lists would drift apart.
    """
    return {"sprachen": list(settings.LANGUAGES)}


def farbwahl(request) -> dict[str, list[tuple[str, str]]]:
    """The choice for the colours of the difference, for the account menu.

    From the field's choices and not from a list of our own, for the same reason
    as with the languages: two lists would drift apart.
    """
    return {"farbwahl": [(wert, str(name)) for wert, name in Differenzfarben.choices]}


def demo(request) -> dict[str, bool]:
    """Whether the fictional stock is running here. The page then carries a banner.

    One query per page build, via the unique house name. It lives here and not
    in the views because the banner belongs on every page, including the login
    and the administration.
    """
    return {"ist_demo": ist_demobestand()}


def mail(request) -> dict[str, object]:
    """Whether mails reach anyone, and how long an invitation lasts.

    The login drops "Forgot your password?", the account's page "Send the
    invitation again", the help its paragraphs about mails. The days come
    from the settings ('PASSWORD_RESET_TIMEOUT'), so the help says the same
    as the form next to it.
    """
    from accounts.invitation import gueltig_fuer_tage  # noqa: PLC0415  (avoids a circular import)

    return {"mail_moeglich": mail_moeglich(), "einladung_tage": gueltig_fuer_tage()}


def erscheinungsbild(request) -> dict[str, object]:
    """Name, own logo, operator and which footer links exist, from the active appearance.

    'app_name' is the name next to the logo. Header, browser tab and the
    administration show it, and it changes with the version like the logo.

    Without an own logo the bundled placeholder stays an SVG and thus sharp at
    every size. With an own logo the derived PNGs come under '/icons/'. One query
    per page build, the same row that also serves the icons and names the
    operator for the footer. That one lives in the database and not in the
    environment, because it describes the business using the application, not
    the server.

    'logo_stand' hangs on the address as a parameter. A new logo is thus a new
    address, and nobody sees the old one for a day.

    'rueckmeldung_link' is the mail behind "Report a problem or suggestion" in
    the account menu ('feedback.py'). Empty without an address or an account.
    """
    vorhanden = Erscheinungsbild.aktuell_fuer(request)
    return {
        "app_name": vorhanden.titel,
        "eigenes_logo": vorhanden.hat_logo,
        "logo_stand": int(vorhanden.geaendert_am.timestamp()) if vorhanden.hat_logo else 0,
        "betreiber": vorhanden.betreiber,
        "betreiber_kontakt": vorhanden.betreiber_kontakt,
        "datenschutz_kontakt": vorhanden.datenschutz_kontakt or vorhanden.betreiber_kontakt,
        "datenschutzseite": vorhanden.hat_datenschutzseite,
        "impressumseite": vorhanden.hat_impressum,
        "rueckmeldung_link": rueckmeldung_link(request, vorhanden.rueckmeldeadresse),
    }
