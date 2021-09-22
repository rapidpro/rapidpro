import json
import subprocess
import sys

import pytz
from django_redis import get_redis_connection

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management import BaseCommand, CommandError, call_command
from django.db import connection
from django.utils import timezone

from temba.campaigns.models import Campaign, CampaignEvent
from temba.channels.models import Channel
from temba.classifiers.models import Classifier
from temba.contacts.models import Contact, ContactField, ContactGroup, ContactURN
from temba.flows.models import Flow
from temba.globals.models import Global
from temba.locations.models import AdminBoundary
from temba.msgs.models import Label
from temba.orgs.models import Org
from temba.templates.models import Template, TemplateTranslation
from temba.tickets.models import Ticketer, Topic

ORGS_SPEC_FILE = "temba/utils/management/commands/data/mailroom_db.json"

# by default every user will have this password including the superuser
USER_PASSWORD = "Qwerty123"

# database dump containing admin boundary records
LOCATIONS_DUMP = "test-data/nigeria.bin"

# database id sequences to be reset to make ids predictable
RESET_SEQUENCES = (
    "contacts_contact_id_seq",
    "contacts_contacturn_id_seq",
    "contacts_contactgroup_id_seq",
    "flows_flow_id_seq",
    "channels_channel_id_seq",
    "campaigns_campaign_id_seq",
    "campaigns_campaignevent_id_seq",
    "msgs_label_id_seq",
    "templates_template_id_seq",
    "templates_templatetranslation_id_seq",
)


