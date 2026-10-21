"""The interface of the inventory workflow, for managers, not for counting.

Quantities are entered exclusively in the PWA. What stands here shows the
progress, the differences and the three steps preparation → count →
closing. Plus the target stock comes in, from the file or by hand.

Closing happens exclusively from the check view: whoever freezes should
have seen beforehand what is being frozen.

What stays on its page sends in the background
('static/js/instant_actions.js'), the status changes of an inventory too. In
addition: return via 'weiter' and 'zurueck_zu()', so a button from within the
list leads back there too.

The views lie in modules by subject. 'inventur/urls.py' and the tests
fetch them from here, nothing changes there when a view switches modules.
"""

from .base import melde_abschluss, mit_fortschritt
from .detail import (
    DifferenzVonHandView,
    InventurArtenView,
    InventurDetailView,
    InventurPdfView,
    SperreView,
    StatuswechselView,
    ZaehlendeView,
    ZaehlungStartenView,
    ZurueckInVorbereitungView,
    arten_reiter,
    nach_ort,
    positionszeilen,
    reihenfolge_der_app,
)
from .evaluation import AuswertungView
from .list import InventurCreateView, InventurDeleteView, InventurListView
from .review import InventurAbschlussView, InventurPruefenView, InventurZaehlstandView
from .run import (
    Lauf,
    LaufAbschlussView,
    LaufDeleteView,
    LaufMixin,
    LaufPdfView,
    LaufView,
    lauf_fortsetzen,
    lauf_reiter,
    lauf_weiter,
)
from .soll_edit import SollPflegeView
from .sollbestand import SollbestandKlaerungView, melde_sollbestand

__all__ = [
    "AuswertungView",
    "DifferenzVonHandView",
    "InventurAbschlussView",
    "InventurCreateView",
    "InventurDeleteView",
    "InventurDetailView",
    "InventurListView",
    "InventurPdfView",
    "InventurPruefenView",
    "InventurZaehlstandView",
    "Lauf",
    "LaufAbschlussView",
    "LaufDeleteView",
    "LaufMixin",
    "LaufPdfView",
    "LaufView",
    "SollPflegeView",
    "SollbestandKlaerungView",
    "SperreView",
    "StatuswechselView",
    "InventurArtenView",
    "ZaehlendeView",
    "ZaehlungStartenView",
    "ZurueckInVorbereitungView",
    "lauf_fortsetzen",
    "lauf_reiter",
    "lauf_weiter",
    "melde_abschluss",
    "melde_sollbestand",
    "mit_fortschritt",
    "nach_ort",
    "positionszeilen",
    "reihenfolge_der_app",
    "arten_reiter",
]
