"""The administration: the Django admin, boiled down to what the site cannot do.

Everything daily lives in the web interface: articles, houses, counting lists,
creating and reviewing inventories. What remains here is what has no place
there: **deleting** a closed inventory, viewing an inventory's raw data, the
login attempts at the honeypot, user permissions and the backups.

Only part of the registered models is therefore visible. The others stay
registered because `autocomplete_fields` and the inlines need them, but do not
appear in the overview (`get_model_perms` returns nothing).

**Administrators only.** A manager is in the wrong place here and is told so,
instead of facing a login form that will never accept their account.
"""

from functools import update_wrapper
from typing import Any

from django.contrib import admin, messages
from django.contrib.auth import logout
from django.core.exceptions import ValidationError
from django.db import models
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import path, reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from django_project.mixin_bases import ModelAdminBasis
from django_project.roles import ist_administrator

from . import backup
from .form_fields import Dezimalfeld

#: Decimals accept a comma as well as a point, in the administration too.
DEZIMALFELDER: dict[type[models.Field], dict[str, Any]] = {
    models.DecimalField: {"form_class": Dezimalfeld}
}


class NurUeberDieSeite:
    """Stays registered but does not appear in the overview.

    For models the web interface maintains itself: offering them here a second
    time would mean two ways for the same work, one with the form's rules and one
    without. They stay registered nonetheless, otherwise the selection fields
    that point to them would break.
    """

    formfield_overrides = DEZIMALFELDER

    def get_model_perms(self, request) -> dict:
        return {}


class FilterMitBezuegen(admin.RelatedFieldListFilter):
    """The filter by a foreign key, with whatever its name needs.

    A location names itself after house and area, an inventory after its house.
    Django fetches that separately per entry of the filter list: two queries per
    location, one per inventory, and the inventories grow every month.
    'select_related()' without arguments follows every required foreign key.
    """

    def field_choices(self, field, request, model_admin):
        if not isinstance(field, models.ForeignKey):
            return super().field_choices(field, request, model_admin)
        saetze = fuer_den_namen(
            field.related_model._default_manager.complex_filter(
                field.get_limit_choices_to()
            ).select_related()
        )
        if ordering := self.field_admin_ordering(field, request, model_admin):
            saetze = saetze.order_by(*ordering)
        schluessel = field.target_field.attname
        return [(getattr(satz, schluessel), str(satz)) for satz in saetze]


# Before Django's own filter, for every entry in 'list_filter' that points to
# a foreign key.
admin.FieldListFilter.register(
    lambda feld: isinstance(feld, models.ForeignKey), FilterMitBezuegen, take_priority=True
)


def fuer_den_namen(saetze):
    """What a model's name needs beyond its foreign keys, prefetched.

    A model names it in 'NAME_BRAUCHT': an inventory is called after its
    types, and those are a relation of their own. Without the prefetch every
    entry of a list would ask for them.
    """
    return saetze.prefetch_related(*getattr(saetze.model, "NAME_BRAUCHT", ()))


def fremdschluessel(modell) -> list[str]:
    """A model's required relations, as 'select_related()' without arguments would follow them."""
    return [
        feld.name
        for feld in modell._meta.fields
        if feld.is_relation and feld.many_to_one and not feld.null
    ]


class AuswahlMitBezuegen(ModelAdminBasis):
    """The same for the selection lists in the form and its inlines."""

    formfield_overrides = DEZIMALFELDER

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        feld = super().formfield_for_foreignkey(db_field, request, **kwargs)
        if feld is not None and feld.queryset is not None:
            # Name the foreign keys explicitly: 'select_related()' without
            # arguments goes away with Django 7.0.
            feld.queryset = fuer_den_namen(
                feld.queryset.select_related(*fremdschluessel(feld.queryset.model))
            )
        return feld


