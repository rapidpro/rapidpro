# -*- coding: utf-8 -*-

from __future__ import absolute_import, unicode_literals

import datetime
import json

import pycountry
import pytz
import six
import time

from celery.app.task import Task
from decimal import Decimal
from django.conf import settings
from django.contrib.auth.models import User, Group
from django.core import mail
from django.core.management import call_command, CommandError
from django.core.urlresolvers import reverse
from django.test import override_settings, SimpleTestCase
from django.utils import timezone
from django_redis import get_redis_connection
from mock import patch, PropertyMock
from openpyxl import load_workbook
from temba.contacts.models import Contact, ContactField, ContactGroup, ContactGroupCount, ExportContactsTask
from temba.locations.models import AdminBoundary
from temba.msgs.models import Msg, SystemLabelCount
from temba.flows.models import FlowRun
from temba.orgs.models import Org, UserSettings
from temba.tests import TembaTest
from temba_expressions.evaluator import EvaluationContext, DateStyle
from . import format_decimal, slugify_with, str_to_datetime, str_to_time, date_to_utc_range, truncate, random_string
from . import json_to_dict, dict_to_struct, datetime_to_ms, ms_to_datetime, dict_to_json, str_to_bool
from . import percentage, datetime_to_json_date, json_date_to_datetime, clean_string
from . import datetime_to_str, chunk_list, get_country_code_by_name, datetime_to_epoch, voicexml
from .cache import get_cacheable_result, get_cacheable_attr, incrby_existing, QueueRecord
from .currencies import currency_for_country
from .email import send_simple_email, is_valid_address
from .export import TableExporter
from .expressions import migrate_template, evaluate_template, evaluate_template_compat, get_function_listing
from .expressions import _build_function_signature
from .gsm7 import is_gsm7, replace_non_gsm7_accents
from .nexmo import NCCOException, NCCOResponse
from .profiler import time_monitor
from .queues import start_task, complete_task, push_task, HIGH_PRIORITY, LOW_PRIORITY, nonoverlapping_task
from .timezones import TimeZoneFormField, timezone_to_country_code
from .voicexml import VoiceXMLException
from . import decode_base64


class InitTest(TembaTest):

    def test_decode_base64(self):

        self.assertEqual('This test\nhas a newline', decode_base64('This test\nhas a newline'))

        self.assertEqual('Please vote NO on the confirmation of Gorsuch.',
                         decode_base64('Please vote NO on the confirmation of Gorsuch.'))

        self.assertEqual('Bannon Explains The World ...\n\u201cThe Camp of the Saints',
                         decode_base64('QmFubm9uIEV4cGxhaW5zIFRoZSBXb3JsZCAuLi4K4oCcVGhlIENhbXAgb2YgdGhlIFNhaW50c+KA\r'))

        self.assertEqual('the sweat, the tears and the sacrifice of working America',
                         decode_base64('dGhlIHN3ZWF0LCB0aGUgdGVhcnMgYW5kIHRoZSBzYWNyaWZpY2Ugb2Ygd29ya2luZyBBbWVyaWNh\r'))

        self.assertIn('I find them to be friendly',
                      decode_base64('Tm93IGlzDQp0aGUgdGltZQ0KZm9yIGFsbCBnb29kDQpwZW9wbGUgdG8NCnJlc2lzdC4NCg0KSG93IGFib3V0IGhhaWt1cz8NCkkgZmluZCB0aGVtIHRvIGJlIGZyaWVuZGx5Lg0KcmVmcmlnZXJhdG9yDQoNCjAxMjM0NTY3ODkNCiFAIyQlXiYqKCkgW117fS09Xys7JzoiLC4vPD4/fFx+YA0KQUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVphYmNkZWZnaGlqa2xtbm9wcXJzdHV2d3h5eg=='))

    def test_datetime_to_ms(self):
        d1 = datetime.datetime(2014, 1, 2, 3, 4, 5, tzinfo=pytz.utc)
        self.assertEqual(datetime_to_ms(d1), 1388631845000)  # from http://unixtimestamp.50x.eu
        self.assertEqual(ms_to_datetime(1388631845000), d1)

        tz = pytz.timezone("Africa/Kigali")
        d2 = tz.localize(datetime.datetime(2014, 1, 2, 3, 4, 5))
        self.assertEqual(datetime_to_ms(d2), 1388624645000)
        self.assertEqual(ms_to_datetime(1388624645000), d2.astimezone(pytz.utc))

    def test_datetime_to_json_date(self):
        d1 = datetime.datetime(2014, 1, 2, 3, 4, 5, tzinfo=pytz.utc)
        self.assertEqual(datetime_to_json_date(d1), '2014-01-02T03:04:05.000Z')
        self.assertEqual(json_date_to_datetime('2014-01-02T03:04:05.000Z'), d1)
        self.assertEqual(json_date_to_datetime('2014-01-02T03:04:05.000'), d1)

        tz = pytz.timezone("Africa/Kigali")
        d2 = tz.localize(datetime.datetime(2014, 1, 2, 3, 4, 5))
        self.assertEqual(datetime_to_json_date(d2), '2014-01-02T01:04:05.000Z')
        self.assertEqual(json_date_to_datetime('2014-01-02T01:04:05.000Z'), d2.astimezone(pytz.utc))
        self.assertEqual(json_date_to_datetime('2014-01-02T01:04:05.000'), d2.astimezone(pytz.utc))

    def test_datetime_to_str(self):
        tz = pytz.timezone("Africa/Kigali")
        d2 = tz.localize(datetime.datetime(2014, 1, 2, 3, 4, 5, 6))

        self.assertEqual(datetime_to_str(d2), '2014-01-02T01:04:05.000006Z')  # no format
        self.assertEqual(datetime_to_str(d2, format='%Y-%m-%d'), '2014-01-02')  # format provided
        self.assertEqual(datetime_to_str(d2, tz=tz), '2014-01-02T03:04:05.000006Z')  # in specific timezone
        self.assertEqual(datetime_to_str(d2, ms=False), '2014-01-02T01:04:05Z')  # no ms
        self.assertEqual(datetime_to_str(d2.date()), '2014-01-02T00:00:00.000000Z')  # no ms

    def test_datetime_to_epoch(self):
        dt = json_date_to_datetime('2014-01-02T01:04:05.000Z')
        self.assertEqual(1388624645, datetime_to_epoch(dt))

    def test_str_to_datetime(self):
        tz = pytz.timezone('Asia/Kabul')
        with patch.object(timezone, 'now', return_value=tz.localize(datetime.datetime(2014, 1, 2, 3, 4, 5, 6))):
            self.assertIsNone(str_to_datetime(None, tz))  # none
            self.assertIsNone(str_to_datetime('', tz))  # empty string
            self.assertIsNone(str_to_datetime('xxx', tz))  # unparseable string
            self.assertIsNone(str_to_datetime('xxx', tz, fill_time=False))  # unparseable string
            self.assertEqual(tz.localize(datetime.datetime(2013, 2, 1, 3, 4, 5, 6)),
                             str_to_datetime('01-02-2013', tz, dayfirst=True))  # day first
            self.assertEqual(tz.localize(datetime.datetime(2013, 1, 2, 3, 4, 5, 6)),
                             str_to_datetime('01-02-2013', tz, dayfirst=False))  # month first
            self.assertEqual(tz.localize(datetime.datetime(2013, 1, 31, 3, 4, 5, 6)),
                             str_to_datetime('01-31-2013', tz, dayfirst=True))  # impossible as day first
            self.assertEqual(tz.localize(datetime.datetime(2013, 2, 1, 7, 8, 5, 6)),
                             str_to_datetime('01-02-2013 07:08', tz, dayfirst=True))  # hour and minute provided
            self.assertEqual(tz.localize(datetime.datetime(2013, 2, 1, 7, 8, 9, 100000)),
                             str_to_datetime('01-02-2013 07:08:09.100000', tz, dayfirst=True))  # complete time provided
            self.assertEqual(tz.localize(datetime.datetime(2013, 2, 1, 0, 0, 0, 0)),
                             str_to_datetime('01-02-2013', tz, dayfirst=True, fill_time=False))  # no time filling

            # just year
            self.assertEqual(datetime.datetime(123, 1, 2, 3, 4, 5, 6, tz),
                             str_to_datetime('123', tz))

        # localizing while in DST to something outside DST
        tz = pytz.timezone('US/Eastern')
        with patch.object(timezone, 'now', return_value=tz.localize(datetime.datetime(2029, 11, 1, 12, 30, 0, 0))):
            parsed = str_to_datetime('06-11-2029', tz, dayfirst=True)
            self.assertEqual(tz.localize(datetime.datetime(2029, 11, 6, 12, 30, 0, 0)),
                             parsed)

            # assert there is no DST offset
            self.assertFalse(parsed.tzinfo.dst(parsed))

            self.assertEqual(tz.localize(datetime.datetime(2029, 11, 6, 13, 45, 0, 0)),
                             str_to_datetime('06-11-2029 13:45', tz, dayfirst=True))

        # deal with datetimes that have timezone info
        self.assertEqual(pytz.utc.localize(datetime.datetime(2016, 11, 21, 20, 36, 51, 215681)).astimezone(tz),
                         str_to_datetime('2016-11-21T20:36:51.215681Z', tz))

        self.assertEqual(datetime.datetime(123, 1, 2, 5, 4, 5, 6, pytz.utc),
                         str_to_datetime('123-1-2T5:4:5.000006Z', tz))

    def test_str_to_time(self):
        tz = pytz.timezone('Asia/Kabul')
        with patch.object(timezone, 'now', return_value=tz.localize(datetime.datetime(2014, 1, 2, 3, 4, 5, 6))):
            self.assertEqual(datetime.time(3, 4), str_to_time('03:04'))  # zero padded
            self.assertEqual(datetime.time(3, 4), str_to_time('3:4'))  # not zero padded
            self.assertEqual(datetime.time(3, 4), str_to_time('01-02-2013 03:04'))  # with date
            self.assertEqual(datetime.time(15, 4), str_to_time('3:04 PM'))  # as PM

    def test_date_to_utc_range(self):
        self.assertEqual(date_to_utc_range(datetime.date(2017, 2, 20), self.org), (
            datetime.datetime(2017, 2, 19, 22, 0, 0, 0, tzinfo=pytz.UTC),
            datetime.datetime(2017, 2, 20, 22, 0, 0, 0, tzinfo=pytz.UTC)
        ))

    def test_str_to_bool(self):
        self.assertFalse(str_to_bool(None))
        self.assertFalse(str_to_bool(''))
        self.assertFalse(str_to_bool('x'))
        self.assertTrue(str_to_bool('Y'))
        self.assertTrue(str_to_bool('Yes'))
        self.assertTrue(str_to_bool('TRUE'))
        self.assertTrue(str_to_bool('1'))

    def test_format_decimal(self):
        self.assertEquals('', format_decimal(None))
        self.assertEquals('0', format_decimal(Decimal('0.0')))
        self.assertEquals('10', format_decimal(Decimal('10')))
        self.assertEquals('100', format_decimal(Decimal('100.0')))
        self.assertEquals('123', format_decimal(Decimal('123')))
        self.assertEquals('123', format_decimal(Decimal('123.0')))
        self.assertEquals('123.34', format_decimal(Decimal('123.34')))
        self.assertEquals('123.34', format_decimal(Decimal('123.3400000')))
        self.assertEquals('-123', format_decimal(Decimal('-123.0')))

    def test_slugify_with(self):
        self.assertEquals('foo_bar', slugify_with('foo bar'))
        self.assertEquals('foo$bar', slugify_with('foo bar', '$'))

    def test_truncate(self):
        self.assertEquals('abc', truncate('abc', 5))
        self.assertEquals('abcde', truncate('abcde', 5))
        self.assertEquals('ab...', truncate('abcdef', 5))

    def test_random_string(self):
        rs = random_string(1000)
        self.assertEquals(1000, len(rs))
        self.assertFalse('1' in rs or 'I' in rs or '0' in rs or 'O' in rs)

    def test_percentage(self):
        self.assertEquals(0, percentage(0, 100))
        self.assertEquals(0, percentage(0, 0))
        self.assertEquals(0, percentage(100, 0))
        self.assertEquals(75, percentage(75, 100))
        self.assertEquals(76, percentage(759, 1000))

    def test_get_country_code_by_name(self):
        self.assertEqual('RW', get_country_code_by_name('Rwanda'))
        self.assertEqual('US', get_country_code_by_name('United States of America'))
        self.assertEqual('US', get_country_code_by_name('United States'))
        self.assertEqual('GB', get_country_code_by_name('United Kingdom'))
        self.assertEqual('CI', get_country_code_by_name('Ivory Coast'))
        self.assertEqual('CD', get_country_code_by_name('Democratic Republic of the Congo'))

    def test_remove_control_charaters(self):
        self.assertIsNone(clean_string(None))
        self.assertEqual(clean_string("ngert\x07in."), "ngertin.")
        self.assertEqual(clean_string("Norbért"), "Norbért")


