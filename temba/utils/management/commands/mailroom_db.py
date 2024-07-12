import json
import subprocess
import time
from zoneinfo import ZoneInfo

from django_redis import get_redis_connection

from django.conf import settings
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
from temba.orgs.models import Org, OrgRole, User
from temba.templates.models import Template, TemplateTranslation
from temba.tickets.models import Team, Topic

SPECS_FILE = "temba/utils/management/commands/data/mailroom_db.json"

# by default every user will have this password including the superuser
USER_PASSWORD = "Qwerty123"

# database dump containing admin boundary records
LOCATIONS_FILE = "test-data/nigeria.bin"

# database id sequences to be reset to make ids predictable
RESET_SEQUENCES = (
    "contacts_contact_id_seq",
    "contacts_contacturn_id_seq",
    "contacts_contactgroup_id_seq",
    "flows_flow_id_seq",
    "flows_flowrevision_id_seq",
    "channels_channel_id_seq",
    "campaigns_campaign_id_seq",
    "campaigns_campaignevent_id_seq",
    "msgs_label_id_seq",
    "templates_template_id_seq",
    "templates_templatetranslation_id_seq",
    "triggers_trigger_id_seq",
)

PG_CONTAINER_NAME = "textit-postgres-1"
MAILROOM_PORT = 8092
MAILROOM_DB_NAME = "mailroom_test"
MAILROOM_DB_USER = "mailroom_test"
DUMP_FILE = "mailroom_test.dump"


