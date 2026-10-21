"""The frozen result keeps the unit the house counted in, and its factor to the product.

Until 10/2026 a result was read in the article's unit of the day. A house
that changed its unit afterwards found its old numbers in the new one: 10
pieces became 10 kg on the sheet. Every existing result gets the unit and
the factor its article carries today. Exactly that was its reading so far,
so nothing changes for it.

The field becomes required in '0003'. PostgreSQL does not alter a table
whose rows were changed in the same transaction, so the filling stays in a
migration of its own.

Backwards a row frozen in another unit than its article carries by then
converts its numbers into the article's, because that is how the code before
reads them. Then the two fields go.
"""

from decimal import ROUND_HALF_UP, Decimal

import django.db.models.deletion
from django.db import migrations, models
from django.db.models import F, OuterRef, Subquery

MENGEN = ("gesamtmenge_basiseinheit", "sollbestand", "differenz")


def einheit_eintragen(apps, schema_editor):
    Inventurergebnis = apps.get_model("inventur", "Inventurergebnis")
    Hausartikel = apps.get_model("katalog", "Hausartikel")
    artikel = Hausartikel.objects.filter(pk=OuterRef("hausartikel_id"))
    Inventurergebnis.objects.filter(einheit__isnull=True).update(
        einheit_id=Subquery(artikel.values("basiseinheit_id")[:1]),
        faktor_zur_ware=Subquery(artikel.values("faktor_zur_ware")[:1]),
    )


def in_artikeleinheit(apps, schema_editor):
    """Rows without a way into the article's unit keep their numbers. There is no better one."""
    Inventurergebnis = apps.get_model("inventur", "Inventurergebnis")
    geaendert = []
    for ergebnis in (
        Inventurergebnis.objects.exclude(einheit__isnull=True)
        .exclude(einheit_id=F("hausartikel__basiseinheit_id"))
        .select_related("einheit", "hausartikel__basiseinheit", "hausartikel__ware__basiseinheit")
    ):
        verhaeltnis = _verhaeltnis(ergebnis)
        if verhaeltnis is None:
            continue
        for feld in MENGEN:
            if (wert := getattr(ergebnis, feld)) is not None:
                # Three places, half up, as at closing ('_eingefroren').
                wert = (wert * verhaeltnis).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
                setattr(ergebnis, feld, wert)
        geaendert.append(ergebnis)
    Inventurergebnis.objects.bulk_update(geaendert, MENGEN)


def _verhaeltnis(ergebnis):
    """How many units of the article one unit of the result is, or None without a way."""
    einheit, artikel = ergebnis.einheit, ergebnis.hausartikel
    if einheit.dimension == artikel.basiseinheit.dimension:
        return einheit.faktor_zur_referenz / artikel.basiseinheit.faktor_zur_referenz
    ware = artikel.ware.basiseinheit
    von = _zur_ware(einheit, ware, ergebnis.faktor_zur_ware)
    nach = _zur_ware(artikel.basiseinheit, ware, artikel.faktor_zur_ware)
    return None if von is None or nach is None else von / nach


def _zur_ware(einheit, ware, faktor):
    """'katalog.models.umrechnung' as of this migration. Historical models carry no functions."""
    if einheit.dimension == ware.dimension:
        return einheit.faktor_zur_referenz / ware.faktor_zur_referenz
    return faktor if faktor and faktor > 0 else None


class Migration(migrations.Migration):
    dependencies = [
        ("inventur", "0001_initial"),
        ("katalog", "0004_gedaechtnis_fuer_den_rueckweg"),
    ]

    operations = [
        migrations.AddField(
            model_name="inventurergebnis",
            name="einheit",
            field=models.ForeignKey(
                help_text="So hat das Haus beim Abschluss gezählt. Die Einheit bleibt, auch wenn das Haus später anders zählt.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to="katalog.basiseinheit",
                verbose_name="Einheit im Haus",
            ),
        ),
        migrations.AddField(
            model_name="inventurergebnis",
            name="faktor_zur_ware",
            field=models.DecimalField(
                blank=True,
                decimal_places=12,
                max_digits=18,
                null=True,
                verbose_name="Umrechnung zur Ware",
            ),
        ),
        migrations.RunPython(einheit_eintragen, in_artikeleinheit),
    ]
