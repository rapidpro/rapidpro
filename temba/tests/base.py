import shutil
from datetime import datetime

import pytz
import redis
from smartmin.tests import SmartminTest, SmartminTestMixin

from django.conf import settings
from django.contrib.auth.models import Group, User
from django.core import mail
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone

from temba.archives.models import Archive
from temba.channels.models import Channel, ChannelLog
from temba.contacts.models import URN, Contact, ContactField, ContactGroup
from temba.flows.models import Flow, FlowRevision, FlowRun, FlowSession, clear_flow_users
from temba.ivr.models import IVRCall
from temba.locations.models import AdminBoundary, BoundaryAlias
from temba.msgs.models import HANDLED, INBOX, INCOMING, OUTGOING, PENDING, SENT, Broadcast, Label, Msg
from temba.orgs.models import Org
from temba.utils import json
from temba.utils.uuid import UUID, uuid4
from temba.values.constants import Value

from .mailroom import update_field_locally, update_fields_locally


def add_testing_flag_to_context(*args):
    return dict(testing=settings.TESTING)


class TembaTestMixin:
    databases = ("default", "direct")

    def setUpOrgs(self):
        # make sure we start off without any service users
        Group.objects.get(name="Service Users").user_set.clear()

        self.clear_cache()

        self.create_anonymous_user()

        self.superuser = User.objects.create_superuser(username="super", email="super@user.com", password="super")

        # create different user types
        self.non_org_user = self.create_user("NonOrg")
        self.user = self.create_user("User", ("Viewers",))
        self.editor = self.create_user("Editor")
        self.admin = self.create_user("Administrator")
        self.surveyor = self.create_user("Surveyor")
        self.customer_support = self.create_user("support", ("Customer Support",))

        self.org = Org.objects.create(
            name="Temba",
            timezone=pytz.timezone("Africa/Kigali"),
            brand=settings.DEFAULT_BRAND,
            created_by=self.user,
            modified_by=self.user,
        )
        self.org.initialize(topup_size=1000)

        # add users to the org
        self.user.set_org(self.org)
        self.org.viewers.add(self.user)

        self.editor.set_org(self.org)
        self.org.editors.add(self.editor)

        self.admin.set_org(self.org)
        self.org.administrators.add(self.admin)

        self.surveyor.set_org(self.org)
        self.org.surveyors.add(self.surveyor)

        # setup a second org with a single admin
        self.admin2 = self.create_user("Administrator2")
        self.org2 = Org.objects.create(
            name="Trileet Inc.",
            timezone=pytz.timezone("Africa/Kigali"),
            brand="rapidpro.io",
            created_by=self.admin2,
            modified_by=self.admin2,
        )
        self.org2.initialize(topup_size=1000)

        self.org2.administrators.add(self.admin2)
        self.admin2.set_org(self.org)

        self.superuser.set_org(self.org)

        # a single Android channel
        self.channel = Channel.create(
            self.org,
            self.user,
            "RW",
            "A",
            name="Test Channel",
            address="+250785551212",
            device="Nexus 5X",
            secret="12345",
            config={Channel.CONFIG_FCM_ID: "123"},
        )

        # don't cache anon user between tests
        from temba import utils

        utils._anon_user = None

        clear_flow_users()

    def setUpLocations(self):
        """
        Installs some basic test location data for Rwanda
        """
        self.country = AdminBoundary.create(osm_id="171496", name="Rwanda", level=0)
        self.state1 = AdminBoundary.create(osm_id="1708283", name="Kigali City", level=1, parent=self.country)
        self.state2 = AdminBoundary.create(osm_id="171591", name="Eastern Province", level=1, parent=self.country)
        self.district1 = AdminBoundary.create(osm_id="R1711131", name="Gatsibo", level=2, parent=self.state2)
        self.district2 = AdminBoundary.create(osm_id="1711163", name="Kayônza", level=2, parent=self.state2)
        self.district3 = AdminBoundary.create(osm_id="3963734", name="Nyarugenge", level=2, parent=self.state1)
        self.district4 = AdminBoundary.create(osm_id="1711142", name="Rwamagana", level=2, parent=self.state2)
        self.ward1 = AdminBoundary.create(osm_id="171113181", name="Kageyo", level=3, parent=self.district1)
        self.ward2 = AdminBoundary.create(osm_id="171116381", name="Kabare", level=3, parent=self.district2)
        self.ward3 = AdminBoundary.create(osm_id="VMN.49.1_1", name="Bukure", level=3, parent=self.district4)

        BoundaryAlias.create(self.org, self.admin, self.state1, "Kigari")

        self.country.update_path()

        self.org.country = self.country
        self.org.save(update_fields=("country",))

    def clear_cache(self):
        """
        Clears the redis cache. We are extra paranoid here and check that redis host is 'localhost'
        Redis 10 is our testing redis db
        """
        if settings.REDIS_HOST != "localhost":
            raise ValueError(f"Expected redis test server host to be: 'localhost', got '{settings.REDIS_HOST}'")

        r = redis.StrictRedis(host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=10)
        r.flushdb()

    def clear_storage(self):
        """
        If a test has written files to storage, it should remove them by calling this
        """
        shutil.rmtree("%s/%s" % (settings.MEDIA_ROOT, settings.STORAGE_ROOT_DIR), ignore_errors=True)

    def import_file(self, filename, site="http://rapidpro.io", substitutions=None, legacy=False):
        data = self.get_import_json(filename, substitutions=substitutions)
        self.org.import_app(data, self.admin, site=site, legacy=legacy)

    def get_import_json(self, filename, substitutions=None):
        handle = open("%s/test_flows/%s.json" % (settings.MEDIA_ROOT, filename), "r+")
        data = handle.read()
        handle.close()

        if substitutions:
            for k, v in substitutions.items():
                print('Replacing "%s" with "%s"' % (k, v))
                data = data.replace(k, str(v))

        return json.loads(data)

    def get_flow(self, filename, substitutions=None, legacy=False):
        now = timezone.now()

        self.import_file(filename, substitutions=substitutions, legacy=legacy)

        imported_flows = Flow.objects.filter(org=self.org, saved_on__gt=now)
        flow = imported_flows.order_by("id").last()

        assert flow, f"no flow imported from {filename}.json"

        flow.org = self.org
        return flow

    def get_flow_json(self, filename, substitutions=None):
        data = self.get_import_json(filename, substitutions=substitutions)
        return data["flows"][0]

    def create_contact(self, name=None, number=None, twitter=None, urn=None, fields=None, **kwargs):
        """
        Create a contact in the master test org
        """

        org = kwargs.pop("org", None) or self.org
        user = kwargs.pop("user", None) or self.user

        urns = []
        if number:
            urns.append(URN.from_tel(number))
        if twitter:
            urns.append(URN.from_twitter(twitter))
        if urn:
            urns.append(urn)

        assert name or urns, "contact should have a name or a contact"

        kwargs["name"] = name
        kwargs["urns"] = urns

        contact = Contact.get_or_create_by_urns(org, user, **kwargs)

        if fields:
            update_fields_locally(user, contact, fields)

        return contact

    def create_group(self, name, contacts=(), query=None, org=None):
        assert not (contacts and query), "can't provide contact list for a dynamic group"

        if query:
            return ContactGroup.create_dynamic(org or self.org, self.user, name, query=query)
        else:
            group = ContactGroup.create_static(org or self.org, self.user, name)
            if contacts:
                group.contacts.add(*contacts)
            return group

    def create_label(self, name, org=None):
        return Label.get_or_create(org or self.org, self.user, name)

    def create_field(self, key, label, value_type=Value.TYPE_TEXT, org=None):
        return ContactField.user_fields.create(
            org=org or self.org,
            key=key,
            label=label,
            value_type=value_type,
            created_by=self.admin,
            modified_by=self.admin,
        )

    def create_incoming_msg(
        self,
        contact,
        text,
        channel=None,
        msg_type=None,
        attachments=(),
        status=HANDLED,
        visibility=Msg.VISIBILITY_VISIBLE,
        created_on=None,
        external_id=None,
        surveyor=False,
    ):
        assert not msg_type or status != PENDING, "pending messages don't have a msg type"

        if status == HANDLED and not msg_type:
            msg_type = INBOX

        return self._create_msg(
            contact,
            text,
            INCOMING,
            channel,
            msg_type,
            attachments,
            status,
            created_on,
            visibility=visibility,
            external_id=external_id,
            surveyor=surveyor,
        )

    def create_incoming_msgs(self, contact, count):
        for m in range(count):
            self.create_incoming_msg(contact, f"Test {m}")

    def create_outgoing_msg(
        self,
        contact,
        text,
        channel=None,
        msg_type=INBOX,
        attachments=(),
        status=SENT,
        created_on=None,
        sent_on=None,
        high_priority=False,
        response_to=None,
        surveyor=False,
    ):
        if status == SENT and not sent_on:
            sent_on = timezone.now()

        return self._create_msg(
            contact,
            text,
            OUTGOING,
            channel,
            msg_type,
            attachments,
            status,
            created_on,
            sent_on,
            high_priority=high_priority,
            response_to=response_to,
            surveyor=surveyor,
        )

    def _create_msg(
        self,
        contact,
        text,
        direction,
        channel,
        msg_type,
        attachments,
        status,
        created_on,
        sent_on=None,
        visibility=Msg.VISIBILITY_VISIBLE,
        external_id=None,
        high_priority=False,
        response_to=None,
        surveyor=False,
        broadcast=None,
    ):
        assert not (surveyor and channel), "surveyor messages don't have channels"
        assert not channel or channel.org == contact.org, "channel belong to different org than contact"

        org = contact.org

        if surveyor:
            contact_urn = None
            channel = None
            topup_id = None
        else:
            # a simplified version of how channels are chosen
            contact_urn = contact.get_urn()
            if not channel:
                if contact_urn and contact_urn.channel:
                    channel = contact_urn.channel
                else:
                    channel = org.channels.filter(is_active=True, schemes__contains=[contact_urn.scheme]).first()

            (topup_id, amount) = org.decrement_credit()

        return Msg.objects.create(
            org=org,
            direction=direction,
            contact=contact,
            contact_urn=contact_urn,
            text=text,
            channel=channel,
            topup_id=topup_id,
            status=status,
            msg_type=msg_type,
            attachments=attachments,
            visibility=visibility,
            external_id=external_id,
            high_priority=high_priority,
            response_to=response_to,
            created_on=created_on or timezone.now(),
            sent_on=sent_on,
            broadcast=broadcast,
        )

    def create_broadcast(self, user, text, contacts=(), groups=(), response_to=None, msg_status=SENT):
        bcast = Broadcast.create(self.org, user, text, contacts=contacts, groups=groups, status=SENT)

        contacts = set(bcast.contacts.all())
        for group in bcast.groups.all():
            contacts.update(group.contacts.all())

        for contact in contacts:
            self._create_msg(
                contact,
                text,
                OUTGOING,
                channel=None,
                msg_type=INBOX,
                attachments=(),
                status=msg_status,
                created_on=timezone.now(),
                sent_on=timezone.now(),
                response_to=response_to,
                broadcast=bcast,
            )

        return bcast

    def create_flow(self, definition=None, **kwargs):
        if "org" not in kwargs:
            kwargs["org"] = self.org
        if "user" not in kwargs:
            kwargs["user"] = self.user
        if "name" not in kwargs:
            kwargs["name"] = "Color Flow"

        flow = Flow.create(**kwargs)
        if not definition:
            # if definition isn't provided, generate simple single message flow
            node_uuid = str(uuid4())
            definition = {
                "version": "10",
                "flow_type": "F",
                "base_language": "eng",
                "entry": node_uuid,
                "action_sets": [
                    {
                        "uuid": node_uuid,
                        "x": 0,
                        "y": 0,
                        "actions": [
                            {"msg": {"eng": "Hey everybody!"}, "media": {}, "send_all": False, "type": "reply"}
                        ],
                        "destination": None,
                    }
                ],
                "rule_sets": [],
            }

        flow.version_number = definition["version"]
        flow.save()

        json_flow = FlowRevision.migrate_definition(definition, flow, to_version=Flow.FINAL_LEGACY_VERSION)
        flow.update(json_flow)

        return flow

    def create_incoming_call(self, flow, contact, status=IVRCall.COMPLETED):
        """
        Create something that looks like an incoming IVR call handled by mailroom
        """
        call = IVRCall.objects.create(
            org=self.org,
            channel=self.channel,
            direction=IVRCall.INCOMING,
            contact=contact,
            contact_urn=contact.get_urn(),
            status=status,
            duration=15,
        )
        session = FlowSession.objects.create(uuid=uuid4(), org=contact.org, contact=contact, connection=call)
        FlowRun.objects.create(org=self.org, flow=flow, contact=contact, connection=call, session=session)
        Msg.objects.create(
            org=self.org,
            channel=self.channel,
            connection=call,
            direction="O",
            contact=contact,
            contact_urn=contact.get_urn(),
            text="Hello",
            status="S",
            created_on=timezone.now(),
        )
        ChannelLog.objects.create(
            channel=self.channel,
            connection=call,
            request='{"say": "Hello"}',
            response='{"status": "%s"}' % ("error" if status == IVRCall.FAILED else "OK"),
            url="https://acme-calls.com/reply",
            method="POST",
            is_error=status == IVRCall.FAILED,
            response_status=200,
            description="Looks good",
        )
        return call

    def create_archive(
        self, archive_type, period, start_date, records=(), needs_deletion=False, rollup_of=(), s3=None, org=None
    ):
        archive_hash = uuid4().hex
        bucket = "s3-bucket"
        key = f"things/{archive_hash}.jsonl.gz"
        if s3:
            s3.put_jsonl(bucket, key, records)

        archive = Archive.objects.create(
            org=org or self.org,
            archive_type=archive_type,
            size=10,
            hash=archive_hash,
            url=f"http://{bucket}.aws.com/{key}",
            record_count=len(records),
            start_date=start_date,
            period=period,
            build_time=23425,
            needs_deletion=needs_deletion,
        )
        if rollup_of:
            Archive.objects.filter(id__in=[a.id for a in rollup_of]).update(rollup=archive)
        return archive

    def set_contact_field(self, contact, key, value, legacy_handle=False):
        update_field_locally(self.admin, contact, key, value)

        if legacy_handle:
            contact.handle_update(fields=[key])

    def bulk_release(self, objs, delete=False, user=None):
        for obj in objs:
            if user:
                obj.release(user)
            else:
                obj.release()

            if obj.id and delete:
                obj.delete()

    def releaseContacts(self, delete=False):
        self.bulk_release(Contact.objects.all(), delete=delete, user=self.admin)

    def assertOutbox(self, outbox_index, from_email, subject, body, recipients):
        self.assertEqual(len(mail.outbox), outbox_index + 1)
        email = mail.outbox[outbox_index]
        self.assertEqual(email.from_email, from_email)
        self.assertEqual(email.subject, subject)
        self.assertEqual(email.body, body)
        self.assertEqual(email.recipients(), recipients)

    def assertExcelRow(self, sheet, row_num, values, tz=None):
        """
        Asserts the cell values in the given worksheet row. Date values are converted using the provided timezone.
        """

        row = tuple(sheet.rows)[row_num]

        for index, expected in enumerate(values):
            actual = row[index].value if index < len(row) else None
            if actual is None:
                actual = ""

            # if expected value is datetime, localize and remove microseconds since Excel doesn't have that accuracy
            if tz and isinstance(expected, datetime):
                expected = expected.astimezone(tz).replace(microsecond=0, tzinfo=None)

            if isinstance(expected, UUID):
                expected = str(expected)

            self.assertEqual(expected, actual, f"mismatch in cell {chr(index+65)}{row_num+1}")

    def assertExcelSheet(self, sheet, rows, tz=None):
        """
        Asserts the row values in the given worksheet
        """
        self.assertEqual(len(list(sheet.rows)), len(rows))

        for r, row in enumerate(rows):
            self.assertExcelRow(sheet, r, row, tz)

    def assertResponseError(self, response, field, message, status_code=400):
        self.assertEqual(status_code, response.status_code)
        body = response.json()
        self.assertIn(field, body)
        self.assertTrue(message, isinstance(body[field], (list, tuple)))
        self.assertIn(message, body[field])