class TimezonesTest(TembaTest):
    def test_field(self):
        field = TimeZoneFormField(help_text="Test field")

        self.assertEqual(field.choices[0], ('Pacific/Midway', u'(GMT-1100) Pacific/Midway'))
        self.assertEqual(field.coerce("Africa/Kigali"), pytz.timezone("Africa/Kigali"))

    def test_timezone_country_code(self):
        self.assertEqual('RW', timezone_to_country_code(pytz.timezone('Africa/Kigali')))
        self.assertEqual('US', timezone_to_country_code(pytz.timezone('America/Chicago')))
        self.assertEqual('US', timezone_to_country_code(pytz.timezone('US/Pacific')))

        # GMT and UTC give empty
        self.assertEqual('', timezone_to_country_code(pytz.timezone('GMT')))
        self.assertEqual('', timezone_to_country_code(pytz.timezone('UTC')))


class TemplateTagTest(TembaTest):

    def test_icon(self):
        from temba.campaigns.models import Campaign
        from temba.triggers.models import Trigger
        from temba.flows.models import Flow
        from temba.utils.templatetags.temba import icon

        campaign = Campaign.create(self.org, self.admin, 'Test Campaign', self.create_group('Test group', []))
        flow = Flow.create(self.org, self.admin, 'Test Flow')
        trigger = Trigger.objects.create(org=self.org, keyword='trigger', flow=flow, created_by=self.admin, modified_by=self.admin)

        self.assertEquals('icon-instant', icon(campaign))
        self.assertEquals('icon-feed', icon(trigger))
        self.assertEquals('icon-tree', icon(flow))
        self.assertEquals("", icon(None))

    def test_format_seconds(self):
        from temba.utils.templatetags.temba import format_seconds

        self.assertIsNone(format_seconds(None))

        # less than a minute
        self.assertEquals("30 sec", format_seconds(30))

        # round down
        self.assertEquals("1 min", format_seconds(89))

        # round up
        self.assertEquals("2 min", format_seconds(100))

    def test_delta(self):
        from temba.utils.templatetags.temba import delta_filter

        # empty
        self.assertEqual('', delta_filter(datetime.timedelta(seconds=0)))

        # in the future
        self.assertEqual('0 seconds', delta_filter(datetime.timedelta(seconds=-10)))

        # some valid times
        self.assertEqual('2 minutes, 40 seconds', delta_filter(datetime.timedelta(seconds=160)))
        self.assertEqual('5 minutes', delta_filter(datetime.timedelta(seconds=300)))
        self.assertEqual('10 minutes, 1 second', delta_filter(datetime.timedelta(seconds=601)))

        # non-delta arg
        self.assertEqual('', delta_filter('Invalid'))


