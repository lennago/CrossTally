from django.apps import AppConfig
from django.db.models.signals import pre_save
from django.utils.translation import gettext_lazy as _


def kennung_nachtragen(sender, instance, **kwargs) -> None:
    """A backup from before 09/2026 does not know the base unit's id.

    Without it every restore of such a state would run into the unique index:
    five units with an empty id. The name is in the backup, and through it the
    id is unambiguous.

    As a signal, because 'loaddata' writes past 'save()' ('save_base' with
    'raw=True'). This signal it sends nonetheless. That way it applies on both
    paths: through the administration and through the console.
    """
    from .models import KENNUNG_JE_NAME  # noqa: PLC0415

    if not instance.kennung:
        instance.kennung = KENNUNG_JE_NAME.get(instance.name, "")


def einheit_nachtragen(sender, instance, **kwargs) -> None:
    """A backup from before 09/2026 does not know the house's unit on the house article.

    Until then the unit was on the product alone. An older state brings
    nothing for the new required field and would run into "NOT NULL
    constraint failed". The product is in the backup, and its unit is the
    value that applied until then. The same path also fills it in where code
    creates a house article without a unit: a house counts, until someone says
    otherwise, in the unit of its product.

    As a signal for the same reason as 'kennung_nachtragen'.
    """
    instance.einheit_angleichen()


def inventurart_nachtragen(sender, instance, **kwargs) -> None:
    """A backup from before 10/2026 does not know the house article's inventory type.

    Until then the scope hung on the product, and restoring derives the type
    from it ('django_project/backup_upgrade.py'). What still comes without
    one, and every path that creates a house article without a form, gets
    the type the product's other articles agree on, otherwise the first.

    As a signal for the same reason as 'kennung_nachtragen'.
    """
    instance.inventurart_angleichen()


class KatalogConfig(AppConfig):
    name = "katalog"
    verbose_name = _("Katalog")

    def ready(self) -> None:
        from .models import Basiseinheit, Hausartikel  # noqa: PLC0415

        pre_save.connect(
            kennung_nachtragen, sender=Basiseinheit, dispatch_uid="basiseinheit_kennung"
        )
        pre_save.connect(einheit_nachtragen, sender=Hausartikel, dispatch_uid="hausartikel_einheit")
        pre_save.connect(
            inventurart_nachtragen, sender=Hausartikel, dispatch_uid="hausartikel_inventurart"
        )
