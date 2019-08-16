import inspect
import shutil
import sys
from datetime import datetime, timedelta
from io import StringIO
from unittest import skipIf
from unittest.mock import patch
from uuid import uuid4

import pytz
import redis
import regex
from future.moves.html.parser import HTMLParser
from smartmin.tests import SmartminTest

from django.conf import settings
from django.contrib.auth.models import Group, User
from django.core import mail
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test.runner import DiscoverRunner
from django.utils import timezone
from django.utils.encoding import force_bytes, force_text

from temba.channels.models import Channel
from temba.contacts.models import URN, Contact, ContactField, ContactGroup
from temba.flows.models import ActionSet, Flow, FlowRevision, FlowRun, RuleSet, clear_flow_users
from temba.ivr.models import IVRCall
from temba.locations.models import AdminBoundary
from temba.msgs.models import INCOMING, Msg
from temba.orgs.models import Org
from temba.utils import dict_to_struct, json
from temba.values.constants import Value

from .http import MockServer

mock_server = MockServer()


class TembaTestRunner(DiscoverRunner):
    """
    Adds the ability to exclude tests in given packages to the default test runner, and starts the mock server instance
    """

    def __init__(self, *args, **kwargs):
        settings.TESTING = True

        super().__init__(*args, **kwargs)
        mock_server.start()

    def build_suite(self, *args, **kwargs):
        suite = super().build_suite(*args, **kwargs)
        excluded = getattr(settings, "TEST_EXCLUDE", [])
        if not getattr(settings, "RUN_ALL_TESTS", False):
            tests = []
            for case in suite:
                pkg = case.__class__.__module__.split(".")[0]
                if pkg not in excluded:
                    tests.append(case)
            suite._tests = tests
        return suite

    def run_suite(self, suite, **kwargs):

        return super().run_suite(suite, **kwargs)


def add_testing_flag_to_context(*args):
    return dict(testing=settings.TESTING)


def skip_if_no_mailroom(test):
    """
    Skip a test if mailroom isn't configured
    """
    return skipIf(not settings.MAILROOM_URL, "this test can't be run without a mailroom instance")(test)


class ESMockWithScroll:
    def __init__(self, data=None):
        self.mock_es = patch("temba.utils.es.ES")

        self.data = data if data is not None else []

    def __enter__(self):
        patched_object = self.mock_es.start()

        patched_object.search.return_value = {
            "_shards": {"failed": 0, "successful": 10, "total": 10},
            "timed_out": False,
            "took": 1,
            "_scroll_id": "1",
            "hits": {"hits": self.data},
        }
        patched_object.scroll.return_value = {
            "_shards": {"failed": 0, "successful": 10, "total": 10},
            "timed_out": False,
            "took": 1,
            "_scroll_id": "1",
            "hits": {"hits": []},
        }

        return patched_object()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.mock_es.stop()


class ESMockWithScrollMultiple(ESMockWithScroll):
    def __enter__(self):
        patched_object = self.mock_es.start()

        patched_object.search.side_effect = [
            {
                "_shards": {"failed": 0, "successful": 10, "total": 10},
                "timed_out": False,
                "took": 1,
                "_scroll_id": "1",
                "hits": {"hits": return_value},
            }
            for return_value in self.data
        ]
        patched_object.scroll.side_effect = [
            {
                "_shards": {"failed": 0, "successful": 10, "total": 10},
                "timed_out": False,
                "took": 1,
                "_scroll_id": "1",
                "hits": {"hits": []},
            }
            for _ in range(len(self.data))
        ]

        return patched_object()


