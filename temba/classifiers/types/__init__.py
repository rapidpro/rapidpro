from collections import OrderedDict

from django.conf import settings
from django.utils.module_loading import import_string

TYPES = OrderedDict({})


def register_classifier_type(type_class):
    """
    Registers a classifier type
    """
    global TYPES

    if not type_class.slug:
        type_class.slug = type_class.__module__.split(".")[-2]

    if type_class.slug in TYPES:  # pragma: no cover
        raise ValueError("More than one classifier type with slug: %s" % type_class.slug)
    TYPES[type_class.slug] = type_class()


def reload_classifier_types():
    """
    Re-loads the dynamic classifier types
    """
    global TYPES

    TYPES = OrderedDict({})
    for class_name in settings.CLASSIFIER_TYPES:
        register_classifier_type(import_string(class_name))


reload_classifier_types()
