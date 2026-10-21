"""The answers to what goes wrong in the API, where DRF's own do not fit.

DRF's handler turns every exception into a response with 'detail' as text.
Two answers need more than text: the counting app decides by a boolean
whether its numbers wait or count as rejected ('NichtInDerZaehlung',
'Gesperrt').
"""

from rest_framework.response import Response
from rest_framework.views import exception_handler

from .checks import Gesperrt, NichtInDerZaehlung


def behandeln(exc, kontext):
    """DRF's handler, except for the answers that carry their own data."""
    if isinstance(exc, NichtInDerZaehlung | Gesperrt):
        return Response(exc.daten, status=exc.status_code)
    return exception_handler(exc, kontext)
