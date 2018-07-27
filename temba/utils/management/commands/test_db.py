# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import json
import math
import pytz
import random
import resource
import six
import sys
import time
import uuid

from collections import defaultdict
from datetime import timedelta
from django.conf import settings
from django.contrib.auth.models import User
from django.core.management import BaseCommand, CommandError
from django.core.management.base import CommandParser
from django.db import connection, transaction
from django.utils import timezone
from django_redis import get_redis_connection
from subprocess import check_call, CalledProcessError
from temba.channels.models import Channel
from temba.channels.tasks import squash_channelcounts
from temba.contacts.models import Contact, ContactField, ContactGroup, ContactURN, ContactGroupCount, URN, TEL_SCHEME, TWITTER_SCHEME
from temba.flows.models import FlowStart, FlowRun
from temba.flows.tasks import squash_flowpathcounts, squash_flowruncounts
from temba.locations.models import AdminBoundary
from temba.msgs.models import Label, Msg
from temba.msgs.tasks import squash_labelcounts
from temba.orgs.models import Org
from temba.orgs.tasks import squash_topupcredits
from temba.utils import chunk_list
from temba.utils.dates import ms_to_datetime, datetime_to_str, datetime_to_ms
from temba.values.models import Value


# maximum age in days of database content
CONTENT_AGE = 3 * 365

# every user will have this password including the superuser
USER_PASSWORD = "Qwerty123"

# database dump containing admin boundary records
LOCATIONS_DUMP = 'test-data/nigeria.bin'

# organization names are generated from these components
ORG_NAMES = (
    ("UNICEF", "WHO", "WFP", "UNESCO", "UNHCR", "UNITAR", "FAO", "UNEP", "UNAIDS", "UNDAF"),
    ("Nigeria", "Chile", "Indonesia", "Rwanda", "Mexico", "Zambia", "India", "Brazil", "Sudan", "Mozambique")
)

# the users, channels, groups, labels and fields to create for each organization
USERS = (
    {'username': "admin%d", 'email': "org%d_admin@example.com", 'role': 'administrators'},
    {'username': "editor%d", 'email': "org%d_editor@example.com", 'role': 'editors'},
    {'username': "viewer%d", 'email': "org%d_viewer@example.com", 'role': 'viewers'},
    {'username': "surveyor%d", 'email': "org%d_surveyor@example.com", 'role': 'surveyors'},
)
CHANNELS = (
    {'name': "Android", 'channel_type': Channel.TYPE_ANDROID, 'scheme': 'tel', 'address': "1234"},
    {'name': "Nexmo", 'channel_type': 'NX', 'scheme': 'tel', 'address': "2345"},
    {'name': "Twitter", 'channel_type': 'TT', 'scheme': 'twitter', 'address': "my_handle"},
)
FIELDS = (
    {'key': 'gender', 'label': "Gender", 'value_type': Value.TYPE_TEXT},
    {'key': 'age', 'label': "Age", 'value_type': Value.TYPE_DECIMAL},
    {'key': 'joined', 'label': "Joined On", 'value_type': Value.TYPE_DATETIME},
    {'key': 'ward', 'label': "Ward", 'value_type': Value.TYPE_WARD},
    {'key': 'district', 'label': "District", 'value_type': Value.TYPE_DISTRICT},
    {'key': 'state', 'label': "State", 'value_type': Value.TYPE_STATE},
)
GROUPS = (
    {'name': "Reporters", 'query': None, 'member': 0.95},  # member is either a probability or callable
    {'name': "Farmers", 'query': None, 'member': 0.5},
    {'name': "Doctors", 'query': None, 'member': 0.4},
    {'name': "Teachers", 'query': None, 'member': 0.3},
    {'name': "Drivers", 'query': None, 'member': 0.2},
    {'name': "Testers", 'query': None, 'member': 0.1},
    {'name': "Empty", 'query': None, 'member': 0.0},
    {'name': "Youth (Dynamic)", 'query': 'age <= 18', 'member': lambda c: c['age'] and c['age'] <= 18},
    {'name': "Unregistered (Dynamic)", 'query': 'joined = ""', 'member': lambda c: not c['joined']},
    {'name': "Districts (Dynamic)", 'query': 'district=Faskari or district=Zuru or district=Anka',
     'member': lambda c: c['district'] and c['district'].name in ("Faskari", "Zuru", "Anka")},
)
LABELS = ("Reporting", "Testing", "Youth", "Farming", "Health", "Education", "Trade", "Driving", "Building", "Spam")
FLOWS = (
    {'name': "Favorites", 'file': "favorites.json", 'templates': (
        ["blue", "mutzig", "bob"],
        ["orange", "green", "primus", "jeb"],
        ["red", "skol", "rowan"],
        ["red", "turbo", "nic"]
    )},
    {'name': "SMS Form", 'file': "sms_form.json", 'templates': (["22 F Seattle"], ["35 M MIAMI"])},
    {'name': "Pick a Number", 'file': "pick_a_number.json", 'templates': (["1"], ["4"], ["5"], ["7"], ["8"])}
)

