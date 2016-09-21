# coding=utf-8
from __future__ import absolute_import, unicode_literals

import inspect
import json
import os
import re
import redis
import shutil
import string
import time

from datetime import datetime
from django.conf import settings
from django.contrib.auth.models import User
from django.core.urlresolvers import reverse
from django.db import connection
from django.test import LiveServerTestCase
from django.test.runner import DiscoverRunner
from django.utils import timezone
from HTMLParser import HTMLParser
from selenium.webdriver.firefox.webdriver import WebDriver
from smartmin.tests import SmartminTest
from temba.contacts.models import Contact, ContactGroup, URN
from temba.orgs.models import Org
from temba.channels.models import Channel
from temba.locations.models import AdminBoundary
from temba.flows.models import Flow, ActionSet, RuleSet, FlowStep
from temba.ivr.clients import TwilioClient
from temba.msgs.models import Msg, INCOMING
from temba.utils import dict_to_struct
from twilio.util import RequestValidator
from xlrd import xldate_as_tuple
from xlrd.sheet import XL_CELL_DATE


class ExcludeTestRunner(DiscoverRunner):
    def __init__(self, *args, **kwargs):
        from django.conf import settings
        settings.TESTING = True
        super(ExcludeTestRunner, self).__init__(*args, **kwargs)

    def build_suite(self, *args, **kwargs):
        suite = super(ExcludeTestRunner, self).build_suite(*args, **kwargs)
        excluded = getattr(settings, 'TEST_EXCLUDE', [])
        if not getattr(settings, 'RUN_ALL_TESTS', False):
            tests = []
            for case in suite:
                pkg = case.__class__.__module__.split('.')[0]
                if pkg not in excluded:
                    tests.append(case)
            suite._tests = tests
        return suite


def add_testing_flag_to_context(*args):
    return dict(testing=settings.TESTING)


def uuid(val):
    return '00000000-00000000-00000000-%08d' % val


