from collections import OrderedDict

from django.conf import settings
from django.utils.module_loading import import_string

TYPES = OrderedDict({})


def register_ticketer_type(type_class):
    """
    Registers a ticketer type
    """
    global TYPES

    if not type_class.slug:
        type_class.slug = type_class.__module__.split(".")[-2]

    assert type_class.slug not in TYPES, f"ticketer type slug {type_class.slug} already taken"

    TYPES[type_class.slug] = type_class()


def reload_ticketer_types():
    """
    Re-loads the dynamic ticketer types
    """
    global TYPES

    TYPES = OrderedDict({})
    for class_name in settings.TICKETER_TYPES:
        register_ticketer_type(import_string(class_name))


reload_ticketer_types()
