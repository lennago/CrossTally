"""The mails the application sends, their placeholders and the rules for an own text.

Every mail has a subject and a text per language, and the business that runs
the application words them itself in the administration ('Mailtext'). The
shipped texts lie as files under 'templates/mails/<language>/', one per mail,
the subject in the first line. They are the default: a field left empty means
the shipped text, so an improvement in a later version reaches everyone who
did not change it.

**A small part of Django's template language**, and no more: placeholders
like '{{ vorname }}' and conditions with 'if', 'elif', 'else' and 'endif'.
The conditions are what a free text needs here. A change mail reads
differently when the person did it themselves, when someone else did it and
is named, and when nobody is named. Three texts per mail would be three
places to keep in step. Everything else (filters, loops, 'include', 'load')
is refused when saving, and the context holds strings and booleans only, so a
text cannot reach into an account or the settings. Unlike the privacy text
these are no pages: nothing in a plain text mail runs.
"""

import re
from collections.abc import Mapping
from functools import cache

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.template import Context, Engine, TemplateSyntaxError
from django.template.base import FilterExpression, Node, TextNode, Variable, VariableNode
from django.template.defaulttags import CommentNode, IfNode, TemplateLiteral
from django.utils.translation import gettext_lazy as _

from django_project.labels import Beschriftung


class Mailart(models.TextChoices):
    """The mails, in the order the administration lists them. The value names the file."""

    EINLADUNG = "einladung", _("Einladung")
    PASSWORT_VERGESSEN = "passwort_vergessen", _("Passwort vergessen")
    KONTO_GEAENDERT = "konto_geaendert", _("Konto geändert")
    EMAIL_BESTAETIGEN = "email_bestaetigen", _("E-Mail-Adresse bestätigen")
    KONTO_DEAKTIVIERT = "konto_deaktiviert", _("Konto deaktiviert")
    KONTO_AKTIVIERT = "konto_aktiviert", _("Konto wieder aktiv")
    KONTO_GELOESCHT = "konto_geloescht", _("Konto gelöscht")
    PASSWORT_GEAENDERT = "passwort_geaendert", _("Passwort geändert")
    PASSWORT_GESETZT = "passwort_gesetzt", _("Passwort in der Verwaltung gesetzt")
    PASSWORT_ABGESCHALTET = "passwort_abgeschaltet", _("Anmeldung mit Passwort abgeschaltet")
    PASSKEY_ANGELEGT = "passkey_angelegt", _("Neuer Passkey")
    PASSKEY_ENTFERNT = "passkey_entfernt", _("Passkey entfernt")


#: When each mail goes out. Stands above the fields in the administration.
ANLASS: dict[str, Beschriftung] = {
    Mailart.EINLADUNG: _(
        "Geht an ein neues Konto ohne Passwort. Über den Link setzt die Person ihr Passwort selbst."
    ),
    Mailart.PASSWORT_VERGESSEN: _(
        "Geht hinaus, wenn jemand auf der Anmeldeseite „Passwort vergessen?“ nutzt und die "
        "Adresse zu einem aktiven Konto mit Passwort gehört."
    ),
    Mailart.KONTO_GEAENDERT: _(
        "Geht hinaus, wenn jemand Benutzername, Name, Adresse, Rolle oder Häuser eines Kontos "
        "ändert. Ändert sich die Adresse, an die alte und die neue."
    ),
    Mailart.EMAIL_BESTAETIGEN: _(
        "Geht an eine neue E-Mail-Adresse, bevor sie gilt: wenn die Person sie selbst einträgt "
        "oder ein Administrator sie ändert. Über den Link bestätigt die Person die Adresse."
    ),
    Mailart.KONTO_DEAKTIVIERT: _(
        "Geht hinaus, wenn ein Konto deaktiviert wird. Danach bekommt das Konto keine Mail "
        "mehr, bis es wieder aktiv ist."
    ),
    Mailart.KONTO_AKTIVIERT: _("Geht hinaus, wenn ein deaktiviertes Konto wieder aktiv wird."),
    Mailart.KONTO_GELOESCHT: _("Geht hinaus, nachdem ein Konto gelöscht ist."),
    Mailart.PASSWORT_GEAENDERT: _(
        "Geht hinaus, wenn die Person ihr Passwort selbst ändert: auf der Website, in der "
        "Zähl-App oder über „Passwort vergessen?“."
    ),
    Mailart.PASSWORT_GESETZT: _(
        "Geht hinaus, wenn ein Administrator in der Verwaltung ein neues Passwort setzt."
    ),
    Mailart.PASSWORT_ABGESCHALTET: _(
        "Geht hinaus, wenn ein Administrator in der Verwaltung die Anmeldung mit Passwort "
        "abschaltet."
    ),
    Mailart.PASSKEY_ANGELEGT: _("Geht hinaus, wenn die Person einen Passkey anlegt."),
    Mailart.PASSKEY_ENTFERNT: _(
        "Geht hinaus, wenn ein Passkey entfernt wird, von der Person selbst oder in der Verwaltung."
    ),
}

