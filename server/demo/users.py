"""The four accounts of the demo: one for each role, and one still waiting for its invitation.

The passwords are in plain text in DEMO.md. That is right here: the stock
is invented, and a demo whose login nobody knows is none. In a real
database the command has no business, and it protects itself against that.
"""

from accounts.models import CustomUser
from organisation.models import Haus

from .houses import NORD, OST
from .names_en import Namen

PASSWORT = "demo1234"

#: The account that was created without a password. It shows the state
#: "invited" in the user list, and its page offers to send the invitation.
EINGELADEN = "eingeladen"

#: Id, first name, last name, manager, administrator.
KONTEN: list[tuple[str, str, str, bool, bool]] = [
    ("admin", "Alex", "Amsel", True, True),
    ("leitung", "Leonie", "Lerche", True, False),
    ("zaehler", "Zoe", "Zeisig", False, False),
    (EINGELADEN, "Nele", "Nachtigall", False, False),
]


def konten_anlegen(haeuser: dict[str, Haus], namen: Namen) -> dict[str, CustomUser]:
    """Administrator, manager, counter, and one that is invited.

    Accounts are created through 'CustomUser.objects', not through
    'get_user_model()'. That would only give the type checker 'AbstractBaseUser'.

    The counter is assigned to both populated houses. The assignment suggests
    who could count when creating an inventory. Who really may count is on
    the inventory itself.

    The invited one arises the way the user form creates it without a
    password: no usable password, switched off. No mail goes out here, the
    button on its page sends one.

    The login names stay German: they are credentials and stand so in
    DEMO.md. The accounts speak the language of the stock, so the demo is in
    it right after signing in.
    """
    konten = {}
    for kennung, vorname, nachname, leitung, admin in KONTEN:
        eingeladen = kennung == EINGELADEN
        konto = CustomUser.objects.create_user(
            username=kennung,
            password=None if eingeladen else PASSWORT,
            first_name=namen(vorname),
            last_name=namen(nachname),
            email=f"{kennung}@example.org",
            is_staff=leitung,
            is_superuser=admin,
            is_active=not eingeladen,
            sprache=namen.sprache,
        )
        konten[kennung] = konto
    konten["zaehler"].haeuser.set([haeuser[NORD], haeuser[OST]])
    return konten
