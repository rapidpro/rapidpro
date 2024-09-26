import datetime
from collections import OrderedDict
from datetime import date, timezone as tzone
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

from celery.app.task import Task
from django_redis import get_redis_connection

from django import forms
from django.forms import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from temba.orgs.models import Org
from temba.tests import TembaTest, matchers, override_brand
from temba.utils import json, uuid
from temba.utils.compose import compose_serialize

from . import (
    chunk_list,
    countries,
    format_number,
    get_nested_key,
    languages,
    percentage,
    redact,
    set_nested_key,
    sizeof_fmt,
    str_to_bool,
)
from .checks import storage
from .crons import clear_cron_stats, cron_task
from .dates import date_range, datetime_to_str, datetime_to_timestamp, timestamp_to_datetime
from .fields import ExternalURLField, NameValidator
from .text import clean_string, generate_secret, generate_token, slugify_with, truncate, unsnakify
from .timezones import TimeZoneFormField, timezone_to_country_code


class InitTest(TembaTest):
    def test_sizeof_fmt(self):
        self.assertEqual("512.0 b", sizeof_fmt(512))
        self.assertEqual("1.0 Kb", sizeof_fmt(1024))
        self.assertEqual("1.0 Mb", sizeof_fmt(1024**2))
        self.assertEqual("1.0 Gb", sizeof_fmt(1024**3))
        self.assertEqual("1.0 Tb", sizeof_fmt(1024**4))
        self.assertEqual("1.0 Pb", sizeof_fmt(1024**5))
        self.assertEqual("1.0 Eb", sizeof_fmt(1024**6))
        self.assertEqual("1.0 Zb", sizeof_fmt(1024**7))
        self.assertEqual("1.0 Yb", sizeof_fmt(1024**8))
        self.assertEqual("1024.0 Yb", sizeof_fmt(1024**9))

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

    def test_generate_secret(self):
        rs = generate_secret(1000)
        self.assertEqual(1000, len(rs))
        self.assertFalse("1" in rs or "I" in rs or "0" in rs or "O" in rs)

    def test_percentage(self):
        self.assertEqual(0, percentage(0, 100))
        self.assertEqual(0, percentage(0, 0))
        self.assertEqual(0, percentage(100, 0))
        self.assertEqual(75, percentage(75, 100))
        self.assertEqual(76, percentage(759, 1000))

    def test_remove_control_charaters(self):
        self.assertIsNone(clean_string(None))
        self.assertEqual(clean_string("ngert\x07in."), "ngertin.")
        self.assertEqual(clean_string("Norbért"), "Norbért")

    def test_replace_non_characters(self):
        self.assertEqual(clean_string("Bangsa\ufddfBangsa"), "Bangsa\ufffdBangsa")

    def test_generate_token(self):
        self.assertEqual(len(generate_token()), 8)

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

    def test_nested_keys(self):
        nested = {}

        # set nested keys
        set_nested_key(nested, "favorites.beer", "Turbo King")
        self.assertEqual(nested, {"favorites": {"beer": "Turbo King"}})

        # get nested keys
        self.assertEqual("Turbo King", get_nested_key(nested, "favorites.beer"))
        self.assertEqual("", get_nested_key(nested, "favorites.missing"))
        self.assertEqual(None, get_nested_key(nested, "favorites.missing", None))


