"""Backups from before the inventory types became tabs, rewritten into today's format.

Until 10/2026 the scope stood on the product ('inventurumfang'), an own type
held its products and a rule, and an inventory had one kind ('typ').
'loaddata' skips fields that no longer exist. Here that would silently lose
what an old backup says about monthly, annual and own types: every house
article would land in the first type, and no inventory would count any.

So the state is rewritten on the way in, by the rules of the migrations
that made the types tabs. They were squashed on 2026-10-14 and live on only
here. Only in memory: the file stays as it is.
Restoring in the administration and 'manage.py backup read' both go through
'umschreiben'. Nothing here asks the database, because 'backup read' also
runs while it is away.

The attempts at the decoy were the package's model until 10/2026
('admin_honeypot.loginattempt'). Now they are our own, under the same table
('accounts.Anmeldeversuch'), and an old backup only needs the new label.

A frozen result carries the unit it was counted in since 10/2026
('inventur/0002'). One from before gets the unit of its house article, as
the migration gives it. The receiver at loading cannot do that here: in the
file the results stand before the house articles ('inventur/apps.py').
"""

import json
from datetime import date
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import formats, translation
from django.utils.translation import gettext as _

Satz = dict[str, Any]

#: What only the old format carries, per model.
ALTE_FELDER = {
    "katalog.ware": "inventurumfang",
    "katalog.inventurart": "umfang",
    "inventur.inventur": "typ",
    "inventur.artentscheidung": "aktion",
}

#: The model of the old format that is gone entirely.
UMFANGSENTSCHEIDUNG = "inventur.umfangsentscheidung"

#: Models that only changed their label. Same fields, same table.
UMBENANNT = {"admin_honeypot.loginattempt": "accounts.anmeldeversuch"}


def umschreiben(inhalt: bytes) -> bytes:
    """The state in today's format. One that already is comes back as the same object.

    What is no JSON list is not ours to judge: 'loaddata' says what is
    wrong with it.
    """
    try:
        saetze = json.loads(inhalt)
    except ValueError:
        return inhalt
    if not isinstance(saetze, list):
        return inhalt
    umbenannt = _umbenennen(saetze)
    eingetragen = _einheiten_eintragen(saetze)
    alt = any(_ist_alt(satz) for satz in saetze)
    if not alt and not umbenannt and not eingetragen:
        return inhalt
    ergebnis = _Umschreibung(saetze).ergebnis() if alt else saetze
    return json.dumps(ergebnis, ensure_ascii=False, indent=2).encode()


def _umbenennen(saetze: list) -> bool:
    """Gives the rows of a renamed model their new label. Says whether any had the old one."""
    getroffen = False
    for satz in saetze:
        if isinstance(satz, dict) and satz.get("model") in UMBENANNT:
            satz["model"] = UMBENANNT[satz["model"]]
            getroffen = True
    return getroffen


def _einheiten_eintragen(saetze: list) -> bool:
    """Gives every result without a unit the one of its house article. Says whether any lacked one.

    The unit that applied until then: the article's, and in a state from
    before 09/2026, where the article had none, the product's
    ('katalog/apps.py', 'einheit_nachtragen'). A result whose article is not
    in the state stays as it is. The receiver looks for it in the database.
    """
    ohne = [
        satz["fields"]
        for satz in _alle(saetze, "inventur.inventurergebnis")
        if isinstance(satz.get("fields"), dict) and "einheit" not in satz["fields"]
    ]
    if not ohne:
        return False
    einheit_der_ware: dict[Any, Any] = {}
    for satz in _alle(saetze, "katalog.ware"):
        felder = satz.get("fields", {})
        einheit_der_ware[_schluessel([felder.get("name")])] = felder.get("basiseinheit")
        if "pk" in satz:
            einheit_der_ware[_schluessel(satz["pk"])] = felder.get("basiseinheit")
    artikel = {
        _schluessel(satz["pk"]): satz.get("fields", {})
        for satz in _alle(saetze, "katalog.hausartikel")
        if "pk" in satz
    }
    for felder in ohne:
        quelle = artikel.get(_schluessel(felder.get("hausartikel")))
        if quelle is None:
            continue
        if quelle.get("basiseinheit") is not None:
            felder["einheit"] = quelle["basiseinheit"]
            felder["faktor_zur_ware"] = quelle.get("faktor_zur_ware")
        elif (einheit := einheit_der_ware.get(_schluessel(quelle.get("ware")))) is not None:
            felder["einheit"] = einheit
    return True


def _alle(saetze: list, modell: str) -> list[Satz]:
    return [satz for satz in saetze if isinstance(satz, dict) and satz.get("model") == modell]