class Command(BaseCommand):
    help = "Generates a database suitable for mailroom testing"

    def handle(self, *args, **kwargs):
        self.generate_and_dump(SPECS_FILE, LOCATIONS_FILE, MAILROOM_PORT, MAILROOM_DB_NAME, MAILROOM_DB_USER, DUMP_FILE)

    def generate_and_dump(self, specs_file, locs_file, mr_port: int, db_name, db_user, dump_file):
        with open(specs_file, "r") as orgs_file:
            orgs_spec = json.load(orgs_file)

        self._log(f"Initializing {db_name} database...\n")

        # drop and recreate the test db and user
        self._sql(f"DROP DATABASE IF EXISTS {db_name}")
        self._sql(f"CREATE DATABASE {db_name}")
        self._sql(f"DROP USER IF EXISTS {db_user}")
        self._sql(f"CREATE USER {db_user} PASSWORD 'temba'")
        self._sql(f"ALTER ROLE {db_user} WITH SUPERUSER")

        # always use test db as our db and override mailroom location
        settings.DATABASES["default"]["NAME"] = db_name
        settings.DATABASES["default"]["USER"] = db_user
        settings.MAILROOM_URL = f"http://host.docker.internal:{mr_port}"

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

        mr_cmd = f'mailroom --port={mr_port} -db="postgres://{db_user}:temba@localhost/{db_name}?sslmode=disable" -uuid-seed=123'
        input(f"\nPlease start mailroom:\n   % ./{mr_cmd}\n\nPress enter when ready.\n")

        country = self.load_locations(locs_file)

        # patch UUID generation so it's deterministic
        from temba.utils import uuid

        uuid.default_generator = uuid.seeded_generator(1234)

        # create each of our orgs
        for spec in orgs_spec["orgs"]:
            self.create_org(spec, superuser, country)

        # leave id sequences starting at a known number so it's easier to identity newly created data in mailroom tests
        self.reset_id_sequences(30000)

        # dump our file
        result = subprocess.run(
            ["docker", "exec", "-i", PG_CONTAINER_NAME, "pg_dump", "-U", "postgres", "-Fc", db_name],
            stdout=subprocess.PIPE,
            check=True,
        )

        with open(dump_file, "wb") as f:
            f.write(result.stdout)

        self._log("\n" + self.style.SUCCESS("Success!") + f" Dump file: {dump_file}\n\n")

    def load_locations(self, path):
        """
        Loads admin boundary records from the given dump of that table
        """
        self._log("Loading locations from %s... " % path)

        with open(path, "rb") as f:
            try:
                subprocess.run(
                    [
                        "docker",
                        "exec",
                        "-i",
                        PG_CONTAINER_NAME,
                        "pg_restore",
                        "-d",
                        MAILROOM_DB_NAME,
                        "-U",
                        MAILROOM_DB_USER,
                    ],
                    input=f.read(),
                    check=True,
                )
            except subprocess.CalledProcessError:
                raise CommandError("Error occurred whilst calling pg_restore to load locations dump")

        self._log(self.style.SUCCESS("OK") + "\n")

        # TODO figure out why this is needed
        time.sleep(1)

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
            timezone=ZoneInfo("America/Los_Angeles"),
            flow_languages=spec["languages"],
            country=country,
            created_on=timezone.now(),
            created_by=superuser,
            modified_by=superuser,
        )
        org.initialize(sample_flows=False)

        # set our sequences to make ids stable across orgs
        self.reset_id_sequences(spec["sequence_start"])

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
        self.create_topics(spec, org, superuser)
        self.create_teams(spec, org, superuser)
        self.create_users(spec, org)

        return org

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
                config=c["config"],
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

    def create_teams(self, spec, org, user):
        self._log(f"Creating {len(spec['teams'])} teams... ")

        for t in spec["teams"]:
            team = Team.objects.create(
                uuid=t["uuid"],
                org=org,
                name=t["name"],
                created_by=user,
                modified_by=user,
            )
            for topic in t["topics"]:
                team.topics.add(Topic.objects.get(name=topic))

        self._log(self.style.SUCCESS("OK") + "\n")

    def create_users(self, spec, org):
        self._log(f"Creating {len(spec['users'])} users... ")

        for u in spec["users"]:
            user = User.objects.create_user(
                u["email"], u["email"], USER_PASSWORD, first_name=u["first_name"], last_name=u["last_name"]
            )
            org.add_user(user, OrgRole.from_code(u["role"]))
            if u.get("team"):
                user.set_team(Team.objects.get(name=u["team"]))

        self._log(self.style.SUCCESS("OK") + "\n")

    def create_fields(self, spec, org, user):
        self._log(f"Creating {len(spec['fields'])} fields... ")

        for f in spec["fields"]:
            field = ContactField.objects.create(
                org=org,
                key=f["key"],
                name=f["name"],
                is_system=False,
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
                group = ContactGroup.create_smart(org, user, g["name"], g["query"], evaluate=False)
            else:
                group = ContactGroup.create_manual(org, user, g["name"])
            group.uuid = g["uuid"]
            group.save(update_fields=["uuid"])

        self._log(self.style.SUCCESS("OK") + "\n")

    def create_labels(self, spec, org, user):
        self._log(f"Creating {len(spec['labels'])} labels... ")

        for lb in spec["labels"]:
            Label.objects.create(org=org, name=lb["name"], uuid=lb["uuid"], created_by=user, modified_by=user)

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
            group = ContactGroup.objects.get(org=org, name=c["group"])
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
                field = ContactField.objects.get(org=org, key=e["offset_field"])

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
                        start_mode=e["start_mode"],
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
                        start_mode=e["start_mode"],
                    )
                    evt.flow.uuid = e["uuid"]
                    evt.flow.save()

        self._log(self.style.SUCCESS("OK") + "\n")

    def create_templates(self, spec, org, templates):
        self._log(f"Creating {len(spec['templates'])} templates... ")

        for t in spec["templates"]:
            template = Template.objects.create(
                org=org, uuid=t["uuid"], name=t["name"], created_by=org.created_by, modified_by=org.modified_by
            )
            for tt in t["translations"]:
                channel = Channel.objects.get(uuid=tt["channel_uuid"])
                TemplateTranslation.objects.create(
                    template=template,
                    channel=channel,
                    locale=tt["locale"],
                    status=tt["status"],
                    external_id=tt["external_id"],
                    external_locale=tt["external_locale"],
                    namespace=tt["namespace"],
                    components=tt["components"],
                    variables=tt["variables"],
                )
            template.update_base()

        self._log(self.style.SUCCESS("OK") + "\n")

    def create_contacts(self, spec, org, user):
        self._log(f"Creating {len(spec['contacts'])} contacts... ")

        fields_by_key = {f.key: f for f in ContactField.user_fields.all()}

        for c in spec["contacts"]:
            values = {fields_by_key[key]: val for key, val in c.get("fields", {}).items()}
            groups = list(ContactGroup.objects.filter(org=org, name__in=c.get("groups", [])))

            contact = Contact.create(
                org,
                user,
                name=c["name"],
                language="",
                status=Contact.STATUS_ACTIVE,
                urns=c["urns"],
                fields=values,
                groups=groups,
            )
            contact.uuid = c["uuid"]
            contact.created_on = c["created_on"]
            contact.save(update_fields=("uuid", "created_on"))

        self._log(self.style.SUCCESS("OK") + "\n")

    def create_group_contacts(self, spec, org, user):
        self._log("Generating group contacts...")

        for g in spec["groups"]:
            size = int(g.get("size", 0))
            if size > 0:
                group = ContactGroup.objects.get(org=org, name=g["name"])

                contacts = []
                for i in range(size):
                    urn = f"tel:+250788{i:06}"
                    urn_obj = ContactURN.objects.filter(org=org, identity=urn).first()
                    if urn_obj and urn_obj.contact:
                        contact = urn_obj.contact
                    else:
                        contact = Contact.create(
                            org,
                            user,
                            name="",
                            language="",
                            status=Contact.STATUS_ACTIVE,
                            urns=[urn],
                            fields={},
                            groups=[],
                        )

                    contacts.append(contact)

                Contact.bulk_change_group(user, contacts, group, add=True)

        self._log(self.style.SUCCESS("OK") + "\n")

    def _sql(self, sql: str):
        try:
            result = subprocess.run(
                ["docker", "exec", "-i", PG_CONTAINER_NAME, "psql", "-U", "postgres"],
                input=sql.encode(),
                stdout=subprocess.PIPE,
                check=True,
            )
        except subprocess.CalledProcessError as e:
            raise CommandError(str(e))

        self._log(result.stdout.decode())

    def _log(self, text):
        self.stdout.write(text, ending="")
        self.stdout.flush()
