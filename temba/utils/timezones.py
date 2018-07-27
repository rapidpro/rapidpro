# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import pytz
import six

from datetime import datetime
from timezone_field import TimeZoneFormField as BaseTimeZoneFormField


# these are not mapped by pytz.country_timezones
INITIAL_TIMEZONE_COUNTRY = {
    'US/Hawaii': 'US',
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
    'UTC': ''
}

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


def timezone_to_country_code(tz):
    country_timezones = pytz.country_timezones

    timezone_country = INITIAL_TIMEZONE_COUNTRY
    for countrycode in country_timezones:
        timezones = country_timezones[countrycode]
        for zone in timezones:
            timezone_country[zone] = countrycode

    return timezone_country.get(six.text_type(tz), '')
