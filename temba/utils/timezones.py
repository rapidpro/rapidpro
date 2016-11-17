from __future__ import unicode_literals

import pytz

from datetime import datetime
from timezone_field import TimeZoneFormField as BaseTimeZoneFormField


PRETTY_TIMEZONE_CHOICES = []

for tz in pytz.common_timezones:
    now = datetime.now(pytz.timezone(tz))
    ofs = now.strftime("%z")
    PRETTY_TIMEZONE_CHOICES.append((int(ofs), tz, "(GMT%s) %s" % (ofs, tz)))

PRETTY_TIMEZONE_CHOICES.sort()

for i in range(len(PRETTY_TIMEZONE_CHOICES)):
    PRETTY_TIMEZONE_CHOICES[i] = PRETTY_TIMEZONE_CHOICES[i][1:]


class TimeZoneFormField(BaseTimeZoneFormField):
    def __init__(self, *args, **kwargs):
        kwargs['choices'] = PRETTY_TIMEZONE_CHOICES

        super(TimeZoneFormField, self).__init__(*args, **kwargs)
