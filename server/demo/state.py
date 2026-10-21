"""Whether the stock currently in place comes from 'manage.py demo'.

The marker is asked, not the data. Whoever deletes or renames a house in
the demo should not lose the banner, and whoever restores a real stock into
the same database should not keep it. Only a row of its own withstands both
('Demokennzeichen').
"""

from .models import Demokennzeichen


def ist_demobestand() -> bool:
    """One query on a table with at most one row."""
    return Demokennzeichen.objects.exists()


def kennzeichnen() -> None:
    """Sets the marker. The build calls it, nobody else."""
    Demokennzeichen.objects.get_or_create(pk=1)


def kennzeichen_entfernen() -> int:
    """Takes it away again. Part of the cleanup."""
    return Demokennzeichen.objects.all().delete()[0]