#: What each placeholder holds. The administration lists those of the mail at hand.
PLATZHALTER: dict[str, Beschriftung] = {
    "vorname": _("Vorname der Person"),
    "nachname": _("Nachname der Person"),
    "benutzername": _("ihr Benutzername"),
    "anwendung": _("Name der Anwendung, wie im Erscheinungsbild"),
    "wer": _(
        "wer es ausgelöst hat. Leer, wenn die Mail keine Namen nennt oder die Person es selbst war."
    ),
    "selbst": _("wahr, wenn die Person es selbst war"),
    "link": _("der Link, über den die Person ihr Passwort setzt oder die Adresse bestätigt"),
    "tage": _("wie viele Tage der Link gilt"),
    "app": _("Adresse der Zähl-App"),
    "website": _("Adresse der Anmeldung auf der Website"),
    "aenderungen": _("was sich geändert hat, je Zeile „Feld: alt → neu“"),
    "wo": _("wo das Passwort geändert wurde, etwa „auf der Website“"),
    "passkey": _("Name des Passkeys"),
}

#: Every mail knows who receives it and what the application is called.
GEMEINSAM = ("vorname", "nachname", "benutzername", "anwendung")

#: The placeholders per mail. Any other name is refused when saving.
VARIABLEN: dict[str, tuple[str, ...]] = {
    Mailart.EINLADUNG: (*GEMEINSAM, "wer", "link", "tage", "app", "website"),
    Mailart.PASSWORT_VERGESSEN: (*GEMEINSAM, "link", "tage"),
    Mailart.KONTO_GEAENDERT: (*GEMEINSAM, "aenderungen", "wer", "selbst"),
    Mailart.EMAIL_BESTAETIGEN: (*GEMEINSAM, "link", "tage", "wer", "selbst"),
    Mailart.KONTO_DEAKTIVIERT: (*GEMEINSAM, "aenderungen", "wer", "selbst"),
    # An inactive account cannot sign in, so it cannot switch itself back on.
    Mailart.KONTO_AKTIVIERT: (*GEMEINSAM, "aenderungen", "wer"),
    Mailart.KONTO_GELOESCHT: (*GEMEINSAM, "wer", "selbst"),
    Mailart.PASSWORT_GEAENDERT: (*GEMEINSAM, "wo"),
    Mailart.PASSWORT_GESETZT: (*GEMEINSAM, "wer", "selbst"),
    Mailart.PASSWORT_ABGESCHALTET: (*GEMEINSAM, "wer", "selbst"),
    Mailart.PASSKEY_ANGELEGT: (*GEMEINSAM, "passkey"),
    Mailart.PASSKEY_ENTFERNT: (*GEMEINSAM, "passkey", "wer", "selbst"),
}

#: Without these the mail is useless: the person would not get any further.
PFLICHT: dict[str, tuple[str, ...]] = {
    Mailart.EINLADUNG: ("link",),
    Mailart.PASSWORT_VERGESSEN: ("link",),
    Mailart.EMAIL_BESTAETIGEN: ("link",),
}

#: The languages there are shipped texts for. Any other account reads German.
SPRACHEN = ("de", "en")


def sprache_von(sprache: str) -> str:
    """'en-gb' is English, anything unknown is German, the source language."""
    kurz = (sprache or "").split("-")[0]
    return kurz if kurz in SPRACHEN else SPRACHEN[0]


@cache
def mitgeliefert(art: str, sprache: str) -> tuple[str, str]:
    """Subject and text as shipped. The first line of the file is the subject."""
    pfad = settings.BASE_DIR / "templates" / "mails" / sprache_von(sprache) / f"{art}.txt"
    betreff, _leer, text = pfad.read_text(encoding="utf-8").strip().partition("\n\n")
    return betreff.strip(), text.strip()


def bereinigt(text: str) -> str:
    """Line ends as '\\n', no spaces at the end of a line, nothing around the whole.

    The browser sends '\\r\\n', the file has '\\n'. Without this an unchanged
    text would count as an own one.
    """
    zeilen = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(zeile.rstrip() for zeile in zeilen).strip()


