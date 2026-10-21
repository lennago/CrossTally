"""The unit of a frozen result is required, now that every row carries one ('0002').

A migration of its own: PostgreSQL does not alter a table whose rows were
changed in the same transaction, and '0002' filled them.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("inventur", "0002_einheit_am_ergebnis"),
    ]

    operations = [
        migrations.AlterField(
            model_name="inventurergebnis",
            name="einheit",
            field=models.ForeignKey(
                help_text="So hat das Haus beim Abschluss gezählt. Die Einheit bleibt, auch wenn das Haus später anders zählt.",
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to="katalog.basiseinheit",
                verbose_name="Einheit im Haus",
            ),
        ),
    ]
