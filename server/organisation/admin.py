from typing import TYPE_CHECKING

from django.conf import settings
from django.contrib import admin, messages
from django.db.models import Case, Count, Value, When
from django.urls import reverse
from django.utils import translation
from django.utils.html import format_html, format_html_join
from django.utils.translation import gettext_lazy as _

from django_project.admin_site import NurUeberDieSeite
from django_project.deletion import VerwaltungSperrtZuerst
from django_project.mail import mail_moeglich

from .forms import ErscheinungsbildForm, MailtextForm
from .mail_texts import ANLASS, PLATZHALTER, VARIABLEN, Mailart
from .models import Bereich, Erscheinungsbild, Haus, Mailtext, Ort, orte_der_bereiche_sperren


class BereichInline(admin.TabularInline):
    model = Bereich
    extra = 1
    fields = ("name",)

    def get_queryset(self, request):
        # Every row is headed by its area, and that names its house.
        return super().get_queryset(request).select_related("haus")


class OrtInline(admin.TabularInline):
    """Locations hang on the area, no longer on the house, hence here."""

    model = Ort
    extra = 1
    fields = ("name",)

    def get_queryset(self, request):
        # Every row is headed by its location, and that names house and area.
        return super().get_queryset(request).select_related("bereich__haus")


if TYPE_CHECKING:

    class _GezaehltesHaus(Haus):
        """House as 'get_queryset' below delivers it, with the counted columns.
        Only for the type checker. At runtime the class would give a second model.
        """

        bereiche_gezaehlt: int
        orte_gezaehlt: int


@admin.register(Haus)
class HausAdmin(NurUeberDieSeite, VerwaltungSperrtZuerst, admin.ModelAdmin):
    list_display = ("name", "anzahl_bereiche", "anzahl_orte", "anzahl_mitarbeitende")
    search_fields = ("name",)
    inlines = (BereichInline,)

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .prefetch_related("mitarbeitende")
            .annotate(
                bereiche_gezaehlt=Count("bereiche", distinct=True),
                orte_gezaehlt=Count("bereiche__orte", distinct=True),
            )
        )

    def inline_sperren(self, inline, eltern, nummern):
        if inline.model is Bereich:
            orte_der_bereiche_sperren(nummern)
        else:
            super().inline_sperren(inline, eltern, nummern)

    @admin.display(description=_("Bereiche"), ordering="bereiche_gezaehlt")
    def anzahl_bereiche(self, obj: _GezaehltesHaus) -> int:
        return obj.bereiche_gezaehlt

    @admin.display(description=_("Orte"), ordering="orte_gezaehlt")
    def anzahl_orte(self, obj: _GezaehltesHaus) -> int:
        return obj.orte_gezaehlt

    @admin.display(description=_("Mitarbeitende"))
    def anzahl_mitarbeitende(self, obj: Haus) -> int:
        return obj.mitarbeitende.count()


@admin.register(Bereich)
class BereichAdmin(NurUeberDieSeite, VerwaltungSperrtZuerst, admin.ModelAdmin):
    list_display = ("name", "haus", "anzahl_orte")
    list_filter = ("haus",)
    search_fields = ("name", "haus__name")
    autocomplete_fields = ("haus",)
    inlines = (OrtInline,)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("haus").prefetch_related("orte")

    def vor_dem_loeschen_sperren(self, nummern):
        orte_der_bereiche_sperren(nummern)

    @admin.display(description=_("Orte"))
    def anzahl_orte(self, obj: Bereich) -> int:
        return obj.orte.count()


@admin.register(Ort)
class OrtAdmin(NurUeberDieSeite, VerwaltungSperrtZuerst, admin.ModelAdmin):
    list_display = ("name", "bereich", "haus", "anzahl_erwartungen")
    list_filter = ("bereich__haus", "bereich")
    search_fields = ("name", "bereich__name", "bereich__haus__name")
    autocomplete_fields = ("bereich",)

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("bereich__haus")
            .prefetch_related("erwartungen")
        )

    @admin.display(description=_("Haus"), ordering="bereich__haus__name")
    def haus(self, obj: Ort) -> str:
        return obj.bereich.haus

    @admin.display(description=_("Erwartete Artikel"))
    def anzahl_erwartungen(self, obj: Ort) -> int:
        return obj.erwartungen.count()


