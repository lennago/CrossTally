"""Numbers as the app writes them."""

import re

from django.db import models
from rest_framework import serializers

#: A sign, digits, at most one point or comma.
EINFACHE_ZAHL = re.compile(r"[+-]?(?:\d+(?:[.,]\d*)?|[.,]\d+)")


class Dezimalzahl(serializers.DecimalField):
    """A number only in the plain notation the app writes, with a point or a comma.

    DRF's own field reads what Python's 'Decimal' reads, "1e5" and "1_000"
    too, and refuses the comma. The app takes neither the one nor the other
    since 10/2026 ('asNumberText' in 'app/src/services/numbers.js'), and the
    server took them from any client. The comma counts like the point, as in
    the forms ('django_project.form_fields.Dezimalfeld'). A number from JSON
    is read as its text. The refusal is DRF's own sentence for no number.
    """

    def to_internal_value(self, data):
        text = str(data).strip()
        if not EINFACHE_ZAHL.fullmatch(text):
            self.fail("invalid")
        return super().to_internal_value(text.replace(",", "."))


class Modellserializer(serializers.ModelSerializer):
    """A model serializer whose decimals take only the app's notation ('Dezimalzahl')."""

    serializer_field_mapping = {
        **serializers.ModelSerializer.serializer_field_mapping,
        models.DecimalField: Dezimalzahl,
    }
