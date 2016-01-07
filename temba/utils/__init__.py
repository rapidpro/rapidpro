from __future__ import unicode_literals

import calendar
import json
import pytz
import random
import datetime
import locale
import resource

from dateutil.parser import parse
from decimal import Decimal
from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection, transaction
from django.utils import timezone
from django.utils.text import slugify
from django.utils.timezone import is_aware
from django.http import HttpResponse
from itertools import islice, chain

DEFAULT_DATE = timezone.now().replace(day=1, month=1, year=1)

# these are not mapped by pytz.country_timezones
INITIAL_TIMEZONE_COUNTRY = {'US/Hawaii': 'US',
                            'US/Alaska': 'US',
                            'Canada/Pacific': 'CA',
                            'US/Pacific': 'US',
                            'Canada/Mountain': 'CA',
                            'US/Arizona': 'US',
                            'US/Mountain': 'US',
                            'Canada/Central': 'CA',
                            'US/Central': 'US',
                            'America/Montreal': 'CA',
                            'Canada/Eastern': 'CA',
                            'US/Eastern': 'US',
                            'Canada/Atlantic': 'CA',
                            'Canada/Newfoundland': 'CA',
                            'GMT': '',
                            'UTC': ''}


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

    try:
        if fill_time:
            date = parse(date_str, dayfirst=dayfirst, fuzzy=True, default=DEFAULT_DATE)
            if date != DEFAULT_DATE:
                output_date = parse(date_str, dayfirst=dayfirst, fuzzy=True, default=timezone.now().astimezone(tz))
                return output_date
            else:
                return None
        else:
            default = datetime.datetime(1, 1, 1, 0, 0, 0, 0, None)
            parsed = parse(date_str, dayfirst=dayfirst, fuzzy=True, default=default)
            parsed = tz.localize(parsed)  # localize in timezone
            return parsed if parsed.year != 1 else None  # only return parsed value if year at least differs from 1CE
    except Exception:
        return None


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


def datetime_to_json_date(dt):
    """
    Formats a datetime as a string for inclusion in JSON
    """
    # always output as UTC / Z and always include milliseconds
    as_utc = dt.astimezone(pytz.utc)
    return as_utc.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'


def json_date_to_datetime(date_str):
    """
    Parses a datetime from a JSON string value
    """
    iso_format = '%Y-%m-%dT%H:%M:%S.%f'
    if date_str.endswith('Z'):
        iso_format += 'Z'
    return datetime.datetime.strptime(date_str, iso_format).replace(tzinfo=pytz.utc)


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
    dt = datetime.datetime.utcfromtimestamp(ms/1000)
    return dt.replace(microsecond=(ms % 1000) * 1000).replace(tzinfo=pytz.utc)


def str_to_bool(text):
    """
    Parses a boolean value from the given text
    """
    return text and text.lower() in ['true', 'y', 'yes', '1']


def build_json_response(json_dict, status=200):
    """
    Helper function to build JSON responses form dictionaries.
    """
    return HttpResponse(json.dumps(json_dict), status=status, content_type='application/json')


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

    val = unicode(val)

    if '.' in val:
        val = val.rstrip('0').rstrip('.')  # e.g. 12.3000 -> 12.3

    return val


def random_string(length):
    """
    Generates a random alphanumeric string
    """
    letters = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"  # avoid things that could be mistaken ex: 'I' and '1'
    return ''.join([random.choice(letters) for _ in range(length)])


def slugify_with(value, sep='_'):
    """
    Slugifies a value using a word separator other than -
    """
    return slugify(value).replace('-', sep)


def truncate(text, max_len):
    """
    Truncates text to be less than max_len characters. If truncation is required, text ends with ...
    """
    if len(text) > max_len:
        return "%s..." % text[:(max_len - 3)]
    else:
        return text


def get_dict_from_cursor(cursor):
    """
    Returns all rows from a cursor as a dict
    """
    desc = cursor.description
    return [
        dict(zip([col[0] for col in desc], row))
        for row in cursor.fetchall()
    ]

