"""The addresses as they apply to anonymous visitors: the same, only without the real admin.

The 'StaffOnlyMiddleware' attaches this URLconf to anonymous calls of the
admin address. Otherwise 'CommonMiddleware' would turn the call without a
trailing slash into a redirect to the one with, and that only exists when
the page exists too. Exactly that the address must not give away.
"""

from .urls import admin_adresse
from .urls import urlpatterns as mit_admin

urlpatterns = [muster for muster in mit_admin if muster is not admin_adresse]
