import locale
import resource
from itertools import islice

from django.conf import settings
from django.db import transaction


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


def sizeof_fmt(num, suffix="b"):
    for unit in ["", "K", "M", "G", "T", "P", "E", "Z"]:
        if abs(num) < 1024.0:
            return "%3.1f %s%s" % (num, unit, suffix)
        num /= 1024.0
    return "%.1f %s%s" % (num, "Y", suffix)


def prepped_request_to_str(prepped):
    """
    Graciously cribbed from http://stackoverflow.com/a/23816211
    """
    return "{}\n{}\n\n{}".format(
        prepped.method + " " + prepped.url,
        "\n".join("{}: {}".format(k, v) for k, v in prepped.headers.items()),
        prepped.body,
    )


def splitting_getlist(request, name, default=None):
    """
    Used for backward compatibility in the API where some list params can be provided as comma separated values
    """
    vals = request.query_params.getlist(name, default)
    if vals and len(vals) == 1:
        return vals[0].split(",")
    else:
        return vals


def chunk_list(iterable, size):
    """
    Splits a very large list into evenly sized chunks.
    Returns an iterator of lists that are no more than the size passed in.
    """
    it = iter(iterable)
    item = list(islice(it, size))
    while item:
        yield item
        item = list(islice(it, size))


def print_max_mem_usage(msg=None):
    """
    Prints the maximum RAM used by the process thus far.
    """
    if msg is None:
        msg = "Max usage: "

    locale.setlocale(locale.LC_ALL, "")
    print("")
    print("=" * 80)
    print(msg + locale.format("%d", resource.getrusage(resource.RUSAGE_SELF).ru_maxrss, grouping=True))
    print("=" * 80)


def on_transaction_commit(func):
    """
    Requests that the given function be called after the current transaction has been committed. However function will
    be called immediately if CELERY_TASK_ALWAYS_EAGER is True or if there is no active transaction.
    """
    if getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):
        func()
    else:
        transaction.on_commit(func)


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


def extract_constants(config, reverse=False):
    """
    Extracts a mapping between db and API codes from a constant config in a model
    """
    if reverse:
        return {t[2]: t[0] for t in config}
    else:
        return {t[0]: t[2] for t in config}
