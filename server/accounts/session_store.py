"""The store of the website's sessions: Django's own, with the account in a column.

Django's database store writes key, content and expiry. This one writes the
account as well ('Sitzung.konto'), read from the content on every save under
the key where Django's sign-in keeps it ('SESSION_KEY'). So the column says
what the content says: a sign-in sets it, also under the new key it gets
('cycle_key'), signing out removes the row, and a visitor who is not signed
in has none. Ending an account's sessions is a lookup by the column
('sign_out.sitzungen_beenden'). The settings name this module
('SESSION_ENGINE'), and Django's 'clearsessions' works through it.

The class keeps Django's name, and with it Django's salt for the content
('key_salt'). What Django's store wrote reads here unchanged, and the other
way round ('accounts/0003').
"""

from typing import TYPE_CHECKING, Any

from django.contrib.auth import SESSION_KEY
from django.contrib.sessions.backends import db
from django.core.exceptions import ValidationError

from .models import CustomUser, Sitzung

if TYPE_CHECKING:
    # The stubs make Django's store generic over its model, Django itself does not.
    Datenbankspeicher = db.SessionStore[Sitzung]
else:
    Datenbankspeicher = db.SessionStore


def kontonummer(daten: dict[str, Any]) -> int | None:
    """The signed-in account's number, read as Django's sign-in reads it, or None."""
    try:
        return CustomUser._meta.pk.to_python(daten.get(SESSION_KEY))
    except ValidationError:
        return None


class SessionStore(Datenbankspeicher):
    @classmethod
    def get_model_class(cls) -> type[Sitzung]:
        return Sitzung

    def create_model_instance(self, data: dict[str, Any]) -> Sitzung:
        sitzung = super().create_model_instance(data)
        sitzung.konto_id = kontonummer(data)
        return sitzung

    async def acreate_model_instance(self, data: dict[str, Any]) -> Sitzung:
        sitzung = await super().acreate_model_instance(data)
        sitzung.konto_id = kontonummer(data)
        return sitzung