class TembaTest(SmartminTest):

    def setUp(self):

        # if we are super verbose, turn on debug for sql queries
        if self.get_verbosity() > 2:
            settings.DEBUG = True

        self.clear_cache()

        self.superuser = User.objects.create_superuser(username="super", email="super@user.com", password="super")

        # create different user types
        self.non_org_user = self.create_user("NonOrg")
        self.user = self.create_user("User")
        self.editor = self.create_user("Editor")
        self.admin = self.create_user("Administrator")
        self.surveyor = self.create_user("Surveyor")

        # setup admin boundaries for Rwanda
        self.country = AdminBoundary.objects.create(osm_id='171496', name='Rwanda', level=0)
        self.state1 = AdminBoundary.objects.create(osm_id='1708283', name='Kigali City', level=1, parent=self.country)
        self.state2 = AdminBoundary.objects.create(osm_id='171591', name='Eastern Province', level=1, parent=self.country)
        self.district1 = AdminBoundary.objects.create(osm_id='1711131', name='Gatsibo', level=2, parent=self.state2)
        self.district2 = AdminBoundary.objects.create(osm_id='1711163', name='Kayônza', level=2, parent=self.state2)
        self.district3 = AdminBoundary.objects.create(osm_id='3963734', name='Nyarugenge', level=2, parent=self.state1)
        self.district4 = AdminBoundary.objects.create(osm_id='1711142', name='Rwamagana', level=2, parent=self.state2)
        self.ward1 = AdminBoundary.objects.create(osm_id='171113181', name='Kageyo', level=3, parent=self.district1)
        self.ward2 = AdminBoundary.objects.create(osm_id='171116381', name='Kabare', level=3, parent=self.district2)
        self.ward3 = AdminBoundary.objects.create(osm_id='171114281', name='Bukure', level=3, parent=self.district4)

        self.org = Org.objects.create(name="Temba", timezone="Africa/Kigali", country=self.country, brand=settings.DEFAULT_BRAND,
                                      created_by=self.user, modified_by=self.user)

        self.org.initialize(topup_size=1000)

        # add users to the org
        self.user.set_org(self.org)
        self.org.viewers.add(self.user)

        self.editor.set_org(self.org)
        self.org.editors.add(self.editor)

        self.admin.set_org(self.org)
        self.org.administrators.add(self.admin)

        self.surveyor.set_org(self.org)
        self.org.surveyors.add(self.surveyor)

        self.superuser.set_org(self.org)

        # welcome topup with 1000 credits
        self.welcome_topup = self.org.topups.all()[0]

        # a single Android channel
        self.channel = Channel.create(self.org, self.user, 'RW', 'A', name="Test Channel", address="+250785551212",
                                      device="Nexus 5X", secret="12345", gcm_id="123")

        # reset our simulation to False
        Contact.set_simulation(False)

    def get_verbosity(self):
        for s in reversed(inspect.stack()):
            options = s[0].f_locals.get('options')
            if isinstance(options, dict):
                return int(options['verbosity'])
        return 1

    def explain(self, query):
        cursor = connection.cursor()
        cursor.execute('explain %s' % query)
        plan = cursor.fetchall()
        indexes = []
        for match in re.finditer('Index Scan using (.*?) on (.*?) \(cost', unicode(plan), re.DOTALL):
            index = match.group(1).strip()
            table = match.group(2).strip()
            indexes.append((table, index))

        indexes = sorted(indexes, key=lambda i: i[0])
        return indexes

    def tearDown(self):

        if self.get_verbosity() > 2:
            details = []
            for query in connection.queries:
                query = query['sql']
                if 'SAVEPOINT' not in query:
                    indexes = self.explain(query)
                    details.append(dict(query=query, indexes=indexes))

            for stat in details:
                print
                print stat['query']
                for table, index in stat['indexes']:
                    print '  Index Used: %s.%s' % (table, index)

                if not len(stat['indexes']):
                    print '  No Index Used'

            settings.DEBUG = False

    def clear_cache(self):
        """
        Clears the redis cache. We are extra paranoid here and actually hard-code redis to 'localhost' and '10'
        Redis 10 is our testing redis db
        """
        r = redis.StrictRedis(host='localhost', db=10)
        r.flushdb()

    def clear_storage(self):
        """
        If a test has written files to storage, it should remove them by calling this
        """
        shutil.rmtree('%s/%s' % (settings.MEDIA_ROOT, settings.STORAGE_ROOT_DIR), ignore_errors=True)

    def import_file(self, filename, site='http://rapidpro.io', substitutions=None):
        data = self.get_import_json(filename, substitutions=substitutions)
        self.org.import_app(json.loads(data), self.admin, site=site)

    def get_import_json(self, filename, substitutions=None):
        handle = open('%s/test_flows/%s.json' % (settings.MEDIA_ROOT, filename), 'r+')
        data = handle.read()
        handle.close()

        if substitutions:
            for k, v in substitutions.iteritems():
                print 'Replacing "%s" with "%s"' % (k, v)
                data = data.replace(k, str(v))

        return data

    def get_flow(self, filename, substitutions=None):
        last_flow = Flow.objects.all().order_by('-pk').first()
        self.import_file(filename, substitutions=substitutions)

        if last_flow:
            return Flow.objects.filter(pk__gt=last_flow.pk).first()

        return Flow.objects.all().order_by('-created_on').first()

    def get_flow_json(self, filename, substitutions=None):
        data = self.get_import_json(filename, substitutions=substitutions)
        return json.loads(data)['flows'][0]

    def create_secondary_org(self):
        self.admin2 = self.create_user("Administrator2")
        self.org2 = Org.objects.create(name="Trileet Inc.", timezone="Africa/Kigali", brand='rapidpro.io',
                                       created_by=self.admin2, modified_by=self.admin2)
        self.org2.administrators.add(self.admin2)
        self.admin2.set_org(self.org)

        self.org2.initialize()

    def create_contact(self, name=None, number=None, twitter=None, urn=None, is_test=False, **kwargs):
        """
        Create a contact in the master test org
        """
        urns = []
        if number:
            urns.append(URN.from_tel(number))
        if twitter:
            urns.append(URN.from_twitter(twitter))
        if urn:
            urns.append(urn)

        if not name and not urns:  # pragma: no cover
            raise ValueError("Need a name or URN to create a contact")

        kwargs['name'] = name
        kwargs['urns'] = urns
        kwargs['is_test'] = is_test

        if 'org' not in kwargs:
            kwargs['org'] = self.org
        if 'user' not in kwargs:
            kwargs['user'] = self.user

        return Contact.get_or_create(**kwargs)

    def create_group(self, name, contacts=(), query=None):
        if contacts and query:
            raise ValueError("Can't provide contact list for a dynamic group")

        if query:
            return ContactGroup.create_dynamic(self.org, self.user, name, query=query)
        else:
            group = ContactGroup.create_static(self.org, self.user, name)
            if contacts:
                group.contacts.add(*contacts)
            return group

    def create_msg(self, **kwargs):
        if 'org' not in kwargs:
            kwargs['org'] = self.org
        if 'channel' not in kwargs:
            kwargs['channel'] = self.channel
        if 'contact_urn' not in kwargs:
            kwargs['contact_urn'] = kwargs['contact'].get_urn()
        if 'created_on' not in kwargs:
            kwargs['created_on'] = timezone.now()

        if not kwargs['contact'].is_test:
            (kwargs['topup_id'], amount) = kwargs['org'].decrement_credit()

        return Msg.all_messages.create(**kwargs)

    def create_flow(self, uuid_start=None, **kwargs):
        if 'org' not in kwargs:
            kwargs['org'] = self.org
        if 'user' not in kwargs:
            kwargs['user'] = self.user
        if 'name' not in kwargs:
            kwargs['name'] = "Color Flow"

        flow = Flow.create(**kwargs)
        flow.update(self.create_flow_definition(uuid_start))
        return Flow.objects.get(pk=flow.pk)

    def create_flow_definition(self, uuid_start=None):
        """
        Creates the "Color" flow definition
        """
        if uuid_start is None:
            uuid_start = int(time.time() * 1000) % 1000000

        return dict(version=8,
                    action_sets=[dict(uuid=uuid(uuid_start + 1), x=1, y=1, destination=uuid(uuid_start + 5),
                                      actions=[dict(type='reply', msg=dict(base="What is your favorite color?", fre="Quelle est votre couleur préférée?"))]),
                                 dict(uuid=uuid(uuid_start + 2), x=2, y=2, destination=None,
                                      actions=[dict(type='reply', msg=dict(base='I love orange too! You said: @step.value which is category: @flow.color.category You are: @step.contact.tel SMS: @step Flow: @flow'))]),
                                 dict(uuid=uuid(uuid_start + 3), x=3, y=3, destination=None,
                                      actions=[dict(type='reply', msg=dict(base='Blue is sad. :('))]),
                                 dict(uuid=uuid(uuid_start + 4), x=4, y=4, destination=uuid(uuid_start + 5),
                                      actions=[dict(type='reply', msg=dict(base='That is a funny color. Try again.'))])],
                    rule_sets=[dict(uuid=uuid(uuid_start + 5), x=5, y=5,
                                    label='color',
                                    finished_key=None,
                                    operand=None,
                                    response_type='',
                                    ruleset_type='wait_message',
                                    config={},
                                    rules=[dict(uuid=uuid(uuid_start + 12), destination=uuid(uuid_start + 2), test=dict(type='contains', test=dict(base='orange')), category=dict(base="Orange")),
                                           dict(uuid=uuid(uuid_start + 13), destination=uuid(uuid_start + 3), test=dict(type='contains', test=dict(base='blue')), category=dict(base="Blue")),
                                           dict(uuid=uuid(uuid_start + 14), destination=uuid(uuid_start + 4), test=dict(type='true'), category=dict(base="Other")),
                                           dict(uuid=uuid(uuid_start + 15), test=dict(type='true'), category=dict(base="Nothing"))])],  # test case with no destination
                    entry=uuid(uuid_start + 1),
                    base_language='base',
                    flow_type='F',
                    metadata=dict(author="Ryan Lewis"))

    def update_destination(self, flow, source, destination):
        flow_json = flow.as_json()

        for actionset in flow_json.get('action_sets'):
            if actionset.get('uuid') == source:
                actionset['destination'] = destination

        for ruleset in flow_json.get('rule_sets'):
            for rule in ruleset.get('rules'):
                if rule.get('uuid') == source:
                    rule['destination'] = destination

        flow.update(flow_json)
        return Flow.objects.get(pk=flow.pk)

    def update_destination_no_check(self, flow, node, destination, rule=None):  # pragma: no cover
        """ Update the destination without doing a cycle check """
        # look up our destination, we need this in order to set the correct destination_type
        destination_type = FlowStep.TYPE_ACTION_SET
        action_destination = Flow.get_node(flow, destination, destination_type)
        if not action_destination:
            destination_type = FlowStep.TYPE_RULE_SET
            ruleset_destination = Flow.get_node(flow, destination, destination_type)
            self.assertTrue(ruleset_destination, "Unable to find new destination with uuid: %s" % destination)

        actionset = ActionSet.get(flow, node)
        if actionset:
            actionset.destination = destination
            actionset.destination_type = destination_type
            actionset.save()

        ruleset = RuleSet.get(flow, node)
        if ruleset:
            rules = ruleset.get_rules()
            for r in rules:
                if r.uuid == rule:
                    r.destination = destination
                    r.destination_type = destination_type
            ruleset.set_rules(rules)
            ruleset.save()
        else:
            self.fail("Couldn't find node with uuid: %s" % node)

    def assertExcelRow(self, sheet, row_num, values, tz=None):
        """
        Asserts the cell values in the given worksheet row. Date values are converted using the provided timezone.
        """
        expected_values = []
        for expected in values:
            # if expected value is datetime, localize and remove microseconds
            if isinstance(expected, datetime):
                expected = expected.astimezone(tz).replace(microsecond=0, tzinfo=None)

            expected_values.append(expected)

        actual_values = []
        for c in range(0, sheet.ncols):
            cell = sheet.cell(row_num, c)
            actual = cell.value

            if cell.ctype == XL_CELL_DATE:
                actual = datetime(*xldate_as_tuple(actual, sheet.book.datemode))

            actual_values.append(actual)

        self.assertEqual(actual_values, expected_values)