class DatesTest(TembaTest):
    def test_datetime_to_timestamp(self):
        d1 = datetime.datetime(2014, 1, 2, 3, 4, 5, microsecond=123_456, tzinfo=tzone.utc)
        self.assertEqual(datetime_to_timestamp(d1), 1_388_631_845_123_456)  # from http://unixtimestamp.50x.eu
        self.assertEqual(timestamp_to_datetime(1_388_631_845_123_456), d1)

        tz = ZoneInfo("Africa/Kigali")
        d2 = datetime.datetime(2014, 1, 2, 3, 4, 5, microsecond=123_456).replace(tzinfo=tz)
        self.assertEqual(datetime_to_timestamp(d2), 1_388_624_645_123_456)
        self.assertEqual(timestamp_to_datetime(1_388_624_645_123_456), d2.astimezone(tzone.utc))

    def test_datetime_to_str(self):
        tz = ZoneInfo("Africa/Kigali")
        d2 = datetime.datetime(2014, 1, 2, 3, 4, 5, 6).replace(tzinfo=tz)

        self.assertIsNone(datetime_to_str(None, "%Y-%m-%d %H:%M", tz=tz))
        self.assertEqual(datetime_to_str(d2, "%Y-%m-%d %H:%M", tz=tz), "2014-01-02 03:04")
        self.assertEqual(datetime_to_str(d2, "%Y/%m/%d %H:%M", tz=tzone.utc), "2014/01/02 01:04")
        self.assertEqual(datetime_to_str(date(2023, 8, 16), "%Y/%m/%d %H:%M", tz=tzone.utc), "2023/08/16 00:00")

    def test_date_range(self):
        self.assertEqual(
            [date(2015, 1, 29), date(2015, 1, 30), date(2015, 1, 31), date(2015, 2, 1)],
            list(date_range(date(2015, 1, 29), date(2015, 2, 2))),
        )
        self.assertEqual([], list(date_range(date(2015, 1, 29), date(2015, 1, 29))))


class CountriesTest(TembaTest):
    def test_from_tel(self):
        self.assertIsNone(countries.from_tel(""))
        self.assertIsNone(countries.from_tel("123"))
        self.assertEqual("EC", countries.from_tel("+593979123456"))
        self.assertEqual("US", countries.from_tel("+1 213 621 0002"))


class TimezonesTest(TembaTest):
    def test_field(self):
        field = TimeZoneFormField(help_text="Test field")

        self.assertEqual(field.choices[0], ("Pacific/Midway", "(GMT-1100) Pacific/Midway"))
        self.assertEqual(field.coerce("Africa/Kigali"), ZoneInfo("Africa/Kigali"))

    def test_timezone_country_code(self):
        self.assertEqual("RW", timezone_to_country_code(ZoneInfo("Africa/Kigali")))
        self.assertEqual("US", timezone_to_country_code(ZoneInfo("America/Chicago")))
        self.assertEqual("US", timezone_to_country_code(ZoneInfo("US/Pacific")))

        # GMT and UTC give empty
        self.assertEqual("", timezone_to_country_code(ZoneInfo("GMT")))
        self.assertEqual("", timezone_to_country_code(ZoneInfo("UTC")))


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

        # try it with a microsecond of 0 instead
        source["now"] = timezone.now().replace(microsecond=0)

        # encode it
        encoded = json.dumps(source)

        # test that we throw with unknown types
        with self.assertRaises(TypeError):
            json.dumps(dict(foo=Exception("invalid")))


class CronsTest(TembaTest):
    @patch("redis.client.StrictRedis.lock")
    @patch("redis.client.StrictRedis.get")
    def test_cron_task(self, mock_redis_get, mock_redis_lock):
        clear_cron_stats()

        mock_redis_get.return_value = None
        task_calls = []

        @cron_task()
        def test_task1(foo, bar):
            task_calls.append("1-%d-%d" % (foo, bar))
            return {"foo": 1}

        @cron_task(name="task2", time_limit=100)
        def test_task2(foo, bar):
            task_calls.append("2-%d-%d" % (foo, bar))
            return 1234

        @cron_task(name="task3", time_limit=100, lock_timeout=55)
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
        mock_redis_get.assert_any_call("celery-task-lock:task3")
        mock_redis_lock.assert_any_call("celery-task-lock:test_task1", timeout=900)
        mock_redis_lock.assert_any_call("celery-task-lock:task2", timeout=100)
        mock_redis_lock.assert_any_call("celery-task-lock:task3", timeout=55)

        self.assertEqual(task_calls, ["1-11-12", "2-21-22", "3-31-32"])

        r = get_redis_connection()
        self.assertEqual({b"test_task1", b"task2", b"task3"}, set(r.hkeys("cron_stats:last_start")))
        self.assertEqual({b"test_task1", b"task2", b"task3"}, set(r.hkeys("cron_stats:last_time")))
        self.assertEqual(
            {b"test_task1": b'{"foo": 1}', b"task2": b"1234", b"task3": b"null"}, r.hgetall("cron_stats:last_result")
        )
        self.assertEqual({b"test_task1": b"1", b"task2": b"1", b"task3": b"1"}, r.hgetall("cron_stats:call_count"))
        self.assertEqual({b"test_task1", b"task2", b"task3"}, set(r.hkeys("cron_stats:total_time")))

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


