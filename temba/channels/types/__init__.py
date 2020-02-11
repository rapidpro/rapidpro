from collections import OrderedDict

from django.conf import settings
from django.utils.module_loading import import_string

TYPES = OrderedDict({})


def register_channel_type(type_class):
    """
    Registers a channel type
    """
    if not type_class.slug:
        type_class.slug = type_class.__module__.split(".")[-2]

    if type_class.code in TYPES:  # pragma: no cover
        raise ValueError("More than channel type with code: %s" % type_class.code)
    TYPES[type_class.code] = type_class()


def reload_channel_types():
    """
    Re-loads the dynamic channel types
    """
    for class_name in settings.CHANNEL_TYPES:
        register_channel_type(import_string(class_name))


reload_channel_types()
