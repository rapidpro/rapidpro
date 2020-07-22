import copy
import datetime
import os
from collections import OrderedDict
from decimal import Decimal
from io import StringIO
from types import SimpleNamespace
from unittest import mock
from unittest.mock import MagicMock, PropertyMock, patch

import intercom.errors
import iso8601
import pycountry
import pytz
from django_redis import get_redis_connection
from openpyxl import load_workbook
from smartmin.tests import SmartminTestMixin

from django.conf import settings
from django.contrib.auth.models import User
from django.core import checks
from django.core.management import CommandError, call_command
from django.db import connection, models
from django.forms import ValidationError
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from celery.app.task import Task

import temba.utils.analytics
from temba.contacts.models import Contact, ContactField, ContactGroup, ContactGroupCount, ExportContactsTask
from temba.flows.models import FlowRun
from temba.orgs.models import Org, UserSettings
from temba.tests import ESMockWithScroll, TembaTest, matchers
from temba.utils import json, uuid
from temba.utils.json import TembaJsonAdapter

from . import (
    chunk_list,
    dict_to_struct,
    format_number,
    get_country_code_by_name,
    percentage,
    redact,
    sizeof_fmt,
    str_to_bool,
)
from .cache import get_cacheable_attr, get_cacheable_result, incrby_existing
from .celery import nonoverlapping_task
from .currencies import currency_for_country
from .dates import (
    date_to_day_range_utc,
    datetime_to_epoch,
    datetime_to_ms,
    datetime_to_str,
    ms_to_datetime,
    str_to_date,
    str_to_datetime,
    str_to_time,
)
from .email import is_valid_address, send_simple_email
from .export import TableExporter
from .fields import validate_external_url
from .gsm7 import calculate_num_segments, is_gsm7, replace_non_gsm7_accents
from .http import http_headers
from .locks import LockNotAcquiredException, NonBlockingLock
from .models import IDSliceQuerySet, JSONAsTextField, patch_queryset_count
from .templatetags.temba import short_datetime
from .text import clean_string, decode_base64, generate_token, random_string, slugify_with, truncate, unsnakify
from .timezones import TimeZoneFormField, timezone_to_country_code


class InitTest(TembaTest):
    def test_decode_base64(self):

        self.assertEqual("This test\nhas a newline", decode_base64("This test\nhas a newline"))

        self.assertEqual(
            "Please vote NO on the confirmation of Gorsuch.",
            decode_base64("Please vote NO on the confirmation of Gorsuch."),
        )

        # length not multiple of 4
        self.assertEqual(
            "The aim of the game is to be the first player to score 500 points, achieved (usually over several rounds of play)",
            decode_base64(
                "The aim of the game is to be the first player to score 500 points, achieved (usually over several rounds of play)"
            ),
        )

        # end not match base64 characteres
        self.assertEqual(
            "The aim of the game is to be the first player to score 500 points, achieved (usually over several rounds of play) by a player discarding all of their cards!!???",
            decode_base64(
                "The aim of the game is to be the first player to score 500 points, achieved (usually over several rounds of play) by a player discarding all of their cards!!???"
            ),
        )

        self.assertEqual(
            "Bannon Explains The World ...\n\u201cThe Camp of the Saints",
            decode_base64("QmFubm9uIEV4cGxhaW5zIFRoZSBXb3JsZCAuLi4K4oCcVGhlIENhbXAgb2YgdGhlIFNhaW50c+KA\r"),
        )

        self.assertEqual(
            "the sweat, the tears and the sacrifice of working America",
            decode_base64("dGhlIHN3ZWF0LCB0aGUgdGVhcnMgYW5kIHRoZSBzYWNyaWZpY2Ugb2Ygd29ya2luZyBBbWVyaWNh\r"),
        )

        self.assertIn(
            "I find them to be friendly",
            decode_base64(
                "Tm93IGlzDQp0aGUgdGltZQ0KZm9yIGFsbCBnb29kDQpwZW9wbGUgdG8NCnJlc2lzdC4NCg0KSG93IGFib3V0IGhhaWt1cz8NCkkgZmluZCB0aGVtIHRvIGJlIGZyaWVuZGx5Lg0KcmVmcmlnZXJhdG9yDQoNCjAxMjM0NTY3ODkNCiFAIyQlXiYqKCkgW117fS09Xys7JzoiLC4vPD4/fFx+YA0KQUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVphYmNkZWZnaGlqa2xtbm9wcXJzdHV2d3h5eg=="
            ),
        )

        # not 50% ascii letters
        self.assertEqual(
            "8J+YgvCfmITwn5iA8J+YhvCfkY3wn5ii8J+Yn/CfmK3wn5it4pi677iP8J+YjPCfmInwn5iK8J+YivCfmIrwn5iK8J+YivCfmIrwn5iK8J+ko/CfpKPwn6Sj8J+ko/CfpKNvaw==",
            decode_base64(
                "8J+YgvCfmITwn5iA8J+YhvCfkY3wn5ii8J+Yn/CfmK3wn5it4pi677iP8J+YjPCfmInwn5iK8J+YivCfmIrwn5iK8J+YivCfmIrwn5iK8J+ko/CfpKPwn6Sj8J+ko/CfpKNvaw=="
            ),
        )

        with patch("temba.utils.text.Counter") as mock_decode:
            mock_decode.side_effect = Exception("blah")

            self.assertEqual(
                "Tm93IGlzDQp0aGUgdGltZQ0KZm9yIGFsbCBnb29kDQpwZW9wbGUgdG8NCnJlc2lzdC4NCg0KSG93IGFib3V0IGhhaWt1cz8NCkkgZmluZCB0aGVtIHRvIGJlIGZyaWVuZGx5Lg0KcmVmcmlnZXJhdG9yDQoNCjAxMjM0NTY3ODkNCiFAIyQlXiYqKCkgW117fS09Xys7JzoiLC4vPD4/fFx+YA0KQUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVphYmNkZWZnaGlqa2xtbm9wcXJzdHV2d3h5eg==",
                decode_base64(
                    "Tm93IGlzDQp0aGUgdGltZQ0KZm9yIGFsbCBnb29kDQpwZW9wbGUgdG8NCnJlc2lzdC4NCg0KSG93IGFib3V0IGhhaWt1cz8NCkkgZmluZCB0aGVtIHRvIGJlIGZyaWVuZGx5Lg0KcmVmcmlnZXJhdG9yDQoNCjAxMjM0NTY3ODkNCiFAIyQlXiYqKCkgW117fS09Xys7JzoiLC4vPD4/fFx+YA0KQUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVphYmNkZWZnaGlqa2xtbm9wcXJzdHV2d3h5eg=="
                ),
            )

    def test_sizeof_fmt(self):
        self.assertEqual("512.0 b", sizeof_fmt(512))
        self.assertEqual("1.0 Kb", sizeof_fmt(1024))
        self.assertEqual("1.0 Mb", sizeof_fmt(1024 ** 2))
        self.assertEqual("1.0 Gb", sizeof_fmt(1024 ** 3))
        self.assertEqual("1.0 Tb", sizeof_fmt(1024 ** 4))
        self.assertEqual("1.0 Pb", sizeof_fmt(1024 ** 5))
        self.assertEqual("1.0 Eb", sizeof_fmt(1024 ** 6))
        self.assertEqual("1.0 Zb", sizeof_fmt(1024 ** 7))

    def test_str_to_bool(self):
        self.assertFalse(str_to_bool(None))
        self.assertFalse(str_to_bool(""))
        self.assertFalse(str_to_bool("x"))
        self.assertTrue(str_to_bool("Y"))
        self.assertTrue(str_to_bool("Yes"))
        self.assertTrue(str_to_bool("TRUE"))
        self.assertTrue(str_to_bool("1"))

    def test_format_decimal(self):
        self.assertEqual("", format_number(None))
        self.assertEqual("0", format_number(Decimal("0.0")))
        self.assertEqual("10", format_number(Decimal("10")))
        self.assertEqual("100", format_number(Decimal("100.0")))
        self.assertEqual("123", format_number(Decimal("123")))
        self.assertEqual("123", format_number(Decimal("123.0")))
        self.assertEqual("123.34", format_number(Decimal("123.34")))
        self.assertEqual("123.34", format_number(Decimal("123.3400000")))
        self.assertEqual("-123", format_number(Decimal("-123.0")))
        self.assertEqual("-12300", format_number(Decimal("-123E+2")))
        self.assertEqual("-12350", format_number(Decimal("-123.5E+2")))
        self.assertEqual("-1.235", format_number(Decimal("-123.5E-2")))
        self.assertEqual(
            "-1000000000000001467812345696542157800075344236445874615",
            format_number(Decimal("-1000000000000001467812345696542157800075344236445874615")),
        )
        self.assertEqual("", format_number(Decimal("NaN")))

    def test_slugify_with(self):
        self.assertEqual("foo_bar", slugify_with("foo bar"))
        self.assertEqual("foo$bar", slugify_with("foo bar", "$"))

    def test_truncate(self):
        self.assertEqual("abc", truncate("abc", 5))
        self.assertEqual("abcde", truncate("abcde", 5))
        self.assertEqual("ab...", truncate("abcdef", 5))

    def test_unsnakify(self):
        self.assertEqual("", unsnakify(""))
        self.assertEqual("Org Name", unsnakify("org_name"))

    def test_random_string(self):
        rs = random_string(1000)
        self.assertEqual(1000, len(rs))
        self.assertFalse("1" in rs or "I" in rs or "0" in rs or "O" in rs)

    def test_percentage(self):
        self.assertEqual(0, percentage(0, 100))
        self.assertEqual(0, percentage(0, 0))
        self.assertEqual(0, percentage(100, 0))
        self.assertEqual(75, percentage(75, 100))
        self.assertEqual(76, percentage(759, 1000))

    def test_get_country_code_by_name(self):
        self.assertEqual("RW", get_country_code_by_name("Rwanda"))
        self.assertEqual("US", get_country_code_by_name("United States of America"))
        self.assertEqual("US", get_country_code_by_name("United States"))
        self.assertEqual("GB", get_country_code_by_name("United Kingdom"))
        self.assertEqual("CI", get_country_code_by_name("Ivory Coast"))
        self.assertEqual("CD", get_country_code_by_name("Democratic Republic of the Congo"))

    def test_remove_control_charaters(self):
        self.assertIsNone(clean_string(None))
        self.assertEqual(clean_string("ngert\x07in."), "ngertin.")
        self.assertEqual(clean_string("Norbért"), "Norbért")

    def test_replace_non_characters(self):
        self.assertEqual(clean_string("Bangsa\ufddfBangsa"), "Bangsa\ufffdBangsa")

    def test_http_headers(self):
        headers = http_headers(extra={"Foo": "Bar"})
        headers["Token"] = "123456"

        self.assertEqual(headers, {"User-agent": "RapidPro", "Foo": "Bar", "Token": "123456"})
        self.assertEqual(http_headers(), {"User-agent": "RapidPro"})  # check changes don't leak

    def test_generate_token(self):
        self.assertEqual(len(generate_token()), 8)


