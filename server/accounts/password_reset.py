"""The „Passwort vergessen?“ flow with a limit, our own mail, and only with a mail server.

Django's view sends the mail for every known address and sends nothing for
an unknown one. Two things followed from that until 10/2026: whoever knew the
site could flood a known address with mails, and the response took longer for
a known address, because only then token and mail arose. With an SMTP
server the difference grows to hundreds of milliseconds.

Here every request is noted ('Passwortanfrage'), address and sender are
counted per hour, and above the limit the same "done" page comes back
without a mail. The mail itself goes out in a thread of its own, so the
response does not carry its duration. Tests set 'im_hintergrund' to run
inline.

**Without a mail server there is no such page** ('NurMitMailserver'). The
login does not link to it, and the address answers 404. Until 10/2026 the page
said "mail sent", and the valid link stood in the log of the container.
"""

import logging
import threading
from collections.abc import Callable
from datetime import timedelta
from smtplib import SMTPException

from axes.helpers import get_client_ip_address
from django.contrib.auth.forms import PasswordResetForm
from django.contrib.auth.views import (
    PasswordResetConfirmView,
    PasswordResetDoneView,
    PasswordResetView,
)
from django.core.mail import send_mail
from django.db import connection
from django.http import Http404, HttpResponseRedirect
from django.urls import reverse
from django.utils import timezone

from django_project.mail import mail_moeglich
from django_project.mixin_bases import AnsichtBasis
from organisation.mail_texts import Mailart

from .invitation import gueltig_fuer_tage
from .models import CustomUser, Passwortanfrage
from .notification import passwort_melden, verfasst
from .password_forms import NeuesPasswortForm

logger = logging.getLogger(__name__)

#: Requests per address and per sender within an hour. Five is generous for
#: a person who mistyped and a wall for a script.
GRENZE = 5
FENSTER = timedelta(hours=1)


def im_hintergrund(arbeit: Callable[[], None]) -> None:
    """Runs the mail off the request. A daemon thread would die with the worker."""

    def laufen() -> None:
        try:
            arbeit()
        finally:
            connection.close()

    threading.Thread(target=laufen, name="passwort-mail").start()


def zu_viele(email: str, ip: str | None) -> bool:
    """Whether this address or this sender has asked too often in the last hour."""
    seit = timezone.now() - FENSTER
    letzte = Passwortanfrage.objects.filter(zeitpunkt__gte=seit)
    if letzte.filter(email=email).count() >= GRENZE:
        return True
    return ip is not None and letzte.filter(ip_address=ip).count() >= GRENZE


class NurMitMailserver(AnsichtBasis):
    """Without a mail server the page does not exist. It would promise a mail that never comes."""

    def dispatch(self, request, *args, **kwargs):
        if not mail_moeglich():
            raise Http404
        return super().dispatch(request, *args, **kwargs)


class PasswortVergessenForm(PasswordResetForm):
    """Django's form with our mail instead of Django's.

    Django's text addresses the person formally, names the domain instead of
    the application, and comes in the server's language: the mail leaves in a
    thread of its own, and a thread does not inherit the language of the
    request. Here it is the text from the administration ('Mailtext'), in the
    language of the account. Who gets a mail stays Django's decision
    ('get_users'): active accounts with a usable password.
    """

    def send_mail(
        self,
        subject_template_name,
        email_template_name,
        context,
        from_email,
        to_email,
        html_email_template_name=None,
    ):
        konto = context["user"]
        pfad = reverse(
            "password_reset_confirm", kwargs={"uidb64": context["uid"], "token": context["token"]}
        )
        werte = {
            "link": f"{context['protocol']}://{context['domain']}{pfad}",
            "tage": gueltig_fuer_tage(),
        }
        betreff, text = verfasst(konto, Mailart.PASSWORT_VERGESSEN, werte)
        try:
            send_mail(betreff, text, from_email, [to_email])
        except SMTPException, OSError:
            # Like Django: logged, and the page says the same as for an
            # unknown address.
            logger.exception("Password reset mail to account %s not sent", konto.pk)


class GedrosseltePasswordResetView(NurMitMailserver, PasswordResetView):
    form_class = PasswortVergessenForm

    def form_valid(self, form):
        email = form.cleaned_data["email"].strip().lower()
        # The caller's address as the lockout reads it ('AXES_IPWARE_…'):
        # behind the proxy only 'X-Forwarded-For' is trustworthy. With the
        # library's own list anyone in the house network picked their
        # address with an 'X-Real-IP' and got past the limit per sender
        # (until 10/2026).
        ip = get_client_ip_address(self.request)
        gedrosselt = zu_viele(email, ip)
        Passwortanfrage.objects.create(email=email, ip_address=ip)
        if not gedrosselt:
            opts = {
                "use_https": self.request.is_secure(),
                "token_generator": self.token_generator,
                "from_email": self.from_email,
                "email_template_name": self.email_template_name,
                "subject_template_name": self.subject_template_name,
                "request": self.request,
                "html_email_template_name": self.html_email_template_name,
                "extra_email_context": self.extra_email_context,
            }
            im_hintergrund(lambda: form.save(**opts))
        return HttpResponseRedirect(self.get_success_url())


class PasswortVergessenGesendetView(NurMitMailserver, PasswordResetDoneView):
    """The page "mail sent" says nothing without a mail server, so it is gone too."""


class GemeldetePasswordResetConfirmView(PasswordResetConfirmView):
    """Django's step behind the link, and afterwards the mail every new password gets.

    The invitation sets its first password through its own view
    ('EinladungView') and sends nothing: setting it is the whole point there.
    """

    form_class = NeuesPasswortForm

    def form_valid(self, form):
        antwort = super().form_valid(form)
        konto = form.user
        assert isinstance(konto, CustomUser)
        passwort_melden(konto, "vergessen")
        return antwort