class MiddlewareTest(TembaTest):
    def test_org(self):

        self.other_org = Org.objects.create(
            name="Other Org",
            timezone=ZoneInfo("Africa/Kigali"),
            flow_languages=["eng", "kin"],
            created_by=self.admin,
            modified_by=self.admin,
        )
        self.other_org.initialize()

        response = self.client.get(reverse("public.public_index"))
        self.assertFalse(response.has_header("X-Temba-Org"))

        self.login(self.customer_support)

        # our staff user doesn't have a default org
        response = self.client.get(reverse("public.public_index"))
        self.assertFalse(response.has_header("X-Temba-Org"))

        # but they can specify an org to service as a header
        response = self.client.get(reverse("public.public_index"), headers={"X-Temba-Service-Org": str(self.org.id)})
        self.assertEqual(response["X-Temba-Org"], str(self.org.id))

        response = self.client.get(reverse("public.public_index"))
        self.assertFalse(response.has_header("X-Temba-Org"))

        self.login(self.admin)

        response = self.client.get(reverse("public.public_index"))
        self.assertEqual(response["X-Temba-Org"], str(self.org.id))

        # non-staff can't specify a different org from there own
        response = self.client.get(
            reverse("public.public_index"), headers={"X-Temba-Service-Org": str(self.other_org.id)}
        )
        self.assertNotEqual(response["X-Temba-Org"], str(self.other_org.id))

    def test_redirect(self):
        self.assertNotRedirect(self.client.get(reverse("public.public_index")), None)

        # now set our brand to redirect
        with override_brand(redirect="/redirect"):
            self.assertRedirect(self.client.get(reverse("public.public_index")), "/redirect")

    def test_language(self):
        def assert_text(text: str):
            self.assertContains(self.client.get(reverse("users.user_login")), text)

        # default is English
        assert_text("Sign In")

        # can be overridden in Django settings
        with override_settings(DEFAULT_LANGUAGE="es"):
            assert_text("Ingresar")

        # if we have an authenticated user, their setting takes priority
        self.login(self.admin)

        self.admin.settings.language = "fr"
        self.admin.settings.save(update_fields=("language",))

        assert_text("Se connecter")


class LanguagesTest(TembaTest):
    def test_get_name(self):
        with override_settings(NON_ISO6391_LANGUAGES={"acx", "frc", "kir"}):
            languages.reload()
            self.assertEqual("French", languages.get_name("fra"))
            self.assertEqual("Arabic (Omani, ISO-639-3)", languages.get_name("acx"))  # name is overridden
            self.assertEqual("Cajun French", languages.get_name("frc"))  # non ISO-639-1 lang explicitly included
            self.assertEqual("Kyrgyz", languages.get_name("kir"))
            self.assertEqual("Oromifa", languages.get_name("orm"))

            self.assertEqual("", languages.get_name("cpi"))  # not in our allowed languages
            self.assertEqual("", languages.get_name("xyz"))

            # should strip off anything after an open paren or semicolon
            self.assertEqual("Haitian", languages.get_name("hat"))

        languages.reload()

    def test_search_by_name(self):
        # check that search returns results and in the proper order
        self.assertEqual(
            [
                {"value": "afr", "name": "Afrikaans"},
                {"value": "fra", "name": "French"},
                {"value": "fry", "name": "Western Frisian"},
            ],
            languages.search_by_name("Fr"),
        )

        # usually only return ISO-639-1 languages but can add inclusions in settings
        with override_settings(NON_ISO6391_LANGUAGES={"afr", "afb", "acx", "frc"}):
            languages.reload()

            # order is based on name rather than code
            self.assertEqual(
                [
                    {"value": "afr", "name": "Afrikaans"},
                    {"value": "frc", "name": "Cajun French"},
                    {"value": "fra", "name": "French"},
                    {"value": "fry", "name": "Western Frisian"},
                ],
                languages.search_by_name("Fr"),
            )

            # searching and ordering uses overridden names
            self.assertEqual(
                [
                    {"value": "ara", "name": "Arabic"},
                    {"value": "afb", "name": "Arabic (Gulf, ISO-639-3)"},
                    {"value": "acx", "name": "Arabic (Omani, ISO-639-3)"},
                ],
                languages.search_by_name("Arabic"),
            )

        languages.reload()

    def alpha2_to_alpha3(self):
        self.assertEqual("eng", languages.alpha2_to_alpha3("en"))
        self.assertEqual("eng", languages.alpha2_to_alpha3("en-us"))
        self.assertEqual("spa", languages.alpha2_to_alpha3("es"))
        self.assertIsNone(languages.alpha2_to_alpha3("xx"))


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


