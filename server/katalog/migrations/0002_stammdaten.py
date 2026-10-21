"""Reference data the catalog presupposes.

Base units, the container "without container" and the product family "Not
countable" are no master data someone maintains but constants of the data
model: 'netto_in_basiseinheit' computes against the units, every weighing
offers "without container", and which articles are not counted hangs on the
family's flag. That is why they come from the migration and are protected
against deletion.

Computed is in the two reference units gram and millilitre. They therefore
carry the factor 1, and their decimal places are 0. Everything else converts
there via 'faktor_zur_referenz'. The portion (09/2026) is its own reference.

The name is the value from the WinTICKET export, by it a backup finds the unit
again. Displayed is the short form, and that hangs on the id. The id is unique,
which is why it has to arise here right away.

The first inventory type is reference data too: a fresh database counts in
the "Monatsinventur" until someone adds a type. Until 10/2026 it arose in the
data migration that introduced the types; since the squash it arises here.
"""

from django.conf import settings
from django.db import migrations
from django.utils import translation
from django.utils.translation import gettext

BASISEINHEITEN = [
    # 'name', 'kennung', 'nachkommastellen', 'dimension', 'faktor_zur_referenz'
    ("Stück(e)", "STUECK", 0, "STUECK", "1"),
    ("g", "GRAMM", 0, "MASSE", "1"),
    ("kg", "KILOGRAMM", 3, "MASSE", "1000"),
    ("Milliliter", "MILLILITER", 0, "VOLUMEN", "1"),
    ("Liter", "LITER", 3, "VOLUMEN", "1000"),
    # Only a product family sums in it. No product is carried in portions.
    ("Portion", "PORTION", 0, "PORTION", "1"),
]

OHNE_BEHAELTER = "Ohne Behälter (Waage genullt)"

NICHT_ZAEHLBAR = "Nicht zählbar"
NICHT_ZAEHLBAR_BESCHREIBUNG = (
    "Was Compeso als Artikel führt, ohne dass es eine Ware ist: Pfand, Gutscheine, "
    "Rabatte, Menü-Buttons. Waren dieser Familie sind in jedem Haus nicht zählbar."
)


def stammdaten_anlegen(apps, schema_editor):
    Basiseinheit = apps.get_model("katalog", "Basiseinheit")
    for name, kennung, nachkommastellen, dimension, faktor in BASISEINHEITEN:
        Basiseinheit.objects.update_or_create(
            name=name,
            defaults={
                "kennung": kennung,
                "nachkommastellen": nachkommastellen,
                "dimension": dimension,
                "faktor_zur_referenz": faktor,
            },
        )

    # Exactly one container carries 'immer_erlaubt', exactly one family
    # 'sammelt_ignorierte'. The partial indexes enforce that.
    Behaelter = apps.get_model("katalog", "Behaelter")
    Behaelter.objects.update_or_create(
        immer_erlaubt=True,
        defaults={"name": OHNE_BEHAELTER, "tara": "0"},
    )
    Warenfamilie = apps.get_model("katalog", "Warenfamilie")
    Warenfamilie.objects.get_or_create(
        sammelt_ignorierte=True,
        defaults={"name": NICHT_ZAEHLBAR, "beschreibung": NICHT_ZAEHLBAR_BESCHREIBUNG},
    )
    Inventurart = apps.get_model("katalog", "Inventurart")
    if not Inventurart.objects.exists():
        with translation.override(settings.LANGUAGE_CODE):
            Inventurart.objects.create(name=gettext("Monatsinventur"), reihenfolge=1)


def stammdaten_entfernen(apps, schema_editor):
    apps.get_model("katalog", "Inventurart").objects.all().delete()
    apps.get_model("katalog", "Warenfamilie").objects.filter(sammelt_ignorierte=True).delete()
    apps.get_model("katalog", "Behaelter").objects.filter(immer_erlaubt=True).delete()
    apps.get_model("katalog", "Basiseinheit").objects.filter(
        name__in=[name for name, *_ in BASISEINHEITEN]
    ).delete()


class Migration(migrations.Migration):
    dependencies = [("katalog", "0001_initial")]
    operations = [migrations.RunPython(stammdaten_anlegen, stammdaten_entfernen)]
