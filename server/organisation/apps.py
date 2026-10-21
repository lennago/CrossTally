from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class OrganisationConfig(AppConfig):
    name = "organisation"
    verbose_name = _("Organisation")
