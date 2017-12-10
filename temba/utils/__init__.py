from __future__ import print_function, unicode_literals

import calendar
import datetime
import iso8601
import json
import locale
import pytz
import regex
import resource
import six

from dateutil.parser import parse
from decimal import Decimal
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.timezone import is_aware
from django_countries import countries
from itertools import islice

DEFAULT_DATE = datetime.datetime(1, 1, 1, 0, 0, 0, 0, None)
MAX_UTC_OFFSET = 14 * 60 * 60  # max offset postgres supports for a timezone
FULL_ISO8601_REGEX = regex.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{6})?[\+\-]\d{2}:\d{2}$')


TRANSFERTO_COUNTRY_NAMES = {
    'Democratic Republic of the Congo': 'CD',
    'Ivory Coast': 'CI',
    'United States': 'US',
}


def datetime_to_str(date_obj, format=None, ms=True, tz=None):
    """
    Formats a datetime or date as a string
    :param date_obj: the datetime or date
    :param format: the format (defaults to ISO8601)
    :param ms: whether to include microseconds
    :param tz: the timezone to localize in
    :return: the formatted date string
    """
    if not date_obj:
        return None

    if not tz:
        tz = timezone.utc

    if type(date_obj) == datetime.date:
        date_obj = tz.localize(datetime.datetime.combine(date_obj, datetime.time(0, 0, 0)))

    if date_obj.year < 1900:
        return "%d-%d-%dT%d:%d:%d.%dZ" % (date_obj.year, date_obj.month, date_obj.day, date_obj.hour, date_obj.minute, date_obj.second, date_obj.microsecond)

    if isinstance(date_obj, datetime.datetime):
        date_obj = timezone.localtime(date_obj, tz)

    if not format or not tz:
        if ms:
            return date_obj.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
        else:
            return date_obj.strftime('%Y-%m-%dT%H:%M:%SZ')
    else:
        return date_obj.strftime(format)


def str_to_datetime(date_str, tz, dayfirst=True, fill_time=True):
    """
    Parses a datetime from the given text value
    :param date_str: the string containing a date
    :param tz: the timezone of the date
    :param dayfirst: whether the date has been entered date first or month first
    :param fill_time: whether or not to fill missing time with the current time
    :return: the parsed datetime
    """
    if not date_str:
        return None

    # remove whitespace any trailing period
    date_str = six.text_type(date_str).strip().rstrip('.')

    # try first as full ISO string
    if FULL_ISO8601_REGEX.match(date_str):
        try:
            return iso8601.parse_date(date_str).astimezone(tz)
        except iso8601.ParseError:
            pass

    try:
        if fill_time:
            date = parse(date_str, dayfirst=dayfirst, fuzzy=True, default=DEFAULT_DATE)

            # get the local time and hour
            default = timezone.now().astimezone(tz).replace(tzinfo=None)

            # we parsed successfully
            if date.tzinfo or date != DEFAULT_DATE:
                output_date = parse(date_str, dayfirst=dayfirst, fuzzy=True, default=default)

                # localize it if we don't have a timezone
                if not output_date.tzinfo:
                    output_date = tz.localize(output_date)

                # if we aren't UTC, normalize to take care of any DST weirdnesses
                elif output_date.tzinfo.tzname(output_date) != 'UTC':
                    output_date = tz.normalize(output_date)

            else:
                output_date = None
        else:
            default = DEFAULT_DATE
            output_date = parse(date_str, dayfirst=dayfirst, fuzzy=True, default=default)
            output_date = tz.localize(output_date)

            # only return date if it actually got parsed
            if output_date.year == 1:
                output_date = None

    except Exception:
        output_date = None

    # if we've been parsed into something Postgres can't store (offset is > 12 hours) then throw it away
    if output_date and abs(output_date.utcoffset().total_seconds()) > MAX_UTC_OFFSET:
        output_date = None

    return output_date


def str_to_time(value):
    """
    Parses a time value from the given text value
    """
    default = datetime.datetime(1, 1, 1, 0, 0, 0, 0, None)
    parsed = parse(value, fuzzy=True, default=default)
    return parsed.time()


def get_datetime_format(dayfirst):
    if dayfirst:
        format_date = "%d-%m-%Y"
    else:
        format_date = "%m-%d-%Y"

    format_time = format_date + " %H:%M"

    return format_date, format_time


def datetime_to_json_date(dt, micros=False):
    """
    Formats a datetime as a string for inclusion in JSON
    :param dt: the datetime to format
    :param micros: whether to include microseconds
    """
    # always output as UTC / Z and always include milliseconds
    as_utc = dt.astimezone(pytz.utc)
    as_str = as_utc.strftime('%Y-%m-%dT%H:%M:%S.%f')
    return (as_str if micros else as_str[:-3]) + 'Z'


def json_date_to_datetime(date_str):
    """
    Parses a datetime from a JSON string value
    """
    iso_format = '%Y-%m-%dT%H:%M:%S.%f'
    if date_str.endswith('Z'):
        iso_format += 'Z'
    return datetime.datetime.strptime(date_str, iso_format).replace(tzinfo=pytz.utc)


def datetime_to_s(dt):
    """
    Converts a datetime to a fractional second epoch
    """
    seconds = calendar.timegm(dt.utctimetuple())
    return seconds + dt.microsecond / float(100000)


def datetime_to_ms(dt):
    """
    Converts a datetime to a millisecond accuracy timestamp
    """
    seconds = calendar.timegm(dt.utctimetuple())
    return seconds * 1000 + dt.microsecond / 1000


