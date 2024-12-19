from django.conf import settings
from django.db import transaction

from .checks import *  # noqa


def str_to_bool(text):
    """
    Parses a boolean value from the given text
    """
    return text and text.lower() in ["true", "y", "yes", "1"]


def percentage(numerator, denominator):
    """
    Returns an integer percentage as an integer for the passed in numerator and denominator.
    """
    if not denominator or not numerator:
        return 0

    return int(100.0 * numerator / denominator + 0.5)


def format_number(val):
    """
    Formats a decimal value without trailing zeros
    """
    if val is None:
        return ""
    elif val == 0:
        return "0"

    # we don't support non-finite values
    if not val.is_finite():
        return ""

    val = format(val, "f")

    if "." in val:
        val = val.rstrip("0").rstrip(".")  # e.g. 12.3000 -> 12.3

    return val


def on_transaction_commit(func):
    """
    Requests that the given function be called after the current transaction has been committed. However function will
    be called immediately if CELERY_TASK_ALWAYS_EAGER is True or if there is no active transaction.
    """
    if getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):
        func()
    else:  # pragma: no cover
        transaction.on_commit(func)


def get_nested_key(nested_dict, key, default=""):
    keys = key.split(".")
    value = nested_dict
    while keys:
        key = keys.pop(0)
        value = value.get(key, default)
        if not isinstance(value, dict):
            break
    return value


def set_nested_key(nested_dict, key, value):
    keys = key.split(".")
    level = nested_dict
    while keys:
        key = keys.pop(0)
        if not keys:
            level[key] = value
        next_level = level.get(key)

        # create our next level if it doesn't exist
        if not next_level:
            next_level = {}
            level[key] = next_level

        level = next_level


_anon_user = None


def get_anonymous_user():
    """
    Returns the anonymous user id, originally created by django-guardian
    """

    global _anon_user
    if _anon_user is None:
        from django.contrib.auth.models import User

        _anon_user = User.objects.get(username=settings.ANONYMOUS_USER_NAME)
    return _anon_user
