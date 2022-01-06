from django.conf import settings
from django.utils.module_loading import import_string

from .base import *  # noqa

backends = {}


def register_backend_type(type_class):
    """
    Registers an analytics backend type
    """

    assert type_class.slug not in backends, f"backend type slug {type_class.slug} already taken"

    backends[type_class.slug] = type_class()


def reload_backend_types():
    """
    Re-loads the dynamic backend types
    """

    backends.clear()

    for class_name in settings.ANALYTICS_TYPES:
        register_backend_type(import_string(class_name))


reload_backend_types()
