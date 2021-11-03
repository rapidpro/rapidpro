import shutil
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytz
import redis
from smartmin.tests import SmartminTest, SmartminTestMixin

from django.conf import settings
from django.contrib.auth.models import Group, User
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone

from temba.archives.models import Archive
from temba.channels.models import Channel, ChannelEvent, ChannelLog
from temba.contacts.models import URN, ContactField, ContactGroup, ContactImport, ContactURN
from temba.flows.models import Flow, FlowRun, FlowSession, clear_flow_users
from temba.ivr.models import IVRCall
from temba.locations.models import AdminBoundary, BoundaryAlias
from temba.msgs.models import Broadcast, Label, Msg
from temba.orgs.models import Org, OrgRole
from temba.tickets.models import Ticket, TicketEvent
from temba.utils import json
from temba.utils.uuid import UUID, uuid4

from .mailroom import create_contact_locally, decrement_credit, update_field_locally
from .s3 import jsonlgz_encode


def add_testing_flag_to_context(*args):
    return dict(testing=settings.TESTING)


class TembaTestMixin:
    databases = ("default", "readonly")

    def setUpOrgs(self):
        # make sure we start off without any service users
        Group.objects.get(name="Service Users").user_set.clear()

        self.clear_cache()

        self.create_anonymous_user()

        self.superuser = User.objects.create_superuser(username="super", email="super@user.com", password="super")

        # create different user types
        self.non_org_user = self.create_user("NonOrg")
        self.admin = self.create_user("Administrator")
        self.editor = self.create_user("Editor")
        self.user = self.create_user("User", ("Viewers",))
        self.agent = self.create_user("Agent")
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

        self.agent.set_org(self.org)
        self.org.agents.add(self.agent)

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

    def make_beta(self, user):
        user.groups.add(Group.objects.get(name="Beta"))

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

    def login(self, user, update_last_auth_on: bool = True):
        self.assertTrue(
            self.client.login(username=user.username, password=user.username),
            "Couldn't login as %(user)s:%(user)s" % dict(user=user.username),
        )
        if update_last_auth_on:
            user.record_auth()

    def import_file(self, filename, site="http://rapidpro.io", substitutions=None):
        data = self.get_import_json(filename, substitutions=substitutions)
        self.org.import_app(data, self.admin, site=site)

    def get_import_json(self, filename, substitutions=None):
        handle = open("%s/test_flows/%s.json" % (settings.MEDIA_ROOT, filename), "r+")
        data = handle.read()
        handle.close()

        if substitutions:
            for k, v in substitutions.items():
                print('Replacing "%s" with "%s"' % (k, v))
                data = data.replace(k, str(v))

        return json.loads(data)

    def get_flow(self, filename, substitutions=None, name=None):
        now = timezone.now()

        self.import_file(filename, substitutions=substitutions)

        imported_flows = Flow.objects.filter(org=self.org, saved_on__gt=now)
        flow = imported_flows.filter(name=name).first() if name else imported_flows.order_by("id").last()

        assert flow, f"no flow imported from {filename}.json"

        flow.org = self.org
        return flow

    def get_flow_json(self, filename, substitutions=None):
        data = self.get_import_json(filename, substitutions=substitutions)
        return data["flows"][0]

    def create_contact(
        self, name=None, *, language=None, phone=None, urns=None, fields=None, org=None, user=None, last_seen_on=None
    ):
        """
        Create a new contact
        """

        org = org or self.org
        user = user or self.user
        urns = [URN.from_tel(phone)] if phone else urns

        return create_contact_locally(
            org, user, name, language, urns or [], fields or {}, group_uuids=[], last_seen_on=last_seen_on
        )

    def create_group(self, name, contacts=(), query=None, org=None):
        assert not (contacts and query), "can't provide contact list for a smart group"

        if query:
            return ContactGroup.create_dynamic(org or self.org, self.user, name, query=query)
        else:
            group = ContactGroup.create_static(org or self.org, self.user, name)
            if contacts:
                group.contacts.add(*contacts)
            return group

    def create_label(self, name, org=None):
        return Label.get_or_create(org or self.org, self.user, name)

    def create_field(self, key, label, value_type=ContactField.TYPE_TEXT, org=None):
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
        status=Msg.STATUS_HANDLED,
        visibility=Msg.VISIBILITY_VISIBLE,
        created_on=None,
        external_id=None,
        surveyor=False,
    ):
        assert not msg_type or status != Msg.STATUS_PENDING, "pending messages don't have a msg type"

        if status == Msg.STATUS_HANDLED and not msg_type:
            msg_type = Msg.TYPE_INBOX

        return self._create_msg(
            contact,
            text,
            Msg.DIRECTION_IN,
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
        msg_type=Msg.TYPE_INBOX,
        attachments=(),
        quick_replies=(),
        status=Msg.STATUS_SENT,
        created_on=None,
        sent_on=None,
        high_priority=False,
        response_to=None,
        surveyor=False,
        next_attempt=None,
    ):
        if status in (Msg.STATUS_WIRED, Msg.STATUS_SENT, Msg.STATUS_DELIVERED) and not sent_on:
            sent_on = timezone.now()

        metadata = {}
        if quick_replies:
            metadata["quick_replies"] = quick_replies

        return self._create_msg(
            contact,
            text,
            Msg.DIRECTION_OUT,
            channel,
            msg_type,
            attachments,
            status,
            created_on,
            sent_on,
            high_priority=high_priority,
            response_to=response_to,
            surveyor=surveyor,
            metadata=metadata,
            next_attempt=next_attempt,
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
        metadata=None,
        next_attempt=None,
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

            topup_id = decrement_credit(org)

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
            metadata=metadata,
            next_attempt=next_attempt,
        )

    def create_broadcast(
        self,
        user,
        text,
        contacts=(),
        groups=(),
        response_to=None,
        msg_status=Msg.STATUS_SENT,
        parent=None,
        schedule=None,
    ):
        bcast = Broadcast.create(
            self.org,
            user,
            text,
            contacts=contacts,
            groups=groups,
            status=Msg.STATUS_SENT,
            parent=parent,
            schedule=schedule,
        )

        contacts = set(bcast.contacts.all())
        for group in bcast.groups.all():
            contacts.update(group.contacts.all())

        if not schedule:
            for contact in contacts:
                self._create_msg(
                    contact,
                    text,
                    Msg.DIRECTION_OUT,
                    channel=None,
                    msg_type=Msg.TYPE_INBOX,
                    attachments=(),
                    status=msg_status,
                    created_on=timezone.now(),
                    sent_on=timezone.now(),
                    response_to=response_to,
                    broadcast=bcast,
                )

        return bcast

    def create_flow(self, name="Test Flow", *, flow_type=Flow.TYPE_MESSAGE, nodes=None, is_system=False, org=None):
        org = org or self.org
        flow = Flow.create(org, self.admin, name, flow_type=flow_type, is_system=is_system)
        if not nodes:
            nodes = [
                {
                    "uuid": "f3d5ccd0-fee0-4955-bcb7-21613f049eae",
                    "actions": [
                        {"uuid": "f661e3f0-5148-4397-92ef-925629ad444d", "type": "send_msg", "text": "Hey everybody!"}
                    ],
                    "exits": [{"uuid": "72a3f1da-bde1-4549-a986-d35809807be8"}],
                }
            ]
        definition = {
            "uuid": str(uuid4()),
            "name": name,
            "type": Flow.GOFLOW_TYPES[flow_type],
            "revision": 1,
            "spec_version": "13.1.0",
            "expire_after_minutes": Flow.DEFAULT_EXPIRES_AFTER,
            "language": "eng",
            "nodes": nodes,
        }

        flow.version_number = definition["spec_version"]
        flow.save()

        json_flow = Flow.migrate_definition(definition, flow)
        flow.save_revision(self.admin, json_flow)

        return flow

    def create_incoming_call(self, flow, contact, status=IVRCall.STATUS_COMPLETED):
        """
        Create something that looks like an incoming IVR call handled by mailroom
        """
        call = IVRCall.objects.create(
            org=self.org,
            channel=self.channel,
            direction=IVRCall.DIRECTION_IN,
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
            sent_on=timezone.now(),
            created_on=timezone.now(),
        )
        ChannelLog.objects.create(
            channel=self.channel,
            connection=call,
            request='{"say": "Hello"}',
            response='{"status": "%s"}' % ("error" if status == IVRCall.STATUS_FAILED else "OK"),
            url="https://acme-calls.com/reply",
            method="POST",
            is_error=status == IVRCall.STATUS_FAILED,
            response_status=200,
            description="Looks good",
        )
        return call

    def create_archive(
        self, archive_type, period, start_date, records=(), needs_deletion=False, rollup_of=(), s3=None, org=None
    ):
        org = org or self.org
        body, md5, size = jsonlgz_encode(records)
        bucket = "s3-bucket"
        type_code = "run" if archive_type == Archive.TYPE_FLOWRUN else "message"
        date_code = start_date.strftime("%Y%m") if period == "M" else start_date.strftime("%Y%m%d")
        key = f"{org.id}/{type_code}_{period}{date_code}_{md5}.jsonl.gz"

        if s3:
            s3.put_object(bucket, key, body)

        archive = Archive.objects.create(
            org=org,
            archive_type=archive_type,
            size=size,
            hash=md5,
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

    def create_contact_import(self, path):
        with open(path, "rb") as f:
            mappings, num_records = ContactImport.try_to_parse(self.org, f, path)
            return ContactImport.objects.create(
                org=self.org,
                original_filename=path,
                file=SimpleUploadedFile(f.name, f.read()),
                mappings=mappings,
                num_records=num_records,
                group_name=Path(path).stem.title(),
                created_by=self.admin,
                modified_by=self.admin,
            )

    def create_channel(
        self,
        channel_type: str,
        name: str,
        address: str,
        role=None,
        schemes=None,
        country=None,
        secret=None,
        config=None,
        org=None,
    ):
        channel_type = Channel.get_type_from_code(channel_type)

        return Channel.objects.create(
            org=org or self.org,
            country=country,
            channel_type=channel_type.code,
            name=name,
            address=address,
            config=config or {},
            role=role or Channel.DEFAULT_ROLE,
            secret=secret,
            schemes=schemes or channel_type.schemes,
            created_by=self.admin,
            modified_by=self.admin,
        )

    def create_channel_event(self, channel, urn, event_type, occurred_on=None, extra=None):
        urn_obj = ContactURN.lookup(channel.org, urn, country_code=channel.country)
        if urn_obj:
            contact = urn_obj.contact
        else:
            contact = self.create_contact(urns=[urn])
            urn_obj = contact.urns.get()

        return ChannelEvent.objects.create(
            org=channel.org,
            channel=channel,
            contact=contact,
            contact_urn=urn_obj,
            occurred_on=occurred_on or timezone.now(),
            event_type=event_type,
            extra=extra,
        )

    def create_ticket(
        self,
        ticketer,
        contact,
        body: str,
        topic=None,
        assignee=None,
        opened_on=None,
        opened_by=None,
        closed_on=None,
        closed_by=None,
    ):
        if not opened_on:
            opened_on = timezone.now()

        ticket = Ticket.objects.create(
            org=ticketer.org,
            ticketer=ticketer,
            contact=contact,
            topic=topic or ticketer.org.default_ticket_topic,
            body=body,
            status=Ticket.STATUS_CLOSED if closed_on else Ticket.STATUS_OPEN,
            assignee=assignee,
            opened_on=opened_on,
            closed_on=closed_on,
        )
        TicketEvent.objects.create(
            org=ticket.org, contact=contact, ticket=ticket, event_type=TicketEvent.TYPE_OPENED, created_by=opened_by
        )
        if assignee:
            TicketEvent.objects.create(
                org=ticket.org,
                contact=contact,
                ticket=ticket,
                event_type=TicketEvent.TYPE_ASSIGNED,
                created_by=opened_by,
            )
        if closed_on:
            TicketEvent.objects.create(
                org=ticket.org,
                contact=contact,
                ticket=ticket,
                event_type=TicketEvent.TYPE_CLOSED,
                created_by=closed_by,
            )

        return ticket

    def set_contact_field(self, contact, key, value):
        update_field_locally(self.admin, contact, key, value)

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

    def assertPathValue(self, container: dict, path: str, expected, msg: str):
        """
        Asserts a value at a path in a container, e.g.
          assertPathValue({"foo": "bar", "zed": 123}, "foo", "bar")
          assertPathValue({"foo": {"bar": 123}}, "foo__bar", 123)
        """
        actual = container
        for key in path.split("__"):
            if key not in actual:
                self.fail(self._formatMessage(msg, f"path {path} not found in {json.dumps(container)}"))
            actual = actual[key]
        self.assertEqual(actual, expected, self._formatMessage(msg, f"value mismatch at {path}"))

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

        # OrgRole.group is a cached property so get that cached before test starts to avoid query count differences
        # when a test is first to request it and when it's not.
        for role in OrgRole:
            role.group  # noqa

    def tearDown(self):
        clear_flow_users()

    def mockReadOnly(self, assert_models: set = None):
        return MockReadOnly(self, assert_models=assert_models)


class TembaNonAtomicTest(TembaTestMixin, SmartminTestMixin, TransactionTestCase):
    """
    Base class for tests that can't be wrapped in DB transactions
    """

    pass


class AnonymousOrg:
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


class MockReadOnly:
    """
    Context manager which mocks calls to .using("readonly") on querysets and records the model types.
    """

    def __init__(self, test_class, assert_models: set = None):
        self.test_class = test_class
        self.assert_models = assert_models
        self.actual_models = set()

    def __enter__(self):
        self.patch_using = patch("django.db.models.query.QuerySet.using", autospec=True)
        mock_using = self.patch_using.start()

        def using(qs, alias):
            if alias == "readonly":
                self.actual_models.add(qs.model)
            return qs

        mock_using.side_effect = using

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.patch_using.stop()

        if self.assert_models:
            self.test_class.assertEqual(self.assert_models, self.actual_models)


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
