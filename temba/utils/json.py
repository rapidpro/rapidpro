import datetime
import decimal
import json

import pytz


def load(value):
    """
    Reads the passed in file to a JSON dictionary. The dictionary passed back will be ordered
    and decimal values will be represented as a decimal.Decimal.
    """
    return json.load(value, parse_float=decimal.Decimal)


def loads(value):
    """
    Converts the passed in string to a JSON dictionary. The dictionary passed back will be ordered
    and decimal values will be represented as a decimal.Decimal.
    """
    return json.loads(value, parse_float=decimal.Decimal)


def dumps(value, *args, **kwargs):
    """
    Converts the passed in dictionary into a JSON string. Any decimal.Decimal values will be
    turned into floats.
    """
    return json.dumps(value, *args, cls=TembaEncoder, **kwargs)


def encode_datetime(dt, micros=False):
    """
    Formats a datetime as a string for inclusion in JSON using the format 2018-08-31T12:13:30.123Z which is parseable
    on all modern browsers.
    :param dt: the datetime to format
    :param micros: whether to include microseconds
    """
    # always output as UTC / Z and always include milliseconds
    as_utc = dt.astimezone(pytz.utc)
    as_str = as_utc.strftime("%Y-%m-%dT%H:%M:%S.%f")
    return (as_str if micros else as_str[:-3]) + "Z"


class TembaEncoder(json.JSONEncoder):
    """
    Our own encoder for datetimes.. we always convert to UTC and always include milliseconds
    """

    def default(self, o):
        # See "Date Time String Format" in the ECMA-262 specification.
        if isinstance(o, datetime.datetime):
            return encode_datetime(o)
        elif isinstance(o, decimal.Decimal):
            return float(o)
        else:
            return super().default(o)


class TembaDecoder(json.JSONDecoder):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, parse_float=decimal.Decimal, **kwargs)
