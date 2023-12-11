import calendar
from datetime import date, datetime, time, timedelta, timezone as tzone

from django.utils import timezone


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

    if type(date_obj) == date:
        date_obj = datetime.combine(date_obj, time(0, 0, 0)).replace(tzinfo=tz)

    if isinstance(date_obj, datetime):
        date_obj = timezone.localtime(date_obj, tz)

    return date_obj.strftime(format)


def datetime_to_timestamp(dt):
    """
    Converts a datetime to a UTC microsecond timestamp
    """
    seconds = calendar.timegm(dt.utctimetuple())
    return seconds * 1_000_000 + dt.microsecond


def timestamp_to_datetime(ms):
    """
    Converts a UTC microsecond timestamp to a datetime
    """
    dt = datetime.utcfromtimestamp(ms / 1_000_000)
    return dt.replace(tzinfo=tzone.utc)


def date_range(start: date, stop: date):
    """
    A date-based range generator
    """
    for n in range(int((stop - start).days)):
        yield start + timedelta(n)