class DatesTest(TembaTest):
    def test_datetime_to_ms(self):
        d1 = datetime.datetime(2014, 1, 2, 3, 4, 5, tzinfo=pytz.utc)
        self.assertEqual(datetime_to_ms(d1), 1_388_631_845_000)  # from http://unixtimestamp.50x.eu
        self.assertEqual(ms_to_datetime(1_388_631_845_000), d1)

        tz = pytz.timezone("Africa/Kigali")
        d2 = tz.localize(datetime.datetime(2014, 1, 2, 3, 4, 5))
        self.assertEqual(datetime_to_ms(d2), 1_388_624_645_000)
        self.assertEqual(ms_to_datetime(1_388_624_645_000), d2.astimezone(pytz.utc))

    def test_datetime_to_str(self):
        tz = pytz.timezone("Africa/Kigali")
        d2 = tz.localize(datetime.datetime(2014, 1, 2, 3, 4, 5, 6))

        self.assertIsNone(datetime_to_str(None, "%Y-%m-%d %H:%M", tz=tz))
        self.assertEqual(datetime_to_str(d2, "%Y-%m-%d %H:%M", tz=tz), "2014-01-02 03:04")
        self.assertEqual(datetime_to_str(d2, "%Y/%m/%d %H:%M", tz=pytz.UTC), "2014/01/02 01:04")

    def test_datetime_to_epoch(self):
        dt = iso8601.parse_date("2014-01-02T01:04:05.000Z")
        self.assertEqual(1_388_624_645, datetime_to_epoch(dt))

    def test_str_to_date(self):
        self.assertIsNone(str_to_date(""))
        self.assertIsNone(str_to_date(None))
        self.assertIsNone(str_to_date("not a date"))
        self.assertIsNone(str_to_date("2017 10 23"))

        # full iso8601 timestamp
        self.assertEqual(str_to_date("2013-02-01T04:38:09.100000+02:00"), datetime.date(2013, 2, 1))
        self.assertEqual(str_to_date("2013-02-01 04:38:09.100000+02:00"), datetime.date(2013, 2, 1))

        # iso date
        self.assertEqual(str_to_date("2012-02-21", dayfirst=True), datetime.date(2012, 2, 21))
        self.assertEqual(str_to_date("2012-21-02", dayfirst=True), None)

        # similar to iso date
        self.assertEqual(str_to_date("2012-2-21", dayfirst=True), datetime.date(2012, 2, 21))
        self.assertEqual(str_to_date("2012.2.21", dayfirst=True), datetime.date(2012, 2, 21))
        self.assertEqual(str_to_date("2012\\2\\21", dayfirst=True), datetime.date(2012, 2, 21))
        self.assertEqual(str_to_date("2012/2/21", dayfirst=True), datetime.date(2012, 2, 21))
        self.assertEqual(str_to_date("2012_2_21", dayfirst=True), datetime.date(2012, 2, 21))

        # mixed delimiters
        self.assertEqual(str_to_date("2012-2/21", dayfirst=True), datetime.date(2012, 2, 21))

        # day and month are switched, depends on org conf
        self.assertEqual(str_to_date("12/11/16", dayfirst=True), datetime.date(2016, 11, 12))
        self.assertEqual(str_to_date("12/11/16", dayfirst=False), datetime.date(2016, 12, 11))

        # there is no 21st month
        self.assertEqual(str_to_date("11/21/17 at 12:00PM", dayfirst=True), None)
        self.assertEqual(str_to_date("11/21/17 at 12:00PM", dayfirst=False), datetime.date(2017, 11, 21))

    def test_str_to_datetime(self):
        tz = pytz.timezone("Asia/Kabul")
        with patch.object(timezone, "now", return_value=tz.localize(datetime.datetime(2014, 1, 2, 3, 4, 5, 6))):
            self.assertIsNone(str_to_datetime(None, tz))  # none
            self.assertIsNone(str_to_datetime("", tz))  # empty string
            self.assertIsNone(str_to_datetime("xxx", tz))  # unparseable string
            self.assertIsNone(str_to_datetime("xxx", tz, fill_time=False))  # unparseable string
            self.assertIsNone(str_to_datetime("31-02-2017", tz))  # day out of range
            self.assertIsNone(str_to_datetime("03-13-2017", tz))  # month out of range
            self.assertIsNone(str_to_datetime("03-12-99999", tz))  # year out of range

            self.assertEqual(
                tz.localize(datetime.datetime(2013, 2, 1, 3, 4, 5, 6)),
                str_to_datetime(" 2013-02-01 ", tz, dayfirst=True),
            )  # iso

            self.assertEqual(
                tz.localize(datetime.datetime(2013, 2, 1, 3, 4, 5, 6)),
                str_to_datetime("01-02-2013", tz, dayfirst=True),
            )  # day first

            self.assertEqual(
                tz.localize(datetime.datetime(2013, 1, 2, 3, 4, 5, 6)),
                str_to_datetime("01-02-2013", tz, dayfirst=False),
            )  # month first

            # two digit years
            self.assertEqual(
                tz.localize(datetime.datetime(2013, 1, 2, 3, 4, 5, 6)), str_to_datetime("01-02-13", tz, dayfirst=False)
            )
            self.assertEqual(
                tz.localize(datetime.datetime(1999, 1, 2, 3, 4, 5, 6)), str_to_datetime("01-02-99", tz, dayfirst=False)
            )

            # no two digit iso date
            self.assertEqual(None, str_to_datetime("99-02-01", tz, dayfirst=False))

            # single digit months in iso-like date
            self.assertEqual(
                tz.localize(datetime.datetime(1999, 1, 2, 3, 4, 5, 6)), str_to_datetime("1999-2-1", tz, dayfirst=False)
            )

            self.assertEqual(
                tz.localize(datetime.datetime(2013, 2, 1, 7, 8, 0, 0)),
                str_to_datetime("01-02-2013 07:08", tz, dayfirst=True),
            )  # hour and minute provided

            # AM / PM edge cases
            self.assertEqual(
                tz.localize(datetime.datetime(2017, 11, 21, 12, 0, 0, 0)),
                str_to_datetime("11/21/17 at 12:00PM", tz, dayfirst=False),
            )
            self.assertEqual(
                tz.localize(datetime.datetime(2017, 11, 21, 0, 0, 0, 0)),
                str_to_datetime("11/21/17 at 12:00 am", tz, dayfirst=False),
            )
            self.assertEqual(
                tz.localize(datetime.datetime(2017, 11, 21, 23, 59, 0, 0)),
                str_to_datetime("11/21/17 at 11:59 pm", tz, dayfirst=False),
            )
            self.assertEqual(
                tz.localize(datetime.datetime(2017, 11, 21, 0, 30, 0, 0)),
                str_to_datetime("11/21/17 at 00:30 am", tz, dayfirst=False),
            )

            self.assertEqual(
                tz.localize(datetime.datetime(2017, 11, 21, 0, 0, 0, 0)),  # illogical time ignored
                str_to_datetime("11/21/17 at 34:62", tz, dayfirst=False, fill_time=False),
            )

            self.assertEqual(
                tz.localize(datetime.datetime(2013, 2, 1, 7, 8, 9, 100_000)),
                str_to_datetime("01-02-2013 07:08:09.100000", tz, dayfirst=True),
            )  # complete time provided

            self.assertEqual(
                datetime.datetime(2013, 2, 1, 7, 8, 9, 100_000, tzinfo=pytz.UTC),
                str_to_datetime("2013-02-01T07:08:09.100000Z", tz, dayfirst=True),
            )  # Z marker
            self.assertEqual(
                tz.localize(datetime.datetime(2013, 2, 1, 7, 8, 9, 100_000)),
                str_to_datetime("2013-02-01T07:08:09.100000+04:30", tz, dayfirst=True),
            )  # ISO in local tz
            self.assertEqual(
                tz.localize(datetime.datetime(2013, 2, 1, 7, 8, 9, 100_000)),
                str_to_datetime("2013-02-01T04:38:09.100000+02:00", tz, dayfirst=True),
            )  # ISO in other tz
            self.assertEqual(
                tz.localize(datetime.datetime(2013, 2, 1, 7, 8, 9, 100_000)),
                str_to_datetime("2013-02-01T00:38:09.100000-02:00", tz, dayfirst=True),
            )  # ISO in other tz
            self.assertEqual(
                datetime.datetime(2013, 2, 1, 7, 8, 9, 0, tzinfo=pytz.UTC),
                str_to_datetime("2013-02-01T07:08:09Z", tz, dayfirst=True),
            )  # with no second fraction
            self.assertEqual(
                datetime.datetime(2013, 2, 1, 7, 8, 9, 198_000, tzinfo=pytz.UTC),
                str_to_datetime("2013-02-01T07:08:09.198Z", tz, dayfirst=True),
            )  # with milliseconds
            self.assertEqual(
                datetime.datetime(2013, 2, 1, 7, 8, 9, 198_537, tzinfo=pytz.UTC),
                str_to_datetime("2013-02-01T07:08:09.198537686Z", tz, dayfirst=True),
            )  # with nanoseconds
            self.assertEqual(
                datetime.datetime(2013, 2, 1, 7, 8, 9, 198_500, tzinfo=pytz.UTC),
                str_to_datetime("2013-02-01T07:08:09.1985Z", tz, dayfirst=True),
            )  # with 4 second fraction digits
            self.assertEqual(
                tz.localize(datetime.datetime(2013, 2, 1, 7, 8, 9, 100_000)),
                str_to_datetime("2013-02-01T07:08:09.100000+04:30.", tz, dayfirst=True),
            )  # trailing period
            self.assertEqual(
                tz.localize(datetime.datetime(2013, 2, 1, 0, 0, 0, 0)),
                str_to_datetime("01-02-2013", tz, dayfirst=True, fill_time=False),
            )  # no time filling

        # localizing while in DST to something outside DST
        tz = pytz.timezone("US/Eastern")
        with patch.object(timezone, "now", return_value=tz.localize(datetime.datetime(2029, 11, 1, 12, 30, 0, 0))):
            parsed = str_to_datetime("06-11-2029", tz, dayfirst=True)
            self.assertEqual(tz.localize(datetime.datetime(2029, 11, 6, 12, 30, 0, 0)), parsed)

            # assert there is no DST offset
            self.assertFalse(parsed.tzinfo.dst(parsed))

            self.assertEqual(
                tz.localize(datetime.datetime(2029, 11, 6, 13, 45, 0, 0)),
                str_to_datetime("06-11-2029 13:45", tz, dayfirst=True),
            )

        # deal with datetimes that have timezone info
        self.assertEqual(
            pytz.utc.localize(datetime.datetime(2016, 11, 21, 20, 36, 51, 215_681)).astimezone(tz),
            str_to_datetime("2016-11-21T20:36:51.215681Z", tz),
        )

        self.assertEqual(
            pytz.utc.localize(datetime.datetime(2017, 8, 9, 18, 38, 24, 469_581)).astimezone(tz),
            str_to_datetime("2017-08-09 18:38:24.469581+00:00", tz),
        )

    def test_str_to_time(self):
        self.assertEqual(str_to_time(""), None)
        self.assertEqual(str_to_time("x"), None)
        self.assertEqual(str_to_time("32:01"), None)
        self.assertEqual(str_to_time("12:61"), None)
        self.assertEqual(str_to_time("12:30:61"), None)

        tz = pytz.timezone("Asia/Kabul")
        with patch.object(timezone, "now", return_value=tz.localize(datetime.datetime(2014, 1, 2, 3, 4, 5, 6))):
            self.assertEqual(str_to_time("03:04"), datetime.time(3, 4))  # hour zero padded
            self.assertEqual(str_to_time("3:04"), datetime.time(3, 4))  # hour not zero padded
            self.assertEqual(str_to_time("01-02-2013 03:04"), datetime.time(3, 4))  # with date
            self.assertEqual(str_to_time("3:04 PM"), datetime.time(15, 4))  # as PM
            self.assertEqual(str_to_time("03:04:30"), datetime.time(3, 4, 30))  # with seconds
            self.assertEqual(str_to_time("03:04:30.123"), datetime.time(3, 4, 30, 123_000))  # with milliseconds
            self.assertEqual(str_to_time("03:04:30.123000"), datetime.time(3, 4, 30, 123_000))  # with microseconds

    def test_date_to_day_range_utc(self):
        result = date_to_day_range_utc(datetime.date(2017, 2, 20), self.org)
        self.assertEqual(result[0].isoformat(), "2017-02-19T22:00:00+00:00")
        self.assertEqual(result[1].isoformat(), "2017-02-20T22:00:00+00:00")


