from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView, PasswordChangeView, PasswordResetConfirmView
from django.db import transaction
from django.http import HttpResponseRedirect
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    FormView,
    ListView,
    TemplateView,
    UpdateView,
)
from django.views.generic.detail import SingleObjectMixin

from django_project.columns import SpaltenMixin
from django_project.deletion import NurMitRueckfrage
from django_project.mail import mail_moeglich
from django_project.middleware import sprache_merken
from django_project.mixin_bases import EinzelansichtBasis
from django_project.roles import (
    bleibt_administrator,
    ist_administrator,
    letzter_administrator_geht,
)
from django_project.views import zurueck_zu

from .email_change import (
    BESTAETIGUNG_NICHT_GESENDET,
    BESTAETIGUNG_UNTERWEGS,
    AdresseVergeben,
    LinkVerbraucht,
    anwenden,
    bestaetigung_senden,
    einleiten,
    sofort,
    wechsel_aus,
)
from .forms import (
    KONTO_GELOESCHT,
    LETZTER_ADMINISTRATOR_BLEIBT,
    CustomUserChangeForm,
    CustomUserCreationForm,
    EmailAendernForm,
)
from .invitation import (
    einladung_senden,
    einladungen_entwerten,
    einladungs_token,
    gueltig_fuer_tage,
)
from .lockout import spuren_loeschen
from .models import Adresswechsel, CustomUser, Differenzfarben, Passkey, Sprache
from .notification import (
    Kontostand,
    aenderung_melden,
    loeschung_melden,
    passwort_melden,
    warnen_wenn_nicht_gesendet,
)
from .passkey import fehlende_bestaetigung
from .password_forms import ErstesPasswortForm, PasswortAendernForm
from .sign_out import gesperrtes_konto, ueberall_abmelden


class AdminsSindGeschuetztMixin(EinzelansichtBasis):
    """An administrator can only be changed by an administrator.

    All other users may also be managed by the managers. But an administrator
    is exactly the access through which the permission system could be
    undermined: whoever can edit or delete one can deactivate or take it over.
    Hence one more level here.

    Asked when the request begins, and once more by the step that writes,
    under the account's lock ('geschuetzt'). An account promoted in between
    took a manager's edit or deletion (until 10/2026).
    """

    def dispatch(self, request, *args, **kwargs):
        if self.geschuetzt(self.get_object()):
            return self.abweisen()
        return super().dispatch(request, *args, **kwargs)

    def geschuetzt(self, konto: CustomUser) -> bool:
        """Whether the account is an administrator and whoever asks is none."""
        return konto.is_superuser and not ist_administrator(self.request.user)

    def abweisen(self):
        return render(self.request, "accounts/no_access.html", status=403)


class SprachwahlView(View):
    """Sets the language on the account and returns to where the button was.

    A form and not a script: the page is built server-side in the chosen
    language, so it has to be reloaded anyway. That way the switch also works
    without JavaScript, and the Content-Security-Policy has nothing to block.

    The choice applies at once everywhere, in the counting app too: it hangs on
    the account and not on the device. For whoever has no account yet, the
    browser remembers it ('sprache_merken'): that way the login form including
    "Forgot your password" stays in the chosen language ('SprachwahlMiddleware').
    """

    def post(self, request, *args, **kwargs):
        gewaehlt = request.POST.get("sprache", "")
        antwort = redirect(zurueck_zu(request, reverse("home")))
        if gewaehlt in Sprache.values:
            if request.user.is_authenticated:
                request.user.sprache = gewaehlt
                request.user.save(update_fields=["sprache"])
            else:
                sprache_merken(antwort, gewaehlt)
        return antwort


class FarbwahlView(LoginRequiredMixin, View):
    """Sets the colours of the difference on the account, like the language next to it.

    On the account and not on the application, because colour vision is
    personal. A form and not a script, for the same reason as with the language:
    the choice sits as a class in the page the server builds.
    """

    def post(self, request, *args, **kwargs):
        gewaehlt = request.POST.get("farben", "")
        if gewaehlt in Differenzfarben.values:
            request.user.differenzfarben = gewaehlt
            request.user.save(update_fields=["differenzfarben"])
        return redirect(zurueck_zu(request, reverse("home")))


