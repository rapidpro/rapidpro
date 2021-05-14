import calendar
import datetime

import pytz

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

    if type(date_obj) == datetime.date:
        date_obj = tz.localize(datetime.datetime.combine(date_obj, datetime.time(0, 0, 0)))

    if isinstance(date_obj, datetime.datetime):
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
    dt = datetime.datetime.utcfromtimestamp(ms / 1_000_000)
    return dt.replace(tzinfo=pytz.utc)
