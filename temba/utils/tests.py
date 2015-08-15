# -*- coding: utf-8 -*-

from __future__ import unicode_literals

import json
import pytz

from datetime import date, datetime, time
from django.conf import settings
from django.core.paginator import Paginator
from mock import patch
from redis_cache import get_redis_connection
from temba.contacts.models import Contact
from temba.tests import TembaTest
from .cache import get_cacheable_result, get_cacheable_attr, incrby_existing
from .queues import pop_task, push_task, HIGH_PRIORITY, LOW_PRIORITY
from .parser import EvaluationError, EvaluationContext, evaluate_template, evaluate_expression, set_evaluation_context, get_function_listing
from .parser_functions import *
from . import format_decimal, slugify_with, str_to_datetime, str_to_time, truncate, random_string, non_atomic_when_eager
from . import PageableQuery, json_to_dict, dict_to_struct, datetime_to_ms, ms_to_datetime, dict_to_json, str_to_bool
from . import percentage, datetime_to_json_date, json_date_to_datetime, timezone_to_country_code, non_atomic_gets


class InitTest(TembaTest):

    def test_datetime_to_ms(self):
        d1 = datetime(2014, 1, 2, 3, 4, 5, tzinfo=pytz.utc)
        self.assertEqual(datetime_to_ms(d1), 1388631845000)  # from http://unixtimestamp.50x.eu
        self.assertEqual(ms_to_datetime(1388631845000), d1)

        tz = pytz.timezone("Africa/Kigali")
        d2 = tz.localize(datetime(2014, 1, 2, 3, 4, 5))
        self.assertEqual(datetime_to_ms(d2), 1388624645000)
        self.assertEqual(ms_to_datetime(1388624645000), d2.astimezone(pytz.utc))

    def test_datetime_to_json_date(self):
        d1 = datetime(2014, 1, 2, 3, 4, 5, tzinfo=pytz.utc)
        self.assertEqual(datetime_to_json_date(d1), '2014-01-02T03:04:05.000Z')
        self.assertEqual(json_date_to_datetime('2014-01-02T03:04:05.000Z'), d1)
        self.assertEqual(json_date_to_datetime('2014-01-02T03:04:05.000'), d1)

        tz = pytz.timezone("Africa/Kigali")
        d2 = tz.localize(datetime(2014, 1, 2, 3, 4, 5))
        self.assertEqual(datetime_to_json_date(d2), '2014-01-02T01:04:05.000Z')
        self.assertEqual(json_date_to_datetime('2014-01-02T01:04:05.000Z'), d2.astimezone(pytz.utc))
        self.assertEqual(json_date_to_datetime('2014-01-02T01:04:05.000'), d2.astimezone(pytz.utc))

    def test_str_to_datetime(self):
        tz = pytz.timezone('Asia/Kabul')
        with patch.object(timezone, 'now', return_value=tz.localize(datetime(2014, 1, 2, 3, 4, 5, 6))):
            self.assertIsNone(str_to_datetime(None, tz))  # none
            self.assertIsNone(str_to_datetime('', tz))  # empty string
            self.assertIsNone(str_to_datetime('xxx', tz))  # unparseable string
            self.assertIsNone(str_to_datetime('xxx', tz, fill_time=False))  # unparseable string
            self.assertEqual(tz.localize(datetime(2013, 2, 1, 3, 4, 5, 6)),
                             str_to_datetime('01-02-2013', tz, dayfirst=True))  # day first
            self.assertEqual(tz.localize(datetime(2013, 1, 2, 3, 4, 5, 6)),
                             str_to_datetime('01-02-2013', tz, dayfirst=False))  # month first
            self.assertEqual(tz.localize(datetime(2013, 1, 31, 3, 4, 5, 6)),
                             str_to_datetime('01-31-2013', tz, dayfirst=True))  # impossible as day first
            self.assertEqual(tz.localize(datetime(2013, 2, 1, 7, 8, 5, 6)),
                             str_to_datetime('01-02-2013 07:08', tz, dayfirst=True))  # hour and minute provided
            self.assertEqual(tz.localize(datetime(2013, 2, 1, 7, 8, 9, 100000)),
                             str_to_datetime('01-02-2013 07:08:09.100000', tz, dayfirst=True))  # complete time provided
            self.assertEqual(tz.localize(datetime(2013, 2, 1, 0, 0, 0, 0)),
                             str_to_datetime('01-02-2013', tz, dayfirst=True, fill_time=False))  # no time filling

    def test_str_to_time(self):
        tz = pytz.timezone('Asia/Kabul')
        with patch.object(timezone, 'now', return_value=tz.localize(datetime(2014, 1, 2, 3, 4, 5, 6))):
            self.assertEqual(time(3, 4), str_to_time('03:04'))  # zero padded
            self.assertEqual(time(3, 4), str_to_time('3:4'))  # not zero padded
            self.assertEqual(time(3, 4), str_to_time('01-02-2013 03:04'))  # with date
            self.assertEqual(time(15, 4), str_to_time('3:04 PM'))  # as PM

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

    def test_non_atomic_when_eager(self):
        settings.CELERY_ALWAYS_EAGER = False

        @non_atomic_when_eager
        def dispatch_func1(*args, **kwargs):
            return args[0] + kwargs['arg2']

        settings.CELERY_ALWAYS_EAGER = True

        @non_atomic_when_eager
        def dispatch_func2(*args, **kwargs):
            return args[0] + kwargs['arg2']

        self.assertFalse(hasattr(dispatch_func1, '_non_atomic_requests'))
        self.assertIsNotNone(dispatch_func2._non_atomic_requests)

        # check that both functions call correctly
        self.assertEqual(dispatch_func1(1, arg2=2), 3)
        self.assertEqual(dispatch_func2(1, arg2=2), 3)

    def test_non_atomic_gets(self):
        @non_atomic_gets
        def dispatch_func(*args, **kwargs):
            return args[0] + kwargs['arg2']

        self.assertTrue(hasattr(dispatch_func, '_non_atomic_gets'))

        # check that function calls correctly
        self.assertEqual(dispatch_func(1, arg2=2), 3)

    def test_timezone_country_code(self):
        self.assertEqual('RW', timezone_to_country_code('Africa/Kigali'))
        self.assertEqual('US', timezone_to_country_code('America/Chicago'))
        self.assertEqual('US', timezone_to_country_code('US/Pacific'))
        # GMT and UTC give empty
        self.assertEqual('', timezone_to_country_code('GMT'))

        # any invalid timezones should return ""
        self.assertEqual('', timezone_to_country_code('Nyamirambo'))


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
        r.setex('foo', 10, 100)
        r.set('bar', 20)

        incrby_existing('foo', 3, r)  # positive delta
        self.assertEqual(r.get('foo'), '13')
        self.assertTrue(r.ttl('foo') > 0)

        incrby_existing('foo', -1, r)  # negative delta
        self.assertEqual(r.get('foo'), '12')
        self.assertTrue(r.ttl('foo') > 0)

        r.setex('foo', 0, 100)
        incrby_existing('foo', 5, r)  # zero val key
        self.assertEqual(r.get('foo'), '5')
        self.assertTrue(r.ttl('foo') > 0)

        incrby_existing('bar', 5, r)  # persistent key
        self.assertEqual(r.get('bar'), '25')
        self.assertTrue(r.ttl('bar') < 0)

        incrby_existing('xxx', -2, r)  # non-existent key
        self.assertIsNone(r.get('xxx'))


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
        args1 = dict(task=1)

        # basic push and pop
        push_task(self.org, None, 'test', args1)
        self.assertEquals(args1, pop_task('test'))

        self.assertFalse(pop_task('test'))

        # ok, try pushing and popping multiple on now
        args2 = dict(task=2)

        push_task(self.org, None, 'test', args1)
        push_task(self.org, None, 'test', args2)

        # should come back in order of insertion
        self.assertEquals(args1, pop_task('test'))
        self.assertEquals(args2, pop_task('test'))

        self.assertFalse(pop_task('test'))

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
        self.assertEquals(args3, pop_task('test'))
        self.assertEquals(args1, pop_task('test'))
        self.assertEquals(args2, pop_task('test'))
        self.assertEquals(args4, pop_task('test'))

        self.assertFalse(pop_task('test'))

    def test_org_queuing(self):
        self.create_secondary_org()

        args = [dict(task=i) for i in range(6)]

        push_task(self.org, None, 'test', args[2], LOW_PRIORITY)
        push_task(self.org, None, 'test', args[1])
        push_task(self.org, None, 'test', args[0], HIGH_PRIORITY)


        push_task(self.org2, None, 'test', args[4])
        push_task(self.org2, None, 'test', args[3], HIGH_PRIORITY)
        push_task(self.org2, None, 'test', args[5], LOW_PRIORITY)

        # order isn't guaranteed except per org when popping these off
        curr1, curr2 = 0, 3

        for i in range(6):
            task = pop_task('test')['task']

            if task < 3:
                self.assertEquals(curr1, task)
                curr1 += 1
            else:
                self.assertEquals(curr2, task)
                curr2 += 1

        self.assertFalse(pop_task('test'))