class TimezonesTest(TembaTest):
    def test_field(self):
        field = TimeZoneFormField(help_text="Test field")

        self.assertEqual(field.choices[0], ("Pacific/Midway", "(GMT-1100) Pacific/Midway"))
        self.assertEqual(field.coerce("Africa/Kigali"), pytz.timezone("Africa/Kigali"))

    def test_timezone_country_code(self):
        self.assertEqual("RW", timezone_to_country_code(pytz.timezone("Africa/Kigali")))
        self.assertEqual("US", timezone_to_country_code(pytz.timezone("America/Chicago")))
        self.assertEqual("US", timezone_to_country_code(pytz.timezone("US/Pacific")))

        # GMT and UTC give empty
        self.assertEqual("", timezone_to_country_code(pytz.timezone("GMT")))
        self.assertEqual("", timezone_to_country_code(pytz.timezone("UTC")))


class TemplateTagTest(TembaTest):
    def test_icon(self):
        from temba.campaigns.models import Campaign
        from temba.triggers.models import Trigger
        from temba.flows.models import Flow
        from temba.utils.templatetags.temba import icon

        campaign = Campaign.create(self.org, self.admin, "Test Campaign", self.create_group("Test group", []))
        flow = Flow.create(self.org, self.admin, "Test Flow")
        trigger = Trigger.objects.create(
            org=self.org, keyword="trigger", flow=flow, created_by=self.admin, modified_by=self.admin
        )

        self.assertEqual("icon-instant", icon(campaign))
        self.assertEqual("icon-feed", icon(trigger))
        self.assertEqual("icon-tree", icon(flow))
        self.assertEqual("", icon(None))

    def test_format_datetime(self):
        import pytz
        from temba.utils.templatetags.temba import format_datetime

        with patch.object(timezone, "now", return_value=datetime.datetime(2015, 9, 15, 0, 0, 0, 0, pytz.UTC)):
            self.org.date_format = "D"
            self.org.save()

            # date without timezone and no user org in context
            test_date = datetime.datetime(2012, 7, 20, 17, 5, 0, 0)
            self.assertEqual("20-07-2012 17:05", format_datetime(dict(), test_date))

            test_date = datetime.datetime(2012, 7, 20, 17, 5, 0, 0).replace(tzinfo=pytz.utc)
            self.assertEqual("20-07-2012 17:05", format_datetime(dict(), test_date))

            context = dict(user_org=self.org)

            # date without timezone
            test_date = datetime.datetime(2012, 7, 20, 17, 5, 0, 0)
            self.assertEqual("20-07-2012 19:05", format_datetime(context, test_date))

            test_date = datetime.datetime(2012, 7, 20, 17, 5, 0, 0).replace(tzinfo=pytz.utc)
            self.assertEqual("20-07-2012 19:05", format_datetime(context, test_date))

            # the org has month first configured
            self.org.date_format = "M"
            self.org.save()

            # date without timezone
            test_date = datetime.datetime(2012, 7, 20, 17, 5, 0, 0)
            self.assertEqual("07-20-2012 19:05", format_datetime(context, test_date))

            test_date = datetime.datetime(2012, 7, 20, 17, 5, 0, 0).replace(tzinfo=pytz.utc)
            self.assertEqual("07-20-2012 19:05", format_datetime(context, test_date))

    def test_short_datetime(self):
        with patch.object(timezone, "now", return_value=datetime.datetime(2015, 9, 15, 0, 0, 0, 0, pytz.UTC)):
            self.org.date_format = "D"
            self.org.save()

            context = dict(user_org=self.org)

            # date without timezone
            test_date = datetime.datetime.now()
            modified_now = test_date.replace(hour=17, minute=5)
            self.assertEqual("19:05", short_datetime(context, modified_now))

            # given the time as now, should display as 24 hour time
            now = timezone.now()
            self.assertEqual("08:10", short_datetime(context, now.replace(hour=6, minute=10)))
            self.assertEqual("19:05", short_datetime(context, now.replace(hour=17, minute=5)))

            # given the time beyond 12 hours ago within the same month, should display "MonthName DayOfMonth" eg. "Jan 2"
            test_date = now.replace(day=2)
            self.assertEqual("2 " + test_date.strftime("%b"), short_datetime(context, test_date))

            # last month should still be pretty
            test_date = test_date.replace(month=2)
            self.assertEqual("2 " + test_date.strftime("%b"), short_datetime(context, test_date))

            # but a different year is different
            jan_2 = datetime.datetime(2012, 7, 20, 17, 5, 0, 0).replace(tzinfo=pytz.utc)
            self.assertEqual("20/7/12", short_datetime(context, jan_2))

            # the org has month first configured
            self.org.date_format = "M"
            self.org.save()

            # given the time as now, should display "Hour:Minutes AM|PM" eg. "5:05 pm"
            now = timezone.now()
            modified_now = now.replace(hour=17, minute=5)
            self.assertEqual("7:05 pm", short_datetime(context, modified_now))

            # given the time beyond 12 hours ago within the same month, should display "MonthName DayOfMonth" eg. "Jan 2"
            test_date = now.replace(day=2)
            self.assertEqual(test_date.strftime("%b") + " 2", short_datetime(context, test_date))

            # last month should still be pretty
            test_date = test_date.replace(month=2)
            self.assertEqual(test_date.strftime("%b") + " 2", short_datetime(context, test_date))

            # but a different year is different
            jan_2 = datetime.datetime(2012, 7, 20, 17, 5, 0, 0).replace(tzinfo=pytz.utc)
            self.assertEqual("7/20/12", short_datetime(context, jan_2))


