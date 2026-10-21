"""The help text of 'ean' no longer promises scanning in the app.

Only the label: the column stays as it is, and the database does nothing. The
form carried the right text since 10/2026, the administration now does too.
"""

import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("katalog", "0004_gedaechtnis_fuer_den_rueckweg"),
    ]

    operations = [
        migrations.AlterField(
            model_name="hausartikel",
            name="ean",
            field=models.CharField(
                blank=True,
                help_text="Optional. Die Suche in den Listen findet den Artikel auch darüber.",
                max_length=14,
                validators=[
                    django.core.validators.RegexValidator("^\\d+$", "Nur Ziffern erlaubt.")
                ],
                verbose_name="EAN",
            ),
        ),
    ]
