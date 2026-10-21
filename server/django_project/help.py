"""The help sidebar: which page has a detailed explanation.

The pages themselves say only what is necessary. Whoever wants to know more
opens a sidebar through the "Help" button in the bar. Its text lives under
'templates/help/<language>/' and is named after the page's URL name
('de/ware_list.html'). That way all explanations for users are in one place,
and a new one needs only a new file, no change to view or page.

The help does not go through gettext. These are long running texts, and they
read better side by side as whole pages than as a hundred fragments in the
catalog. If a text is missing in the chosen language, the German one appears.
Better an explanation in the wrong language than none at all.

Creating and editing almost always share a text: if 'help/de/behaelter_add.html'
is missing, 'help/de/behaelter_form.html' applies. Where the URL name is
German, the file is named after the page's template ('DATEINAMEN'), because
file names in the project are English.
"""

from django.http import HttpRequest
from django.template import TemplateDoesNotExist
from django.template.loader import select_template
from django.utils.translation import get_language

FORMULARENDUNGEN = ("_add", "_edit")

#: The source language. It has every text and serves as the fallback.
GRUNDSPRACHE = "de"

#: URL name -> file name, where the two differ.
DATEINAMEN = {
    "auswertung": "evaluation",
    "email_aendern": "email_change",
    "inventur_pruefen": "inventur_review",
    "inventur_abschluss": "inventur_closing",
    "inventur_arten": "inventur_types",
    "inventur_lauf": "inventur_run",
    "inventur_zaehlende": "inventur_counters",
    "inventur_sollbestand": "sollbestand",
    "inventur_soll_pflegen": "soll_edit",
    "hausartikel_import": "import",
    "hausartikel_zusammenfuehren": "hausartikel_merge",
    "ort_aufteilen": "ort_split",
    "ort_kopieren": "ort_copy",
    "ort_zusammenfuehren": "ort_merge",
    "ware_aufteilen": "ware_split",
    "warenfamilie_aufteilen": "warenfamilie_split",
    "warenfamilie_zusammenfuehren": "warenfamilie_merge",
}


def kandidaten(url_name: str, sprache: str = GRUNDSPRACHE) -> list[str]:
    """The files that fit this page, in the order of the search.

    First the chosen language, then German. 'get_language' also yields variants
    like 'en-gb', which is why only the part before the dash counts.
    """
    namen = [DATEINAMEN.get(url_name, url_name)]
    for endung in FORMULARENDUNGEN:
        if url_name.endswith(endung):
            namen.append(url_name.removesuffix(endung) + "_form")
    sprachen = dict.fromkeys([(sprache or GRUNDSPRACHE).split("-")[0], GRUNDSPRACHE])
    return [f"help/{kuerzel}/{name}.html" for kuerzel in sprachen for name in namen]


def hilfe(request: HttpRequest) -> dict[str, str]:
    """Context processor: 'hilfe_vorlage' if the page has a help text.

    Only for managers, because nobody else sees the bar with the button. Pages
    with a namespace (the administration) are left out: their URL names are
    'index' or 'login' and would otherwise hit a text by accident.
    """
    treffer = request.resolver_match
    if treffer is None or not treffer.url_name or treffer.namespace:
        return {}
    if not getattr(request.user, "is_staff", False):
        return {}
    try:
        vorlage = select_template(kandidaten(treffer.url_name, get_language()))
    except TemplateDoesNotExist:
        return {}
    return {"hilfe_vorlage": vorlage.template.name}