class CacheTest(TembaTest):

    def test_get_cacheable_result(self):
        self.create_contact("Bob", number="1234")

        def calculate():
            return Contact.objects.all().count()

        with self.assertNumQueries(1):
            self.assertEqual(get_cacheable_result('test_contact_count', 60, calculate), 1)  # from db
        with self.assertNumQueries(0):
            self.assertEqual(get_cacheable_result('test_contact_count', 60, calculate), 1)  # from cache

        self.create_contact("Jim", number="2345")

        with self.assertNumQueries(0):
            self.assertEqual(get_cacheable_result('test_contact_count', 60, calculate), 1)  # not updated

        get_redis_connection().delete('test_contact_count')  # delete from cache for force re-fetch from db

        with self.assertNumQueries(1):
            self.assertEqual(get_cacheable_result('test_contact_count', 60, calculate), 2)  # from db
        with self.assertNumQueries(0):
            self.assertEqual(get_cacheable_result('test_contact_count', 60, calculate), 2)  # from cache

    def test_get_cacheable_attr(self):
        def calculate():
            return "CALCULATED"

        self.assertEqual(get_cacheable_attr(self, '_test_value', calculate), "CALCULATED")
        self._test_value = "CACHED"
        self.assertEqual(get_cacheable_attr(self, '_test_value', calculate), "CACHED")

    def test_incrby_existing(self):
        r = get_redis_connection()
        r.setex('foo', 100, 10)
        r.set('bar', 20)

        incrby_existing('foo', 3, r)  # positive delta
        self.assertEqual(r.get('foo'), '13')
        self.assertTrue(r.ttl('foo') > 0)

        incrby_existing('foo', -1, r)  # negative delta
        self.assertEqual(r.get('foo'), '12')
        self.assertTrue(r.ttl('foo') > 0)

        r.setex('foo', 100, 0)
        incrby_existing('foo', 5, r)  # zero val key
        self.assertEqual(r.get('foo'), '5')
        self.assertTrue(r.ttl('foo') > 0)

        incrby_existing('bar', 5, r)  # persistent key
        self.assertEqual(r.get('bar'), '25')
        self.assertTrue(r.ttl('bar') < 0)

        incrby_existing('xxx', -2, r)  # non-existent key
        self.assertIsNone(r.get('xxx'))

    def test_queue_record(self):
        items1 = [dict(id=1), dict(id=2), dict(id=3)]
        lock = QueueRecord('test_items', lambda i: i['id'])
        self.assertEqual(lock.filter_unqueued(items1), [dict(id=1), dict(id=2), dict(id=3)])

        lock.set_queued(items1)  # mark those items as queued now

        self.assertTrue(lock.is_queued(dict(id=3)))
        self.assertFalse(lock.is_queued(dict(id=4)))

        # try getting access to queued item #3 and a new item #4
        items2 = [dict(id=3), dict(id=4)]
        self.assertEqual(lock.filter_unqueued(items2), [dict(id=4)])

        # check locked items are still locked tomorrow
        with patch('temba.utils.cache.timezone') as mock_timezone:
            mock_timezone.now.return_value = timezone.now() + datetime.timedelta(days=1)

            lock = QueueRecord('test_items', lambda i: i['id'])
            self.assertEqual(lock.filter_unqueued([dict(id=3)]), [])


