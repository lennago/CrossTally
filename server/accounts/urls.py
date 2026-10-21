from django_project.routes import pfad

from .views import (
    EinladungErneutView,
    FarbwahlView,
    SprachwahlView,
    UserCreateView,
    UserDeleteView,
    UserDetailView,
    UserListView,
    UserUpdateView,
)

urlpatterns = [
    pfad("", UserListView.as_view(), name="user_list"),
    # The switch in both bars points here.
    pfad("sprache/", SprachwahlView.as_view(), name="sprachwahl"),
    pfad("farben/", FarbwahlView.as_view(), name="farbwahl"),
    pfad("add/", UserCreateView.as_view(), name="user_add"),
    pfad("<int:pk>/", UserDetailView.as_view(), name="user_detail"),
    pfad("<int:pk>/edit/", UserUpdateView.as_view(), name="user_edit"),
    pfad("<int:pk>/delete/", UserDeleteView.as_view(), name="user_delete"),
    pfad("<int:pk>/einladen/", EinladungErneutView.as_view(), name="user_einladen"),
]