class AnmeldenView(LoginView):
    """Django's sign-in with a password, the account let in under its lock.

    Django checks the password and the tick "Active" and signs in. The
    middleware saves the session only after the response. A deactivation in
    between ended every session of the account but this one, which did not
    name the account yet, and switched on again within two weeks the
    account found it valid (until 10/2026). So the account's row is locked
    without its key and asked once more: still active, and still the
    password just checked. The session is saved under the lock, as for a
    passkey ('accounts.passkey.anmelden'), and a deactivation that comes
    after it ends it as well. Refused, the form says what it says for a
    wrong password: whoever stands at it learns nothing a guess would not.

    Its route belongs in front of Django's own ('accounts/login/'), in
    'django_project/urls.py'.
    """

    def form_valid(self, form):
        angemeldet = form.get_user()
        with transaction.atomic():
            konto = gesperrtes_konto(angemeldet.get_username())
            if konto is None or not konto.is_active or konto.password != angemeldet.password:
                form.add_error(None, form.get_invalid_login_error())
                return self.form_invalid(form)
            auth_login(self.request, angemeldet)
            # Saved here, under the lock. One that comes after this and before the
            # middleware leaves it nothing to save: Django answers 400
            # ('SessionInterrupted'), and nobody is signed in.
            self.request.session.save()
        return HttpResponseRedirect(self.get_success_url())


class KeinZugriffView(LoginRequiredMixin, TemplateView):
    """Where counters land who open the web interface.

    An address of its own, because the middleware **redirects** here: that way
    the same page is always in the bar, whatever address someone tried, and
    whether it exists at all it does not give away either.
    """

    template_name = "accounts/no_access.html"


class UserCreateView(CreateView):
    form_class = CustomUserCreationForm
    success_url = reverse_lazy("user_list")
    template_name = "accounts/user_add.html"

    def get_initial(self):
        # Whoever creates the account most likely shares its language.
        return super().get_initial() | {"sprache": getattr(self.request.user, "sprache", None)}

    def form_valid(self, form):
        """Without a password the mail goes out, and the message says so."""
        assert isinstance(form, CustomUserCreationForm)
        konto: CustomUser = form.save()
        self.object = konto
        if form.einladen:
            if not einladung_senden(konto, self.request):
                # To the account's page: the button to send it again is there.
                messages.warning(
                    self.request,
                    _(
                        "„%(name)s“ ist angelegt. Die Einladung ging nicht hinaus, der "
                        "E-Mail-Server antwortet nicht. Hier lässt sie sich erneut senden."
                    )
                    % {"name": konto},
                )
                return redirect(konto.get_absolute_url())
            messages.success(
                self.request,
                _("„%(name)s“ ist angelegt. Die Einladung ist an %(email)s unterwegs.")
                % {"name": konto, "email": konto.email},
            )
        return redirect(self.get_success_url())


class EinladungView(PasswordResetConfirmView):
    """The link from the invitation mail: set the password, and the account is active.

    Django's confirm view does the work: it checks the token, moves it into
    the session so it does not leak through the referer, and offers the form.
    Different are the token (its own salt), the page and what happens on
    success: the account is switched on ('ErstesPasswortForm'). Nobody is
    signed in here. Whoever counts belongs in the app, and the done page says
    where to go.

    Only an account that is still waiting for its password opens. For every
    other one the link is dead, like a used one: whoever has a password and
    forgot it takes "Forgot your password".
    """

    token_generator = einladungs_token
    form_class = ErstesPasswortForm
    template_name = "accounts/invitation.html"
    success_url = reverse_lazy("einladung_fertig")
    title = _("Passwort setzen")

    def get_user(self, uidb64):
        benutzer = super().get_user(uidb64)
        return benutzer if isinstance(benutzer, CustomUser) and benutzer.ist_eingeladen else None