@admin.register(Erscheinungsbild)
class ErscheinungsbildAdmin(admin.ModelAdmin):
    """Name, logo, operator, legal notice and privacy text, in versions to switch between.

    "Standard" comes with the application and can only be read and copied. A
    copy is what gets changed, and in the list one chooses which version
    applies. Every one except the standard and the active one can be deleted.
    """

    form = ErscheinungsbildForm
    list_display = (
        "anzeigename",
        "aktiv",
        "anwendungsname",
        "betreiber",
        "hat_logo",
        "geaendert_am",
    )
    list_display_links = ("anzeigename",)
    actions = ["aktiv_setzen", "kopie_anlegen"]
    fieldsets = (
        (None, {"fields": ("name", "aktiv")}),
        (
            _("Name und Logo"),
            {
                "fields": ("anwendungsname", "datei", "entfernen", "hintergrund", "vorschau"),
                "description": _(
                    "Der Name steht neben dem Logo in der Leiste, im Browsertab und am Icon "
                    "der Zähl-App."
                ),
            },
        ),
        (
            _("Betreiber"),
            {
                "fields": ("betreiber", "betreiber_kontakt", "datenschutz_kontakt"),
                "description": _(
                    "Steht im Fuß jeder Seite und auf der Seite „Datenschutz“. Alles freiwillig."
                ),
            },
        ),
        (
            _("Rückmeldungen"),
            {
                "fields": ("rueckmeldeadresse",),
                "description": _(
                    "Mit einer Adresse steht im Kontomenü der Website und der Zähl-App „Problem "
                    "oder Wunsch melden“. Der Eintrag öffnet eine Mail an diese Adresse, "
                    "vorbelegt mit Version, Seite, Konto, Zeit und Browser, aus der Zähl-App "
                    "auch mit Verbindung und wartenden Eingaben. Abgeschickt wird sie aus dem "
                    "eigenen Mailprogramm, gespeichert wird nichts."
                ),
            },
        ),
        (
            _("Impressum"),
            {
                "fields": ("impressum", "impressum_en"),
                "description": _(
                    "Ein Impressum gibt es nur, wenn hier etwas steht. Die Seite zeigt den Text "
                    "so, wie er hier steht, ohne Rahmen. Was hineingehört, entscheidet der "
                    "Betrieb."
                ),
            },
        ),
        (
            _("Datenschutz"),
            {
                "fields": ("datenschutztext", "datenschutztext_en"),
                "description": _(
                    "Der mitgelieferte Text beschreibt, was die Anwendung speichert. Er lässt "
                    "sich ergänzen, ändern oder ganz ersetzen, je Sprache einer. Leer heißt: "
                    "keine Seite „Datenschutz“ und kein Verweis darauf."
                ),
            },
        ),
        (None, {"fields": ("geaendert_am",)}),
    )
    # 'aktiv' is chosen in the list, not in the form: otherwise there would be two active ones.
    readonly_fields = ("aktiv", "vorschau", "geaendert_am")

    def has_change_permission(self, request, obj=None) -> bool:
        """The standard opens for reading only."""
        if obj is not None and obj.ist_standard:
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None) -> bool:
        if obj is not None and (obj.ist_standard or obj.aktiv):
            return False
        return super().has_delete_permission(request, obj)

    @admin.display(description=_("Name"), ordering="name")
    def anzeigename(self, obj: Erscheinungsbild):
        return obj.anzeigename

    @admin.display(description=_("eigenes Logo"), boolean=True)
    def hat_logo(self, obj: Erscheinungsbild) -> bool:
        return obj.hat_logo

    @admin.display(description=_("Vorschau"))
    def vorschau(self, obj: Erscheinungsbild) -> str:
        if not obj.pk or not obj.hat_logo:
            return str(_("Es gilt das mitgelieferte Logo."))
        # The address names the version, because without one it serves the
        # active one. The timestamp is attached, otherwise the browser would
        # keep showing the logo just replaced.
        adresse = reverse("icon", kwargs={"name": "logo"})
        return format_html(
            '<img src="{}?von={}&amp;stand={}" alt="" width="64" height="64">',
            adresse,
            obj.pk,
            int(obj.geaendert_am.timestamp()),
        )

    @admin.action(description=_("Als aktive Fassung wählen"))
    def aktiv_setzen(self, request, queryset) -> None:
        if queryset.count() != 1:
            self.message_user(
                request, _("Bitte genau eine Fassung wählen."), level=messages.WARNING
            )
            return
        fassung = queryset.get()
        fassung.aktivieren()
        self.message_user(
            request, _("„%(name)s“ ist jetzt die aktive Fassung.") % {"name": fassung.anzeigename}
        )

    @admin.action(description=_("Kopie anlegen"))
    def kopie_anlegen(self, request, queryset) -> None:
        """A copy of every chosen one. That is how the standard turns into something of your own."""
        for fassung in queryset:
            kopie = fassung.kopie(fassung.kopiename())
            self.message_user(
                request,
                _("„%(name)s“ angelegt. Sie ist noch nicht aktiv.") % {"name": kopie.name},
            )