class FlowFileTest(TembaTest):

    def setUp(self):
        super(FlowFileTest, self).setUp()
        self.contact = self.create_contact('Ben Haggerty', '+12065552020')

    def assertLastResponse(self, message):
        response = Msg.all_messages.filter(contact=self.contact).order_by('-created_on', '-pk').first()

        self.assertTrue("Missing response from contact.", response)
        self.assertEquals(message, response.text)

    def send(self, message, contact=None):
        if not contact:
            contact = self.contact
        if contact.is_test:
            Contact.set_simulation(True)
        incoming = self.create_msg(direction=INCOMING, contact=contact, text=message)
        Flow.find_and_handle(incoming)
        return Msg.all_messages.filter(response_to=incoming).order_by('pk').first()

    def send_message(self, flow, message, restart_participants=False, contact=None, initiate_flow=False,
                     assert_reply=True, assert_handle=True):
        """
        Starts the flow, sends the message, returns the reply
        """
        if not contact:
            contact = self.contact

        try:
            if contact.is_test:
                Contact.set_simulation(True)

            incoming = self.create_msg(direction=INCOMING, contact=contact, text=message)

            # start the flow
            if initiate_flow:
                flow.start(groups=[], contacts=[contact], restart_participants=restart_participants, start_msg=incoming)
            else:
                flow.start(groups=[], contacts=[contact], restart_participants=restart_participants)
                handled = Flow.find_and_handle(incoming)

                Msg.mark_handled(incoming)

                if assert_handle:
                    self.assertTrue(handled, "'%s' did not handle message as expected" % flow.name)
                else:
                    self.assertFalse(handled, "'%s' handled message, was supposed to ignore" % flow.name)

            # our message should have gotten a reply
            if assert_reply:
                replies = Msg.all_messages.filter(response_to=incoming).order_by('pk')
                self.assertGreaterEqual(len(replies), 1)

                if len(replies) == 1:
                    self.assertEquals(contact, replies.first().contact)
                    return replies.first().text

                # if it's more than one, send back a list of replies
                return [reply.text for reply in replies]

            else:
                # assert we got no reply
                replies = Msg.all_messages.filter(response_to=incoming).order_by('pk')
                self.assertFalse(replies)

            return None

        finally:
            Contact.set_simulation(False)


