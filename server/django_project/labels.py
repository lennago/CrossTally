"""The type for texts that get translated.

'gettext_lazy' does not return a 'str' but a promise of one. It only becomes
a string when someone needs it, and then in the language that applies at that
moment. That is needed where a text is created at import time and displayed
only later: column headers, choices, messages on a class.

For pyright the two are different types. Where such a text flows on (into a
message, a group name, a column header), 'Beschriftung' therefore stands
instead of 'str'.

The two branches are needed: the name '_StrOrPromise' exists only in the
stubs, and that is exactly what Django's own signatures expect. At runtime the
class is simply called 'Promise'. It would also work through
'django_stubs_ext', but that package is in the 'dev' group and missing from
the image.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from django.utils.functional import _StrOrPromise

    #: A text, translated or not yet.
    type Beschriftung = _StrOrPromise
else:
    from django.utils.functional import Promise

    Beschriftung = str | Promise
