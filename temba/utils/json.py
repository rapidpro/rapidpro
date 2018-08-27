import collections
import datetime
import decimal

import pytz
import simplejson

from django.utils.timezone import is_aware


def loads(value):
    """
    Converts the passed in string to a JSON dictionary. The dictionary passed back will be ordered
    and decimal values will be represented as a decimal.Decimal.
    """
    return simplejson.loads(value, use_decimal=True, object_pairs_hook=collections.OrderedDict)


def dumps(value, *args, **kwargs):
    """
    Converts the passed in dictionary into a JSON string. Any decimal.Decimal values will be
    turned into floats.
    """
    return simplejson.dumps(value, *args, cls=TembaEncoder, use_decimal=True, **kwargs)


def datetime_to_json_date(dt, micros=False):
    """
    Formats a datetime as a string for inclusion in JSON
    :param dt: the datetime to format
    :param micros: whether to include microseconds
    """
    # always output as UTC / Z and always include milliseconds
    as_utc = dt.astimezone(pytz.utc)
    as_str = as_utc.strftime("%Y-%m-%dT%H:%M:%S.%f")
    return (as_str if micros else as_str[:-3]) + "Z"


"""
Custom encoder that takes care of converting datetimes and Decimal values to the appropriate JSON
encodings.
"""


class TembaEncoder(simplejson.JSONEncoder):
    """
    Our own encoder for datetimes.. we always convert to UTC and always include milliseconds
    """

    def default(self, o):
        # See "Date Time String Format" in the ECMA-262 specification.
        if isinstance(o, datetime.datetime):
            return datetime_to_json_date(o)
        elif isinstance(o, datetime.date):
            return o.isoformat()
        elif isinstance(o, datetime.time):
            if is_aware(o):
                raise ValueError("JSON can't represent timezone-aware times.")
            r = o.isoformat()
            if o.microsecond:
                r = r[:12]
            return r
        elif isinstance(o, decimal.Decimal):
            return str(o)
        else:
            return super().default(o)