class TemplateTagTestSimple(TestCase):
    def test_format_seconds(self):
        from temba.utils.templatetags.temba import format_seconds

        self.assertIsNone(format_seconds(None))

        # less than a minute
        self.assertEqual("30 sec", format_seconds(30))

        # round down
        self.assertEqual("1 min", format_seconds(89))

        # round up
        self.assertEqual("2 min", format_seconds(100))

    def test_delta(self):
        from temba.utils.templatetags.temba import delta_filter

        # empty
        self.assertEqual("", delta_filter(datetime.timedelta(seconds=0)))

        # in the future
        self.assertEqual("0 seconds", delta_filter(datetime.timedelta(seconds=-10)))

        # some valid times
        self.assertEqual("2 minutes, 40 seconds", delta_filter(datetime.timedelta(seconds=160)))
        self.assertEqual("5 minutes", delta_filter(datetime.timedelta(seconds=300)))
        self.assertEqual("10 minutes, 1 second", delta_filter(datetime.timedelta(seconds=601)))

        # non-delta arg
        self.assertEqual("", delta_filter("Invalid"))

    def test_oxford(self):
        from temba.utils.templatetags.temba import oxford

        def forloop(idx, total):
            """
            Creates a dict like that available inside a template tag
            """
            return dict(counter0=idx, counter=idx + 1, revcounter=total - idx, last=total == idx + 1)

        # list of two
        self.assertEqual(" and ", oxford(forloop(0, 2)))
        self.assertEqual(".", oxford(forloop(1, 2), "."))

        # list of three
        self.assertEqual(", ", oxford(forloop(0, 3)))
        self.assertEqual(", and ", oxford(forloop(1, 3)))
        self.assertEqual(".", oxford(forloop(2, 3), "."))

        # list of four
        self.assertEqual(", ", oxford(forloop(0, 4)))
        self.assertEqual(", ", oxford(forloop(1, 4)))
        self.assertEqual(", and ", oxford(forloop(2, 4)))
        self.assertEqual(".", oxford(forloop(3, 4), "."))

    def test_to_json(self):
        from temba.utils.templatetags.temba import to_json

        # only works with plain str objects
        self.assertRaises(ValueError, to_json, dict())

        self.assertEqual(to_json(json.dumps({})), 'JSON.parse("{}")')
        self.assertEqual(to_json(json.dumps({"a": 1})), 'JSON.parse("{\\u0022a\\u0022: 1}")')
        self.assertEqual(
            to_json(json.dumps({"special": '"'})),
            'JSON.parse("{\\u0022special\\u0022: \\u0022\\u005C\\u0022\\u0022}")',
        )

        # ecapes special <script>
        self.assertEqual(
            to_json(json.dumps({"special": '<script>alert("XSS");</script>'})),
            'JSON.parse("{\\u0022special\\u0022: \\u0022\\u003Cscript\\u003Ealert(\\u005C\\u0022XSS\\u005C\\u0022)\\u003B\\u003C/script\\u003E\\u0022}")',
        )


class CacheTest(TembaTest):
    def test_get_cacheable_result(self):
        self.create_contact("Bob", number="1234")

        def calculate():
            return Contact.objects.all().count(), 60

        with self.assertNumQueries(1):
            self.assertEqual(get_cacheable_result("test_contact_count", calculate), 1)  # from db
        with self.assertNumQueries(0):
            self.assertEqual(get_cacheable_result("test_contact_count", calculate), 1)  # from cache

        self.create_contact("Jim", number="2345")

        with self.assertNumQueries(0):
            self.assertEqual(get_cacheable_result("test_contact_count", calculate), 1)  # not updated

        get_redis_connection().delete("test_contact_count")  # delete from cache for force re-fetch from db

        with self.assertNumQueries(1):
            self.assertEqual(get_cacheable_result("test_contact_count", calculate), 2)  # from db
        with self.assertNumQueries(0):
            self.assertEqual(get_cacheable_result("test_contact_count", calculate), 2)  # from cache

    def test_get_cacheable_attr(self):
        def calculate():
            return "CALCULATED"

        self.assertEqual(get_cacheable_attr(self, "_test_value", calculate), "CALCULATED")
        self._test_value = "CACHED"
        self.assertEqual(get_cacheable_attr(self, "_test_value", calculate), "CACHED")

    def test_incrby_existing(self):
        r = get_redis_connection()
        r.setex("foo", 100, 10)
        r.set("bar", 20)

        incrby_existing("foo", 3, r)  # positive delta
        self.assertEqual(r.get("foo"), b"13")
        self.assertTrue(r.ttl("foo") > 0)

        incrby_existing("foo", -1, r)  # negative delta
        self.assertEqual(r.get("foo"), b"12")
        self.assertTrue(r.ttl("foo") > 0)

        r.setex("foo", 100, 0)
        incrby_existing("foo", 5, r)  # zero val key
        self.assertEqual(r.get("foo"), b"5")
        self.assertTrue(r.ttl("foo") > 0)

        incrby_existing("bar", 5, r)  # persistent key
        self.assertEqual(r.get("bar"), b"25")
        self.assertTrue(r.ttl("bar") < 0)

        incrby_existing("xxx", -2, r)  # non-existent key
        self.assertIsNone(r.get("xxx"))


class EmailTest(TembaTest):
    @override_settings(SEND_EMAILS=True)
    def test_send_simple_email(self):
        send_simple_email(["recipient@bar.com"], "Test Subject", "Test Body")
        self.assertOutbox(0, settings.DEFAULT_FROM_EMAIL, "Test Subject", "Test Body", ["recipient@bar.com"])

        send_simple_email(["recipient@bar.com"], "Test Subject", "Test Body", from_email="no-reply@foo.com")
        self.assertOutbox(1, "no-reply@foo.com", "Test Subject", "Test Body", ["recipient@bar.com"])

    def test_is_valid_address(self):

        self.VALID_EMAILS = [
            # Cases from https://en.wikipedia.org/wiki/Email_address
            "prettyandsimple@example.com",
            "very.common@example.com",
            "disposable.style.email.with+symbol@example.com",
            "other.email-with-dash@example.com",
            "x@example.com",
            '"much.more unusual"@example.com',
            '"very.unusual.@.unusual.com"@example.com'
            '"very.(),:;<>[]".VERY."very@\\ "very".unusual"@strange.example.com',
            "example-indeed@strange-example.com",
            "#!$%&'*+-/=?^_`{}|~@example.org",
            '"()<>[]:,;@\\"!#$%&\'-/=?^_`{}| ~.a"@example.org' '" "@example.org',
            "example@localhost",
            "example@s.solutions",
            # Cases from Django tests
            "email@here.com",
            "weirder-email@here.and.there.com",
            "email@[127.0.0.1]",
            "email@[2001:dB8::1]",
            "email@[2001:dB8:0:0:0:0:0:1]",
            "email@[::fffF:127.0.0.1]",
            "example@valid-----hyphens.com",
            "example@valid-with-hyphens.com",
            "test@domain.with.idn.tld.उदाहरण.परीक्षा",
            "email@localhost",
            '"test@test"@example.com',
            "example@atm.%s" % ("a" * 63),
            "example@%s.atm" % ("a" * 63),
            "example@%s.%s.atm" % ("a" * 63, "b" * 10),
            '"\\\011"@here.com',
            "a@%s.us" % ("a" * 63),
        ]

        self.INVALID_EMAILS = [
            # Cases from https://en.wikipedia.org/wiki/Email_address
            None,
            "",
            "abc",
            "a@b",
            " @ .c",
            "a @b.c",
            "{@flow.email}",
            "Abc.example.com",
            "A@b@c@example.com",
            r'a"b(c)d,e:f;g<h>i[j\k]l@example.com'
            'just"not"right@example.com'
            'this is"not\allowed@example.com'
            r'this\ still"not\\allowed@example.com'
            "1234567890123456789012345678901234567890123456789012345678901234+x@example.com"
            "john..doe@example.com"
            "john.doe@example..com"
            # Cases from Django tests
            "example@atm.%s" % ("a" * 64),
            "example@%s.atm.%s" % ("b" * 64, "a" * 63),
            None,
            "",
            "abc",
            "abc@",
            "abc@bar",
            "a @x.cz",
            "abc@.com",
            "something@@somewhere.com",
            "email@127.0.0.1",
            "email@[127.0.0.256]",
            "email@[2001:db8::12345]",
            "email@[2001:db8:0:0:0:0:1]",
            "email@[::ffff:127.0.0.256]",
            "example@invalid-.com",
            "example@-invalid.com",
            "example@invalid.com-",
            "example@inv-.alid-.com",
            "example@inv-.-alid.com",
            'test@example.com\n\n<script src="x.js">',
            # Quoted-string format (CR not allowed)
            '"\\\012"@here.com',
            "trailingdot@shouldfail.com.",
            # Max length of domain name labels is 63 characters per RFC 1034.
            "a@%s.us" % ("a" * 64),
            # Trailing newlines in username or domain not allowed
            "a@b.com\n",
            "a\n@b.com",
            '"test@test"\n@example.com',
            "a@[127.0.0.1]\n",
        ]

        for email in self.VALID_EMAILS:
            self.assertTrue(is_valid_address(email), "FAILED: %s should be a valid email" % email)

        for email in self.INVALID_EMAILS:
            self.assertFalse(is_valid_address(email), "FAILED: %s should be an invalid email" % email)


class JsonTest(TembaTest):
    def test_encode_decode(self):
        # create a time that has a set millisecond
        now = timezone.now().replace(microsecond=1000)

        # our dictionary to encode
        source = dict(name="Date Test", age=Decimal("10"), now=now)

        # encode it
        encoded = json.dumps(source)

        self.assertEqual(
            json.loads(encoded), {"name": "Date Test", "age": Decimal("10"), "now": json.encode_datetime(now)}
        )

        # test the same using our object mocking
        mock = dict_to_struct("Mock", json.loads(encoded), ["now"])
        self.assertEqual(mock.now, source["now"])

        # try it with a microsecond of 0 instead
        source["now"] = timezone.now().replace(microsecond=0)

        # encode it
        encoded = json.dumps(source)

        # test the same using our object mocking
        mock = dict_to_struct("Mock", json.loads(encoded), ["now"])
        self.assertEqual(mock.now, source["now"])

        # test that we throw with unknown types
        with self.assertRaises(TypeError):
            json.dumps(dict(foo=Exception("invalid")))