def ms_to_datetime(ms):
    """
    Converts a millisecond accuracy timestamp to a datetime
    """
    dt = datetime.datetime.utcfromtimestamp(ms / 1000)
    return dt.replace(microsecond=(ms % 1000) * 1000).replace(tzinfo=pytz.utc)


def datetime_to_epoch(dt):
    """
    Converts a datetime to seconds since 1970
    """
    utc_naive = dt.replace(tzinfo=None) - dt.utcoffset()
    return (utc_naive - datetime.datetime(1970, 1, 1)).total_seconds()


def date_to_utc_range(d, org):
    """
    Converts a date in the given org's timezone, to a range of datetimes in UTC
    """
    local_midnight = org.timezone.localize(datetime.datetime.combine(d, datetime.time(0, 0)))
    utc_midnight = local_midnight.astimezone(pytz.UTC)
    return utc_midnight, utc_midnight + datetime.timedelta(days=1)


def str_to_bool(text):
    """
    Parses a boolean value from the given text
    """
    return text and text.lower() in ['true', 'y', 'yes', '1']


def percentage(numerator, denominator):
    """
    Returns an integer percentage as an integer for the passed in numerator and denominator.
    """
    if not denominator or not numerator:
        return 0

    return int(100.0 * numerator / denominator + .5)


def format_decimal(val):
    """
    Formats a decimal value without trailing zeros
    """
    if val is None:
        return ''
    elif val == 0:
        return '0'

    val = six.text_type(val)

    if '.' in val:
        val = val.rstrip('0').rstrip('.')  # e.g. 12.3000 -> 12.3

    return val


def get_dict_from_cursor(cursor):
    """
    Returns all rows from a cursor as a dict
    """
    desc = cursor.description
    return [
        dict(zip([col[0] for col in desc], row))
        for row in cursor.fetchall()
    ]


@six.python_2_unicode_compatible
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
                self._values[field] = json_date_to_datetime(value)

        self._initialized = True

    def __getattr__(self, item):
        if item not in self._values:
            raise Exception("%s does not have a %s field" % (self._classname, item))

        return self._values[item]

    def __setattr__(self, item, value):
        # needed to prevent infinite loop
        if '_initialized' not in self.__dict__:
            return object.__setattr__(self, item, value)

        if item not in self._values:
            raise Exception("%s does not have a %s field" % (self._classname, item))

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
    return '{}\n{}\n\n{}'.format(
        prepped.method + ' ' + prepped.url,
        '\n'.join('{}: {}'.format(k, v) for k, v in prepped.headers.items()),
        prepped.body,
    )


class DateTimeJsonEncoder(json.JSONEncoder):
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
        elif isinstance(o, Decimal):
            return str(o)
        else:
            return super(DateTimeJsonEncoder, self).default(o)


def dict_to_json(dictionary):
    """
    Converts a dictionary to JSON, taking care of converting dates as needed.
    """
    return json.dumps(dictionary, cls=DateTimeJsonEncoder)


def datetime_decoder(d):
    """
    Looks through strings in a dictionary trying to find things that look like date or
    datetimes and converting them back to datetimes.
    """
    if isinstance(d, list):
        pairs = enumerate(d)
    elif isinstance(d, dict):
        pairs = d.items()
    result = []
    for k, v in pairs:
        if isinstance(v, six.string_types):
            try:
                # The %f format code is only supported in Python >= 2.6.
                # For Python <= 2.5 strip off microseconds
                # v = datetime.datetime.strptime(v.rsplit('.', 1)[0],
                #     '%Y-%m-%dT%H:%M:%S')
                v = json_date_to_datetime(v)
            except ValueError:
                pass
        elif isinstance(v, (dict, list)):
            v = datetime_decoder(v)
        result.append((k, v))
    if isinstance(d, list):
        return [x[1] for x in result]
    elif isinstance(d, dict):
        return dict(result)


def json_to_dict(json_string):
    """
    Converts an incoming json string to a Python dictionary trying to detect datetime fields and convert them
    to Python objects. (you shouldn't do this with untrusted input)
    """
    return json.loads(json_string, object_hook=datetime_decoder)


def splitting_getlist(request, name, default=None):
    """
    Used for backward compatibility in the API where some list params can be provided as comma separated values
    """
    vals = request.query_params.getlist(name, default)
    if vals and len(vals) == 1:
        return vals[0].split(',')
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

    locale.setlocale(locale.LC_ALL, '')
    print("")
    print("=" * 80)
    print(msg + locale.format("%d", resource.getrusage(resource.RUSAGE_SELF).ru_maxrss, grouping=True))
    print("=" * 80)


def get_country_code_by_name(name):
    code = countries.by_name(name)

    if not code:
        code = TRANSFERTO_COUNTRY_NAMES.get(name, None)

    return code if code else None


def on_transaction_commit(func):
    """
    Requests that the given function be called after the current transaction has been committed. However function will
    be called immediately if CELERY_ALWAYS_EAGER is True or if there is no active transaction.
    """
    if getattr(settings, 'CELERY_ALWAYS_EAGER', False):
        func()
    else:
        transaction.on_commit(func)


def get_anonymous_user():
    """
    Returns the anonymous user, originally created by django-guardian
    """
    from django.contrib.auth.models import User
    return User.objects.get(username=settings.ANONYMOUS_USER_NAME)
