from django_project.routes import pfad

from .views import (
    AuswertungView,
    DifferenzVonHandView,
    InventurAbschlussView,
    InventurArtenView,
    InventurCreateView,
    InventurDeleteView,
    InventurDetailView,
    InventurListView,
    InventurPdfView,
    InventurPruefenView,
    InventurZaehlstandView,
    LaufAbschlussView,
    LaufDeleteView,
    LaufPdfView,
    LaufView,
    SollbestandKlaerungView,
    SollPflegeView,
    SperreView,
    ZaehlendeView,
    ZaehlungStartenView,
    ZurueckInVorbereitungView,
)

urlpatterns = [
    pfad("auswertungen/", AuswertungView.as_view(), name="auswertung"),
    pfad("inventuren/", InventurListView.as_view(), name="inventur_list"),
    pfad("inventuren/add/", InventurCreateView.as_view(), name="inventur_add"),
    # The run: all houses of a cut-off date. There is no bracket of its own in
    # the database, the address is the cut-off date.
    pfad("inventuren/lauf/<str:stichtag>/", LaufView.as_view(), name="inventur_lauf"),
    pfad(
        "inventuren/lauf/<str:stichtag>/pdf/",
        LaufPdfView.as_view(),
        name="inventur_lauf_pdf",
    ),
    pfad(
        "inventuren/lauf/<str:stichtag>/abschliessen/",
        LaufAbschlussView.as_view(),
        name="inventur_lauf_abschluss",
    ),
    pfad(
        "inventuren/lauf/<str:stichtag>/delete/",
        LaufDeleteView.as_view(),
        name="inventur_lauf_delete",
    ),
    pfad("inventuren/<int:pk>/", InventurDetailView.as_view(), name="inventur_detail"),
    pfad("inventuren/<int:pk>/pdf/", InventurPdfView.as_view(), name="inventur_pdf"),
    pfad("inventuren/<int:pk>/delete/", InventurDeleteView.as_view(), name="inventur_delete"),
    # The status changes are addresses of their own and reachable only via POST.
    # A closing must not hang on a link that a preview image calls.
    pfad(
        "inventuren/<int:pk>/zaehlung-starten/",
        ZaehlungStartenView.as_view(),
        name="inventur_zaehlung_starten",
    ),
    pfad(
        "inventuren/<int:pk>/zurueck/",
        ZurueckInVorbereitungView.as_view(),
        name="inventur_zurueck",
    ),
    pfad(
        "inventuren/<int:pk>/differenz-von-hand/",
        DifferenzVonHandView.as_view(),
        name="inventur_differenz_von_hand",
    ),
    pfad("inventuren/<int:pk>/sperre/", SperreView.as_view(), name="inventur_sperre"),
    # Closing happens from the check view: without open questions in the
    # same go as saving the notes, otherwise via 'abschliessen/', the page
    # with the questions, reachable only from there.
    pfad("inventuren/<int:pk>/pruefen/", InventurPruefenView.as_view(), name="inventur_pruefen"),
    # The id of the stored count state that the check view queries regularly.
    # "Stand" like the key in the response.
    pfad("inventuren/<int:pk>/stand/", InventurZaehlstandView.as_view(), name="inventur_stand"),
    pfad(
        "inventuren/<int:pk>/abschliessen/",
        InventurAbschlussView.as_view(),
        name="inventur_abschluss",
    ),
    pfad("inventuren/<int:pk>/zaehlende/", ZaehlendeView.as_view(), name="inventur_zaehlende"),
    pfad("inventuren/<int:pk>/arten/", InventurArtenView.as_view(), name="inventur_arten"),
    pfad(
        "inventuren/<int:pk>/sollbestand/",
        SollbestandKlaerungView.as_view(),
        name="inventur_sollbestand",
    ),
    pfad(
        "inventuren/<int:pk>/soll/",
        SollPflegeView.as_view(),
        name="inventur_soll_pflegen",
    ),
]
