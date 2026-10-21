"""What the views of the API share: the body of a request and the point in time of a sync."""

from collections.abc import Mapping

from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import ValidationError


def eingabe(request) -> Mapping:
    """The body as a mapping, or an empty one for anything else.

    A JSON array, a string or 'null' is valid JSON, and DRF hands it over as
    it is. '.get' on it ended in an 'AttributeError' until 10/2026: a server
    error where the request deserves the endpoint's ordinary answer, at the
    passkey's sign-in even for anyone without an account ('api/passkey.py').
    """
    return request.data if isinstance(request.data, Mapping) else {}


def seit_wann(request):
    """The point in time from '?geaendert_seit=', or None.

    Unreadable input is rejected instead of silently skipped: a PWA that sends
    its timestamp wrongly would otherwise get too little data and not notice.
    """
    rohwert = request.query_params.get("geaendert_seit")
    if not rohwert:
        return None
    # An unencoded '+' of the time zone arrives as a space ('…841 00:00').
    # That is no wrong input but the usual stumbling block when assembling
    # the address, so turn it back instead of rejecting.
    try:
        zeitpunkt = parse_datetime(rohwert.replace(" ", "+"))
    except ValueError:
        # Well-formed, but no day of the calendar ('2026-02-30'). Until 10/2026
        # that was a server error, and the app took it for a passing fault.
        zeitpunkt = None
    if zeitpunkt is None:
        raise ValidationError(
            {
                "geaendert_seit": _(
                    "Erwartet wird ein Zeitpunkt nach ISO 8601, etwa „2026-01-31T18:30:00+01:00“."
                )
            }
        )
    # Without a zone it counts as the server's time. The app always sends one,
    # but a filter with a naive value made Django warn on every such request.
    if timezone.is_naive(zeitpunkt):
        zeitpunkt = timezone.make_aware(zeitpunkt)
    return zeitpunkt
