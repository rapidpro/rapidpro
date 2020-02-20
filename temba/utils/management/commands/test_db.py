import json
import math
import random
import resource
import sys
import time
import uuid
from collections import defaultdict
from datetime import timedelta
from subprocess import CalledProcessError, check_call

import pytz
from django_redis import get_redis_connection

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone

from temba.archives.models import Archive
from temba.campaigns.models import Campaign, CampaignEvent
from temba.channels.models import Channel
from temba.contacts.models import (
    TEL_SCHEME,
    TWITTER_SCHEME,
    URN,
    Contact,
    ContactField,
    ContactGroup,
    ContactGroupCount,
    ContactURN,
)
from temba.flows.models import Flow
from temba.locations.models import AdminBoundary
from temba.msgs.models import Label
from temba.orgs.models import Org
from temba.utils import chunk_list
from temba.utils.dates import datetime_to_ms, ms_to_datetime
from temba.values.constants import Value

# maximum age in days of database content
CONTENT_AGE = 3 * 365

# by default every user will have this password including the superuser
USER_PASSWORD = "Qwerty123"

# database dump containing admin boundary records
LOCATIONS_DUMP = "test-data/nigeria.bin"

# number of each type of archive to create
ARCHIVES = 50

# organization names are generated from these components
ORG_NAMES = (
    ("UNICEF", "WHO", "WFP", "UNESCO", "UNHCR", "UNITAR", "FAO", "UNEP", "UNAIDS", "UNDAF"),
    ("Nigeria", "Chile", "Indonesia", "Rwanda", "Mexico", "Zambia", "India", "Brazil", "Sudan", "Mozambique"),
)

# the users, channels, groups, labels and fields to create for each organization
USERS = (
    {"email": "admin%d@nyaruka.com", "role": "administrators"},
    {"email": "editor%d@nyaruka.com", "role": "editors"},
    {"email": "viewer%d@nyaruka.com", "role": "viewers"},
    {"email": "surveyor%d@nyaruka.com", "role": "surveyors"},
)
CHANNELS = (
    {"name": "Android", "channel_type": "A", "scheme": "tel", "address": "1234"},
    {"name": "Nexmo", "channel_type": "NX", "scheme": "tel", "address": "2345"},
    {"name": "Twitter", "channel_type": "TWT", "scheme": "twitter", "address": "my_handle"},
)
FIELDS = (
    {"key": "gender", "label": "Gender", "value_type": Value.TYPE_TEXT},
    {"key": "age", "label": "Age", "value_type": Value.TYPE_NUMBER},
    {"key": "joined", "label": "Joined On", "value_type": Value.TYPE_DATETIME},
    {"key": "ward", "label": "Ward", "value_type": Value.TYPE_WARD},
    {"key": "district", "label": "District", "value_type": Value.TYPE_DISTRICT},
    {"key": "state", "label": "State", "value_type": Value.TYPE_STATE},
)
GROUPS = (
    {"name": "Reporters", "query": None, "member": 0.95},  # member is either a probability or callable
    {"name": "Farmers", "query": None, "member": 0.5},
    {"name": "Doctors", "query": None, "member": 0.4},
    {"name": "Teachers", "query": None, "member": 0.3},
    {"name": "Drivers", "query": None, "member": 0.2},
    {"name": "Testers", "query": None, "member": 0.1},
    {"name": "Empty", "query": None, "member": 0.0},
    {"name": "Youth (Dynamic)", "query": "age <= 18", "member": lambda c: c["age"] and c["age"] <= 18},
    {"name": "Unregistered (Dynamic)", "query": 'joined = ""', "member": lambda c: not c["joined"]},
    {
        "name": "Districts (Dynamic)",
        "query": "district=Faskari or district=Zuru or district=Anka",
        "member": lambda c: c["district"] and c["district"].name in ("Faskari", "Zuru", "Anka"),
    },
)
LABELS = ("Reporting", "Testing", "Youth", "Farming", "Health", "Education", "Trade", "Driving", "Building", "Spam")
FLOWS = ("favorites_timeout.json", "sms_form.json", "pick_a_number.json")
CAMPAIGNS = (
    {
        "name": "Doctor Reminders",
        "group": "Doctors",
        "events": (
            {"flow": "Favorites", "offset_field": "joined", "offset": "5", "offset_unit": "D", "delivery_hour": 12},
            {
                "base_language": "eng",
                "message": {
                    "eng": "Hi @contact.name, it is time to consult with your patients.",
                    "fra": "Bonjour @contact.name, il est temps de consulter vos patients.",
                },
                "offset_field": "joined",
                "offset": "10",
                "offset_unit": "M",
            },
        ),
    },
)

