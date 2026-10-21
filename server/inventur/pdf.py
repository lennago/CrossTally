"""PDF output: a sheet from a Django template.

Rendered with **WeasyPrint**: the layout is HTML and CSS, like the rest of
the interface. Another document is thus another template and not another
piece of layout code, and that is foreseeable here, more reports are to
come as PDF.

The page frame (margins, header and footer, page numbers) stands once in
'templates/pdf/base.html'. Every document inherits from it.
"""

from django.http import HttpResponse
from django.template.loader import render_to_string
from django.utils.text import slugify

from katalog.models import portion_abgekuerzt


def pdf_html(vorlage: str, kontext: dict) -> str:
    """The HTML that WeasyPrint gets. Tests read the sheet there.

    On paper the portion is short ('portion_abgekuerzt'). Whoever renders a
    PDF template without this function sees "Portionen" where the sheet
    says "Port.".
    """
    with portion_abgekuerzt():
        return render_to_string(vorlage, kontext)


def als_pdf(request, vorlage: str, kontext: dict, dateiname: str) -> HttpResponse:
    """Renders a template and returns it as PDF.

    The import sits inside the function: WeasyPrint pulls in its font and
    graphics libraries when loading. At application start (and in every test
    that only checks a form) that would be wasted time.

    The request is only needed for the colours of the difference. They hang
    on the account, and printing is for whoever presses the button. A context
    processor does not apply here, because rendering happens without
    'RequestContext'.
    """
    from weasyprint import HTML  # noqa: PLC0415  (deliberately loaded late)

    farben = getattr(request.user, "differenzfarben", "alle")
    html = pdf_html(vorlage, kontext | {"farben": farben})
    antwort = HttpResponse(content_type="application/pdf")
    antwort["Content-Disposition"] = f'inline; filename="{slugify(dateiname)}.pdf"'
    # 'write_pdf' returns nothing only when it writes into a file.
    antwort.write(HTML(string=html, url_fetcher=ohne_nachladen()).write_pdf() or b"")
    return antwort


def ohne_nachladen():
    """A fetcher that opens nothing, because the sheets need nothing from outside.

    No image, no stylesheet, no font: everything stands in the page.
    WeasyPrint's own fetcher would open every address the document names,
    local files included, and names from the master data stand in it. Until
    10/2026 nothing held it back.
    """
    from weasyprint import URLFetcher  # noqa: PLC0415  (deliberately loaded late)

    return URLFetcher(allowed_protocols=())
