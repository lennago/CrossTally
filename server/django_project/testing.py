"""What the tests of all apps share.

Almost every page test starts the same: create an account that gets
everywhere and sign it in. That stands here once, so the test classes start
with what distinguishes them.

Accounts are created through 'CustomUser.objects', not through
'get_user_model()'. That would only give the type checker 'AbstractBaseUser'.
"""

import re
from http import HTTPStatus

from django.conf import settings
from django.http import HttpResponseBase
from django.test import Client, TestCase

from accounts.models import CustomUser
from organisation.models import Bereich


def administrator(username: str = "chef", **abweichend) -> CustomUser:
    """Manager and administrator at once, gets onto every page."""
    angaben = {"password": "geheim", "is_staff": True, "is_superuser": True} | abweichend
    return CustomUser.objects.create_user(username=username, **angaben)


def fuehrungskraft(username: str = "leitung", **abweichend) -> CustomUser:
    """May use the web interface, but not the administration."""
    return administrator(username, is_superuser=False, **abweichend)


def bereich(haus, name: str = "Allgemein") -> Bereich:
    """A location needs an area. Where it does not matter, this one will do."""
    return Bereich.objects.get_or_create(haus=haus, name=name)[0]


def monatsinventur():
    """The type every database starts with, the monthly inventory of the migration."""
    from katalog.models import Inventurart  # noqa: PLC0415  (the catalog lies above)

    return Inventurart.erste()


def jahresinventur():
    """A second type, for what is only counted once a year. Created on first use."""
    from katalog.models import Inventurart  # noqa: PLC0415  (the catalog lies above)

    return Inventurart.objects.get_or_create(name="Jahresinventur")[0]


def inventur_anlegen(haus, *arten, **felder):
    """An inventory as most tests need it: without count positions, counting the given types.

    Without types it counts the first one, the monthly inventory. Creating
    with positions goes through 'inventur.services.inventuren_anlegen'.
    """
    from inventur.models import Inventur  # noqa: PLC0415  (the inventory lies above)

    inventur = Inventur.objects.create(haus=haus, **felder)
    inventur.inventurarten.set(arten or [monatsinventur()])
    return inventur


def ware_zusammenfuehren(client: Client, adresse: str, daten: dict) -> HttpResponseBase:
    """Merges two products the way the page does: the preview, then its button.

    The product's merge always shows a preview first and writes only for the
    choice the preview showed ('WareZusammenfuehrenView', 'gezeigt'). A test
    that only wants the result sends both steps through here. An objection
    of the form ends after the first one, and that answer comes back.
    """
    vorschau = client.post(adresse, daten)
    if vorschau.status_code != HTTPStatus.OK or "signatur" not in vorschau.context:
        return vorschau
    return client.post(
        adresse, daten | {"bestaetigt": "1", "gezeigt": vorschau.context["signatur"]}
    )


def skript(name: str) -> str:
    """The text of one of our scripts from 'static/js/'.

    The scripts are no longer in the page. A test that checks whether page and
    script fit together therefore looks in two places: the page includes
    'js/<name>.js', and the script looks for what the page offers.
    """
    return (settings.BASE_DIR / "static" / "js" / f"{name}.js").read_text(encoding="utf-8")


def stylesheet(name: str) -> str:
    """The text of a stylesheet from 'static/'.

    The web interface and the administration have separate files because the
    admin loads no Bootstrap. Whoever needs a class in both checks here that it
    is in both.
    """
    return (settings.BASE_DIR / "static" / name).read_text(encoding="utf-8")


def oeffnet_neuen_tab(antwort: HttpResponseBase, href: str) -> bool:
    """Whether the link to 'href' on the page carries 'target="_blank"'.

    The counting app and the PDFs open beside the page, not in its place.
    The attributes of an anchor may be spread over several lines, so the
    check goes over the tag and not over an exact string.
    """
    seite = getattr(antwort, "content", b"").decode()
    return re.search(rf'<a\s[^>]*href="{re.escape(href)}"[^>]*target="_blank"', seite) is not None


class AngemeldeteSeiten(TestCase):
    """Base for page tests: 'cls.user' is administrator and signed in in every test.

    Subclasses call 'super().setUpTestData()' first in 'setUpTestData'.
    """

    user: CustomUser

    @classmethod
    def setUpTestData(cls):
        cls.user = administrator()

    def setUp(self):
        self.client.force_login(self.user)
