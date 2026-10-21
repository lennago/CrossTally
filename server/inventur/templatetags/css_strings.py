"""Template filters for text in a CSS string, where HTML's escaping does not apply.

The footer of the sheets is the "content" string of a page margin
('pdf/base.html'). There an entity stands as text, and Django's
autoescaping writes one for every '&' and quote: until 10/2026 the footer
read "Kino Ost &amp; Süd". Loaded with '{% load css_strings %}'.
"""

import html
import re

from django import template
from django.utils.safestring import SafeString, mark_safe

register = template.Library()

#: What could end the string or start an escape, close the '<style>'
#: element, or break the line of the rule.
_ZU_MASKIEREN = re.compile(r"[\\\"'<>\x00-\x1f\x7f]")


@register.filter
def css_text(wert) -> SafeString:
    """A text as it stands between the double quotes of a CSS string.

    It comes from a template, escaped for HTML, and is read back first.
    Then every character that could end the string, start an escape or
    close the '<style>' element becomes a CSS escape: its code point in hex
    and a space, which CSS takes as the end of the escape. A line break in a
    name would otherwise have broken the rule of every sheet.
    """
    text = html.unescape(str(wert))
    return mark_safe(_ZU_MASKIEREN.sub(lambda zeichen: f"\\{ord(zeichen.group()):x} ", text))
