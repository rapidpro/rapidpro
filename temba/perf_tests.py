from __future__ import unicode_literals

import json
import random

from django.core.urlresolvers import reverse
from django.utils import timezone
from temba.contacts.models import Contact, ContactField, ContactGroup, ContactURN, TEL_SCHEME, TWITTER_SCHEME
from temba.orgs.models import Org
from temba.channels.models import Channel, ChannelEvent, ChannelLog
from temba.flows.models import FlowRun, FlowStep
from temba.msgs.models import Broadcast, ExportMessagesTask, Label, Msg, INCOMING, OUTGOING, PENDING
from temba.utils import dict_to_struct
from temba.values.models import Value
from temba.utils.profiler import SegmentProfiler
from tests import TembaTest


API_INITIAL_REQUEST_QUERIES = 9  # num of required db hits for an initial API request
API_REQUEST_QUERIES = 7  # num of required db hits for a subsequent API request


class PerformanceTest(TembaTest):  # pragma: no cover
    segments = []

    def setUp(self):
        self.clear_cache()

        self.user = self.create_user("tito")
        self.admin = self.create_user("Administrator")
        self.org = Org.objects.create(name="Nyaruka Ltd.", timezone="Africa/Kigali",
                                      created_by=self.user, modified_by=self.user)
        self.org.initialize()

        self.org.administrators.add(self.admin)
        self.admin.set_org(self.org)
        self.org.administrators.add(self.user)
        self.user.set_org(self.org)

        self.tel_mtn = Channel.create(self.org, self.user, 'RW', 'A', name="MTN", address="+250780000000",
                                      secret="12345", gcm_id="123")
        self.tel_tigo = Channel.create(self.org, self.user, 'RW', 'A', name="Tigo", address="+250720000000",
                                       secret="23456", gcm_id="234")
        self.tel_bulk = Channel.create(self.org, self.user, 'RW', 'NX', name="Nexmo", parent=self.tel_tigo)
        self.twitter = Channel.create(self.org, self.user, None, 'TT', name="Twitter", address="billy_bob")

        # for generating tuples of scheme, path and channel
        def generate_tel_mtn(num):
            return TEL_SCHEME, "+25078%07d" % (num + 1), self.tel_mtn

        def generate_tel_tigo(num):
            return TEL_SCHEME, "+25072%07d" % (num + 1), self.tel_tigo

        def generate_twitter(num):
            return TWITTER_SCHEME, "tweep_%d" % (num + 1), self.twitter

        self.urn_generators = (generate_tel_mtn, generate_tel_tigo, generate_twitter)

        self.field_nick = ContactField.get_or_create(self.org, self.admin, 'nick', 'Nickname', show_in_table=True, value_type=Value.TYPE_TEXT)
        self.field_age = ContactField.get_or_create(self.org, self.admin, 'age', 'Age', show_in_table=True, value_type=Value.TYPE_DECIMAL)

    @classmethod
    def tearDownClass(cls):
        print "\n------------------ Segment Profiles ------------------"
        for segment in cls.segments:
            print unicode(segment)

    def _create_contacts(self, count, base_names):
        """
        Creates the given number of contacts with URNs of each type, and fields value for dob and nickname
        """
        contacts = []

        for c in range(0, count):
            name = '%s %d' % (base_names[c % len(base_names)], c + 1)
            scheme, path, channel = self.urn_generators[c % len(self.urn_generators)](c)
            contacts.append(Contact.get_or_create(self.org, self.user, name, urns=[':'.join([scheme, path])]))
        return contacts

    def _create_groups(self, count, base_names, contacts):
        """
        Creates the given number of groups and fills them with contacts
        """
        groups = []
        num_bases = len(base_names)
        for g in range(0, count):
            name = '%s %d' % (base_names[g % num_bases], g + 1)
            group = ContactGroup.create_static(self.org, self.user, name)
            group.contacts.add(*contacts[(g % num_bases)::num_bases])
            groups.append(ContactGroup.user_groups.get(pk=group.pk))
        return groups

    def _create_broadcast(self, text, recipients):
        """
        Creates the a single broadcast to the given recipients (which can groups, contacts, URNs)
        """
        return Broadcast.create(self.org, self.user, text, recipients)

    def _create_values(self, contacts, field, callback):
        """
        Creates a field value for each given contact (a lot faster than calling set_field)
        """
        values = []
        for contact in contacts:
            string_value = callback(contact)
            values.append(Value(contact=contact, org=self.org, contact_field=field, string_value=string_value))
        Value.objects.bulk_create(values)
        return values

    def _create_incoming(self, count, base_text, channel, contacts):
        """
        Creates the given number of incoming messages
        """
        messages = []
        date = timezone.now()
        for m in range(0, count):
            text = '%s %d' % (base_text, m + 1)
            contact = contacts[m % len(contacts)]
            contact_urn = contact.urn_objects.values()[0]
            msg = Msg.all_messages.create(contact=contact, contact_urn=contact_urn,
                                          org=self.org, channel=channel,
                                          text=text, direction=INCOMING, status=PENDING,
                                          created_on=date, queued_on=date)
            messages.append(msg)
        return messages

    def _create_labels(self, count, base_names, messages):
        """
        Creates the given number of labels and fills them with messages
        """
        labels = []
        num_bases = len(base_names)
        for g in range(0, count):
            name = '%s %d' % (base_names[g % num_bases], g + 1)
            label = Label.label_objects.create(org=self.org, name=name, folder=None,
                                               created_by=self.user, modified_by=self.user)
            labels.append(label)

            assign_to = messages[(g % num_bases)::num_bases]
            for msg in assign_to:
                Msg.all_messages.get(pk=msg.pk).labels.add(label)
        return labels

    def _create_calls(self, count, channel, contacts):
        """
        Creates the given number of missed call events
        """
        calls = []
        date = timezone.now()
        for c in range(0, count):
            duration = random.randint(10, 30)
            contact = contacts[c % len(contacts)]
            contact_urn = contact.urn_objects.values()[0]
            calls.append(ChannelEvent(channel=channel, org=self.org, event_type='mt_miss',
                                      contact=contact, contact_urn=contact_urn,
                                      time=date, duration=duration,
                                      created_by=self.user, modified_by=self.user))
        ChannelEvent.objects.bulk_create(calls)
        return calls

    def _create_runs(self, count, flow, contacts):
        """
        Creates the given number of flow runs
        """
        runs = []
        for c in range(0, count):
            contact = contacts[c % len(contacts)]
            runs.append(FlowRun.create(flow, contact.pk, db_insert=False))
        FlowRun.objects.bulk_create(runs)

        # add a step to each run
        steps = []
        for run in FlowRun.objects.all():
            steps.append(FlowStep(run=run, contact=run.contact, step_type='R', step_uuid=flow.entry_uuid, arrived_on=timezone.now()))
        FlowStep.objects.bulk_create(steps)

        return runs

    def _fetch_json(self, url):
        """
        GETs JSON from an API endpoint
        """
        resp = self.client.get(url, content_type="application/json", HTTP_X_FORWARDED_HTTPS='https')
        self.assertEquals(200, resp.status_code)
        return resp

    def _post_json(self, url, data):
        """
        POSTs JSON to an API endpoint
        """
        resp = self.client.post(url, json.dumps(data), content_type="application/json", HTTP_X_FORWARDED_HTTPS='https')
        self.assertEquals(201, resp.status_code)
        return resp

    def test_contact_create(self):
        num_contacts = 1000

        with SegmentProfiler("Creating new contacts", self, force_profile=True):
            self._create_contacts(num_contacts, ["Bobby"])

        with SegmentProfiler("Updating existing contacts", self, force_profile=True):
            self._create_contacts(num_contacts, ["Jimmy"])

    def test_message_incoming(self):
        num_contacts = 300

        with SegmentProfiler("Creating incoming messages from new contacts", self, False, force_profile=True):
            for c in range(0, num_contacts):
                scheme, path, channel = self.urn_generators[c % len(self.urn_generators)](c)
                Msg.create_incoming(channel, (scheme, path), "Thanks #1", self.user)

        with SegmentProfiler("Creating incoming messages from existing contacts", self, False, force_profile=True):
            for c in range(0, num_contacts):
                scheme, path, channel = self.urn_generators[c % len(self.urn_generators)](c)
                Msg.create_incoming(channel, (scheme, path), "Thanks #2", self.user)

        # check messages for each channel
        incoming_total = 2 * num_contacts
        self.assertEqual(incoming_total / 3, Msg.all_messages.filter(direction=INCOMING, channel=self.tel_mtn).count())
        self.assertEqual(incoming_total / 3, Msg.all_messages.filter(direction=INCOMING, channel=self.tel_tigo).count())
        self.assertEqual(incoming_total / 3, Msg.all_messages.filter(direction=INCOMING, channel=self.twitter).count())

    def test_message_outgoing(self):
        num_contacts = 3000
        contacts = self._create_contacts(num_contacts, ["Bobby"])  # i.e. 1000 Bobbys of each URN type

        broadcast = self._create_broadcast("Hello message #1", contacts)

        with SegmentProfiler("Sending broadcast to new contacts", self, True, force_profile=True):
            broadcast.send()

        # give all contact URNs an assigned channel as if they've been used for incoming
        for contact in contacts:
            urn = contact.get_urn()
            if urn.scheme == TWITTER_SCHEME:
                urn.channel = self.twitter
            elif urn.path.startswith('+25078'):
                urn.channel = self.tel_mtn
            elif urn.path.startswith('+25072'):
                urn.channel = self.tel_tigo
            urn.save()

        broadcast = self._create_broadcast("Hello message #2", contacts)

        with SegmentProfiler("Sending broadcast when urns have channels", self, True, force_profile=True):
            broadcast.send()

        broadcast = self._create_broadcast("Hello =contact #3", contacts)

        with SegmentProfiler("Sending broadcast with expression", self, True, force_profile=True):
            broadcast.send()

        # check messages for each channel
        outgoing_total = 3 * num_contacts
        self.assertEqual(outgoing_total / 3, Msg.all_messages.filter(direction=OUTGOING, channel=self.tel_mtn).count())
        self.assertEqual(outgoing_total / 3, Msg.all_messages.filter(direction=OUTGOING, channel=self.tel_bulk).count())
        self.assertEqual(outgoing_total / 3, Msg.all_messages.filter(direction=OUTGOING, channel=self.twitter).count())
        self.assertEqual(len(contacts) / 3, ContactURN.objects.filter(channel=self.tel_mtn).count())
        self.assertEqual(len(contacts) / 3, ContactURN.objects.filter(channel=self.tel_tigo).count())
        self.assertEqual(len(contacts) / 3, ContactURN.objects.filter(channel=self.twitter).count())

    def test_message_export(self):
        # create contacts
        contacts = self._create_contacts(100, ["Bobby", "Jimmy", "Mary"])

        # create messages and labels
        incoming = self._create_incoming(100, "Hello", self.tel_mtn, contacts)
        self._create_labels(10, ["My Label"], incoming)

        task = ExportMessagesTask.objects.create(org=self.org, created_by=self.user, modified_by=self.user)

        with SegmentProfiler("Export messages", self, True, force_profile=True):
            task.do_export()

    def test_flow_start(self):
        contacts = self._create_contacts(10000, ["Bobby", "Jimmy", "Mary"])
        groups = self._create_groups(10, ["Bobbys", "Jims", "Marys"], contacts)
        flow = self.create_flow()

        with SegmentProfiler("Starting a flow", self, True, force_profile=True):
            flow.start(groups, [])

        self.assertEqual(10000, Msg.all_messages.all().count())
        self.assertEqual(10000, FlowRun.objects.all().count())
        self.assertEqual(20000, FlowStep.objects.all().count())

    def test_api_contacts(self):
        contacts = self._create_contacts(300, ["Bobby", "Jimmy", "Mary"])
        self._create_groups(10, ["Bobbys", "Jims", "Marys"], contacts)

        self.login(self.user)
        self.clear_cache()

        with SegmentProfiler("Fetch first page of contacts from API", self,
                             assert_queries=API_INITIAL_REQUEST_QUERIES + 7, assert_tx=0, force_profile=True):
            self._fetch_json('%s.json' % reverse('api.v1.contacts'))

        # query count now cached

        with SegmentProfiler("Fetch second page of contacts from API", self,
                             assert_queries=API_REQUEST_QUERIES + 6, assert_tx=0, force_profile=True):
            self._fetch_json('%s.json?page=2' % reverse('api.v1.contacts'))

    def test_api_groups(self):
        contacts = self._create_contacts(300, ["Bobby", "Jimmy", "Mary"])
        self._create_groups(300, ["Bobbys", "Jims", "Marys"], contacts)

        self.login(self.user)
        self.clear_cache()

        with SegmentProfiler("Fetch first page of groups from API", self,
                             assert_queries=API_INITIAL_REQUEST_QUERIES + 2, assert_tx=0, force_profile=True):
            self._fetch_json('%s.json' % reverse('api.v1.contactgroups'))

    def test_api_messages(self):
        contacts = self._create_contacts(300, ["Bobby", "Jimmy", "Mary"])

        # create messages and labels
        incoming = self._create_incoming(300, "Hello", self.tel_mtn, contacts)
        self._create_labels(10, ["My Label"], incoming)

        self.login(self.user)
        self.clear_cache()

        with SegmentProfiler("Fetch first page of messages from API", self,
                             assert_queries=API_INITIAL_REQUEST_QUERIES + 3, assert_tx=0, force_profile=True):
            self._fetch_json('%s.json' % reverse('api.v1.messages'))

        # query count now cached

        with SegmentProfiler("Fetch second page of messages from API", self,
                             assert_queries=API_REQUEST_QUERIES + 2, assert_tx=0, force_profile=True):
            self._fetch_json('%s.json?page=2' % reverse('api.v1.messages'))

    def test_api_runs(self):
        flow = self.create_flow()
        contacts = self._create_contacts(50, ["Bobby", "Jimmy", "Mary"])
        self._create_runs(300, flow, contacts)

        self.login(self.user)
        self.clear_cache()

        with SegmentProfiler("Fetch first page of flow runs from API", self,
                             assert_queries=API_INITIAL_REQUEST_QUERIES + 7, assert_tx=0, force_profile=True):
            self._fetch_json('%s.json' % reverse('api.v1.runs'))

        # query count, terminal nodes and category nodes for the flow all now cached

        with SegmentProfiler("Fetch second page of flow runs from API", self,
                             assert_queries=API_REQUEST_QUERIES + 4, assert_tx=0, force_profile=True):
            self._fetch_json('%s.json?page=2' % reverse('api.v1.runs'))

        with SegmentProfiler("Create new flow runs via API endpoint", self, assert_tx=1, force_profile=True):
            data = {'flow': flow.pk, 'contact': [c.uuid for c in contacts]}
            self._post_json('%s.json' % reverse('api.v1.runs'), data)

    def test_omnibox(self):
        contacts = self._create_contacts(10000, ["Bobby", "Jimmy", "Mary"])
        self._create_groups(100, ["Bobbys", "Jims", "Marys"], contacts)

        self.login(self.user)

        with SegmentProfiler("Omnibox with telephone search", self, force_profile=True):
            self._fetch_json("%s?search=078" % reverse("contacts.contact_omnibox"))

    def test_contact_search(self):
        contacts = self._create_contacts(10000, ["Bobby", "Jimmy", "Mary"])
        self._create_values(contacts, self.field_nick, lambda c: c.name.lower().replace(' ', '_'))

        with SegmentProfiler("Contact search with simple query", self, force_profile=True):
            qs, is_complex = Contact.search(self.org, 'bob')

        self.assertEqual(3334, qs.count())
        self.assertEqual(False, is_complex)

        with SegmentProfiler("Contact search with complex query", self, force_profile=True):
            qs, is_complex = Contact.search(self.org, 'name = bob or tel has 078 or twitter = tweep_123 or nick is bob')

        self.assertEqual(3377, qs.count())
        self.assertEqual(True, is_complex)

    def test_group_counts(self):
        num_contacts = 10000
        contacts = self._create_contacts(num_contacts, ["Bobby", "Jimmy", "Mary"])
        groups = self._create_groups(10, ["Big Group"], contacts)

        with SegmentProfiler("Contact group counts via regular queries", self, force_profile=True):
            for group in groups:
                self.assertEqual(group.contacts.count(), num_contacts)
                self.assertEqual(group.contacts.count(), num_contacts)

        with SegmentProfiler("Contact group counts with caching", self, force_profile=True):
            for group in groups:
                self.assertEqual(group.get_member_count(), num_contacts)
                self.assertEqual(group.get_member_count(), num_contacts)

    def test_pages(self):
        # create contacts and groups
        contacts = self._create_contacts(10000, ["Bobby", "Jimmy", "Mary"])
        self._create_groups(10, ["My Group"], contacts)

        # create messages and labels
        incoming = self._create_incoming(10000, "Hello", self.tel_mtn, contacts)
        self._create_labels(10, ["My Label"], incoming)

        # create calls
        self._create_calls(10000, self.tel_mtn, contacts)

        # populate nickname and age fields
        self._create_values(contacts, self.field_nick, lambda c: c.name.lower().replace(' ', '_'))
        self._create_values(contacts, self.field_age, lambda c: (c.id % 80) + 1)

        self.login(self.user)

        with SegmentProfiler("Contact list page", self, force_profile=True):
            self.client.get(reverse('contacts.contact_list'))

        with SegmentProfiler("Contact list page (repeat)", self, force_profile=True):
            self.client.get(reverse('contacts.contact_list'))

        with SegmentProfiler("Message inbox page", self, force_profile=True):
            self.client.get(reverse('msgs.msg_inbox'))

        with SegmentProfiler("Message inbox page (repeat)", self, force_profile=True):
            self.client.get(reverse('msgs.msg_inbox'))

    def test_channellog(self):
        contact = self.create_contact("Test", "+250788383383")
        msg = Msg.create_outgoing(self.org, self.admin, contact, "This is a test message")
        msg = dict_to_struct('MockMsg', msg.as_task_json())

        with SegmentProfiler("Channel Log inserts (10,000)", self, force_profile=True):
            for i in range(10000):
                ChannelLog.log_success(msg, "Sent Message", method="GET", url="http://foo",
                                       request="GET http://foo", response="Ok", response_status="201")
