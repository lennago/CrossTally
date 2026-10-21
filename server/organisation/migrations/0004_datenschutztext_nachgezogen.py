"""The bundled privacy text, carried into the versions that still hold an older one.

The file is the default of a new version and nothing more: a version keeps
the text it was created with. The audit of 2026-10-16 found the text short
of what the application stores (passkeys, the decoy under '/admin/', the
mails about the account, the web server's log) and wrong about two periods.
Every version whose text is still one of the bundled texts since 2026-09-22
gets the current one, per language. A text someone edited stays as it is.

Compared by hash, after the line endings a browser form sends are
normalised. The next change of the files needs the hash of the then-old
text here and a migration like this one.

Alongside: the help texts of three fields say that the English field
stands in for an empty German one, and the lead unit's text no longer
claims it has to be one of the family's units.
"""

import hashlib

import organisation.privacy_text
from django.db import migrations, models

#: sha256 of every bundled text so far, per field: line endings normalised, stripped.
MITGELIEFERT = {
    "datenschutztext": {
        "3d1dedadd119173ade994991d93894bcac384ce6712482ec7244c6a536af3187",  # 2026-09-22
        "7667e0394e692d004840f4961373ecae5e9b75c438631f87cd58cacce4d2f0a0",  # 2026-09-28
        "1ef95f1d086e2642ab7eb0000c5aef32b75fd1b898e0431bdf24487a20f94f08",  # 2026-10-09
        "e702b4f3a8b480c6f4275a85e6b9d4bb87d7dfcd36c41e51d2e444b173eaf9df",  # 2026-10-12
        "31195b7a3653370417d138a97c138a71f9414f8fd56bbb898de9050fb00bcdbc",  # 2026-10-13
    },
    "datenschutztext_en": {
        "36363738f6eb5e129d21a8d19f3aa2e27ff6c41229318860bc3cadb497623819",  # 2026-09-22
        "71786c1ac91578e689f5f01a71f8551a565a4dc40ccdab8cc72aa786b73927ce",  # 2026-09-28
        "e83392aea97dd7e802321c4fd943a211d732c2ea55575925ed71a96063085c19",  # 2026-10-12
        "c6dcd5ff78b49ec9fdedd423602a5c34fb8c53a055177021c18d510f867cb913",  # 2026-10-13
        "051d14255bbcb46895a496bdc3284080080b9eb07cf895aaa39fc7d9d226efcb",  # 2026-10-14
    },
}

SPRACHE = {"datenschutztext": "de", "datenschutztext_en": "en"}


def pruefsumme(text: str) -> str:
    return hashlib.sha256(text.replace("\r\n", "\n").strip().encode("utf-8")).hexdigest()


def nachziehen(apps, schema_editor):
    Erscheinungsbild = apps.get_model("organisation", "Erscheinungsbild")
    for fassung in Erscheinungsbild.objects.all():
        felder = [
            feld
            for feld, alte in MITGELIEFERT.items()
            if pruefsumme(getattr(fassung, feld)) in alte
        ]
        for feld in felder:
            setattr(
                fassung,
                feld,
                organisation.privacy_text.mitgelieferter_datenschutztext(SPRACHE[feld]),
            )
        if felder:
            fassung.save(update_fields=felder)


class Migration(migrations.Migration):
    dependencies = [
        ("organisation", "0003_erscheinungsbild_rueckmeldeadresse"),
    ]

    operations = [
        migrations.AlterField(
            model_name="erscheinungsbild",
            name="datenschutztext",
            field=models.TextField(
                blank=True,
                default=organisation.privacy_text.datenschutztext_de,
                help_text="Vorbelegt mit dem mitgelieferten Text. Ergänzen, ändern oder ganz ersetzen. Leer lassen nimmt die Seite „Datenschutz“ samt Verweis heraus, sobald auch das englische Feld leer ist. Steht nur dort ein Text, sehen ihn alle. Eine Leerzeile trennt Absätze, ein einfacher Zeilenumbruch bleibt stehen, „## “ beginnt eine Überschrift, „- “ einen Aufzählungspunkt, **so** schreibt fett.",
                verbose_name="Datenschutztext (Deutsch)",
            ),
        ),
        migrations.AlterField(
            model_name="erscheinungsbild",
            name="impressum",
            field=models.TextField(
                blank=True,
                help_text="Steht auf einer eigenen Seite „Impressum“, verlinkt im Fuß jeder Seite und im Kontomenü der Zähl-App. Leer lassen heißt: kein Impressum, sobald auch das englische Feld leer ist. Steht nur dort ein Text, sehen ihn alle. Dasselbe Format wie beim Datenschutztext: eine Leerzeile trennt Absätze, ein einfacher Zeilenumbruch bleibt stehen, „## “ beginnt eine Überschrift, „- “ einen Aufzählungspunkt, **so** schreibt fett.",
                verbose_name="Impressum (Deutsch)",
            ),
        ),
        migrations.RunPython(nachziehen, migrations.RunPython.noop),
    ]