class DictStruct(object):
    """
    Wraps a dictionary turning it into a structure looking object. This is useful to 'mock' dictionaries
    coming from Redis to look like normal objects
    """
    def __init__(self, classname, entries, datetime_fields=[]):
        self._classname = classname
        self._values = entries

        # for each of our datetime fields, convert back to datetimes
        for field in datetime_fields:
            value = self._values.get(field, None)
            if value:
                self._values[field] = json_date_to_datetime(value)

        self._initialized = True

    def __getattr__(self, item):
        if not item in self._values:
            raise Exception("%s does not have a %s field" % (self._classname, item))

        return self._values[item]

    def __setattr__(self, item, value):
        # needed to prevent infinite loop
        if not self.__dict__.has_key('_initialized'):
            return object.__setattr__(self, item, value)

        if not item in self._values:
            raise Exception("%s does not have a %s field" % (self._classname, item))

        self._values[item] = value

    def __unicode__(self):
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
        if isinstance(v, basestring):
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


class PageableQuery(object):
    """
    Allows paging with Paginator of a raw SQL query
    """
    def __init__(self, query, order=(), params=()):
        self.query = query
        self.order = order
        self.params = params
        self._count = None

    def __len__(self):
        return self.count()

    def __getitem__(self, item):
        offset, stop, step = item.indices(self.count())
        limit = stop - offset
        return self.execute(offset, limit)

    def execute(self, offset, limit):
        cursor = connection.cursor()

        if self.order:
            ordering_clauses = [("%s DESC" % col[1:]) if col[0] == '-' else ("%s ASC" % col) for col in self.order]
            query = "%s ORDER BY %s" % (self.query, ", ".join(ordering_clauses))
        else:
            query = self.query

        query = "%s OFFSET %s LIMIT %s" % (query, offset, limit)
        cursor.execute(query, self.params)
        return get_dict_from_cursor(cursor)

    def count(self):
        if self._count is not None:
            return self._count

        cursor = connection.cursor()
        cursor.execute("SELECT count(*) FROM (%s) s" % self.query, self.params)
        self._count = cursor.fetchone()[0]
        return self._count


class JsonResponse(HttpResponse):
    """
    Borrowed from Django 1.7 until we upgrade to that...
    """
    def __init__(self, data, encoder=DjangoJSONEncoder, safe=True, **kwargs):
        if safe and not isinstance(data, dict):
            raise TypeError('In order to allow non-dict objects to be serialized set the safe parameter to False')
        kwargs.setdefault('content_type', 'application/json')
        data = json.dumps(data, cls=encoder)
        super(JsonResponse, self).__init__(content=data, **kwargs)


def non_atomic_when_eager(view_func):
    """
    Decorator which disables atomic requests for a view/dispatch function when celery is running in eager mode
    """
    if getattr(settings, 'CELERY_ALWAYS_EAGER', False):
        return transaction.non_atomic_requests(view_func)
    else:
        return view_func


def non_atomic_gets(view_func):
    """
    Decorator which disables atomic requests for a view/dispatch function when the request method is GET. Works in
    conjunction with the NonAtomicGetsMiddleware.
    """
    view_func._non_atomic_gets = True
    return view_func


def timezone_to_country_code(tz):
    country_timezones = pytz.country_timezones

    timezone_country = INITIAL_TIMEZONE_COUNTRY
    for countrycode in country_timezones:
        timezones = country_timezones[countrycode]
        for timezone in timezones:
            timezone_country[timezone] = countrycode

    return timezone_country.get(tz, '')

def splitting_getlist(request, name, default=None):
    vals = request.QUERY_PARAMS.getlist(name, default)
    if vals and len(vals) == 1:
        return vals[0].split(',')
    else:
        return vals

def chunk_list(iterable, size):
    """
    Splits a very large list into evenly sized chunks.
    Returns an iterator of lists that are no more than the size passed in.
    """
    source_iter = iter(iterable)
    while True:
        chunk_iter = islice(source_iter, size)
        yield chain([chunk_iter.next()], chunk_iter)


def print_max_mem_usage(msg=None):
    """
    Prints the maximum RAM used by the process thus far.
    """
    if msg is None:
        msg = "Max usage: "

    locale.setlocale(locale.LC_ALL, '')
    print
    print "=" * 80
    print msg + locale.format("%d", resource.getrusage(resource.RUSAGE_SELF).ru_maxrss, grouping=True)
    print "=" * 80