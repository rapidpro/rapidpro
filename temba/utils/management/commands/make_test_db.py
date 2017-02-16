from __future__ import unicode_literals

import math
import pytz
import random
import resource
import sys

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.utils.timezone import now
from temba.channels.models import Channel
from temba.contacts.models import Contact, ContactField, ContactGroup, ContactURN, URN, TEL_SCHEME, TWITTER_SCHEME
from temba.msgs.models import Broadcast, Label, Msg, FLOW, INBOX, INCOMING, OUTGOING, HANDLED, SENT
from temba.orgs.models import Org
from temba.utils import chunk_list
from temba.values.models import Value


DEFAULT_NUM_ORGS = 100
DEFAULT_NUM_CONTACTS = 2000000
DEFAULT_NUM_MESSAGES = 5000000

GROUPS_PER_ORG = 20
LABELS_PER_ORG = 10

# how much to bias to apply when allocating contacts, messages and runs. For 100 orgs, a bias of 5 gives the first org
# about 40% of the content.
ORG_BIAS = 5

USER_PASSWORD = "password"
ORG_NAMES = ("House Stark", "House Targaryen", "House Lannister", "House Bolton", "House Greyjoy", "House Frey", "House Mormont")
CONTACT_NAMES = (None, "Jon", "Daenerys", "Melisandre", "Arya", "Sansa", "Tyrion", "Cersei", "Gregor", "Khal")
CONTACT_LANGS = (None, "eng", "fra", "kin")
CONTACT_HAS_TEL_PROB = 0.9  # 9/10 contacts have a phone number
CONTACT_HAS_TWITTER_PROB = 0.1  # 1/10 contacts have a twitter handle
CONTACT_IS_FAILED_PROB = 0.01  # 1/100 contacts are failed
CONTACT_IS_BLOCKED_PROB = 0.01  # 1/100 contacts are blocked
CONTACT_IS_DELETED_PROB = 0.005  # 1/200 contacts are deleted
CONTACT_FIELD_VALUES = ("yes", "no", "maybe", 1, 2, 3, 10, 100)
MESSAGE_WORDS = (CONTACT_NAMES[1:], ("eats", "fights", "loves", "builds"), ("castles", "the throne", "a wolf", "horses"))
MESSAGE_OUT_IN_RATIO = 10  # 10x as many outgoing as incoming
MESSAGE_IS_FLOW_PROB = 0.7  # 7/10 messages are flow messages
MESSAGE_ARCHIVED_PROB = 0.5  # 1/2 non-flow incoming messages are archived (i.e. 5/100 of total incoming)
MESSAGE_LABELLED_PROB = 0.5  # 1/2 incoming messages are labelled


