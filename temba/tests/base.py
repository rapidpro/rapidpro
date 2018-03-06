# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import inspect
import json
import os
import pytz
import regex
import redis
import shutil
import string
import six
import time

from datetime import datetime, timedelta
from django.conf import settings
from django.contrib.auth.models import User, Group
from django.core import mail
from django.core.urlresolvers import reverse
from django.db import connection
from django.test import LiveServerTestCase, override_settings
from django.test.runner import DiscoverRunner
from django.utils import timezone
from django.utils.encoding import force_bytes, force_text
from future.moves.html.parser import HTMLParser
from selenium.webdriver.firefox.webdriver import WebDriver
from smartmin.tests import SmartminTest
from temba.contacts.models import Contact, ContactGroup, ContactField, URN
from temba.orgs.models import Org
from temba.channels.models import Channel
from temba.locations.models import AdminBoundary
from temba.flows.models import Flow, ActionSet, RuleSet, FlowStep, FlowRevision, clear_flow_users
from temba.msgs.models import Msg, INCOMING
from temba.utils import dict_to_struct, get_anonymous_user
from temba.values.models import Value
from unittest import skipIf
from uuid import uuid4
from .http import MockServer


mock_server = MockServer()


class TembaTestRunner(DiscoverRunner):
    """
    Adds the ability to exclude tests in given packages to the default test runner, and starts the mock server instance
    """
    def __init__(self, *args, **kwargs):
        settings.TESTING = True

        super(TembaTestRunner, self).__init__(*args, **kwargs)
        mock_server.start()

    def build_suite(self, *args, **kwargs):
        suite = super(TembaTestRunner, self).build_suite(*args, **kwargs)
        excluded = getattr(settings, 'TEST_EXCLUDE', [])
        if not getattr(settings, 'RUN_ALL_TESTS', False):
            tests = []
            for case in suite:
                pkg = case.__class__.__module__.split('.')[0]
                if pkg not in excluded:
                    tests.append(case)
            suite._tests = tests
        return suite

    def run_suite(self, suite, **kwargs):

        return super(TembaTestRunner, self).run_suite(suite, **kwargs)


def add_testing_flag_to_context(*args):
    return dict(testing=settings.TESTING)


def skip_if_no_flowserver(test):
    """
    Skip a test if flow server isn't configured
    """
    return skipIf(settings.FLOW_SERVER_URL is None, "this test can't be run without a flowserver instance")(test)


def also_in_flowserver(test_func):
    """
    Decorator to mark a test function as one that should also be run with the flow server
    """
    test_func._also_in_flowserver = True
    return test_func


class AddFlowServerTestsMeta(type):
    """
    Metaclass with adds new flowserver-based tests based on existing tests decorated with @also_in_flowserver. For
    example a test method called test_foo will become two test methods - the original test_foo which runs in the old
    engine, and a new one called test_foo_flowserver with is run using the flowserver.
    """
    def __new__(mcs, name, bases, dct):
        if settings.FLOW_SERVER_URL:
            new_tests = {}
            for key, val in six.iteritems(dct):
                if key.startswith('test_') and getattr(val, '_also_in_flowserver', False):
                    new_func = override_settings(FLOW_SERVER_AUTH_TOKEN='1234', FLOW_SERVER_FORCE=True)(val)
                    new_tests[key + '_flowserver'] = new_func
            dct.update(new_tests)

        return super(AddFlowServerTestsMeta, mcs).__new__(mcs, name, bases, dct)


