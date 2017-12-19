from __future__ import print_function, unicode_literals

import calendar
import datetime
import iso8601
import pytz
import regex
import six

from dateutil.parser import parse
from django.utils import timezone

DEFAULT_DATE = datetime.datetime(1, 1, 1, 0, 0, 0, 0, None)
MAX_UTC_OFFSET = 14 * 60 * 60  # max offset postgres supports for a timezone
FULL_ISO8601_REGEX = regex.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{6})?([\+\-]\d{2}:\d{2}|Z)$')

DD_MM_YYYY = regex.compile(r'([0-9]{1,4})[-.\\/_ ]([0-9]{1,2})[-.\\/_ ]([0-9]{4,5})')
DD_MM_YY = regex.compile(r'([0-9]{1,4})[-.\\/_ ]([0-9]{1,2})[-.\\/_ ]([0-9]{2,3})')
MM_DD_YYYY = regex.compile(r'([0-9]{1,4})[-.\\/_ ]([0-9]{1,2})[-.\\/_ ]([0-9]{4,5})')
MM_DD_YY = regex.compile(r'([0-9]{1,4})[-.\\/_ ]([0-9]{1,2})[-.\\/_ ]([0-9]{2,3})')
YYYY_MM_DD = regex.compile(r'([0-9]{4})[-.\\/_ ]([0-9]{1,2})[-.\\/_ ]([0-9]{1,4})')
YY_MM_DD = regex.compile(r'([0-9]{1,4})[-.\\/_ ]([0-9]{1,2})[-.\\/_ ]([0-9]{1,4})')
HH_MM_SS = regex.compile(r'([0-9]{1,4}):([0-9]{2})(:([0-9]{2})(\.(\d+))?)?\W*([aApP][mM])?')


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
    :param tz: the timezone of the date if not included
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
            return iso8601.parse_date(date_str)
        except iso8601.ParseError:
            pass

    current_year = datetime.datetime.now().year

    if dayfirst:
        parsed = _date_from_formats(date_str, current_year, DD_MM_YYYY, DD_MM_YY, 1, 2, 3)
    else:
        parsed = _date_from_formats(date_str, current_year, MM_DD_YYYY, MM_DD_YY, 2, 1, 3)

    # couldn't find a date? bail
    if not parsed:
        return None

    # can we pull out a time?
    time = None
    for match in HH_MM_SS.finditer(date_str):
        hour = _atoi(match[1])

        # do we have an AM/PM
        if match[7] and match[7].lower() == "pm":
            hour += 12

        # is this a valid hour?
        if hour > 24:
            continue

        minute = _atoi(match[2])
        if minute > 60:
            continue

        seconds = 0
        if match[4]:
            seconds = _atoi(match[4])
            if seconds > 60:
                continue

        micro = 0
        if match[6]:
            micro = _atoi(match[6])

            if len(match[6]) == 3:
                # these are milliseconds, multi by 1,000,000 for micro
                micro *= 1000

        time = datetime.time(hour, minute, seconds, micro)
        break

    if time:
        parsed = datetime.datetime.combine(parsed, time)
    elif fill_time:
        parsed = datetime.datetime.combine(parsed, timezone.now().time())
    else:
        parsed = datetime.datetime.combine(parsed, datetime.time(0, 0, 0))

    # set our timezone if we have one
    if tz and parsed:
        parsed = tz.localize(parsed)

    return parsed


def _date_from_formats(date_str, current_year, four_digit, two_digit, d, m, y):

    # four digit year comes first
    for match in four_digit.finditer(date_str):
        # does our day look believable?
        day = _atoi(match[d])
        if day == 0 or day > 31:
            continue

        month = _atoi(match[m])
        if month == 0 or month > 12:
            continue

        year = _atoi(match[y])

        # looks believable, let's return it
        return datetime.date(year, month, day)

    # then two digit
    for match in two_digit.finditer(date_str):
        # does our day look believable?
        day = _atoi(match[d])
        if day == 0 or day > 31:
            continue

        month = _atoi(match[m])
        if month == 0 or month > 12:
            continue

        # convert to four digit year
        year = _atoi(match[y])
        if year > current_year % 1000:
            year += 1900
        else:
            year += 2000

        # looks believable, go for it
        return datetime.date(year, month, day)

    return None


def str_to_datetime_old(date_str, tz, dayfirst=True, fill_time=True):
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


def _atoi(s):
    try:
        return int(s)
    except ValueError:
        return 0
