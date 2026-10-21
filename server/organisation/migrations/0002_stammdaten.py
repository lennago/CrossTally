"""The "Standard" version of the appearance.

The application brings it along itself: the shipped logo, no operator, the
bundled privacy text. It cannot be changed, only copied, and it is active
until someone chooses a copy. Like the base units in the catalog it is
reference data and no master data someone maintains.
"""

from django.db import migrations

STANDARD = "Standard"


def standard_anlegen(apps, schema_editor):
    Erscheinungsbild = apps.get_model("organisation", "Erscheinungsbild")
    if Erscheinungsbild.objects.filter(name=STANDARD).exists():
        return
    Erscheinungsbild.objects.create(
        name=STANDARD, aktiv=not Erscheinungsbild.objects.filter(aktiv=True).exists()
    )


def standard_entfernen(apps, schema_editor):
    apps.get_model("organisation", "Erscheinungsbild").objects.filter(name=STANDARD).delete()


class Migration(migrations.Migration):
    dependencies = [("organisation", "0001_initial")]
    operations = [migrations.RunPython(standard_anlegen, standard_entfernen)]