class TembaTest(TembaTestMixin, SmartminTest):
    """
    Base class for tests where each test executes in a DB transaction
    """

    def setUp(self):
        self.setUpOrgs()

    def tearDown(self):
        clear_flow_users()


class TembaNonAtomicTest(TembaTestMixin, SmartminTestMixin, TransactionTestCase):
    """
    Base class for tests that can't be wrapped in DB transactions
    """

    pass


class AnonymousOrg(object):
    """
    Makes the given org temporarily anonymous
    """

    def __init__(self, org):
        self.org = org

    def __enter__(self):
        self.org.is_anon = True
        self.org.save(update_fields=("is_anon",))

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.org.is_anon = False
        self.org.save(update_fields=("is_anon",))


class MigrationTest(TembaTest):
    app = None
    migrate_from = None
    migrate_to = None

    def setUp(self):
        assert (
            self.migrate_from and self.migrate_to
        ), "TestCase '{}' must define migrate_from and migrate_to properties".format(type(self).__name__)

        # set up our temba test
        super().setUp()

        self.migrate_from = [(self.app, self.migrate_from)]
        self.migrate_to = [(self.app, self.migrate_to)]
        executor = MigrationExecutor(connection)
        old_apps = executor.loader.project_state(self.migrate_from).apps

        # Reverse to the original migration
        executor.migrate(self.migrate_from)

        self.setUpBeforeMigration(old_apps)

        # Run the migration to test
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()  # reload.
        executor.migrate(self.migrate_to)

        self.apps = executor.loader.project_state(self.migrate_to).apps

    def setUpBeforeMigration(self, apps):
        pass
