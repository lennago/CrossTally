from django.apps import AppConfig
from django.contrib.auth.signals import user_logged_in
from django.utils.translation import gettext_lazy as _


class AccountsConfig(AppConfig):
    name = "accounts"
    verbose_name = _("Benutzer")

    def ready(self) -> None:
        from .passkey import frist_aufheben  # noqa: PLC0415  (it needs the models)

        user_logged_in.connect(frist_aufheben, dispatch_uid="accounts_frist_aufheben")
