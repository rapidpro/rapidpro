from __future__ import unicode_literals

import random

from django.conf import settings
from django.core.urlresolvers import reverse
from django.db import connection, reset_queries
from django.utils import timezone
from temba.contacts.models import Contact, ContactField, ContactGroup, ContactURN, TEL_SCHEME, TWITTER_SCHEME
from temba.orgs.models import Org
from temba.channels.models import Channel
from temba.msgs.models import Broadcast, Call, Label, Msg, INCOMING, OUTGOING, PENDING
from temba.utils import truncate
from temba.values.models import Value, TEXT, DECIMAL
from tests import TembaTest
from timeit import default_timer


MAX_QUERIES_PRINT = 15


class SegmentProfiler(object):
    """
    Used in a with block to profile a segment of code
    """
    def __init__(self, test, name, db_profile):
        self.test = test
        self.test.segments.append(self)
        self.name = name
        self.db_profile = db_profile
        self.old_debug = settings.DEBUG

        self.time_total = 0.0
        self.time_queries = 0.0
        self.queries = []

    def __enter__(self):
        if self.db_profile:
            settings.DEBUG = True
            reset_queries()

        self.start_time = default_timer()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.time_total = default_timer() - self.start_time

        if self.db_profile:
            settings.DEBUG = self.old_debug
            self.queries = connection.queries
            reset_queries()

    def __unicode__(self):
        def format_query(q):
            return "Query [%s] %.3f secs" % (truncate(q['sql'], 60), float(q['time']))

        message = "Segment [%s] time: %.3f secs" % (self.name, self.time_total)
        if self.db_profile:
            num_queries = len(self.queries)
            time_db = sum([float(q['time']) for q in self.queries])

            message += ", %.3f secs db time, %d db queries" % (time_db, num_queries)

            # if we have only have a few queries, include them all in order of execution
            if len(self.queries) <= MAX_QUERIES_PRINT:
                message += ":"
                for query in self.queries:
                    message += "\n\t%s" % format_query(query)
            # if there are too many, only include slowest in order of duration
            else:
                message += ". %d slowest:" % MAX_QUERIES_PRINT
                slowest = sorted(list(self.queries), key=lambda q: float(q['time']), reverse=True)[:MAX_QUERIES_PRINT]
                for query in slowest:
                    message += "\n\t%s" % format_query(query)

        return message