class PasswortAendernView(PasswordChangeView):
    """Django's page for the own password, with the old one counted like a sign-in.

    Django hands its form only the account. Ours asks through
    'authenticate()' and needs the request for it ('PasswortAendernForm').
    """

    form_class = PasswortAendernForm

    def get_form_kwargs(self):
        return super().get_form_kwargs() | {"request": self.request}


class GemeldetePasswordChangeView(PasswortAendernView):
    """The page for the own password, and a mail afterwards.

    Whoever did not change it learns this way that someone else knows the old one.
    The administration's page for the own password is this one as well
    ('VerwaltungsAdminSite.password_change'). The mail says "website" there
    too, for the administration is part of it: "verwaltung" means the
    password of another account ('passwort_melden').
    """

    def form_valid(self, form):
        antwort = super().form_valid(form)
        konto = form.user
        assert isinstance(konto, CustomUser)
        passwort_melden(konto, "website")
        return antwort


class EinladungFertigView(TemplateView):
    """After the password is set: where to sign in now, depending on the role."""

    template_name = "accounts/invitation_done.html"


#: Where the form leaves what the done page says, for one request.
WECHSEL = "email_wechsel"
BESTAETIGT = "email_bestaetigt"


class EmailAendernView(LoginRequiredMixin, FormView):
    """The own address, from the account menu, for counters too.

    With a mail server the new address gets the mail with the link and
    applies once it is confirmed. Without one it applies now. A server that
    does not answer keeps the form open and says so: the person is here and
    can try again, unlike a change made on their account by someone else.

    An account without a usable password confirms with one of its passkeys
    on this page first ('PasskeyBestaetigenView'). The form learns from here
    whether that holds.
    """

    form_class = EmailAendernForm
    template_name = "accounts/email_change.html"
    success_url = reverse_lazy("email_fertig")

    def get_form_kwargs(self):
        konto = self.request.user
        assert isinstance(konto, CustomUser)
        unbestaetigt = (
            "" if konto.has_usable_password() else fehlende_bestaetigung(self.request, konto)
        )
        return super().get_form_kwargs() | {
            "konto": konto,
            "request": self.request,
            "unbestaetigt": unbestaetigt,
        }

    def get_context_data(self, **kwargs):
        konto = self.request.user
        assert isinstance(konto, CustomUser)
        return super().get_context_data(**kwargs) | {
            "bestaetigung": not sofort(konto),
            "tage": gueltig_fuer_tage(),
            # Without a password the page offers the confirmation, or says who helps.
            "hat_passkey": Passkey.objects.filter(benutzer=konto).exists(),
        }

    def form_valid(self, form):
        konto = self.request.user
        assert isinstance(konto, CustomUser)
        neu = form.cleaned_data["email"]
        ergebnis = einleiten(konto, neu, self.request)
        if ergebnis.bestaetigung and ergebnis.gesendet is False:
            form.add_error(None, BESTAETIGUNG_NICHT_GESENDET % {"email": neu})
            return self.form_invalid(form)
        self.request.session[WECHSEL] = {"email": neu, "bestaetigung": ergebnis.bestaetigung}
        return super().form_valid(form)


class EmailWechselFertigView(LoginRequiredMixin, TemplateView):
    """After the form: the mail is out, or the address applies already."""

    template_name = "accounts/email_change_done.html"

    def get(self, request, *args, **kwargs):
        wechsel = request.session.pop(WECHSEL, None)
        if not wechsel:
            return redirect("email_aendern")
        return self.render_to_response(
            self.get_context_data(**wechsel, tage=gueltig_fuer_tage(), **kwargs)
        )


class EmailBestaetigenView(TemplateView):
    """The link from the mail. Shows what it would do, and does it on the button.

    Not on opening: mail programs fetch links ahead, and a scanner must not
    change an address. A dead link gets the same page with the reason. The
    page needs no sign-in. Whoever reads the mail on another device would
    otherwise have to sign in there first, and the link already proves the
    address.
    """

    template_name = "accounts/email_confirm.html"

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.wechsel = wechsel_aus(kwargs["token"])

    def get_context_data(self, **kwargs):
        return super().get_context_data(**kwargs) | {"wechsel": self.wechsel}

    def post(self, request, *args, **kwargs):
        if self.wechsel is None:
            return self.get(request, *args, **kwargs)
        try:
            anwenden(self.wechsel)
        except LinkVerbraucht:
            self.wechsel = None
            return self.get(request, *args, **kwargs)
        except AdresseVergeben:
            return self.render_to_response(self.get_context_data(vergeben=True))
        request.session[BESTAETIGT] = self.wechsel.neu
        return redirect("email_bestaetigt")


