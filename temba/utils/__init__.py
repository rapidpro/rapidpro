import locale
import resource
from itertools import islice

import iso8601
from django_countries import countries

from django.conf import settings
from django.db import transaction

DTONE_COUNTRY_NAMES = {"Democratic Republic of the Congo": "CD", "Ivory Coast": "CI", "United States": "US"}


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


def get_dict_from_cursor(cursor):
    """
    Returns all rows from a cursor as a dict
    """
    desc = cursor.description
    return [dict(zip([col[0] for col in desc], row)) for row in cursor.fetchall()]


class DictStruct(object):
    """
    Wraps a dictionary turning it into a structure looking object. This is useful to 'mock' dictionaries
    coming from Redis to look like normal objects
    """

    def __init__(self, classname, entries, datetime_fields=()):
        self._classname = classname
        self._values = entries

        # for each of our datetime fields, convert back to datetimes
        for field in datetime_fields:
            value = self._values.get(field, None)
            if value:
                self._values[field] = iso8601.parse_date(value)

        self._initialized = True

    def __getattr__(self, item):
        if item not in self._values:
            raise AttributeError("%s does not have a %s field" % (self._classname, item))

        return self._values[item]

    def __setattr__(self, item, value):
        # needed to prevent infinite loop
        if "_initialized" not in self.__dict__:
            return object.__setattr__(self, item, value)

        if item not in self._values:
            raise AttributeError("%s does not have a %s field" % (self._classname, item))

        self._values[item] = value

    def __str__(self):
        return "%s [%s]" % (self._classname, self._values)


def dict_to_struct(classname, attributes, datetime_fields=()):
    """
    Given a classname and a dictionary will return an object that allows for dot access to
    the passed in attributes.

    ex: dict_to_struct('MsgStruct', attributes)
    """
    return DictStruct(classname, attributes, datetime_fields)


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


def get_country_code_by_name(name):
    code = countries.by_name(name)

    if not code:
        code = DTONE_COUNTRY_NAMES.get(name, None)

    return code if code else None


def on_transaction_commit(func):
    """
    Requests that the given function be called after the current transaction has been committed. However function will
    be called immediately if CELERY_ALWAYS_EAGER is True or if there is no active transaction.
    """
    if getattr(settings, "CELERY_ALWAYS_EAGER", False):
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