class CeleryTest(TembaTest):
    @patch("redis.client.StrictRedis.lock")
    @patch("redis.client.StrictRedis.get")
    def test_nonoverlapping_task(self, mock_redis_get, mock_redis_lock):
        mock_redis_get.return_value = None
        task_calls = []

        @nonoverlapping_task()
        def test_task1(foo, bar):
            task_calls.append("1-%d-%d" % (foo, bar))

        @nonoverlapping_task(name="task2", time_limit=100)
        def test_task2(foo, bar):
            task_calls.append("2-%d-%d" % (foo, bar))

        @nonoverlapping_task(name="task3", time_limit=100, lock_key="test_key", lock_timeout=55)
        def test_task3(foo, bar):
            task_calls.append("3-%d-%d" % (foo, bar))

        self.assertIsInstance(test_task1, Task)
        self.assertIsInstance(test_task2, Task)
        self.assertEqual(test_task2.name, "task2")
        self.assertEqual(test_task2.time_limit, 100)
        self.assertIsInstance(test_task3, Task)
        self.assertEqual(test_task3.name, "task3")
        self.assertEqual(test_task3.time_limit, 100)

        test_task1(11, 12)
        test_task2(21, bar=22)
        test_task3(foo=31, bar=32)

        mock_redis_get.assert_any_call("celery-task-lock:test_task1")
        mock_redis_get.assert_any_call("celery-task-lock:task2")
        mock_redis_get.assert_any_call("test_key")
        mock_redis_lock.assert_any_call("celery-task-lock:test_task1", timeout=900)
        mock_redis_lock.assert_any_call("celery-task-lock:task2", timeout=100)
        mock_redis_lock.assert_any_call("test_key", timeout=55)

        self.assertEqual(task_calls, ["1-11-12", "2-21-22", "3-31-32"])

        # simulate task being already running
        mock_redis_get.reset_mock()
        mock_redis_get.return_value = "xyz"
        mock_redis_lock.reset_mock()

        # try to run again
        test_task1(13, 14)

        # check that task is skipped
        mock_redis_get.assert_called_once_with("celery-task-lock:test_task1")
        self.assertEqual(mock_redis_lock.call_count, 0)
        self.assertEqual(task_calls, ["1-11-12", "2-21-22", "3-31-32"])


class GSM7Test(TembaTest):
    def test_is_gsm7(self):
        self.assertTrue(is_gsm7("Hello World! {} <>"))
        self.assertFalse(is_gsm7("No capital accented È!"))
        self.assertFalse(is_gsm7("No unicode. ☺"))

        replaced = replace_non_gsm7_accents("No capital accented È!")
        self.assertEqual("No capital accented E!", replaced)
        self.assertTrue(is_gsm7(replaced))

        replaced = replace_non_gsm7_accents("No crazy “word” quotes.")
        self.assertEqual('No crazy "word" quotes.', replaced)
        self.assertTrue(is_gsm7(replaced))

        # non breaking space
        replaced = replace_non_gsm7_accents("Pour chercher du boulot, comment fais-tu ?")
        self.assertEqual("Pour chercher du boulot, comment fais-tu ?", replaced)
        self.assertTrue(is_gsm7(replaced))

        # no tabs
        replaced = replace_non_gsm7_accents("I am followed by a\x09tab")
        self.assertEqual("I am followed by a tab", replaced)
        self.assertTrue(is_gsm7(replaced))

    def test_num_segments(self):
        ten_chars = "1234567890"

        self.assertEqual(1, calculate_num_segments(ten_chars * 16))
        self.assertEqual(1, calculate_num_segments(ten_chars * 6 + "“word”7890"))

        # 161 should be two segments
        self.assertEqual(2, calculate_num_segments(ten_chars * 16 + "1"))

        # 306 is exactly two gsm7 segments
        self.assertEqual(2, calculate_num_segments(ten_chars * 30 + "123456"))

        # 159 but with extended as last should be two as well
        self.assertEqual(2, calculate_num_segments(ten_chars * 15 + "123456789{"))

        # 355 should be three segments
        self.assertEqual(3, calculate_num_segments(ten_chars * 35 + "12345"))

        # 134 is exactly two ucs2 segments
        self.assertEqual(2, calculate_num_segments(ten_chars * 12 + "“word”12345678"))

        # 136 characters with quotes should be three segments
        self.assertEqual(3, calculate_num_segments(ten_chars * 13 + "“word”"))


class ModelsTest(TembaTest):
    def test_require_update_fields(self):
        contact = self.create_contact("Bob", twitter="bobby")
        flow = self.get_flow("color")
        run = FlowRun.objects.create(org=self.org, flow=flow, contact=contact)

        # we can save if we specify update_fields
        run.modified_on = timezone.now()
        run.save(update_fields=("modified_on",))

        # but not without
        with self.assertRaises(ValueError):
            run.modified_on = timezone.now()
            run.save()

    def test_chunk_list(self):
        curr = 0
        for chunk in chunk_list(range(100), 7):
            batch_curr = curr
            for item in chunk:
                self.assertEqual(item, curr)
                curr += 1

            # again to make sure things work twice
            curr = batch_curr
            for item in chunk:
                self.assertEqual(item, curr)
                curr += 1

        self.assertEqual(curr, 100)

    def test_patch_queryset_count(self):
        self.create_contact("Ann", twitter="ann")
        self.create_contact("Bob", twitter="bob")

        with self.assertNumQueries(0):
            qs = Contact.objects.all()
            patch_queryset_count(qs, lambda: 33)

            self.assertEqual(qs.count(), 33)


class ExportTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.group = self.create_group("New contacts", [])
        self.task = ExportContactsTask.objects.create(
            org=self.org, group=self.group, created_by=self.admin, modified_by=self.admin
        )

    def test_prepare_value(self):
        self.assertEqual(self.task.prepare_value(None), "")
        self.assertEqual(self.task.prepare_value("=()"), "'=()")  # escape formulas
        self.assertEqual(self.task.prepare_value(123), "123")
        self.assertEqual(self.task.prepare_value(True), True)
        self.assertEqual(self.task.prepare_value(False), False)

        dt = pytz.timezone("Africa/Nairobi").localize(datetime.datetime(2017, 2, 7, 15, 41, 23, 123_456))
        self.assertEqual(self.task.prepare_value(dt), datetime.datetime(2017, 2, 7, 14, 41, 23, 0))

    def test_task_status(self):
        self.assertEqual(self.task.status, ExportContactsTask.STATUS_PENDING)

        self.task.perform()

        self.assertEqual(self.task.status, ExportContactsTask.STATUS_COMPLETE)

        task2 = ExportContactsTask.objects.create(
            org=self.org, group=self.group, created_by=self.admin, modified_by=self.admin
        )

        # if task throws exception, will be marked as failed
        with patch.object(task2, "write_export") as mock_write_export:
            mock_write_export.side_effect = ValueError("Problem!")

            with self.assertRaises(Exception):
                task2.perform()

            self.assertEqual(task2.status, ExportContactsTask.STATUS_FAILED)

    @patch("temba.utils.export.BaseExportTask.MAX_EXCEL_ROWS", new_callable=PropertyMock)
    def test_tableexporter_xls(self, mock_max_rows):
        test_max_rows = 1500
        mock_max_rows.return_value = test_max_rows

        cols = []
        for i in range(32):
            cols.append("Column %d" % i)

        extra_cols = []
        for i in range(16):
            extra_cols.append("Extra Column %d" % i)

        exporter = TableExporter(self.task, "test", cols + extra_cols)

        values = []
        for i in range(32):
            values.append("Value %d" % i)

        extra_values = []
        for i in range(16):
            extra_values.append("Extra Value %d" % i)

        # write out 1050000 rows, that'll make two sheets
        for i in range(test_max_rows + 200):
            exporter.write_row(values + extra_values)

        temp_file, file_ext = exporter.save_file()
        workbook = load_workbook(filename=temp_file.name)

        self.assertEqual(2, len(workbook.worksheets))

        # check our sheet 1 values
        sheet1 = workbook.worksheets[0]

        rows = tuple(sheet1.rows)

        self.assertEqual(cols + extra_cols, [cell.value for cell in rows[0]])
        self.assertEqual(values + extra_values, [cell.value for cell in rows[1]])

        self.assertEqual(test_max_rows, len(list(sheet1.rows)))
        self.assertEqual(32 + 16, len(list(sheet1.columns)))

        sheet2 = workbook.worksheets[1]
        rows = tuple(sheet2.rows)
        self.assertEqual(cols + extra_cols, [cell.value for cell in rows[0]])
        self.assertEqual(values + extra_values, [cell.value for cell in rows[1]])

        self.assertEqual(200 + 2, len(list(sheet2.rows)))
        self.assertEqual(32 + 16, len(list(sheet2.columns)))

        os.unlink(temp_file.name)


class CurrencyTest(TembaTest):
    def test_currencies(self):

        self.assertEqual(currency_for_country("US").alpha_3, "USD")
        self.assertEqual(currency_for_country("EC").alpha_3, "USD")
        self.assertEqual(currency_for_country("FR").alpha_3, "EUR")
        self.assertEqual(currency_for_country("DE").alpha_3, "EUR")
        self.assertEqual(currency_for_country("YE").alpha_3, "YER")
        self.assertEqual(currency_for_country("AF").alpha_3, "AFN")

        for country in list(pycountry.countries):

            currency = currency_for_country(country.alpha_2)
            if currency is None:
                self.fail(f"Country missing currency: {country}")

        # a country that does not exist
        self.assertIsNone(currency_for_country("XX"))