class EmailBestaetigtView(TemplateView):
    """After the confirmation: the address applies, and where to go on from here."""

    template_name = "accounts/email_confirm_done.html"

    def get(self, request, *args, **kwargs):
        email = request.session.pop(BESTAETIGT, "")
        return self.render_to_response(self.get_context_data(email=email, **kwargs))


class EinladungErneutView(AdminsSindGeschuetztMixin, SingleObjectMixin, View):
    """Sends the invitation again, from the account's page.

    For a link that has expired, a mail that went astray, or an account that
    was created while no mail server was configured. Only while the account
    still waits for its password ('ist_eingeladen'): any other would get a
    link that does not open anyway.

    For an administrator only an administrator sends it, as for editing and
    deleting. The link sets the password and switches the account on, and
    until 10/2026 a manager could send it for an administrator.
    """

    model = CustomUser

    def post(self, request, *args, **kwargs):
        benutzer = self.get_object()
        if benutzer.has_usable_password():
            messages.error(request, _("Dieses Konto hat schon ein Passwort."))
        elif not benutzer.ist_eingeladen:
            messages.error(
                request,
                _("Dieses Konto hat sich schon angemeldet und wartet auf keine Einladung."),
            )
        elif not mail_moeglich():
            messages.error(
                request,
                _("Ohne E-Mail-Server geht keine Einladung hinaus. Siehe DEPLOYMENT.md."),
            )
        elif not einladung_senden(benutzer, request):
            messages.error(
                request,
                _(
                    "Die Einladung ging nicht hinaus, der E-Mail-Server antwortet nicht. "
                    "Bitte später noch einmal versuchen."
                ),
            )
        else:
            messages.success(
                request,
                _("Die Einladung ist noch einmal an %(email)s unterwegs.")
                % {"email": benutzer.email},
            )
        return redirect(benutzer.get_absolute_url())


class UserListView(SpaltenMixin, ListView):
    model = CustomUser
    template_name = "accounts/user_list.html"
    spalten = {
        "name": (_("Name"), ("last_name", "first_name")),
        "benutzername": (_("Benutzername"), ("username",)),
        "email": (_("E-Mail"), ("email",)),
        # Ascending: counters first, then managers, then admins.
        "rolle": (_("Rolle"), ("is_superuser", "is_staff")),
        "login": (_("Letzte Anmeldung"), ("last_login",)),
    }
    standard_spalte = "name"
    zweitschluessel = ("last_name", "first_name")

    def get_queryset(self):
        return self.sortiert(CustomUser.objects.prefetch_related("haeuser"))


class UserDetailView(DetailView):
    model = CustomUser
    template_name = "accounts/user_detail.html"


class UserDeleteView(AdminsSindGeschuetztMixin, NurMitRueckfrage, DeleteView):
    model = CustomUser
    template_name = "accounts/user_delete.html"
    success_url = reverse_lazy("user_list")

    def form_valid(self, form):
        """Asked and deleted in one transaction, under the lock of the administrators.

        The account's own row is locked after them, by number, and asked once
        more whether a manager may delete it ('geschuetzt'). The mail goes
        out afterwards, when the lock is let go.
        """
        konto = self.object
        assert isinstance(konto, CustomUser)
        with transaction.atomic():
            if letzter_administrator_geht(CustomUser.objects.filter(pk=konto.pk)):
                messages.error(self.request, LETZTER_ADMINISTRATOR_BLEIBT)
                return redirect(konto.get_absolute_url())
            gesperrt = CustomUser.objects.select_for_update().filter(pk=konto.pk).first()
            if gesperrt is not None and self.geschuetzt(gesperrt):
                return self.abweisen()
            spuren_loeschen(konto.get_username())
            antwort = super().form_valid(form)
        # Deleted, the instance still knows name, address and language.
        warnen_wenn_nicht_gesendet(self.request, konto, loeschung_melden(konto, self.request.user))
        return antwort