class MLStripper(HTMLParser):  # pragma: no cover
    def __init__(self):
        self.reset()
        self.fed = []

    def handle_data(self, d):
        self.fed.append(d)

    def get_data(self):
        return ''.join(self.fed)


class BrowserTest(LiveServerTestCase):  # pragma: no cover

    @classmethod
    def setUpClass(cls):
        cls.driver = WebDriver()

        try:
            import os
            os.mkdir('screenshots')
        except Exception:
            pass

        super(BrowserTest, cls).setUpClass()

    @classmethod
    def tearDownClass(cls):
        pass
        # cls.driver.quit()
        # super(BrowserTest, cls).tearDownClass()

    def strip_tags(self, html):
        s = MLStripper()
        s.feed(html)
        return s.get_data()

    def save_screenshot(self):
        time.sleep(1)
        valid_chars = "-_.() %s%s" % (string.ascii_letters, string.digits)
        filename = ''.join(c for c in self.driver.current_url if c in valid_chars)
        self.driver.get_screenshot_as_file("screenshots/%s.png" % filename)

    def fetch_page(self, url=None):

        if not url:
            url = ''

        if 'http://' not in url:
            url = self.live_server_url + url

        self.driver.get(url)
        self.save_screenshot()

    def get_elements(self, selector):
        return self.driver.find_elements_by_css_selector(selector)

    def get_element(self, selector):
        if selector[0] == '#' or selector[0] == '.':
            return self.driver.find_element_by_css_selector(selector)
        else:
            return self.driver.find_element_by_name(selector)

    def keys(self, selector, value):
        self.get_element(selector).send_keys(value)

    def click(self, selector):
        time.sleep(1)
        self.get_element(selector).click()
        self.save_screenshot()

    def link(self, link_text):
        self.driver.find_element_by_link_text(link_text).click()
        time.sleep(2)
        self.save_screenshot()

    def submit(self, selector):
        time.sleep(1)
        self.get_element(selector).submit()
        self.save_screenshot()
        time.sleep(1)

    def assertInElements(self, selector, text, strip_html=True):
        for element in self.get_elements(selector):
            if text in (self.strip_tags(element.text) if strip_html else element.text):
                return

        self.fail("Couldn't find '%s' in any element '%s'" % (text, selector))

    def assertInElement(self, selector, text, strip_html=True):
        element = self.get_element(selector)
        if text not in (self.strip_tags(element.text) if strip_html else element.text):
            self.fail("Couldn't find '%s' in  '%s'" % (text, element.text))

    def browser(self):

        self.driver.set_window_size(1024, 2000)

        # view the homepage
        self.fetch_page()

        # go directly to our signup
        self.fetch_page(reverse('orgs.org_signup'))

        # create account
        self.keys('email', 'code@temba.com')
        self.keys('password', 'SuperSafe1')
        self.keys('first_name', 'Joe')
        self.keys('last_name', 'Blow')
        self.click('#form-one-submit')
        self.keys('name', 'Temba')
        self.click('#form-two-submit')

        # set up our channel for claiming
        anon = User.objects.get(username=settings.ANONYMOUS_USER_NAME)
        channel = Channel.create(None, anon, 'RW', 'A', name="Test Channel", address="0785551212",
                                 claim_code='AAABBBCCC', secret="12345", gcm_id="123")

        # and claim it
        self.fetch_page(reverse('channels.channel_claim_android'))
        self.keys('#id_claim_code', 'AAABBBCCC')
        self.keys('#id_phone_number', '0785551212')
        self.submit('.claim-form')

        # get our freshly claimed channel
        channel = Channel.objects.get(pk=channel.pk)

        # now go to the contacts page
        self.click('#menu-right .icon-contact')
        self.click('#id_import_contacts')

        # upload some contacts
        directory = os.path.dirname(os.path.realpath(__file__))
        self.keys('#csv_file', '%s/../media/test_imports/sample_contacts.xls' % directory)
        self.submit('.smartmin-form')

        # make sure they are there
        self.click('#menu-right .icon-contact')
        self.assertInElements('.value-phone', '+250788382382')
        self.assertInElements('.value-text', 'Eric Newcomer')
        self.assertInElements('.value-text', 'Sample Contacts')