class MiddlewareTest(TembaTest):
    def test_org_header(self):
        response = self.client.get(reverse("public.public_index"))
        self.assertFalse(response.has_header("X-Temba-Org"))

        self.login(self.superuser)

        response = self.client.get(reverse("public.public_index"))
        self.assertFalse(response.has_header("X-Temba-Org"))

        self.login(self.admin)

        response = self.client.get(reverse("public.public_index"))
        self.assertEqual(response["X-Temba-Org"], str(self.org.id))

    def test_branding(self):
        response = self.client.get(reverse("public.public_index"))
        self.assertEqual(response.context["request"].branding, settings.BRANDING["rapidpro.io"])

    def test_redirect(self):
        self.assertNotRedirect(self.client.get(reverse("public.public_index")), None)

        # now set our brand to redirect
        branding = copy.deepcopy(settings.BRANDING)
        branding["rapidpro.io"]["redirect"] = "/redirect"
        with self.settings(BRANDING=branding):
            self.assertRedirect(self.client.get(reverse("public.public_index")), "/redirect")

    def test_activate_language(self):
        self.assertContains(self.client.get(reverse("public.public_index")), "Sign Up")

        self.login(self.admin)

        self.assertContains(self.client.get(reverse("public.public_index")), "Sign Up")
        self.assertContains(self.client.get(reverse("contacts.contact_list")), "Import Contacts")

        UserSettings.objects.filter(user=self.admin).update(language="fr")

        self.assertContains(self.client.get(reverse("contacts.contact_list")), "Importer des contacts")


class MakeTestDBTest(SmartminTestMixin, TransactionTestCase):
    def test_command(self):
        self.create_anonymous_user()

        with ESMockWithScroll():
            call_command("test_db", num_orgs=3, num_contacts=30, seed=1234)

        org1, org2, org3 = tuple(Org.objects.order_by("id"))

        def assertOrgCounts(qs, counts):
            self.assertEqual([qs.filter(org=o).count() for o in (org1, org2, org3)], counts)

        self.assertEqual(
            User.objects.exclude(username__in=["AnonymousUser", "root", "rapidpro_flow", "temba_flow"]).count(), 12
        )
        assertOrgCounts(ContactField.user_fields.all(), [6, 6, 6])
        assertOrgCounts(ContactGroup.user_groups.all(), [10, 10, 10])
        assertOrgCounts(Contact.objects.all(), [12, 11, 7])

        org_1_all_contacts = ContactGroup.system_groups.get(org=org1, name="All Contacts")

        self.assertEqual(org_1_all_contacts.contacts.count(), 12)
        self.assertEqual(
            list(ContactGroupCount.objects.filter(group=org_1_all_contacts).values_list("count")), [(12,)]
        )

        # same seed should generate objects with same UUIDs
        self.assertEqual(ContactGroup.user_groups.order_by("id").first().uuid, "086dde0d-eb11-4413-aa44-6896fef91f7f")

        # check if contact fields are serialized
        self.assertIsNotNone(Contact.objects.first().fields)

        # check generate can't be run again on a now non-empty database
        with self.assertRaises(CommandError):
            call_command("test_db", num_orgs=3, num_contacts=30, seed=1234)


class PreDeployTest(TembaTest):
    def test_command(self):
        buffer = StringIO()
        call_command("pre_deploy", stdout=buffer)

        self.assertEqual("", buffer.getvalue())

        ExportContactsTask.create(self.org, self.admin)
        ExportContactsTask.create(self.org, self.admin)

        buffer = StringIO()
        call_command("pre_deploy", stdout=buffer)

        self.assertEqual(
            "WARNING: there are 2 unfinished tasks of type contact-export. Last one started 0\xa0minutes ago.\n",
            buffer.getvalue(),
        )


class JsonModelTestDefaultNull(models.Model):
    field = JSONAsTextField(default=dict, null=True)


class JsonModelTestDefault(models.Model):
    field = JSONAsTextField(default=dict, null=False)


class JsonModelTestNull(models.Model):
    field = JSONAsTextField(null=True)


class TestJSONAsTextField(TestCase):
    def test_invalid_default(self):
        class InvalidJsonModel(models.Model):
            field = JSONAsTextField(default={})

        model = InvalidJsonModel()
        self.assertEqual(
            model.check(),
            [
                checks.Warning(
                    msg=(
                        "JSONAsTextField default should be a callable instead of an instance so that it's not shared "
                        "between all field instances."
                    ),
                    hint="Use a callable instead, e.g., use `dict` instead of `{}`.",
                    obj=InvalidJsonModel._meta.get_field("field"),
                    id="postgres.E003",
                )
            ],
        )

    def test_to_python(self):

        field = JSONAsTextField(default=dict)

        self.assertEqual(field.to_python({}), {})

        self.assertEqual(field.to_python("{}"), {})

    def test_default_with_null(self):

        model = JsonModelTestDefaultNull()
        model.save()
        model.refresh_from_db()

        # the field in the database is null, and we have set the default value so we get the default value
        self.assertEqual(model.field, {})

        with connection.cursor() as cur:
            cur.execute("select * from utils_jsonmodeltestdefaultnull")

            data = cur.fetchall()
        # but in the database the field is saved as null
        self.assertEqual(data[0][1], None)

    def test_default_without_null(self):

        model = JsonModelTestDefault()
        model.save()
        model.refresh_from_db()

        # the field in the database saves the default value, and we get the default value back
        self.assertEqual(model.field, {})

        with connection.cursor() as cur:
            cur.execute("select * from utils_jsonmodeltestdefault")

            data = cur.fetchall()
        # and in the database the field saved as default value
        self.assertEqual(data[0][1], "{}")

    def test_invalid_field_values(self):
        model = JsonModelTestDefault()
        model.field = "53"
        self.assertRaises(ValueError, model.save)

        model.field = 34
        self.assertRaises(ValueError, model.save)

        model.field = ""
        self.assertRaises(ValueError, model.save)

    def test_write_None_value(self):
        model = JsonModelTestDefault()
        # assign None (null) value to the field
        model.field = None

        self.assertRaises(Exception, model.save)

    def test_read_None_value(self):
        with connection.cursor() as null_cur:
            null_cur.execute("DELETE FROM utils_jsonmodeltestnull")
            null_cur.execute("INSERT INTO utils_jsonmodeltestnull (field) VALUES (%s)", (None,))

            self.assertEqual(JsonModelTestNull.objects.first().field, None)

    def test_invalid_field_values_db(self):
        with connection.cursor() as cur:
            cur.execute("DELETE FROM utils_jsonmodeltestdefault")
            cur.execute("INSERT INTO utils_jsonmodeltestdefault (field) VALUES (%s)", ("53",))
            self.assertRaises(ValueError, JsonModelTestDefault.objects.first)

            cur.execute("DELETE FROM utils_jsonmodeltestdefault")
            cur.execute("INSERT INTO utils_jsonmodeltestdefault (field) VALUES (%s)", ("None",))
            self.assertRaises(ValueError, JsonModelTestDefault.objects.first)

            cur.execute("DELETE FROM utils_jsonmodeltestdefault")
            cur.execute("INSERT INTO utils_jsonmodeltestdefault (field) VALUES (%s)", ("null",))
            self.assertRaises(ValueError, JsonModelTestDefault.objects.first)


class TestJSONField(TembaTest):
    def test_jsonfield_decimal_encoding(self):
        contact = self.create_contact("Xavier", number="+5939790990001")

        with connection.cursor() as cur:
            cur.execute(
                "UPDATE contacts_contact SET fields = %s where id = %s",
                (
                    TembaJsonAdapter({"1eaf5c91-8d56-4ca0-8e00-9b1c0b12e722": {"number": Decimal("123.45")}}),
                    contact.id,
                ),
            )

            cur.execute("SELECT cast(fields as text) from contacts_contact where id = %s", (contact.id,))

            raw_fields = cur.fetchone()[0]

            self.assertEqual(raw_fields, '{"1eaf5c91-8d56-4ca0-8e00-9b1c0b12e722": {"number": 123.45}}')

            cur.execute("SELECT fields from contacts_contact where id = %s", (contact.id,))

            dict_fields = cur.fetchone()[0]
            number_field = dict_fields.get("1eaf5c91-8d56-4ca0-8e00-9b1c0b12e722", {}).get("number")

            self.assertEqual(number_field, Decimal("123.45"))


class MatchersTest(TembaTest):
    def test_string(self):
        self.assertEqual("abc", matchers.String())
        self.assertEqual("", matchers.String())
        self.assertNotEqual(None, matchers.String())
        self.assertNotEqual(123, matchers.String())

        self.assertEqual("abc", matchers.String(pattern=r"\w{3}$"))
        self.assertNotEqual("ab", matchers.String(pattern=r"\w{3}$"))
        self.assertNotEqual("abcd", matchers.String(pattern=r"\w{3}$"))

    def test_isodate(self):
        self.assertEqual("2013-02-01T07:08:09.100000+04:30", matchers.ISODate())
        self.assertEqual("2018-02-21T20:34:07.198537686Z", matchers.ISODate())
        self.assertEqual("2018-02-21T20:34:07.19853768Z", matchers.ISODate())
        self.assertEqual("2018-02-21T20:34:07.198Z", matchers.ISODate())
        self.assertEqual("2018-02-21T20:34:07Z", matchers.ISODate())
        self.assertEqual("2013-02-01T07:08:09.100000Z", matchers.ISODate())
        self.assertNotEqual(None, matchers.ISODate())
        self.assertNotEqual("abc", matchers.ISODate())

    def test_uuid4string(self):
        self.assertEqual("85ECBE45-E2DF-4785-8FC8-16FA941E0A79", matchers.UUID4String())
        self.assertEqual("85ecbe45-e2df-4785-8fc8-16fa941e0a79", matchers.UUID4String())
        self.assertNotEqual(None, matchers.UUID4String())
        self.assertNotEqual("abc", matchers.UUID4String())

    def test_dict(self):
        self.assertEqual({}, matchers.Dict())
        self.assertEqual({"a": "b"}, matchers.Dict())
        self.assertNotEqual(None, matchers.Dict())
        self.assertNotEqual([], matchers.Dict())


