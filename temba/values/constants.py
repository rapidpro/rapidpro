from django.conf import settings
from django.utils.translation import ugettext_lazy as _


class Value(object):
    TYPE_TEXT = "T"
    TYPE_NUMBER = "N"
    TYPE_DATETIME = "D"
    TYPE_STATE = "S"
    TYPE_DISTRICT = "I"
    TYPE_WARD = "W"

    KEY_TEXT = "text"
    KEY_NUMERIC = "numeric"
    KEY_DATETIME = "datetime"
    KEY_STATE = "state"
    KEY_DISTRICT = "district"
    KEY_WARD = "ward"

    # while we represent field types in our API and context as `numeric` we store as `number` on contacts themselves
    KEY_NUMBER = "number"

    TYPE_CONFIG = (
        (TYPE_TEXT, _("Text"), KEY_TEXT),
        (TYPE_NUMBER, _("Number"), KEY_NUMERIC),
        (TYPE_DATETIME, _("Date & Time"), KEY_DATETIME),
        (TYPE_STATE, _("State"), KEY_STATE),
        (TYPE_DISTRICT, _("District"), KEY_DISTRICT),
        (TYPE_WARD, _("Ward"), KEY_WARD),
    )

    TYPE_CHOICES = [(c[0], c[1]) for c in TYPE_CONFIG]

    MAX_VALUE_LEN = settings.VALUE_FIELD_SIZE
