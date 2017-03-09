from __future__ import unicode_literals, division

import math
import pytz
import random
import resource
import sys
import time
import uuid

from collections import defaultdict
from datetime import timedelta
from django.conf import settings
from django.contrib.auth.models import User
from django.core.management import BaseCommand, CommandError
from django.db import connection
from django.utils.timezone import now
from subprocess import check_call, CalledProcessError
from temba.channels.models import Channel
from temba.contacts.models import Contact, ContactField, ContactGroup, ContactURN, ContactGroupCount, URN, TEL_SCHEME, TWITTER_SCHEME
from temba.locations.models import AdminBoundary
from temba.msgs.models import Label
from temba.orgs.models import Org
from temba.utils import chunk_list, ms_to_datetime, datetime_to_str, datetime_to_ms
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
    {'name': "Nexmo", 'channel_type': Channel.TYPE_NEXMO, 'scheme': 'tel', 'address': "2345"},
    {'name': "Twitter", 'channel_type': Channel.TYPE_TWITTER, 'scheme': 'twitter', 'address': "my_handle"},
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


class Command(BaseCommand):
    help = "Generates a database suitable for performance testing"

    def add_arguments(self, parser):
        parser.add_argument('--num-orgs', type=int, action='store', dest='num_orgs', default=100)
        parser.add_argument('--num-contacts', type=int, action='store', dest='num_contacts', default=1000000)
        parser.add_argument('--seed', type=int, action='store', dest='seed', default=None)

    def handle(self, num_orgs, num_contacts, seed, **kwargs):
        self.check_db_state()

        if seed is None:
            seed = random.randrange(0, 65536)

        self.random = random.Random(seed)

        # monkey patch uuid4 so it returns the same UUIDs for the same seed
        from temba.utils import models
        models.uuid4 = lambda: uuid.UUID(int=self.random.getrandbits(128))

        self._log("Generating random test database (seed=%d)...\n" % seed)

        # We want a variety of large and small orgs so when allocating content like contacts and messages, we apply a
        # bias toward the beginning orgs. if there are N orgs, then the amount of content the first org will be
        # allocated is (1/N) ^ (1/bias). This sets the bias so that the first org will get ~50% of the content:
        self.org_bias = math.log(1.0 / num_orgs, 0.5)

        # The timespan being simulated by this database
        self.db_ends_on = now()
        self.db_begins_on = self.db_ends_on - timedelta(days=CONTENT_AGE)

        start = time.time()

        superuser = User.objects.create_superuser("root", "root@example.com", "password")

        country, locations = self.load_locations(LOCATIONS_DUMP)
        orgs = self.create_orgs(superuser, country, num_orgs)
        self.create_users(orgs)
        self.create_channels(orgs)
        self.create_fields(orgs)
        self.create_groups(orgs)
        self.create_contacts(orgs, locations, num_contacts)
        self.create_labels(orgs)

        time_taken = time.time() - start
        self._log("Time taken: %d secs, peak memory usage: %d MiB\n" % (int(time_taken), int(self.peak_memory())))

    def check_db_state(self):
        """
        Checks whether database is in correct state before continuing
        """
        try:
            has_data = Org.objects.exists()
        except Exception:  # pragma: no cover
            raise CommandError("Run migrate command first to create database tables")
        if has_data:
            raise CommandError("Can only be run on an empty database")

    def load_locations(self, path):
        """
        Loads admin boundary records from the given dump of that table
        """
        self._log("Loading locations from %s... " % path)

        # load dump into current db with pg_restore
        db_config = settings.DATABASES['default']
        try:
            check_call('export PGPASSWORD=%s && pg_restore -U%s -w -d %s %s' %
                       (db_config['PASSWORD'], db_config['USER'], db_config['NAME'], path), shell=True)
        except CalledProcessError:  # pragma: no cover
            raise CommandError("Error occurred whilst calling pg_restore to load locations dump")

        # fetch as tuples of (WARD, DISTRICT, STATE)
        wards = AdminBoundary.objects.filter(level=3).prefetch_related('parent', 'parent__parent')
        locations = [(w, w.parent, w.parent.parent) for w in wards]

        country = AdminBoundary.objects.filter(level=0).get()

        self._log(self.style.SUCCESS("OK") + '\n')
        return country, locations

    def create_orgs(self, superuser, country, num_total):
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
                'channels': [],
                'fields': {},
                'groups': [],
                'system_groups': {g.group_type: g for g in ContactGroup.system_groups.filter(org=org)},
                'contacts': [],
                'labels': [],
            }

        self._log(self.style.SUCCESS("OK") + '\n')
        return orgs

    def create_users(self, orgs):
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
        self._log("Creating %d channels... " % (len(orgs) * len(CHANNELS)))

        for org in orgs:
            user = org.cache['users'][0]
            for c in CHANNELS:
                channel = Channel.objects.create(org=org, name=c['name'], channel_type=c['channel_type'],
                                                 address=c['address'], scheme=c['scheme'],
                                                 created_by=user, modified_by=user)
                org.cache['channels'].append(channel)

        self._log(self.style.SUCCESS("OK") + '\n')

    def create_fields(self, orgs):
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
        self._log("Creating %d groups... " % (len(orgs) * len(GROUPS)))

        for org in orgs:
            user = org.cache['users'][0]
            for g in GROUPS:
                if g['query']:
                    group = ContactGroup.create_dynamic(org, user, g['name'], g['query'])
                else:
                    group = ContactGroup.user_groups.create(org=org, name=g['name'], created_by=user, modified_by=user)
                group.member = g['member']
                org.cache['groups'].append(group)

        self._log(self.style.SUCCESS("OK") + '\n')

    def create_contacts(self, orgs, locations, num_total):
        batch_size = 5000
        num_test_contacts = len(orgs) * len(USERS)
        group_membership_model = ContactGroup.contacts.through
        group_counts = defaultdict(int)

        self._log("Creating %d test contacts...\n" % num_test_contacts)

        for org in orgs:
            for user in org.cache['users']:
                Contact.get_test_contact(user)

        self._log("Creating %d regular contacts...\n" % (num_total - num_test_contacts))

        base_contact_id = self.get_current_id(Contact) + 1

        # Disable table triggers to speed up insertion and in the case of contact group m2m, avoid having an unsquashed
        # count row for every contact
        with DisableTriggersOn(Contact, ContactURN, Value, group_membership_model):
            names = [('%s %s' % (c1, c2)).strip() for c2 in CONTACT_NAMES[1] for c1 in CONTACT_NAMES[0]]
            names = [n if n else None for n in names]

            batch = 1
            for index_batch in chunk_list(range(num_total - num_test_contacts), batch_size):
                contacts = []
                urns = []
                values = []
                memberships = []

                def add_to_group(g):
                    group_counts[g] += 1
                    memberships.append(group_membership_model(contact_id=c['id'], contactgroup=g))

                for c_index in index_batch:  # pragma: no cover

                    org = orgs[c_index] if c_index < len(orgs) else self.random_org(orgs)  # at least 1 contact per org
                    name = self.random_choice(names)
                    location = self.random_choice(locations) if self.probability(CONTACT_HAS_FIELD_PROB) else None
                    created_on = self.timeline_date(float(num_test_contacts + c_index) / num_total)

                    c = {
                        'id': base_contact_id + c_index,  # database id this contact will have when created
                        'org': org,
                        'user': org.cache['users'][0],
                        'name': name,
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

                    if c['is_active']:
                        if not c['is_blocked'] and not c['is_stopped']:
                            add_to_group(org.cache['system_groups'][ContactGroup.TYPE_ALL])
                        if c['is_blocked']:
                            add_to_group(org.cache['system_groups'][ContactGroup.TYPE_BLOCKED])
                        if c['is_stopped']:
                            add_to_group(org.cache['system_groups'][ContactGroup.TYPE_STOPPED])

                    contacts.append(Contact(org=org, name=c['name'], language=c['language'],
                                            is_stopped=c['is_stopped'], is_blocked=c['is_blocked'],
                                            is_active=c['is_active'],
                                            created_by=user, created_on=c['created_on'],
                                            modified_by=user, modified_on=c['modified_on']))

                    if c['tel']:
                        urns.append(ContactURN(org=org, contact_id=c['id'], priority=50, scheme=TEL_SCHEME,
                                               path=c['tel'], urn=URN.from_tel(c['tel'])))
                    if c['twitter']:
                        urns.append(ContactURN(org=org, contact_id=c['id'], priority=50, scheme=TWITTER_SCHEME,
                                               path=c['twitter'], urn=URN.from_twitter(c['twitter'])))
                    if c['gender']:
                        values.append(Value(org=org, contact_id=c['id'], contact_field=org.cache['fields']['gender'],
                                            string_value=c['gender']))
                    if c['age']:
                        values.append(Value(org=org, contact_id=c['id'], contact_field=org.cache['fields']['age'],
                                            string_value=str(c['age']), decimal_value=c['age']))
                    if c['joined']:
                        values.append(Value(org=org, contact_id=c['id'], contact_field=org.cache['fields']['joined'],
                                            string_value=datetime_to_str(c['joined']), datetime_value=c['joined']))
                    if location:
                        values.append(Value(org=org, contact_id=c['id'], contact_field=org.cache['fields']['ward'],
                                            string_value=c['ward'].name, location_value=c['ward']))
                        values.append(Value(org=org, contact_id=c['id'], contact_field=org.cache['fields']['district'],
                                            string_value=c['district'].name, location_value=c['district']))
                        values.append(Value(org=org, contact_id=c['id'], contact_field=org.cache['fields']['state'],
                                            string_value=c['state'].name, location_value=c['state']))

                    # let each group decide if it is taking this contact
                    for g in org.cache['groups']:
                        if g.member(c) if callable(g.member) else self.probability(g.member):
                            add_to_group(g)

                Contact.objects.bulk_create(contacts)
                ContactURN.objects.bulk_create(urns)
                Value.objects.bulk_create(values)
                group_membership_model.objects.bulk_create(memberships)

                self._log(" > Created batch %d of %d\n" % (batch, max(num_total // batch_size, 1)))
                batch += 1

        # create group count records manually
        counts = []
        for group, count in group_counts.items():
            counts.append(ContactGroupCount(group=group, count=count, is_squashed=True))
        ContactGroupCount.objects.bulk_create(counts)

        # for sanity check that our presumed last contact id matches the last actual contact id
        assert c['id'] == Contact.objects.order_by('-id').first().id

    def create_labels(self, orgs):
        self._log("Creating %d labels... " % (len(orgs) * len(LABELS)))

        for org in orgs:
            user = org.cache['users'][0]
            for name in LABELS:
                label = Label.label_objects.create(org=org, name=name, created_by=user, modified_by=user)
                org.cache['labels'].append(label)

        self._log(self.style.SUCCESS("OK") + '\n')

    @staticmethod
    def get_current_id(model):
        """
        Gets the current (i.e. last generated) id for the given model
        """
        with connection.cursor() as cursor:
            cursor.execute('SELECT last_value FROM %s_id_seq' % model._meta.db_table)
            return cursor.fetchone()[0]

    def probability(self, prob):
        return self.random.random() < prob

    def random_choice(self, seq, bias=1.0):
        return seq[int(math.pow(self.random.random(), bias) * len(seq))]

    def random_org(self, orgs):
        """
        Returns a random org with bias toward the orgs with the lowest indexes
        """
        return self.random_choice(orgs, bias=self.org_bias)

    def random_date(self, start=None, end=None):
        if not end:
            end = now()
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
        rusage_denom = 1024.0
        if sys.platform == 'darwin':
            # OSX gives value in bytes, other OSes in kilobytes
            rusage_denom *= rusage_denom
        mem = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / rusage_denom
        return mem

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
