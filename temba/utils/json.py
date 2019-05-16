import datetime

import pytz
import simplejson


def loads(value):
    """
    Converts the passed in string to a JSON dictionary. The dictionary passed back will be ordered
    and decimal values will be represented as a decimal.Decimal.
    """
    return simplejson.loads(value, use_decimal=True)


def dumps(value, *args, **kwargs):
    """
    Converts the passed in dictionary into a JSON string. Any decimal.Decimal values will be
    turned into floats.
    """
    return simplejson.dumps(value, *args, cls=TembaEncoder, use_decimal=True, **kwargs)


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


class TembaEncoder(simplejson.JSONEncoder):
    """
    Our own encoder for datetimes.. we always convert to UTC and always include milliseconds
    """

    def default(self, o):
        # See "Date Time String Format" in the ECMA-262 specification.
        if isinstance(o, datetime.datetime):
            return encode_datetime(o)
        else:
            return super().default(o)


def find_nodes(j, matcher, callback):
    """
    Recursively looks for nodes in parsed JSON that match
    :param j: the current parsed JSON
    :param matcher: a callable which returns whether a node matches
    :param callback: a callable invoked with any nodes that do match
    """
    if matcher(j):
        callback(j)

    if isinstance(j, dict):
        for v in j.values():
            find_nodes(v, matcher, callback)
    elif isinstance(j, list):
        for i in j:
            find_nodes(i, matcher, callback)


def remap_values(j, mapper):
    """
    Recursively remaps string values in parsed JSON
    :param j: the current parsed JSON
    :param mapper: a callable map function
    :return: the remapped JSON
    """
    if isinstance(j, dict):
        return {k: remap_values(v, mapper) for k, v in j.items()}
    elif isinstance(j, list):
        return [remap_values(i, mapper) for i in j]
    elif isinstance(j, str):
        return mapper(j)
    return j