class NonBlockingLockTest(TestCase):
    def test_nonblockinglock(self):
        with NonBlockingLock(redis=get_redis_connection(), name="test_nonblockinglock", timeout=5) as lock:
            # we are able to get the initial lock
            self.assertTrue(lock.acquired)

            with NonBlockingLock(redis=get_redis_connection(), name="test_nonblockinglock", timeout=5) as lock:
                # but we are not able to get it the second time
                self.assertFalse(lock.acquired)
                # we need to terminate the execution
                lock.exit_if_not_locked()

        def raise_exception():
            with NonBlockingLock(redis=get_redis_connection(), name="test_nonblockinglock", timeout=5) as lock:
                if not lock.acquired:
                    raise LockNotAcquiredException

                raise Exception

        # any other exceptions are handled as usual
        self.assertRaises(Exception, raise_exception)


class JSONTest(TestCase):
    def test_json(self):
        self.assertEqual(OrderedDict({"one": 1, "two": Decimal("0.2")}), json.loads('{"one": 1, "two": 0.2}'))
        self.assertEqual('{"dt": "2018-08-27T20:41:28.123Z"}', json.dumps(dict(dt=ms_to_datetime(1_535_402_488_123))))


class AnalyticsTest(TestCase):
    def setUp(self):
        super().setUp()

        # create org and user stubs
        self.org = SimpleNamespace(
            id=1000, name="Some Org", brand="Some Brand", created_on=timezone.now(), account_value=lambda: 1000
        )
        self.admin = SimpleNamespace(
            username="admin@example.com", first_name="", last_name="", email="admin@example.com"
        )

        self.intercom_mock = MagicMock()
        temba.utils.analytics._intercom = self.intercom_mock
        temba.utils.analytics.init_analytics()

    @override_settings(IS_PROD=False)
    def test_identify_not_prod_env(self):
        result = temba.utils.analytics.identify(self.admin, "test", self.org)

        self.assertIsNone(result)
        self.intercom_mock.users.create.assert_not_called()
        self.intercom_mock.users.save.assert_not_called()

    @override_settings(IS_PROD=True)
    def test_identify_intercom_exception(self):
        self.intercom_mock.users.create.side_effect = Exception("Kimi says bwoah...")

        with patch("temba.utils.analytics.logger") as mocked_logging:
            temba.utils.analytics.identify(self.admin, "test", self.org)

        mocked_logging.error.assert_called_with("error posting to intercom", exc_info=True)

    @override_settings(IS_PROD=True)
    def test_identify_intercom(self):
        temba.utils.analytics.identify(self.admin, "test", self.org)

        # assert mocks
        self.intercom_mock.users.create.assert_called_with(
            custom_attributes={
                "brand": "test",
                "segment": mock.ANY,
                "org": self.org.name,
                "paid": self.org.account_value(),
            },
            email=self.admin.email,
            name=" ",
        )
        self.assertListEqual(
            self.intercom_mock.users.create.return_value.companies,
            [
                {
                    "company_id": self.org.id,
                    "name": self.org.name,
                    "created_at": mock.ANY,
                    "custom_attributes": {"brand": self.org.brand, "org_id": self.org.id},
                }
            ],
        )
        # did we actually call save?
        self.intercom_mock.users.save.assert_called_once()

    @override_settings(IS_PROD=True)
    def test_track_intercom(self):
        temba.utils.analytics.track(self.admin.email, "test event", properties={"plan": "free"})

        self.intercom_mock.events.create.assert_called_with(
            event_name="test event", created_at=mock.ANY, email=self.admin.email, metadata={"plan": "free"}
        )

    @override_settings(IS_PROD=False)
    def test_track_not_prod_env(self):
        result = temba.utils.analytics.track(self.admin.email, "test event", properties={"plan": "free"})

        self.assertIsNone(result)

        self.intercom_mock.events.create.assert_not_called()

    @override_settings(IS_PROD=True)
    def test_track_intercom_exception(self):
        self.intercom_mock.events.create.side_effect = Exception("It's raining today")

        with patch("temba.utils.analytics.logger") as mocked_logging:
            temba.utils.analytics.track(self.admin.email, "test event", properties={"plan": "free"})

        mocked_logging.error.assert_called_with("error posting to intercom", exc_info=True)

    @override_settings(IS_PROD=True)
    def test_consent_missing_user(self):
        self.intercom_mock.users.find.return_value = None
        temba.utils.analytics.change_consent(self.admin.email, consent=True)

        self.intercom_mock.users.create.assert_called_with(
            email=self.admin.email, custom_attributes=dict(consent=True, consent_changed=mock.ANY)
        )

    @override_settings(IS_PROD=True)
    def test_consent_invalid_user_decline(self):
        self.intercom_mock.users.find.return_value = None
        temba.utils.analytics.change_consent(self.admin.email, consent=False)

        self.intercom_mock.users.create.assert_not_called()
        self.intercom_mock.users.delete.assert_not_called()

    @override_settings(IS_PROD=True)
    def test_consent_valid_user(self):

        # valid user which did not consent
        self.intercom_mock.users.find.return_value = MagicMock(custom_attributes={"consent": False})

        temba.utils.analytics.change_consent(self.admin.email, consent=True)

        self.intercom_mock.users.create.assert_called_with(
            email=self.admin.email, custom_attributes=dict(consent=True, consent_changed=mock.ANY)
        )

    @override_settings(IS_PROD=True)
    def test_consent_valid_user_already_consented(self):
        # valid user which did not consent
        self.intercom_mock.users.find.return_value = MagicMock(custom_attributes={"consent": True})

        temba.utils.analytics.change_consent(self.admin.email, consent=True)

        self.intercom_mock.users.create.assert_not_called()

    @override_settings(IS_PROD=True)
    def test_consent_valid_user_decline(self):

        # valid user which did not consent
        self.intercom_mock.users.find.return_value = MagicMock(custom_attributes={"consent": False})

        temba.utils.analytics.change_consent(self.admin.email, consent=False)

        self.intercom_mock.users.create.assert_called_with(
            email=self.admin.email, custom_attributes=dict(consent=False, consent_changed=mock.ANY)
        )
        self.intercom_mock.users.delete.assert_called_with(mock.ANY)

    @override_settings(IS_PROD=True)
    def test_consent_exception(self):
        self.intercom_mock.users.find.side_effect = Exception("Kimi says bwoah...")

        with patch("temba.utils.analytics.logger") as mocked_logging:
            temba.utils.analytics.change_consent(self.admin.email, consent=False)

        mocked_logging.error.assert_called_with("error posting to intercom", exc_info=True)

    @override_settings(IS_PROD=False)
    def test_consent_not_prod_env(self):
        result = temba.utils.analytics.change_consent(self.admin.email, consent=False)

        self.assertIsNone(result)
        self.intercom_mock.users.find.assert_not_called()
        self.intercom_mock.users.create.assert_not_called()
        self.intercom_mock.users.delete.assert_not_called()

    def test_get_intercom_user(self):
        temba.utils.analytics.get_intercom_user(email="an email")

        self.intercom_mock.users.find.assert_called_with(email="an email")

    def test_get_intercom_user_resourcenotfound(self):
        self.intercom_mock.users.find.side_effect = intercom.errors.ResourceNotFound

        result = temba.utils.analytics.get_intercom_user(email="an email")

        self.assertIsNone(result)

    @override_settings(IS_PROD=False)
    def test_set_orgs_not_prod_env(self):
        result = temba.utils.analytics.set_orgs(email="an email", all_orgs=[self.org])

        self.assertIsNone(result)

        self.intercom_mock.users.find.assert_not_called()
        self.intercom_mock.users.save.assert_not_called()

    @override_settings(IS_PROD=True)
    def test_set_orgs_invalid_user(self):
        self.intercom_mock.users.find.return_value = None

        temba.utils.analytics.set_orgs(email="an email", all_orgs=[self.org])

        self.intercom_mock.users.find.assert_called_with(email="an email")
        self.intercom_mock.users.save.assert_not_called()

    @override_settings(IS_PROD=True)
    def test_set_orgs_valid_user_same_company(self):
        intercom_user = MagicMock(companies=[MagicMock(company_id=self.org.id)])
        self.intercom_mock.users.find.return_value = intercom_user

        temba.utils.analytics.set_orgs(email="an email", all_orgs=[self.org])

        self.intercom_mock.users.find.assert_called_with(email="an email")

        self.assertEqual(intercom_user.companies, [{"company_id": self.org.id, "name": self.org.name}])

        self.intercom_mock.users.save.assert_called_with(mock.ANY)

    @override_settings(IS_PROD=True)
    def test_set_orgs_valid_user_new_company(self):
        intercom_user = MagicMock(companies=[MagicMock(company_id=-1), MagicMock(company_id=self.org.id)])
        self.intercom_mock.users.find.return_value = intercom_user

        temba.utils.analytics.set_orgs(email="an email", all_orgs=[self.org])

        self.intercom_mock.users.find.assert_called_with(email="an email")

        self.assertListEqual(
            intercom_user.companies,
            [{"company_id": self.org.id, "name": self.org.name}, {"company_id": -1, "remove": True}],
        )

        self.intercom_mock.users.save.assert_called_with(mock.ANY)

    @override_settings(IS_PROD=True)
    def test_set_orgs_valid_user_without_a_company(self):
        intercom_user = MagicMock(companies=[MagicMock(company_id=-1), MagicMock(company_id=self.org.id)])
        self.intercom_mock.users.find.return_value = intercom_user

        # we are not setting any org for the user
        temba.utils.analytics.set_orgs(email="an email", all_orgs=[])

        self.intercom_mock.users.find.assert_called_with(email="an email")

        self.assertListEqual(
            intercom_user.companies, [{"company_id": -1, "remove": True}, {"company_id": self.org.id, "remove": True}]
        )

        self.intercom_mock.users.save.assert_called_with(mock.ANY)

    @override_settings(IS_PROD=False)
    def test_identify_org_not_prod_env(self):
        result = temba.utils.analytics.identify_org(org=self.org, attributes=None)

        self.assertIsNone(result)

        self.intercom_mock.companies.create.assert_not_called()

    @override_settings(IS_PROD=True)
    def test_identify_org_empty_attributes(self):
        result = temba.utils.analytics.identify_org(org=self.org, attributes=None)

        self.assertIsNone(result)

        self.intercom_mock.companies.create.assert_called_with(
            company_id=self.org.id,
            created_at=mock.ANY,
            custom_attributes={"brand": self.org.brand, "org_id": self.org.id},
            name=self.org.name,
        )

    @override_settings(IS_PROD=True)
    def test_identify_org_with_attributes(self):
        attributes = dict(
            website="https://example.com",
            industry="Mining",
            monthly_spend="a lot",
            this_is_not_an_intercom_attribute="or is it?",
        )

        result = temba.utils.analytics.identify_org(org=self.org, attributes=attributes)

        self.assertIsNone(result)

        self.intercom_mock.companies.create.assert_called_with(
            company_id=self.org.id,
            created_at=mock.ANY,
            custom_attributes={
                "brand": self.org.brand,
                "org_id": self.org.id,
                "this_is_not_an_intercom_attribute": "or is it?",
            },
            name=self.org.name,
            website="https://example.com",
            industry="Mining",
            monthly_spend="a lot",
        )