class TembaTest(six.with_metaclass(AddFlowServerTestsMeta, SmartminTest)):
    def setUp(self):
        self.maxDiff = 4096
        self.mock_server = mock_server

        # if we are super verbose, turn on debug for sql queries
        if self.get_verbosity() > 2:
            settings.DEBUG = True

        # make sure we start off without any service users
        Group.objects.get(name='Service Users').user_set.clear()

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

        self.country.update_path()

        self.org = Org.objects.create(name="Temba", timezone=pytz.timezone("Africa/Kigali"), country=self.country,
                                      brand=settings.DEFAULT_BRAND, created_by=self.user, modified_by=self.user)

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

        # don't cache anon user between tests
        from temba import utils
        utils._anon_user = None
        clear_flow_users()

        # reset our simulation to False
        Contact.set_simulation(False)

    def create_inbound_msgs(self, recipient, count):
        for m in range(count):
            self.create_msg(contact=recipient, direction='I', text="Test %d" % m)

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
        for match in regex.finditer('Index Scan using (.*?) on (.*?) \(cost', six.text_type(plan), regex.DOTALL):
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
                print("")
                print(stat['query'])
                for table, index in stat['indexes']:
                    print('  Index Used: %s.%s' % (table, index))

                if not len(stat['indexes']):
                    print('  No Index Used')

            settings.DEBUG = False

        from temba.flows.models import clear_flow_users
        clear_flow_users()

        # clear any unused mock requests
        self.mock_server.mocked_requests = []

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
            for k, v in six.iteritems(substitutions):
                print('Replacing "%s" with "%s"' % (k, v))
                data = data.replace(k, str(v))

        return data

    def update_action_field(self, flow, action_uuid, key, value):
        action_json = self.get_action_json(flow, action_uuid)
        action_json[key] = value
        self.update_action_json(flow, action_json)

    def update_action_json(self, flow, action_json):
        """
        Given an action json_dict, replaces the existing action by uuid
        """
        flowdef = flow.as_json()
        for i, actionset in enumerate(flowdef['action_sets']):
            for j, prev_action in enumerate(actionset['actions']):
                if action_json['uuid'] == prev_action['uuid']:
                    flowdef['action_sets'][i]['actions'][j] = action_json
                    flow.update(flowdef, self.admin)
                    return
        self.fail("Couldn't find action with uuid %s" % action_json['uuid'])

    def get_action_json(self, flow, uuid):
        """
        Gets the action json dict from the given flow
        """
        flowdef = flow.as_json()
        for actionset in flowdef['action_sets']:
            for action in actionset['actions']:
                if action['uuid'] == uuid:
                    return action
        self.fail("Couldn't find action with uuid %s" % uuid)

    def get_flow(self, filename, substitutions=None):
        last_flow = Flow.objects.all().order_by('-pk').first()
        self.import_file(filename, substitutions=substitutions)

        if last_flow:
            flow = Flow.objects.filter(pk__gt=last_flow.pk).first()
            flow.org = self.org
            return flow

        flow = Flow.objects.all().order_by('-created_on').first()
        flow.org = self.org
        return flow

    def get_flow_json(self, filename, substitutions=None):
        data = self.get_import_json(filename, substitutions=substitutions)
        return json.loads(data)['flows'][0]

    def create_secondary_org(self, topup_size=None):
        self.admin2 = self.create_user("Administrator2")
        self.org2 = Org.objects.create(name="Trileet Inc.", timezone="Africa/Kigali", brand='rapidpro.io',
                                       created_by=self.admin2, modified_by=self.admin2)
        self.org2.administrators.add(self.admin2)
        self.admin2.set_org(self.org)

        self.org2.initialize(topup_size=topup_size)

    def create_contact(self, name=None, number=None, twitter=None, twitterid=None, urn=None, is_test=False, **kwargs):
        """
        Create a contact in the master test org
        """
        urns = []
        if number:
            urns.append(URN.from_tel(number))
        if twitter:
            urns.append(URN.from_twitter(twitter))
        if twitterid:
            urns.append(URN.from_twitterid(twitterid))
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

        return Contact.get_or_create_by_urns(**kwargs)

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

    def create_field(self, key, label, value_type=Value.TYPE_TEXT):
        return ContactField.objects.create(org=self.org, key=key, label=label, value_type=value_type,
                                           created_by=self.admin, modified_by=self.admin)

    def add_message(self, payload, text):
        """
        Add a message to the payload for the flow server using the default contact
        """
        payload['events'] = [{
            'type': 'msg_received',
            'msg': {
                'text': text,
                'uuid': six.text_type(uuid4()),
                'urn': 'tel:+12065551212',
                'created_on': timezone.now().isoformat(),
            },
            'created_on': timezone.now().isoformat(),
            'contact': payload['session']['contact']
        }]

    def get_replies(self, response):
        """
        Gets any replies in a response from the flow server as a list of strings
        """
        replies = []
        for log in response['log']:
            if 'event' in log:
                if log['event']['type'] == 'broadcast_created':
                    replies.append(log['event']['text'])
        return replies

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

        return Msg.objects.create(**kwargs)

    def create_flow(self, definition=None, **kwargs):
        if 'org' not in kwargs:
            kwargs['org'] = self.org
        if 'user' not in kwargs:
            kwargs['user'] = self.user
        if 'name' not in kwargs:
            kwargs['name'] = "Color Flow"

        flow = Flow.create(**kwargs)
        if not definition:
            # if definition isn't provided, generate simple single message flow
            node_uuid = str(uuid4())
            definition = {
                "version": 10,
                "flow_type": "F",
                "base_language": "eng",
                "entry": node_uuid,
                "action_sets": [
                    {
                        "uuid": node_uuid,
                        "x": 0,
                        "y": 0,
                        "actions": [
                            {
                                "msg": {"eng": "Hey everybody!"},
                                "media": {},
                                "send_all": False,
                                "type": "reply"
                            }
                        ],
                        "destination": None
                    }
                ],
                "rule_sets": [],
            }

        flow.version_number = definition['version']
        flow.save()

        json_flow = FlowRevision.migrate_definition(definition, flow)
        flow.update(json_flow)

        return flow

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

    def mockRequest(self, method, path_pattern, content, content_type='text/plain', status=200):
        return self.mock_server.mock_request(method, path_pattern, content, content_type, status)

    def assertOutbox(self, outbox_index, from_email, subject, body, recipients):
        self.assertEqual(len(mail.outbox), outbox_index + 1)
        email = mail.outbox[outbox_index]
        self.assertEqual(email.from_email, from_email)
        self.assertEqual(email.subject, subject)
        self.assertEqual(email.body, body)
        self.assertEqual(email.recipients(), recipients)

    def assertMockedRequest(self, mock_request, data=None, **headers):
        if not mock_request.requested:
            self.fail("expected %s %s to have been requested" % (mock_request.method, mock_request.path))

        if data is not None:
            self.assertEqual(mock_request.data, data)

        # check any provided header values
        for key, val in six.iteritems(headers):
            self.assertEqual(mock_request.headers.get(key.replace('_', '-')), val)

    def assertAllRequestsMade(self):
        if self.mock_server.mocked_requests:
            self.fail("test has %d unused mock requests: %s" % (len(mock_server.mocked_requests), mock_server.mocked_requests))

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

        rows = tuple(sheet.rows)

        actual_values = []
        for cell in rows[row_num]:
            actual = cell.value

            if actual is None:
                actual = ''

            if isinstance(actual, datetime):
                actual = actual

            actual_values.append(actual)

        for index, expected in enumerate(expected_values):
            actual = actual_values[index]

            if isinstance(expected, datetime):
                close_enough = abs(expected - actual) < timedelta(seconds=1)
                self.assertTrue(close_enough, "Datetime value %s doesn't match %s" % (expected, actual))
            else:
                self.assertEqual(expected, actual)

    def assertExcelSheet(self, sheet, rows, tz=None):
        """
        Asserts the row values in the given worksheet
        """
        self.assertEqual(len(list(sheet.rows)), len(rows))

        for r, row in enumerate(rows):
            self.assertExcelRow(sheet, r, row, tz)


