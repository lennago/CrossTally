from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenVerifyView

from . import passkey
from .views import (
    InventurViewSet,
    TokenErneuernView,
    TokenHolenView,
    WiegungViewSet,
    ZaehlpositionViewSet,
    abmelden,
    differenz_von_hand,
    email_aendern,
    ich,
    passwort_aendern,
    pruefliste_zeile,
    sperre,
    token_aus_sitzung,
)
from .views.account import abmelden_nachgeholt

router = DefaultRouter()
router.register("inventuren", InventurViewSet, basename="inventur")
router.register("zaehlpositionen", ZaehlpositionViewSet, basename="zaehlposition")
router.register("wiegungen", WiegungViewSet, basename="wiegung")


urlpatterns = [
    # Not the package's version: see 'TokenHolenView'. Without it the lock
    # after too many failed attempts would run into nothing at this door.
    path("<str:version>/token/", TokenHolenView.as_view(), name="token_holen"),
    # Here too the own version: a token without an account is a 401, not a 500.
    path("<str:version>/token/erneuern/", TokenErneuernView.as_view(), name="token_erneuern"),
    path("<str:version>/token/aus-sitzung/", token_aus_sitzung, name="token_aus_sitzung"),
    path("<str:version>/abmelden/", abmelden, name="abmelden"),
    # The same sign-out the app owes, by the token's id alone, for a start without a session.
    path(
        "<str:version>/abmelden/nachgeholt/",
        abmelden_nachgeholt,
        name="abmelden_nachgeholt_api",
    ),
    path("<str:version>/token/pruefen/", TokenVerifyView.as_view(), name="token_pruefen"),
    path("<str:version>/ich/", ich, name="ich"),
    path("<str:version>/passwort/", passwort_aendern, name="passwort_aendern"),
    path("<str:version>/email/", email_aendern, name="email_aendern_api"),
    # Passkeys in the app: sign in without a password, manage the own keys.
    path(
        "<str:version>/passkey/anmelden/optionen/",
        passkey.anmelden_optionen,
        name="passkey_anmelden_optionen_api",
    ),
    path("<str:version>/passkey/anmelden/", passkey.anmelden_pruefen, name="passkey_anmelden_api"),
    path("<str:version>/passkey/", passkey.liste, name="passkey_liste_api"),
    path(
        "<str:version>/passkey/anlegen/optionen/",
        passkey.anlegen_optionen,
        name="passkey_anlegen_optionen_api",
    ),
    path("<str:version>/passkey/anlegen/", passkey.anlegen_pruefen, name="passkey_anlegen_api"),
    # Without a password one of the own keys confirms a new key or a new address.
    path(
        "<str:version>/passkey/bestaetigen/optionen/",
        passkey.bestaetigen_optionen,
        name="passkey_bestaetigen_optionen_api",
    ),
    path(
        "<str:version>/passkey/bestaetigen/",
        passkey.bestaetigen_pruefen,
        name="passkey_bestaetigen_api",
    ),
    path("<str:version>/passkey/<int:pk>/", passkey.loeschen, name="passkey_loeschen_api"),
    # The check list of the app: reading is an action of the inventory, writing
    # finds the inventory in every state so a waiting job learns why it cannot go.
    path(
        "<str:version>/inventuren/<int:pk>/pruefliste/<int:hausartikel>/",
        pruefliste_zeile,
        name="pruefliste_zeile",
    ),
    path(
        "<str:version>/inventuren/<int:pk>/differenz-von-hand/",
        differenz_von_hand,
        name="differenz_von_hand_api",
    ),
    path("<str:version>/inventuren/<int:pk>/sperre/", sperre, name="sperre_api"),
    path("<str:version>/", include(router.urls)),
]
