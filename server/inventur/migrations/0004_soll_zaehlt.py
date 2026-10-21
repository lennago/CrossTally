"""The tick 'soll_statt_ist' is called "Soll zählt" in the administration too.

Only the label: the column stays as it is, and the database does nothing.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("inventur", "0003_einheit_am_ergebnis_pflicht"),
    ]

    operations = [
        migrations.AlterField(
            model_name="inventurnotiz",
            name="soll_statt_ist",
            field=models.BooleanField(
                default=False,
                help_text="Beim Abschluss gilt der Sollbestand als Ergebnis. Die Zählung bleibt unberührt.",
                verbose_name="Soll zählt",
            ),
        ),
    ]