#: Without loaders and libraries: 'include' finds nothing, 'load' knows nothing.
#: What is left is refused by 'pruefen' anyway.
_ENGINE = Engine(loaders=[], libraries={})

#: What every context knows without being given it ('BaseContext'). Allowed in
#: a condition like "{% if selbst == True %}".
EINGEBAUT = {"True", "False", "None"}


def _ausdruck(ausdruck: FilterExpression, erlaubt: set[str], namen: set[str]) -> str | None:
    """The reason a placeholder is refused, or None. Collects the names it uses."""
    if ausdruck.filters:
        return str(_("Filter wie „%(beispiel)s“ gehen hier nicht.") % {"beispiel": ausdruck.token})
    wert = ausdruck.var
    if not isinstance(wert, Variable) or wert.lookups is None:
        # A string or a number in quotes, nothing that reaches into the context.
        return None
    name = ".".join(wert.lookups)
    if len(wert.lookups) != 1 or name not in erlaubt:
        return str(_("„%(name)s“ gibt es in dieser Mail nicht.") % {"name": name})
    namen.add(name)
    return None


def _bedingung(bedingung, erlaubt: set[str], namen: set[str]) -> str | None:
    """Walks an 'if' condition: values at the leaves, operators in between."""
    if bedingung is None:
        return None
    if isinstance(bedingung, TemplateLiteral):
        wert = bedingung.value
        return _ausdruck(wert, erlaubt, namen) if isinstance(wert, FilterExpression) else None
    return _bedingung(bedingung.first, erlaubt, namen) or _bedingung(
        bedingung.second, erlaubt, namen
    )


def _knoten(liste, erlaubt: set[str], namen: set[str], oben: set[str] | None = None) -> str | None:
    """Walks the nodes. 'oben' collects the placeholders that stand outside every condition."""
    knoten: Node
    for knoten in liste:
        if isinstance(knoten, TextNode | CommentNode):
            continue
        if isinstance(knoten, VariableNode):
            vorher = set(namen)
            grund = _ausdruck(knoten.filter_expression, erlaubt, namen)
            if oben is not None:
                oben |= namen - vorher
        elif isinstance(knoten, IfNode):
            grund = None
            for bedingung, unterliste in knoten.conditions_nodelists:
                grund = _bedingung(bedingung, erlaubt, namen) or _knoten(unterliste, erlaubt, namen)
                if grund:
                    break
        else:
            grund = str(
                _("Erlaubt sind nur Platzhalter wie %(platzhalter)s und Bedingungen wie %(wenn)s.")
                % {"platzhalter": "{{ vorname }}", "wenn": "{% if selbst %} … {% endif %}"}
            )
        if grund:
            return grund
    return None


def pruefen(quelle: str, art: str, mit_pflicht: bool = True) -> None:
    """Refuses a text the mail could not be built from, with the reason.

    A syntax error, a filter or tag beyond 'if', a placeholder this mail does
    not have, and for the body a missing placeholder the mail needs ('PFLICHT').
    """
    try:
        vorlage = _ENGINE.from_string(quelle)
    except TemplateSyntaxError as fehler:
        raise ValidationError(
            _("Der Text lässt sich nicht lesen: %(fehler)s") % {"fehler": str(fehler).strip()}
        ) from fehler
    namen: set[str] = set()
    # Only what stands outside every condition counts for 'PFLICHT': a link
    # inside "{% if wer %}" is missing from the mail as soon as the name is
    # left out (until 10/2026 the check saw it anyway).
    oben: set[str] = set()
    grund = _knoten(vorlage.nodelist, set(VARIABLEN[art]) | EINGEBAUT, namen, oben)
    if grund:
        raise ValidationError(grund)
    if mit_pflicht:
        for name in PFLICHT.get(art, ()):
            if name not in oben:
                raise ValidationError(
                    _("Der Text braucht %(platzhalter)s, sonst kommt die Person nicht weiter.")
                    % {"platzhalter": "{{ " + name + " }}"}
                )


def ausgefuellt(quelle: str, werte: Mapping[str, object]) -> str:
    """The text with its placeholders filled, without the gaps a condition leaves.

    A condition on a line of its own leaves an empty line behind, and one
    that does not apply leaves two. At most one empty line stays between
    paragraphs.
    """
    text = _ENGINE.from_string(quelle).render(Context(dict(werte), autoescape=False))
    return re.sub(r"\n{3,}", "\n\n", bereinigt(text))


def betreffzeile(quelle: str, werte: Mapping[str, object]) -> str:
    """A subject has exactly one line. Mail servers refuse a line break in it."""
    return " ".join(ausgefuellt(quelle, werte).split())
