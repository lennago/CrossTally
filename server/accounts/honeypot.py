"""The decoy under '/admin/': the same form as the real login, and the attempts noted.

Taken over from django-admin-honeypot 1.2.0 (MIT, Derek Payton and blag, the
text is 'accounts/LICENSE-admin-honeypot.txt'), because the package had no
maintainer since 2021 and the Django it was written for is gone. Kept is
the idea: every address under '/admin/' answers with a login form that lets
nobody in, and an attempt leaves a row ('Anmeldeversuch'), up to ten per
sender and hour ('aufzeichnen'). Gone are the
mail to 'ADMINS' on every attempt (nobody is listed there), the package's
own template, which imitated the Django admin, and its admin page.

Whoever tries '/admin/' should see nothing from which one could tell that
something other than the login at the front lies behind this address.
Hence the same template ('components/login_card.html') and the same form
properties, down to the character. A wrong password counts as at the real
login, under the same bolt ('fehlversuch'). The passkey button asks the
same question as the real one, checks the answer as far as the real one
does before the signature, and then says no.
"""

from datetime import timedelta

from axes.handlers.proxy import AxesProxyHandler
from axes.helpers import get_client_ip_address, get_credentials
from django.contrib.auth import REDIRECT_FIELD_NAME
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.hashers import make_password
from django.contrib.auth.signals import user_login_failed
from django.contrib.auth.views import redirect_to_login
from django.shortcuts import redirect
from django.urls import path, re_path, reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.cache import never_cache
from django.views.generic import FormView

from .lockout import gekappt
from .models import Anmeldeversuch
from .passkey import (
    ABGELAUFEN,
    FEHLGESCHLAGEN,
    PasskeyFehler,
    als_json,
    anmeldeoptionen,
    challenge_holen,
    challenge_merken,
    fehler,
    gelesen,
    vor_der_signatur,
)

app_name = "honeypot"

#: The decoy's challenge lives under its own key: what it asked, the real
#: login never accepts.
ATTRAPPENSCHLUESSEL = "passkey_attrappe"

#: As much of browser identifier and address as is kept, like 'axes' does.
#: Uncut a single attempt stored about 12 KB, for 30 days (until 10/2026).
TEXTLAENGE = 255

#: This many attempts per sender and hour are recorded. The pattern shows
#: after a few, and beyond them a flood would only fill the database.
HOECHSTENS_JE_STUNDE = 10

#: What a NUL becomes in a record. PostgreSQL refuses a NUL in a text column.
ERSATZZEICHEN = "\ufffd"


def ohne_nul(text: str | None) -> str | None:
    """The text as it can be stored, every NUL replaced and so still visible.

    The real login refuses a name with a NUL at its field and shows the
    form. The decoy wrote it into its record and ended in a server error
    under PostgreSQL, and one request told it apart (until 10/2026). The
    headers carry none: 'gunicorn' refuses a request with one.
    """
    return text.replace("\x00", ERSATZZEICHEN) if isinstance(text, str) else text


def aufzeichnen(request, username: str | None) -> Anmeldeversuch | None:
    """One row per attempt, with the address the lockout trusts. None beyond the limit.

    Not the library's own guess at the address: anyone could put a header
    in that it believes ('AttrappenPasskeyAnmeldung' says why). The name is
    cut as at the real login ('lockout.gekappt'). Longer than the column, it
    ended in a server error under PostgreSQL, and the decoy gave itself away
    (until 10/2026). So did a NUL ('ohne_nul'). The answer is the same with
    or without a row.

    **No session key.** The package recorded it, and so did this until
    10/2026. For a signed-in visitor, a manager who typed '/admin/', it was
    the key of the live session: shown in the administration, carried by
    every backup for 30 days, and set as a cookie it signed in as that
    person. Not a digest of it either. Address, browser and time show a
    pattern as well, and a digest would still single out a person's browser,
    which the privacy text does not name. The column stays empty
    ('accounts/0006' cleared the rows from before).
    """
    adresse = get_client_ip_address(request)
    seit = timezone.now() - timedelta(hours=1)
    if (
        Anmeldeversuch.objects.filter(ip_address=adresse, timestamp__gte=seit).count()
        >= HOECHSTENS_JE_STUNDE
    ):
        return None
    return Anmeldeversuch.objects.create(
        username=ohne_nul(gekappt(username)),
        ip_address=adresse,
        user_agent=(request.META.get("HTTP_USER_AGENT") or "")[:TEXTLAENGE],
        path=request.get_full_path()[:TEXTLAENGE],
    )


