from __future__ import unicode_literals

import calendar
from decimal import Decimal
import json
from django.utils.timezone import is_aware
import pytz
import random
import datetime

from dateutil.parser import parse
from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection, transaction
from django.utils import timezone
from django.utils.text import slugify
from django.http import HttpResponse

DEFAULT_DATE = timezone.now().replace(day=1, month=1, year=1)


def datetime_to_str(date_obj, format=None, ms=True, tz=None):
    if not date_obj:
        return None

    if date_obj.year < 1900:
        return "%d-%d-%dT%d:%d:%d.%dZ" % (date_obj.year, date_obj.month, date_obj.day, date_obj.hour, date_obj.minute, date_obj.second, date_obj.microsecond)

    if not tz:
        tz = timezone.utc

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


def json_date_to_datetime(date_str):
    return datetime.datetime.strptime(date_str, '%Y-%m-%dT%H:%M:%S.%f').replace(tzinfo=pytz.utc)


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


def build_json_response(json_dict, status=200):
    """
    Helper function to build JSON responses form dictionaries.
    """
    return HttpResponse(json.dumps(json_dict), status=status, content_type='application/json')


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


def get_preferred_language(language_dict, preferred_languages):

    # If language_dict is not a dict, the original value is returned
    if not isinstance(language_dict, dict):
        return language_dict

    for lang in preferred_languages:
        localized = language_dict.get(lang, None)
        if localized:
            return localized
    return None


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
                self._values[field] = datetime.datetime.strptime(value, '%Y-%m-%dT%H:%M:%S.%fZ').replace(tzinfo=pytz.utc)

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


class DateTimeJsonEncoder(json.JSONEncoder):
    """
    Our own encoder for datetimes.. we always convert to UTC and always include milliseconds
    """
    def default(self, o):
        # See "Date Time String Format" in the ECMA-262 specification.
        if isinstance(o, datetime.datetime):
            # always output as UTC / Z and always include milliseconds
            as_utc = o.astimezone(pytz.utc)
            r = as_utc.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
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
                v = datetime.datetime.strptime(v, '%Y-%m-%dT%H:%M:%S.%fZ').replace(tzinfo=pytz.utc)
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
