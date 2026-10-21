"""The one model of the demo: its marker.

It is in the database exactly when its stock comes from 'manage.py demo',
and the interface thereupon carries its banner. From the data itself that
cannot be read reliably: in the demo one should be allowed to try
everything, including deleting or renaming a house, and with the last demo
house the notice would otherwise vanish. A setting in the environment would
be the other option, but says something about the server and nothing about
the data currently in it.

The table stays empty in every other database. That is the whole price.
"""

from django.db import models
from django.utils.translation import gettext_lazy as _


class Demokennzeichen(models.Model):
    """A single row. If it is there, this stock is invented."""

    #: A fixed key instead of a running number. Two markers side by side would
    #: be two truths about the same database.
    id = models.PositiveSmallIntegerField(
        primary_key=True, default=1, editable=False, verbose_name=_("Kennung")
    )
    aufgebaut_am = models.DateTimeField(auto_now_add=True, verbose_name=_("aufgebaut am"))

    class Meta:
        verbose_name = _("Demokennzeichen")
        verbose_name_plural = _("Demokennzeichen")

    def __str__(self) -> str:
        return _("Demo-Bestand, aufgebaut am %(datum)s") % {
            "datum": f"{self.aufgebaut_am:%d.%m.%Y}"
        }