# contact names are generated from these components
CONTACT_NAMES = (
    ("Anne", "Bob", "Cathy", "Dave", "Evan", "Freda", "George", "Hallie", "Igor"),
    ("Jameson", "Kardashian", "Lopez", "Mooney", "Newman", "O'Shea", "Poots", "Quincy", "Roberts"),
)
CONTACT_LANGS = (None, "eng", "fra", "spa", "kin")
CONTACT_HAS_TEL_PROB = 0.9  # 9/10 contacts have a phone number
CONTACT_HAS_TWITTER_PROB = 0.1  # 1/10 contacts have a twitter handle
CONTACT_IS_STOPPED_PROB = 0.01  # 1/100 contacts are stopped
CONTACT_IS_BLOCKED_PROB = 0.01  # 1/100 contacts are blocked
CONTACT_IS_DELETED_PROB = 0.005  # 1/200 contacts are deleted
CONTACT_HAS_FIELD_PROB = 0.8  # 8/10 fields set for each contact


class Command(BaseCommand):
    help = "Generates a database suitable for performance testing"

    # https://docs.djangoproject.com/en/2.0/releases/2.0/#call-command-validates-the-options-it-receives
    stealth_options = ("num_orgs", "num_contacts", "seed")

    def add_arguments(self, parser):
        parser.add_argument("--orgs", type=int, action="store", dest="num_orgs", default=10)
        parser.add_argument("--contacts", type=int, action="store", dest="num_contacts", default=10000)
        parser.add_argument("--seed", type=int, action="store", dest="seed", default=None)
        parser.add_argument("--password", type=str, action="store", dest="password", default=USER_PASSWORD)

    def handle(self, *args, **kwargs):
        start = time.time()

        self.handle_generate(kwargs["num_orgs"], kwargs["num_contacts"], kwargs["seed"], kwargs["password"])

        time_taken = time.time() - start
        self._log("Completed in %d secs, peak memory usage: %d MiB\n" % (int(time_taken), int(self.peak_memory())))

    def handle_generate(self, num_orgs, num_contacts, seed, password):
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
        self._log(self.style.SUCCESS("OK") + "\n")

        superuser = User.objects.create_superuser("root", "root@nyaruka.com", password)

        country, locations = self.load_locations(LOCATIONS_DUMP)
        orgs = self.create_orgs(superuser, country, num_orgs)
        self.create_users(orgs, password)
        self.create_channels(orgs)
        self.create_fields(orgs)
        self.create_groups(orgs)
        self.create_contacts(orgs, locations, num_contacts)
        self.create_labels(orgs)
        self.create_flows(orgs)
        self.create_archives(orgs)
        self.create_campaigns(orgs)

    def configure_random(self, num_orgs, seed=None):
        if not seed:
            seed = random.randrange(0, 65536)

        self.random = random.Random(seed)

        # monkey patch uuid4 so it returns the same UUIDs for the same seed, see https://github.com/joke2k/faker/issues/484#issuecomment-287931101
        from temba.utils import models

        models.uuid4 = lambda: uuid.UUID(
            int=(self.random.getrandbits(128) | (1 << 63) | (1 << 78))
            & (~(1 << 79) & ~(1 << 77) & ~(1 << 76) & ~(1 << 62))
        )

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
        db_config = settings.DATABASES["default"]
        try:
            check_call(
                f"export PGPASSWORD={db_config['PASSWORD']} && pg_restore -h {db_config['HOST']} "
                f"-p {db_config['PORT']} -U {db_config['USER']} -w -d {db_config['NAME']} {path}",
                shell=True,
            )
        except CalledProcessError:  # pragma: no cover
            raise CommandError("Error occurred whilst calling pg_restore to load locations dump")

        # fetch as tuples of (WARD, DISTRICT, STATE)
        wards = AdminBoundary.objects.filter(level=3).prefetch_related("parent", "parent__parent")
        locations = [(w, w.parent, w.parent.parent) for w in wards]

        country = AdminBoundary.objects.filter(level=0).get()

        self._log(self.style.SUCCESS("OK") + "\n")
        return country, locations

    def create_orgs(self, superuser, country, num_total):
        """
        Creates and initializes the orgs
        """
        self._log("Creating %d orgs... " % num_total)

        org_names = ["%s %s" % (o1, o2) for o2 in ORG_NAMES[1] for o1 in ORG_NAMES[0]]
        self.random.shuffle(org_names)

        orgs = []
        for o in range(num_total):
            orgs.append(
                Org(
                    name=org_names[o % len(org_names)],
                    timezone=self.random.choice(pytz.all_timezones),
                    brand="rapidpro.io",
                    country=country,
                    created_on=self.db_begins_on,
                    created_by=superuser,
                    modified_by=superuser,
                )
            )
        Org.objects.bulk_create(orgs)
        orgs = list(Org.objects.order_by("id"))

        self._log(self.style.SUCCESS("OK") + "\nInitializing orgs... ")

        for o, org in enumerate(orgs):
            org.initialize(topup_size=max((1000 - o), 1) * 1000)

            # we'll cache some metadata on each org as it's created to save re-fetching things
            org.cache = {
                "users": [],
                "fields": {},
                "groups": [],
                "system_groups": {g.group_type: g for g in ContactGroup.system_groups.filter(org=org)},
            }

        self._log(self.style.SUCCESS("OK") + "\n")
        return orgs

    def create_users(self, orgs, password):
        """
        Creates a user of each type for each org
        """
        self._log("Creating %d users... " % (len(orgs) * len(USERS)))

        # create users for each org
        for org in orgs:
            for u in USERS:
                user = User.objects.create_user(u["email"] % org.id, u["email"] % org.id, password)
                getattr(org, u["role"]).add(user)
                user.set_org(org)
                org.cache["users"].append(user)

        self._log(self.style.SUCCESS("OK") + "\n")

    def create_channels(self, orgs):
        """
        Creates the channels for each org
        """
        self._log("Creating %d channels... " % (len(orgs) * len(CHANNELS)))

        for org in orgs:
            user = org.cache["users"][0]
            for c in CHANNELS:
                Channel.objects.create(
                    org=org,
                    name=c["name"],
                    channel_type=c["channel_type"],
                    address=c["address"],
                    schemes=[c["scheme"]],
                    created_by=user,
                    modified_by=user,
                )

        self._log(self.style.SUCCESS("OK") + "\n")

    def create_archives(self, orgs):
        """
        Creates archives for each org
        """
        self._log("Creating %d archives... " % (len(orgs) * ARCHIVES * 3))

        MAX_RECORDS_PER_DAY = 3_000_000

        def create_archive(max_records, start, period):
            record_count = random.randint(0, max_records)
            archive_size = record_count * 20
            archive_hash = uuid.uuid4().hex

            if period == Archive.PERIOD_DAILY:
                archive_url = (
                    f"https://dl-rapidpro-archives.s3.amazonaws.com/{org.id}/"
                    f"{type[0]}_{period}_{start.year}_{start.month}_{start.day}_{archive_hash}.jsonl.gz"
                )
            else:

                archive_url = (
                    f"https://dl-rapidpro-archives.s3.amazonaws.com/{org.id}/"
                    f"{type[0]}_{period}_{start.year}_{start.month}_{archive_hash}.jsonl.gz"
                )

            Archive.objects.create(
                org=org,
                archive_type=type[0],
                url=archive_url,
                start_date=start,
                period=period,
                size=archive_size,
                hash=archive_hash,
                record_count=record_count,
                build_time=record_count / 123,
            )

        for org in orgs:
            for type in Archive.TYPE_CHOICES:
                end = timezone.now() - timedelta(days=90)

                # daily archives up until now
                for idx in range(0, end.day - 2):
                    end = end - timedelta(days=1)
                    start = end - timedelta(days=1)
                    create_archive(MAX_RECORDS_PER_DAY, start, Archive.PERIOD_DAILY)

                # month archives before that
                end = timezone.now() - timedelta(days=90)
                for idx in range(0, ARCHIVES):
                    # last day of the previous month
                    end = end.replace(day=1) - timedelta(days=1)
                    start = end.replace(day=1)
                    create_archive(MAX_RECORDS_PER_DAY * 30, start, Archive.PERIOD_MONTHLY)

        self._log(self.style.SUCCESS("OK") + "\n")

    def create_fields(self, orgs):
        """
        Creates the contact fields for each org
        """
        self._log("Creating %d fields... " % (len(orgs) * len(FIELDS)))

        for org in orgs:
            user = org.cache["users"][0]
            for f in FIELDS:
                field = ContactField.user_fields.create(
                    org=org,
                    key=f["key"],
                    label=f["label"],
                    value_type=f["value_type"],
                    show_in_table=True,
                    created_by=user,
                    modified_by=user,
                )
                org.cache["fields"][f["key"]] = field

        self._log(self.style.SUCCESS("OK") + "\n")

    def create_groups(self, orgs):
        """
        Creates the contact groups for each org
        """
        self._log("Creating %d groups... " % (len(orgs) * len(GROUPS)))

        for org in orgs:
            user = org.cache["users"][0]
            for g in GROUPS:
                if g["query"]:
                    group = ContactGroup.create_dynamic(org, user, g["name"], g["query"], evaluate=False)
                else:
                    group = ContactGroup.create_static(org, user, g["name"])
                group.member = g["member"]
                group.count = 0
                org.cache["groups"].append(group)

        self._log(self.style.SUCCESS("OK") + "\n")

    def create_labels(self, orgs):
        """
        Creates the message labels for each org
        """
        self._log("Creating %d labels... " % (len(orgs) * len(LABELS)))

        for org in orgs:
            user = org.cache["users"][0]
            for name in LABELS:
                Label.label_objects.create(org=org, name=name, created_by=user, modified_by=user)

        self._log(self.style.SUCCESS("OK") + "\n")

    def create_flows(self, orgs):
        """
        Creates the flows for each org
        """
        self._log("Creating %d flows... " % (len(orgs) * len(FLOWS)))

        for org in orgs:
            user = org.cache["users"][0]
            for f in FLOWS:
                with open("media/test_flows/" + f, "r") as flow_file:
                    org.import_app(json.load(flow_file), user)

        self._log(self.style.SUCCESS("OK") + "\n")

    def create_campaigns(self, orgs):
        """
        Creates the campaigns for each org
        """
        self._log("Creating %d campaigns... " % (len(orgs) * len(CAMPAIGNS)))

        for org in orgs:
            user = org.cache["users"][0]
            for c in CAMPAIGNS:
                group = ContactGroup.all_groups.get(org=org, name=c["group"])
                campaign = Campaign.objects.create(
                    name=c["name"], group=group, is_archived=False, org=org, created_by=user, modified_by=user
                )

                for e in c.get("events", []):
                    field = ContactField.all_fields.get(org=org, key=e["offset_field"])

                    if "flow" in e:
                        flow = Flow.objects.get(org=org, name=e["flow"])
                        CampaignEvent.create_flow_event(
                            org,
                            user,
                            campaign,
                            field,
                            e["offset"],
                            e["offset_unit"],
                            flow,
                            delivery_hour=e.get("delivery_hour", -1),
                        )
                    else:
                        CampaignEvent.create_message_event(
                            org,
                            user,
                            campaign,
                            field,
                            e["offset"],
                            e["offset_unit"],
                            e["message"],
                            delivery_hour=e.get("delivery_hour", -1),
                            base_language=e["base_language"],
                        )

        self._log(self.style.SUCCESS("OK") + "\n")

    def create_contacts(self, orgs, locations, num_contacts):
        """
        Creates test and regular contacts for this database. Returns tuples of org, contact id and the preferred urn
        id to avoid trying to hold all contact and URN objects in memory.
        """
        group_counts = defaultdict(int)

        self._log(self.style.SUCCESS("OK") + "\n")
        self._log("Creating %d regular contacts...\n" % num_contacts)

        # disable table triggers to speed up insertion and in the case of contact group m2m, avoid having an unsquashed
        # count row for every contact
        with DisableTriggersOn(Contact, ContactURN, ContactGroup.contacts.through):
            names = [("%s %s" % (c1, c2)).strip() for c2 in CONTACT_NAMES[1] for c1 in CONTACT_NAMES[0]]
            names = [n if n else None for n in names]

            batch_num = 1
            for index_batch in chunk_list(range(num_contacts), self.batch_size):
                batch = []

                # generate flat representations and contact objects for this batch
                for c_index in index_batch:  # pragma: no cover
                    org = self.random_org(orgs)
                    name = self.random_choice(names)
                    location = self.random_choice(locations) if self.probability(CONTACT_HAS_FIELD_PROB) else None
                    created_on = self.timeline_date(c_index / num_contacts)

                    c = {
                        "org": org,
                        "user": org.cache["users"][0],
                        "name": name,
                        "groups": [],
                        "tel": "+2507%08d" % c_index if self.probability(CONTACT_HAS_TEL_PROB) else None,
                        "twitter": "%s%d" % (name.replace(" ", "_").lower() if name else "tweep", c_index)
                        if self.probability(CONTACT_HAS_TWITTER_PROB)
                        else None,
                        "gender": self.random_choice(("M", "F")) if self.probability(CONTACT_HAS_FIELD_PROB) else None,
                        "age": self.random.randint(16, 80) if self.probability(CONTACT_HAS_FIELD_PROB) else None,
                        "joined": self.random_date() if self.probability(CONTACT_HAS_FIELD_PROB) else None,
                        "ward": location[0] if location else None,
                        "district": location[1] if location else None,
                        "state": location[2] if location else None,
                        "language": self.random_choice(CONTACT_LANGS),
                        "is_stopped": self.probability(CONTACT_IS_STOPPED_PROB),
                        "is_blocked": self.probability(CONTACT_IS_BLOCKED_PROB),
                        "is_active": self.probability(1 - CONTACT_IS_DELETED_PROB),
                        "created_on": created_on,
                        "modified_on": self.random_date(created_on, self.db_ends_on),
                        "fields_as_json": {},
                    }

                    if c["gender"] is not None:
                        c["fields_as_json"][str(org.cache["fields"]["gender"].uuid)] = {"text": str(c["gender"])}
                    if c["age"] is not None:
                        c["fields_as_json"][str(org.cache["fields"]["age"].uuid)] = {
                            "text": str(c["age"]),
                            "number": str(c["age"]),
                        }
                    if c["joined"] is not None:
                        c["fields_as_json"][str(org.cache["fields"]["joined"].uuid)] = {
                            "text": org.format_datetime(c["joined"], show_time=False),
                            "datetime": timezone.localtime(c["joined"], org.timezone).isoformat(),
                        }

                    if location:
                        c["fields_as_json"].update(
                            {
                                str(org.cache["fields"]["ward"].uuid): {
                                    "text": str(c["ward"].path.split(" > ")[-1]),
                                    "ward": c["ward"].path,
                                    "district": c["district"].path,
                                    "state": c["state"].path,
                                },
                                str(org.cache["fields"]["district"].uuid): {
                                    "text": str(c["district"].path.split(" > ")[-1]),
                                    "district": c["district"].path,
                                    "state": c["state"].path,
                                },
                                str(org.cache["fields"]["state"].uuid): {
                                    "text": str(c["state"].path.split(" > ")[-1]),
                                    "state": c["state"].path,
                                },
                            }
                        )

                    # work out which system groups this contact belongs to
                    if c["is_active"]:
                        if not c["is_blocked"] and not c["is_stopped"]:
                            c["groups"].append(org.cache["system_groups"][ContactGroup.TYPE_ALL])
                        if c["is_blocked"]:
                            c["groups"].append(org.cache["system_groups"][ContactGroup.TYPE_BLOCKED])
                        if c["is_stopped"]:
                            c["groups"].append(org.cache["system_groups"][ContactGroup.TYPE_STOPPED])

                    # let each user group decide if it is taking this contact
                    for g in org.cache["groups"]:
                        if g.member(c) if callable(g.member) else self.probability(g.member):
                            c["groups"].append(g)

                    # track changes to group counts
                    for g in c["groups"]:
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
            c["object"] = Contact(
                org=c["org"],
                name=c["name"],
                language=c["language"],
                is_stopped=c["is_stopped"],
                is_blocked=c["is_blocked"],
                is_active=c["is_active"],
                created_by=c["user"],
                created_on=c["created_on"],
                modified_by=c["user"],
                modified_on=c["modified_on"],
                fields=c["fields_as_json"],
            )
        Contact.objects.bulk_create([c["object"] for c in batch])

        # now that contacts have pks, bulk create the actual URN, value and group membership objects
        batch_urns = []
        batch_memberships = []

        for c in batch:
            org = c["org"]
            c["urns"] = []

            if c["tel"]:
                c["urns"].append(
                    ContactURN(
                        org=org,
                        contact=c["object"],
                        priority=50,
                        scheme=TEL_SCHEME,
                        path=c["tel"],
                        identity=URN.from_tel(c["tel"]),
                    )
                )
            if c["twitter"]:
                c["urns"].append(
                    ContactURN(
                        org=org,
                        contact=c["object"],
                        priority=50,
                        scheme=TWITTER_SCHEME,
                        path=c["twitter"],
                        identity=URN.from_twitter(c["twitter"]),
                    )
                )
            for g in c["groups"]:
                batch_memberships.append(ContactGroup.contacts.through(contact=c["object"], contactgroup=g))

            batch_urns += c["urns"]

        ContactURN.objects.bulk_create(batch_urns)
        ContactGroup.contacts.through.objects.bulk_create(batch_memberships)

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
        if sys.platform == "darwin":
            # OSX gives value in bytes, other OSes in kilobytes
            rusage_denom *= rusage_denom
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / rusage_denom

    def _log(self, text):
        self.stdout.write(text, ending="")
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
                cursor.execute("ALTER TABLE %s DISABLE TRIGGER ALL;" % table)

    def __exit__(self, exc_type, exc_val, exc_tb):
        with connection.cursor() as cursor:
            for table in self.tables:
                cursor.execute("ALTER TABLE %s ENABLE TRIGGER ALL;" % table)
