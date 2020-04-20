from collections import OrderedDict

from django.conf import settings
from django.utils.module_loading import import_string

TYPES = OrderedDict({})


def register_ticketing_service_type(type_class):
    """
    Registers a ticketing service type
    """
    global TYPES

    if not type_class.slug:
        type_class.slug = type_class.__module__.split(".")[-2]

    if type_class.slug in TYPES:  # pragma: no cover
        raise ValueError("More than one ticketing service type with slug: %s" % type_class.slug)
    TYPES[type_class.slug] = type_class()


def reload_ticketing_service_types():
    """
    Re-loads the dynamic classifier types
    """
    global TYPES

    TYPES = OrderedDict({})
    for class_name in settings.TICKETING_SERVICE_TYPES:
        register_ticketing_service_type(import_string(class_name))


reload_ticketing_service_types()