class FlowFileTest(TembaTest):

    def setUp(self):
        super(FlowFileTest, self).setUp()
        self.contact = self.create_contact('Ben Haggerty', number='+12065552020')

    def assertInUserGroups(self, contact, group_names, only=False):

        truth = [g.name for g in contact.user_groups.all()]
        for name in group_names:
            self.assertIn(name, truth)

        if only:
            self.assertEqual(len(group_names), len(truth), 'Contact not found in expected group. expected: %s, was: %s' % (group_names, truth))
            other_groups = contact.user_groups.exclude(name__in=group_names)
            if other_groups:
                self.fail("Contact found in unexpected group: %s" % other_groups)

    def assertLastResponse(self, message):
        response = Msg.objects.filter(contact=self.contact).order_by('-created_on', '-pk').first()

        self.assertTrue("Missing response from contact.", response)
        self.assertEqual(message, response.text)

    def send(self, message, contact=None):
        if not contact:
            contact = self.contact
        if contact.is_test:
            Contact.set_simulation(True)
        incoming = self.create_msg(direction=INCOMING, contact=contact, contact_urn=contact.get_urn(), text=message)

        # evaluate the inbound message against our triggers first
        from temba.triggers.models import Trigger
        if not Trigger.find_and_handle(incoming):
            Flow.find_and_handle(incoming)
        return Msg.objects.filter(response_to=incoming).order_by('pk').first()

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

            incoming = self.create_msg(direction=INCOMING, contact=contact, contact_urn=contact.get_urn(), text=message)

            # start the flow
            if initiate_flow:
                flow.start(groups=[], contacts=[contact], restart_participants=restart_participants, start_msg=incoming)
            else:
                flow.start(groups=[], contacts=[contact], restart_participants=restart_participants)
                (handled, msgs) = Flow.find_and_handle(incoming)

                Msg.mark_handled(incoming)

                if assert_handle:
                    self.assertTrue(handled, "'%s' did not handle message as expected" % flow.name)
                else:
                    self.assertFalse(handled, "'%s' handled message, was supposed to ignore" % flow.name)

            # our message should have gotten a reply
            if assert_reply:
                replies = Msg.objects.filter(response_to=incoming).order_by('pk')
                self.assertGreaterEqual(len(replies), 1)

                if len(replies) == 1:
                    self.assertEqual(contact, replies.first().contact)
                    return replies.first().text

                # if it's more than one, send back a list of replies
                return [reply.text for reply in replies]

            else:
                # assert we got no reply
                replies = Msg.objects.filter(response_to=incoming).order_by('pk')
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
        channel = Channel.create(None, get_anonymous_user(), 'RW', 'A', name="Test Channel", address="0785551212",
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
        self.text = force_text(text)
        self.content = force_bytes(text)
        self.body = force_text(text)
        self.status_code = status_code
        self.headers = headers if headers else {}
        self.url = url
        self.ok = True
        self.cookies = dict()
        self.streaming = False
        self.charset = 'utf-8'

        # mock up a request object on our response as well
        self.request = dict_to_struct('MockRequest', dict(method=method, url=url, body='request body'))

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
        self.org.save(update_fields=('is_anon',))

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.org.is_anon = False
        self.org.save(update_fields=('is_anon',))
