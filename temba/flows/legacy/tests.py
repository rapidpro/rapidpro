from unittest.mock import patch
from uuid import uuid4

from packaging.version import Version

from django.urls import reverse

from temba.contacts.models import ContactGroup
from temba.flows.models import ActionSet, Flow, FlowRevision, RuleSet, get_flow_user
from temba.msgs.models import Label
from temba.tests import TembaTest, matchers
from temba.utils import json
from temba.values.constants import Value

from .definition import InGroupTest
from .expressions import _build_function_signature, get_function_listing, migrate_v7_template
from .migrations import (
    map_actions,
    migrate_export_to_version_9,
    migrate_to_version_5,
    migrate_to_version_6,
    migrate_to_version_7,
    migrate_to_version_8,
    migrate_to_version_9,
    migrate_to_version_10_2,
    migrate_to_version_10_4,
    migrate_to_version_11_0,
    migrate_to_version_11_1,
    migrate_to_version_11_2,
    migrate_to_version_11_3,
    migrate_to_version_11_5,
    migrate_to_version_11_6,
    migrate_to_version_11_7,
    migrate_to_version_11_8,
    migrate_to_version_11_9,
    migrate_to_version_11_11,
    migrate_to_version_11_12,
)


def get_legacy_groups(definition):
    groups = {}
    for actionset in definition[Flow.ACTION_SETS]:
        for action in actionset[Flow.ACTIONS]:
            for group in action.get("groups", []):
                groups[group["uuid"]] = group["name"]

    for ruleset in definition[Flow.RULE_SETS]:
        for rule in ruleset.get(Flow.RULES, []):
            if rule["test"]["type"] == InGroupTest.TYPE:
                group = rule["test"]["test"]
                groups[group["uuid"]] = group["name"]
    return groups


def get_labels(definition):
    labels = {}
    for actionset in definition[Flow.ACTION_SETS]:
        for action in actionset[Flow.ACTIONS]:
            for label in action.get("labels", []):
                labels[label["uuid"]] = label["name"]
    return labels


class ExpressionsTest(TembaTest):
    def test_get_function_listing(self):
        listing = get_function_listing()
        self.assertEqual(
            listing[0],
            {"signature": "ABS(number)", "name": "ABS", "display": "Returns the absolute value of a number"},
        )

    def test_build_function_signature(self):
        self.assertEqual("ABS()", _build_function_signature(dict(name="ABS", params=[])))

        self.assertEqual(
            "ABS(number)",
            _build_function_signature(dict(name="ABS", params=[dict(optional=False, name="number", vararg=False)])),
        )

        self.assertEqual(
            "ABS(number, ...)",
            _build_function_signature(dict(name="ABS", params=[dict(optional=False, name="number", vararg=True)])),
        )

        self.assertEqual(
            "ABS([number])",
            _build_function_signature(dict(name="ABS", params=[dict(optional=True, name="number", vararg=False)])),
        )

        self.assertEqual(
            "ABS([number], ...)",
            _build_function_signature(dict(name="ABS", params=[dict(optional=True, name="number", vararg=True)])),
        )

        self.assertEqual(
            "MOD(number, divisor)",
            _build_function_signature(
                dict(
                    name="MOD",
                    params=[
                        dict(optional=False, name="number", vararg=False),
                        dict(optional=False, name="divisor", vararg=False),
                    ],
                )
            ),
        )

        self.assertEqual(
            "MOD(number, ..., divisor)",
            _build_function_signature(
                dict(
                    name="MOD",
                    params=[
                        dict(optional=False, name="number", vararg=True),
                        dict(optional=False, name="divisor", vararg=False),
                    ],
                )
            ),
        )

    def test_migrate_v7_template(self):
        self.assertEqual(
            migrate_v7_template("Hi @contact.name|upper_case|capitalize from @flow.chw|lower_case"),
            "Hi @(PROPER(UPPER(contact.name))) from @(LOWER(flow.chw))",
        )
        self.assertEqual(migrate_v7_template('Hi @date.now|time_delta:"1"'), "Hi @(date.now + 1)")
        self.assertEqual(migrate_v7_template('Hi @date.now|time_delta:"-3"'), "Hi @(date.now - 3)")

        self.assertEqual(migrate_v7_template("Hi =contact.name"), "Hi @contact.name")
        self.assertEqual(migrate_v7_template("Hi =(contact.name)"), "Hi @(contact.name)")
        self.assertEqual(migrate_v7_template("Hi =NOW() =(TODAY())"), "Hi @(NOW()) @(TODAY())")
        self.assertEqual(migrate_v7_template('Hi =LEN("@=")'), 'Hi @(LEN("@="))')

        # handle @ expressions embedded inside = expressions, with optional surrounding quotes
        self.assertEqual(
            migrate_v7_template('=AND("Malkapur"= "@flow.stuff.category", 13 = @extra.Depar_city|upper_case)'),
            '@(AND("Malkapur"= flow.stuff.category, 13 = UPPER(extra.Depar_city)))',
        )

        # don't convert unnecessarily
        self.assertEqual(migrate_v7_template("Hi @contact.name from @flow.chw"), "Hi @contact.name from @flow.chw")

        # don't convert things that aren't expressions
        self.assertEqual(migrate_v7_template("Reply 1=Yes, 2=No"), "Reply 1=Yes, 2=No")


