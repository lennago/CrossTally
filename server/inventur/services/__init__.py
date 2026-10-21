"""What an inventory needs beyond its model: files, remarks and display rows.

They do not belong on the model because they know files, forms and pages:

- **Creating** a run ('creating'): per chosen house an inventory including
  its count positions, all in one transaction.
- **Reading in the target stock** ('target_stock'): writes at once what fits
  and stores the rest as a question. The article import shows a plan
  beforehand because it creates master data. Here one number comes per
  article number. But not every number has a place: the number is unknown,
  the article inactive or discontinued. These rows remain as 'Sollzeile',
  and the clarification ('clarification') asks what should happen with them.
  Otherwise the file would have to be uploaded again after every creation of
  an article. **The last file applies**: what it no longer names drops away.
- **The target by hand** ('target_by_hand'), where no file names it.
- **Remarks** on an article ('notes'): note, "Target counts" and the
  difference by hand, from the website and from the app.
- **Result rows** for display ('results', with the numbers and colours of
  'display'). As long as counting goes on, they are computed from the count
  positions. After closing they come from the frozen 'Inventurergebnis'
  rows. That both yield the same row is on purpose: running and closed
  inventory have only one template.
"""

from .clarification import (
    KLAERUNG_NUR_IN_DER_VORBEREITUNG,
    NichtInDerVorbereitung,
    Sollklaerung,
    ohne_soll_ausscheiden,
    sollklaerung,
    sollzeile_aktivieren,
    sollzeile_dazurechnen,
    sollzeile_verwerfen,
    sollzeilen_erneut_zuordnen,
    sollzeilen_verwerfen,
)
from .creating import inventuren_anlegen
from .display import FEHLT, MEHR, PASST, differenz_klasse, menge_text, menge_text_mit_vorzeichen
from .notes import (
    NOTIZ_HOECHSTLAENGE,
    NOTIZ_ZU_LANG,
    UNVERAENDERT,
    Gesichert,
    InventurAbgeschlossen,
    NotizenVeraltet,
    VermerkVeraltet,
    bemerkungsstand,
    bemerkungsstand_wie,
    ist_bemerkungsstand,
    notizen_speichern,
    vermerk_aendern,
)
from .results import (
    Ergebniszeile,
    betroffene_hausartikel,
    ergebniszeile,
    ergebniszeilen,
    zaehlstand,
)
from .target_by_hand import (
    Pflegezeile,
    Sollpflege,
    Sollpflegebericht,
    soll_von_hand_speichern,
    sollpflege,
)
from .target_stock import (
    NUR_IN_DER_VORBEREITUNG,
    OHNE_SOLL_VON_HAND,
    Sollbestandbericht,
    Zuordner,
    soll_darf_herein,
    sollbestand_einlesen,
)

__all__ = [
    "Ergebniszeile",
    "FEHLT",
    "Gesichert",
    "InventurAbgeschlossen",
    "KLAERUNG_NUR_IN_DER_VORBEREITUNG",
    "VermerkVeraltet",
    "MEHR",
    "NOTIZ_HOECHSTLAENGE",
    "NOTIZ_ZU_LANG",
    "NotizenVeraltet",
    "NUR_IN_DER_VORBEREITUNG",
    "NichtInDerVorbereitung",
    "OHNE_SOLL_VON_HAND",
    "PASST",
    "UNVERAENDERT",
    "Pflegezeile",
    "Sollbestandbericht",
    "Sollklaerung",
    "Sollpflege",
    "Sollpflegebericht",
    "Zuordner",
    "bemerkungsstand",
    "bemerkungsstand_wie",
    "betroffene_hausartikel",
    "differenz_klasse",
    "ergebniszeile",
    "ergebniszeilen",
    "inventuren_anlegen",
    "ist_bemerkungsstand",
    "menge_text",
    "menge_text_mit_vorzeichen",
    "notizen_speichern",
    "ohne_soll_ausscheiden",
    "soll_darf_herein",
    "soll_von_hand_speichern",
    "sollbestand_einlesen",
    "sollklaerung",
    "sollpflege",
    "sollzeile_aktivieren",
    "sollzeile_dazurechnen",
    "sollzeile_verwerfen",
    "sollzeilen_erneut_zuordnen",
    "sollzeilen_verwerfen",
    "vermerk_aendern",
    "zaehlstand",
]
