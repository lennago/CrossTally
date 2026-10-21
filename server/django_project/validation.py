"""Checks that more than one app needs.

The upper limit for uploads: the article import and the target stock both
need it. And a number from a form, a parameter or an address ('ganzzahl'),
which every app reads.
"""

from django.core.exceptions import ValidationError
from django.template.defaultfilters import filesizeformat
from django.utils.translation import gettext_lazy as _

#: Generously sized: an export with a few thousand articles stays well below
#: it. The limit is not meant to limit the work but to prevent an
#: accidentally wrong file from filling the memory, because pandas reads it
#: in one piece before anyone can check it.
MAX_UPLOAD_MB = 10


#: The longest number read here is an article number with 20 characters, a
#: number in the database has 19 digits at most.
HOECHSTENS_ZIFFERN = 20


def ganzzahl(text: object) -> int | None:
    """A number from a form, a parameter or an address, or None for anything else.

    Only the digits 0 to 9. 'str.isdigit' also says yes to "²", and 'int'
    then raises a 'ValueError'. Until 10/2026 that was a server error, at
    '/icons/logo.png?von=²' even without signing in. So it was above 4,300
    digits, Python's limit for reading a number, hence the length first.
    """
    wert = "" if text is None else str(text)
    if len(wert) > HOECHSTENS_ZIFFERN or not (wert.isascii() and wert.isdigit()):
        return None
    return int(wert)


class HoechstensGross:
    """Rejects files above the limit.

    As a class and not as a function, so the limit sits on the field and shows
    up in the error message: "at most 10.0 MB" helps, "the file is too large"
    does not.
    """

    def __init__(self, megabyte: int = MAX_UPLOAD_MB):
        self.grenze = megabyte * 1024 * 1024

    def __call__(self, datei) -> None:
        if datei.size > self.grenze:
            raise ValidationError(
                _("Diese Datei ist %(gross)s groß. Erlaubt sind höchstens %(grenze)s."),
                code="zu_gross",
                params={
                    "gross": filesizeformat(datei.size),
                    "grenze": filesizeformat(self.grenze),
                },
            )
