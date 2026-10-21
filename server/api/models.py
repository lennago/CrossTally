"""What the API keeps of its own: the questions the app's passkeys have open."""

from django.db import models
from django.utils.translation import gettext_lazy as _


class Passkeyfrage(models.Model):
    """A challenge the app was handed for a passkey and has not answered yet.

    The app has no session, so the challenge travels to it signed and comes
    back with the answer ('api/passkey.py'). A signature says the server
    asked, not that nobody answered yet. Until 10/2026 the same challenge and
    answer let the account in as often as they came within two minutes.
    Whoever saw the body of a request and not its answer (a proxy, a
    firewall, an extension of the browser) got a token pair of their own,
    and its tokens outlived the owner's sign-out in the app. One row per
    question now, and the first answer deletes it.

    Not in the backups ('django_project/backup.py'): a question holds two
    minutes and means nothing in yesterday's state.
    """

    #: The SHA-256 of the challenge, as hex: the same length whatever the
    #: challenge's. The challenge itself travels signed and is not kept here.
    pruefsumme = models.CharField(max_length=64, primary_key=True, verbose_name=_("Prüfsumme"))
    #: When it was asked. Older than 'GUELTIG_SEKUNDEN' it opens nothing, and
    #: the next question deletes it.
    gefragt_am = models.DateTimeField(db_index=True, verbose_name=_("gefragt am"))

    class Meta:
        verbose_name = _("offene Passkey-Frage")
        verbose_name_plural = _("offene Passkey-Fragen")

    def __str__(self) -> str:
        return self.pruefsumme
