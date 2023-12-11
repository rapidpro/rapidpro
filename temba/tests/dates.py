import datetime

import iso8601
import regex

from django.utils import timezone

from temba.orgs.models import Org

# max offset postgres supports for a timezone
MAX_UTC_OFFSET = 14 * 60 * 60

# pattern for any date which should be parsed by the ISO8601 library (assumed to be not human-entered)
FULL_ISO8601_REGEX = regex.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?(\.(\d{,9}))?([\+\-]\d{2}:\d{2}|Z)$")
ISO_YYYY_MM_DD = regex.compile(r"^([0-9]{4})-([0-9]{2})-([0-9]{2})$")

# patterns for date and time formats supported for human-entered data
DD_MM_YYYY = regex.compile(r"\b([0-9]{1,2})[-.\\/_]([0-9]{1,2})[-.\\/_]([0-9]{4}|[0-9]{2})\b")
YYYY_MM_DD = regex.compile(r"\b([0-9]{4})[-.\\/_]([0-9]{1,2})[-.\\/_]([0-9]{1,2})\b")
HH_MM_SS = regex.compile(r"\b([0-9]{1,2}):([0-9]{2})(:([0-9]{2})(\.(\d+))?)?\W*([aApP][mM])?\b")


def parse_datetime(org, date_str):
    # remove whitespace any trailing period
    date_str = str(date_str).strip().rstrip(".")

    # try first as full ISO string
    if FULL_ISO8601_REGEX.match(date_str) or ISO_YYYY_MM_DD.match(date_str):
        try:
            return iso8601.parse_date(date_str)
        except iso8601.ParseError:  # pragma: no cover
            pass

    current_year = datetime.datetime.now().year

    parsed = None
    if org.date_format == Org.DATE_FORMAT_DAY_FIRST:
        parsed, remainder = _date_from_formats(current_year, DD_MM_YYYY, 1, 2, 3, date_str)
    elif org.date_format == Org.DATE_FORMAT_MONTH_FIRST:
        parsed, remainder = _date_from_formats(current_year, DD_MM_YYYY, 2, 1, 3, date_str)
    elif org.date_format == Org.DATE_FORMAT_YEAR_FIRST:
        parsed, remainder = _date_from_formats(current_year, YYYY_MM_DD, 3, 2, 1, date_str)

    if parsed is None:
        return None

    # can we pull out a time?
    has_time, time_of_day = str_to_time(remainder)
    if not has_time:
        time_of_day = timezone.now().astimezone(org.timezone).time()

    parsed = datetime.datetime.combine(parsed, time_of_day)

    # set our timezone if we have one
    if org.timezone and parsed:
        parsed = parsed.replace(tzinfo=org.timezone)

    # if we've been parsed into something Postgres can't store (offset is > 12 hours) then throw it away
    if parsed and abs(parsed.utcoffset().total_seconds()) > MAX_UTC_OFFSET:  # pragma: no cover
        parsed = None

    return parsed


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

        if hour == 24 and minute == 0 and seconds == 0 and micro == 0:
            hour = 0

        if hour > 24:
            continue

        if minute > 60:
            continue

        if seconds > 60:
            continue

        try:
            return True, datetime.time(hour, minute, seconds, micro)
        except ValueError:
            # if our numbers don't form a valid time keep trying
            pass

    return False, None


def _date_from_formats(current_year, pattern, d, m, y, date_str):
    """
    Parses a human-entered date which should be in the org display format
    """

    for match in pattern.finditer(date_str):
        day = _atoi(match[d])
        if day == 0 or day > 31:
            continue

        month = _atoi(match[m])
        if month == 0 or month > 12:
            continue

        year = _atoi(match[y])

        # convert to four digit year
        if len(match[y]) == 2:
            if year > current_year % 1000:
                year += 1900
            else:
                year += 2000

        remainder = date_str[len(match[0]) :]

        try:
            return datetime.date(year, month, day), remainder
        except ValueError:
            # if our numbers don't form a valid date keep trying
            pass

    return None, ""


def _atoi(s):
    try:
        return int(s)
    except ValueError:  # pragma: no cover
        return 0