class IDSliceQuerySetTest(TestCase):
    def test_query_set(self):
        users = IDSliceQuerySet(User, [1], 0, 1)
        self.assertEqual(1, users[0].id)
        self.assertEqual(1, users[0:1][0].id)

        with self.assertRaises(IndexError):
            users[2]

        with self.assertRaises(IndexError):
            users[-1]

        with self.assertRaises(IndexError):
            users[1:2]

        with self.assertRaises(TypeError):
            users["foo"]

        users = IDSliceQuerySet(User, [1], 10, 100)
        self.assertEqual(1, users[10].id)
        self.assertEqual(1, users[10:11][0].id)

        with self.assertRaises(IndexError):
            users[0]

        with self.assertRaises(IndexError):
            users[11:15]

        users = IDSliceQuerySet(User, [], 0, 0)
        self.assertEqual(0, len(users))


class RedactTest(TestCase):
    def test_variations(self):
        # phone number variations
        self.assertEqual(
            redact._variations("+593979099111"),
            [
                "%2B593979099111",
                "0593979099111",
                "+593979099111",
                "593979099111",
                "93979099111",
                "3979099111",
                "979099111",
                "79099111",
                "9099111",
            ],
        )

        # reserved XML/HTML characters escaped and unescaped
        self.assertEqual(
            redact._variations("<?&>"),
            [
                "0&lt;?&amp;&gt;",
                "+&lt;?&amp;&gt;",
                "%2B%3C%3F%26%3E",
                "&lt;?&amp;&gt;",
                "0%3C%3F%26%3E",
                "%3C%3F%26%3E",
                "0<?&>",
                "+<?&>",
                "<?&>",
            ],
        )

        # reserved JSON characters escaped and unescaped
        self.assertEqual(
            redact._variations("\n\r\t😄"),
            [
                "%2B%0A%0D%09%F0%9F%98%84",
                "0%0A%0D%09%F0%9F%98%84",
                "%0A%0D%09%F0%9F%98%84",
                "0\\n\\r\\t\\ud83d\\ude04",
                "+\\n\\r\\t\\ud83d\\ude04",
                "\\n\\r\\t\\ud83d\\ude04",
                "0\n\r\t😄",
                "+\n\r\t😄",
                "\n\r\t😄",
            ],
        )

    def test_text(self):
        # no match returns original and false
        self.assertEqual(redact.text("this is <+private>", "<public>", "********"), "this is <+private>")
        self.assertEqual(redact.text("this is 0123456789", "9876543210", "********"), "this is 0123456789")

        # text contains un-encoded raw value to be redacted
        self.assertEqual(redact.text("this is <+private>", "<+private>", "********"), "this is ********")

        # text contains URL encoded version of the value to be redacted
        self.assertEqual(redact.text("this is %2Bprivate", "+private", "********"), "this is ********")

        # text contains JSON encoded version of the value to be redacted
        self.assertEqual(redact.text('this is "+private"', "+private", "********"), 'this is "********"')

        # text contains XML encoded version of the value to be redacted
        self.assertEqual(redact.text("this is &lt;+private&gt;", "<+private>", "********"), "this is ********")

        # test matching the value partially
        self.assertEqual(redact.text("this is 123456789", "+123456789", "********"), "this is ********")

        self.assertEqual(redact.text("this is +123456789", "123456789", "********"), "this is ********")
        self.assertEqual(redact.text("this is 123456789", "0123456789", "********"), "this is ********")

        # '3456789' matches the input string
        self.assertEqual(redact.text("this is 03456789", "+123456789", "********"), "this is 0********")

        # only rightmost 7 chars of the test matches
        self.assertEqual(redact.text("this is 0123456789", "xxx3456789", "********"), "this is 012********")

        # all matches replaced
        self.assertEqual(
            redact.text('{"number_full": "+593979099111", "number_short": "0979099111"}', "+593979099111", "********"),
            '{"number_full": "********", "number_short": "0********"}',
        )

        # custom mask
        self.assertEqual(redact.text("this is private", "private", "🌼🌼🌼🌼"), "this is 🌼🌼🌼🌼")

    def test_http_trace(self):
        # not an HTTP trace
        self.assertEqual(redact.http_trace("hello", "12345", "********", ("name",)), "********")

        # a JSON body
        self.assertEqual(
            redact.http_trace(
                'POST /c/t/23524/receive HTTP/1.1\r\nHost: yy12345\r\n\r\n{"name": "Bob Smith", "number": "xx12345"}',
                "12345",
                "********",
                ("name",),
            ),
            'POST /c/t/23524/receive HTTP/1.1\r\nHost: yy********\r\n\r\n{"name": "********", "number": "xx********"}',
        )

        # a URL-encoded body
        self.assertEqual(
            redact.http_trace(
                "POST /c/t/23524/receive HTTP/1.1\r\nHost: yy12345\r\n\r\nnumber=xx12345&name=Bob+Smith",
                "12345",
                "********",
                ("name",),
            ),
            "POST /c/t/23524/receive HTTP/1.1\r\nHost: yy********\r\n\r\nnumber=xx********&name=********",
        )

        # a body with neither encoding redacted as text if body keys not provided
        self.assertEqual(
            redact.http_trace(
                "POST /c/t/23524/receive HTTP/1.1\r\nHost: yy12345\r\n\r\n//xx12345//", "12345", "********"
            ),
            "POST /c/t/23524/receive HTTP/1.1\r\nHost: yy********\r\n\r\n//xx********//",
        )

        # a body with neither encoding returned as is if body keys provided but we couldn't parse the body
        self.assertEqual(
            redact.http_trace(
                "POST /c/t/23524/receive HTTP/1.1\r\nHost: yy12345\r\n\r\n//xx12345//", "12345", "********", ("name",)
            ),
            "POST /c/t/23524/receive HTTP/1.1\r\nHost: yy********\r\n\r\n********",
        )


class TestValidators(TestCase):
    def test_validate_external_url(self):
        cases = (
            dict(url="ftp://localhost/foo", error="must be http or https scheme"),
            dict(url="http://localhost/foo", error="cannot be localhost"),
            dict(url="http://localhost:80/foo", error="cannot be localhost"),
            dict(url="https://localhost/foo", error="cannot be localhost"),
            dict(url="http://127.0.00.1/foo", error="cannot be localhost"),
            dict(url="http://::1:80/foo", error="host cannot be resolved"),  # no ipv6 addresses for now
            dict(url="http://google.com/foo", error=None),
            dict(url="http://google.com:8000/foo", error=None),
            dict(url="HTTP://google.com:8000/foo", error=None),
        )

        for case in cases:
            if not case["error"]:
                try:
                    validate_external_url(case["url"])
                except Exception as e:
                    self.assertIsNone(e)

            else:
                with self.assertRaises(ValidationError) as cm:
                    cm.expected.__name__ = f'ValueError for {case["url"]}'
                    validate_external_url(case["url"])

                self.assertTrue(case["error"] in str(cm.exception), f"{case['error']} not in {cm.exception}")


class TestUUIDs(TembaTest):
    def test_seeded_generator(self):
        g = uuid.seeded_generator(123)
        self.assertEqual(uuid.UUID("66b3670d-b37d-4644-aedd-51167c53dac4", version=4), g())
        self.assertEqual(uuid.UUID("07ff4068-f3de-4c44-8a3e-921b952aa8d6", version=4), g())

        # same seed, same UUIDs
        g = uuid.seeded_generator(123)
        self.assertEqual(uuid.UUID("66b3670d-b37d-4644-aedd-51167c53dac4", version=4), g())
        self.assertEqual(uuid.UUID("07ff4068-f3de-4c44-8a3e-921b952aa8d6", version=4), g())

        # different seed, different UUIDs
        g = uuid.seeded_generator(456)
        self.assertEqual(uuid.UUID("8c338abf-94e2-4c73-9944-72f7a6ff5877", version=4), g())
        self.assertEqual(uuid.UUID("c8e0696f-b3f6-4e63-a03a-57cb95bdb6e3", version=4), g())
