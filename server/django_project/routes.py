"""The addresses of the web interface in the server's language.

The patterns in the 'urls.py' files are German, like everything in the
project. If the server is set to English ('LANGUAGE_CODE'), one reads
'/sites/' instead of '/haeuser/' and '/privacy/' instead of '/datenschutz/'.
'pfad' translates a pattern at load time, segment by segment, from the table
'SEGMENTE'. Parameters like '<int:pk>' stay, and the names of the addresses
stay German: code and templates only know 'reverse("haus_list")', never the
text.

Why not Django's translated patterns ('path(_("haeuser/"), …)')? They follow
the language of the **request**, here the account. A German account on an
English server would get addresses an English account cannot open, and a
bookmark would only be valid for the language it was created in. An address
belongs to the server, not to the account, like its certificate. Hence
'LANGUAGE_CODE' decides once at load time, and a change there is a change of
all addresses. 'DEPLOYMENT.md' says so under "Language and time".

What does not pass through here stays as it is: '/api/' (the counting app is
built with these addresses), '/app/', '/accounts/' (Django), '/admin/',
'/icons/' and '/static/'.

The table names **every** segment, including those that are the same in both
languages ('add', 'edit', 'delete'). A missing segment fails at load time, on
the German server just as on the English one. That way a forgotten entry
shows up in the first test and not only in English operation.
"""

from django.conf import settings
from django.urls import path

#: German → English, per segment. The English words follow the catalog:
#: 'Haus' is "site", 'Ware' "product", 'Hausartikel' "article", 'Auswertung'
#: "report".
SEGMENTE = {
    # The same in both languages.
    "add": "add",
    "edit": "edit",
    "delete": "delete",
    "import": "import",
    "pdf": "pdf",
    "status": "status",
    "users": "users",
    # Houses and their structure.
    "haeuser": "sites",
    "bereiche": "areas",
    "orte": "locations",
    # The catalog.
    "katalog": "catalogue",
    "waren": "products",
    "hausartikel": "articles",
    "rezepturartikel": "prepared-items",
    "warenfamilien": "product-families",
    "warengruppen": "article-groups",
    "behaelter": "containers",
    "gebinde": "packs",
    "zaehlliste": "count-list",
    "erwartung": "expectation",
    "inventurarten": "inventory-types",
    "inventurart": "inventory-type",
    "verschieben": "move",
    "abwiegen": "weighing",
    "sortieren": "sort",
    "umschalten": "toggle",
    "aufteilen": "split",
    "zusammenfuehren": "merge",
    "kopieren": "copy",
    # Inventories.
    "inventuren": "inventories",
    "auswertungen": "reports",
    "lauf": "run",
    "abschliessen": "close",
    "zaehlung-starten": "start-counting",
    "zurueck": "back",
    "pruefen": "review",
    "stand": "state",
    "zaehlende": "counters",
    "arten": "types",
    "sollbestand": "target-stock",
    "soll": "target",
    "differenz-von-hand": "manual-difference",
    "sperre": "lock",
    # Accounts and pages for everyone.
    "sprache": "language",
    "farben": "colours",
    "datenschutz": "privacy",
    "impressum": "legal-notice",
    "kein-zugriff": "no-access",
    "accounts": "accounts",
    "einladung": "invitation",
    "einladen": "invite",
    "fertig": "done",
    "passkeys": "passkeys",
    "options": "options",
    "email": "email",
    "bestaetigen": "confirm",
}


#: What must stay reachable even without the manager role, as far as it is
#: called the same in every language: the API, the counting app with its
#: extras, sign-in and sign-out and the honeypot. Without them the redirect
#: would loop. And the icons: the browser tab shows its image on the login
#: page already. Here and not in the middleware ('StaffOnlyMiddleware'),
#: because the settings check the admin's path against them, and they load
#: before any app.
EXEMPT_PREFIXES = (
    "/api/",
    "/accounts/",
    "/static/",
    "/admin/",
    "/app/",
    "/icons/",
)

#: Plus the pages of the web interface whose address follows the server's
#: language: the block page itself, the privacy page and the legal notice
#: (whoever is on them is precisely a counter who otherwise only sees the
#: block page, and the login form links to them too) and the language choice
#: without an account, because the login form offers it.
FREIE_MUSTER = ("datenschutz/", "impressum/", "kein-zugriff/", "users/sprache/")


def freie_praefixe(sprache: str | None = None) -> tuple[str, ...]:
    """All prefixes the block lets through, in the server's language or in 'sprache'.

    Fresh on every call and not once at load time: that way the list follows a
    switched language in tests too, and the few strings cost nothing.
    """
    return EXEMPT_PREFIXES + tuple("/" + uebersetzt(muster, sprache) for muster in FREIE_MUSTER)


def ist_englisch(sprache: str | None = None) -> bool:
    """Whether the addresses are English. Unspecified, the server's language counts."""
    return (sprache or settings.LANGUAGE_CODE).lower().startswith("en")


def uebersetzt(muster: str, sprache: str | None = None) -> str:
    """The pattern in the requested language. Unknown segments are an error."""
    teile = []
    for teil in muster.split("/"):
        if not teil or teil.startswith("<"):
            teile.append(teil)
            continue
        if teil not in SEGMENTE:
            raise LookupError(f"Das Segment '{teil}' fehlt in 'routes.SEGMENTE'.")
        teile.append(SEGMENTE[teil] if ist_englisch(sprache) else teil)
    return "/".join(teile)


def pfad(muster: str, ansicht, name: str | None = None, **kwargs):
    """'path', except that the pattern follows the server's language.

    The name stays the German one: 'reverse', the templates and the help texts
    ('help.py') hang on it.
    """
    return path(uebersetzt(muster), ansicht, name=name, **kwargs)
