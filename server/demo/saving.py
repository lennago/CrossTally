"""Rows of the demo go through the model's own check before they are saved.

'objects.create()' skips 'clean()'. A rule that lives there (a factor the
family needs, a circle of families, an article that does not belong to the
location) would then not break the build but age silently. That is exactly
what DEMO.md rules out, so every row takes this way.
"""

from django.db import models


def angelegt[T: models.Model](satz: T) -> T:
    satz.full_clean()
    satz.save()
    return satz
