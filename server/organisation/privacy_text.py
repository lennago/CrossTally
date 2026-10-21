"""The bundled privacy text, one file per language.

It is the default of the field on the appearance: whoever uses the
application finds it there and extends, changes or replaces it. That is why
it lies as text under 'templates/privacy/' and not as a template with
gettext: the administration shows exactly what the page shows too, and the
format is that of the page ('django_project/textformat.py').
"""

from functools import cache

from django.conf import settings


@cache
def mitgelieferter_datenschutztext(sprache: str) -> str:
    pfad = settings.BASE_DIR / "templates" / "privacy" / f"{sprache}.txt"
    return pfad.read_text(encoding="utf-8").strip()


def datenschutztext_de() -> str:
    """The default of the German field. Without an argument, because 'default' calls without."""
    return mitgelieferter_datenschutztext("de")


def datenschutztext_en() -> str:
    return mitgelieferter_datenschutztext("en")