class UserUpdateView(AdminsSindGeschuetztMixin, UpdateView):
    model = CustomUser
    form_class = CustomUserChangeForm
    template_name = "accounts/user_edit.html"

    def get_form_kwargs(self):
        # Who edits decides whether the address is a field ('CustomUserChangeForm').
        return super().get_form_kwargs() | {"bearbeiter": self.request.user}

    def form_valid(self, form):
        """After saving, the person learns what changed and who changed it.

        Written are the columns the form changed ('geaenderte_spalten'), the
        houses as the form sets them. Saved whole, the row as the request
        read it undid what came in between: a new password, a deactivation
        (until 10/2026). Under the account's lock, after the administrators',
        so an account deleted meanwhile is seen, and not brought back.

        A new address does not land in the account yet where the person has
        to confirm it first ('accounts/email_change.py'). The column stays as
        it is, and the mail with the link goes out afterwards. The change mail
        then says nothing about the address, because nothing about it changed
        yet. An address written at once takes a waiting one back.

        Whether the change takes the last administrator is asked once more
        here, under the lock of the administrators and in the transaction
        that saves ('letzter_administrator_geht'). The form asked before and
        without a lock. So is whether a manager may change the account at
        all ('geschuetzt'). The mails go out once the lock is let go.

        Switched off, the account is signed out everywhere ('ueberall_abmelden').
        Until 10/2026 its sessions and the app's refresh tokens only waited,
        and switching it on again brought them back, a lost phone's too. An
        account without a password loses its invitation links as well
        ('einladungen_entwerten').
        """
        assert isinstance(form, CustomUserChangeForm)
        konto = form.save(commit=False)
        assert isinstance(konto, CustomUser)
        with transaction.atomic():
            if not bleibt_administrator(konto) and letzter_administrator_geht(
                CustomUser.objects.filter(pk=konto.pk)
            ):
                if not form.letzten_administrator_halten():
                    form.add_error(None, LETZTER_ADMINISTRATOR_BLEIBT)
                return self.form_invalid(form)
            gespeichert = form.gesperrt_gelesen()
            if gespeichert is None:
                return self.konto_weg(form)
            if self.geschuetzt(gespeichert):
                return self.abweisen()
            vorher = Kontostand.aus_der_datenbank(gespeichert)
            spalten = form.geaenderte_spalten()
            neu = konto.email
            aufgeschoben = "email" in spalten and neu != vorher.email and not sofort(gespeichert)
            if aufgeschoben:
                spalten.remove("email")
                konto.email = vorher.email
            try:
                with transaction.atomic():
                    konto.save(update_fields=spalten)
            except CustomUser.NotUpdated:
                return self.konto_weg(form)
            form.save_m2m()
            if "email" in spalten:
                Adresswechsel.zuruecknehmen(konto)
            if vorher.is_active and "is_active" in spalten and not konto.is_active:
                ueberall_abmelden(konto)
                einladungen_entwerten(gespeichert)
        if aufgeschoben:
            self.bestaetigung_melden(
                neu, bestaetigung_senden(konto, neu, self.request, von=self.request.user)
            )
        warnen_wenn_nicht_gesendet(
            self.request, konto, aenderung_melden(konto, vorher, self.request.user)
        )
        self.object = konto
        return HttpResponseRedirect(self.get_success_url())

    def konto_weg(self, form: CustomUserChangeForm):
        """Deleted while the form was checked: the list says so, and nothing is written."""
        messages.error(self.request, KONTO_GELOESCHT % {"name": form.angezeigter_name})
        return redirect("user_list")

    def bestaetigung_melden(self, neu: str, gesendet: bool | None) -> None:
        if gesendet is False:
            messages.warning(self.request, BESTAETIGUNG_NICHT_GESENDET % {"email": neu})
        else:
            messages.info(self.request, BESTAETIGUNG_UNTERWEGS % {"email": neu})
