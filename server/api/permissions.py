"""Who may use the API at all.

Two levels: **signed in and active** for everything, and **assigned to this
inventory** for everything that belongs to a particular inventory. The second
level is on the model ('Inventur.darf_zaehlen'), so the rule does not hang in
the API. It applies to every other path too.
"""

from django.utils.translation import gettext_lazy as _
from rest_framework.permissions import BasePermission


class IstAktiverBenutzer(BasePermission):
    """Signed in and not deactivated.

    The JWT check loads the user from the database on **every** request and
    rejects deactivated accounts ('CHECK_USER_IS_ACTIVE'). This here is the bolt
    in front that also applies with other authentication paths, such as the
    session the browsable DRF interface works with.
    """

    message = _("Dieses Konto ist deaktiviert.")

    def has_permission(self, request, view) -> bool:
        benutzer = request.user
        return bool(benutzer and benutzer.is_authenticated and benutzer.is_active)