class Command(BaseCommand):
    help = "Installs a database suitable for testing"

    def add_arguments(self, parser):
        parser.add_argument('--num-orgs', type=int, action='store', dest='num_orgs', default=DEFAULT_NUM_ORGS)
        parser.add_argument('--num-contacts', type=int, action='store', dest='num_contacts', default=DEFAULT_NUM_CONTACTS)
        parser.add_argument('--num-messages', type=int, action='store', dest='num_messages', default=DEFAULT_NUM_MESSAGES)

    def handle(self, num_orgs, num_contacts, num_messages, **kwargs):
        self.check_db_state()

        superuser = User.objects.create_superuser("root", "root@example.com", "password")

        orgs = self.create_orgs(superuser, num_orgs)
        self.create_channels(orgs)
        self.create_fields(orgs)
        self.create_groups(orgs, GROUPS_PER_ORG)
        self.create_labels(orgs, LABELS_PER_ORG)
        # self.create_contacts(orgs, num_contacts)
        # self.create_messages(orgs, num_messages)

        self.stdout.write("Peak memory usage: %d MiB" % int(peak_memory()))

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
        return random_choice(orgs, bias=ORG_BIAS)

    def create_orgs(self, superuser, num_total):
        self.stdout.write("Creating %d orgs... " % num_total, ending='')

        orgs = []
        for o in range(num_total):
            orgs.append(Org(name="%s (%d)" % (random.choice(ORG_NAMES), o + 1),
                        timezone=random.choice(pytz.all_timezones),
                        brand='rapidpro.io', created_by=superuser, modified_by=superuser))
        Org.objects.bulk_create(orgs)
        orgs = list(Org.objects.order_by('id'))

        self.stdout.write(self.style.SUCCESS("OK"))
        self.stdout.write("Initializing orgs... ", ending='')

        for org in orgs:
            org.initialize()
            org._fields = []
            org._groups = []
            org._contacts = []
            org._labels = []

        self.stdout.write(self.style.SUCCESS("OK"))
        self.stdout.write("Creating %d users... " % (len(orgs) * 4), ending='')
        self.stdout.flush()  # user creation is CPU intensive

        for o, org in enumerate(orgs):
            # each org has a user of every type
            admin = User.objects.create_user("admin%d" % (o + 1), "org%d_admin@example.com" % (o + 1), USER_PASSWORD)
            org._admin = admin
            org.administrators.add(admin)

            editor = User.objects.create_user("editor%d" % (o + 1), "org%d_editor@example.com" % (o + 1), USER_PASSWORD)
            org.editors.add(editor)

            viewer = User.objects.create_user("viewer%d" % (o + 1), "org%d_viewer@example.com" % (o + 1), USER_PASSWORD)
            org.viewers.add(viewer)

            surveyor = User.objects.create_user("surveyor%d" % (o + 1), "org%d_surveyor@example.com" % (o + 1), USER_PASSWORD)
            org.surveyors.add(surveyor)

        self.stdout.write(self.style.SUCCESS("OK"))
        return orgs

    def create_channels(self, orgs):
        self.stdout.write("Creating %d channels... " % (len(orgs) * 3), ending='')

        for o, org in enumerate(orgs):
            # each org has 3 channels
            android = Channel.objects.create(org=org, name="Android", channel_type=Channel.TYPE_ANDROID,
                                             address='1234', scheme='tel',
                                             created_by=org._admin, modified_by=org._admin)
            org._channels_by_scheme = {
                TEL_SCHEME: [
                    android,
                    Channel.objects.create(org=org, name="Nexmo", channel_type=Channel.TYPE_NEXMO, address='2345',
                                           scheme='tel', parent=android, created_by=org._admin, modified_by=org._admin)
                ],
                TWITTER_SCHEME: [
                    Channel.objects.create(org=org, name="Twitter", channel_type=Channel.TYPE_TWITTER,
                                           address='org%d' % o, scheme='twitter',
                                           created_by=org._admin, modified_by=org._admin)
                ]
            }

        self.stdout.write(self.style.SUCCESS("OK"))
        return orgs

    def create_fields(self, orgs):
        self.stdout.write("Creating %d fields... " % (len(orgs) * 5), ending='')

        for org in orgs:
            # each org gets a contact field of each type
            org._fields = [
                ContactField.objects.create(org=org, key='gender', label="Gender", value_type=Value.TYPE_TEXT,
                                            created_by=org._admin, modified_by=org._admin),
                ContactField.objects.create(org=org, key='age', label="Age", value_type=Value.TYPE_DECIMAL,
                                            created_by=org._admin, modified_by=org._admin),
                ContactField.objects.create(org=org, key='joined', label="Joined On", value_type=Value.TYPE_DATETIME,
                                            created_by=org._admin, modified_by=org._admin),
                ContactField.objects.create(org=org, key='ward', label="Ward", value_type=Value.TYPE_WARD,
                                            created_by=org._admin, modified_by=org._admin),
                ContactField.objects.create(org=org, key='district', label="District", value_type=Value.TYPE_DISTRICT,
                                            created_by=org._admin, modified_by=org._admin),
                ContactField.objects.create(org=org, key='state', label="State", value_type=Value.TYPE_STATE,
                                            created_by=org._admin, modified_by=org._admin),
            ]

        self.stdout.write(self.style.SUCCESS("OK"))

    def create_groups(self, orgs, num_per_org):
        total_groups = len(orgs) * num_per_org

        self.stdout.write("Creating %d groups... " % total_groups, ending='')

        for g in range(total_groups):
            org = orgs[g % len(orgs)]
            group = ContactGroup.user_groups.create(org=org, name="Group #%d" % (g + 1),
                                                    created_by=org._admin, modified_by=org._admin)
            org._groups.append(group)

        self.stdout.write(self.style.SUCCESS("OK"))

    def create_labels(self, orgs, num_per_org):
        total_labels = len(orgs) * num_per_org

        self.stdout.write("Creating %d labels... " % total_labels, ending='')

        for l in range(total_labels):
            org = orgs[l % len(orgs)]
            label = Label.label_objects.create(org=org, name="Label #%d" % (l + 1),
                                               created_by=org._admin, modified_by=org._admin)
            org._labels.append(label)

        self.stdout.write(self.style.SUCCESS("OK"))

    def create_contacts(self, orgs, num_total):
        self.stdout.write("Creating contacts...")

        for c in range(num_total):
            org = orgs[c] if c < len(orgs) else self.random_org(orgs)  # ensure every org gets at least one contact
            name = random_choice(CONTACT_NAMES)

            contact = Contact.objects.create(org=org,
                                             name=name,
                                             language=random_choice(CONTACT_LANGS),
                                             is_failed=probability(CONTACT_IS_FAILED_PROB),
                                             is_blocked=probability(CONTACT_IS_BLOCKED_PROB),
                                             is_active=probability(1 - CONTACT_IS_DELETED_PROB),
                                             created_by=org._admin, modified_by=org._admin)

            # maybe give the contact some URNs
            urn = None

            if probability(CONTACT_HAS_TEL_PROB):
                phone = '+2507%08d' % c
                urn = ContactURN.objects.create(org=org, contact=contact, priority=50,
                                                scheme=TEL_SCHEME, path=phone, urn=URN.from_tel(phone))

            if probability(CONTACT_HAS_TWITTER_PROB):
                handle = '%s%d' % (name.lower() if name else 'tweep', c)
                urn = ContactURN.objects.create(org=org, contact=contact, priority=50,
                                                scheme=TWITTER_SCHEME, path=handle, urn=URN.from_twitter(handle))

            # give contact values for random sample of their org's fields
            contact_fields = random.sample(org._fields, random.randrange(len(org._fields)))
            contact_values = []
            for field in contact_fields:
                val = random_choice(CONTACT_FIELD_VALUES)
                contact_values.append(Value(org=org, contact=contact, contact_field=field, string_value=str(val)))
            Value.objects.bulk_create(contact_values)

            # place the contact in a biased sample of up to half of their org's groups
            for g in range(random.randrange(len(org._groups) / 2)):
                group = random_choice(org._groups, 3)
                group.contacts.add(contact)

            # keeping all contact objects in memory is too expensive so just keep the important bits
            contact_desc = (contact.pk, urn.pk, urn.scheme) if urn else (contact.pk, None, None)
            org._contacts.append(contact_desc)

            if (c + 1) % 1000 == 0 or c == (num_total - 1):
                self.stdout.write(" > Created %d of %d contacts" % (c + 1, num_total))

    def create_messages(self, orgs, num_target):
        self.stdout.write("Creating messages...")

        num_created = 0
        while num_created < num_target:
            org = self.random_org(orgs)

            num_outgoing = self.create_broadcast(org)
            num_incoming = num_outgoing / MESSAGE_OUT_IN_RATIO

            self.create_responses(org, num_incoming)

            num_created += (num_outgoing + num_incoming)

    def create_broadcast(self, org):
        contacts = org._contacts
        contact_ids = [c[0] for c in contacts]
        urn_ids = [c[1] for c in contacts if c[1]]
        text = random_text() + "?"

        broadcast = Broadcast.objects.create(org=org, text=text, status=SENT,
                                             created_by=org._admin, modified_by=org._admin)
        broadcast.contacts.add(*contact_ids)
        broadcast.recipients.add(*urn_ids)

        self.stdout.write(" > Created broadcast")

        num_created = 0
        for batch in chunk_list(contacts, 1000):
            msgs = []
            batch_created_on = now()
            for contact_id, contact_urn_id, urn_scheme in batch:
                msgs.append(Msg(org=org,
                                contact_id=contact_id, contact_urn_id=contact_urn_id,
                                text=text, visibility=Msg.VISIBILITY_VISIBLE, msg_type=FLOW,
                                direction=OUTGOING, status=SENT,
                                created_on=batch_created_on, modified_on=batch_created_on))

            Msg.all_messages.bulk_create(msgs)
            num_created += len(msgs)

            self.stdout.write(" > Created %d of %d outgoing messages" % (num_created, len(contacts)))

        return len(contacts)

    def create_responses(self, org, num_total):
        for m in range(num_total):
            contact_id, contact_urn_id, urn_scheme = random_choice(org._contacts)
            channel = random_choice(org._channels_by_scheme[urn_scheme]) if urn_scheme else None
            text = random_text()
            msg_type = FLOW if probability(MESSAGE_IS_FLOW_PROB) else INBOX
            archived = msg_type == INBOX and probability(MESSAGE_ARCHIVED_PROB)
            visibility = Msg.VISIBILITY_ARCHIVED if archived else Msg.VISIBILITY_VISIBLE

            msg = Msg.all_messages.create(org=org,
                                          contact_id=contact_id, contact_urn_id=contact_urn_id, channel=channel,
                                          text=text, visibility=visibility, msg_type=msg_type,
                                          direction=INCOMING, status=HANDLED,
                                          created_on=now(), modified_on=now())

            # give some messages a random label with bias toward first labels
            if probability(MESSAGE_LABELLED_PROB):
                msg.labels.add(random_choice(org._labels, bias=3))

            if (m + 1) % 1000 == 0 or m == (num_total - 1):
                self.stdout.write(" > Created %d of %d incoming messages" % (m + 1, num_total))


def probability(prob):
    return random.random() < prob


def random_choice(seq, bias=1):
    return seq[int(math.pow(random.random(), bias) * len(seq))]


def random_text():
    return " ".join([random.choice(l) for l in MESSAGE_WORDS])


def peak_memory():
    rusage_denom = 1024.0
    if sys.platform == 'darwin':
        # OSX gives value in bytes, other OSes in kilobytes
        rusage_denom *= rusage_denom
    mem = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / rusage_denom
    return mem