def _ist_alt(satz: object) -> bool:
    if not isinstance(satz, dict):
        return False
    modell = str(satz.get("model"))
    return modell == UMFANGSENTSCHEIDUNG or ALTE_FELDER.get(modell, "") in satz.get("fields", {})


def _schluessel(verweis: Any) -> Any:
    """A reference as a dictionary key: a natural key is a list in JSON, a number stays one."""
    return tuple(verweis) if isinstance(verweis, list) else verweis


class _Umschreibung:
    """One pass over the rows of a state. Names the types as the migration does."""

    def __init__(self, saetze: list[Satz]) -> None:
        self.saetze = saetze
        with translation.override(settings.LANGUAGE_CODE):
            self.monat = _("Monatsinventur")
            self.jahr = _("Jahresinventur")

        alte_arten = self.alle("katalog.inventurart")
        self.alte_art = {satz["fields"]["name"]: satz for satz in alte_arten}
        # Other rows point to a type by its name, or without natural keys by its number.
        self.name_der_art: dict[Any, str] = {}
        for satz in alte_arten:
            name = satz["fields"]["name"]
            self.name_der_art[_schluessel([name])] = name
            if "pk" in satz:
                self.name_der_art[_schluessel(satz["pk"])] = name
        self.eigene = sorted(name for name in self.alte_art if name not in (self.monat, self.jahr))

        def kommt_vor(modell: str, feld: str, wert: str) -> bool:
            return any(satz["fields"].get(feld) == wert for satz in self.alle(modell))

        jaehrlich_gebraucht = (
            self.jahr in self.alte_art
            or kommt_vor("katalog.ware", "inventurumfang", "NUR_JAHR")
            or kommt_vor("inventur.inventur", "typ", "JAHR")
            or kommt_vor(UMFANGSENTSCHEIDUNG, "umfang", "NUR_JAHR")
        )
        # The order of the migration: monthly, annual, then the own ones by name.
        self.reihenfolge = {self.monat: 1}
        if jaehrlich_gebraucht:
            self.reihenfolge[self.jahr] = 2
        for stelle, name in enumerate(self.eigene, start=3):
            self.reihenfolge[name] = stelle

    def alle(self, modell: str) -> list[Satz]:
        return [satz for satz in self.saetze if satz.get("model") == modell]

    def regel(self, name: str) -> str:
        return self.alte_art[name]["fields"].get("umfang", "MONAT_UND_JAHR")

    def ergebnis(self) -> list[Satz]:
        self._doppelte_tage_melden()
        art_der_ware = self._arten_der_waren()
        zuordnungen = self._arten_der_inventuren()
        entscheidungen = self._entscheidungen()

        ergebnis: list[Satz] = []
        arten_stehen = False
        for satz in self.saetze:
            modell = satz.get("model")
            if modell in ("katalog.inventurart", UMFANGSENTSCHEIDUNG, "inventur.artentscheidung"):
                continue
            # Before the first row that names a type, as 'dumpdata' would sort it.
            if not arten_stehen and modell in ("katalog.hausartikel", "inventur.inventur"):
                ergebnis.extend(self._neue_arten())
                arten_stehen = True
            if modell == "katalog.hausartikel":
                felder = satz["fields"]
                felder["inventurart"] = [
                    art_der_ware.get(_schluessel(felder.get("ware")), self.monat)
                ]
            ergebnis.append(satz)
        if not arten_stehen:
            ergebnis.extend(self._neue_arten())
        return ergebnis + zuordnungen + entscheidungen

    def _neue_arten(self) -> list[Satz]:
        return [
            {"model": "katalog.inventurart", "fields": {"name": name, "reihenfolge": stelle}}
            for name, stelle in self.reihenfolge.items()
        ]

    def _arten_der_waren(self) -> dict[Any, str]:
        """Per product the type its house articles go into, as the type migration did.

        Into the own type it stood in, in two of them into the first by name.
        Otherwise its scope decides.
        """
        aus_eigener: dict[Any, str] = {}
        for name in [self.monat, self.jahr, *self.eigene]:
            if name not in self.alte_art:
                continue
            for ware in self.alte_art[name]["fields"].get("waren", []):
                aus_eigener.setdefault(_schluessel(ware), name)

        art_der_ware: dict[Any, str] = {}
        for satz in self.alle("katalog.ware"):
            felder = satz["fields"]
            umfang = felder.pop("inventurumfang", None)
            schluessel = [_schluessel([felder["name"]])]
            if "pk" in satz:
                schluessel.append(_schluessel(satz["pk"]))
            ziel = next((aus_eigener[k] for k in schluessel if k in aus_eigener), None)
            if ziel is None:
                ziel = self.jahr if umfang == "NUR_JAHR" else self.monat
            for eintrag in schluessel:
                art_der_ware[eintrag] = ziel
        return art_der_ware

    def _arten_der_inventuren(self) -> list[Satz]:
        """Per inventory the types it counts, as the type migration did.

        Monthly: the monthly inventory and every own type that counted along
        monthly. Annual: monthly and annual and every own type that counted
        along annually. Own type: that one.
        """
        monatlich = [name for name in self.eigene if self.regel(name) == "MONAT_UND_JAHR"]
        jaehrlich = [
            name for name in self.eigene if self.regel(name) in ("MONAT_UND_JAHR", "NUR_JAHR")
        ]
        zuordnungen: list[Satz] = []
        for satz in self.alle("inventur.inventur"):
            felder = satz["fields"]
            typ = felder.pop("typ", "MONAT")
            eigene_art = self.name_der_art.get(_schluessel(felder.pop("inventurart", None)))
            if typ == "EIGEN" and eigene_art is not None:
                arten = [eigene_art]
            elif typ == "JAHR":
                arten = [self.monat, self.jahr, *jaehrlich]
            else:
                arten = [self.monat, *monatlich]
            for name in arten:
                zuordnungen.append(
                    {
                        "model": "inventur.inventurinventurart",
                        "pk": len(zuordnungen) + 1,
                        "fields": {"inventur": satz["pk"], "inventurart": [name]},
                    }
                )
        return zuordnungen

    def _entscheidungen(self) -> list[Satz]:
        """The decisions at closing as decisions for a type, one per inventory and product.

        "Out of the type" said nothing about where to, it goes. A scope
        decision becomes one for the annual or the monthly inventory. Of two
        for the same product the later stands, as in the migration.
        """
        entscheidungen: list[Satz] = []
        for satz in self.alle("inventur.artentscheidung"):
            felder = satz["fields"]
            if felder.pop("aktion", None) == "HERAUSGENOMMEN":
                continue
            name = self.name_der_art.get(_schluessel(felder["inventurart"]))
            if name is not None:
                felder["inventurart"] = [name]
            entscheidungen.append(satz)
        naechste = max((satz.get("pk", 0) for satz in entscheidungen), default=0) + 1
        for stelle, satz in enumerate(self.alle(UMFANGSENTSCHEIDUNG), start=naechste):
            felder = satz["fields"]
            ziel = self.jahr if felder.get("umfang") == "NUR_JAHR" else self.monat
            entscheidungen.append(
                {
                    "model": "inventur.artentscheidung",
                    "pk": stelle,
                    "fields": {
                        "inventur": felder["inventur"],
                        "ware": felder["ware"],
                        "inventurart": [ziel],
                    },
                }
            )
        letzte: dict[tuple, Satz] = {}
        for satz in sorted(entscheidungen, key=lambda satz: satz.get("pk", 0)):
            felder = satz["fields"]
            schluessel = (_schluessel(felder["inventur"]), _schluessel(felder["ware"]))
            letzte[schluessel] = satz
        return list(letzte.values())

    def _doppelte_tage_melden(self) -> None:
        """One inventory per house and day from now on. Which one goes is a person's decision.

        The migration stops at that, and a state with two would fail at the
        database. Said before anything is emptied, with the houses by name.
        """
        hausname = {
            satz["pk"]: satz["fields"].get("name", "")
            for satz in self.alle("organisation.haus")
            if "pk" in satz
        }
        gesehen: set[tuple] = set()
        doppelt: list[tuple] = []
        for satz in self.alle("inventur.inventur"):
            felder = satz["fields"]
            schluessel = (_schluessel(felder.get("haus")), felder.get("stichtag"))
            if schluessel in gesehen and schluessel not in doppelt:
                doppelt.append(schluessel)
            gesehen.add(schluessel)
        if not doppelt:
            return
        tage = ", ".join(
            _("%(haus)s am %(tag)s")
            % {
                "haus": hausname.get(haus, str(haus)),
                "tag": formats.date_format(date.fromisoformat(tag), "SHORT_DATE_FORMAT"),
            }
            for haus, tag in doppelt
        )
        raise ValidationError(
            _(
                "Seit 10/2026 hat ein Haus je Tag nur eine Inventur. In diesem Backup stehen "
                "mehrere am selben Tag: %(tage)s. Es lässt sich deshalb nicht einspielen."
            )
            % {"tage": tage}
        )
