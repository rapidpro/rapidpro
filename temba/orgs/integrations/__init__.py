from collections import OrderedDict

from django.conf import settings
from django.utils.module_loading import import_string

TYPES = OrderedDict({})


def register_type(type_class):
    """
    Registers an integration type
    """
    global TYPES

    if not type_class.slug:  # pragma: no cover
        type_class.slug = type_class.__module__.split(".")[-2]

    assert type_class.slug not in TYPES, f"type slug {type_class.slug} already taken"

    TYPES[type_class.slug] = type_class()


def reload_types():
    """
    Re-loads the dynamic integration types
    """
    global TYPES

    TYPES = OrderedDict({})
    for class_name in settings.INTEGRATION_TYPES:
        register_type(import_string(class_name))


reload_types()