class PerformanceTest(TembaTest):
    segments = []

    def setUp(self):
        self.clear_cache()

        self.user = self.create_user("tito")
        self.org = Org.objects.create(name="Nyaruka Ltd.", timezone="Africa/Kigali",
                                      created_by=self.user, modified_by=self.user)
        self.org.administrators.add(self.user)
        self.user.set_org(self.org)

        self.tel_mtn = Channel.objects.create(org=self.org, name="MTN", channel_type="A", role="SR",
                                              address="+250780000000", secret="12345", gcm_id="123",
                                              created_by=self.user, modified_by=self.user)
        self.tel_tigo = Channel.objects.create(org=self.org, name="Tigo", channel_type="A", role="SR",
                                               address="+250720000000", secret="23456", gcm_id="234",
                                               created_by=self.user, modified_by=self.user)
        self.tel_bulk = Channel.objects.create(org=self.org, name="Nexmo", channel_type="NX", role="S",
                                               parent=self.tel_tigo, secret="34567",
                                               created_by=self.user, modified_by=self.user)
        self.twitter = Channel.objects.create(org=self.org, name="Twitter", channel_type="TT", role="SR",
                                              created_by=self.user, modified_by=self.user)

        # for generating tuples of scheme, path and channel
        generate_tel_mtn = lambda num: (TEL_SCHEME, "+25078%07d" % (num + 1), self.tel_mtn)
        generate_tel_tigo = lambda num: (TEL_SCHEME, "+25072%07d" % (num + 1), self.tel_tigo)
        generate_twitter = lambda num: (TWITTER_SCHEME, "tweep_%d" % (num + 1), self.twitter)
        self.urn_generators = (generate_tel_mtn, generate_tel_tigo, generate_twitter)

        self.field_nick = ContactField.get_or_create(self.org, 'nick', 'Nickname', show_in_table=True, value_type=TEXT)
        self.field_age = ContactField.get_or_create(self.org, 'age', 'Age', show_in_table=True, value_type=DECIMAL)

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
            contacts.append(Contact.get_or_create(self.user, self.org, name, urns=[(scheme, path)]))
        return contacts

    def _create_groups(self, count, base_names, contacts):
        """
        Creates the given number of groups and fills them with contacts
        """
        groups = []
        num_bases = len(base_names)
        for g in range(0, count):
            name = '%s %d' % (base_names[g % num_bases], g + 1)
            group = ContactGroup.objects.create(org=self.org, name=name, is_active=True,
                                                created_by=self.user, modified_by=self.user)
            group.contacts.add(*contacts[(g % num_bases)::num_bases])
            groups.append(group)
        return groups

    def _create_broadcast(self, text, recipients):
        """
        Creates the a single broadcast to the given recipients (which can groups, contacts, URNs)
        """
        b = Broadcast.objects.create(org=self.org, text=text, created_by=self.user, modified_by=self.user)
        b.set_recipients(*recipients)
        return b

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
            msg = Msg.objects.create(contact=contact, contact_urn=contact_urn, org=self.org, channel=channel, text=text,
                                     direction=INCOMING, status=PENDING, created_on=date, queued_on=date)
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
            label = Label.objects.create(org=self.org, name=name, label_type='M')
            labels.append(label)

            assign_to = messages[(g % num_bases)::num_bases]
            for msg in assign_to:
                Msg.objects.get(pk=msg.pk).labels.add(label)
        return labels

    def _create_calls(self, count, channel, contacts):
        """
        Creates the given number of calls
        """
        calls = []
        date = timezone.now()
        for c in range(0, count):
            duration = random.randint(10, 30)
            contact = contacts[c % len(contacts)]
            calls.append(Call(channel=channel, org=self.org, contact=contact, time=date, duration=duration,
                              call_type='mo_call', created_by=self.user, modified_by=self.user))
        Call.objects.bulk_create(calls)
        return calls

    def _fetch_json(self, url):
        """
        GETs JSON from an API endpoint
        """
        resp = self.client.get(url, content_type="application/json", HTTP_X_FORWARDED_HTTPS='https')
        self.assertEquals(200, resp.status_code)
        return resp

    def test_contact_create(self):
        num_contacts = 1000

        with SegmentProfiler(self, "Creating new contacts", True):
            self._create_contacts(num_contacts, ["Bobby"])

        with SegmentProfiler(self, "Updating existing contacts", True):
            self._create_contacts(num_contacts, ["Jimmy"])

    def test_message_incoming(self):
        num_contacts = 300

        with SegmentProfiler(self, "Creating incoming messages from new contacts", False):
            for c in range(0, num_contacts):
                scheme, path, channel = self.urn_generators[c % len(self.urn_generators)](c)
                Msg.create_incoming(channel, (scheme, path), "Thanks #1", self.user)

        with SegmentProfiler(self, "Creating incoming messages from existing contacts", False):
            for c in range(0, num_contacts):
                scheme, path, channel = self.urn_generators[c % len(self.urn_generators)](c)
                Msg.create_incoming(channel, (scheme, path), "Thanks #2", self.user)

        # check messages for each channel
        incoming_total = 2 * num_contacts
        self.assertEqual(incoming_total / 3, Msg.objects.filter(direction=INCOMING, channel=self.tel_mtn).count())
        self.assertEqual(incoming_total / 3, Msg.objects.filter(direction=INCOMING, channel=self.tel_tigo).count())
        self.assertEqual(incoming_total / 3, Msg.objects.filter(direction=INCOMING, channel=self.twitter).count())

    def test_message_outgoing(self):
        num_contacts = 3000
        contacts = self._create_contacts(num_contacts, ["Bobby"])  # i.e. 1000 Bobbys of each URN type

        broadcast = self._create_broadcast("Hello message #1", contacts)

        with SegmentProfiler(self, "Sending broadcast to new contacts", True):
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

        with SegmentProfiler(self, "Sending broadcast when urns have channels", True):
            broadcast.send()

        broadcast = self._create_broadcast("Hello =contact #3", contacts)

        with SegmentProfiler(self, "Sending broadcast with expression", True):
            broadcast.send()

        # check messages for each channel
        outgoing_total = 3 * num_contacts
        self.assertEqual(outgoing_total / 3, Msg.objects.filter(direction=OUTGOING, channel=self.tel_mtn).count())
        self.assertEqual(outgoing_total / 3, Msg.objects.filter(direction=OUTGOING, channel=self.tel_bulk).count())
        self.assertEqual(outgoing_total / 3, Msg.objects.filter(direction=OUTGOING, channel=self.twitter).count())
        self.assertEqual(len(contacts) / 3, ContactURN.objects.filter(channel=self.tel_mtn).count())
        self.assertEqual(len(contacts) / 3, ContactURN.objects.filter(channel=self.tel_tigo).count())
        self.assertEqual(len(contacts) / 3, ContactURN.objects.filter(channel=self.twitter).count())

    def test_api(self):
        contacts = self._create_contacts(10000, ["Bobby", "Jimmy", "Mary"])
        self._create_groups(10, ["Bobbys", "Jims", "Marys"], contacts)

        self.login(self.user)

        with SegmentProfiler(self, "Fetch contacts from API", True):
            self._fetch_json('%s.json' % reverse('api.contacts'))

        with SegmentProfiler(self, "Fetch groups from API", True):
            self._fetch_json('%s.json' % reverse('api.contactgroups'))

        with SegmentProfiler(self, "Fetch groups from API (again)", True):
            self._fetch_json('%s.json' % reverse('api.contactgroups'))

    def test_omnibox(self):
        contacts = self._create_contacts(10000, ["Bobby", "Jimmy", "Mary"])
        self._create_groups(100, ["Bobbys", "Jims", "Marys"], contacts)

        self.login(self.user)

        with SegmentProfiler(self, "Omnibox with telephone search", True):
            self._fetch_json("%s?search=078" % reverse("contacts.contact_omnibox"))

    def test_contact_search(self):
        contacts = self._create_contacts(10000, ["Bobby", "Jimmy", "Mary"])
        self._create_values(contacts, self.field_nick, lambda c: c.name.lower().replace(' ', '_'))

        with SegmentProfiler(self, "Contact search with simple query", True):
            qs, is_complex = Contact.search(self.org, 'bob')

        self.assertEqual(3334, qs.count())
        self.assertEqual(False, is_complex)

        with SegmentProfiler(self, "Contact search with complex query", True):
            qs, is_complex = Contact.search(self.org, 'name = bob or tel has 078 or twitter = tweep_123 or nick is bob')

        self.assertEqual(3377, qs.count())
        self.assertEqual(True, is_complex)

    def test_group_counts(self):
        num_contacts = 10000
        contacts = self._create_contacts(num_contacts, ["Bobby", "Jimmy", "Mary"])
        groups = self._create_groups(10, ["Big Group"], contacts)

        with SegmentProfiler(self, "Contact group counts via regular queries", True):
            for group in groups:
                self.assertEqual(group.contacts.count(), num_contacts)
                self.assertEqual(group.contacts.count(), num_contacts)

        with SegmentProfiler(self, "Contact group counts with caching", True):
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

        with SegmentProfiler(self, "Contact list page", True):
            self.client.get(reverse('contacts.contact_list'))

        with SegmentProfiler(self, "Contact list page (repeat)", True):
            self.client.get(reverse('contacts.contact_list'))

        with SegmentProfiler(self, "Message inbox page", True):
            self.client.get(reverse('msgs.msg_inbox'))

        with SegmentProfiler(self, "Message inbox page (repeat)", True):
            self.client.get(reverse('msgs.msg_inbox'))