class FlowMigrationTest(TembaTest):
    def migrate_flow(self, flow, to_version=None):

        if not to_version:
            to_version = Flow.FINAL_LEGACY_VERSION

        flow_json = flow.as_json()
        if Version(flow.version_number) < Version("6"):
            revision = flow.revisions.all().order_by("-revision").first()
            flow_json = dict(
                definition=flow_json,
                flow_type=flow.flow_type,
                expires=flow.expires_after_minutes,
                id=flow.pk,
                revision=revision.revision if revision else 1,
            )

        flow_json = FlowRevision.migrate_definition(flow_json, flow, to_version=to_version)
        if "definition" in flow_json:
            flow_json = flow_json["definition"]

        flow.update(flow_json)
        return Flow.objects.get(pk=flow.pk)

    def test_migrate_with_flow_user(self):
        flow = Flow.objects.create(
            name="Favorites",
            org=self.org,
            created_by=self.admin,
            modified_by=self.admin,
            saved_by=self.admin,
            version_number="7",
            flow_type="M",
        )

        flow_json = self.get_flow_json("favorites")
        FlowRevision.objects.create(
            flow=flow, definition=flow_json, spec_version=7, revision=1, created_by=self.admin, modified_by=self.admin
        )

        old_json = flow.as_json()

        saved_on = flow.saved_on
        modified_on = flow.modified_on
        flow.ensure_current_version()
        flow.refresh_from_db()

        # system migration should not affect our saved_on even tho we are modified
        self.assertNotEqual(modified_on, flow.modified_on)
        self.assertEqual(saved_on, flow.saved_on)

        # but should still create a revision using the flow user
        self.assertEqual(1, flow.revisions.filter(created_by=get_flow_user(self.org)).count())

        # should see the system user on our revision json
        self.login(self.admin)
        response = self.client.get(reverse("flows.flow_revisions", args=[flow.uuid]))

        self.assertContains(response, "System Update")
        self.assertEqual(2, len(response.json()["results"]))

        # attempt to save with old json, no bueno
        response = self.client.post(
            reverse("flows.flow_json", args=[flow.uuid]), data=json.dumps(old_json), content_type="application/json"
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json(),
            {
                "description": "rapidpro_flow is currently editing this Flow. Your changes will not be saved until you refresh your browser.",
                "status": "failure",
            },
        )

        # now refresh and save a new version
        flow.update(flow.as_json(), user=self.admin)

        self.assertEqual(flow.revisions.count(), 3)
        self.assertEqual(flow.revisions.filter(created_by=get_flow_user(self.org)).count(), 1)

        # if we request a specific revision by id, flow will be migrated to new format
        revision = flow.revisions.order_by("id").last()
        response = self.client.get("%s%s/" % (reverse("flows.flow_revisions", args=[flow.uuid]), str(revision.id)))

        self.assertEqual(response.json()["definition"]["spec_version"], "13.1.0")

    def test_migrate_malformed_single_message_flow(self):

        flow = Flow.objects.create(
            name="Single Message Flow",
            org=self.org,
            created_by=self.admin,
            modified_by=self.admin,
            saved_by=self.admin,
            version_number="3",
        )

        flow_json = self.get_flow_json("malformed_single_message")["definition"]

        FlowRevision.objects.create(
            flow=flow, definition=flow_json, spec_version=3, revision=1, created_by=self.admin, modified_by=self.admin
        )

        flow.ensure_current_version()
        flow_json = flow.as_json()

        self.assertEqual(len(flow_json["action_sets"]), 1)
        self.assertEqual(len(flow_json["rule_sets"]), 0)
        self.assertEqual(flow_json["version"], Flow.FINAL_LEGACY_VERSION)
        self.assertEqual(flow_json["metadata"]["revision"], 2)

    def test_update_with_ruleset_to_actionset_change(self):
        flow = self.get_flow("favorites", legacy=True)

        # so that we check clearing of prefetched nodes that might be deleted in .update()
        flow = Flow.objects.prefetch_related("action_sets", "rule_sets").get(id=flow.id)

        flow_json = flow.as_json()

        # remove first ruleset
        ruleset1 = flow_json["rule_sets"][0]
        flow_json["rule_sets"] = flow_json["rule_sets"][1:]

        # create actionset in its place with same uuid etc
        flow_json["action_sets"].append(
            {
                "uuid": ruleset1["uuid"],
                "actions": [{"uuid": "da6d7657-8940-4778-ada8-27a2035a8352", "type": "lang", "language": "fra"}],
                "x": ruleset1["x"],
                "y": ruleset1["y"],
                "destination": ruleset1["rules"][0]["destination"],
                "destination_type": ruleset1["rules"][0]["destination_type"],
                "exit_uuid": str(uuid4()),
            }
        )

        flow.update(flow_json)

        self.assertFalse(RuleSet.objects.filter(flow=flow, uuid=ruleset1["uuid"]).exists())
        self.assertTrue(ActionSet.objects.filter(flow=flow, uuid=ruleset1["uuid"]).exists())

    def test_update_with_actionset_to_ruleset_change(self):
        flow = self.get_flow("favorites", legacy=True)

        # so that we check clearing of prefetched nodes that might be deleted in .update()
        flow = Flow.objects.prefetch_related("action_sets", "rule_sets").get(id=flow.id)

        flow_json = flow.as_json()

        # remove first actionset
        actionset1 = flow_json["action_sets"][0]
        flow_json["action_sets"] = flow_json["action_sets"][1:]

        # create ruleset in its place with same uuid etc
        flow_json["rule_sets"].append(
            {
                "uuid": actionset1["uuid"],
                "ruleset_type": "wait_message",
                "rules": [
                    {
                        "uuid": actionset1["exit_uuid"],
                        "test": {"type": "true"},
                        "category": {"eng": "All Responses"},
                        "destination": actionset1["destination"],
                        "destination_type": "R",
                    }
                ],
                "x": actionset1["x"],
                "y": actionset1["y"],
            }
        )

        flow.update(flow_json)

        self.assertTrue(RuleSet.objects.filter(flow=flow, uuid=actionset1["uuid"]).exists())
        self.assertFalse(ActionSet.objects.filter(flow=flow, uuid=actionset1["uuid"]).exists())

    def test_ensure_current_version(self):
        flow_json = self.get_flow_json("favorites_v4")["definition"]
        flow = Flow.objects.create(
            name="Favorites",
            org=self.org,
            created_by=self.admin,
            modified_by=self.admin,
            saved_by=self.admin,
            version_number="4",
            flow_type="M",
        )

        FlowRevision.objects.create(
            flow=flow, definition=flow_json, spec_version=3, revision=1, created_by=self.admin, modified_by=self.admin
        )

        # now make sure we are on the latest version
        flow.ensure_current_version()

        # and that the format looks correct
        flow_json = flow.as_json()

        self.assertEqual(flow_json["metadata"]["name"], "Favorites")
        self.assertEqual(flow_json["metadata"]["revision"], 2)
        self.assertEqual(flow_json["metadata"]["expires"], 720)
        self.assertEqual(flow_json["base_language"], "base")
        self.assertEqual(len(flow_json["action_sets"]), 6)
        self.assertEqual(len(flow_json["rule_sets"]), 6)

    def test_migrate_to_11_12(self):
        flow = self.get_flow("favorites", legacy=True)
        definition = {
            "entry": "79b4776b-a995-475d-ae06-1cab9af8a28e",
            "rule_sets": [],
            "action_sets": [
                {
                    "uuid": "d1244cfb-dc48-4dd5-ac45-7da49fdf46fb",
                    "x": 459,
                    "y": 150,
                    "destination": "ef4865e9-1d34-4876-a0ff-fa3fe5025b3e",
                    "actions": [
                        {
                            "type": "reply",
                            "uuid": "3db54617-cce1-455b-a787-12df13df87bd",
                            "msg": {"base": "Hi there"},
                            "media": {},
                            "quick_replies": [],
                            "send_all": False,
                        }
                    ],
                    "exit_uuid": "959fbe68-ba5a-4c78-b8d1-861e64d1e1e3",
                },
                {
                    "uuid": "79b4776b-a995-475d-ae06-1cab9af8a28e",
                    "x": 476,
                    "y": 0,
                    "destination": "d1244cfb-dc48-4dd5-ac45-7da49fdf46fb",
                    "actions": [
                        {
                            "type": "channel",
                            "uuid": "f133934a-9772-419f-ad52-00fe934dab19",
                            "channel": None,
                            "name": None,
                        }
                    ],
                    "exit_uuid": "aec0318d-45c2-4c39-92fc-81d3d21178f6",
                },
            ],
        }

        migrated = migrate_to_version_11_12(definition, flow)

        # removed the invalid reference
        self.assertEqual(len(migrated["action_sets"]), 1)

        # reconnected the nodes to new destinations and adjust entry
        self.assertEqual(migrated["entry"], migrated["action_sets"][0]["uuid"])
        self.assertEqual(migrated["action_sets"][0]["y"], 0)

        definition = {
            "entry": "79b4776b-a995-475d-ae06-1cab9af8a28e",
            "rule_sets": [],
            "action_sets": [
                {
                    "uuid": "d1244cfb-dc48-4dd5-ac45-7da49fdf46fb",
                    "x": 459,
                    "y": 150,
                    "destination": "ef4865e9-1d34-4876-a0ff-fa3fe5025b3e",
                    "actions": [
                        {
                            "type": "reply",
                            "uuid": "3db54617-cce1-455b-a787-12df13df87bd",
                            "msg": {"base": "Hi there"},
                            "media": {},
                            "quick_replies": [],
                            "send_all": False,
                        }
                    ],
                    "exit_uuid": "959fbe68-ba5a-4c78-b8d1-861e64d1e1e3",
                },
                {
                    "uuid": "79b4776b-a995-475d-ae06-1cab9af8a28e",
                    "x": 476,
                    "y": 0,
                    "destination": "d1244cfb-dc48-4dd5-ac45-7da49fdf46fb",
                    "actions": [
                        {
                            "type": "channel",
                            "uuid": "f133934a-9772-419f-ad52-00fe934dab19",
                            "channel": self.channel.uuid,
                            "name": self.channel.name,
                        }
                    ],
                    "exit_uuid": "aec0318d-45c2-4c39-92fc-81d3d21178f6",
                },
            ],
        }

        migrated = migrate_to_version_11_12(definition, flow)

        # removed the invalid reference
        self.assertEqual(len(migrated["action_sets"]), 2)

        flow = self.get_flow("migrate_to_11_12", legacy=True)
        flow_json = self.get_flow_json("migrate_to_11_12")
        migrate_to_version_11_12(flow_json, flow)

        actionset = flow.action_sets.filter(y=0).first()
        self.assertEqual(actionset.actions[0]["msg"]["base"], "Hey there, Yes or No?")

        action_sets = flow.action_sets.all()
        self.assertEqual(len(action_sets), 3)

    def test_migrate_to_11_12_with_valid_channels(self):
        self.channel.name = "1234"
        self.channel.save()

        self.org = self.channel.org
        flow = self.get_flow("migrate_to_11_12", legacy=True)
        flow_json = self.get_flow_json("migrate_to_11_12")
        migrate_to_version_11_12(flow_json, flow)

        action_sets = flow.action_sets.all()
        self.assertEqual(len(action_sets), 7)

    def test_migrate_to_11_12_with_one_node(self):

        flow = self.get_flow("migrate_to_11_12_one_node")
        flow_json = self.get_flow_json("migrate_to_11_12_one_node")
        migrate_to_version_11_12(flow_json, flow)

        action_sets = flow.action_sets.all()
        self.assertEqual(len(action_sets), 0)

    def test_migrate_to_11_12_other_org_new_flow(self):
        # change ownership of the channel it's referencing
        self.setUpSecondaryOrg()
        self.channel.org = self.org2
        self.channel.save(update_fields=("org",))

        flow = self.get_flow("migrate_to_11_12_other_org", {"CHANNEL-UUID": str(self.channel.uuid)}, legacy=True)

        # check action was removed
        definition = flow.revisions.order_by("revision").last().definition
        self.assertEqual(len(definition["action_sets"]), 1)
        self.assertEqual(len(definition["action_sets"][0]["actions"]), 0)

    def test_migrate_to_11_12_other_org_existing_flow(self):
        # import a flow but don't yet migrate it to 11.12
        with patch("temba.flows.models.Flow.FINAL_LEGACY_VERSION", "11.11"):
            flow = self.get_flow("migrate_to_11_12_other_org", {"CHANNEL-UUID": str(self.channel.uuid)}, legacy=True)

        self.assertEqual(flow.version_number, "11.11")
        self.assertEqual(flow.revisions.order_by("revision").last().spec_version, "11.11")

        # change ownership of the channel it's referencing
        self.setUpSecondaryOrg()
        self.channel.org = self.org2
        self.channel.save(update_fields=("org",))

        flow.ensure_current_version()

        # check action set was removed
        definition = flow.revisions.order_by("revision").last().definition
        self.assertEqual(len(definition["action_sets"]), 0)

    def test_migrate_to_11_11(self):

        flow = self.get_flow("migrate_to_11_11")
        flow_json = self.get_flow_json("migrate_to_11_11")

        migrated = migrate_to_version_11_11(flow_json, flow)
        migrated_labels = get_labels(migrated)
        for uuid, name in migrated_labels.items():
            self.assertTrue(Label.label_objects.filter(uuid=uuid, name=name).exists(), msg="Label UUID mismatch")

    def test_migrate_to_11_10(self):
        self.get_flow("migrate_to_11_10", legacy=True)

        parent = Flow.objects.get(name__contains="Parent")
        parent_json = parent.as_json()
        ivr_child = Flow.objects.get(name__contains="IVR")

        # the subflow ruleset to a messaging flow remains as the only ruleset
        self.assertEqual(len(parent_json["rule_sets"]), 1)
        self.assertEqual(parent_json["rule_sets"][0]["config"]["flow"]["name"], "Migrate to 11.10 SMS Child")

        # whereas the subflow ruleset to an IVR flow has become a new trigger flow action
        self.assertEqual(len(parent_json["action_sets"]), 4)

        new_actionset = parent_json["action_sets"][3]
        self.assertEqual(
            new_actionset,
            {
                "uuid": matchers.UUID4String(),
                "x": 218,
                "y": 228,
                "destination": parent_json["action_sets"][1]["uuid"],
                "actions": [
                    {
                        "uuid": matchers.UUID4String(),
                        "type": "trigger-flow",
                        "flow": {"uuid": str(ivr_child.uuid), "name": "Migrate to 11.10 IVR Child"},
                        "variables": [{"id": "@contact.uuid"}],
                        "contacts": [],
                        "groups": [],
                    }
                ],
                "exit_uuid": matchers.UUID4String(),
            },
        )

        # as did the start flow action
        new_trigger2 = parent_json["action_sets"][1]["actions"][0]
        self.assertEqual(
            new_trigger2,
            {
                "uuid": matchers.UUID4String(),
                "type": "trigger-flow",
                "flow": {"uuid": str(ivr_child.uuid), "name": "Migrate to 11.10 IVR Child"},
                "variables": [{"id": "@contact.uuid"}],
                "contacts": [],
                "groups": [],
            },
        )

    def test_migrate_to_11_9(self):
        self.get_flow("migrate_to_11_9", legacy=True)

        invalid1 = Flow.objects.get(name="Invalid1")
        invalid1.is_archived = True
        invalid1.save()

        invalid2 = Flow.objects.get(name="Invalid2")
        invalid2.is_active = False
        invalid2.save()

        flow = Flow.objects.get(name="Master")
        flow_json = flow.as_json()

        self.assertEqual(len(flow_json["rule_sets"]), 4)
        self.assertEqual(sum(len(action_set["actions"]) for action_set in flow_json["action_sets"]), 8)

        migrated = migrate_to_version_11_9(flow_json, flow)

        # expected to remove 1 ruleset and 3 actions referencing invalid flows
        self.assertEqual(len(migrated["rule_sets"]), 3)
        self.assertEqual(sum(len(action_set["actions"]) for action_set in migrated["action_sets"]), 5)

    def test_migrate_to_11_8(self):
        def get_rule_uuids(f):
            uuids = []
            for rs in f.get(Flow.RULE_SETS, []):
                for rule in rs.get("rules"):
                    uuids.append(rule["uuid"])
            return uuids

        original = self.get_flow_json("migrate_to_11_8")
        original_uuids = get_rule_uuids(original)

        self.assertEqual(len(original_uuids), 9)
        self.assertEqual(len(set(original_uuids)), 7)

        migrated = migrate_to_version_11_8(original)
        migrated_uuids = get_rule_uuids(migrated)

        # check that all rule UUIDs are now unique and only two new ones were added
        self.assertEqual(len(set(migrated_uuids)), 9)
        self.assertEqual(len(set(migrated_uuids).difference(original_uuids)), 2)

    def test_migrate_to_11_7(self):
        original = self.get_flow_json("migrate_to_11_7")

        self.assertEqual(len(original["action_sets"]), 5)
        self.assertEqual(len(original["rule_sets"]), 1)

        migrated = migrate_to_version_11_7(original)

        self.assertEqual(len(migrated["action_sets"]), 3)
        self.assertEqual(len(migrated["rule_sets"]), 6)

    def test_migrate_to_11_6(self):

        flow = self.get_flow("migrate_to_11_6")
        flow_json = self.get_flow_json("migrate_to_11_6")

        migrated = migrate_to_version_11_6(flow_json, flow)
        migrated_groups = get_legacy_groups(migrated)
        for uuid, name in migrated_groups.items():
            self.assertTrue(ContactGroup.user_groups.filter(uuid=uuid, name=name).exists(), msg="Group UUID mismatch")

    def test_migrate_to_11_5(self):
        flow_json = self.get_flow_json("migrate_to_11_5")
        flow_json = migrate_to_version_11_5(flow_json)

        # check text was updated in the reply action
        expected_msg = "\n".join(
            [
                "@extra.response_1",
                "@extra.response_1",
                "@flow.response_1.category",  # unchanged because its category
                "@(upper(extra.response_1))",
                "@(upper(flow.response_1.category))",
                "",
                "@flow.response_2",  # unchanged because this slug is also used by a non-webhook ruleset
                "@flow.response_2.value",
                "@flow.response_2.category",
                "@(upper(flow.response_2))",
                "@(upper(flow.response_2.category))",
                "",
                "@extra.response_3",
                "@extra.response_3",
                "@flow.response_3.category",
                "@(upper(extra.response_3))",
                "@(upper(flow.response_3.category))",
            ]
        )
        self.assertEqual(flow_json["action_sets"][0]["actions"][0]["msg"]["eng"], expected_msg)

        # check operand was updated in the split by expression
        self.assertEqual(
            flow_json["rule_sets"][4]["operand"], "@(extra.response_1 & flow.response_2 & extra.response_3)"
        )

        # check operand and type were updated in the split by flow field
        rs = flow_json["rule_sets"][5]
        self.assertEqual(rs["operand"], "@extra.response_1")
        self.assertEqual(rs["ruleset_type"], "expression")

        # check rule test was updated
        self.assertEqual(flow_json["rule_sets"][5]["rules"][1]["test"]["test"]["eng"], "@extra.response_1")

        # check webhook URL on ruleset was updated
        self.assertEqual(flow_json["rule_sets"][6]["config"]["webhook"], "http://example.com/?thing=@extra.response_1")

        # check webhook field on webhook action was updsated
        self.assertEqual(
            flow_json["action_sets"][1]["actions"][0]["webhook"], "http://example.com/?thing=@extra.response_1&foo=bar"
        )

        # check value field on save action was updsated
        self.assertEqual(flow_json["action_sets"][1]["actions"][1]["value"], "@extra.response_3")

    def test_migrate_to_11_4(self):
        flow = self.get_flow("migrate_to_11_4", legacy=True)
        flow_json = flow.as_json()

        # gather up replies to check expressions were migrated
        replies = []
        for action_set in flow_json["action_sets"]:
            for action in action_set["actions"]:
                if "msg" in action:
                    if isinstance(action["msg"], str):
                        replies.append(action["msg"])
                    else:
                        for text in sorted(action["msg"].values()):
                            replies.append(text)

        self.assertEqual(
            replies,
            ['@flow.response_1.text\n@step.value\n@step.value\n@flow.response_3\n@(CONCATENATE(step.value, "blerg"))']
            * 3,
        )

    def test_migrate_to_11_3(self):
        flow_json = self.get_flow_json("migrate_to_11_3")

        migrated = migrate_to_version_11_3(flow_json)

        self.assertTrue(migrated["action_sets"][0]["actions"][0]["legacy_format"])
        self.assertTrue(migrated["rule_sets"][0]["config"]["legacy_format"])

    def test_migrate_to_11_2(self):
        fre_definition = {
            "base_language": "fre",
            "action_sets": [
                {
                    "uuid": "9468bbce-0df6-4d86-ae14-f26525ddda1d",
                    "destination": "cc904a60-9de1-4f0b-9b55-a42b4ea6c434",
                    "actions": [
                        {
                            "msg": {
                                "base": "What is your favorite color?",
                                "eng": "What is your favorite color?",
                                "fra": "Quelle est votre couleur préférée?",
                            },
                            "type": "reply",
                            "uuid": "335eb13d-5167-48ba-90c6-eb116656247c",
                        }
                    ],
                    "exit_uuid": "a9904153-c831-4b95-aa20-13f84fed0841",
                    "y": 0,
                    "x": 100,
                }
            ],
        }

        base_definition = {
            "base_language": "base",
            "action_sets": [
                {
                    "uuid": "9468bbce-0df6-4d86-ae14-f26525ddda1d",
                    "destination": "cc904a60-9de1-4f0b-9b55-a42b4ea6c434",
                    "actions": [
                        {
                            "msg": {
                                "base": "What is your favorite color?",
                                "eng": "What is your favorite color?",
                                "fra": "Quelle est votre couleur préférée?",
                            },
                            "type": "reply",
                            "uuid": "335eb13d-5167-48ba-90c6-eb116656247c",
                        }
                    ],
                    "exit_uuid": "a9904153-c831-4b95-aa20-13f84fed0841",
                    "y": 0,
                    "x": 100,
                }
            ],
        }

        flow1 = Flow.objects.create(
            name="base lang test",
            org=self.org,
            created_by=self.admin,
            modified_by=self.admin,
            saved_by=self.admin,
            version_number=1,
        )
        flow2 = Flow.objects.create(
            name="Base lang test",
            org=self.org,
            created_by=self.admin,
            modified_by=self.admin,
            saved_by=self.admin,
            version_number=1,
        )
        FlowRevision.objects.create(
            flow=flow1,
            definition=fre_definition,
            spec_version=1,
            revision=1,
            created_by=self.admin,
            modified_by=self.admin,
        )
        FlowRevision.objects.create(
            flow=flow2,
            definition=fre_definition,
            spec_version=1,
            revision=1,
            created_by=self.admin,
            modified_by=self.admin,
        )

        new_definition = migrate_to_version_11_2(fre_definition, flow=flow1)

        fre_lang_value = new_definition["base_language"]
        self.assertEqual(fre_lang_value, "fra")

        new_definition = migrate_to_version_11_2(base_definition, flow=flow2)

        base_lang_value = new_definition["base_language"]
        self.assertEqual(base_lang_value, "base")

    def test_migrate_to_11_1(self):
        definition = {
            "base_language": "base",
            "action_sets": [
                {
                    "uuid": "9468bbce-0df6-4d86-ae14-f26525ddda1d",
                    "destination": "cc904a60-9de1-4f0b-9b55-a42b4ea6c434",
                    "actions": [
                        {
                            "msg": {
                                "base": "What is your favorite color?",
                                "eng": "What is your favorite color?",
                                "fre": "Quelle est votre couleur préférée?",
                            },
                            "type": "reply",
                            "uuid": "335eb13d-5167-48ba-90c6-eb116656247c",
                        }
                    ],
                    "exit_uuid": "a9904153-c831-4b95-aa20-13f84fed0841",
                    "y": 0,
                    "x": 100,
                },
                {
                    "y": 1214,
                    "x": 284,
                    "destination": "498b1953-02f1-47dd-b9cb-1b51913e348f",
                    "uuid": "9769918c-8ca4-4ec5-8b5b-bf94cc6746a9",
                    "actions": [
                        {
                            "lang": "fre",
                            "type": "lang",
                            "name": "French",
                            "uuid": "56a4bca5-b9e5-4d04-883c-ca65d7c4d538",
                        }
                    ],
                },
                {
                    "uuid": "9468bbce-0df6-4d86-ae14-f26525ddda1d",
                    "destination": "cc904a60-9de1-4f0b-9b55-a42b4ea6c434",
                    "actions": [
                        {
                            "msg": {
                                "base": "What is your favorite color?",
                                "eng": "What is your favorite color?",
                                "fre": "Quelle est votre couleur préférée?",
                                "newl": "Bogus translation",
                            },
                            "type": "reply",
                            "uuid": "335eb13d-5167-48ba-90c6-eb116656247c",
                        }
                    ],
                    "exit_uuid": "a9904153-c831-4b95-aa20-13f84fed0841",
                    "y": 0,
                    "x": 100,
                },
            ],
        }

        flow = Flow.objects.create(
            name="String group",
            org=self.org,
            created_by=self.admin,
            modified_by=self.admin,
            saved_by=self.admin,
            version_number=1,
        )

        FlowRevision.objects.create(
            flow=flow, definition=definition, spec_version=1, revision=1, created_by=self.admin, modified_by=self.admin
        )

        new_definition = migrate_to_version_11_1(definition, flow=flow)

        lang_path = new_definition["action_sets"][0]["actions"][0]["msg"]

        self.assertIn("fra", lang_path)
        self.assertEqual(len(lang_path), 3)

        lang_key_value = new_definition["action_sets"][1]["actions"][0]["lang"]

        self.assertEqual(lang_key_value, "fra")

        should_not_be_migrated_path = new_definition["action_sets"][2]["actions"][0]["msg"]
        self.assertIn("fre", should_not_be_migrated_path)

        # we cannot migrate flows to version 11 without flow object (languages depend on flow.org)
        self.assertRaises(ValueError, migrate_to_version_11_1, definition)

    def test_migrate_to_11_0(self):
        self.create_field("nickname", "Nickname", Value.TYPE_TEXT)
        self.create_field("district", "District", Value.TYPE_DISTRICT)
        self.create_field("joined_on", "Joined On", Value.TYPE_DATETIME)

        flow = self.get_flow("type_flow", legacy=True)
        flow_json = flow.as_json()

        # gather up replies to check expressions were migrated
        replies = []
        for action_set in flow_json["action_sets"]:
            for action in action_set["actions"]:
                if action["type"] == "reply":
                    for text in sorted(action["msg"].values()):
                        replies.append(text)

        self.assertEqual(
            replies,
            [
                "Hey @contact.nickname, you joined on @(format_date(contact.joined_on)) in @(format_location(contact.district)).",
                "It's @(format_date(date)). The time is @(format_date(date.now)) on @date.today.",
                "Send text",
                "You said @flow.text at @(format_date(flow.text.time)). Send date",
                "You said @(format_date(flow.date)) which was in category @flow.date.category Send number",
                "You said @flow.number. Send state",
                "You said @(format_location(flow.state)) which was in category @flow.state.category. Send district",
                "You said @(format_location(flow.district)). Send ward",
                "Tu as dit @(format_location(flow.ward))",  # flow var followed by end of input
                "You said @(format_location(flow.ward)).",  # flow var followed by period then end of input
            ],
        )

    def test_migrate_to_11_0_with_null_ruleset_label(self):
        flow = self.get_flow("migrate_to_11_0")
        definition = {
            "rule_sets": [
                {
                    "uuid": "9ed4a233-c737-4f46-9b0a-de6e88134e14",
                    "rules": [],
                    "ruleset_type": "wait_message",
                    "label": None,
                    "operand": None,
                    "finished_key": None,
                    "y": 180,
                    "x": 179,
                }
            ]
        }

        migrated = migrate_to_version_11_0(definition, flow)

        self.assertEqual(migrated, definition)

    def test_migrate_to_11_0_with_null_msg_text(self):
        flow = self.get_flow("migrate_to_11_0")
        definition = {
            "action_sets": [
                {
                    "y": 0,
                    "x": 100,
                    "destination": "0ecf7914-05e0-4b71-8816-495d2c0921b5",
                    "uuid": "a6676605-332a-4309-a8b8-79b33e73adcd",
                    "actions": [{"type": "reply", "msg": {"base": None}}],
                }
            ]
        }

        migrated = migrate_to_version_11_0(definition, flow)
        self.assertEqual(migrated, definition)

    def test_migrate_to_11_0_with_broken_localization(self):
        migrated = self.get_flow("migrate_to_11_0", legacy=True).as_json()

        self.assertEqual(
            migrated["action_sets"][0]["actions"][0]["msg"],
            {"base": "@(format_date(date)) Something went wrong once. I shouldn't be a dict inside a dict."},
        )

    def test_migrate_to_10_4(self):
        definition = {
            "action_sets": [
                {
                    "y": 0,
                    "x": 100,
                    "destination": "0ecf7914-05e0-4b71-8816-495d2c0921b5",
                    "uuid": "a6676605-332a-4309-a8b8-79b33e73adcd",
                    "actions": [{"type": "reply", "msg": {"base": "What is your favorite color?"}}],
                }
            ]
        }

        definition = migrate_to_version_10_4(definition)

        # make sure all of our action sets have an exit uuid and all of our actions have uuids set
        for actionset in definition["action_sets"]:
            self.assertIsNotNone(actionset["exit_uuid"])
            for action in actionset["actions"]:
                self.assertIsNotNone(action["uuid"])

    def test_migrate_to_10_3(self):
        favorites = self.get_flow("favorites")

        # make sure all of our action sets have an exit uuid
        for actionset in favorites.action_sets.all():
            self.assertIsNotNone(actionset.exit_uuid)

    def test_migrate_to_10_2(self):
        flow_json = self.get_flow_json("single_message_bad_localization")
        flow_json = migrate_to_version_10_2(flow_json)
        self.assertEqual("Campaign Message 12", flow_json["action_sets"][0]["actions"][0]["msg"]["eng"])

    def test_migrate_to_10_1(self):
        favorites = self.get_flow("favorites", legacy=True)

        # make sure all of our actions have uuids set
        for actionset in favorites.action_sets.all():
            for action in actionset.get_actions():
                self.assertIsNotNone(action.uuid)

        # since actions can generate their own uuids, lets make sure fetching from the databse yields the same uuids
        exported = favorites.as_json()
        flow = Flow.objects.filter(name="Favorites").first()
        self.assertEqual(exported, flow.as_json())
        self.assertEqual(flow.version_number, Flow.FINAL_LEGACY_VERSION)

    def test_migrate_to_10(self):
        # this is really just testing our rewriting of webhook rulesets
        webhook_flow = self.get_flow("dual_webhook", legacy=True)
        self.assertNotEqual(webhook_flow.modified_on, webhook_flow.saved_on)

        # get our definition out
        flow_def = webhook_flow.as_json()

        # make sure our rulesets no longer have 'webhook' or 'webhook_action'
        for ruleset in flow_def["rule_sets"]:
            self.assertNotIn("webhook", ruleset)
            self.assertNotIn("webhook_action", ruleset)

    def test_migrate_to_9(self):
        contact = self.create_contact("Ben Haggerty", number="+12065552020")

        # our group and flow to move to uuids
        group = self.create_group("Phans", [])
        previous_flow = self.create_flow()
        start_flow = self.create_flow()
        label = Label.get_or_create(self.org, self.admin, "My label")

        substitutions = dict(
            group_id=group.pk,
            contact_id=contact.pk,
            start_flow_id=start_flow.pk,
            previous_flow_id=previous_flow.pk,
            label_id=label.pk,
        )

        exported_json = self.get_import_json("migrate_to_9", substitutions)
        exported_json = migrate_export_to_version_9(exported_json, self.org, True)

        # our campaign events shouldn't have ids
        campaign = exported_json["campaigns"][0]
        event = campaign["events"][0]

        # campaigns should have uuids
        self.assertIn("uuid", campaign)
        self.assertNotIn("id", campaign)

        # our event flow should be a uuid
        self.assertIn("flow", event)
        self.assertIn("uuid", event["flow"])
        self.assertNotIn("id", event["flow"])

        # our relative field should not have an id
        self.assertNotIn("id", event["relative_to"])

        # evaluate that the flow json is migrated properly
        flow_json = exported_json["flows"][0]

        # check that contacts migrated properly
        send_action = flow_json["action_sets"][0]["actions"][1]
        self.assertEqual(1, len(send_action["contacts"]))
        self.assertEqual(0, len(send_action["groups"]))

        for contact in send_action["contacts"]:
            self.assertIn("uuid", contact)
            self.assertNotIn("id", contact)

        for group in send_action["groups"]:
            if isinstance(group, dict):
                self.assertIn("uuid", group)
                self.assertNotIn("id", group)

        label_action = flow_json["action_sets"][0]["actions"][2]
        for label in label_action.get("labels"):
            self.assertNotIn("id", label)
            self.assertIn("uuid", label)

        action_set = flow_json["action_sets"][1]
        actions = action_set["actions"]

        for action in actions[0:2]:
            self.assertIn(action["type"], ("del_group", "add_group"))
            self.assertIn("uuid", action["groups"][0])
            self.assertNotIn("id", action["groups"][0])

        for action in actions[2:4]:
            self.assertIn(action["type"], ("trigger-flow", "flow"))
            self.assertIn("flow", action)
            self.assertIn("uuid", action["flow"])
            self.assertIn("name", action["flow"])
            self.assertNotIn("id", action)
            self.assertNotIn("name", action)

        # we also switch flow ids to uuids in the metadata
        self.assertIn("uuid", flow_json["metadata"])
        self.assertNotIn("id", flow_json["metadata"])

        # import the same thing again, should have the same uuids
        new_exported_json = self.get_import_json("migrate_to_9", substitutions)
        new_exported_json = migrate_export_to_version_9(new_exported_json, self.org, True)
        self.assertEqual(flow_json["metadata"]["uuid"], new_exported_json["flows"][0]["metadata"]["uuid"])

        # but when done as a different site, it should be unique
        new_exported_json = self.get_import_json("migrate_to_9", substitutions)
        new_exported_json = migrate_export_to_version_9(new_exported_json, self.org, False)
        self.assertNotEqual(flow_json["metadata"]["uuid"], new_exported_json["flows"][0]["metadata"]["uuid"])

        # check we can update a flow with the migrated definition
        flow = Flow.objects.create(
            name="test flow", created_by=self.admin, modified_by=self.admin, org=self.org, saved_by=self.admin
        )
        flow.update(
            FlowRevision.migrate_definition(exported_json["flows"][0], flow, to_version=Flow.FINAL_LEGACY_VERSION)
        )

        # can also just import a single flow
        exported_json = self.get_import_json("migrate_to_9", substitutions)
        flow_json = migrate_to_version_9(exported_json["flows"][0], flow)
        self.assertIn("uuid", flow_json["metadata"])
        self.assertNotIn("id", flow_json["metadata"])

        # try it with missing metadata
        flow_json = self.get_import_json("migrate_to_9", substitutions)["flows"][0]
        del flow_json["metadata"]
        flow_json = migrate_to_version_9(flow_json, flow)
        self.assertEqual(1, flow_json["metadata"]["revision"])
        self.assertEqual("test flow", flow_json["metadata"]["name"])
        self.assertEqual(720, flow_json["metadata"]["expires"])
        self.assertIn("uuid", flow_json["metadata"])
        self.assertIn("saved_on", flow_json["metadata"])

        # check that our replacements work
        self.assertEqual("@(CONCAT(parent.divided, parent.sky))", flow_json["action_sets"][0]["actions"][3]["value"])
        self.assertEqual("@parent.contact.name", flow_json["action_sets"][0]["actions"][4]["value"])

    def test_migrate_to_8(self):
        # file uses old style expressions
        flow_json = self.get_flow_json("old_expressions")

        # migrate to the version right before us first
        flow_json = migrate_to_version_7(flow_json)
        flow_json = migrate_to_version_8(flow_json)

        self.assertEqual(
            flow_json["action_sets"][0]["actions"][0]["msg"]["eng"], "Hi @(UPPER(contact.name)). Today is @(date.now)"
        )
        self.assertEqual(flow_json["action_sets"][1]["actions"][0]["groups"][0], "@flow.response_1.category")
        self.assertEqual(
            flow_json["action_sets"][1]["actions"][1]["msg"]["eng"], "Was @(PROPER(LOWER(contact.name)))."
        )
        self.assertEqual(flow_json["action_sets"][1]["actions"][1]["variables"][0]["id"], "@flow.response_1.category")
        self.assertEqual(
            flow_json["rule_sets"][0]["webhook"], "http://example.com/query.php?contact=@(UPPER(contact.name))"
        )
        self.assertEqual(flow_json["rule_sets"][0]["operand"], "@(step.value)")
        self.assertEqual(flow_json["rule_sets"][1]["operand"], "@(step.value + 3)")

    def test_migrate_to_7(self):
        flow_json = self.get_flow_json("ivr_v3")

        # migrate to the version right before us first
        flow_json = migrate_to_version_5(flow_json)
        flow_json = migrate_to_version_6(flow_json)

        self.assertIsNotNone(flow_json.get("definition"))
        self.assertEqual("Call me maybe", flow_json.get("name"))
        self.assertEqual(100, flow_json.get("id"))
        self.assertEqual("V", flow_json.get("flow_type"))

        flow_json = migrate_to_version_7(flow_json)
        self.assertIsNone(flow_json.get("definition", None))
        self.assertIsNotNone(flow_json.get("metadata", None))

        metadata = flow_json.get("metadata")
        self.assertEqual("Call me maybe", metadata["name"])
        self.assertEqual(100, metadata["id"])
        self.assertEqual("V", flow_json.get("flow_type"))

    def test_migrate_to_6(self):

        # file format is old non-localized format
        voice_json = self.get_flow_json("ivr_v3")
        definition = voice_json.get("definition")

        # no language set
        self.assertIsNone(definition.get("base_language", None))
        self.assertEqual("Yes", definition["rule_sets"][0]["rules"][0]["category"])
        self.assertEqual("Press one, two, or three. Thanks.", definition["action_sets"][0]["actions"][0]["msg"])

        # add a recording to make sure that gets migrated properly too
        definition["action_sets"][0]["actions"][0]["recording"] = "/recording.mp3"

        voice_json = migrate_to_version_5(voice_json)
        voice_json = migrate_to_version_6(voice_json)
        definition = voice_json.get("definition")

        # now we should have a language
        self.assertEqual("base", definition.get("base_language", None))
        self.assertEqual("Yes", definition["rule_sets"][0]["rules"][0]["category"]["base"])
        self.assertEqual(
            "Press one, two, or three. Thanks.", definition["action_sets"][0]["actions"][0]["msg"]["base"]
        )
        self.assertEqual("/recording.mp3", definition["action_sets"][0]["actions"][0]["recording"]["base"])

        # now try one that doesn't have a recording set
        voice_json = self.get_flow_json("ivr_v3")
        definition = voice_json.get("definition")
        del definition["action_sets"][0]["actions"][0]["recording"]
        voice_json = migrate_to_version_5(voice_json)
        voice_json = migrate_to_version_6(voice_json)
        definition = voice_json.get("definition")
        self.assertNotIn("recording", definition["action_sets"][0]["actions"][0])

    def test_migrate_to_5_language(self):

        flow_json = self.get_flow_json("multi_language_flow")
        ruleset = flow_json["definition"]["rule_sets"][0]
        ruleset["operand"] = "@step.value|lower_case"

        # now migrate us forward
        flow_json = migrate_to_version_5(flow_json)

        wait_ruleset = None
        rules = None
        for ruleset in flow_json.get("definition").get("rule_sets"):
            if ruleset["ruleset_type"] == "wait_message":
                rules = ruleset["rules"]
                wait_ruleset = ruleset
                break

        self.assertIsNotNone(wait_ruleset)
        self.assertIsNotNone(rules)

        self.assertEqual(1, len(rules))
        self.assertEqual("All Responses", rules[0]["category"]["eng"])
        self.assertEqual("Otro", rules[0]["category"]["spa"])

    def test_migrate_to_5(self):
        flow = self.get_flow("favorites_v4", legacy=True)

        # first node should be a wait node
        ruleset = RuleSet.objects.filter(label="Color Response").first()
        self.assertEqual("wait_message", ruleset.ruleset_type)
        self.assertEqual("@step.value", ruleset.operand)

        # we should now be pointing to a newly created webhook rule
        webhook = RuleSet.objects.get(flow=flow, uuid=ruleset.get_rules()[0].destination)
        self.assertEqual("webhook", webhook.ruleset_type)
        self.assertEqual("http://localhost:49999/status", webhook.config[RuleSet.CONFIG_WEBHOOK])
        self.assertEqual("POST", webhook.config[RuleSet.CONFIG_WEBHOOK_ACTION])
        self.assertEqual("@step.value", webhook.operand)
        self.assertEqual("Color Webhook", webhook.label)

        # which should in turn point to a new expression split on @extra.value
        expression = RuleSet.objects.get(flow=flow, uuid=webhook.get_rules()[0].destination)
        self.assertEqual("expression", expression.ruleset_type)
        self.assertEqual("@extra.value", expression.operand)

        # takes us to the next question
        beer_question = ActionSet.objects.get(flow=flow, uuid=expression.get_rules()[0].destination)

        # which should pause for the response
        wait_beer = RuleSet.objects.get(flow=flow, uuid=beer_question.destination)
        self.assertEqual("wait_message", wait_beer.ruleset_type)
        self.assertEqual("@step.value", wait_beer.operand)
        self.assertEqual(1, len(wait_beer.get_rules()))
        self.assertEqual("All Responses", wait_beer.get_rules()[0].category[flow.base_language])

        # and then split on the expression for various beer choices
        beer_expression = RuleSet.objects.get(flow=flow, uuid=wait_beer.get_rules()[0].destination)
        self.assertEqual("expression", beer_expression.ruleset_type)
        self.assertEqual("@(LOWER(step.value))", beer_expression.operand)
        self.assertEqual(5, len(beer_expression.get_rules()))

        # set our expression to operate on the last inbound message
        expression.operand = "@step.value"
        expression.save()

    def test_migrate_revisions(self):
        flow = self.get_flow("favorites_v4", legacy=True)
        rev = flow.revisions.all().first()
        json_flow = rev.get_definition_json(Flow.FINAL_LEGACY_VERSION)

        # remove our flow version from the flow
        del json_flow[Flow.VERSION]
        rev.definition = json_flow
        rev.spec_version = "10"
        rev.save()

        new_rev = flow.update(rev.get_definition_json(Flow.FINAL_LEGACY_VERSION))
        self.assertEqual(new_rev.spec_version, Flow.FINAL_LEGACY_VERSION)

        flow.refresh_from_db()
        self.assertEqual(flow.revisions.all().count(), 2)
        self.assertEqual(flow.version_number, Flow.FINAL_LEGACY_VERSION)

    def test_migrate_sample_flows(self):
        self.org.create_sample_flows("https://app.rapidpro.io")
        self.assertEqual(3, self.org.flows.filter(name__icontains="Sample Flow").count())

        # make sure it is localized
        poll = self.org.flows.filter(name="Sample Flow - Simple Poll").first()
        self.assertEqual("base", poll.base_language)

        # check substitutions
        order_checker = self.org.flows.filter(name="Sample Flow - Order Status Checker").first()
        webhook_node = order_checker.as_json()["nodes"][3]
        webhook_action = webhook_node["actions"][0]

        self.assertEqual("https://app.rapidpro.io/demo/status/", webhook_action["url"])

        # our test user doesn't use an email address, check for Administrator for the email
        email_node = order_checker.as_json()["nodes"][10]
        email_action = email_node["actions"][1]

        self.assertEqual(["Administrator"], email_action["addresses"])

    def test_migrate_bad_group_names(self):
        # This test makes sure that bad contact groups (< 25, etc) are migrated forward properly.
        # However, since it was a missed migration, now we need to apply it for any current version
        # at the time of this fix
        for v in ("4", "5", "6", "7", "8", "9", "10"):
            error = 'Failure migrating group names "%s" forward from v%s'
            flow = self.get_flow("favorites_bad_group_name_v%s" % v, legacy=True)
            self.assertIsNotNone(flow, "Failure importing favorites from v%s" % v)
            self.assertTrue(ContactGroup.user_groups.filter(name="Contacts < 25").exists(), error % ("< 25", v))
            self.assertTrue(ContactGroup.user_groups.filter(name="Contacts > 100").exists(), error % ("> 100", v))

            ContactGroup.user_groups.all().delete()
            self.assertEqual(Flow.FINAL_LEGACY_VERSION, flow.version_number)
            flow.release()

    def test_migrate_malformed_groups(self):
        flow = self.get_flow("malformed_groups")
        self.assertIsNotNone(flow)
        self.assertTrue(ContactGroup.user_groups.filter(name="Contacts < 25").exists())
        self.assertTrue(ContactGroup.user_groups.filter(name="Unknown").exists())


