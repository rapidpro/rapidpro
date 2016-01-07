from __future__ import unicode_literals

import json
import os
import redis
import shutil
import string
import time

from datetime import datetime
from django.conf import settings
from django.contrib.auth.models import User, Group
from django.core.urlresolvers import reverse
from django.db import connection
from django.test import LiveServerTestCase
from django.utils import timezone
from smartmin.tests import SmartminTest
from temba.contacts.models import Contact, ContactGroup, TEL_SCHEME, TWITTER_SCHEME
from temba.orgs.models import Org
from temba.channels.models import Channel
from temba.locations.models import AdminBoundary
from temba.flows.models import Flow, ActionSet, RuleSet, FLOW, RULE_SET, ACTION_SET
from temba.ivr.clients import TwilioClient
from temba.msgs.models import Msg, INCOMING
from temba.utils import dict_to_struct
from twilio.util import RequestValidator

def add_testing_flag_to_context(*args):
    return dict(testing=settings.TESTING)


def uuid(val):
    return '00000000-00000000-00000000-%08d' % val


class TembaTest(SmartminTest):

    def setUp(self):
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
        self.district2 = AdminBoundary.objects.create(osm_id='1711163', name='Kayonza', level=2, parent=self.state2)
        self.district3 = AdminBoundary.objects.create(osm_id='60485579', name='Kigali', level=2, parent=self.state1)
        self.district4 = AdminBoundary.objects.create(osm_id='1711142', name='Rwamagana', level=2, parent=self.state2)

        self.org = Org.objects.create(name="Temba", timezone="Africa/Kigali", country=self.country,
                                      created_by=self.user, modified_by=self.user)
        self.org.initialize()

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
                                      secret="12345", gcm_id="123")

        # reset our simulation to False
        Contact.set_simulation(False)

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
        shutil.rmtree('media/test_orgs', ignore_errors=True)

    def import_file(self, file, site='http://rapidpro.io', substitutions=None):

        handle = open('%s/test_flows/%s.json' % (settings.MEDIA_ROOT, file), 'r+')
        data = handle.read()
        handle.close()

        if substitutions:
            for k,v in substitutions.iteritems():
                print 'Replacing "%s" with "%s"' % (k,v)
                data = data.replace(k, str(v))

        # import all our bits
        self.org.import_app(json.loads(data), self.admin, site=site)

    def get_flow(self, filename, substitutions=None):
        last_flow = Flow.objects.all().order_by('-pk').first()
        self.import_file(filename, substitutions=substitutions)

        if last_flow:
            return Flow.objects.filter(pk__gt=last_flow.pk).first()

        return Flow.objects.all().order_by('-created_on').first()

    def get_flow_json(self, file):
        handle = open('%s/test_flows/%s.json' % (settings.MEDIA_ROOT, file), 'r+')
        data = handle.read()
        handle.close()
        return json.loads(data)['flows'][0]

    def create_secondary_org(self):
        self.admin2 = self.create_user("Administrator2")
        self.org2 = Org.objects.create(name="Trileet Inc.", timezone="Africa/Kigali", created_by=self.admin2, modified_by=self.admin2)
        self.org2.administrators.add(self.admin2)
        self.admin2.set_org(self.org)

        self.org2.initialize()

    def create_contact(self, name=None, number=None, twitter=None, is_test=False):
        """
        Create a contact in the master test org
        """
        urns = []
        if number:
            urns.append((TEL_SCHEME, number))
        if twitter:
            urns.append((TWITTER_SCHEME, twitter))

        if not name and not urns:  # pragma: no cover
            raise ValueError("Need a name or URN to create a contact")

        return Contact.get_or_create(self.org, self.user, name, urns=urns, is_test=is_test)

    def create_group(self, name, contacts):
        group = ContactGroup.create(self.org, self.user, name)
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
            kwargs['topup_id'] = kwargs['org'].decrement_credit()

        return Msg.objects.create(**kwargs)

    def create_flow(self, uuid_start=None):
        flow = Flow.create(self.org, self.admin, "Color Flow")
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
                                      actions=[dict(type='reply', msg=dict(base='What is your favorite color?'))]),
                                 dict(uuid=uuid(uuid_start + 2), x=2, y=2, destination=None,
                                      actions=[dict(type='reply', msg=dict(base='I love orange too! You said: @step.value which is category: @flow.color You are: @step.contact.tel SMS: @step Flow: @flow'))]),
                                 dict(uuid=uuid(uuid_start + 3), x=3, y=3, destination=None,
                                      actions=[dict(type='reply', msg=dict(base='Blue is sad. :('))]),
                                 dict(uuid=uuid(uuid_start + 4), x=4, y=4, destination=None,
                                      actions=[dict(type='reply', msg=dict(base='That is a funny color.'))])],
                    rule_sets=[dict(uuid=uuid(uuid_start + 5), x=5, y=5,
                                    label='color',
                                    finished_key=None,
                                    operand=None,
                                    webhook=None,
                                    webhook_action=None,
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

    def update_destination_no_check(self, flow, node, destination, rule=None):
        """ Update the destination without doing a cycle check """
        # look up our destination, we need this in order to set the correct destination_type
        destination_type = ACTION_SET
        action_destination = Flow.get_node(flow, destination, destination_type)
        if not action_destination:
            destination_type = RULE_SET
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


class FlowFileTest(TembaTest):

    def setUp(self):
        super(FlowFileTest, self).setUp()
        self.contact = self.create_contact('Ben Haggerty', '+12065552020')

    def assertLastResponse(self, message):
        response = Msg.objects.filter(contact=self.contact).order_by('-created_on', '-pk').first()

        self.assertTrue("Missing response from contact.", response)
        self.assertEquals(message, response.text)

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

                if assert_handle:
                    self.assertTrue(handled, "'%s' did not handle message as expected" % flow.name)
                else:
                    self.assertFalse(handled, "'%s' handled message, was supposed to ignore" % flow.name)

            # our message should have gotten a reply
            if assert_reply:
                replies = Msg.objects.filter(response_to=incoming).order_by('pk')
                self.assertGreaterEqual(len(replies), 1)

                if len(replies) == 1:
                    self.assertEquals(contact, replies.first().contact)
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


from selenium.webdriver.firefox.webdriver import WebDriver
from HTMLParser import HTMLParser

class MLStripper(HTMLParser):
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
        except:
            pass

        super(BrowserTest, cls).setUpClass()

    @classmethod
    def tearDownClass(cls):
        pass
        #cls.driver.quit()
        #super(BrowserTest, cls).tearDownClass()

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

    #def flow_basics(self):

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
        anon = User.objects.get(pk=settings.ANONYMOUS_USER_ID)
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

    def __init__(self, status_code, text, method='GET', url='http://foo.com/'):
        self.text = text
        self.content = text
        self.status_code = status_code

        # mock up a request object on our response as well
        self.request = dict_to_struct('MockRequest', dict(method=method, url=url))

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

    def __init__(self, sid, token):
        self.applications = MockTwilioClient.MockApplications()
        self.calls = MockTwilioClient.MockCalls()
        self.accounts = MockTwilioClient.MockAccounts()
        self.phone_numbers = MockTwilioClient.MockPhoneNumbers()
        self.sms = MockTwilioClient.MockSMS()
        self.auth = ['', 'FakeRequestToken']

    def validate(self, request):
        return True

    class MockShortCode():
        def __init__(self, short_code):
            self.short_code = short_code
            self.sid = "ShortSid"

    class MockShortCodes():
        def __init__(self, *args):
            pass

        def list(self, short_code=None):
            return [MockTwilioClient.MockShortCode(short_code)]

        def update(self, sid, **kwargs):
            print "Updating short code with sid %s" % sid

    class MockSMS():
        def __init__(self, *args):
            self.uri = "/SMS"
            self.short_codes = MockTwilioClient.MockShortCodes()

    class MockCall():
        def __init__(self, to=None, from_=None, url=None, status_callback=None):
            self.to = to
            self.from_ = from_
            self.url = url
            self.status_callback = status_callback
            self.sid = 'CallSid'

    class MockApplication():
        def __init__(self, friendly_name):
            self.friendly_name = friendly_name
            self.sid = 'TwilioTestSid'

    class MockPhoneNumber():
        def __init__(self, phone_number):
            self.phone_number = phone_number
            self.sid = 'PhoneNumberSid'

    class MockAccount():
        def __init__(self, account_type, auth_token='AccountToken'):
            self.type = account_type
            self.auth_token = auth_token
            self.sid = 'AccountSid'

    class MockAccounts():
        def __init__(self, *args):
            pass

        def get(self, account_type):
            return MockTwilioClient.MockAccount(account_type)

    class MockPhoneNumbers():
        def __init__(self, *args):
            pass

        def list(self, phone_number=None):
            return [MockTwilioClient.MockPhoneNumber(phone_number)]

        def update(self, sid, **kwargs):
            print "Updating phone number with sid %s" % sid

    class MockApplications():
        def __init__(self, *args):
            pass
        def list(self, friendly_name=None):
            return [MockTwilioClient.MockApplication(friendly_name)]

    class MockCalls():
        def __init__(self):
            pass

        def create(self, to=None, from_=None, url=None, status_callback=None):
            return MockTwilioClient.MockCall(to=to, from_=from_, url=url, status_callback=status_callback)

        def hangup(self, external_id):
            print "Hanging up %s on Twilio" % external_id

        def update(self, external_id, url):
            print "Updating call for %s to url %s" % (external_id, url)