class Command(BaseCommand):
    help = "Generates a database suitable for mailroom testing"

    def handle(self, *args, **kwargs):
        with open(ORGS_SPEC_FILE, "r") as orgs_file:
            orgs_spec = json.load(orgs_file)

        self._log("Checking Postgres database version... ")

        result = subprocess.run(["pg_dump", "--version"], stdout=subprocess.PIPE)
        version = result.stdout.decode("utf8")
        if version.split(" ")[-1].find("11.") == 0:
            self._log(self.style.SUCCESS("OK") + "\n")
        else:
            self._log(
                "\n" + self.style.ERROR("Incorrect pg_dump version, needs version 11.*, found: " + version) + "\n"
            )
            sys.exit(1)

        self._log("Initializing mailroom_test database...\n")

        # drop and recreate the mailroom_test db and user
        subprocess.check_call('psql -c "DROP DATABASE IF EXISTS mailroom_test;"', shell=True)
        subprocess.check_call('psql -c "CREATE DATABASE mailroom_test;"', shell=True)
        subprocess.check_call('psql -c "DROP USER IF EXISTS mailroom_test;"', shell=True)
        subprocess.check_call("psql -c \"CREATE USER mailroom_test PASSWORD 'temba';\"", shell=True)
        subprocess.check_call('psql -c "ALTER ROLE mailroom_test WITH SUPERUSER;"', shell=True)

        # always use mailroom_test as our db
        settings.DATABASES["default"]["NAME"] = "mailroom_test"
        settings.DATABASES["default"]["USER"] = "mailroom_test"

        # patch UUID generation so it's deterministic
        from temba.utils import uuid

        uuid.default_generator = uuid.seeded_generator(1234)

        self._log("Running migrations...\n")

        # run our migrations to put our database in the right state
        call_command("migrate")

        # this is a new database so clear out redis
        self._log("Clearing out Redis cache... ")
        r = get_redis_connection()
        r.flushdb()
        self._log(self.style.SUCCESS("OK") + "\n")

        self._log("Creating superuser... ")
        superuser = User.objects.create_superuser("root", "root@nyaruka.com", USER_PASSWORD)
        self._log(self.style.SUCCESS("OK") + "\n")

        mr_cmd = 'mailroom -db="postgres://mailroom_test:temba@localhost/mailroom_test?sslmode=disable" -uuid-seed=123'
        input(f"\nPlease start mailroom:\n   % ./{mr_cmd}\n\nPress enter when ready.\n")

        country = self.load_locations(LOCATIONS_DUMP)

        # create each of our orgs
        for spec in orgs_spec["orgs"]:
            self.create_org(spec, superuser, country)

        # leave id sequences starting at a known number so it's easier to identity newly created data in mailroom tests
        self.reset_id_sequences(30000)

        # dump our file
        subprocess.check_call("pg_dump -Fc mailroom_test > mailroom_test.dump", shell=True)

        self._log("\n" + self.style.SUCCESS("Success!") + " Dump file: mailroom_test.dump\n\n")

    def load_locations(self, path):
        """
        Loads admin boundary records from the given dump of that table
        """
        self._log("Loading locations from %s... " % path)

        # load dump into current db with pg_restore
        db_config = settings.DATABASES["default"]
        try:
            subprocess.check_call(
                f"export PGPASSWORD={db_config['PASSWORD']} && pg_restore -h {db_config['HOST']} "
                f"-p {db_config['PORT']} -U {db_config['USER']} -w -d {db_config['NAME']} {path}",
                shell=True,
            )
        except subprocess.CalledProcessError:  # pragma: no cover
            raise CommandError("Error occurred whilst calling pg_restore to load locations dump")

        self._log(self.style.SUCCESS("OK") + "\n")

        return AdminBoundary.objects.filter(level=0).get()

    def reset_id_sequences(self, start: int):
        with connection.cursor() as cursor:
            for seq_name in RESET_SEQUENCES:
                cursor.execute(f"ALTER SEQUENCE {seq_name} RESTART WITH {start}")

    def create_org(self, spec, superuser, country):
        self._log(f"\nCreating org {spec['name']}...\n")

        org = Org.objects.create(
            uuid=spec["uuid"],
            name=spec["name"],
            timezone=pytz.timezone("America/Los_Angeles"),
            brand="rapidpro.io",
            country=country,
            created_on=timezone.now(),
            created_by=superuser,
            modified_by=superuser,
        )
        org.initialize(topup_size=100_000, sample_flows=False)

        # set our sequences to make ids stable across orgs
        self.reset_id_sequences(spec["sequence_start"])

        self.create_users(spec, org)
        self.create_channels(spec, org, superuser)
        self.create_fields(spec, org, superuser)
        self.create_globals(spec, org, superuser)
        self.create_labels(spec, org, superuser)
        self.create_groups(spec, org, superuser)
        self.create_flows(spec, org, superuser)
        self.create_contacts(spec, org, superuser)
        self.create_group_contacts(spec, org, superuser)
        self.create_campaigns(spec, org, superuser)
        self.create_templates(spec, org, superuser)
        self.create_classifiers(spec, org, superuser)
        self.create_ticketers(spec, org, superuser)
        self.create_topics(spec, org, superuser)

        return org

    def create_users(self, spec, org):
        self._log(f"Creating {len(spec['users'])} users... ")

        for u in spec["users"]:
            user = User.objects.create_user(
                u["email"], u["email"], USER_PASSWORD, first_name=u["first_name"], last_name=u["last_name"]
            )
            getattr(org, u["role"]).add(user)
            user.set_org(org)

        self._log(self.style.SUCCESS("OK") + "\n")

    def create_channels(self, spec, org, user):
        self._log(f"Creating {len(spec['channels'])} channels... ")

        for c in spec["channels"]:
            Channel.objects.create(
                org=org,
                name=c["name"],
                channel_type=c["channel_type"],
                address=c["address"],
                schemes=[c["scheme"]],
                uuid=c["uuid"],
                role=c["role"],
                created_by=user,
                modified_by=user,
            )

        self._log(self.style.SUCCESS("OK") + "\n")

    def create_classifiers(self, spec, org, user):
        self._log(f"Creating {len(spec['classifiers'])} classifiers... ")

        for c in spec["classifiers"]:
            classifier = Classifier.objects.create(
                org=org,
                name=c["name"],
                config=c["config"],
                classifier_type=c["classifier_type"],
                uuid=c["uuid"],
                created_by=user,
                modified_by=user,
            )

            # add the intents
            for intent in c["intents"]:
                classifier.intents.create(
                    name=intent["name"], external_id=intent["external_id"], created_on=timezone.now()
                )

        self._log(self.style.SUCCESS("OK") + "\n")

    def create_ticketers(self, spec, org, user):
        self._log(f"Creating {len(spec['ticketers'])} ticketers... ")

        for t in spec["ticketers"]:
            Ticketer.objects.create(
                org=org,
                name=t["name"],
                config=t["config"],
                ticketer_type=t["ticketer_type"],
                uuid=t["uuid"],
                created_by=user,
                modified_by=user,
            )

        self._log(self.style.SUCCESS("OK") + "\n")

    def create_topics(self, spec, org, user):
        self._log(f"Creating {len(spec['topics'])} topics... ")

        for t in spec["topics"]:
            Topic.objects.create(
                uuid=t["uuid"],
                org=org,
                name=t["name"],
                created_by=user,
                modified_by=user,
            )

        self._log(self.style.SUCCESS("OK") + "\n")

    def create_fields(self, spec, org, user):
        self._log(f"Creating {len(spec['fields'])} fields... ")

        for f in spec["fields"]:
            field = ContactField.user_fields.create(
                org=org,
                key=f["key"],
                label=f["label"],
                value_type=f["value_type"],
                show_in_table=True,
                created_by=user,
                modified_by=user,
            )
            field.uuid = f["uuid"]
            field.save(update_fields=["uuid"])

        self._log(self.style.SUCCESS("OK") + "\n")

    def create_globals(self, spec, org, user):
        self._log(f"Creating {len(spec['globals'])} globals... ")

        for g in spec["globals"]:
            Global.objects.create(
                org=org, key=g["key"], name=g["name"], value=g["value"], created_by=user, modified_by=user
            )

        self._log(self.style.SUCCESS("OK") + "\n")

    def create_groups(self, spec, org, user):
        self._log(f"Creating {len(spec['groups'])} groups... ")

        for g in spec["groups"]:
            if g.get("query"):
                group = ContactGroup.create_dynamic(org, user, g["name"], g["query"], evaluate=False)
            else:
                group = ContactGroup.create_static(org, user, g["name"])
            group.uuid = g["uuid"]
            group.save(update_fields=["uuid"])

        self._log(self.style.SUCCESS("OK") + "\n")

    def create_labels(self, spec, org, user):
        self._log(f"Creating {len(spec['labels'])} labels... ")

        for l in spec["labels"]:
            Label.label_objects.create(org=org, name=l["name"], uuid=l["uuid"], created_by=user, modified_by=user)

        self._log(self.style.SUCCESS("OK") + "\n")

    def create_flows(self, spec, org, user):
        self._log(f"Creating {len(spec['flows'])} flows... ")

        for f in spec["flows"]:
            with open("media/test_flows/mailroom/" + f["file"], "r") as flow_file:
                org.import_app(json.load(flow_file), user)

                # set the uuid on this flow
                Flow.objects.filter(org=org, name=f["name"]).update(uuid=f["uuid"])

        self._log(self.style.SUCCESS("OK") + "\n")

    def create_campaigns(self, spec, org, user):
        self._log(f"Creating {len(spec['campaigns'])} campaigns... ")

        for c in spec["campaigns"]:
            group = ContactGroup.all_groups.get(org=org, name=c["group"])
            campaign = Campaign.objects.create(
                name=c["name"],
                group=group,
                is_archived=False,
                org=org,
                created_by=user,
                modified_by=user,
                uuid=c["uuid"],
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
                    evt = CampaignEvent.create_message_event(
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
                    evt.flow.uuid = e["uuid"]
                    evt.flow.save()

        self._log(self.style.SUCCESS("OK") + "\n")

    def create_templates(self, spec, org, templates):
        self._log(f"Creating {len(spec['templates'])} templates... ")

        for t in spec["templates"]:
            Template.objects.create(org=org, uuid=t["uuid"], name=t["name"])
            for tt in t["translations"]:
                channel = Channel.objects.get(uuid=tt["channel_uuid"])
                TemplateTranslation.get_or_create(
                    channel,
                    t["name"],
                    tt["language"],
                    tt["country"],
                    tt["content"],
                    tt["variable_count"],
                    tt["status"],
                    tt["external_id"],
                    tt["namespace"],
                )

        self._log(self.style.SUCCESS("OK") + "\n")

    def create_contacts(self, spec, org, user):
        self._log(f"Creating {len(spec['contacts'])} contacts... ")

        fields_by_key = {f.key: f for f in ContactField.user_fields.all()}

        for c in spec["contacts"]:
            values = {fields_by_key[key]: val for key, val in c.get("fields", {}).items()}
            groups = list(ContactGroup.user_groups.filter(org=org, name__in=c.get("groups", [])))

            contact = Contact.create(org, user, c["name"], language="", urns=c["urns"], fields=values, groups=groups)
            contact.uuid = c["uuid"]
            contact.created_on = c["created_on"]
            contact.save(update_fields=("uuid", "created_on"))

        self._log(self.style.SUCCESS("OK") + "\n")

    def create_group_contacts(self, spec, org, user):
        self._log(f"Generating group contacts...")

        for g in spec["groups"]:
            size = int(g.get("size", 0))
            if size > 0:
                group = ContactGroup.user_groups.get(org=org, name=g["name"])

                contacts = []
                for i in range(size):
                    urn = f"tel:+250788{i:06}"
                    contact = ContactURN.lookup(org, urn)
                    if not contact:
                        contact = Contact.create(org, user, name="", language="", urns=[urn], fields={}, groups=[])
                    contacts.append(contact)

                Contact.bulk_change_group(user, contacts, group, add=True)

        self._log(self.style.SUCCESS("OK") + "\n")

    def _log(self, text):
        self.stdout.write(text, ending="")
        self.stdout.flush()