class EmailTest(TembaTest):

    @override_settings(SEND_EMAILS=True)
    def test_send_simple_email(self):
        send_simple_email(['recipient@bar.com'], "Test Subject", "Test Body")
        self.assertEquals(len(mail.outbox), 1)
        self.assertEquals(mail.outbox[0].from_email, settings.DEFAULT_FROM_EMAIL)
        self.assertEquals(mail.outbox[0].subject, "Test Subject")
        self.assertEquals(mail.outbox[0].body, "Test Body")
        self.assertEquals(mail.outbox[0].recipients(), ['recipient@bar.com'])

        send_simple_email(['recipient@bar.com'], "Test Subject", "Test Body", from_email='no-reply@foo.com')
        self.assertEquals(len(mail.outbox), 2)
        self.assertEquals(mail.outbox[1].from_email, 'no-reply@foo.com')
        self.assertEquals(mail.outbox[1].subject, "Test Subject")
        self.assertEquals(mail.outbox[1].body, "Test Body")
        self.assertEquals(mail.outbox[1].recipients(), ["recipient@bar.com"])

    def test_is_valid_address(self):

        self.VALID_EMAILS = [

            # Cases from https://en.wikipedia.org/wiki/Email_address
            'prettyandsimple@example.com',
            'very.common@example.com',
            'disposable.style.email.with+symbol@example.com',
            'other.email-with-dash@example.com',
            'x@example.com',
            '"much.more unusual"@example.com',
            '"very.unusual.@.unusual.com"@example.com'
            '"very.(),:;<>[]\".VERY.\"very@\\ \"very\".unusual"@strange.example.com',
            'example-indeed@strange-example.com',
            "#!$%&'*+-/=?^_`{}|~@example.org",
            '"()<>[]:,;@\\\"!#$%&\'-/=?^_`{}| ~.a"@example.org'
            '" "@example.org',
            'example@localhost',
            'example@s.solutions',


            # Cases from Django tests
            'email@here.com',
            'weirder-email@here.and.there.com',
            'email@[127.0.0.1]',
            'email@[2001:dB8::1]',
            'email@[2001:dB8:0:0:0:0:0:1]',
            'email@[::fffF:127.0.0.1]',
            'example@valid-----hyphens.com',
            'example@valid-with-hyphens.com',
            'test@domain.with.idn.tld.उदाहरण.परीक्षा',
            'email@localhost',
            '"test@test"@example.com',
            'example@atm.%s' % ('a' * 63),
            'example@%s.atm' % ('a' * 63),
            'example@%s.%s.atm' % ('a' * 63, 'b' * 10),
            '"\\\011"@here.com',
            'a@%s.us' % ('a' * 63)
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
            'Abc.example.com',
            'A@b@c@example.com',
            'a"b(c)d,e:f;g<h>i[j\k]l@example.com'
            'just"not"right@example.com'
            'this is"not\allowed@example.com'
            'this\ still\"not\\allowed@example.com'
            '1234567890123456789012345678901234567890123456789012345678901234+x@example.com'
            'john..doe@example.com'
            'john.doe@example..com'

            # Cases from Django tests
            'example@atm.%s' % ('a' * 64),
            'example@%s.atm.%s' % ('b' * 64, 'a' * 63),
            None,
            '',
            'abc',
            'abc@',
            'abc@bar',
            'a @x.cz',
            'abc@.com',
            'something@@somewhere.com',
            'email@127.0.0.1',
            'email@[127.0.0.256]',
            'email@[2001:db8::12345]',
            'email@[2001:db8:0:0:0:0:1]',
            'email@[::ffff:127.0.0.256]',
            'example@invalid-.com',
            'example@-invalid.com',
            'example@invalid.com-',
            'example@inv-.alid-.com',
            'example@inv-.-alid.com',
            'test@example.com\n\n<script src="x.js">',
            # Quoted-string format (CR not allowed)
            '"\\\012"@here.com',
            'trailingdot@shouldfail.com.',
            # Max length of domain name labels is 63 characters per RFC 1034.
            'a@%s.us' % ('a' * 64),
            # Trailing newlines in username or domain not allowed
            'a@b.com\n',
            'a\n@b.com',
            '"test@test"\n@example.com',
            'a@[127.0.0.1]\n'
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
        source = dict(name="Date Test", age=10, now=now)

        # encode it
        encoded = dict_to_json(source)

        # now decode it back out
        decoded = json_to_dict(encoded)

        # should be the same as our source
        self.assertDictEqual(source, decoded)

        # test the same using our object mocking
        mock = dict_to_struct('Mock', json.loads(encoded), ['now'])
        self.assertEquals(mock.now, source['now'])

        # try it with a microsecond of 0 instead
        source['now'] = timezone.now().replace(microsecond=0)

        # encode it
        encoded = dict_to_json(source)

        # now decode it back out
        decoded = json_to_dict(encoded)

        # should be the same as our source
        self.assertDictEqual(source, decoded)

        # test the same using our object mocking
        mock = dict_to_struct('Mock', json.loads(encoded), ['now'])
        self.assertEquals(mock.now, source['now'])


class QueueTest(TembaTest):

    def test_queueing(self):
        r = get_redis_connection()

        args1 = dict(task=1)

        # basic push and pop
        push_task(self.org, None, 'test', args1)
        org_id, task = start_task('test')
        self.assertEqual(args1, task)
        self.assertEqual(org_id, self.org.id)

        # should show as having one worker on that worker
        self.assertEqual(r.zscore('test:active', self.org.id), 1)

        # there aren't any more tasks so this will actually clear our active worker count
        self.assertFalse(start_task('test')[1])
        self.assertIsNone(r.zscore('test:active', self.org.id))

        # marking the task as complete should also be a no-op
        complete_task('test', self.org.id)
        self.assertIsNone(r.zscore('test:active', self.org.id))

        # pop on another task and start it and complete it
        push_task(self.org, None, 'test', args1)
        self.assertEquals(args1, start_task('test')[1])
        complete_task('test', self.org.id)

        # should have no active workers
        self.assertEqual(r.zscore('test:active', self.org.id), 0)

        # ok, try pushing and popping multiple on now
        args2 = dict(task=2)

        push_task(self.org, None, 'test', args1)
        push_task(self.org, None, 'test', args2)

        # should come back in order of insertion
        self.assertEquals(args1, start_task('test')[1])
        self.assertEquals(args2, start_task('test')[1])

        # two active workers
        self.assertEqual(r.zscore('test:active', self.org.id), 2)

        # mark one as complete
        complete_task('test', self.org.id)
        self.assertEqual(r.zscore('test:active', self.org.id), 1)

        # start another, this will clear our counts
        self.assertFalse(start_task('test')[1])
        self.assertIsNone(r.zscore('test:active', self.org.id))

        complete_task('test', self.org.id)
        self.assertIsNone(r.zscore('test:active', self.org.id))

        # ok, same set up
        push_task(self.org, None, 'test', args1)
        push_task(self.org, None, 'test', args2)

        # but add a high priority item this time
        args3 = dict(task=3)
        push_task(self.org, None, 'test', args3, HIGH_PRIORITY)

        # and a low priority task
        args4 = dict(task=4)
        push_task(self.org, None, 'test', args4, LOW_PRIORITY)

        # high priority should be first out, then defaults, then low
        self.assertEquals(args3, start_task('test')[1])
        self.assertEquals(args1, start_task('test')[1])
        self.assertEquals(args2, start_task('test')[1])
        self.assertEquals(args4, start_task('test')[1])

        self.assertEqual(r.zscore('test:active', self.org.id), 4)

        self.assertFalse(start_task('test')[1])
        self.assertIsNone(r.zscore('test:active', self.org.id))

    def test_org_queuing(self):
        r = get_redis_connection()

        self.create_secondary_org()

        args = [dict(task=i) for i in range(6)]

        push_task(self.org, None, 'test', args[4], LOW_PRIORITY)
        push_task(self.org, None, 'test', args[2])
        push_task(self.org, None, 'test', args[0], HIGH_PRIORITY)

        push_task(self.org2, None, 'test', args[3])
        push_task(self.org2, None, 'test', args[1], HIGH_PRIORITY)
        push_task(self.org2, None, 'test', args[5], LOW_PRIORITY)

        # order should alternate between the two orgs (based on # of active workers)
        for i in range(6):
            task = start_task('test')[1]['task']
            self.assertEqual(i, task)

        # each org should show 3 active works
        self.assertEqual(r.zscore('test:active', self.org.id), 3)
        self.assertEqual(r.zscore('test:active', self.org2.id), 3)

        self.assertFalse(start_task('test')[1])

        # no more tasks to do, both should now be empty
        self.assertIsNone(r.zscore('test:active', self.org.id))
        self.assertIsNone(r.zscore('test:active', self.org2.id))

    @patch('redis.client.StrictRedis.lock')
    @patch('redis.client.StrictRedis.get')
    def test_nonoverlapping_task(self, mock_redis_get, mock_redis_lock):
        mock_redis_get.return_value = None
        task_calls = []

        @nonoverlapping_task()
        def test_task1(foo, bar):
            task_calls.append('1-%d-%d' % (foo, bar))

        @nonoverlapping_task(name='task2', time_limit=100)
        def test_task2(foo, bar):
            task_calls.append('2-%d-%d' % (foo, bar))

        @nonoverlapping_task(name='task3', time_limit=100, lock_key='test_key', lock_timeout=55)
        def test_task3(foo, bar):
            task_calls.append('3-%d-%d' % (foo, bar))

        self.assertIsInstance(test_task1, Task)
        self.assertIsInstance(test_task2, Task)
        self.assertEqual(test_task2.name, 'task2')
        self.assertEqual(test_task2.time_limit, 100)
        self.assertIsInstance(test_task3, Task)
        self.assertEqual(test_task3.name, 'task3')
        self.assertEqual(test_task3.time_limit, 100)

        test_task1(11, 12)
        test_task2(21, bar=22)
        test_task3(foo=31, bar=32)

        mock_redis_get.assert_any_call('celery-task-lock:test_task1')
        mock_redis_get.assert_any_call('celery-task-lock:task2')
        mock_redis_get.assert_any_call('test_key')
        mock_redis_lock.assert_any_call('celery-task-lock:test_task1', timeout=900)
        mock_redis_lock.assert_any_call('celery-task-lock:task2', timeout=100)
        mock_redis_lock.assert_any_call('test_key', timeout=55)

        self.assertEqual(task_calls, ['1-11-12', '2-21-22', '3-31-32'])

        # simulate task being already running
        mock_redis_get.reset_mock()
        mock_redis_get.return_value = 'xyz'
        mock_redis_lock.reset_mock()

        # try to run again
        test_task1(13, 14)

        # check that task is skipped
        mock_redis_get.assert_called_once_with('celery-task-lock:test_task1')
        self.assertEqual(mock_redis_lock.call_count, 0)
        self.assertEqual(task_calls, ['1-11-12', '2-21-22', '3-31-32'])


class ExpressionsTest(TembaTest):

    def setUp(self):
        super(ExpressionsTest, self).setUp()

        contact = self.create_contact("Joe Blow", "123")
        contact.language = u'eng'
        contact.save()

        variables = dict()
        variables['contact'] = contact.build_expressions_context()
        variables['flow'] = dict(water_source="Well",     # key with underscore
                                 blank="",                # blank string
                                 arabic="اثنين ثلاثة",    # RTL chars
                                 english="two three",     # LTR chars
                                 urlstuff=' =&\u0628',    # stuff that needs URL encoding
                                 users=5,                 # numeric as int
                                 count="5",               # numeric as string
                                 average=2.5,             # numeric as float
                                 joined=datetime.datetime(2014, 12, 1, 9, 0, 0, 0, timezone.utc),  # date as datetime
                                 started="1/12/14 9:00")  # date as string

        self.context = EvaluationContext(variables, timezone.utc, DateStyle.DAY_FIRST)

    def test_evaluate_template(self):
        self.assertEquals(("Hello World", []), evaluate_template('Hello World', self.context))  # no expressions
        self.assertEquals(("Hello = Well 5", []),
                          evaluate_template("Hello = @(flow.water_source) @flow.users", self.context))
        self.assertEquals(("xxJoexx", []),
                          evaluate_template("xx@(contact.first_name)xx", self.context))  # no whitespace
        self.assertEquals(('Hello "World"', []),
                          evaluate_template('@( "Hello ""World""" )', self.context))  # string with escaping
        self.assertEquals(("Hello World", []),
                          evaluate_template('@( "Hello" & " " & "World" )', self.context))  # string concatenation
        self.assertEquals(('("', []),
                          evaluate_template('@("(" & """")', self.context))  # string literals containing delimiters
        self.assertEquals(('Joe Blow and Joe Blow', []),
                          evaluate_template('@contact and @(contact)', self.context))  # old and new style
        self.assertEquals(("Joe Blow language is set to 'eng'", []),
                          evaluate_template("@contact language is set to '@contact.language'", self.context))  # language

        # test LTR and RTL mixing
        self.assertEquals(("one two three four", []),
                          evaluate_template("one @flow.english four", self.context))  # LTR var, LTR value, LTR text
        self.assertEquals(("one اثنين ثلاثة four", []),
                          evaluate_template("one @flow.arabic four", self.context))  # LTR var, RTL value, LTR text
        self.assertEquals(("واحد اثنين ثلاثة أربعة", []),
                          evaluate_template("واحد @flow.arabic أربعة", self.context))  # LTR var, RTL value, RTL text
        self.assertEquals(("واحد two three أربعة", []),
                          evaluate_template("واحد @flow.english أربعة", self.context))  # LTR var, LTR value, RTL text

        # test decimal arithmetic
        self.assertEquals(("Result: 7", []),
                          evaluate_template("Result: @(flow.users + 2)",
                                            self.context))  # var is int
        self.assertEquals(("Result: 0", []),
                          evaluate_template("Result: @(flow.count - 5)",
                                            self.context))  # var is string
        self.assertEquals(("Result: 0.5", []),
                          evaluate_template("Result: @(5 / (flow.users * 2))",
                                            self.context))  # result is decimal
        self.assertEquals(("Result: -10", []),
                          evaluate_template("Result: @(-5 - flow.users)", self.context))  # negatives

        # test date arithmetic
        self.assertEquals(("Date: 02-12-2014 09:00", []),
                          evaluate_template("Date: @(flow.joined + 1)",
                                            self.context))  # var is datetime
        self.assertEquals(("Date: 28-11-2014 09:00", []),
                          evaluate_template("Date: @(flow.started - 3)",
                                            self.context))  # var is string
        self.assertEquals(("Date: 04-07-2014", []),
                          evaluate_template("Date: @(DATE(2014, 7, 1) + 3)",
                                            self.context))  # date constructor
        self.assertEquals(("Date: 01-12-2014 11:30", []),
                          evaluate_template("Date: @(flow.joined + TIME(2, 30, 0))",
                                            self.context))  # time addition to datetime var
        self.assertEquals(("Date: 01-12-2014 06:30", []),
                          evaluate_template("Date: @(flow.joined - TIME(2, 30, 0))",
                                            self.context))  # time subtraction from string var

        # test function calls
        self.assertEquals(("Hello joe", []),
                          evaluate_template("Hello @(lower(contact.first_name))",
                                            self.context))  # use lowercase for function name
        self.assertEquals(("Hello JOE", []),
                          evaluate_template("Hello @(UPPER(contact.first_name))",
                                            self.context))  # use uppercase for function name
        self.assertEquals(("Bonjour world", []),
                          evaluate_template('@(SUBSTITUTE("Hello world", "Hello", "Bonjour"))',
                                            self.context))  # string arguments
        self.assertRegexpMatches(evaluate_template('Today is @(TODAY())', self.context)[0],
                                 'Today is \d\d-\d\d-\d\d\d\d')  # function with no args
        self.assertEquals(('3', []),
                          evaluate_template('@(LEN( 1.2 ))',
                                            self.context))  # auto decimal -> string conversion
        self.assertEquals(('16', []),
                          evaluate_template('@(LEN(flow.joined))',
                                            self.context))  # auto datetime -> string conversion
        self.assertEquals(('2', []),
                          evaluate_template('@(WORD_COUNT("abc-def", FALSE))',
                                            self.context))  # built-in variable
        self.assertEquals(('TRUE', []),
                          evaluate_template('@(OR(AND(True, flow.count = flow.users, 1), 0))',
                                            self.context))  # booleans / varargs
        self.assertEquals(('yes', []),
                          evaluate_template('@(IF(IF(flow.count > 4, "x", "y") = "x", "yes", "no"))',
                                            self.context))  # nested conditional

        # evaluation errors
        self.assertEquals(("Error: @()", ["Expression error at: )"]),
                          evaluate_template("Error: @()",
                                            self.context))  # syntax error due to empty expression
        self.assertEquals(("Error: @('2')", ["Expression error at: '"]),
                          evaluate_template("Error: @('2')",
                                            self.context))  # don't support single quote string literals
        self.assertEquals(("Error: @(2 / 0)", ["Division by zero"]),
                          evaluate_template("Error: @(2 / 0)",
                                            self.context))  # division by zero
        self.assertEquals(("Error: @(1 + flow.blank)", ["Expression could not be evaluated as decimal or date arithmetic"]),
                          evaluate_template("Error: @(1 + flow.blank)",
                                            self.context))  # string that isn't numeric
        self.assertEquals(("Well @flow.boil", ["Undefined variable: flow.boil"]),
                          evaluate_template("@flow.water_source @flow.boil",
                                            self.context))  # undefined variables
        self.assertEquals(("Hello @(XXX(1, 2))", ["Undefined function: XXX"]),
                          evaluate_template("Hello @(XXX(1, 2))",
                                            self.context))  # undefined function
        self.assertEquals(('Hello @(ABS(1, "x", TRUE))', ["Too many arguments provided for function ABS"]),
                          evaluate_template('Hello @(ABS(1, "x", TRUE))',
                                            self.context))  # wrong number of args
        self.assertEquals(('Hello @(REPT(flow.blank, -2))', ['Error calling function REPT with arguments "", -2']),
                          evaluate_template('Hello @(REPT(flow.blank, -2))',
                                            self.context))  # internal function error

    def test_evaluate_template_compat(self):
        # test old style expressions, i.e. @ and with filters
        self.assertEqual(("Hello World Joe Joe", []),
                         evaluate_template_compat("Hello World @contact.first_name @contact.first_name", self.context))
        self.assertEqual(("Hello World Joe Blow", []),
                         evaluate_template_compat("Hello World @contact", self.context))
        self.assertEqual(("Hello World: Well", []),
                         evaluate_template_compat("Hello World: @flow.water_source", self.context))
        self.assertEqual(("Hello World: ", []),
                         evaluate_template_compat("Hello World: @flow.blank", self.context))
        self.assertEqual(("Hello اثنين ثلاثة thanks", []),
                         evaluate_template_compat("Hello @flow.arabic thanks", self.context))
        self.assertEqual((' %20%3D%26%D8%A8 ', []),
                         evaluate_template_compat(' @flow.urlstuff ', self.context, True))  # url encoding enabled
        self.assertEqual(("Hello Joe", []),
                         evaluate_template_compat("Hello @contact.first_name|notthere", self.context))
        self.assertEqual(("Hello joe", []),
                         evaluate_template_compat("Hello @contact.first_name|lower_case", self.context))
        self.assertEqual(("Hello Joe", []),
                         evaluate_template_compat("Hello @contact.first_name|lower_case|capitalize", self.context))
        self.assertEqual(("Hello Joe", []),
                         evaluate_template_compat("Hello @contact|first_word", self.context))
        self.assertEqual(("Hello Blow", []),
                         evaluate_template_compat("Hello @contact|remove_first_word|title_case", self.context))
        self.assertEqual(("Hello Joe Blow", []),
                         evaluate_template_compat("Hello @contact|title_case", self.context))
        self.assertEqual(("Hello JOE", []),
                         evaluate_template_compat("Hello @contact.first_name|upper_case", self.context))
        self.assertEqual(("Hello Joe from info@example.com", []),
                         evaluate_template_compat("Hello @contact.first_name from info@example.com", self.context))
        self.assertEqual(("Joe", []),
                         evaluate_template_compat("@contact.first_name", self.context))
        self.assertEqual(("foo@nicpottier.com", []),
                         evaluate_template_compat("foo@nicpottier.com", self.context))
        self.assertEqual(("@nicpottier is on twitter", []),
                         evaluate_template_compat("@nicpottier is on twitter", self.context))

    def test_migrate_template(self):
        self.assertEqual(migrate_template("Hi @contact.name|upper_case|capitalize from @flow.chw|lower_case"),
                         "Hi @(PROPER(UPPER(contact.name))) from @(LOWER(flow.chw))")
        self.assertEqual(migrate_template('Hi @date.now|time_delta:"1"'), "Hi @(date.now + 1)")
        self.assertEqual(migrate_template('Hi @date.now|time_delta:"-3"'), "Hi @(date.now - 3)")

        self.assertEqual(migrate_template("Hi =contact.name"), "Hi @contact.name")
        self.assertEqual(migrate_template("Hi =(contact.name)"), "Hi @(contact.name)")
        self.assertEqual(migrate_template("Hi =NOW() =(TODAY())"), "Hi @(NOW()) @(TODAY())")
        self.assertEqual(migrate_template('Hi =LEN("@=")'), 'Hi @(LEN("@="))')

        # handle @ expressions embedded inside = expressions, with optional surrounding quotes
        self.assertEqual(migrate_template('=AND("Malkapur"= "@flow.stuff.category", 13 = @extra.Depar_city|upper_case)'), '@(AND("Malkapur"= flow.stuff.category, 13 = UPPER(extra.Depar_city)))')

        # don't convert unnecessarily
        self.assertEqual(migrate_template("Hi @contact.name from @flow.chw"), "Hi @contact.name from @flow.chw")

        # don't convert things that aren't expressions
        self.assertEqual(migrate_template("Reply 1=Yes, 2=No"), "Reply 1=Yes, 2=No")

    def test_get_function_listing(self):
        listing = get_function_listing()
        self.assertEqual(listing[0], {
            'signature': 'ABS(number)',
            'name': 'ABS',
            'display': "Returns the absolute value of a number"
        })

    def test_build_function_signature(self):
        self.assertEqual('ABS()',
                         _build_function_signature(dict(name='ABS',
                                                        params=[])))

        self.assertEqual('ABS(number)',
                         _build_function_signature(dict(name='ABS',
                                                        params=[dict(optional=False,
                                                                     name='number',
                                                                     vararg=False)])))

        self.assertEqual('ABS(number, ...)',
                         _build_function_signature(dict(name='ABS',
                                                        params=[dict(optional=False,
                                                                     name='number',
                                                                     vararg=True)])))

        self.assertEqual('ABS([number])',
                         _build_function_signature(dict(name='ABS',
                                                        params=[dict(optional=True,
                                                                     name='number',
                                                                     vararg=False)])))

        self.assertEqual('ABS([number], ...)',
                         _build_function_signature(dict(name='ABS',
                                                        params=[dict(optional=True,
                                                                     name='number',
                                                                     vararg=True)])))

        self.assertEqual('MOD(number, divisor)',
                         _build_function_signature(dict(name='MOD',
                                                        params=[dict(optional=False,
                                                                     name='number',
                                                                     vararg=False),
                                                                dict(optional=False,
                                                                     name='divisor',
                                                                     vararg=False)])))

        self.assertEqual('MOD(number, ..., divisor)',
                         _build_function_signature(dict(name='MOD',
                                                        params=[dict(optional=False,
                                                                     name='number',
                                                                     vararg=True),
                                                                dict(optional=False,
                                                                     name='divisor',
                                                                     vararg=False)])))

    def test_percentage(self):
        self.assertEquals(0, percentage(0, 100))
        self.assertEquals(0, percentage(0, 0))
        self.assertEquals(0, percentage(100, 0))
        self.assertEquals(75, percentage(75, 100))
        self.assertEquals(76, percentage(759, 1000))


class GSM7Test(TembaTest):

    def test_is_gsm7(self):
        self.assertTrue(is_gsm7("Hello World! {} <>"))
        self.assertFalse(is_gsm7("No capital accented È!"))
        self.assertFalse(is_gsm7("No unicode. ☺"))

        replaced = replace_non_gsm7_accents("No capital accented È!")
        self.assertEquals("No capital accented E!", replaced)
        self.assertTrue(is_gsm7(replaced))

        replaced = replace_non_gsm7_accents("No crazy “word” quotes.")
        self.assertEquals('No crazy "word" quotes.', replaced)
        self.assertTrue(is_gsm7(replaced))

        # non breaking space
        replaced = replace_non_gsm7_accents("Pour chercher du boulot, comment fais-tu ?")
        self.assertEquals('Pour chercher du boulot, comment fais-tu ?', replaced)
        self.assertTrue(is_gsm7(replaced))


class ChunkTest(TembaTest):

    def test_chunking(self):
        curr = 0
        for chunk in chunk_list(six.moves.xrange(100), 7):
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


class ExportTest(TembaTest):
    def setUp(self):
        super(ExportTest, self).setUp()

        self.group = self.create_group("New contacts", [])
        self.task = ExportContactsTask.objects.create(org=self.org, group=self.group,
                                                      created_by=self.admin, modified_by=self.admin)

    def test_prepare_value(self):
        self.assertEqual(self.task.prepare_value(None), '')
        self.assertEqual(self.task.prepare_value("=()"), "'=()")  # escape formulas
        self.assertEqual(self.task.prepare_value(123), '123')

        dt = pytz.timezone("Africa/Nairobi").localize(datetime.datetime(2017, 2, 7, 15, 41, 23, 123456))
        self.assertEqual(self.task.prepare_value(dt), datetime.datetime(2017, 2, 7, 14, 41, 23, 0))

    def test_task_status(self):
        self.assertEqual(self.task.status, ExportContactsTask.STATUS_PENDING)

        self.task.perform()

        self.assertEqual(self.task.status, ExportContactsTask.STATUS_COMPLETE)

        task2 = ExportContactsTask.objects.create(org=self.org, group=self.group,
                                                  created_by=self.admin, modified_by=self.admin)

        # if task throws exception, will be marked as failed
        with patch.object(task2, 'write_export') as mock_write_export:
            mock_write_export.side_effect = ValueError("Problem!")

            task2.perform()

            self.assertEqual(task2.status, ExportContactsTask.STATUS_FAILED)

    @patch('temba.utils.export.BaseExportTask.MAX_EXCEL_COLS', new_callable=PropertyMock)
    def test_tableexporter_csv(self, mock_max_cols):
        test_max_cols = 255
        mock_max_cols.return_value = test_max_cols

        # tests writing a CSV, that is a file that has more than 255 columns
        cols = []
        for i in range(test_max_cols + 1):
            cols.append("Column %d" % i)

        # create a new exporter
        exporter = TableExporter(self.task, "test", cols)

        # should be CSV because we have too many columns
        self.assertTrue(exporter.is_csv)

        # write some rows
        values = []
        for i in range(test_max_cols + 1):
            values.append("Value %d" % i)

        exporter.write_row(values)
        exporter.write_row(values)

        # ok, let's check the result now
        temp_file, file_ext = exporter.save_file()

        with open(temp_file.name, 'rb') as csvfile:
            import csv
            reader = csv.reader(csvfile)

            for idx, row in enumerate(reader):
                if idx == 0:
                    self.assertEquals(cols, row)
                else:
                    self.assertEquals(values, row)

            # should only be three rows
            self.assertEquals(2, idx)

    @patch('temba.utils.export.BaseExportTask.MAX_EXCEL_ROWS', new_callable=PropertyMock)
    def test_tableexporter_xls(self, mock_max_rows):
        test_max_rows = 1500
        mock_max_rows.return_value = test_max_rows

        cols = []
        for i in range(32):
            cols.append("Column %d" % i)

        exporter = TableExporter(self.task, "test", cols)

        # should be an XLS file
        self.assertFalse(exporter.is_csv)

        values = []
        for i in range(32):
            values.append("Value %d" % i)

        # write out 1050000 rows, that'll make two sheets
        for i in range(test_max_rows + 200):
            exporter.write_row(values)

        temp_file, file_ext = exporter.save_file()
        workbook = load_workbook(filename=temp_file.name)

        self.assertEquals(2, len(workbook.worksheets))

        # check our sheet 1 values
        sheet1 = workbook.worksheets[0]

        rows = tuple(sheet1.rows)

        self.assertEquals(cols, [cell.value for cell in rows[0]])
        self.assertEquals(values, [cell.value for cell in rows[1]])

        self.assertEquals(test_max_rows, len(list(sheet1.rows)))
        self.assertEquals(32, len(list(sheet1.columns)))

        sheet2 = workbook.worksheets[1]
        rows = tuple(sheet2.rows)
        self.assertEquals(cols, [cell.value for cell in rows[0]])
        self.assertEquals(values, [cell.value for cell in rows[1]])

        self.assertEquals(200 + 2, len(list(sheet2.rows)))
        self.assertEquals(32, len(list(sheet2.columns)))


class CurrencyTest(TembaTest):

    def test_currencies(self):

        self.assertEqual(currency_for_country('US').alpha_3, 'USD')
        self.assertEqual(currency_for_country('EC').alpha_3, 'USD')
        self.assertEqual(currency_for_country('FR').alpha_3, 'EUR')
        self.assertEqual(currency_for_country('DE').alpha_3, 'EUR')
        self.assertEqual(currency_for_country('YE').alpha_3, 'YER')
        self.assertEqual(currency_for_country('AF').alpha_3, 'AFN')

        for country in list(pycountry.countries):
            try:
                currency_for_country(country.alpha_2)
            except KeyError:
                self.fail('Country missing currency: %s' % country)


class VoiceXMLTest(TembaTest):

    def test_context_managers(self):
        response = voicexml.VXMLResponse()
        self.assertEqual(response, response.__enter__())
        self.assertFalse(response.__exit__(None, None, None))

    def test_response(self):
        response = voicexml.VXMLResponse()
        self.assertEqual(response.document, '<?xml version="1.0" encoding="UTF-8"?><vxml version = "2.1"><form>')
        self.assertEqual(six.text_type(response),
                         '<?xml version="1.0" encoding="UTF-8"?><vxml version = "2.1"><form></form></vxml>')

        response.document += '</form></vxml>'
        self.assertEqual(six.text_type(response),
                         '<?xml version="1.0" encoding="UTF-8"?><vxml version = "2.1"><form></form></vxml>')

    def test_join(self):
        response1 = voicexml.VXMLResponse()
        response2 = voicexml.VXMLResponse()

        response1.document += 'Allo '
        response2.document += 'Hey '

        # the content of response2 should be prepended before the content of response1
        self.assertEqual(six.text_type(response1.join(response2)),
                         '<?xml version="1.0" encoding="UTF-8"?><vxml version = "2.1"><form>Hey Allo </form></vxml>')

    def test_say(self):
        response = voicexml.VXMLResponse()
        response.say('Hello')

        self.assertEqual(six.text_type(response),
                         '<?xml version="1.0" encoding="UTF-8"?><vxml version = "2.1"><form>'
                         '<block><prompt>Hello</prompt></block></form></vxml>')

    def test_play(self):
        response = voicexml.VXMLResponse()

        with self.assertRaises(VoiceXMLException):
            response.play()

        response.play(digits='123')
        self.assertEqual(six.text_type(response),
                         '<?xml version="1.0" encoding="UTF-8"?><vxml version = "2.1"><form>'
                         '<block><prompt>123</prompt></block></form></vxml>')

        response = voicexml.VXMLResponse()
        response.play(url='http://example.com/audio.wav')

        self.assertEqual(six.text_type(response),
                         '<?xml version="1.0" encoding="UTF-8"?><vxml version = "2.1"><form>'
                         '<block><prompt><audio src="http://example.com/audio.wav" /></prompt></block></form></vxml>')

    def test_pause(self):
        response = voicexml.VXMLResponse()

        response.pause()
        self.assertEqual(six.text_type(response),
                         '<?xml version="1.0" encoding="UTF-8"?><vxml version = "2.1"><form>'
                         '<block><prompt><break /></prompt></block></form></vxml>')

        response = voicexml.VXMLResponse()

        response.pause(length=40)
        self.assertEqual(six.text_type(response),
                         '<?xml version="1.0" encoding="UTF-8"?><vxml version = "2.1"><form>'
                         '<block><prompt><break time="40s"/></prompt></block></form></vxml>')

    def test_redirect(self):
        response = voicexml.VXMLResponse()
        response.redirect('http://example.com/')

        self.assertEqual(six.text_type(response),
                         '<?xml version="1.0" encoding="UTF-8"?><vxml version = "2.1"><form>'
                         '<subdialog src="http://example.com/" ></subdialog></form></vxml>')

    def test_hangup(self):
        response = voicexml.VXMLResponse()
        response.hangup()

        self.assertEqual(six.text_type(response),
                         '<?xml version="1.0" encoding="UTF-8"?><vxml version = "2.1"><form><exit /></form></vxml>')

    def test_reject(self):
        response = voicexml.VXMLResponse()
        response.reject(reason='some')

        self.assertEqual(six.text_type(response),
                         '<?xml version="1.0" encoding="UTF-8"?><vxml version = "2.1"><form><exit /></form></vxml>')

    def test_gather(self):
        response = voicexml.VXMLResponse()
        response.gather()

        self.assertEqual(six.text_type(response),
                         '<?xml version="1.0" encoding="UTF-8"?><vxml version = "2.1"><form>'
                         '<field name="Digits"><grammar termchar="#" src="builtin:dtmf/digits" />'
                         '</field></form></vxml>')

        response = voicexml.VXMLResponse()
        response.gather(action='http://example.com')

        self.assertEqual(six.text_type(response),
                         '<?xml version="1.0" encoding="UTF-8"?><vxml version = "2.1"><form>'
                         '<field name="Digits"><grammar termchar="#" src="builtin:dtmf/digits" />'
                         '<nomatch><submit next="http://example.com?empty=1" method="post" /></nomatch></field>'
                         '<filled><submit next="http://example.com" method="post" /></filled></form></vxml>')

        response = voicexml.VXMLResponse()
        response.gather(action='http://example.com', numDigits=1, timeout=45, finishOnKey='*')

        self.assertEqual(six.text_type(response),
                         '<?xml version="1.0" encoding="UTF-8"?><vxml version = "2.1"><form>'
                         '<field name="Digits"><grammar termtimeout="45s" timeout="45s" termchar="*" '
                         'src="builtin:dtmf/digits?minlength=1;maxlength=1" />'
                         '<nomatch><submit next="http://example.com?empty=1" method="post" /></nomatch></field>'
                         '<filled><submit next="http://example.com" method="post" /></filled></form></vxml>')

    def test_record(self):
        response = voicexml.VXMLResponse()
        response.record()

        self.assertEqual(six.text_type(response),
                         '<?xml version="1.0" encoding="UTF-8"?><vxml version = "2.1"><form>'
                         '<record name="UserRecording" beep="true" finalsilence="4000ms" '
                         'dtmfterm="true" type="audio/x-wav"></record></form></vxml>')

        response = voicexml.VXMLResponse()
        response.record(action="http://example.com", method="post", maxLength=60)

        self.assertEqual(six.text_type(response),
                         '<?xml version="1.0" encoding="UTF-8"?><vxml version = "2.1"><form>'
                         '<record name="UserRecording" beep="true" maxtime="60s" finalsilence="4000ms" '
                         'dtmfterm="true" type="audio/x-wav">'
                         '<filled><submit next="http://example.com" method="post" '
                         'enctype="multipart/form-data" /></filled></record></form></vxml>')


class NCCOTest(TembaTest):

    def test_context_managers(self):
        response = NCCOResponse()
        self.assertEqual(response, response.__enter__())
        self.assertFalse(response.__exit__(None, None, None))

    def test_response(self):
        response = NCCOResponse()
        self.assertEqual(response.document, [])
        self.assertEqual(json.loads(six.text_type(response)), [])

    def test_join(self):
        response1 = NCCOResponse()
        response2 = NCCOResponse()

        response1.document.append(dict(action='foo'))
        response2.document.append(dict(action='bar'))

        # the content of response2 should be prepended before the content of response1
        self.assertEqual(json.loads(six.text_type(response1.join(response2))), [dict(action='bar'), dict(action='foo')])

    def test_say(self):
        response = NCCOResponse()
        response.say('Hello')

        self.assertEqual(json.loads(six.text_type(response)), [dict(action='talk', text='Hello', bargeIn=False)])

    def test_play(self):
        response = NCCOResponse()

        with self.assertRaises(NCCOException):
            response.play()

        response.play(digits='123')
        self.assertEqual(json.loads(six.text_type(response)), [dict(action='talk', text='123', bargeIn=False)])

        response = NCCOResponse()
        response.play(url='http://example.com/audio.wav')

        self.assertEqual(json.loads(six.text_type(response)), [dict(action='stream', bargeIn=False,
                                                                    streamUrl=['http://example.com/audio.wav'])])

        response = NCCOResponse()
        response.play(url='http://example.com/audio.wav', digits='123')

        self.assertEqual(json.loads(six.text_type(response)), [dict(action='stream', bargeIn=False,
                                                                    streamUrl=['http://example.com/audio.wav'])])

    def test_bargeIn(self):
        response = NCCOResponse()
        response.say('Hello')
        response.redirect('http://example.com/')

        self.assertEqual(json.loads(six.text_type(response)), [dict(action='talk', text='Hello', bargeIn=True),
                                                               dict(action='input', maxDigits=1, timeOut=1,
                                                                    eventUrl=[
                                                                        "%s?input_redirect=1" % 'http://example.com/'
                                                                    ])])

        response = NCCOResponse()
        response.say('Hello')
        response.redirect('http://example.com/')
        response.say('Goodbye')

        self.assertEqual(json.loads(six.text_type(response)), [dict(action='talk', text='Hello', bargeIn=True),
                                                               dict(action='input', maxDigits=1, timeOut=1,
                                                                    eventUrl=[
                                                                        "%s?input_redirect=1" % 'http://example.com/']),
                                                               dict(action='talk', text='Goodbye', bargeIn=False)])

        response = NCCOResponse()
        response.say('Hello')
        response.redirect('http://example.com/')
        response.say('Please make a recording')
        response.record(action="http://example.com", method="post", maxLength=60)
        response.say('Thanks')
        response.say('Allo')
        response.say('Cool')
        response.redirect('http://example.com/')
        response.say('Bye')

        self.assertEqual(json.loads(six.text_type(response)), [dict(action='talk', text='Hello', bargeIn=True),
                                                               dict(action='input', maxDigits=1, timeOut=1,
                                                                    eventUrl=[
                                                                        "%s?input_redirect=1" % 'http://example.com/']),
                                                               dict(action='talk', text='Please make a recording',
                                                                    bargeIn=False),
                                                               dict(format='wav', eventMethod='post',
                                                                    eventUrl=['http://example.com'],
                                                                    endOnSilence=4, timeOut=60, endOnKey='#',
                                                                    action='record', beepStart=True),
                                                               dict(action='input', maxDigits=1, timeOut=1,
                                                                    eventUrl=[
                                                                        "%s?save_media=1" % "http://example.com"]),
                                                               dict(action='talk', text='Thanks', bargeIn=False),
                                                               dict(action='talk', text='Allo', bargeIn=False),
                                                               dict(action='talk', text='Cool', bargeIn=True),
                                                               dict(action='input', maxDigits=1, timeOut=1,
                                                                    eventUrl=[
                                                                        "%s?input_redirect=1" % 'http://example.com/']),
                                                               dict(action='talk', text='Bye', bargeIn=False)])

        response = NCCOResponse()
        response.play(url='http://example.com/audio.wav')
        response.redirect('http://example.com/')
        response.say('Goodbye')

        self.assertEqual(json.loads(six.text_type(response)), [dict(action='stream', bargeIn=True,
                                                                    streamUrl=['http://example.com/audio.wav']),
                                                               dict(action='input', maxDigits=1, timeOut=1,
                                                                    eventUrl=[
                                                                        "%s?input_redirect=1" % 'http://example.com/']),
                                                               dict(action='talk', text='Goodbye', bargeIn=False)])

    def test_pause(self):
        response = NCCOResponse()
        response.pause()

    def test_redirect(self):
        response = NCCOResponse()
        response.redirect('http://example.com/')

        self.assertEqual(json.loads(six.text_type(response)), [dict(action='input', maxDigits=1, timeOut=1,
                                                                    eventUrl=[
                                                                        "%s?input_redirect=1" % 'http://example.com/'
                                                                    ])])

        response = NCCOResponse()
        response.redirect('http://example.com/?param=12')

        self.assertEqual(json.loads(six.text_type(response)), [dict(action='input', maxDigits=1, timeOut=1,
                                                                    eventUrl=[
                                                                        'http://example.com/?param=12&input_redirect=1'
                                                                    ])])

    def test_hangup(self):
        response = NCCOResponse()
        response.hangup()

    def test_reject(self):
        response = NCCOResponse()
        response.reject()

    def test_gather(self):
        response = NCCOResponse()
        response.gather()

        self.assertEqual(json.loads(six.text_type(response)), [dict(action='input', submitOnHash=True)])

        response = NCCOResponse()
        response.gather(action='http://example.com')

        self.assertEqual(json.loads(six.text_type(response)), [dict(eventMethod='post', action='input',
                                                                    submitOnHash=True,
                                                                    eventUrl=['http://example.com'])])

        response = NCCOResponse()
        response.gather(action='http://example.com', numDigits=1, timeout=45, finishOnKey='*')

        self.assertEqual(json.loads(six.text_type(response)), [dict(maxDigits=1, eventMethod='post', action='input',
                                                                    submitOnHash=False,
                                                                    eventUrl=['http://example.com'],
                                                                    timeOut=45)])

    def test_record(self):
        response = NCCOResponse()
        response.record()

        self.assertEqual(json.loads(six.text_type(response)), [dict(format='wav', endOnSilence=4, beepStart=True,
                                                                    action='record', endOnKey='#'),
                                                               dict(action='input', maxDigits=1, timeOut=1,
                                                                    eventUrl=["None?save_media=1"])
                                                               ])

        response = NCCOResponse()
        response.record(action="http://example.com", method="post", maxLength=60)

        self.assertEqual(json.loads(six.text_type(response)), [dict(format='wav', eventMethod='post',
                                                                    eventUrl=['http://example.com'],
                                                                    endOnSilence=4, timeOut=60, endOnKey='#',
                                                                    action='record', beepStart=True),
                                                               dict(action='input', maxDigits=1, timeOut=1,
                                                                    eventUrl=["%s?save_media=1" % "http://example.com"])
                                                               ])
        response = NCCOResponse()
        response.record(action="http://example.com?param=12", method="post", maxLength=60)

        self.assertEqual(json.loads(six.text_type(response)), [dict(format='wav', eventMethod='post',
                                                                    eventUrl=['http://example.com?param=12'],
                                                                    endOnSilence=4, timeOut=60, endOnKey='#',
                                                                    action='record', beepStart=True),
                                                               dict(action='input', maxDigits=1, timeOut=1,
                                                                    eventUrl=["http://example.com?param=12&save_media=1"])
                                                               ])


class MiddlewareTest(TembaTest):

    def test_org_header(self):
        response = self.client.get(reverse('public.public_index'))
        self.assertFalse(response.has_header('X-Temba-Org'))

        self.login(self.superuser)

        response = self.client.get(reverse('public.public_index'))
        self.assertFalse(response.has_header('X-Temba-Org'))

        self.login(self.admin)

        response = self.client.get(reverse('public.public_index'))
        self.assertEqual(response['X-Temba-Org'], six.text_type(self.org.id))

    def test_branding(self):
        response = self.client.get(reverse('public.public_index'))
        self.assertEqual(response.context['request'].branding, settings.BRANDING['rapidpro.io'])

    def test_flow_simulation(self):
        Contact.set_simulation(True)

        self.client.get(reverse('public.public_index'))

        self.assertFalse(Contact.get_simulation())

    def test_activate_language(self):
        self.assertContains(self.client.get(reverse('public.public_index')), "Create Account")

        self.login(self.admin)

        self.assertContains(self.client.get(reverse('public.public_index')), "Create Account")
        self.assertContains(self.client.get(reverse('contacts.contact_list')), "Import Contacts")

        UserSettings.objects.filter(user=self.admin).update(language='fr')

        self.assertContains(self.client.get(reverse('contacts.contact_list')), "Importer des contacts")


class ProfilerTest(TembaTest):
    @time_monitor(threshold=50)
    def foo(self, bar):
        time.sleep(bar / 1000.0)

    @patch('logging.Logger.error')
    def test_time_monitor(self, mock_error):
        self.foo(1)
        self.assertEqual(len(mock_error.mock_calls), 0)

        self.foo(51)
        self.assertEqual(len(mock_error.mock_calls), 1)


class MakeTestDBTest(SimpleTestCase):
    """
    This command can't be run in a transaction so we have to manually ensure all data is deleted on completion
    """
    allow_database_queries = True

    def tearDown(self):
        Msg.objects.all().delete()
        FlowRun.objects.all().delete()
        SystemLabelCount.objects.all().delete()
        Org.objects.all().delete()
        User.objects.all().delete()
        Group.objects.all().delete()
        AdminBoundary.objects.all().delete()

    def test_command(self):
        call_command('test_db', 'generate', num_orgs=3, num_contacts=30, seed=1234)

        org1, org2, org3 = tuple(Org.objects.order_by('id'))

        def assertOrgCounts(qs, counts):
            self.assertEqual([qs.filter(org=o).count() for o in (org1, org2, org3)], counts)

        print(User.objects.all())
        self.assertEqual(User.objects.exclude(username__in=["AnonymousUser", "root", "rapidpro_flow", "temba_flow"]).count(), 12)
        assertOrgCounts(ContactField.objects.all(), [6, 6, 6])
        assertOrgCounts(ContactGroup.user_groups.all(), [10, 10, 10])
        assertOrgCounts(Contact.objects.filter(is_test=True), [4, 4, 4])  # 1 for each user
        assertOrgCounts(Contact.objects.filter(is_test=False), [17, 7, 6])

        org_1_all_contacts = ContactGroup.system_groups.get(org=org1, name="All Contacts")

        self.assertEqual(org_1_all_contacts.contacts.count(), 17)
        self.assertEqual(list(ContactGroupCount.objects.filter(group=org_1_all_contacts).values_list('count')), [(17,)])

        # same seed should generate objects with same UUIDs
        self.assertEqual(ContactGroup.user_groups.order_by('id').first().uuid, 'ea60312b-25f5-47a0-cac7-4fe0c2064f3e')

        # check generate can't be run again on a now non-empty database
        with self.assertRaises(CommandError):
            call_command('test_db', 'generate', num_orgs=3, num_contacts=30, seed=1234)

        # but simulate can
        call_command('test_db', 'simulate', num_runs=2)