class JSONTest(TestCase):
    def test_json(self):
        self.assertEqual(OrderedDict({"one": 1, "two": Decimal("0.2")}), json.loads('{"one": 1, "two": 0.2}'))
        self.assertEqual(
            '{"dt": "2018-08-27T20:41:28.123Z"}',
            json.dumps({"dt": datetime.datetime(2018, 8, 27, 20, 41, 28, 123000, tzinfo=tzone.utc)}),
        )


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
    def test_name_validator(self):
        cases = (
            (" ", "Cannot begin or end with whitespace."),
            (" hello", "Cannot begin or end with whitespace."),
            ("hello\t", "Cannot begin or end with whitespace."),
            ('hello "', 'Cannot contain the character: "'),
            ("hello \\", "Cannot contain the character: \\"),
            ("hello \0 world", "Cannot contain null characters."),
            ("x" * 65, "Cannot be longer than 64 characters."),
            ("hello world", None),
            ("x" * 64, None),
        )

        validator = NameValidator(64)

        for tc in cases:
            if tc[1]:
                with self.assertRaises(ValidationError) as cm:
                    validator(tc[0])

                self.assertEqual(tc[1], cm.exception.messages[0])
            else:
                try:
                    validator(tc[0])
                except Exception:
                    self.fail(f"unexpected validation error for '{tc[0]}'")

        self.assertEqual(NameValidator(64), validator)
        self.assertNotEqual(NameValidator(32), validator)

    def test_external_url_field(self):
        class Form(forms.Form):
            url = ExternalURLField()

        cases = (
            ("//[", ["Enter a valid URL."]),
            ("ftp://google.com", ["Must use HTTP or HTTPS."]),
            ("google.com", ["Enter a valid URL."]),
            ("http://localhost/foo", ["Cannot be a local or private host."]),
            ("http://localhost:80/foo", ["Cannot be a local or private host."]),
            ("http://127.0.00.1/foo", ["Cannot be a local or private host."]),  # loop back
            ("http://192.168.0.0/foo", ["Cannot be a local or private host."]),  # private
            ("http://255.255.255.255", ["Cannot be a local or private host."]),  # multicast
            ("http://169.254.169.254/latest", ["Cannot be a local or private host."]),  # link local
            ("http://::1:80/foo", ["Unable to resolve host."]),  # no ipv6 addresses for now
            ("http://google.com/foo", []),
            ("http://google.com:8000/foo", []),
            ("HTTP://google.com:8000/foo", []),
            ("HTTP://8.8.8.8/foo", []),
        )

        for tc in cases:
            form = Form({"url": tc[0]})
            is_valid = form.is_valid()

            if tc[1]:
                self.assertFalse(is_valid, f"form.is_valid() unexpectedly true for '{tc[0]}'")
                self.assertEqual({"url": tc[1]}, form.errors, f"validation errors mismatch for '{tc[0]}'")

            else:
                self.assertTrue(is_valid, f"form.is_valid() unexpectedly false for '{tc[0]}'")
                self.assertEqual({}, form.errors)


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


class ComposeTest(TembaTest):
    def test_empty_compose(self):
        self.assertEqual(compose_serialize(), {})


class SystemChecksTest(TembaTest):
    def test_storage(self):
        self.assertEqual(len(storage(None)), 0)

        with override_settings(STORAGES={"default": {"BACKEND": "x"}, "staticfiles": {"BACKEND": "x"}}):
            self.assertEqual(storage(None)[0].msg, "Missing 'archives' storage config.")
            self.assertEqual(storage(None)[1].msg, "Missing 'public' storage config.")

        with override_settings(STORAGE_URL=None):
            self.assertEqual(storage(None)[0].msg, "No storage URL set.")

        with override_settings(STORAGE_URL="http://example.com/uploads/"):
            self.assertEqual(storage(None)[0].msg, "Storage URL shouldn't end with trailing slash.")