class TembaTestMixin:
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

    def update_action_field(self, flow, action_uuid, key, value):
        action_json = self.get_action_json(flow, action_uuid)
        action_json[key] = value
        self.update_action_json(flow, action_json)

    def update_action_json(self, flow, action_json):
        """
        Given an action json_dict, replaces the existing action by uuid
        """
        flowdef = flow.as_json()
        for i, actionset in enumerate(flowdef["action_sets"]):
            for j, prev_action in enumerate(actionset["actions"]):
                if action_json["uuid"] == prev_action["uuid"]:
                    flowdef["action_sets"][i]["actions"][j] = action_json
                    flow.update(flowdef, self.admin)
                    return
        self.fail("Couldn't find action with uuid %s" % action_json["uuid"])

    def get_action_json(self, flow, uuid):
        """
        Gets the action json dict from the given flow
        """
        flowdef = flow.as_json()
        for actionset in flowdef["action_sets"]:
            for action in actionset["actions"]:
                if action["uuid"] == uuid:
                    return action
        self.fail("Couldn't find action with uuid %s" % uuid)

    def get_flow(self, filename, substitutions=None):
        now = timezone.now()

        self.import_file(filename, substitutions=substitutions)

        imported_flows = Flow.objects.filter(org=self.org, saved_on__gt=now)
        flow = imported_flows.order_by("id").last()

        assert flow, f"no flow imported from {filename}.json"

        flow.org = self.org
        return flow

    def get_flow_json(self, filename, substitutions=None):
        data = self.get_import_json(filename, substitutions=substitutions)
        return data["flows"][0]

    def create_secondary_org(self, topup_size=None):
        self.admin2 = self.create_user("Administrator2")
        self.org2 = Org.objects.create(
            name="Trileet Inc.",
            timezone=pytz.timezone("Africa/Kigali"),
            brand="rapidpro.io",
            created_by=self.admin2,
            modified_by=self.admin2,
        )
        self.org2.administrators.add(self.admin2)
        self.admin2.set_org(self.org)

        self.org2.initialize(topup_size=topup_size)

    def create_contact(self, name=None, number=None, twitter=None, twitterid=None, urn=None, **kwargs):
        """
        Create a contact in the master test org
        """
        urns = []
        if number:
            urns.append(URN.from_tel(number))
        if twitter:
            urns.append(URN.from_twitter(twitter))
        if twitterid:
            urns.append(URN.from_twitterid(twitterid))
        if urn:
            urns.append(urn)

        if not name and not urns:  # pragma: no cover
            raise ValueError("Need a name or URN to create a contact")

        kwargs["name"] = name
        kwargs["urns"] = urns

        if "org" not in kwargs:
            kwargs["org"] = self.org
        if "user" not in kwargs:
            kwargs["user"] = self.user

        return Contact.get_or_create_by_urns(**kwargs)

    def create_group(self, name, contacts=(), query=None):
        if contacts and query:
            raise ValueError("Can't provide contact list for a dynamic group")

        if query:
            return ContactGroup.create_dynamic(self.org, self.user, name, query=query)
        else:
            group = ContactGroup.create_static(self.org, self.user, name)
            if contacts:
                group.contacts.add(*contacts)
            return group

    def create_field(self, key, label, value_type=Value.TYPE_TEXT):
        return ContactField.user_fields.create(
            org=self.org, key=key, label=label, value_type=value_type, created_by=self.admin, modified_by=self.admin
        )

    def create_msg(self, **kwargs):
        if "org" not in kwargs:
            kwargs["org"] = self.org
        if "channel" not in kwargs:
            kwargs["channel"] = self.channel
        if "contact_urn" not in kwargs:
            kwargs["contact_urn"] = kwargs["contact"].get_urn()
        if "created_on" not in kwargs:
            kwargs["created_on"] = timezone.now()

        (kwargs["topup_id"], amount) = kwargs["org"].decrement_credit()

        return Msg.objects.create(**kwargs)

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
                "version": 10,
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

        json_flow = FlowRevision.migrate_definition(definition, flow)
        flow.update(json_flow)

        return flow

    def update_destination(self, flow, source, destination):
        flow_json = flow.as_json()

        for actionset in flow_json.get("action_sets"):
            if actionset.get("uuid") == source:
                actionset["destination"] = destination

        for ruleset in flow_json.get("rule_sets"):
            for rule in ruleset.get("rules"):
                if rule.get("uuid") == source:
                    rule["destination"] = destination

        flow.update(flow_json)
        return Flow.objects.get(pk=flow.pk)

    def update_destination_no_check(self, flow, node, destination, rule=None):  # pragma: no cover
        """ Update the destination without doing a cycle check """
        # look up our destination, we need this in order to set the correct destination_type
        destination_type = Flow.NODE_TYPE_ACTIONSET
        action_destination = Flow.get_node(flow, destination, destination_type)
        if not action_destination:
            destination_type = Flow.NODE_TYPE_RULESET
            ruleset_destination = Flow.get_node(flow, destination, destination_type)
            self.assertTrue(ruleset_destination, "Unable to find new destination with uuid: %s" % destination)

        actionset = ActionSet.get(flow, node)
        if actionset:
            actionset.destination = destination
            actionset.destination_type = destination_type
            actionset.save()

        ruleset = RuleSet.get(flow, node)
        if ruleset:
            rules = ruleset.get_rules()
            for r in rules:
                if r.uuid == rule:
                    r.destination = destination
                    r.destination_type = destination_type
            ruleset.set_rules(rules)
            ruleset.save()
        else:
            self.fail("Couldn't find node with uuid: %s" % node)

    def mockRequest(self, method, path_pattern, content, content_type="text/plain", status=200):
        return self.mock_server.mock_request(method, path_pattern, content, content_type, status)

    def assertOutbox(self, outbox_index, from_email, subject, body, recipients):
        self.assertEqual(len(mail.outbox), outbox_index + 1)
        email = mail.outbox[outbox_index]
        self.assertEqual(email.from_email, from_email)
        self.assertEqual(email.subject, subject)
        self.assertEqual(email.body, body)
        self.assertEqual(email.recipients(), recipients)

    def assertMockedRequest(self, mock_request, data=None, **headers):
        if not mock_request.requested:
            self.fail("expected %s %s to have been requested" % (mock_request.method, mock_request.path))

        if data is not None:
            self.assertEqual(mock_request.data, data)

        # check any provided header values
        for key, val in headers.items():
            self.assertEqual(mock_request.headers.get(key.replace("_", "-")), val)

    def assertAllRequestsMade(self):
        if self.mock_server.mocked_requests:
            self.fail(
                "test has %d unused mock requests: %s"
                % (len(mock_server.mocked_requests), mock_server.mocked_requests)
            )
        if self.mock_server.unexpected_requests:
            self.fail(
                "test made %d expected requests: %s"
                % (len(mock_server.unexpected_requests), mock_server.unexpected_requests)
            )

    def assertExcelRow(self, sheet, row_num, values, tz=None):
        """
        Asserts the cell values in the given worksheet row. Date values are converted using the provided timezone.
        """
        expected_values = []
        for expected in values:
            # if expected value is datetime, localize and remove microseconds
            if isinstance(expected, datetime):
                expected = expected.astimezone(tz).replace(microsecond=0, tzinfo=None)

            expected_values.append(expected)

        rows = tuple(sheet.rows)

        actual_values = []
        for cell in rows[row_num]:
            actual = cell.value

            if actual is None:
                actual = ""

            actual_values.append(actual)

        for index, expected in enumerate(expected_values):
            actual = actual_values[index]

            if isinstance(expected, datetime):
                close_enough = abs(expected - actual) < timedelta(seconds=1)
                self.assertTrue(close_enough, "Datetime value %s doesn't match %s" % (expected, actual))
            else:
                self.assertEqual(expected, actual)

    def assertExcelSheet(self, sheet, rows, tz=None):
        """
        Asserts the row values in the given worksheet
        """
        self.assertEqual(len(list(sheet.rows)), len(rows))

        for r, row in enumerate(rows):
            self.assertExcelRow(sheet, r, row, tz)

    def create_inbound_msgs(self, recipient, count):
        for m in range(count):
            self.create_msg(contact=recipient, direction="I", text="Test %d" % m)

    def get_verbosity(self):
        for s in reversed(inspect.stack()):
            options = s[0].f_locals.get("options")
            if isinstance(options, dict):
                return int(options["verbosity"])
        return 1

    def explain(self, query):
        cursor = connection.cursor()
        cursor.execute("explain %s" % query)
        plan = cursor.fetchall()
        indexes = []
        for match in regex.finditer(r"Index Scan using (.*?) on (.*?) \(cost", str(plan), regex.DOTALL):
            index = match.group(1).strip()
            table = match.group(2).strip()
            indexes.append((table, index))

        indexes = sorted(indexes, key=lambda i: i[0])
        return indexes