class ParserTest(TembaTest):

    def test_evaluate_template(self):
        contact = self.create_contact("Joe Blow", "123")
        contact.language = u'eng'
        contact.save()

        variables = dict()
        variables['contact'] = contact.build_message_context()
        variables['flow'] = dict(water_source="Well",     # key with underscore
                                 blank="",                # blank string
                                 arabic="اثنين ثلاثة",    # RTL chars
                                 english="two three",     # LTR chars
                                 urlstuff=' =&\u0628',    # stuff that needs URL encoding
                                 users=5,                 # numeric as int
                                 count="5",               # numeric as string
                                 average=2.5,             # numeric as float
                                 joined=datetime(2014, 12, 1, 9, 0, 0, 0, timezone.utc),  # date as datetime
                                 started="1/12/14 9:00")  # date as string
        variables['a'] = '!'  # single char var
        
        context = EvaluationContext(variables, dict(tz=timezone.utc, dayfirst=True))

        self.assertEquals(("Hello World", []), evaluate_template('Hello World', context))  # no expressions
        self.assertEquals(("Hello = Well 5 !", []),
                          evaluate_template("Hello = =(flow.water_source) =flow.users =a", context))
        self.assertEquals(("xxJoexx", []),
                          evaluate_template("xx=(contact.first_name)xx", context))  # no whitespace
        self.assertEquals(('Hello "World"', []),
                          evaluate_template('=( "Hello ""World""" )', context))  # string with escaping
        self.assertEquals(("Hello World", []),
                          evaluate_template('=( "Hello" & " " & "World" )',  context))  # string concatenation
        self.assertEquals(('("', []),
                          evaluate_template('=("(" & """")',  context))  # string literals containing delimiters
        self.assertEquals(('Joe Blow and Joe Blow', []),
                          evaluate_template('@contact and =(contact)',  context))  # old and new style
        self.assertEquals(("Joe Blow language is set to 'eng'", []),
                          evaluate_template("@contact language is set to '@contact.language'", context))  # language

        # test LTR and RTL mixing
        self.assertEquals(("one two three four", []),
                          evaluate_template("one =flow.english four", context))  # LTR var, LTR value, LTR text
        self.assertEquals(("one اثنين ثلاثة four", []),
                          evaluate_template("one =flow.arabic four", context))  # LTR var, RTL value, LTR text
        self.assertEquals(("واحد اثنين ثلاثة أربعة", []),
                          evaluate_template("واحد =flow.arabic أربعة",  context))  # LTR var, RTL value, RTL text
        self.assertEquals(("واحد two three أربعة", []),
                          evaluate_template("واحد =flow.english أربعة",  context))  # LTR var, LTR value, RTL text

        # test decimal arithmetic
        self.assertEquals(("Result: 7", []),
                          evaluate_template("Result: =(flow.users + 2)",
                                            context))  # var is int
        self.assertEquals(("Result: 0", []),
                          evaluate_template("Result: =(flow.count - 5)",
                                            context))  # var is string
        self.assertEquals(("Result: 0.5", []),
                          evaluate_template("Result: =(5 / (flow.users * 2))",
                                            context))  # result is decimal
        self.assertEquals(("Result: -10", []),
                          evaluate_template("Result: =(-5 - flow.users)", context))  # negatives

        # test date arithmetic
        self.assertEquals(("Date: 02-12-2014 09:00", []),
                          evaluate_template("Date: =(flow.joined + 1)",
                                            context))  # var is datetime
        self.assertEquals(("Date: 28-11-2014 09:00", []),
                          evaluate_template("Date: =(flow.started - 3)",
                                            context))  # var is string
        self.assertEquals(("Date: 04-07-2014", []),
                          evaluate_template("Date: =(DATE(2014, 7, 1) + 3)",
                                            context))  # date constructor
        self.assertEquals(("Date: 01-12-2014 11:30", []),
                          evaluate_template("Date: =(flow.joined + TIME(2, 30, 0))",
                                            context))  # time addition to datetime var
        self.assertEquals(("Date: 01-12-2014 06:30", []),
                          evaluate_template("Date: =(flow.joined - TIME(2, 30, 0))",
                                            context))  # time subtraction from string var

        # test function calls
        self.assertEquals(("Hello 1 1", []),
                          evaluate_template("Hello =ABS(-1) =(ABS(-1))",
                                            context))  # with and without outer parentheses
        self.assertEquals(("Hello joe", []),
                          evaluate_template("Hello =lower(contact.first_name)",
                                            context))  # use lowercase for function name
        self.assertEquals(("Hello JOE", []),
                          evaluate_template("Hello =UPPER(contact.first_name)",
                                            context))  # use uppercase for function name
        self.assertEquals(("Bonjour world", []),
                          evaluate_template('=SUBSTITUTE("Hello world", "Hello", "Bonjour")',
                                            context))  # string arguments
        self.assertRegexpMatches(evaluate_template('Today is =TODAY()', context)[0],
                                 'Today is \d\d-\d\d-\d\d\d\d')  # function with no args
        self.assertEquals(('3', []),
                          evaluate_template('=LEN( 1.2 )',
                                            context))  # auto decimal -> string conversion
        self.assertEquals(('16', []),
                          evaluate_template('=LEN(flow.joined)',
                                            context))  # auto datetime -> string conversion
        self.assertEquals(('2', []),
                          evaluate_template('=WORD_COUNT("abc-def", FALSE)',
                                            context))  # built-in variable
        self.assertEquals(('True', []),
                          evaluate_template('=OR(AND(True, flow.count = flow.users, 1), 0)',
                                            context))  # booleans / varargs
        self.assertEquals(('yes', []),
                          evaluate_template('=IF(IF(flow.count > 4, "x", "y") = "x", "yes", "no")',
                                            context))  # nested conditional

        # test old style expressions, i.e. @ and with filters
        self.assertEquals(("Hello World Joe Joe", []),
                          evaluate_template("Hello World @contact.first_name @contact.first_name", context))
        self.assertEquals(("Hello World Joe Blow", []),
                          evaluate_template("Hello World @contact", context))
        self.assertEquals(("Hello World: Well", []),
                          evaluate_template("Hello World: @flow.water_source", context))
        self.assertEquals(("Hello World: ", []),
                          evaluate_template("Hello World: @flow.blank", context))
        self.assertEquals(("Hello اثنين ثلاثة thanks", []),
                          evaluate_template("Hello @flow.arabic thanks", context))
        self.assertEqual((' %20%3D%26%D8%A8 ', []),
                         evaluate_template(' @flow.urlstuff ', context, True))  # url encoding enabled
        self.assertEquals(("Hello Joe", []),
                          evaluate_template("Hello @contact.first_name|notthere", context))
        self.assertEquals(("Hello joe", []),
                          evaluate_template("Hello @contact.first_name|lower_case", context))
        self.assertEquals(("Hello Joe", []),
                          evaluate_template("Hello @contact.first_name|lower_case|capitalize", context))
        self.assertEquals(("Hello Joe", []),
                          evaluate_template("Hello @contact|first_word", context))
        self.assertEquals(("Hello Blow", []),
                          evaluate_template("Hello @contact|remove_first_word|title_case", context))
        self.assertEquals(("Hello Joe Blow", []),
                          evaluate_template("Hello @contact|title_case", context))
        self.assertEquals(("Hello JOE", []),
                          evaluate_template("Hello @contact.first_name|upper_case", context))
        self.assertEquals(("Hello Joe from info@example.com", []),
                          evaluate_template("Hello @contact.first_name from info@example.com", context))
        self.assertEquals(("Joe", []),
                          evaluate_template("@contact.first_name", context))
        self.assertEquals(("foo@nicpottier.com", []),
                          evaluate_template("foo@nicpottier.com", context))
        self.assertEquals(("@nicpottier is on twitter", []),
                          evaluate_template("@nicpottier is on twitter", context))


        # evaluation errors
        self.assertEquals(("Error: =()", ["Syntax error at ')'"]),
                          evaluate_template("Error: =()",
                                            context))  # syntax error due to empty expression
        self.assertEquals(("Error: =('2')", ["Illegal character '''"]),
                          evaluate_template("Error: =('2')",
                                            context))  # don't support single quote string literals
        self.assertEquals(("Error: =(2 / 0)", ["Division by zero"]),
                          evaluate_template("Error: =(2 / 0)",
                                            context))  # division by zero
        self.assertEquals(("Error: =(1 + flow.blank)", ["Can't convert '' to a decimal"]),
                          evaluate_template("Error: =(1 + flow.blank)",
                                            context))  # string that isn't numeric
        self.assertEquals(("Well =flow.boil", ["Undefined variable 'flow.boil'"]),
                          evaluate_template("=flow.water_source =flow.boil",
                                            context))  # undefined variables
        self.assertEquals(("Hello =XXX(1, 2)", ["Undefined function 'XXX'"]),
                          evaluate_template("Hello =XXX(1, 2)",
                                            context))  # undefined function
        self.assertEquals(('Hello =ABS(1, "x", TRUE)', ['Error calling function ABS with arguments 1, "x", True']),
                          evaluate_template('Hello =ABS(1, "x", TRUE)',
                                            context))  # wrong number of args
        self.assertEquals(('Hello =REPT(flow.blank, -2)', ['Error calling function REPT with arguments "", -2']),
                          evaluate_template('Hello =REPT(flow.blank, -2)',
                                            context))  # internal function error

    def test_expressions(self):
        variables = dict()
        context = EvaluationContext(variables, dict(tz=timezone.utc, dayfirst=True))

        # arithmetic
        self.assertEquals(Decimal(5), evaluate_expression('2 + 3', context))
        self.assertEquals(Decimal(5), evaluate_expression('"2" + 3', context))
        self.assertEquals(Decimal(5), evaluate_expression('"2" + "3"', context))  # both args as strings
        self.assertEquals(Decimal(-1), evaluate_expression('2 -3', context))
        self.assertEquals(Decimal(5), evaluate_expression('2 - -3', context))
        self.assertEquals(Decimal(6), evaluate_expression('2*3', context))  # no spaces
        self.assertEquals(Decimal(2), evaluate_expression('4/2', context))
        self.assertEquals(Decimal(8), evaluate_expression('2^3', context))
        self.assertEquals(Decimal(21), evaluate_expression('(1 + 2) * (3 + 4)', context))  # grouping
        self.assertEquals(Decimal(62.5), evaluate_expression('2 + 3 ^ 4 * 5 / 6 - 7', context))  # op precedence

        # logical comparisons
        self.assertEquals(True, evaluate_expression('123.0 = 123', context))
        self.assertEquals(True, evaluate_expression('"123.0" = "123"', context))
        self.assertEquals(True, evaluate_expression('"abc" = "abc"', context))
        self.assertEquals(True, evaluate_expression('"abc" = "ABC"', context))
        self.assertEquals(False, evaluate_expression('"abc" = "xyz"', context))
        self.assertEquals(True, evaluate_expression('DATE(2014, 1, 2) = DATE(2014, 1, 2)', context))
        self.assertEquals(True, evaluate_expression('DATE(2014, 1, 2) = "2-1-2014"', context))
        self.assertEquals(False, evaluate_expression('DATE(2014, 1, 3) = DATE(2014, 1, 2)', context))
        self.assertEquals(False, evaluate_expression('"abc" = 123', context))
        self.assertEquals(True, evaluate_expression('TRUE = TRUE', context))
        self.assertEquals(False, evaluate_expression('TRUE = FALSE', context))
        self.assertEquals(True, evaluate_expression('FALSE = FALSE', context))

        self.assertEquals(False, evaluate_expression('123.0 <> 123', context))
        self.assertEquals(False, evaluate_expression('"abc" <> "abc"', context))
        self.assertEquals(False, evaluate_expression('"abc" <> "ABC"', context))
        self.assertEquals(True, evaluate_expression('"abc" <> "xyz"', context))
        self.assertEquals(True, evaluate_expression('DATE(2014, 1, 3) <> DATE(2014, 1, 2)', context))
        self.assertEquals(False, evaluate_expression('DATE(2014, 1, 2) <> DATE(2014, 1, 2)', context))

        self.assertEquals(True, evaluate_expression('2 >= 2', context))
        self.assertEquals(False, evaluate_expression('1 >= 2', context))

        self.assertEquals(True, evaluate_expression('3 > 2', context))
        self.assertEquals(False, evaluate_expression('2 > 3', context))
        self.assertEquals(True, evaluate_expression('3 > "2"', context))
        self.assertEquals(False, evaluate_expression('"b" > "c"', context))

        self.assertEquals(True, evaluate_expression('2 <= 2', context))
        self.assertEquals(False, evaluate_expression('3 <= 2', context))

        self.assertEquals(True, evaluate_expression('2 < 3', context))
        self.assertEquals(False, evaluate_expression('3 < 2', context))
        self.assertEquals(True, evaluate_expression('"b" < "c"', context))

        self.assertEquals(True, evaluate_expression('4 - 2 > 1', context))  # check precedence
        self.assertEquals(True, evaluate_expression('(2 >= 1) = TRUE', context))

    def test_value_conversion(self):
        set_evaluation_context(EvaluationContext(dict(), dict(tz=timezone.utc, dayfirst=True)))

        # val_to_date should always return date, discarding any time information
        self.assertEqual(date(2013, 2, 1), val_to_date(date(2013, 2, 1)))
        self.assertEqual(date(2013, 1, 2), val_to_date(datetime(2013, 1, 2, 3, 4, 0, 0, timezone.utc)))
        self.assertEqual(date(2013, 1, 2), val_to_date("2/1/13"))
        self.assertEqual(date(2013, 1, 2), val_to_date("2-1-13 03:04"))
        self.assertRaises(EvaluationError, val_to_date, ["2/1"])

        # val_to_date should always return datetime, creating time information if necessary
        self.assertEqual(datetime(2013, 1, 2, 0, 0, 0, 0, timezone.utc), val_to_datetime("02-01-2013"))
        self.assertEqual(datetime(2013, 1, 2, 3, 4, 0, 0, timezone.utc), val_to_datetime("2/1/13 03:04"))
        self.assertRaises(EvaluationError, val_to_datetime, ["2/1"])

        # val_to_date_or_datetime should return date or datetime depending on information given
        self.assertEqual(date(2013, 2, 1), val_to_date_or_datetime(date(2013, 2, 1)))
        self.assertEqual(date(2013, 2, 1), val_to_date_or_datetime("1-2-13"))
        self.assertEqual(date(2013, 1, 31), val_to_date_or_datetime("1-31-13"))  # overrides dayfirst setting
        self.assertEqual(datetime(2013, 1, 2, 3, 4, 0, 0, timezone.utc), val_to_date_or_datetime(datetime(2013, 1, 2, 3, 4, 0, 0, timezone.utc)))
        self.assertEqual(datetime(2013, 1, 2, 3, 4, 0, 0, timezone.utc), val_to_date_or_datetime("2/1/13 03:04"))
        self.assertRaises(EvaluationError, val_to_date_or_datetime, ["2/1"])

        # val_to_boolean has slightly different rules to usual Python truthiness
        self.assertTrue(True)
        self.assertFalse(False)
        self.assertTrue(val_to_boolean(1))
        self.assertFalse(val_to_boolean(0))
        self.assertTrue(val_to_boolean(-1))
        self.assertTrue(val_to_boolean('TRUE'))
        self.assertTrue(val_to_boolean('true'))
        self.assertFalse(val_to_boolean('FALSE'))
        self.assertFalse(val_to_boolean('false'))
        self.assertFalse(val_to_boolean('false'))
        self.assertTrue(val_to_boolean('1'))
        self.assertFalse(val_to_boolean('0.0'))
        self.assertTrue(val_to_boolean('-1'))
        self.assertRaises(EvaluationError, val_to_boolean, 'x')

    def test_parser_functions(self):
        tz = pytz.UTC
        set_evaluation_context(EvaluationContext(dict(), dict(tz=tz, dayfirst=True)))

        # text functions
        self.assertEqual('\t', f_char(9))
        self.assertEqual('\n', f_char(10))
        self.assertEqual('\r', f_char(13))
        self.assertEqual(' ', f_char(32))
        self.assertEqual('A', f_char(65))

        self.assertEqual('Hello world', f_clean('Hello \nwo\trl\rd'))

        self.assertEqual(9, f_code('\t'))
        self.assertEqual(10, f_code('\n'))

        self.assertEqual('Hello4\n', f_concatenate('Hello', 4, '\n'))
        self.assertEqual('واحد إثنان ثلاثة', f_concatenate('واحد', ' ', 'إثنان', ' ', 'ثلاثة'))

        self.assertEqual('1,234.57', f_fixed(Decimal('1234.5678')))  # default is 2 decimal places with commas
        self.assertEqual('1,234.6', f_fixed('1234.5678', 1))
        self.assertEqual('1234.568', f_fixed('1234.5678', 3, True))
        self.assertEqual('1,200', f_fixed('1234.5678', -2))
        self.assertEqual('1200', f_fixed('1234.5678', -2, True))

        self.assertEqual('ab', f_left('abcdef', 2))
        self.assertEqual('وا', f_left('واحد', 2))
        self.assertRaises(ValueError, f_left, 'abcd', -1)  # exception for negative char count

        self.assertEqual(0, f_len(''))
        self.assertEqual(3, f_len('abc'))
        self.assertEqual(4, f_len('واحد'))

        self.assertEqual('abcd', f_lower('aBcD'))
        self.assertEqual('a واحد', f_lower('A واحد'))

        self.assertEqual('First-Second Third', f_proper('first-second third'))
        self.assertEqual('واحد Abc ثلاثة', f_proper('واحد abc ثلاثة'))

        self.assertEqual('abcabcabc', f_rept('abc', 3))
        self.assertEqual('واحدواحدواحد', f_rept('واحد', 3))

        self.assertEqual('ef', f_right('abcdef', 2))
        self.assertEqual('حد', f_right('واحد', 2))
        self.assertRaises(ValueError, f_right, 'abcd', -1)  # exception for negative char count

        self.assertEqual('bonjour Hello world', f_substitute('hello Hello world', 'hello', 'bonjour'))  # case-sensitive
        self.assertEqual('bonjour bonjour world', f_substitute('hello hello world', 'hello', 'bonjour'))  # all instances
        self.assertEqual('hello bonjour world', f_substitute('hello hello world', 'hello', 'bonjour', 2))  # specific instance
        self.assertEqual('إثنان إثنان ثلاثة', f_substitute('واحد إثنان ثلاثة', 'واحد', 'إثنان'))

        self.assertEqual('A', f_unichar(65))
        self.assertEqual('ا', f_unichar(1575))

        self.assertEqual(9, f_unicode('\t'))
        self.assertRaises(ValueError, f_unicode, '')  # exception for empty string
        self.assertEqual(1234, f_unicode('\u04d2'))
        self.assertEqual(1575, f_unicode('ا'))

        self.assertEqual('ABCD', f_upper('aBcD'))
        self.assertEqual('A واحد', f_upper('a واحد'))

        # date and time functions, all performed as if it were 2014-01-02 03:04:05.6 UTC
        with patch.object(timezone, 'now', return_value=tz.localize(datetime(2014, 1, 2, 3, 4, 5, 6))):
            self.assertEqual(date(2012, 3, 2), f_date(2012, "3", 2.0))
            self.assertEqual(date(2013, 3, 2), f_datevalue("2-3-13"))
            self.assertEqual(2, f_day(timezone.now()))
            self.assertEqual(tz.localize(datetime(2014, 2, 2, 3, 4, 5, 6)), f_edate(timezone.now(), 1))
            self.assertEqual(date(2013, 12, 1), f_edate('01-02-2014', -2))
            self.assertEqual(3, f_hour(timezone.now()))
            self.assertEqual(4, f_minute(timezone.now()))
            self.assertEqual(1, f_month(timezone.now()))
            self.assertEqual(timezone.now(), f_now())
            self.assertEqual(5, f_second(timezone.now()))
            self.assertEqual(time(1, 30, 15), f_time(1, 30, 15))
            self.assertEqual(time(1, 30, 15), f_timevalue('1:30:15'))
            self.assertEqual(timezone.now().date(), f_today())
            self.assertEqual(5, f_weekday(timezone.now()))  # thursday = 5
            self.assertEqual(2014, f_year(timezone.now()))

        # run some more in Kabul time for good measure
        tz = pytz.timezone('Asia/Kabul')
        set_evaluation_context(EvaluationContext(dict(), dict(tz=tz, dayfirst=True)))
        with patch.object(timezone, 'now', return_value=tz.localize(datetime(2014, 1, 2, 3, 4, 5, 6))):
            self.assertEqual(tz.localize(datetime(2014, 2, 2, 3, 4, 5, 6)), f_edate(timezone.now(), 1))
            self.assertEqual(tz.localize(datetime(2014, 2, 2, 3, 4, 0, 0)), f_edate('02-01-2014 03:04', 1))
            self.assertEqual(timezone.now(), f_now())

        # math functions
        self.assertEqual(1, f_abs(1))
        self.assertEqual(1, f_abs(-1))

        self.assertEqual(1, f_max(1))
        self.assertEqual(3, f_max(1, 3, 2, -5))
        self.assertEqual(-2, f_max(-2, -5))

        self.assertEqual(1, f_min(1))
        self.assertEqual(-3, f_min(-1, -3, -2, 5))
        self.assertEqual(-5, f_min(-2, -5))

        self.assertEqual(Decimal('16'), f_power('4', '2'))
        self.assertEqual(Decimal('2'), f_power('4', '0.5'))

        self.assertEqual(1, f_sum(1))
        self.assertEqual(6, f_sum(1, 2, 3))

        # logical functions
        self.assertEqual(False, f_and(False))
        self.assertEqual(True, f_and(True))
        self.assertEqual(True, f_and(1, True, "true"))
        self.assertEqual(False, f_and(1, True, "true", 0))

        self.assertEqual(False, f_false())

        self.assertEqual(0, f_if(True))
        self.assertEqual('x', f_if(True, 'x', 'y'))
        self.assertEqual('x', f_if('true', 'x', 'y'))
        self.assertEqual(False, f_if(False))
        self.assertEqual('y', f_if(False, 'x', 'y'))
        self.assertEqual('y', f_if(0, 'x', 'y'))

        self.assertEqual(False, f_or(False))
        self.assertEqual(True, f_or(True))
        self.assertEqual(True, f_or(1, False, "false"))
        self.assertEqual(True, f_or(0, True, "false"))

        self.assertEqual(True, f_true())

        # custom functions
        self.assertEqual('', f_first_word('  '))
        self.assertEqual('abc', f_first_word(' abc '))
        self.assertEqual('abc', f_first_word(' abc '))
        self.assertEqual('abc', f_first_word(' abc def ghi'))
        self.assertEqual('واحد', f_first_word(' واحد '))
        self.assertEqual('واحد', f_first_word(' واحد إثنان ثلاثة '))

        self.assertEqual('25%', f_percent('0.25321'))
        self.assertEqual('33%', f_percent(Decimal('0.33')))

        self.assertEqual('1 2 3 4 , 5 6 7 8 , 9 0 1 2 , 3 4 5 6', f_read_digits('1234567890123456'))  # credit card
        self.assertEqual('1 2 3 , 4 5 6 , 7 8 9 , 0 1 2', f_read_digits('+123456789012'))  # phone number
        self.assertEqual('1 2 3 , 4 5 6', f_read_digits('123456'))  # triplets
        self.assertEqual('1 2 3 , 4 5 , 6 7 8 9', f_read_digits('123456789'))  # soc security
        self.assertEqual('1,2,3,4,5', f_read_digits('12345'))  # regular number, street address, etc
        self.assertEqual('1,2,3', f_read_digits('123'))  # regular number, street address, etc
        self.assertEqual('', f_read_digits(''))  # empty

        self.assertEqual('', f_remove_first_word('abc'))
        self.assertEqual('', f_remove_first_word(' abc '))
        self.assertEqual('def-ghi ', f_remove_first_word(' abc def-ghi '))  # should preserve remainder of text
        self.assertEqual('', f_remove_first_word(' واحد '))
        self.assertEqual('إثنان ثلاثة ', f_remove_first_word(' واحد إثنان ثلاثة '))

        self.assertEqual('abc', f_word(' abc def ghi', 1))
        self.assertEqual('ghi', f_word('abc-def  ghi  jkl', 3))
        self.assertEqual('jkl', f_word('abc-def  ghi  jkl', 3, True))
        self.assertEqual('jkl', f_word('abc-def  ghi  jkl', '3', 'TRUE'))  # string args only
        self.assertEqual('jkl', f_word('abc-def  ghi  jkl', -1))  # negative index
        self.assertEqual('', f_word(' abc def   ghi', 6))  # out of range
        self.assertEqual('', f_word('', 1))
        self.assertEqual('واحد', f_word(' واحد إثنان ثلاثة ', 1))
        self.assertEqual('ثلاثة', f_word(' واحد إثنان ثلاثة ', -1))
        self.assertRaises(ValueError, f_word, '', 0)  # number cannot be zero

        self.assertEqual(0, f_word_count(''))
        self.assertEqual(4, f_word_count(' abc-def  ghi  jkl'))
        self.assertEqual(4, f_word_count(' abc-def  ghi  jkl', False))
        self.assertEqual(3, f_word_count(' abc-def  ghi  jkl', True))
        self.assertEqual(3, f_word_count(' واحد إثنان-ثلاثة ', False))
        self.assertEqual(2, f_word_count(' واحد إثنان-ثلاثة ', True))

        self.assertEqual('abc def', f_word_slice(' abc  def ghi-jkl ', 1, 3))
        self.assertEqual('ghi jkl', f_word_slice(' abc  def ghi-jkl ', 3, 0))
        self.assertEqual('ghi-jkl', f_word_slice(' abc  def ghi-jkl ', 3, 0, True))
        self.assertEqual('ghi jkl', f_word_slice(' abc  def ghi-jkl ', '3', '0', 'false'))  # string args only
        self.assertEqual('ghi jkl', f_word_slice(' abc  def ghi-jkl ', 3))
        self.assertEqual('def ghi', f_word_slice(' abc  def ghi-jkl ', 2, -1))
        self.assertEqual('jkl', f_word_slice(' abc  def ghi-jkl ', -1))
        self.assertEqual('def', f_word_slice(' abc  def ghi-jkl ', 2, -1, True))
        self.assertEqual('واحد إثنان', f_word_slice(' واحد إثنان ثلاثة ', 1, 3))
        self.assertRaises(ValueError, f_word_slice, ' abc  def ghi-jkl ', 0)  # start can't be zero

        # check function listing
        self.assertGreater(len(get_function_listing()), 0)


