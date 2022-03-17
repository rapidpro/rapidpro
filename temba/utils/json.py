import datetime
import decimal
import json
import re

import psycopg2.extensions
import psycopg2.extras
import pytz
import simplejson
from django.http import HttpResponse
from django.utils.timezone import is_aware


def load(value):
    """
    Reads the passed in file to a JSON dictionary. The dictionary passed back will be ordered
    and decimal values will be represented as a decimal.Decimal.
    """
    return json.load(value, parse_float=decimal.Decimal)


def loads(value, object_hook=None):
    """
    Converts the passed in string to a JSON dictionary. The dictionary passed back will be ordered
    and decimal values will be represented as a decimal.Decimal.
    """
    return json.loads(value, object_hook=object_hook, parse_float=decimal.Decimal)


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


DATETIME_FORMAT = "%Y-%m-%dT%H:%M:%S.%f"
DATETIME_FORMAT_REGEX = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")


def decode_datetime(value):
    if isinstance(value, dict) or isinstance(value, list):
        for k, v in value.items() if isinstance(value, dict) else enumerate(value):
            if isinstance(v, str) and DATETIME_FORMAT_REGEX.match(v):
                value[k] = datetime.datetime.strptime(v.removesuffix("Z"), DATETIME_FORMAT)
            if isinstance(v, dict) or isinstance(v, list):
                decode_datetime(v)
    return value


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


class TembaJsonAdapter(psycopg2.extras.Json):
    """
    Json adapter for psycopg2 that uses Temba specific `dumps` that serializes numbers as Decimal types
    """

    def dumps(self, o, **kwargs):
        return dumps(o, **kwargs)


# register UJsonAdapter for all dict Python types
psycopg2.extensions.register_adapter(dict, TembaJsonAdapter)
# register global json python encoders
psycopg2.extras.register_default_jsonb(loads=loads, globally=True)
psycopg2.extras.register_default_json(loads=loads, globally=True)


def default_json_encoder(o):
    if isinstance(o, datetime.datetime):
        r = o.isoformat()
        if o.microsecond:
            r = r[:23] + r[26:]
        if r.endswith("+00:00"):
            r = r[:-6] + "Z"
        return r
    elif isinstance(o, datetime.date):
        return o.isoformat()
    elif isinstance(o, datetime.time):
        if is_aware(o):
            raise ValueError("JSON can't represent timezone-aware times.")
        r = o.isoformat()
        if o.microsecond:
            r = r[:12]
        return r
    else:
        raise TypeError(repr(o) + " is not JSON serializable")


class JsonResponse(HttpResponse):
    """
    JsonResponse encode a dictionary into json format and it handle datetime and decimal types. The problem is JsonResponse encodes decimal to strings. { "minutes" : Decimal('10.1')} becoming { "minutes" : "10.0"} which was causing problems. This modified version transform to { "minutes" : 10.0} as intended.
    """

    def __init__(self, data, safe=True, **kwargs):
        if safe and not isinstance(data, dict):
            raise TypeError("In order to allow non-dict objects to be " "serialized set the safe parameter to False")
        kwargs.setdefault("content_type", "application/json")
        data = simplejson.dumps(data, default=default_json_encoder)
        super(JsonResponse, self).__init__(content=data, **kwargs)
