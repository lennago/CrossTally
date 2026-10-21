"""The API for the counting PWA.

Meant for the counting app: what is pending, the counts and the review
list. Managers also lock the count, switch the difference by hand and set
"target counts" here, as on the website ('sperre', 'differenz_von_hand',
'pruefliste_zeile'). Status, target stock and closing stay with the web
interface.

Visible is exclusively what the user is **assigned** to on the inventory
('Inventur.fuer_zaehler'). This is the first place in the project where this
check really applies: in the web interface only managers arrive anyway,
here the counters sign in.

Spread over modules by subject: what the views share ('base'), the
inventories with their master data and check list ('inventory'), the writes
of the check list and the switches of managers ('review'), the count
positions and weighings ('counting') and the account ('account').
'api/urls.py' fetches them from here. Nothing changes there when a view
switches modules.
"""

from .account import (
    TokenErneuernView,
    TokenHolenView,
    abmelden,
    email_aendern,
    ich,
    passwort_aendern,
    token_aus_sitzung,
)
from .base import eingabe
from .counting import WiegungViewSet, ZaehlpositionViewSet
from .inventory import InventurViewSet
from .review import differenz_von_hand, pruefliste_zeile, sperre

__all__ = [
    "InventurViewSet",
    "TokenErneuernView",
    "TokenHolenView",
    "WiegungViewSet",
    "ZaehlpositionViewSet",
    "abmelden",
    "differenz_von_hand",
    "eingabe",
    "email_aendern",
    "ich",
    "passwort_aendern",
    "pruefliste_zeile",
    "sperre",
    "token_aus_sitzung",
]