# contact names are generated from these components
CONTACT_NAMES = (
    ("", "Anne", "Bob", "Cathy", "Dave", "Evan", "Freda", "George", "Hallie", "Igor"),
    ("", "Jameson", "Kardashian", "Lopez", "Mooney", "Newman", "O'Shea", "Poots", "Quincy", "Roberts"),
)
CONTACT_LANGS = (None, "eng", "fre", "spa", "kin")
CONTACT_HAS_TEL_PROB = 0.9  # 9/10 contacts have a phone number
CONTACT_HAS_TWITTER_PROB = 0.1  # 1/10 contacts have a twitter handle
CONTACT_IS_STOPPED_PROB = 0.01  # 1/100 contacts are stopped
CONTACT_IS_BLOCKED_PROB = 0.01  # 1/100 contacts are blocked
CONTACT_IS_DELETED_PROB = 0.005  # 1/200 contacts are deleted
CONTACT_HAS_FIELD_PROB = 0.8  # 8/10 fields set for each contact

RUN_RESPONSE_PROB = 0.1  # 1/10 runs will be responded to
INBOX_MESSAGES = (("What is", "I like", "No"), ("beer", "tea", "coffee"), ("thank you", "please", "today"))


class Command(BaseCommand):
    COMMAND_GENERATE = 'generate'
    COMMAND_SIMULATE = 'simulate'

    help = "Generates a database suitable for performance testing"

    def add_arguments(self, parser):
        cmd = self
        subparsers = parser.add_subparsers(dest='command', help='Command to perform',
                                           parser_class=lambda **kw: CommandParser(cmd, **kw))

        gen_parser = subparsers.add_parser('generate', help='Generates a clean testing database')
        gen_parser.add_argument('--orgs', type=int, action='store', dest='num_orgs', default=10)
        gen_parser.add_argument('--contacts', type=int, action='store', dest='num_contacts', default=10000)
        gen_parser.add_argument('--seed', type=int, action='store', dest='seed', default=None)

        sim_parser = subparsers.add_parser('simulate', help='Simulates activity on an existing database')
        sim_parser.add_argument('--org', type=int, action='store', dest='org_id', default=None)
        sim_parser.add_argument('--runs', type=int, action='store', dest='num_runs', default=1000)
        sim_parser.add_argument('--flow', type=str, action='store', dest='flow_name', default=None)
        sim_parser.add_argument('--seed', type=int, action='store', dest='seed', default=None)

    def handle(self, command, *args, **kwargs):
        start = time.time()

        if command == self.COMMAND_GENERATE:
            self.handle_generate(kwargs['num_orgs'], kwargs['num_contacts'], kwargs['seed'])
        else:
            self.handle_simulate(kwargs['num_runs'], kwargs['org_id'], kwargs['flow_name'], kwargs['seed'])

        time_taken = time.time() - start
        self._log("Completed in %d secs, peak memory usage: %d MiB\n" % (int(time_taken), int(self.peak_memory())))

    def handle_generate(self, num_orgs, num_contacts, seed):
        """
        Creates a clean database
        """
        seed = self.configure_random(num_orgs, seed)

        self._log("Generating random base database (seed=%d)...\n" % seed)

        try:
            has_data = Org.objects.exists()
        except Exception:  # pragma: no cover
            raise CommandError("Run migrate command first to create database tables")
        if has_data:
            raise CommandError("Can't generate content in non-empty database.")

        self.batch_size = 5000

        # the timespan being modelled by this database
        self.db_ends_on = timezone.now()
        self.db_begins_on = self.db_ends_on - timedelta(days=CONTENT_AGE)

        # this is a new database so clear out redis
        self._log("Clearing out Redis cache... ")
        r = get_redis_connection()
        r.flushdb()
        self._log(self.style.SUCCESS("OK") + '\n')

        superuser = User.objects.create_superuser("root", "root@example.com", USER_PASSWORD)

        country, locations = self.load_locations(LOCATIONS_DUMP)
        orgs = self.create_orgs(superuser, country, num_orgs)
        self.create_users(orgs)
        self.create_channels(orgs)
        self.create_fields(orgs)
        self.create_groups(orgs)
        self.create_labels(orgs)
        self.create_flows(orgs)
        self.create_contacts(orgs, locations, num_contacts)

    def handle_simulate(self, num_runs, org_id, flow_name, seed):
        """
        Prepares to resume simulating flow activity on an existing database
        """
        self._log("Resuming flow activity simulation on existing database...\n")

        orgs = Org.objects.order_by('id')
        if org_id:
            orgs = orgs.filter(id=org_id)

        if not orgs:
            raise CommandError("Can't simulate activity on an empty database")

        self.configure_random(len(orgs), seed)

        # in real life Nexmo messages are throttled, but that's not necessary for this simulation
        Channel.get_type_from_code('NX').max_tps = None

        inputs_by_flow_name = {f['name']: f['templates'] for f in FLOWS}

        self._log("Preparing existing orgs... ")

        for org in orgs:
            flows = org.flows.order_by('id')

            if flow_name:
                flows = flows.filter(name=flow_name)
            flows = list(flows)

            for flow in flows:
                flow.input_templates = inputs_by_flow_name[flow.name]

            org.cache = {
                'users': list(org.get_org_users().order_by('id')),
                'channels': list(org.channels.order_by('id')),
                'groups': list(ContactGroup.user_groups.filter(org=org).order_by('id')),
                'flows': flows,
                'contacts': list(org.org_contacts.values_list('id', flat=True)),  # only ids to save memory
                'activity': None
            }

        self._log(self.style.SUCCESS("OK") + '\n')

        self.simulate_activity(orgs, num_runs)

    def configure_random(self, num_orgs, seed=None):
        if not seed:
            seed = random.randrange(0, 65536)

        self.random = random.Random(seed)

        # monkey patch uuid4 so it returns the same UUIDs for the same seed, see https://github.com/joke2k/faker/issues/484#issuecomment-287931101
        from temba.utils import models
        models.uuid4 = lambda: uuid.UUID(int=(self.random.getrandbits(128) | (1 << 63) | (1 << 78)) & (~(1 << 79) & ~(1 << 77) & ~(1 << 76) & ~(1 << 62)))

        # We want a variety of large and small orgs so when allocating content like contacts and messages, we apply a
        # bias toward the beginning orgs. if there are N orgs, then the amount of content the first org will be
        # allocated is (1/N) ^ (1/bias). This sets the bias so that the first org will get ~50% of the content:
        self.org_bias = math.log(1.0 / num_orgs, 0.5)

        return seed

    def load_locations(self, path):
        """
        Loads admin boundary records from the given dump of that table
        """
        self._log("Loading locations from %s... " % path)

        # load dump into current db with pg_restore
        db_config = settings.DATABASES['default']
        try:
            check_call(
                'export PGPASSWORD=%s && pg_restore -h %s -U%s -w -d %s %s' %
                (db_config['PASSWORD'], db_config['HOST'], db_config['USER'], db_config['NAME'], path),
                shell=True
            )
        except CalledProcessError:  # pragma: no cover
            raise CommandError("Error occurred whilst calling pg_restore to load locations dump")

        # fetch as tuples of (WARD, DISTRICT, STATE)
        wards = AdminBoundary.objects.filter(level=3).prefetch_related('parent', 'parent__parent')
        locations = [(w, w.parent, w.parent.parent) for w in wards]

        country = AdminBoundary.objects.filter(level=0).get()

        self._log(self.style.SUCCESS("OK") + '\n')
        return country, locations

    def create_orgs(self, superuser, country, num_total):
        """
        Creates and initializes the orgs
        """
        self._log("Creating %d orgs... " % num_total)

        org_names = ['%s %s' % (o1, o2) for o2 in ORG_NAMES[1] for o1 in ORG_NAMES[0]]
        self.random.shuffle(org_names)

        orgs = []
        for o in range(num_total):
            orgs.append(Org(name=org_names[o % len(org_names)], timezone=self.random.choice(pytz.all_timezones),
                            brand='rapidpro.io', country=country,
                            created_on=self.db_begins_on, created_by=superuser, modified_by=superuser))
        Org.objects.bulk_create(orgs)
        orgs = list(Org.objects.order_by('id'))

        self._log(self.style.SUCCESS("OK") + "\nInitializing orgs... ")

        for o, org in enumerate(orgs):
            org.initialize(topup_size=max((1000 - o), 1) * 1000)

            # we'll cache some metadata on each org as it's created to save re-fetching things
            org.cache = {
                'users': [],
                'fields': {},
                'groups': [],
                'system_groups': {g.group_type: g for g in ContactGroup.system_groups.filter(org=org)},
            }

        self._log(self.style.SUCCESS("OK") + '\n')
        return orgs

    def create_users(self, orgs):
        """
        Creates a user of each type for each org
        """
        self._log("Creating %d users... " % (len(orgs) * len(USERS)))

        # create users for each org
        for org in orgs:
            for u in USERS:
                user = User.objects.create_user(u['username'] % org.id, u['email'] % org.id, USER_PASSWORD)
                getattr(org, u['role']).add(user)
                user.set_org(org)
                org.cache['users'].append(user)

        self._log(self.style.SUCCESS("OK") + '\n')

    def create_channels(self, orgs):
        """
        Creates the channels for each org
        """
        self._log("Creating %d channels... " % (len(orgs) * len(CHANNELS)))

        for org in orgs:
            user = org.cache['users'][0]
            for c in CHANNELS:
                Channel.objects.create(org=org, name=c['name'], channel_type=c['channel_type'],
                                       address=c['address'], schemes=[c['scheme']],
                                       created_by=user, modified_by=user)

        self._log(self.style.SUCCESS("OK") + '\n')

    def create_fields(self, orgs):
        """
        Creates the contact fields for each org
        """
        self._log("Creating %d fields... " % (len(orgs) * len(FIELDS)))

        for org in orgs:
            user = org.cache['users'][0]
            for f in FIELDS:
                field = ContactField.objects.create(org=org, key=f['key'], label=f['label'],
                                                    value_type=f['value_type'], show_in_table=True,
                                                    created_by=user, modified_by=user)
                org.cache['fields'][f['key']] = field

        self._log(self.style.SUCCESS("OK") + '\n')

    def create_groups(self, orgs):
        """
        Creates the contact groups for each org
        """
        self._log("Creating %d groups... " % (len(orgs) * len(GROUPS)))

        for org in orgs:
            user = org.cache['users'][0]
            for g in GROUPS:
                if g['query']:
                    group = ContactGroup.create_dynamic(org, user, g['name'], g['query'])
                else:
                    group = ContactGroup.user_groups.create(org=org, name=g['name'], created_by=user, modified_by=user)
                group.member = g['member']
                group.count = 0
                org.cache['groups'].append(group)

        self._log(self.style.SUCCESS("OK") + '\n')

    def create_labels(self, orgs):
        """
        Creates the message labels for each org
        """
        self._log("Creating %d labels... " % (len(orgs) * len(LABELS)))

        for org in orgs:
            user = org.cache['users'][0]
            for name in LABELS:
                Label.label_objects.create(org=org, name=name, created_by=user, modified_by=user)

        self._log(self.style.SUCCESS("OK") + '\n')

    def create_flows(self, orgs):
        """
        Creates the flows for each org
        """
        self._log("Creating %d flows... " % (len(orgs) * len(FLOWS)))

        for org in orgs:
            user = org.cache['users'][0]
            for f in FLOWS:
                with open('media/test_flows/' + f['file'], 'r') as flow_file:
                    org.import_app(json.load(flow_file), user)

        self._log(self.style.SUCCESS("OK") + '\n')

    def create_contacts(self, orgs, locations, num_contacts):
        """
        Creates test and regular contacts for this database. Returns tuples of org, contact id and the preferred urn
        id to avoid trying to hold all contact and URN objects in memory.
        """
        group_counts = defaultdict(int)

        self._log("Creating %d test contacts..." % (len(orgs) * len(USERS)))

        for org in orgs:
            test_contacts = []
            for user in org.cache['users']:
                test_contacts.append(Contact.get_test_contact(user))
            org.cache['test_contacts'] = test_contacts

        self._log(self.style.SUCCESS("OK") + '\n')
        self._log("Creating %d regular contacts...\n" % num_contacts)

        # disable table triggers to speed up insertion and in the case of contact group m2m, avoid having an unsquashed
        # count row for every contact
        with DisableTriggersOn(Contact, ContactURN, Value, ContactGroup.contacts.through):
            names = [('%s %s' % (c1, c2)).strip() for c2 in CONTACT_NAMES[1] for c1 in CONTACT_NAMES[0]]
            names = [n if n else None for n in names]

            batch_num = 1
            for index_batch in chunk_list(six.moves.xrange(num_contacts), self.batch_size):
                batch = []

                # generate flat representations and contact objects for this batch
                for c_index in index_batch:  # pragma: no cover
                    org = self.random_org(orgs)
                    name = self.random_choice(names)
                    location = self.random_choice(locations) if self.probability(CONTACT_HAS_FIELD_PROB) else None
                    created_on = self.timeline_date(c_index / num_contacts)

                    c = {
                        'org': org,
                        'user': org.cache['users'][0],
                        'name': name,
                        'groups': [],
                        'tel': '+2507%08d' % c_index if self.probability(CONTACT_HAS_TEL_PROB) else None,
                        'twitter': '%s%d' % (name.replace(' ', '_').lower() if name else 'tweep', c_index) if self.probability(CONTACT_HAS_TWITTER_PROB) else None,
                        'gender': self.random_choice(('M', 'F')) if self.probability(CONTACT_HAS_FIELD_PROB) else None,
                        'age': self.random.randint(16, 80) if self.probability(CONTACT_HAS_FIELD_PROB) else None,
                        'joined': self.random_date() if self.probability(CONTACT_HAS_FIELD_PROB) else None,
                        'ward': location[0] if location else None,
                        'district': location[1] if location else None,
                        'state': location[2] if location else None,
                        'language': self.random_choice(CONTACT_LANGS),
                        'is_stopped': self.probability(CONTACT_IS_STOPPED_PROB),
                        'is_blocked': self.probability(CONTACT_IS_BLOCKED_PROB),
                        'is_active': self.probability(1 - CONTACT_IS_DELETED_PROB),
                        'created_on': created_on,
                        'modified_on': self.random_date(created_on, self.db_ends_on),
                    }

                    # work out which system groups this contact belongs to
                    if c['is_active']:
                        if not c['is_blocked'] and not c['is_stopped']:
                            c['groups'].append(org.cache['system_groups'][ContactGroup.TYPE_ALL])
                        if c['is_blocked']:
                            c['groups'].append(org.cache['system_groups'][ContactGroup.TYPE_BLOCKED])
                        if c['is_stopped']:
                            c['groups'].append(org.cache['system_groups'][ContactGroup.TYPE_STOPPED])

                    # let each user group decide if it is taking this contact
                    for g in org.cache['groups']:
                        if g.member(c) if callable(g.member) else self.probability(g.member):
                            c['groups'].append(g)

                    # track changes to group counts
                    for g in c['groups']:
                        group_counts[g] += 1

                    batch.append(c)

                self._create_contact_batch(batch)
                self._log(" > Created batch %d of %d\n" % (batch_num, max(num_contacts // self.batch_size, 1)))
                batch_num += 1

        # create group count records manually
        counts = []
        for group, count in group_counts.items():
            counts.append(ContactGroupCount(group=group, count=count, is_squashed=True))
            group.count = count
        ContactGroupCount.objects.bulk_create(counts)

    def _create_contact_batch(self, batch):
        """
        Bulk creates a batch of contacts from flat representations
        """
        for c in batch:
            c['object'] = Contact(org=c['org'], name=c['name'], language=c['language'],
                                  is_stopped=c['is_stopped'], is_blocked=c['is_blocked'],
                                  is_active=c['is_active'],
                                  created_by=c['user'], created_on=c['created_on'],
                                  modified_by=c['user'], modified_on=c['modified_on'])
        Contact.objects.bulk_create([c['object'] for c in batch])

        # now that contacts have pks, bulk create the actual URN, value and group membership objects
        batch_urns = []
        batch_values = []
        batch_memberships = []

        for c in batch:
            org = c['org']
            c['urns'] = []

            if c['tel']:
                c['urns'].append(ContactURN(org=org, contact=c['object'], priority=50, scheme=TEL_SCHEME,
                                            path=c['tel'], identity=URN.from_tel(c['tel'])))
            if c['twitter']:
                c['urns'].append(ContactURN(org=org, contact=c['object'], priority=50, scheme=TWITTER_SCHEME,
                                            path=c['twitter'], identity=URN.from_twitter(c['twitter'])))
            if c['gender']:
                batch_values.append(Value(org=org, contact=c['object'], contact_field=org.cache['fields']['gender'],
                                          string_value=c['gender']))
            if c['age']:
                batch_values.append(Value(org=org, contact=c['object'], contact_field=org.cache['fields']['age'],
                                          string_value=str(c['age']), decimal_value=c['age']))
            if c['joined']:
                batch_values.append(Value(org=org, contact=c['object'], contact_field=org.cache['fields']['joined'],
                                          string_value=datetime_to_str(c['joined']), datetime_value=c['joined']))
            if c['ward']:
                batch_values.append(Value(org=org, contact=c['object'], contact_field=org.cache['fields']['ward'],
                                          string_value=c['ward'].name, location_value=c['ward']))
            if c['district']:
                batch_values.append(Value(org=org, contact=c['object'], contact_field=org.cache['fields']['district'],
                                          string_value=c['district'].name, location_value=c['district']))
            if c['state']:
                batch_values.append(Value(org=org, contact=c['object'], contact_field=org.cache['fields']['state'],
                                          string_value=c['state'].name, location_value=c['state']))
            for g in c['groups']:
                batch_memberships.append(ContactGroup.contacts.through(contact=c['object'], contactgroup=g))

            batch_urns += c['urns']

        ContactURN.objects.bulk_create(batch_urns)
        Value.objects.bulk_create(batch_values)
        ContactGroup.contacts.through.objects.bulk_create(batch_memberships)

    def simulate_activity(self, orgs, num_runs):
        self._log("Starting simulation. Ctrl+C to cancel...\n")
        start = time.time()

        runs = 0
        while runs < num_runs:
            try:
                with transaction.atomic():
                    # make sure every org has an active flow
                    for org in orgs:
                        if not org.cache['activity']:
                            self.start_flow_activity(org)

                with transaction.atomic():
                    org = self.random_org(orgs)

                    if self.probability(0.1):
                        self.create_unsolicited_incoming(org)
                    else:
                        self.create_flow_run(org)
                        runs += 1

            except KeyboardInterrupt:
                self._log("Shutting down...\n")
                break

        self._log("Simulation ran for %d seconds\n" % int(time.time() - start))

        squash_channelcounts()
        squash_flowpathcounts()
        squash_flowruncounts()
        squash_topupcredits()
        squash_labelcounts()

    def start_flow_activity(self, org):
        assert not org.cache['activity']

        user = org.cache['users'][0]
        flow = self.random_choice(org.cache['flows'])

        if self.probability(0.9):
            # start a random group using a flow start
            group = self.random_choice(org.cache['groups'])
            contacts_started = list(group.contacts.values_list('id', flat=True))

            self._log(" > Starting flow %s for group %s (%d) in org %s\n"
                      % (flow.name, group.name, len(contacts_started), org.name))

            start = FlowStart.create(flow, user, groups=[group], restart_participants=True)
            start.start()
        else:
            # start a random individual without a flow start
            if not org.cache['contacts']:
                return

            contact = Contact.objects.get(id=self.random_choice(org.cache['contacts']))
            contacts_started = [contact.id]

            self._log(" > Starting flow %s for contact #%d in org %s\n" % (flow.name, contact.id, org.name))

            flow.start([], [contact], restart_participants=True)

        org.cache['activity'] = {'flow': flow, 'unresponded': contacts_started, 'started': list(contacts_started)}

    def end_flow_activity(self, org):
        self._log(" > Ending flow %s for in org %s\n" % (org.cache['activity']['flow'].name, org.name))

        org.cache['activity'] = None

        runs = FlowRun.objects.filter(org=org, is_active=True)
        FlowRun.bulk_exit(runs, FlowRun.EXIT_TYPE_EXPIRED)

    def create_flow_run(self, org):
        activity = org.cache['activity']
        flow = activity['flow']

        if activity['unresponded']:
            contact_id = self.random_choice(activity['unresponded'])
            activity['unresponded'].remove(contact_id)

            contact = Contact.objects.get(id=contact_id)
            urn = contact.urns.first()

            if urn:
                self._log(" > Receiving flow responses for flow %s in org %s\n" % (flow.name, flow.org.name))

                inputs = self.random_choice(flow.input_templates)

                for text in inputs:
                    channel = flow.org.cache['channels'][0]
                    Msg.create_incoming(channel, six.text_type(urn), text)

        # if more than 10% of contacts have responded, consider flow activity over
        if len(activity['unresponded']) <= (len(activity['started']) * 0.9):
            self.end_flow_activity(flow.org)

    def create_unsolicited_incoming(self, org):
        if not org.cache['contacts']:
            return

        self._log(" > Receiving unsolicited incoming message in org %s\n" % org.name)

        available_contacts = list(set(org.cache['contacts']) - set(org.cache['activity']['started']))
        if available_contacts:
            contact = Contact.objects.get(id=self.random_choice(available_contacts))
            channel = self.random_choice(org.cache['channels'])
            urn = contact.urns.first()
            if urn:
                text = ' '.join([self.random_choice(l) for l in INBOX_MESSAGES])
                Msg.create_incoming(channel, six.text_type(urn), text)

    def probability(self, prob):
        return self.random.random() < prob

    def random_choice(self, seq, bias=1.0):
        if not seq:
            raise ValueError("Can't select random item from empty sequence")
        return seq[min(int(math.pow(self.random.random(), bias) * len(seq)), len(seq) - 1)]

    def weighted_choice(self, seq, weights):
        r = self.random.random() * sum(weights)
        cum_weight = 0.0

        for i, item in enumerate(seq):
            cum_weight += weights[i]
            if r < cum_weight or (i == len(seq) - 1):
                return item

    def random_org(self, orgs):
        """
        Returns a random org with bias toward the orgs with the lowest indexes
        """
        return self.random_choice(orgs, bias=self.org_bias)

    def random_date(self, start=None, end=None):
        if not end:
            end = timezone.now()
        if not start:
            start = end - timedelta(days=365)

        if start == end:
            return end

        return ms_to_datetime(self.random.randrange(datetime_to_ms(start), datetime_to_ms(end)))

    def timeline_date(self, dist):
        """
        Converts a 0..1 distance into a date on this database's overall timeline
        """
        seconds_span = (self.db_ends_on - self.db_begins_on).total_seconds()

        return self.db_begins_on + timedelta(seconds=(seconds_span * dist))

    @staticmethod
    def peak_memory():
        rusage_denom = 1024
        if sys.platform == 'darwin':
            # OSX gives value in bytes, other OSes in kilobytes
            rusage_denom *= rusage_denom
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / rusage_denom

    def _log(self, text):
        self.stdout.write(text, ending='')
        self.stdout.flush()


class DisableTriggersOn(object):
    """
    Helper context manager for temporarily disabling database triggers for a given model
    """
    def __init__(self, *models):
        self.tables = [m._meta.db_table for m in models]

    def __enter__(self):
        with connection.cursor() as cursor:
            for table in self.tables:
                cursor.execute('ALTER TABLE %s DISABLE TRIGGER ALL;' % table)

    def __exit__(self, exc_type, exc_val, exc_tb):
        with connection.cursor() as cursor:
            for table in self.tables:
                cursor.execute('ALTER TABLE %s ENABLE TRIGGER ALL;' % table)
