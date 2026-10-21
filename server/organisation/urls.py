from django_project.routes import pfad

from .views import (
    BereichDeleteView,
    BereichDetailView,
    BereichUpdateView,
    HausCreateView,
    HausDeleteView,
    HausDetailView,
    HausListView,
    HausUpdateView,
    OrtDeleteView,
    OrtDetailView,
    OrtUpdateView,
)

urlpatterns = [
    pfad("", HausListView.as_view(), name="haus_list"),
    pfad("add/", HausCreateView.as_view(), name="haus_add"),
    pfad("<int:pk>/", HausDetailView.as_view(), name="haus_detail"),
    pfad("<int:pk>/edit/", HausUpdateView.as_view(), name="haus_edit"),
    pfad("<int:pk>/delete/", HausDeleteView.as_view(), name="haus_delete"),
    # Areas and locations deliberately have no list of their own. They are
    # reached through their house, which shows the structure anyway.
    pfad("bereiche/<int:pk>/", BereichDetailView.as_view(), name="bereich_detail"),
    pfad("bereiche/<int:pk>/edit/", BereichUpdateView.as_view(), name="bereich_edit"),
    pfad("bereiche/<int:pk>/delete/", BereichDeleteView.as_view(), name="bereich_delete"),
    pfad("orte/<int:pk>/", OrtDetailView.as_view(), name="ort_detail"),
    pfad("orte/<int:pk>/edit/", OrtUpdateView.as_view(), name="ort_edit"),
    pfad("orte/<int:pk>/delete/", OrtDeleteView.as_view(), name="ort_delete"),
]