def fehlversuch(request, username: str, password: str) -> None:
    """Every password here is a wrong one, taken as 'authenticate()' takes one.

    The same steps in the same order, without looking for an account. First
    the bolt of 'axes', as 'AxesStandaloneBackend' asks it: whoever is
    locked out costs no hashing. Otherwise the password hasher runs once, as
    'ModelBackend' runs it for a name it does not know. Then the failure goes
    to 'axes' by the signal 'authenticate()' sends, under the same name and
    address as at the real login. Five of them lock both doors, as five at
    the real login do, and the middleware of 'axes' answers with the same
    page and 429 ('lockout.gesperrt'). Until 10/2026 the decoy neither
    hashed nor locked, and seven wrong passwords told it apart. Its own rows
    would not do for counting: they stop at 'HOECHSTENS_JE_STUNDE', and a
    lock at the decoy alone would leave the real login open, which a real
    admin behind the same sign-in does not.
    """
    credentials = get_credentials(username)
    if AxesProxyHandler.is_allowed(request, credentials):
        make_password(password)
    user_login_failed.send(sender=__name__, credentials=credentials, request=request)


class AttrappenForm(AuthenticationForm):
    """The real login's form, except that it never lets anyone in.

    It checks where the real one checks ('AuthenticationForm.clean'): only
    with a name and a password. Until 10/2026 it also called a form without
    a password a wrong password, where the real one only marks the field.
    """

    def clean(self):
        username = self.cleaned_data.get("username")
        password = self.cleaned_data.get("password")
        if username is not None and password:
            fehlversuch(self.request, username, password)
            # Whatever was typed: the same message the real form gives for a wrong password.
            raise self.get_invalid_login_error()
        return self.cleaned_data


# The real login carries 'never_cache'. Without it the decoy's headers told
# it apart with one request (until 10/2026).
@method_decorator(never_cache, name="dispatch")
class AttrappenAnmeldung(FormView):
    """Every address under '/admin/' leads to the form, and the form always says no."""

    template_name = "accounts/decoy_login.html"
    form_class = AttrappenForm

    def dispatch(self, request, *args, **kwargs):
        if not request.path.endswith("/"):
            return redirect(request.path + "/", permanent=True)
        # Django sends to its login view with 'next', so the decoy does the same.
        login_url = reverse("honeypot:login")
        if request.path != login_url:
            return redirect_to_login(request.get_full_path(), login_url)
        return super().dispatch(request, *args, **kwargs)

    def get_form(self, form_class=None):
        # Django's form takes the request first.
        return (form_class or self.get_form_class())(self.request, **self.get_form_kwargs())

    def get_context_data(self, **kwargs):
        # The real login only fills the field when someone came from a
        # protected page. A fixed value would thus be the tell.
        return super().get_context_data(**kwargs) | {REDIRECT_FIELD_NAME: ""}

    def form_valid(self, form):
        return self.form_invalid(form)

    def form_invalid(self, form):
        aufzeichnen(self.request, self.request.POST.get("username"))
        return super().form_invalid(form)


@method_decorator(never_cache, name="dispatch")
class AttrappenPasskeyOptionen(View):
    """The passkey button of the decoy asks the same question as the real one.

    The browser's dialog looks the same with and without a key, and the
    answer below is refused whatever it says. The challenge goes into the
    session like on the real login, so the responses do not differ in their
    cookies either.
    """

    def post(self, request):
        options = anmeldeoptionen(request)
        challenge_merken(request, options.challenge, ATTRAPPENSCHLUESSEL)
        return als_json(options)


@method_decorator(never_cache, name="dispatch")
class AttrappenPasskeyAnmeldung(View):
    """Records the attempt like a password and says no.

    What is recorded is the key's id, not a secret: the signature is over a
    challenge nobody accepts. The answers are those of the real check, in
    the same order: without a challenge "expired", then whatever the checks
    before the signature say ('vor_der_signatur'), and only after them
    "failed". Until 10/2026 the decoy always said "failed", and two requests
    told it apart from the real login. Then it had a copy of the checks, and
    that copy drifted.
    """

    def post(self, request):
        challenge = challenge_holen(request, ATTRAPPENSCHLUESSEL)
        # Whatever the request carries: a list instead of the browser's answer
        # is noted without an id, not answered with a server error.
        credential = gelesen(request).get("credential")
        kennung = str(credential.get("id") or "") if isinstance(credential, dict) else ""
        aufzeichnen(request, f"passkey:{kennung[:40]}")
        if challenge is None:
            return fehler(str(ABGELAUFEN))
        try:
            vor_der_signatur(credential)
        except PasskeyFehler as grund:
            return fehler(str(grund))
        return fehler(str(FEHLGESCHLAGEN))


#: The passkey endpoints come first and answer below every address of the
#: decoy: the script addresses them relative to the page it stands on.
urlpatterns = [
    re_path(
        r"^(?:.*/)?passkey/options/$", AttrappenPasskeyOptionen.as_view(), name="passkey_optionen"
    ),
    re_path(
        r"^(?:.*/)?passkey/anmelden/$", AttrappenPasskeyAnmeldung.as_view(), name="passkey_anmelden"
    ),
    path("login/", AttrappenAnmeldung.as_view(), name="login"),
    re_path(r"^.*$", AttrappenAnmeldung.as_view(), name="index"),
]
