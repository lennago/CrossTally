from django.contrib.admin.apps import AdminConfig


class VerwaltungsAdminConfig(AdminConfig):
    """Puts our version of the admin in place of the bundled one.

    This way and not 'admin.site = ...': every '@admin.register' gets the right
    site without further ado, in the apps too.
    """

    default_site = "django_project.admin_site.VerwaltungsAdminSite"
