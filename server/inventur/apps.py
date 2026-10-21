from django.apps import AppConfig
from django.db.models.signals import pre_save
from django.utils.translation import gettext_lazy as _


def ergebnis_einheit_nachtragen(sender, instance, **kwargs) -> None:
    """A backup from before 10/2026 does not know the unit of a frozen result.

    Until then the numbers were read in the article's unit of the day. The
    backup brings the article as it stood then, and its unit is the value
    that applied. Without it the required field would run into "NOT NULL
    constraint failed". As a signal, because 'loaddata' writes past 'save()'
    ('katalog/apps.py', 'kennung_nachtragen').

    A whole backup has its results before the house articles, so here the
    article is not there yet. Restoring therefore writes the unit into the
    file beforehand ('django_project/backup_upgrade.py'). This stays for
    what arrives past that, a part of a backup into an existing catalog.
    """
    if instance.einheit_id is not None or instance.hausartikel_id is None:
        return
    from katalog.models import Hausartikel  # noqa: PLC0415  (the apps are not ready at import)

    werte = (
        Hausartikel.objects.filter(pk=instance.hausartikel_id)
        .values_list("basiseinheit_id", "faktor_zur_ware")
        .first()
    )
    if werte is not None:
        instance.einheit_id, instance.faktor_zur_ware = werte


class InventurConfig(AppConfig):
    name = "inventur"
    verbose_name = _("Inventur")

    def ready(self) -> None:
        from .models import Inventurergebnis  # noqa: PLC0415  (the apps are not ready at import)

        pre_save.connect(
            ergebnis_einheit_nachtragen,
            sender=Inventurergebnis,
            dispatch_uid="inventurergebnis_einheit",
        )