class TembaTest(TembaTestMixin, SmartminTest):
    def setUp(self):
        self.maxDiff = 4096
        self.mock_server = mock_server

        # if we are super verbose, turn on debug for sql queries
        if self.get_verbosity() > 2:
            settings.DEBUG = True

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

        # setup admin boundaries for Rwanda
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

        self.country.update_path()

        self.org = Org.objects.create(
            name="Temba",
            timezone=pytz.timezone("Africa/Kigali"),
            country=self.country,
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

        self.superuser.set_org(self.org)

        # welcome topup with 1000 credits
        self.welcome_topup = self.org.topups.all()[0]

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

    def tearDown(self):
        if self.get_verbosity() > 2:
            details = []
            for query in connection.queries:
                query = query["sql"]
                if "SAVEPOINT" not in query:
                    indexes = self.explain(query)
                    details.append(dict(query=query, indexes=indexes))

            for stat in details:
                print("")
                print(stat["query"])
                for table, index in stat["indexes"]:
                    print("  Index Used: %s.%s" % (table, index))

                if not len(stat["indexes"]):
                    print("  No Index Used")

            settings.DEBUG = False

        from temba.flows.models import clear_flow_users

        clear_flow_users()

        # clear any unused mock requests
        self.mock_server.mocked_requests = []

    def release(self, objs, delete=False, user=None):
        for obj in objs:
            if user:
                obj.release(user)
            else:
                obj.release()

            if obj.id and delete:
                obj.delete()

    def releaseChannels(self, delete=False):
        channels = Channel.objects.all()
        self.release(channels)
        if delete:
            for channel in channels:
                channel.counts.all().delete()
                channel.delete()

    def releaseIVRCalls(self, delete=False):
        self.release(IVRCall.objects.all(), delete=delete)

    def releaseMessages(self):
        self.release(Msg.objects.all())

    def releaseContacts(self, delete=False):
        self.release(Contact.objects.all(), delete=delete, user=self.admin)

    def releaseContactFields(self, delete=False):
        self.release(ContactField.all_fields.all(), delete=delete, user=self.admin)

    def releaseRuns(self, delete=False):
        self.release(FlowRun.objects.all(), delete=delete)

    def assertResponseError(self, response, field, message, status_code=400):
        self.assertEqual(status_code, response.status_code)
        body = response.json()
        self.assertTrue(message, field in body)
        self.assertTrue(message, isinstance(body[field], (list, tuple)))
        self.assertIn(message, body[field])


class FlowFileTest(TembaTest):
    def setUp(self):
        super().setUp()
        self.contact = self.create_contact("Ben Haggerty", number="+12065552020")

    def assertInUserGroups(self, contact, group_names, only=False):

        truth = [g.name for g in contact.user_groups.all()]
        for name in group_names:
            self.assertIn(name, truth)

        if only:
            self.assertEqual(
                len(group_names),
                len(truth),
                "Contact not found in expected group. expected: %s, was: %s" % (group_names, truth),
            )
            other_groups = contact.user_groups.exclude(name__in=group_names)
            if other_groups:
                self.fail("Contact found in unexpected group: %s" % other_groups)

    def assertLastResponse(self, message):
        response = Msg.objects.filter(contact=self.contact).order_by("-created_on", "-pk").first()

        self.assertTrue("Missing response from contact.", response)
        self.assertEqual(message, response.text)

    def send(self, message, contact=None):
        if not contact:
            contact = self.contact
        incoming = self.create_msg(direction=INCOMING, contact=contact, contact_urn=contact.get_urn(), text=message)

        # evaluate the inbound message against our triggers first
        from temba.triggers.models import Trigger

        if not Trigger.find_and_handle(incoming):
            Flow.find_and_handle(incoming)
        return Msg.objects.filter(response_to=incoming).order_by("pk").first()

    def send_message(
        self,
        flow,
        message,
        restart_participants=False,
        contact=None,
        initiate_flow=False,
        assert_reply=True,
        assert_handle=True,
    ):
        """
        Starts the flow, sends the message, returns the reply
        """
        if not contact:
            contact = self.contact

        incoming = self.create_msg(direction=INCOMING, contact=contact, contact_urn=contact.get_urn(), text=message)

        # start the flow
        if initiate_flow:
            flow.start(groups=[], contacts=[contact], restart_participants=restart_participants, start_msg=incoming)
        else:
            flow.start(groups=[], contacts=[contact], restart_participants=restart_participants)
            (handled, msgs) = Flow.find_and_handle(incoming)

            from temba.msgs import legacy

            legacy.mark_handled(incoming)

            if assert_handle:
                self.assertTrue(handled, "'%s' did not handle message as expected" % flow.name)
            else:
                self.assertFalse(handled, "'%s' handled message, was supposed to ignore" % flow.name)

        # our message should have gotten a reply
        if assert_reply:
            replies = Msg.objects.filter(response_to=incoming).order_by("pk")
            self.assertGreaterEqual(len(replies), 1)

            if len(replies) == 1:
                self.assertEqual(contact, replies.first().contact)
                return replies.first().text

            # if it's more than one, send back a list of replies
            return [reply.text for reply in replies]

        else:
            # assert we got no reply
            replies = Msg.objects.filter(response_to=incoming).order_by("pk")
            self.assertFalse(replies)

        return None


class MLStripper(HTMLParser):  # pragma: no cover
    def __init__(self):
        self.reset()
        self.fed = []

    def handle_data(self, d):
        self.fed.append(d)

    def get_data(self):
        return "".join(self.fed)


class MockResponse(object):
    def __init__(self, status_code, text, method="GET", url="http://foo.com/", headers=None):
        self.text = force_text(text)
        self.content = force_bytes(text)
        self.body = force_text(text)
        self.status_code = status_code
        self.headers = headers if headers else {}
        self.url = url
        self.ok = True
        self.cookies = dict()
        self.streaming = False
        self.charset = "utf-8"

        # mock up a request object on our response as well
        self.request = dict_to_struct("MockRequest", dict(method=method, url=url, body="request body"))

    def add_header(self, key, value):
        self.headers[key] = value

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self):
        if self.status_code != 200:
            raise Exception("Got HTTP error: %d" % self.status_code)


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


class CaptureSTDOUT(object):
    """
    Redirects STDOUT output to a StringIO which can be inspected later
    """

    def __init__(self,):
        self.new_stdout = StringIO()

        self.old_stdout = sys.stdout
        sys.stdout = self.new_stdout

    def __enter__(self):
        return self.new_stdout

    def __exit__(self, exc_type, exc_val, exc_tb):
        sys.stdout = self.old_stdout