#: Invented values for the preview. A person, someone who acted, a link.
BEISPIEL = {
    "vorname": "Alex",
    "nachname": "Beispiel",
    "benutzername": "abeispiel",
    "wer": "Kim Leitung",
    "selbst": False,
    "link": "https://crosstally.example/accounts/einladung/MQ/beispiel/",
    "app": "https://crosstally.example/app/",
    "website": "https://crosstally.example/accounts/login/",
    "passkey": "Laptop",
}


@admin.register(Mailtext)
class MailtextAdmin(admin.ModelAdmin):
    """The mails the application sends, one row each, in the order they are defined.

    No adding and no deleting: which mails there are is fixed ('Mailart'). The
    list creates what is missing when it opens, after an old backup too.
    Without a mail server the texts stay editable and the page says that they
    apply once there is one: whoever prepares the texts before switching mail
    on should not have to wait for it.
    """

    form = MailtextForm
    list_display = ("bezeichnung", "eigener_text", "namen", "geaendert_am")
    list_display_links = ("bezeichnung",)
    actions = ["namen_nennen", "keine_namen_nennen", "zuruecksetzen"]
    readonly_fields = ("anlass", "platzhalter", "vorschau", "geaendert_am")
    # As defined, not by the alphabet of the keys: the invitation first.
    ordering = (
        Case(
            *(When(art=art, then=Value(nummer)) for nummer, art in enumerate(Mailart.values)),
            default=Value(len(Mailart.values)),
        ).asc(),
    )

    def has_add_permission(self, request) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False

    def get_fieldsets(self, request, obj=None):
        """'nennt_namen' only where the mail can name anyone at all."""
        fieldsets = [
            (None, {"fields": ("anlass",)}),
            (_("Deutsch"), {"fields": ("betreff", "text")}),
            (_("Englisch"), {"fields": ("betreff_en", "text_en")}),
        ]
        if obj is not None and obj.hat_urheber:
            fieldsets.append((_("Wer es ausgelöst hat"), {"fields": ("nennt_namen",)}))
        fieldsets += [
            (
                _("Platzhalter"),
                {
                    "fields": ("platzhalter",),
                    "description": _(
                        "Ein leeres Feld heißt: der mitgelieferte Text. Wer ein Feld leert, "
                        "bekommt ihn zurück."
                    ),
                },
            ),
            (_("Vorschau"), {"fields": ("vorschau",)}),
            (None, {"fields": ("geaendert_am",)}),
        ]
        return fieldsets

    def ohne_mailserver_hinweisen(self, request) -> None:
        if request.method == "GET" and not mail_moeglich():
            self.message_user(
                request,
                _(
                    "Zurzeit ist kein E-Mail-Server eingerichtet, es geht also keine Mail "
                    "hinaus. Die Texte gelten, sobald einer eingerichtet ist (DEPLOYMENT.md)."
                ),
                level=messages.WARNING,
            )

    def changelist_view(self, request, extra_context=None):
        Mailtext.alle_anlegen()
        self.ohne_mailserver_hinweisen(request)
        return super().changelist_view(request, extra_context)

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        self.ohne_mailserver_hinweisen(request)
        return super().changeform_view(request, object_id, form_url, extra_context)

    @admin.display(description=_("Mail"))
    def bezeichnung(self, obj: Mailtext) -> str:
        return obj.get_art_display()

    @admin.display(description=_("eigener Text"), boolean=True)
    def eigener_text(self, obj: Mailtext) -> bool:
        return obj.angepasst

    @admin.display(description=_("nennt Namen"))
    def namen(self, obj: Mailtext) -> str:
        if not obj.hat_urheber:
            return "–"
        return str(_("ja") if obj.nennt_namen else _("nein"))

    @admin.display(description=_("Wann sie hinausgeht"))
    def anlass(self, obj: Mailtext) -> str:
        return str(ANLASS.get(obj.art, ""))

    @admin.display(description=_("In dieser Mail"))
    def platzhalter(self, obj: Mailtext) -> str:
        """The placeholders of this mail and how a condition is written."""
        namen = format_html_join(
            "",
            "<li><code>{}</code> {}</li>",
            (("{{ " + name + " }}", PLATZHALTER[name]) for name in VARIABLEN[obj.art]),
        )
        bedingung = _("Eine Bedingung schreibt sich so: %(beispiel)s") % {
            "beispiel": "{% if wer %} … {% else %} … {% endif %}"
        }
        return format_html('<ul class="platzhalterliste">{}</ul><p>{}</p>', namen, bedingung)

    @admin.display(description=_("So sieht sie aus"))
    def vorschau(self, obj: Mailtext) -> str:
        """The stored text in both languages, with invented values.

        Shown is the case that someone else did it. Whether the name appears
        follows the tick, as in the real mail.
        """
        werte = BEISPIEL | {
            "anwendung": Erscheinungsbild.aktuell().titel,
            "tage": max(1, settings.PASSWORD_RESET_TIMEOUT // 86400),
        }
        bloecke = []
        for sprache, name in (("de", _("Deutsch")), ("en", _("Englisch"))):
            with translation.override(sprache):
                beispiel = werte | {
                    "aenderungen": f"{_('Häuser')}: Kino Nord → Kino Nord, Kino Süd",
                    "wo": str(_("auf der Website")),
                }
                betreff, text = obj.ausfuellen(sprache, beispiel)
            bloecke.append((name, betreff, text))
        return format_html_join(
            "",
            '<p class="mailvorschau-kopf">{}</p><p><strong>{}</strong></p>'
            '<pre class="mailvorschau">{}</pre>',
            bloecke,
        )

    @admin.action(description=_("Namen nennen"))
    def namen_nennen(self, request, queryset) -> None:
        self._namen_schalten(queryset, True)
        self.message_user(request, _("Die gewählten Mails nennen jetzt, wer sie ausgelöst hat."))

    @admin.action(description=_("Keine Namen nennen"))
    def keine_namen_nennen(self, request, queryset) -> None:
        self._namen_schalten(queryset, False)
        self.message_user(request, _("Die gewählten Mails nennen jetzt keine Namen mehr."))

    def _namen_schalten(self, queryset, an: bool) -> None:
        """One at a time, so 'geaendert_am' moves. A mail that names nobody stays as it is."""
        for mailtext in queryset:
            if mailtext.hat_urheber and mailtext.nennt_namen != an:
                mailtext.nennt_namen = an
                mailtext.save(update_fields=["nennt_namen", "geaendert_am"])

    @admin.action(description=_("Auf den mitgelieferten Text zurücksetzen"))
    def zuruecksetzen(self, request, queryset) -> None:
        for mailtext in queryset:
            for felder in Mailtext.FELDER.values():
                for feld in felder:
                    setattr(mailtext, feld, "")
            mailtext.save()
        self.message_user(request, _("Die gewählten Mails haben wieder den mitgelieferten Text."))
