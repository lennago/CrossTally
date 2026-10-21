"""The decoy's records lose the session keys it wrote until 10/2026 ('Anmeldeversuch').

For a signed-in visitor the key was the one of the live session, and set as
a cookie it signed in as that person. The administration showed it, and
every backup carried it for 30 days. The decoy records none since
('honeypot.aufzeichnen'). The column stays, it is the table of
django-admin-honeypot, under its name and with its columns.

Backwards nothing comes back: the keys are gone, and bringing one back is
what this removes.
"""

from django.db import migrations


def leeren(apps, schema_editor):
    Anmeldeversuch = apps.get_model("accounts", "Anmeldeversuch")
    Anmeldeversuch.objects.exclude(session_key=None).update(session_key=None)


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0005_adresswechsel"),
    ]

    operations = [
        migrations.RunPython(leeren, migrations.RunPython.noop),
    ]