class PageableQueryTest(TembaTest):
    def setUp(self):
        TembaTest.setUp(self)

        self.joe = self.create_contact("Joe Blow", "1234", "blow80")
        self.frank = self.create_contact("Frank Smith", "2345")
        self.mary = self.create_contact("Mary Jo", "3456")
        self.anne = self.create_contact("Anne Smith", "4567")
        self.billy = self.create_contact("Billy Joel")

    def test_query(self):
        def assertResultNames(names, result):
            self.assertEqual(names, [r['name'] for r in result])

        def assertPage(names, has_next, page):
            assertResultNames(names, page)
            self.assertEqual(has_next, page.has_next())

        # simple parameterless select
        query = PageableQuery("SELECT * FROM contacts_contact", ('name',), ())
        self.assertEqual(5, query.count())
        self.assertEqual(5, len(query))
        assertResultNames(["Anne Smith", "Billy Joel"], query[0:2])
        assertResultNames(["Frank Smith", "Joe Blow"], query[2:4])
        assertResultNames(["Mary Jo"], query[4:6])

        # check use with paginator
        paginator = Paginator(query, 2)
        assertPage(["Anne Smith", "Billy Joel"], True, paginator.page(1))
        assertPage(["Frank Smith", "Joe Blow"], True, paginator.page(2))
        assertPage(["Mary Jo"], False, paginator.page(3))

        # select with parameter
        query = PageableQuery("SELECT * FROM contacts_contact WHERE name ILIKE %s", ('name',), ('%jo%',))
        paginator = Paginator(query, 2)
        assertPage(["Billy Joel", "Joe Blow"], True, paginator.page(1))
        assertPage(["Mary Jo"], False, paginator.page(2))


class PercentageTest(TembaTest):

    def test_percentage(self):
        self.assertEquals(0, percentage(0, 100))
        self.assertEquals(0, percentage(0, 0))
        self.assertEquals(0, percentage(100, 0))
        self.assertEquals(75, percentage(75, 100))
        self.assertEquals(76, percentage(759, 1000))
