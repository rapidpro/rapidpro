from __future__ import unicode_literals, division

import math
import pytz
import random
import resource
import sys
import time

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


# every user will have this password including the superuser
USER_PASSWORD = "password"

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
GROUPS = ("Reporters", "Testers", "Youth", "Farmers", "Doctors", "Teachers", "Traders", "Drivers", "Builders", "Spammers")
LABELS = ("Reporting", "Testing", "Youth", "Farming", "Health", "Education", "Trade", "Driving", "Building", "Spam")
FIELDS = (
    {'key': 'gender', 'label': "Gender", 'value_type': Value.TYPE_TEXT},
    {'key': 'age', 'label': "Age", 'value_type': Value.TYPE_DECIMAL},
    {'key': 'joined', 'label': "Joined On", 'value_type': Value.TYPE_DATETIME},
    {'key': 'ward', 'label': "Ward", 'value_type': Value.TYPE_WARD},
    {'key': 'district', 'label': "District", 'value_type': Value.TYPE_DISTRICT},
    {'key': 'state', 'label': "State", 'value_type': Value.TYPE_STATE},
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


class Command(BaseCommand):
    help = "Generates a database suitable for performance testing"

    def add_arguments(self, parser):
        parser.add_argument('--num-orgs', type=int, action='store', dest='num_orgs', default=100)
        parser.add_argument('--num-contacts', type=int, action='store', dest='num_contacts', default=2000000)
        parser.add_argument('--seed', type=int, action='store', dest='seed', default=None)

    def handle(self, num_orgs, num_contacts, seed, **kwargs):
        if seed is not None:
            random.seed(seed)

        # We want a variety of large and small orgs so when allocating content like contacts and messages, we apply a
        # bias toward the beginning orgs. This bias is set to give the first org approximately 50% of the total content.
        self.org_bias = math.log(1.0 / num_orgs, 0.5)

        self.check_db_state()

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
        self._log("Time taken: %d secs, peak memory usage: %d MiB\n" % (int(time_taken), int(peak_memory())))

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

    def random_org(self, orgs):
        """
        Returns a random org with bias toward the orgs with the lowest indexes
        """
        return random_choice(orgs, bias=self.org_bias)

    def load_locations(self, path):
        """
        Loads admin boundary records from the given dump of that table
        """
        self._log("Loading locations from %s... " % path)

        # load dump into current db with pg_restore
        db_config = settings.DATABASES['default']
        try:
            check_call('pg_restore -U%s -w -d %s %s' % (db_config['USER'], db_config['NAME'], path), shell=True)
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
        random.shuffle(org_names)

        orgs = []
        for o in range(num_total):
            orgs.append(Org(name=org_names[o % len(org_names)], timezone=random.choice(pytz.all_timezones),
                            brand='rapidpro.io', country=country,
                            created_by=superuser, modified_by=superuser))
        Org.objects.bulk_create(orgs)
        orgs = list(Org.objects.order_by('id'))

        self._log(self.style.SUCCESS("OK") + "\nInitializing orgs... ")

        for org in orgs:
            org.initialize(topup_size=1000)  # TODO proportional topup sizes

            # we'll cache some metadata on each org as it's created to save re-fetching things
            org.cache = {'users': [], 'channels': [], 'fields': {}, 'groups': [], 'contacts': [], 'labels': []}

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
            for channel in CHANNELS:
                Channel.objects.create(org=org, name=channel['name'], channel_type=channel['channel_type'],
                                       address=channel['address'], scheme=channel['scheme'],
                                       created_by=user, modified_by=user)
                org.cache['channels'].append(channel)

        self._log(self.style.SUCCESS("OK") + '\n')

    def create_fields(self, orgs):
        self._log("Creating %d fields... " % (len(orgs) * len(FIELDS)))

        for org in orgs:
            user = org.cache['users'][0]
            for field in FIELDS:
                field_obj = ContactField.objects.create(org=org, key=field['key'], label=field['label'],
                                                        value_type=field['value_type'], show_in_table=True,
                                                        created_by=user, modified_by=user)
                org.cache['fields'][field['key']] = field_obj

        self._log(self.style.SUCCESS("OK") + '\n')

    def create_groups(self, orgs):
        self._log("Creating %d groups... " % (len(orgs) * len(GROUPS)))

        for org in orgs:
            user = org.cache['users'][0]
            for name in GROUPS:
                group = ContactGroup.user_groups.create(org=org, name=name, created_by=user, modified_by=user)
                group._count = 0  # used for tracking membership count
                org.cache['groups'].append(group)

        self._log(self.style.SUCCESS("OK") + '\n')

    def create_contacts(self, orgs, locations, num_total):
        num_test_contacts = len(orgs) * len(USERS)
        group_membership_model = ContactGroup.contacts.through

        self._log("Creating %d test contacts...\n" % num_test_contacts)

        for org in orgs:
            for user in org.cache['users']:
                Contact.get_test_contact(user)

        self._log("Creating %d regular contacts...\n" % (num_total - num_test_contacts))

        # disable group count triggers to speed up contact insertion
        with DisableTriggersOn(group_membership_model):
            names = [('%s %s' % (c1, c2)).strip() for c2 in CONTACT_NAMES[1] for c1 in CONTACT_NAMES[0]]
            names = [n if n else None for n in names]

            batch = 1
            for index_batch in chunk_list(range(num_total - num_test_contacts), 10000):
                contacts = []
                urns = []
                values = []
                memberships = []
                for c in index_batch:
                    # calculate the database id this contact will have when created
                    c_id = num_test_contacts + c + 1

                    # ensure every org gets at least one contact
                    org = orgs[c] if c < len(orgs) else self.random_org(orgs)

                    user = org.cache['users'][0]
                    name = random_choice(names)
                    gender = random_choice(('M', 'F'))
                    age = random.randint(16, 80)
                    joined = random_date()

                    contacts.append(Contact(org=org, name=name, language=random_choice(CONTACT_LANGS),
                                            is_stopped=probability(CONTACT_IS_STOPPED_PROB),
                                            is_blocked=probability(CONTACT_IS_BLOCKED_PROB),
                                            is_active=probability(1 - CONTACT_IS_DELETED_PROB),
                                            created_by=user, modified_by=user))

                    if probability(CONTACT_HAS_TEL_PROB):
                        phone = '+2507%08d' % c
                        urns.append(ContactURN(org=org, contact_id=c_id, priority=50,
                                               scheme=TEL_SCHEME, path=phone, urn=URN.from_tel(phone)))

                    if probability(CONTACT_HAS_TWITTER_PROB):
                        handle = '%s%d' % (name.replace(' ', '_').lower() if name else 'tweep', c)
                        urns.append(ContactURN(org=org, contact_id=c_id, priority=50,
                                               scheme=TWITTER_SCHEME, path=handle, urn=URN.from_twitter(handle)))

                    fields = org.cache['fields']
                    if probability(CONTACT_HAS_FIELD_PROB):
                        values.append(Value(org=org, contact_id=c_id, contact_field=fields['gender'], string_value=gender))
                    if probability(CONTACT_HAS_FIELD_PROB):
                        values.append(Value(org=org, contact_id=c_id, contact_field=fields['age'],
                                            string_value=str(age), decimal_value=age))
                    if probability(CONTACT_HAS_FIELD_PROB):
                        values.append(Value(org=org, contact_id=c_id, contact_field=fields['joined'],
                                            string_value=datetime_to_str(joined), datetime_value=joined))
                    if probability(CONTACT_HAS_FIELD_PROB):
                        location = random_choice(locations)
                        values.append(Value(org=org, contact_id=c_id, contact_field=fields['ward'],
                                            string_value=location[0].name, location_value=location[0]))
                        values.append(Value(org=org, contact_id=c_id, contact_field=fields['district'],
                                            string_value=location[1].name, location_value=location[1]))
                        values.append(Value(org=org, contact_id=c_id, contact_field=fields['state'],
                                            string_value=location[2].name, location_value=location[2]))

                    # place contact in a biased sample of their org's groups
                    for g in range(random.randrange(len(org.cache['groups']))):
                        group = org.cache['groups'][g]
                        group._count += 1
                        memberships.append(group_membership_model(contact_id=c_id, contactgroup=group))

                Contact.objects.bulk_create(contacts, batch_size=1000)
                ContactURN.objects.bulk_create(urns, batch_size=1000)
                Value.objects.bulk_create(values, batch_size=1000)
                group_membership_model.objects.bulk_create(memberships, batch_size=1000)

                self._log(" > Created batch %d of %d\n" % (batch, max(num_total // 10000, 1)))
                batch += 1

        # create group count records manually
        for org in orgs:
            for group in org.cache['groups']:
                ContactGroupCount.objects.create(group=group, count=group._count, is_squashed=True)

        # for sanity check that our presumed last contact id matches the last actual contact id
        assert c_id == Contact.objects.order_by('-id').first().id

    def create_labels(self, orgs):
        self._log("Creating %d labels... " % (len(orgs) * len(LABELS)))

        for org in orgs:
            user = org.cache['users'][0]
            for name in LABELS:
                label = Label.label_objects.create(org=org, name=name, created_by=user, modified_by=user)
                org.cache['labels'].append(label)

        self._log(self.style.SUCCESS("OK") + '\n')

    def _log(self, text):
        self.stdout.write(text, ending='')
        self.stdout.flush()


def probability(prob):
    return random.random() < prob


def random_choice(seq, bias=1.0):
    return seq[int(math.pow(random.random(), bias) * len(seq))]


def random_date(start=None, end=None):
    if not start:
        start = now() - timedelta(days=365)
    if not end:
        end = now()

    return ms_to_datetime(random.randrange(datetime_to_ms(start), datetime_to_ms(end)))


def peak_memory():
    rusage_denom = 1024.0
    if sys.platform == 'darwin':
        # OSX gives value in bytes, other OSes in kilobytes
        rusage_denom *= rusage_denom
    mem = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / rusage_denom
    return mem


class DisableTriggersOn(object):
    """
    Helper context manager for temporarily disabling database triggers for a given model
    """
    def __init__(self, model):
        self.table = model._meta.db_table

    def __enter__(self):
        with connection.cursor() as cursor:
            cursor.execute('ALTER TABLE %s DISABLE TRIGGER USER;' % self.table)

    def __exit__(self, exc_type, exc_val, exc_tb):
        with connection.cursor() as cursor:
            cursor.execute('ALTER TABLE %s ENABLE TRIGGER USER;' % self.table)
