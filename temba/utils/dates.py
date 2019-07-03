import calendar
import datetime

import iso8601
import pytz
import regex

from django.utils import timezone

# max offset postgres supports for a timezone
MAX_UTC_OFFSET = 14 * 60 * 60

# pattern for any date which should be parsed by the ISO8601 library (assumed to be not human-entered)
FULL_ISO8601_REGEX = regex.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.(\d{,9}))?([\+\-]\d{2}:\d{2}|Z)$")
ISO_YYYY_MM_DD = regex.compile(r"^([0-9]{4})-([0-9]{2})-([0-9]{2})$")

# patterns for date and time formats supported for human-entered data
DD_MM_YYYY = regex.compile(r"\b([0-9]{1,2})[-.\\/_]([0-9]{1,2})[-.\\/_]([0-9]{4}|[0-9]{2})\b")
YYYY_MM_DD = regex.compile(r"\b([0-9]{4})[-.\\/_]([0-9]{1,2})[-.\\/_]([0-9]{1,2})\b")
HH_MM_SS = regex.compile(r"\b([0-9]{1,2}):([0-9]{2})(:([0-9]{2})(\.(\d+))?)?\W*([aApP][mM])?\b")


def datetime_to_str(date_obj, format, tz):
    """
    Formats a datetime or date as a string
    :param date_obj: the datetime or date
    :param format: the format
    :param tz: the timezone to localize in
    :return: the formatted date string
    """
    if not date_obj:
        return None

    if type(date_obj) == datetime.date:
        date_obj = tz.localize(datetime.datetime.combine(date_obj, datetime.time(0, 0, 0)))

    if isinstance(date_obj, datetime.datetime):
        date_obj = timezone.localtime(date_obj, tz)

    return date_obj.strftime(format)


def str_to_date(date_str, dayfirst=True):
    """
    Parses a date from the given text value

    Function uses several regex expressions to extract a valid date.

    Initially FULL_ISO8601_REGEX will match ISO8601 timetamp strings mostly used
    in JSON serialized timestamps.
        2013-02-01T04:38:09.100000+02:00 -> 2013-02-01

    Following test will match a ISO8601 date and only that, a format of the
    string must conform to: YYYY-MM-DD

    Next test is matching dates that are similar to the ISO8601. The date must
    start with a year and it is followed by either month or a day. Day and month
    have an optional leading zero, and year must have 4 characters. In this test
    ORG level attribute `date_format` is applied.
        2019-06-18, dayfirst=True -> 2019-06-18
        2019-18-06, dayfirst=False -> 2019-06-18
    Following delimiters are supported:
        2019-06-18
        2019.6.18
        2019\06\18
        2019/06/18
        2019_6_18
        2019-6/02

    The last test is similar to the previous one because it supports same
    delimiters and honours `Org.date_format`, but in this we check for dates
    that end with a year  and start with either day or the month. Year can have
    either 2 or 4 characters:
        18-06-2019
        06/18/2019
        6-18-19

    Note:
        `Org.date_format` can produce incorrect dates. For example, 2018-06-11
        is a valid date in both cases:
            dayfirst=True -> 2018-06-11
            dayfirst=False -> 2018-11-06

    Returns:
        date|None: datetime.date object
    """
    if not date_str:
        return None

    # try first as full ISO string
    if FULL_ISO8601_REGEX.match(date_str):
        try:
            return iso8601.parse_date(date_str).date()
        except iso8601.ParseError:  # pragma: no cover
            pass

    # is this an iso date ?
    parsed = _date_from_formats(date_str, ISO_YYYY_MM_DD, 3, 2, 1)

    # maybe it is similar to iso date ?
    if not parsed:
        if dayfirst:
            parsed = _date_from_formats(date_str, YYYY_MM_DD, 3, 2, 1)
        else:
            parsed = _date_from_formats(date_str, YYYY_MM_DD, 2, 3, 1)

    # no? then try org specific formats
    if not parsed:
        if dayfirst:
            parsed = _date_from_formats(date_str, DD_MM_YYYY, 1, 2, 3)
        else:
            parsed = _date_from_formats(date_str, DD_MM_YYYY, 2, 1, 3)

    return parsed


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
    date_str = str(date_str).strip().rstrip(".")

    # try first as full ISO string
    if FULL_ISO8601_REGEX.match(date_str):
        try:
            return iso8601.parse_date(date_str)
        except iso8601.ParseError:  # pragma: no cover
            pass

    parsed = str_to_date(date_str, dayfirst)

    if not parsed:
        return None

    # can we pull out a time?
    time = str_to_time(date_str)

    if time is not None:
        parsed = datetime.datetime.combine(parsed, time)
    elif fill_time:
        parsed = datetime.datetime.combine(parsed, timezone.now().astimezone(tz).time())
    else:
        parsed = datetime.datetime.combine(parsed, datetime.time(0, 0, 0))

    # set our timezone if we have one
    if tz and parsed:
        parsed = tz.localize(parsed)

    # if we've been parsed into something Postgres can't store (offset is > 12 hours) then throw it away
    if parsed and abs(parsed.utcoffset().total_seconds()) > MAX_UTC_OFFSET:  # pragma: no cover
        parsed = None

    return parsed


def _date_from_formats(date_str, pattern, d, m, y):
    """
    Parses a human-entered date which should be in the org display format
    """

    for match in pattern.finditer(date_str):
        day = _atoi(match[d])
        month = _atoi(match[m])
        year = _atoi(match[y])

        # convert to four digit year
        if len(match[y]) == 2:
            current_year = datetime.datetime.now().year

            if year > current_year % 1000:
                year += 1900
            else:
                year += 2000

        try:
            return datetime.date(year, month, day)
        except ValueError:
            # if our numbers don't form a valid date keep trying
            pass

    return None


def str_to_time(value):
    """
    Parses a time value from the given text value
    """
    for match in HH_MM_SS.finditer(value):
        hour = _atoi(match[1])
        minute = _atoi(match[2])

        # do we have an AM/PM marker?
        am_pm = match[7].lower() if match[7] else None

        if hour < 12 and am_pm == "pm":
            hour += 12
        elif hour == 12 and am_pm == "am":
            hour -= 12

        seconds = 0
        micro = 0

        if match[4]:
            seconds = _atoi(match[4])

            if match[6]:
                micro = _atoi(match[6])

                if len(match[6]) == 3:
                    # these are milliseconds, multi by 1,000,000 for micro
                    micro *= 1000

        try:
            return datetime.time(hour, minute, seconds, micro)
        except ValueError:
            # if our numbers don't form a valid time keep trying
            pass

    return None


def get_datetime_format(dayfirst):
    if dayfirst:
        format_date = "%d-%m-%Y"
    else:
        format_date = "%m-%d-%Y"

    format_time = format_date + " %H:%M"

    return format_date, format_time


def datetime_to_ms(dt):
    """
    Converts a datetime to a millisecond accuracy timestamp
    """
    seconds = calendar.timegm(dt.utctimetuple())
    return seconds * 1000 + dt.microsecond // 1000


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


def date_to_day_range_utc(input_date, org):
    """
    Converts a date in the given org's timezone, to a range of datetimes in UTC
    """
    local_midnight = org.timezone.localize(datetime.datetime.combine(input_date, datetime.datetime.min.time()))

    utc_midnight = local_midnight.astimezone(pytz.UTC)

    return utc_midnight, utc_midnight + datetime.timedelta(days=1)


def _atoi(s):
    try:
        return int(s)
    except ValueError:  # pragma: no cover
        return 0