class VerwaltungsAdminSite(admin.AdminSite):
    site_title = _("Verwaltung")
    site_header = _("Verwaltung")
    index_title = _("Was die Weboberfläche nicht kann")
    index_template = "admin/site_index.html"
    # There is no login here: people sign in at the front, and whoever is not an
    # administrator is told by 'login()' what the reason is.
    login_template = "admin/not_admin.html"

    def has_permission(self, request) -> bool:
        """Administrators, not managers.

        Django otherwise lets anyone with 'is_staff' in. And for us that is every
        manager, for whom the web interface is meant.
        """
        return request.user.is_active and ist_administrator(request.user)

    def admin_view(self, view, cacheable=False):
        """Signed in but without permissions: this is the end, not the form.

        Django sends people to the login in this case. A manager would then face a
        form that never accepts their account, and the address bar would read
        "login" although they are signed in already.
        """
        geschuetzt = super().admin_view(view, cacheable)

        def pruefen(request, *args, **kwargs):
            if request.user.is_authenticated and not self.has_permission(request):
                return render(request, "admin/not_admin.html", status=403)
            return geschuetzt(request, *args, **kwargs)

        return update_wrapper(pruefen, view)

    def login(self, request, extra_context=None):
        # Second line of defence: '/login/' itself does not go through 'admin_view'.
        if request.user.is_authenticated:
            return render(request, "admin/not_admin.html", status=403)
        return super().login(request, extra_context)

    def password_change(self, request, extra_context=None):
        """The own password, with the old one counted like a sign-in and a mail, as on the website.

        Django's page checks the old password with 'check_password()', past
        'axes', and wrong ones went uncounted (until 10/2026). The website's
        view asks through 'authenticate()' and tells the person afterwards.
        Here the mail was missing until 10/2026: whoever held an
        administrator's session and knew the old password changed it without a
        word, and the owner found the strongest account shut. The person
        changes it themselves here, so the mail is the website's and names no
        author ('passwort_melden'). The address and the done page stay the
        administration's, as with Django's page.
        """
        # Imported here: the accounts app registers its models with this site.
        from accounts.views import GemeldetePasswordChangeView  # noqa: PLC0415

        request.current_app = self.name
        return GemeldetePasswordChangeView.as_view(
            success_url=reverse("admin:password_change_done", current_app=self.name),
            extra_context={**self.each_context(request), **(extra_context or {})},
        )(request)

    def get_urls(self):
        eigene = [
            path(
                "backup/erstellen/",
                self.admin_view(backup_erstellen),
                name="backup_erstellen",
            ),
            path(
                "backup/einspielen/<str:name>/",
                self.admin_view(backup_einspielen),
                name="backup_einspielen",
            ),
        ]
        # Before the others: the admin catches everything at the end with a catch-all.
        return eigene + super().get_urls()

    def index(self, request, extra_context=None):
        return super().index(
            request,
            (extra_context or {})
            | {
                "staende": backup.staende(),
                "ordner": backup.verzeichnis(),
                "behalten": backup.BEHALTEN,
                "laufende_zaehlungen": backup.laufende_zaehlungen(),
            },
        )


@require_POST
def backup_erstellen(request):
    stand = backup.erstellen()
    messages.success(
        request,
        _(
            "Backup %(stand)s geschrieben (%(groesse)s). Es bleiben die %(behalten)s jüngsten "
            "Stände liegen."
        )
        % {"stand": stand.name, "groesse": stand.groesse, "behalten": backup.BEHALTEN},
    )
    return HttpResponseRedirect(reverse("admin:index"))


def backup_einspielen(request, name: str):
    """Back to a saved snapshot, with typed confirmation.

    A button alone would be too little: this throws away everything created since
    the chosen snapshot, and it can only be recovered from the backup that is
    created along the way.
    """
    stand = backup.stand_holen(name)
    if stand is None:
        messages.error(request, _("Diesen Stand gibt es nicht (mehr): %(name)s") % {"name": name})
        return HttpResponseRedirect(reverse("admin:index"))
    # Said when the page opens, not after the typed word: an unencrypted
    # state is not restored here while a key is set ('pruefe_verschluesselt').
    try:
        backup.pruefe_verschluesselt(stand)
    except backup.Unverschluesselt as fehler:
        messages.error(request, " ".join(fehler.messages))
        return HttpResponseRedirect(reverse("admin:index"))

    # The word in the account's language, as the page asks for it, and the
    # German one for good measure: an English account could not restore at
    # all until 10/2026, the page wanted "restore" and the view "einspielen".
    getippt = request.POST.get("bestaetigung", "").strip()
    if request.method == "POST" and getippt in ("einspielen", str(_("einspielen"))):
        try:
            vorher = backup.einspielen(stand)
        except backup.LaeuftSchon:
            # Usually a double click. No redirect into the administration: by the
            # time the browser gets there, the first restore has signed everyone out.
            return render(request, "admin/backup_running.html", {"stand": stand})
        except ValidationError as fehler:
            messages.error(request, " ".join(fehler.messages))
            return HttpResponseRedirect(reverse("admin:index"))
        # The sessions lived in the database and are gone with it. Sign out
        # before the response goes out. Otherwise the session would rewrite itself.
        logout(request)
        return render(
            request,
            "admin/backup_done.html",
            {"stand": stand, "vorher": vorher},
        )

    return render(
        request,
        "admin/backup_restore.html",
        admin.site.each_context(request)
        | {
            "title": _("Backup einspielen"),
            "stand": stand,
            "falsch_getippt": request.method == "POST",
        },
    )
