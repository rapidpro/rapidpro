from django.conf import settings
from django.utils.translation import ugettext_lazy as _


class Value(object):
    TYPE_TEXT = "T"
    TYPE_NUMBER = "N"
    TYPE_DATETIME = "D"
    TYPE_STATE = "S"
    TYPE_DISTRICT = "I"
    TYPE_WARD = "W"

    TYPE_CONFIG = (
        (TYPE_TEXT, _("Text"), "text"),
        (TYPE_NUMBER, _("Number"), "numeric"),
        (TYPE_DATETIME, _("Date & Time"), "datetime"),
        (TYPE_STATE, _("State"), "state"),
        (TYPE_DISTRICT, _("District"), "district"),
        (TYPE_WARD, _("Ward"), "ward"),
    )

    TYPE_CHOICES = [(c[0], c[1]) for c in TYPE_CONFIG]

    MAX_VALUE_LEN = settings.VALUE_FIELD_SIZE