class MockResponse(object):

    def __init__(self, status_code, text, method='GET', url='http://foo.com/', headers=None):
        self.text = text
        self.content = text
        self.status_code = status_code
        self.headers = headers if headers else {}

        # mock up a request object on our response as well
        self.request = dict_to_struct('MockRequest', dict(method=method, url=url))

    def add_header(self, key, value):
        self.headers[key] = value

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self):
        if self.status_code != 200:
            raise Exception("Got HTTP error: %d" % self.status_code)


class AnonymousOrg(object):
    """
    Makes the given org temporarily anonymous
    """
    def __init__(self, org):
        self.org = org

    def __enter__(self):
        self.org.is_anon = True
        self.org.save()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.org.is_anon = False
        self.org.save()


class MockRequestValidator(RequestValidator):

    def __init__(self, token):
        pass

    def validate(self, url, post, signature):
        return True


class MockTwilioClient(TwilioClient):

    def __init__(self, sid, token, org=None):
        self.org = org
        self.applications = MockTwilioClient.MockApplications()
        self.calls = MockTwilioClient.MockCalls()
        self.accounts = MockTwilioClient.MockAccounts()
        self.phone_numbers = MockTwilioClient.MockPhoneNumbers()
        self.sms = MockTwilioClient.MockSMS()
        self.auth = ['', 'FakeRequestToken']

    def validate(self, request):
        return True

    class MockShortCode(object):
        def __init__(self, short_code):
            self.short_code = short_code
            self.sid = "ShortSid"

    class MockShortCodes(object):
        def __init__(self, *args):
            pass

        def list(self, short_code=None):
            return [MockTwilioClient.MockShortCode(short_code)]

        def update(self, sid, **kwargs):
            print "Updating short code with sid %s" % sid

    class MockSMS(object):
        def __init__(self, *args):
            self.uri = "/SMS"
            self.short_codes = MockTwilioClient.MockShortCodes()

    class MockCall(object):
        def __init__(self, to=None, from_=None, url=None, status_callback=None):
            self.to = to
            self.from_ = from_
            self.url = url
            self.status_callback = status_callback
            self.sid = 'CallSid'

    class MockApplication(object):
        def __init__(self, friendly_name):
            self.friendly_name = friendly_name
            self.sid = 'TwilioTestSid'

    class MockPhoneNumber(object):
        def __init__(self, phone_number):
            self.phone_number = phone_number
            self.sid = 'PhoneNumberSid'

    class MockAccount(object):
        def __init__(self, account_type, auth_token='AccountToken'):
            self.type = account_type
            self.auth_token = auth_token
            self.sid = 'AccountSid'

    class MockAccounts(object):
        def __init__(self, *args):
            pass

        def get(self, account_type):
            return MockTwilioClient.MockAccount(account_type)

    class MockPhoneNumbers(object):
        def __init__(self, *args):
            pass

        def list(self, phone_number=None):
            return [MockTwilioClient.MockPhoneNumber(phone_number)]

        def update(self, sid, **kwargs):
            print "Updating phone number with sid %s" % sid

    class MockApplications(object):
        def __init__(self, *args):
            pass

        def list(self, friendly_name=None):
            return [MockTwilioClient.MockApplication(friendly_name)]

    class MockCalls(object):
        def __init__(self):
            pass

        def create(self, to=None, from_=None, url=None, status_callback=None):
            return MockTwilioClient.MockCall(to=to, from_=from_, url=url, status_callback=status_callback)

        def hangup(self, external_id):
            print "Hanging up %s on Twilio" % external_id

        def update(self, external_id, url):
            print "Updating call for %s to url %s" % (external_id, url)