class MigrationUtilsTest(TembaTest):
    def test_map_actions(self):
        # minimalist flow def with just actions and entry
        flow_def = dict(
            entry="1234",
            action_sets=[dict(uuid="1234", x=100, y=0, actions=[dict(type="reply", msg=None)])],
            rule_sets=[dict(y=10, x=100, uuid="5678")],
        )
        removed = map_actions(flow_def, lambda x: None)

        # no more action sets and entry is remapped
        self.assertFalse(removed["action_sets"])
        self.assertEqual("5678", removed["entry"])

        # add two action sets, we should remap entry to be the first
        flow_def["action_sets"] = [
            dict(uuid="1234", y=0, x=100, actions=[dict(type="reply", msg=None)]),
            dict(uuid="2345", y=5, x=100, actions=[dict(type="reply", msg="foo")]),
        ]
        removed = map_actions(flow_def, lambda x: None if x["msg"] is None else x)

        self.assertEqual(len(removed["action_sets"]), 1)
        self.assertEqual(removed["action_sets"][0]["uuid"], "2345")
        self.assertEqual(removed["entry"], "2345")

        # remove a single action
        flow_def["action_sets"] = [
            dict(uuid="1234", y=10, x=100, actions=[dict(type="reply", msg=None), dict(type="reply", msg="foo")])
        ]
        removed = map_actions(flow_def, lambda x: None if x["msg"] is None else x)

        self.assertEqual(len(removed["action_sets"]), 1)
        self.assertEqual(len(removed["action_sets"][0]["actions"]), 1)
        self.assertEqual(removed["entry"], "2345")

        # no entry
        flow_def = dict(
            entry="1234",
            action_sets=[dict(uuid="1234", y=0, x=100, actions=[dict(type="reply", msg=None)])],
            rule_sets=[],
        )
        removed = map_actions(flow_def, lambda x: None if x["msg"] is None else x)

        self.assertEqual(len(removed["action_sets"]), 0)
        self.assertEqual(removed["entry"], None)

        # check entry horizontal winner
        flow_def = dict(
            entry="1234",
            action_sets=[dict(uuid="1234", x=100, y=0, actions=[dict(type="reply", msg=None)])],
            rule_sets=[dict(y=10, x=100, uuid="5678"), dict(y=10, x=50, uuid="9012")],
        )
        removed = map_actions(flow_def, lambda x: None if x["msg"] is None else x)
        self.assertEqual(removed["entry"], "9012")

        # same horizontal check with action sets
        flow_def = dict(
            entry="1234",
            action_sets=[
                dict(uuid="1234", x=100, y=0, actions=[dict(type="reply", msg=None)]),
                dict(uuid="9012", x=50, y=50, actions=[dict(type="reply", msg="foo")]),
                dict(uuid="3456", x=0, y=50, actions=[dict(type="reply", msg="foo")]),
            ],
            rule_sets=[dict(y=100, x=100, uuid="5678")],
        )

        removed = map_actions(flow_def, lambda x: None if x["msg"] is None else x)
        self.assertEqual(removed["entry"], "3456")
