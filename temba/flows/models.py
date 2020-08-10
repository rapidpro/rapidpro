import logging
import time
from array import array
from collections import OrderedDict, defaultdict
from datetime import timedelta
from enum import Enum
from urllib.request import urlopen

import iso8601
import regex
from django_redis import get_redis_connection
from packaging.version import Version
from smartmin.models import SmartModel
from xlsxlite.writer import XLSXBook

from django.conf import settings
from django.contrib.auth.models import Group, User
from django.core.cache import cache
from django.core.files.temp import NamedTemporaryFile
from django.db import connection as db_connection, models, transaction
from django.db.models import Max, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone
from django.utils.translation import ugettext_lazy as _

from temba import mailroom
from temba.assets.models import register_asset_store
from temba.channels.models import Channel, ChannelConnection
from temba.classifiers.models import Classifier
from temba.contacts.models import URN, Contact, ContactField, ContactGroup
from temba.globals.models import Global
from temba.msgs.models import Attachment, Label, Msg
from temba.orgs.models import Org
from temba.templates.models import Template
from temba.tickets.models import Ticketer
from temba.utils import analytics, chunk_list, json, on_transaction_commit
from temba.utils.dates import str_to_datetime
from temba.utils.export import BaseExportAssetStore, BaseExportTask
from temba.utils.models import (
    JSONAsTextField,
    JSONField,
    RequireUpdateFieldsMixin,
    SquashableModel,
    TembaModel,
    generate_uuid,
)
from temba.utils.s3 import public_file_storage
from temba.utils.uuid import uuid4
from temba.values.constants import Value

from . import legacy

logger = logging.getLogger(__name__)


class Events(Enum):
    broadcast_created = 1
    contact_channel_changed = 2
    contact_field_changed = 3
    contact_groups_changed = 4
    contact_language_changed = 5
    contact_name_changed = 6
    contact_refreshed = 7
    contact_timezone_changed = 8
    contact_urns_changed = 9
    email_created = 10
    environment_refreshed = 11
    error = 12
    flow_entered = 13
    input_labels_added = 14
    ivr_created = 15
    msg_created = 16
    msg_received = 17
    msg_wait = 18
    run_expired = 19
    run_result_changed = 20
    session_triggered = 21
    wait_timed_out = 22
    webhook_called = 23


class FlowException(Exception):
    pass


class FlowInvalidCycleException(FlowException):
    def __init__(self, node_uuids):
        self.node_uuids = node_uuids


class FlowUserConflictException(FlowException):
    def __init__(self, other_user, last_saved_on):
        self.other_user = other_user
        self.last_saved_on = last_saved_on


class FlowVersionConflictException(FlowException):
    def __init__(self, rejected_version):
        self.rejected_version = rejected_version


FLOW_LOCK_TTL = 60  # 1 minute
FLOW_LOCK_KEY = "org:%d:lock:flow:%d:definition"


class Flow(TembaModel):
    UUID = "uuid"
    ENTRY = "entry"
    RULE_SETS = "rule_sets"
    ACTION_SETS = "action_sets"
    RULES = "rules"
    CONFIG = "config"
    ACTIONS = "actions"
    DESTINATION = "destination"
    EXIT_UUID = "exit_uuid"
    LABEL = "label"
    FINISHED_KEY = "finished_key"
    RULESET_TYPE = "ruleset_type"
    OPERAND = "operand"

    LANGUAGE = "language"
    BASE_LANGUAGE = "base_language"
    SAVED_BY = "saved_by"
    VERSION = "version"

    CONTACT_CREATION = "contact_creation"
    CONTACT_PER_RUN = "run"
    CONTACT_PER_LOGIN = "login"

    FLOW_TYPE = "flow_type"
    ID = "id"

    # items in metadata
    METADATA = "metadata"
    METADATA_RESULTS = "results"
    METADATA_DEPENDENCIES = "dependencies"
    METADATA_WAITING_EXIT_UUIDS = "waiting_exit_uuids"
    METADATA_PARENT_REFS = "parent_refs"
    METADATA_ISSUES = "issues"
    METADATA_IVR_RETRY = "ivr_retry"

    # items in legacy metadata
    METADATA_SAVED_ON = "saved_on"
    METADATA_NAME = "name"
    METADATA_REVISION = "revision"
    METADATA_EXPIRES = "expires"

    # items in the response from mailroom flow inspection
    INSPECT_RESULTS = "results"
    INSPECT_DEPENDENCIES = "dependencies"
    INSPECT_WAITING_EXITS = "waiting_exits"
    INSPECT_PARENT_REFS = "parent_refs"
    INSPECT_ISSUES = "issues"

    # items in the flow definition JSON
    DEFINITION_UUID = "uuid"
    DEFINITION_NAME = "name"
    DEFINITION_SPEC_VERSION = "spec_version"
    DEFINITION_TYPE = "type"
    DEFINITION_LANGUAGE = "language"
    DEFINITION_REVISION = "revision"
    DEFINITION_EXPIRE_AFTER_MINUTES = "expire_after_minutes"
    DEFINITION_METADATA = "metadata"
    DEFINITION_NODES = "nodes"
    DEFINITION_UI = "_ui"

    X = "x"
    Y = "y"

    TYPE_MESSAGE = "M"
    TYPE_VOICE = "V"
    TYPE_SURVEY = "S"
    TYPE_USSD = "U"

    FLOW_TYPES = (
        (TYPE_MESSAGE, _("Message flow")),
        (TYPE_VOICE, _("Phone call flow")),
        (TYPE_SURVEY, _("Surveyor flow")),
        (TYPE_USSD, _("USSD flow")),
    )

    GOFLOW_TYPES = {TYPE_MESSAGE: "messaging", TYPE_VOICE: "voice", TYPE_SURVEY: "messaging_offline"}

    NODE_TYPE_RULESET = "R"
    NODE_TYPE_ACTIONSET = "A"

    ENTRY_TYPES = ((NODE_TYPE_RULESET, "Rules"), (NODE_TYPE_ACTIONSET, "Actions"))

    FINAL_LEGACY_VERSION = legacy.VERSIONS[-1]
    INITIAL_GOFLOW_VERSION = "13.0.0"  # initial version of flow spec to use new engine
    CURRENT_SPEC_VERSION = "13.1.0"  # current flow spec version

    DEFAULT_EXPIRES_AFTER = 60 * 12

    name = models.CharField(max_length=64, help_text=_("The name for this flow"))

    labels = models.ManyToManyField(
        "FlowLabel", related_name="flows", verbose_name=_("Labels"), blank=True, help_text=_("Any labels on this flow")
    )

    org = models.ForeignKey(Org, on_delete=models.PROTECT, related_name="flows")

    entry_uuid = models.CharField(null=True, max_length=36, unique=True)

    entry_type = models.CharField(
        max_length=1, null=True, choices=ENTRY_TYPES, help_text=_("The type of node this flow starts with")
    )

    is_archived = models.BooleanField(default=False, help_text=_("Whether this flow is archived"))

    is_system = models.BooleanField(default=False, help_text=_("Whether this is a system created flow"))

    flow_type = models.CharField(
        max_length=1, choices=FLOW_TYPES, default=TYPE_MESSAGE, help_text=_("The type of this flow")
    )

    # additional information about the flow, e.g. possible results
    metadata = JSONAsTextField(null=True, default=dict)

    expires_after_minutes = models.IntegerField(
        default=DEFAULT_EXPIRES_AFTER, help_text=_("Minutes of inactivity that will cause expiration from flow")
    )

    ignore_triggers = models.BooleanField(default=False, help_text=_("Ignore keyword triggers while in this flow"))

    saved_on = models.DateTimeField(auto_now_add=True, help_text=_("When this item was saved"))

    saved_by = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name="flow_saves", help_text=_("The user which last saved this flow")
    )

    base_language = models.CharField(
        max_length=4, null=True, blank=True, help_text=_("The primary language for editing this flow"), default="base"
    )

    version_number = models.CharField(
        default=FINAL_LEGACY_VERSION, max_length=8, help_text=_("The flow version this definition is in")
    )

    channel_dependencies = models.ManyToManyField(Channel, related_name="dependent_flows")

    classifier_dependencies = models.ManyToManyField(Classifier, related_name="dependent_flows")

    field_dependencies = models.ManyToManyField(ContactField, related_name="dependent_flows")

    flow_dependencies = models.ManyToManyField("Flow", related_name="dependent_flows")

    global_dependencies = models.ManyToManyField(Global, related_name="dependent_flows")

    group_dependencies = models.ManyToManyField(ContactGroup, related_name="dependent_flows")

    label_dependencies = models.ManyToManyField(Label, related_name="dependent_flows")

    template_dependencies = models.ManyToManyField(Template, related_name="dependent_flows")

    ticketer_dependencies = models.ManyToManyField(Ticketer, related_name="dependent_flows")

    @classmethod
    def create(
        cls,
        org,
        user,
        name,
        flow_type=TYPE_MESSAGE,
        expires_after_minutes=DEFAULT_EXPIRES_AFTER,
        base_language=None,
        create_revision=False,
        **kwargs,
    ):
        flow = Flow.objects.create(
            org=org,
            name=name,
            flow_type=flow_type,
            expires_after_minutes=expires_after_minutes,
            base_language=base_language,
            saved_by=user,
            created_by=user,
            modified_by=user,
            version_number=Flow.CURRENT_SPEC_VERSION,
            **kwargs,
        )

        if create_revision:
            flow.save_revision(
                user,
                {
                    Flow.DEFINITION_NAME: flow.name,
                    Flow.DEFINITION_UUID: flow.uuid,
                    Flow.DEFINITION_SPEC_VERSION: Flow.CURRENT_SPEC_VERSION,
                    Flow.DEFINITION_LANGUAGE: base_language,
                    Flow.DEFINITION_TYPE: Flow.GOFLOW_TYPES[flow_type],
                    Flow.DEFINITION_NODES: [],
                    Flow.DEFINITION_UI: {},
                },
            )

        analytics.track(user.username, "temba.flow_created", dict(name=name))
        return flow

    @classmethod
    def create_single_message(cls, org, user, message, base_language):
        """
        Creates a special 'single message' flow
        """
        name = "Single Message (%s)" % str(uuid4())
        flow = Flow.create(org, user, name, flow_type=Flow.TYPE_MESSAGE, is_system=True)
        flow.update_single_message_flow(user, message, base_language)
        return flow

    @classmethod
    def label_to_slug(cls, label):
        return regex.sub(r"[^a-z0-9]+", "_", label.lower() if label else "", regex.V0)

    @classmethod
    def create_join_group(cls, org, user, group, response=None, start_flow=None):
        """
        Creates a special 'join group' flow
        """
        base_language = org.primary_language.iso_code if org.primary_language else "base"

        name = Flow.get_unique_name(org, "Join %s" % group.name)
        flow = Flow.create(org, user, name, base_language=base_language)
        flow.version_number = "13.0.0"
        flow.save(update_fields=("version_number",))

        node_uuid = str(uuid4())
        definition = {
            "uuid": flow.uuid,
            "name": flow.name,
            "spec_version": flow.version_number,
            "language": base_language,
            "type": "messaging",
            "localization": {},
            "nodes": [
                {
                    "uuid": node_uuid,
                    "actions": [
                        {
                            "type": "add_contact_groups",
                            "uuid": str(uuid4()),
                            "groups": [{"uuid": group.uuid, "name": group.name}],
                        },
                        {
                            "type": "set_contact_name",
                            "uuid": str(uuid4()),
                            "name": "@(title(remove_first_word(input)))",
                        },
                    ],
                    "exits": [{"uuid": str(uuid4())}],
                }
            ],
            "_ui": {
                "nodes": {node_uuid: {"type": "execute_actions", "position": {"left": 100, "top": 0}}},
                "stickies": {},
            },
        }

        if response:
            definition["nodes"][0]["actions"].append({"type": "send_msg", "uuid": str(uuid4()), "text": response})

        if start_flow:
            definition["nodes"][0]["actions"].append(
                {
                    "type": "enter_flow",
                    "uuid": str(uuid4()),
                    "flow": {"uuid": start_flow.uuid, "name": start_flow.name},
                }
            )

        flow.save_revision(user, definition)
        return flow

    @classmethod
    def get_triggerable_flows(cls, org):
        return Flow.objects.filter(
            org=org,
            is_active=True,
            is_archived=False,
            flow_type__in=(Flow.TYPE_MESSAGE, Flow.TYPE_VOICE),
            is_system=False,
        )

    @classmethod
    def import_flows(cls, org, user, export_json, dependency_mapping, same_site=False):
        """
        Import flows from our flow export file
        """

        from temba.campaigns.models import Campaign
        from temba.triggers.models import Trigger

        version = Version(str(export_json.get("version", "0")))
        created_flows = []
        db_types = {value: key for key, value in Flow.GOFLOW_TYPES.items()}

        # fetch or create all the flow db objects
        for flow_def in export_json[Org.EXPORT_FLOWS]:
            if FlowRevision.is_legacy_definition(flow_def):
                flow_version = Version(flow_def["version"]) if "version" in flow_def else version
                flow_metadata = flow_def[Flow.METADATA]
                flow_type = flow_def.get("flow_type", Flow.TYPE_MESSAGE)
                flow_uuid = flow_metadata["uuid"]
                flow_name = flow_metadata["name"]
                flow_expires = flow_metadata.get(Flow.METADATA_EXPIRES, Flow.DEFAULT_EXPIRES_AFTER)

                FlowRevision.validate_legacy_definition(flow_def)
            else:
                flow_version = Version(flow_def[Flow.DEFINITION_SPEC_VERSION])
                flow_type = db_types[flow_def[Flow.DEFINITION_TYPE]]
                flow_uuid = flow_def[Flow.DEFINITION_UUID]
                flow_name = flow_def[Flow.DEFINITION_NAME]
                flow_expires = flow_def.get(Flow.DEFINITION_EXPIRE_AFTER_MINUTES, Flow.DEFAULT_EXPIRES_AFTER)

            flow = None
            flow_name = flow_name[:64].strip()

            # Exports up to version 3 included campaign message flows, which will have type_type=M. We don't create
            # these here as they'll be created by the campaign event itself.
            if flow_version <= Version("3.0") and flow_type == "M":  # pragma: no cover
                continue

            # M used to mean single message flow and regular flows were F, now all messaging flows are M
            if flow_type == "F":
                flow_type = Flow.TYPE_MESSAGE

            if flow_type == Flow.TYPE_VOICE:
                flow_expires = min([flow_expires, 15])  # voice flow expiration can't be more than 15 minutes

            # check if we can find that flow by UUID first
            if same_site:
                flow = org.flows.filter(is_active=True, uuid=flow_uuid).first()
                if flow:  # pragma: needs cover
                    flow.expires_after_minutes = flow_expires
                    flow.name = Flow.get_unique_name(org, flow_name, ignore=flow)
                    flow.save(update_fields=("name", "expires_after_minutes"))

            # if it's not of our world, let's try by name
            if not flow:
                flow = Flow.objects.filter(org=org, is_active=True, name=flow_name).first()

            # if there isn't one already, create a new flow
            if not flow:
                flow = Flow.create(
                    org,
                    user,
                    Flow.get_unique_name(org, flow_name),
                    flow_type=flow_type,
                    expires_after_minutes=flow_expires,
                )

            # make sure the flow is unarchived
            if flow.is_archived:
                flow.is_archived = False
                flow.save(update_fields=("is_archived",))

            dependency_mapping[flow_uuid] = str(flow.uuid)
            created_flows.append((flow, flow_def))

        # import each definition (includes re-mapping dependency references)
        for flow, definition in created_flows:
            flow.import_definition(user, definition, dependency_mapping)

        # remap flow UUIDs in any campaign events
        for campaign in export_json.get(Org.EXPORT_CAMPAIGNS, []):
            for event in campaign[Campaign.EXPORT_EVENTS]:
                if "flow" in event:
                    flow_uuid = event["flow"]["uuid"]
                    if flow_uuid in dependency_mapping:
                        event["flow"]["uuid"] = dependency_mapping[flow_uuid]

        # remap flow UUIDs in any triggers
        for trigger in export_json.get(Org.EXPORT_TRIGGERS, []):
            if Trigger.EXPORT_FLOW in trigger:
                flow_uuid = trigger[Trigger.EXPORT_FLOW]["uuid"]
                if flow_uuid in dependency_mapping:
                    trigger[Trigger.EXPORT_FLOW]["uuid"] = dependency_mapping[flow_uuid]

        # return the created flows
        return [f[0] for f in created_flows]

    @classmethod
    def copy(cls, flow, user):
        copy = Flow.create(flow.org, user, "Copy of %s" % flow.name[:55], flow_type=flow.flow_type)

        # grab the json of our original
        flow_json = flow.as_json()

        copy.import_definition(user, flow_json, {})

        # copy our expiration as well
        copy.expires_after_minutes = flow.expires_after_minutes
        copy.save()

        return copy

    @classmethod
    def export_translation(cls, org, flows, language, exclude_args):
        flow_ids = [f.id for f in flows]
        return mailroom.get_client().po_export(org.id, flow_ids, language=language, exclude_arguments=exclude_args)

    @classmethod
    def import_translation(cls, org, flows, language, po_data):
        flow_ids = [f.id for f in flows]
        response = mailroom.get_client().po_import(org.id, flow_ids, language=language, po_data=po_data)
        return {d["uuid"]: d for d in response["flows"]}

    @classmethod
    def get_unique_name(cls, org, base_name, ignore=None):
        """
        Generates a unique flow name based on the given base name
        """
        name = base_name[:64].strip()

        count = 2
        while True:
            flows = Flow.objects.filter(name=name, org=org, is_active=True)
            if ignore:  # pragma: needs cover
                flows = flows.exclude(pk=ignore.pk)

            if not flows.exists():
                break

            name = "%s %d" % (base_name[:59].strip(), count)
            count += 1

        return name

    @classmethod
    def apply_action_label(cls, user, flows, label):
        label.toggle_label(flows, add=True)

    @classmethod
    def apply_action_unlabel(cls, user, flows, label):
        label.toggle_label(flows, add=False)

    @classmethod
    def apply_action_archive(cls, user, flows):
        for flow in flows:
            # don't archive flows that belong to campaigns
            from temba.campaigns.models import CampaignEvent

            has_events = CampaignEvent.objects.filter(
                is_active=True, flow=flow, campaign__org=user.get_org(), campaign__is_archived=False
            ).exists()

            if not has_events:
                flow.archive()

    @classmethod
    def apply_action_restore(cls, user, flows):
        for flow in flows:
            try:
                flow.restore()
            except FlowException:  # pragma: no cover
                pass

    def as_select2(self):
        return dict(id=self.uuid, text=self.name)

    def get_category_counts(self):
        keys = [r["key"] for r in self.metadata["results"]]
        counts = (
            FlowCategoryCount.objects.filter(flow_id=self.id)
            .filter(result_key__in=keys)
            .values("result_key", "category_name")
            .annotate(count=Sum("count"), result_name=Max("result_name"))
        )

        results = {}
        for count in counts:
            key = count["result_key"]
            result = results.get(key, {})
            if "name" not in result:
                if count["category_name"] == "All Responses":
                    continue
                result["key"] = key
                result["name"] = count["result_name"]
                result["categories"] = [dict(name=count["category_name"], count=count["count"])]
                result["total"] = count["count"]
            else:
                result["categories"].append(dict(name=count["category_name"], count=count["count"]))
                result["total"] += count["count"]
            results[count["result_key"]] = result

        for k, v in results.items():
            for cat in results[k]["categories"]:
                if results[k]["total"]:
                    cat["pct"] = float(cat["count"]) / float(results[k]["total"])
                else:
                    cat["pct"] = 0

        # order counts by their place on the flow
        result_list = []
        for key in keys:
            result = results.get(key)
            if result:
                result_list.append(result)

        return dict(counts=result_list)

    def lock(self):
        """
        Locks on this flow to let us make changes to the definition in a thread safe way
        """
        r = get_redis_connection()
        lock_key = FLOW_LOCK_KEY % (self.org_id, self.id)
        return r.lock(lock_key, FLOW_LOCK_TTL)

    def get_node_counts(self):
        """
        Gets the number of contacts at each node in the flow
        """
        return FlowNodeCount.get_totals(self)

    def get_segment_counts(self):
        """
        Gets the number of contacts to have taken each flow segment.
        """
        return FlowPathCount.get_totals(self)

    def get_activity(self):
        """
        Get the activity summary for a flow as a tuple of the number of active runs
        at each step and a map of the previous visits
        """
        return self.get_node_counts(), self.get_segment_counts()

    def is_starting(self):
        """
        Returns whether this flow is already being started by a user
        """
        return (
            self.starts.filter(status__in=(FlowStart.STATUS_STARTING, FlowStart.STATUS_PENDING))
            .exclude(created_by=None)
            .exists()
        )

    def import_definition(self, user, definition, dependency_mapping):
        """
        Allows setting the definition for a flow from another definition. All UUID's will be remapped.
        """
        if FlowRevision.is_legacy_definition(definition):
            self.import_legacy_definition(definition, dependency_mapping)
            return

        flow_info = mailroom.get_client().flow_inspect(self.org.id, definition)
        dependencies = flow_info[Flow.INSPECT_DEPENDENCIES]

        def deps_of_type(type_name):
            return [d for d in dependencies if d["type"] == type_name]

        # ensure any channel dependencies exist
        for ref in deps_of_type("channel"):
            channel = self.org.channels.filter(is_active=True, uuid=ref["uuid"]).first()
            if not channel and ref["name"]:
                name = ref["name"].split(":")[-1].strip()
                channel = self.org.channels.filter(is_active=True, name=name).first()

            dependency_mapping[ref["uuid"]] = str(channel.uuid) if channel else ref["uuid"]

        # ensure any field dependencies exist
        for ref in deps_of_type("field"):
            ContactField.get_or_create(self.org, user, ref["key"], ref["name"])

        # lookup additional flow dependencies by name (i.e. for flows not in the export itself)
        for ref in deps_of_type("flow"):
            if ref["uuid"] not in dependency_mapping:
                flow = self.org.flows.filter(uuid=ref["uuid"], is_active=True).first()
                if not flow and ref["name"]:
                    flow = self.org.flows.filter(name=ref["name"], is_active=True).first()

                dependency_mapping[ref["uuid"]] = str(flow.uuid) if flow else ref["uuid"]

        # lookup/create additional group dependencies (i.e. for flows not in the export itself)
        for ref in deps_of_type("group"):
            if ref["uuid"] not in dependency_mapping:
                group = ContactGroup.get_or_create(self.org, user, ref.get("name"), uuid=ref["uuid"])
                dependency_mapping[ref["uuid"]] = str(group.uuid)

        # ensure any label dependencies exist
        for ref in deps_of_type("label"):
            label = Label.get_or_create(self.org, user, ref["name"])
            dependency_mapping[ref["uuid"]] = str(label.uuid)

        # ensure any template dependencies exist
        for ref in deps_of_type("template"):
            template = self.org.templates.filter(uuid=ref["uuid"]).first()
            if not template and ref["name"]:
                template = self.org.templates.filter(name=ref["name"]).first()

            dependency_mapping[ref["uuid"]] = str(template.uuid) if template else ref["uuid"]

        # clone definition so that all flow elements get new random UUIDs
        cloned_definition = mailroom.get_client().flow_clone(definition, dependency_mapping)
        if "revision" in cloned_definition:
            del cloned_definition["revision"]

        # save a new revision but we can't validate it just yet because we're in a transaction and mailroom
        # won't see any new database objects
        self.save_revision(user, cloned_definition)

    def import_legacy_definition(self, flow_json, uuid_map):
        """
        Imports a legacy definition
        """

        def copy_recording(url, path):
            if not url:
                return None

            try:  # pragma: needs cover
                url = f"{settings.STORAGE_URL}/{url}"
                temp = NamedTemporaryFile(delete=True)
                temp.write(urlopen(url).read())
                temp.flush()
                return public_file_storage.save(path, temp)
            except Exception:  # pragma: needs cover
                # its okay if its no longer there, we'll remove the recording
                return None

        def remap_uuid(json, attribute):
            if attribute in json and json[attribute]:
                uuid = json[attribute]
                new_uuid = uuid_map.get(uuid, None)
                if not new_uuid:
                    new_uuid = str(uuid4())
                    uuid_map[uuid] = new_uuid

                json[attribute] = new_uuid

        def remap_label(label):
            # labels can be single string expressions
            if type(label) is dict:
                # we haven't been mapped yet (also, non-uuid labels can't be mapped)
                if ("uuid" not in label or label["uuid"] not in uuid_map) and Label.is_valid_name(label["name"]):
                    label_instance = Label.get_or_create(self.org, self.created_by, label["name"])

                    # map label references that started with a uuid
                    if "uuid" in label:
                        uuid_map[label["uuid"]] = label_instance.uuid

                    label["uuid"] = label_instance.uuid

                # we were already mapped
                elif label["uuid"] in uuid_map:
                    label["uuid"] = uuid_map[label["uuid"]]

        def remap_group(group):
            # groups can be single string expressions
            if type(group) is dict:

                # we haven't been mapped yet (also, non-uuid groups can't be mapped)
                if "uuid" not in group or group["uuid"] not in uuid_map:
                    group_instance = ContactGroup.get_or_create(
                        self.org, self.created_by, group["name"], uuid=group.get("uuid", None)
                    )

                    # map group references that started with a uuid
                    if "uuid" in group:
                        uuid_map[group["uuid"]] = group_instance.uuid

                    group["uuid"] = group_instance.uuid

                # we were already mapped
                elif group["uuid"] in uuid_map:
                    group["uuid"] = uuid_map[group["uuid"]]

        def remap_flow(element):
            # first map our id accordingly
            if element["uuid"] in uuid_map:
                element["uuid"] = uuid_map[element["uuid"]]

            existing_flow = Flow.objects.filter(uuid=element["uuid"], org=self.org, is_active=True).first()
            if not existing_flow:
                existing_flow = Flow.objects.filter(org=self.org, name=element["name"], is_active=True).first()
                if existing_flow:
                    element["uuid"] = existing_flow.uuid  # pragma: needs cover

        remap_uuid(flow_json[Flow.METADATA], "uuid")
        remap_uuid(flow_json, "entry")

        needs_move_entry = False

        for actionset in flow_json[Flow.ACTION_SETS]:
            remap_uuid(actionset, "uuid")
            remap_uuid(actionset, "exit_uuid")
            remap_uuid(actionset, "destination")
            valid_actions = []

            # for all of our recordings, pull them down and remap
            for action in actionset["actions"]:

                for group in action.get("groups", []):
                    remap_group(group)

                for label in action.get("labels", []):
                    remap_label(label)

                if "recording" in action:
                    # if its a localized
                    if isinstance(action["recording"], dict):  # pragma: no cover
                        for lang, url in action["recording"].items():
                            path = copy_recording(
                                url, "recordings/%d/%d/steps/%s.wav" % (self.org.pk, self.pk, action["uuid"])
                            )
                            action["recording"][lang] = path
                    else:
                        path = copy_recording(
                            action["recording"],
                            "recordings/%d/%d/steps/%s.wav" % (self.org.pk, self.pk, action["uuid"]),
                        )
                        action["recording"] = path

                if "channel" in action:
                    channel = None
                    channel_uuid = action.get("channel")
                    channel_name = action.get("name")

                    if channel_uuid is not None:
                        channel = self.org.channels.filter(is_active=True, uuid=channel_uuid).first()

                    if channel is None and channel_name is not None:
                        name = channel_name.split(":")[-1].strip()
                        channel = self.org.channels.filter(is_active=True, name=name).first()

                    if channel is None:
                        continue
                    else:
                        action["channel"] = channel.uuid
                        action["name"] = "%s: %s" % (channel.get_channel_type_display(), channel.get_address_display())

                if action["type"] in ["flow", "trigger-flow"]:
                    remap_flow(action["flow"])

                valid_actions.append(action)

            actionset["actions"] = valid_actions
            if not valid_actions:
                uuid_map[actionset["uuid"]] = actionset.get("destination")
                if actionset["uuid"] == flow_json["entry"]:
                    flow_json["entry"] = actionset.get("destination")
                    needs_move_entry = True

        for actionset in flow_json[Flow.ACTION_SETS]:
            if needs_move_entry and actionset["uuid"] == flow_json.get("entry"):
                actionset["y"] = 0

        for ruleset in flow_json[Flow.RULE_SETS]:
            remap_uuid(ruleset, "uuid")

            if ruleset["ruleset_type"] == RuleSet.TYPE_SUBFLOW:
                remap_flow(ruleset["config"]["flow"])

            for rule in ruleset.get(Flow.RULES, []):
                remap_uuid(rule, "uuid")
                remap_uuid(rule, "destination")

                if rule["test"]["type"] == legacy.InGroupTest.TYPE:
                    group = rule["test"]["test"]
                    remap_group(group)

        # now update with our remapped values
        self.update(flow_json)

    def archive(self):
        self.is_archived = True
        self.save(update_fields=["is_archived"])

        # queue mailroom to interrupt sessions where contact is currently in this flow
        mailroom.queue_interrupt(self.org, flow=self)

        # archive our triggers as well
        from temba.triggers.models import Trigger

        Trigger.objects.filter(flow=self).update(is_archived=True)

    def restore(self):
        self.is_archived = False
        self.save(update_fields=["is_archived"])

    def update_single_message_flow(self, user, translations, base_language):
        assert translations and base_language in translations, "must include translation for base language"

        self.base_language = base_language
        self.version_number = "13.0.0"
        self.save(update_fields=("name", "base_language", "version_number"))

        translations = translations.copy()  # don't modify instance being saved on event object

        action_uuid = str(uuid4())
        base_text = translations.pop(base_language)
        localization = {k: {action_uuid: {"text": [v]}} for k, v in translations.items()}

        definition = {
            "uuid": "8ca44c09-791d-453a-9799-a70dd3303306",
            "name": self.name,
            "spec_version": self.version_number,
            "language": base_language,
            "type": "messaging",
            "localization": localization,
            "nodes": [
                {
                    "uuid": str(uuid4()),
                    "actions": [{"uuid": action_uuid, "type": "send_msg", "text": base_text}],
                    "exits": [{"uuid": "0c599307-8222-4386-b43c-e41654f03acf"}],
                }
            ],
        }

        self.save_revision(user, definition)

    def get_run_stats(self):
        totals_by_exit = FlowRunCount.get_totals(self)
        total_runs = sum(totals_by_exit.values())

        return {
            "total": total_runs,
            "active": totals_by_exit[FlowRun.STATE_ACTIVE],
            "completed": totals_by_exit[FlowRun.EXIT_TYPE_COMPLETED],
            "expired": totals_by_exit[FlowRun.EXIT_TYPE_EXPIRED],
            "interrupted": totals_by_exit[FlowRun.EXIT_TYPE_INTERRUPTED],
            "completion": int(totals_by_exit[FlowRun.EXIT_TYPE_COMPLETED] * 100 // total_runs) if total_runs else 0,
        }

    def async_start(self, user, groups, contacts, query=None, restart_participants=False, include_active=True):
        """
        Causes us to schedule a flow to start in a background thread.
        """

        flow_start = FlowStart.objects.create(
            org=self.org,
            flow=self,
            start_type=FlowStart.TYPE_MANUAL,
            restart_participants=restart_participants,
            include_active=include_active,
            created_by=user,
            query=query,
        )

        contact_ids = [c.id for c in contacts]
        flow_start.contacts.add(*contact_ids)

        group_ids = [g.id for g in groups]
        flow_start.groups.add(*group_ids)
        flow_start.async_start()

    def get_export_dependencies(self):
        """
        Get the dependencies of this flow that should be exported with it
        """
        dependencies = set()
        dependencies.update(self.flow_dependencies.all())
        dependencies.update(self.field_dependencies.all())
        dependencies.update(self.group_dependencies.all())
        return dependencies

    def get_dependencies_metadata(self, type_name):
        """
        Get the dependencies of the given type from the flow metadata
        """
        deps = self.metadata.get(Flow.METADATA_DEPENDENCIES, [])
        return [d for d in deps if d["type"] == type_name]

    def is_legacy(self):
        """
        Returns whether this flow still uses a legacy definition
        """
        return Version(self.version_number) < Version(Flow.INITIAL_GOFLOW_VERSION)

    def as_export_ref(self):
        return {Flow.DEFINITION_UUID: str(self.uuid), Flow.DEFINITION_NAME: self.name}

    def as_json(self, expand_contacts=False):
        if self.is_legacy():
            return self.get_legacy_definition(expand_contacts)

        return self.get_definition()

    def get_legacy_definition(self, expand_contacts=False):
        """
        Builds the JSON definition for a legacy flow from its action sets and rule sets.

          expand_contacts:
            Add names for contacts and groups that are just ids. This is useful for human readable
            situations such as the flow editor.

        """

        flow = dict()

        if self.entry_uuid:
            flow[Flow.ENTRY] = self.entry_uuid
        else:
            flow[Flow.ENTRY] = None

        actionsets = []
        for actionset in ActionSet.objects.filter(flow=self).order_by("pk"):
            actionsets.append(actionset.as_json())

        def lookup_action_contacts(action, contacts, groups):

            if "contact" in action:  # pragma: needs cover
                contacts.append(action["contact"]["uuid"])

            if "contacts" in action:
                for contact in action["contacts"]:
                    contacts.append(contact["uuid"])

            if "group" in action:  # pragma: needs cover
                g = action["group"]
                if isinstance(g, dict):
                    if "uuid" in g:
                        groups.append(g["uuid"])

            if "groups" in action:
                for group in action["groups"]:
                    if isinstance(group, dict):
                        if "uuid" in group:
                            groups.append(group["uuid"])

        def replace_action_contacts(action, contacts, groups):

            if "contact" in action:  # pragma: needs cover
                contact = contacts.get(action["contact"]["uuid"], None)
                if contact:
                    action["contact"] = contact.as_json()

            if "contacts" in action:
                expanded_contacts = []
                for contact in action["contacts"]:
                    contact = contacts.get(contact["uuid"], None)
                    if contact:
                        expanded_contacts.append(contact.as_json())

                action["contacts"] = expanded_contacts

            if "group" in action:  # pragma: needs cover
                # variable substitution
                group = action["group"]
                if isinstance(group, dict):
                    if "uuid" in group:
                        group = groups.get(group["uuid"], None)
                        if group:
                            action["group"] = dict(uuid=group.uuid, name=group.name)

            if "groups" in action:
                expanded_groups = []
                for group in action["groups"]:

                    # variable substitution
                    if not isinstance(group, dict):
                        expanded_groups.append(group)
                    else:
                        group_instance = groups.get(group["uuid"], None)
                        if group_instance:
                            expanded_groups.append(dict(uuid=group_instance.uuid, name=group_instance.name))
                        else:
                            expanded_groups.append(group)

                action["groups"] = expanded_groups

        if expand_contacts:
            groups = []
            contacts = []

            for actionset in actionsets:
                for action in actionset["actions"]:
                    lookup_action_contacts(action, contacts, groups)

            # load them all
            contacts = dict((_.uuid, _) for _ in self.org.contacts.filter(uuid__in=contacts))
            groups = dict((_.uuid, _) for _ in ContactGroup.user_groups.filter(org=self.org, uuid__in=groups))

            # and replace them
            for actionset in actionsets:
                for action in actionset["actions"]:
                    replace_action_contacts(action, contacts, groups)

        flow[Flow.ACTION_SETS] = actionsets

        # add in our rulesets
        rulesets = []
        for ruleset in RuleSet.objects.filter(flow=self).order_by("pk"):
            rulesets.append(ruleset.as_json())
        flow[Flow.RULE_SETS] = rulesets

        # required flow running details
        flow[Flow.BASE_LANGUAGE] = self.base_language
        flow[Flow.FLOW_TYPE] = self.flow_type
        flow[Flow.VERSION] = Flow.FINAL_LEGACY_VERSION
        flow[Flow.METADATA] = self.get_legacy_metadata()
        return flow

    def get_legacy_metadata(self):
        exclude_keys = (Flow.METADATA_RESULTS, Flow.METADATA_WAITING_EXIT_UUIDS, Flow.METADATA_PARENT_REFS)
        metadata = {k: v for k, v in self.metadata.items() if k not in exclude_keys}

        revision = self.get_current_revision()

        last_saved = self.saved_on
        if self.saved_by == get_flow_user(self.org):
            last_saved = self.modified_on

        metadata[Flow.UUID] = self.uuid
        metadata[Flow.METADATA_NAME] = self.name
        metadata[Flow.METADATA_SAVED_ON] = json.encode_datetime(last_saved, micros=True)
        metadata[Flow.METADATA_REVISION] = revision.revision if revision else 1
        metadata[Flow.METADATA_EXPIRES] = self.expires_after_minutes

        return metadata

    @classmethod
    def get_metadata(cls, flow_info, previous=None):
        data = {
            Flow.METADATA_RESULTS: flow_info[Flow.INSPECT_RESULTS],
            Flow.METADATA_DEPENDENCIES: flow_info[Flow.INSPECT_DEPENDENCIES],
            Flow.METADATA_WAITING_EXIT_UUIDS: flow_info[Flow.INSPECT_WAITING_EXITS],
            Flow.METADATA_PARENT_REFS: flow_info[Flow.INSPECT_PARENT_REFS],
            Flow.METADATA_ISSUES: flow_info[Flow.INSPECT_ISSUES],
        }

        # IVR retry is the only value in metadata that doesn't come from flow inspection
        if previous and Flow.METADATA_IVR_RETRY in previous:
            data[Flow.METADATA_IVR_RETRY] = previous[Flow.METADATA_IVR_RETRY]

        return data

    @classmethod
    def detect_invalid_cycles(cls, json_dict):
        """
        Checks for invalid cycles in our flow
        :param json_dict: our flow definition
        :return: invalid cycle path as list of uuids if found, otherwise empty list
        """

        # Adapted from a blog post by Guido:
        # http://neopythonic.blogspot.com/2009/01/detecting-cycles-in-directed-graph.html

        # Maintain path as a a depth-first path in the implicit tree;
        # path is represented as an OrderedDict of {node: [child,...]} pairs.

        nodes = list()
        node_map = {}

        for ruleset in json_dict.get(Flow.RULE_SETS, []):
            nodes.append(ruleset.get("uuid"))
            node_map[ruleset.get("uuid")] = ruleset

        for actionset in json_dict.get(Flow.ACTION_SETS, []):
            nodes.append(actionset.get("uuid"))
            node_map[actionset.get("uuid")] = actionset

        def get_destinations(uuid):
            node = node_map.get(uuid)

            if not node:  # pragma: needs cover
                return []

            rules = node.get("rules", [])
            destinations = []
            if rules:

                if node.get("ruleset_type", None) in RuleSet.TYPE_WAIT:
                    return []

                for rule in rules:
                    if rule.get("destination"):
                        destinations.append(rule.get("destination"))

            elif node.get("destination"):
                destinations.append(node.get("destination"))
            return destinations

        while nodes:
            root = nodes.pop()
            path = OrderedDict({root: get_destinations(root)})
            while path:
                # children at the fringe of the tree
                children = path[next(reversed(path))]
                while children:
                    child = children.pop()

                    # found a loop
                    if child in path:
                        pathlist = list(path)
                        return pathlist[pathlist.index(child) :] + [child]

                    # new path
                    if child in nodes:
                        path[child] = get_destinations(child)
                        nodes.remove(child)
                        break
                else:
                    # no more children; pop back up a level
                    path.popitem()
        return None

    def ensure_current_version(self):
        """
        Makes sure the flow is at the latest legacy or goflow spec version
        """

        to_version = Flow.FINAL_LEGACY_VERSION if self.is_legacy() else Flow.CURRENT_SPEC_VERSION

        # nothing to do if flow is already at the target version
        if Version(self.version_number) >= Version(to_version):
            return

        with self.lock():
            revision = self.get_current_revision()
            if revision:
                flow_def = revision.get_definition_json(to_version)
            else:  # pragma: needs cover
                flow_def = self.as_json()

            if self.is_legacy():
                self.update(flow_def, user=get_flow_user(self.org))
            else:
                self.save_revision(get_flow_user(self.org), flow_def)

            self.refresh_from_db()

    def get_definition(self):
        """
        Returns the current definition of this flow
        """
        rev = self.get_current_revision()

        assert rev, "can't get definition of flow with no revisions"

        # update metadata in definition from database object as it may be out of date
        definition = rev.definition
        definition[Flow.DEFINITION_UUID] = self.uuid
        definition[Flow.DEFINITION_NAME] = self.name
        definition[Flow.DEFINITION_REVISION] = rev.revision
        definition[Flow.DEFINITION_EXPIRE_AFTER_MINUTES] = self.expires_after_minutes
        return definition

    def get_current_revision(self):
        """
        Returns the last saved revision for this flow if any
        """
        return self.revisions.order_by("revision").last()

    def save_revision(self, user, definition):
        """
        Saves a new revision for this flow, validation will be done on the definition first
        """
        if Version(definition.get(Flow.DEFINITION_SPEC_VERSION)) < Version(Flow.INITIAL_GOFLOW_VERSION):
            raise FlowVersionConflictException(definition.get(Flow.DEFINITION_SPEC_VERSION))

        current_revision = self.get_current_revision()

        if current_revision:
            # check we aren't walking over someone else
            definition_revision = definition.get(Flow.DEFINITION_REVISION)
            if definition_revision is not None and definition_revision < current_revision.revision:
                raise FlowUserConflictException(self.saved_by, self.saved_on)

            revision = current_revision.revision + 1
        else:
            revision = 1

        # update metadata from database object
        definition[Flow.DEFINITION_UUID] = self.uuid
        definition[Flow.DEFINITION_NAME] = self.name
        definition[Flow.DEFINITION_REVISION] = revision
        definition[Flow.DEFINITION_EXPIRE_AFTER_MINUTES] = self.expires_after_minutes

        # inspect the flow (with optional validation)
        flow_info = mailroom.get_client().flow_inspect(self.org.id, definition)
        dependencies = flow_info[Flow.INSPECT_DEPENDENCIES]

        with transaction.atomic():
            # update our flow fields
            self.base_language = definition.get(Flow.DEFINITION_LANGUAGE, None)
            self.metadata = Flow.get_metadata(flow_info, self.metadata)
            self.saved_by = user
            self.saved_on = timezone.now()
            self.version_number = Flow.CURRENT_SPEC_VERSION
            self.save(update_fields=["metadata", "version_number", "base_language", "saved_by", "saved_on"])

            # create our new revision
            revision = self.revisions.create(
                definition=definition,
                created_by=user,
                modified_by=user,
                spec_version=Flow.CURRENT_SPEC_VERSION,
                revision=revision,
            )

            self.update_dependencies(dependencies)

        return revision

    def update(self, json_dict, user=None, force=False):
        """
        Updates a definition for a flow and returns the new revision
        """

        cycle_node_uuids = Flow.detect_invalid_cycles(json_dict)
        if cycle_node_uuids:
            raise FlowInvalidCycleException(cycle_node_uuids)

        # make sure the flow version hasn't changed out from under us
        if Version(json_dict.get(Flow.VERSION)) != Version(Flow.FINAL_LEGACY_VERSION):
            raise FlowVersionConflictException(json_dict.get(Flow.VERSION))

        flow_user = get_flow_user(self.org)
        # check whether the flow has changed since this flow was last saved
        if user and not force:
            saved_on = json_dict.get(Flow.METADATA, {}).get(Flow.METADATA_SAVED_ON, None)
            org = user.get_org()

            # check our last save if we aren't the system flow user
            if user != flow_user:
                migrated = self.saved_by == flow_user
                last_save = self.saved_on

                # use modified on if it was a migration
                if migrated:
                    last_save = self.modified_on

                if not saved_on or str_to_datetime(saved_on, org.timezone) < last_save:
                    raise FlowUserConflictException(self.saved_by, last_save)

        try:
            # run through all our action sets and validate / instantiate them, we need to do this in a transaction
            # or mailroom won't know about the labels / groups possibly created here
            with transaction.atomic():
                for actionset in json_dict.get(Flow.ACTION_SETS, []):
                    actions = [
                        _.as_json() for _ in legacy.Action.from_json_array(self.org, actionset.get(Flow.ACTIONS))
                    ]
                    actionset[Flow.ACTIONS] = actions

            flow_info = mailroom.get_client().flow_inspect(self.org.id, json_dict)
            dependencies = flow_info[Flow.INSPECT_DEPENDENCIES]

            with transaction.atomic():
                # TODO remove this when we no longer need rulesets or actionsets
                self.update_rulesets_and_actionsets(json_dict)

                # if we have a base language, set that
                self.base_language = json_dict.get("base_language", None)

                # set our metadata
                self.metadata = json_dict.get(Flow.METADATA, {})
                self.metadata[Flow.METADATA_RESULTS] = flow_info[Flow.INSPECT_RESULTS]
                self.metadata[Flow.METADATA_WAITING_EXIT_UUIDS] = flow_info[Flow.INSPECT_WAITING_EXITS]
                self.metadata[Flow.METADATA_PARENT_REFS] = flow_info[Flow.INSPECT_PARENT_REFS]

                if user:
                    self.saved_by = user

                # if it's our migration user, don't update saved on
                if user and user != flow_user:
                    self.saved_on = timezone.now()

                self.version_number = Flow.FINAL_LEGACY_VERSION
                self.save()

                # in case rulesets/actionsets were prefetched, clear those cached values
                # TODO https://code.djangoproject.com/ticket/29625
                self.action_sets._remove_prefetched_objects()
                self.rule_sets._remove_prefetched_objects()

                # create a version of our flow for posterity
                if user is None:
                    user = self.created_by

                # last version
                revision_num = 1
                last_revision = self.get_current_revision()
                if last_revision:
                    revision_num = last_revision.revision + 1

                # create a new version
                revision = self.revisions.create(
                    definition=json_dict,
                    created_by=user,
                    modified_by=user,
                    spec_version=Flow.FINAL_LEGACY_VERSION,
                    revision=revision_num,
                )

                self.update_dependencies(dependencies)

        except Exception as e:
            # user will see an error in the editor but log exception so we know we got something to fix
            logger.error(str(e), exc_info=True)
            raise e

        return revision

    def update_rulesets_and_actionsets(self, json_dict):
        """
        Creates RuleSet and ActionSet database objects as required by the legacy engine
        """

        def get_step_type(dest, rulesets, actionsets):
            if dest:
                if rulesets.get(dest, None):
                    return Flow.NODE_TYPE_RULESET
                if actionsets.get(dest, None):
                    return Flow.NODE_TYPE_ACTIONSET
            return None

        top_y = 0
        top_uuid = None

        # load all existing objects into dicts by uuid
        existing_actionsets = {actionset.uuid: actionset for actionset in self.action_sets.all()}
        existing_rulesets = {ruleset.uuid: ruleset for ruleset in self.rule_sets.all()}

        # set of uuids which we've seen, we use this set to remove objects no longer used in this flow
        seen_rulesets = set()
        seen_actionsets = set()
        destinations = set()

        # our steps in our current update submission
        current_actionsets = {}
        current_rulesets = {}

        # parse our actions
        for actionset in json_dict.get(Flow.ACTION_SETS, []):

            uuid = actionset.get(Flow.UUID)

            # validate our actions, normalizing them as JSON after reading them
            actions = [_.as_json() for _ in legacy.Action.from_json_array(self.org, actionset.get(Flow.ACTIONS))]

            if actions:
                current_actionsets[uuid] = actions

        for ruleset in json_dict.get(Flow.RULE_SETS, []):
            uuid = ruleset.get(Flow.UUID)
            current_rulesets[uuid] = ruleset
            seen_rulesets.add(uuid)

        # create all our rule sets
        for ruleset in json_dict.get(Flow.RULE_SETS, []):

            uuid = ruleset.get(Flow.UUID)
            rules = ruleset.get(Flow.RULES)
            label = ruleset.get(Flow.LABEL, None)
            operand = ruleset.get(Flow.OPERAND, None)
            finished_key = ruleset.get(Flow.FINISHED_KEY)
            ruleset_type = ruleset.get(Flow.RULESET_TYPE)
            config = ruleset.get(Flow.CONFIG)

            if not config:
                config = dict()

            # cap our lengths
            if label:
                label = label[:64]

            if operand:
                operand = operand[:128]

            (x, y) = (ruleset.get(Flow.X), ruleset.get(Flow.Y))

            if not top_uuid or y < top_y:
                top_y = y
                top_uuid = uuid

            # parse our rules, this will materialize any necessary dependencies
            parsed_rules = []
            rule_objects = legacy.Rule.from_json_array(self.org, rules)
            for r in rule_objects:
                parsed_rules.append(r.as_json())
            rules = parsed_rules

            for rule in rules:
                if "destination" in rule:
                    # if the destination was excluded for not having any actions
                    # remove the connection for our rule too
                    if rule["destination"] not in current_actionsets and rule["destination"] not in seen_rulesets:
                        rule["destination"] = None
                    else:
                        destination_uuid = rule.get("destination", None)
                        destinations.add(destination_uuid)

                        # determine what kind of destination we are pointing to
                        rule["destination_type"] = get_step_type(
                            destination_uuid, current_rulesets, current_actionsets
                        )

                        # print "Setting destination [%s] type to: %s" % (destination_uuid, rule['destination_type'])

            existing = existing_rulesets.get(uuid, None)

            if existing:
                existing.label = ruleset.get(Flow.LABEL, None)
                existing.rules = rules
                existing.operand = operand
                existing.label = label
                existing.finished_key = finished_key
                existing.ruleset_type = ruleset_type
                existing.config = config
                (existing.x, existing.y) = (x, y)
                existing.save()
            else:

                existing = RuleSet.objects.create(
                    flow=self,
                    uuid=uuid,
                    label=label,
                    rules=rules,
                    finished_key=finished_key,
                    ruleset_type=ruleset_type,
                    operand=operand,
                    config=config,
                    x=x,
                    y=y,
                )

            existing_rulesets[uuid] = existing

            # update our value type based on our new rules
            existing.value_type = existing.get_value_type()
            RuleSet.objects.filter(pk=existing.pk).update(value_type=existing.value_type)

        # now work through our action sets
        for actionset in json_dict.get(Flow.ACTION_SETS, []):
            uuid = actionset.get(Flow.UUID)
            exit_uuid = actionset.get(Flow.EXIT_UUID)

            # skip actionsets without any actions. This happens when there are no valid
            # actions in an actionset such as when deleted groups or flows are the only actions
            if uuid not in current_actionsets:
                continue

            actions = current_actionsets[uuid]
            seen_actionsets.add(uuid)

            (x, y) = (actionset.get(Flow.X), actionset.get(Flow.Y))

            if not top_uuid or y < top_y:
                top_y = y
                top_uuid = uuid

            existing = existing_actionsets.get(uuid, None)

            # lookup our destination
            destination_uuid = actionset.get("destination")
            destination_type = get_step_type(destination_uuid, current_rulesets, current_actionsets)

            if destination_uuid:
                if not destination_type:
                    destination_uuid = None

            # only create actionsets if there are actions
            if actions:
                if existing:
                    # print "Updating %s to point to %s" % (unicode(actions), destination_uuid)
                    existing.destination = destination_uuid
                    existing.destination_type = destination_type
                    existing.exit_uuid = exit_uuid
                    existing.actions = actions
                    (existing.x, existing.y) = (x, y)
                    existing.save()
                else:
                    existing = ActionSet.objects.create(
                        flow=self,
                        uuid=uuid,
                        destination=destination_uuid,
                        destination_type=destination_type,
                        exit_uuid=exit_uuid,
                        actions=actions,
                        x=x,
                        y=y,
                    )

                    existing_actionsets[uuid] = existing

        existing_actionsets_to_delete = set()
        seen_existing_actionsets = {}

        # now work through all our objects once more, making sure all uuids map appropriately
        for uuid, actionset in existing_actionsets.items():
            if uuid not in seen_actionsets:
                existing_actionsets_to_delete.add(uuid)
            else:
                seen_existing_actionsets[uuid] = actionset

        # delete actionset which are not seen
        ActionSet.objects.filter(uuid__in=existing_actionsets_to_delete).delete()

        existing_actionsets = seen_existing_actionsets

        existing_rulesets_to_delete = set()
        seen_existing_rulesets = {}

        for uuid, ruleset in existing_rulesets.items():
            if uuid not in seen_rulesets:
                existing_rulesets_to_delete.add(uuid)

                # instead of deleting it, make it a phantom ruleset until we do away with values_value
                ruleset.flow = None
                ruleset.uuid = str(uuid4())
                ruleset.save(update_fields=("flow", "uuid"))
            else:
                seen_existing_rulesets[uuid] = ruleset

        existing_rulesets = seen_existing_rulesets

        # make sure all destinations are present though
        for destination in destinations:
            if destination not in existing_rulesets and destination not in existing_actionsets:  # pragma: needs cover
                raise FlowException("Invalid destination: '%s', no matching actionset or ruleset" % destination)

        entry = json_dict.get("entry", None)

        # check if we are pointing to a destination that is no longer valid
        if entry not in existing_rulesets and entry not in existing_actionsets:
            entry = None

        if not entry and top_uuid:
            entry = top_uuid

        # set our entry
        if entry in existing_actionsets:
            self.entry_uuid = entry
            self.entry_type = Flow.NODE_TYPE_ACTIONSET
        elif entry in existing_rulesets:
            self.entry_uuid = entry
            self.entry_type = Flow.NODE_TYPE_RULESET

    def update_dependencies(self, dependencies):
        # build a lookup of types to identifier lists
        identifiers = defaultdict(list)
        for dep in dependencies:
            identifier = dep.get("uuid", dep.get("key"))
            identifiers[dep["type"]].append(identifier)

        # globals aren't included in exports so they're created here too if they don't exist, with blank values
        if identifiers["global"]:
            org_globals = set(self.org.globals.filter(is_active=True).values_list("key", flat=True))

            globals_to_create = set(identifiers["global"]).difference(org_globals)
            for g in globals_to_create:
                Global.get_or_create(self.org, self.modified_by, g, name="", value="")

        # find all the dependencies in the database
        dep_objs = {
            "channel": self.org.channels.filter(is_active=True, uuid__in=identifiers["channel"]),
            "classifier": self.org.classifiers.filter(is_active=True, uuid__in=identifiers["classifier"]),
            "field": ContactField.user_fields.filter(org=self.org, is_active=True, key__in=identifiers["field"]),
            "flow": self.org.flows.filter(is_active=True, uuid__in=identifiers["flow"]),
            "global": self.org.globals.filter(is_active=True, key__in=identifiers["global"]),
            "group": ContactGroup.user_groups.filter(org=self.org, is_active=True, uuid__in=identifiers["group"]),
            "label": Label.label_objects.filter(org=self.org, uuid__in=identifiers["label"]),
            "template": self.org.templates.filter(uuid__in=identifiers["template"]),
            "ticketer": self.org.ticketers.filter(is_active=True, uuid__in=identifiers["ticketer"]),
        }

        # reset the m2m for each type
        for type_name, objects in dep_objs.items():
            m2m = getattr(self, f"{type_name}_dependencies")
            m2m.clear()
            m2m.add(*objects)

    def release(self):
        """
        Releases this flow, marking it inactive. We interrupt all flow runs in a background process.
        We keep FlowRevisions and FlowStarts however.
        """

        self.is_active = False
        self.save(update_fields=("is_active",))

        # release any campaign events that depend on this flow
        from temba.campaigns.models import CampaignEvent

        for event in CampaignEvent.objects.filter(flow=self, is_active=True):
            event.release()

        # release any triggers that depend on this flow
        for trigger in self.triggers.all():
            trigger.release()

        # release any starts
        for start in self.starts.all():
            start.release()

        self.group_dependencies.clear()
        self.flow_dependencies.clear()
        self.field_dependencies.clear()
        self.channel_dependencies.clear()
        self.label_dependencies.clear()
        self.classifier_dependencies.clear()
        self.ticketer_dependencies.clear()

        # queue mailroom to interrupt sessions where contact is currently in this flow
        mailroom.queue_interrupt(self.org, flow=self)

    def release_runs(self):
        """
        Exits all flow runs
        """
        # grab the ids of all our runs
        run_ids = self.runs.all().values_list("id", flat=True)

        # clear our association with any related sessions
        self.sessions.all().update(current_flow=None)

        # batch this for 1,000 runs at a time so we don't grab locks for too long
        for id_batch in chunk_list(run_ids, 1000):
            runs = FlowRun.objects.filter(id__in=id_batch)
            for run in runs:
                run.release()

    def __str__(self):
        return self.name

    class Meta:
        ordering = ("-modified_on",)


class FlowSession(models.Model):
    """
    A contact's session with the flow engine
    """

    STATUS_WAITING = "W"
    STATUS_COMPLETED = "C"
    STATUS_INTERRUPTED = "I"
    STATUS_EXPIRED = "X"
    STATUS_FAILED = "F"

    STATUS_CHOICES = (
        (STATUS_WAITING, "Waiting"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_INTERRUPTED, "Interrupted"),
        (STATUS_EXPIRED, "Expired"),
        (STATUS_FAILED, "Failed"),
    )

    uuid = models.UUIDField(unique=True)

    # the modality of this session
    session_type = models.CharField(max_length=1, choices=Flow.FLOW_TYPES, default=Flow.TYPE_MESSAGE, null=True)

    # the organization this session belongs to
    org = models.ForeignKey(Org, related_name="sessions", on_delete=models.PROTECT)

    # the contact that this session is with
    contact = models.ForeignKey("contacts.Contact", on_delete=models.PROTECT, related_name="sessions")

    # the channel connection used for flow sessions over IVR or USSD"),
    connection = models.OneToOneField(
        "channels.ChannelConnection", on_delete=models.PROTECT, null=True, related_name="session"
    )

    # the status of this session
    status = models.CharField(max_length=1, choices=STATUS_CHOICES, null=True)

    # whether the contact has responded in this session
    responded = models.BooleanField(default=False)

    # the goflow output of this session
    output = JSONAsTextField(null=True, default=dict)

    # when this session was created
    created_on = models.DateTimeField(default=timezone.now)

    # when this session ended
    ended_on = models.DateTimeField(null=True)

    # when this session's wait will time out (if at all)
    timeout_on = models.DateTimeField(null=True)

    # when this session started waiting (if at all)
    wait_started_on = models.DateTimeField(null=True)

    # the flow of the waiting run
    current_flow = models.ForeignKey("flows.Flow", related_name="sessions", null=True, on_delete=models.PROTECT)

    def release(self):
        self.delete()

    def __str__(self):  # pragma: no cover
        return str(self.contact)


class FlowRun(RequireUpdateFieldsMixin, models.Model):
    """
    A single contact's journey through a flow. It records the path taken, results collected, events generated etc.
    """

    STATUS_ACTIVE = "A"
    STATUS_WAITING = "W"
    STATUS_COMPLETED = "C"
    STATUS_INTERRUPTED = "I"
    STATUS_EXPIRED = "X"
    STATUS_FAILED = "F"
    STATUS_CHOICES = (
        (STATUS_ACTIVE, "Active"),
        (STATUS_WAITING, "Waiting"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_INTERRUPTED, "Interrupted"),
        (STATUS_EXPIRED, "Expired"),
        (STATUS_FAILED, "Failed"),
    )

    STATE_ACTIVE = "A"

    EXIT_TYPE_COMPLETED = "C"
    EXIT_TYPE_INTERRUPTED = "I"
    EXIT_TYPE_EXPIRED = "E"
    EXIT_TYPE_CHOICES = (
        (EXIT_TYPE_COMPLETED, _("Completed")),
        (EXIT_TYPE_INTERRUPTED, _("Interrupted")),
        (EXIT_TYPE_EXPIRED, _("Expired")),
    )

    RESULT_NAME = "name"
    RESULT_NODE_UUID = "node_uuid"
    RESULT_CATEGORY = "category"
    RESULT_CATEGORY_LOCALIZED = "category_localized"
    RESULT_VALUE = "value"
    RESULT_INPUT = "input"
    RESULT_CREATED_ON = "created_on"

    PATH_STEP_UUID = "uuid"
    PATH_NODE_UUID = "node_uuid"
    PATH_ARRIVED_ON = "arrived_on"
    PATH_EXIT_UUID = "exit_uuid"

    EVENT_TYPE = "type"
    EVENT_STEP_UUID = "step_uuid"
    EVENT_CREATED_ON = "created_on"

    DELETE_FOR_ARCHIVE = "A"
    DELETE_FOR_USER = "U"

    DELETE_CHOICES = ((DELETE_FOR_ARCHIVE, _("Archive delete")), (DELETE_FOR_USER, _("User delete")))

    uuid = models.UUIDField(unique=True, default=uuid4)

    org = models.ForeignKey(Org, on_delete=models.PROTECT, related_name="runs", db_index=False)

    flow = models.ForeignKey(Flow, on_delete=models.PROTECT, related_name="runs")

    contact = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="runs")

    # session this run belongs to (can be null if session has been trimmed)
    session = models.ForeignKey(FlowSession, on_delete=models.PROTECT, related_name="runs", null=True)

    # current status of this run
    status = models.CharField(max_length=1, choices=STATUS_CHOICES)

    # for an IVR session this is the connection to the IVR channel
    connection = models.ForeignKey(
        "channels.ChannelConnection", on_delete=models.PROTECT, related_name="runs", null=True
    )

    # when this run was created
    created_on = models.DateTimeField(default=timezone.now)

    # when this run was last modified
    modified_on = models.DateTimeField(default=timezone.now)

    # when this run ended
    exited_on = models.DateTimeField(null=True)

    # when this run will expire
    expires_on = models.DateTimeField(null=True)

    # next wait timeout in this run (if any)
    timeout_on = models.DateTimeField(null=True)

    # true if the contact has responded in this run
    responded = models.BooleanField(default=False)

    # flow start which started the session this run belongs to
    start = models.ForeignKey("flows.FlowStart", on_delete=models.PROTECT, null=True, related_name="runs")

    # if this run is part of a Surveyor session, the user that submitted it
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, db_index=False)

    # parent run that started this run (if any)
    parent = models.ForeignKey("flows.FlowRun", on_delete=models.PROTECT, null=True)

    # UUID of the parent run (if any)
    parent_uuid = models.UUIDField(null=True)

    # results collected in this run keyed by snakified result name
    results = JSONAsTextField(null=True, default=dict)

    # path taken by this run through the flow
    path = JSONAsTextField(null=True, default=list)

    # engine events generated by this run
    events = JSONField(null=True)

    # current node location of this run in the flow
    current_node_uuid = models.UUIDField(null=True)

    # if this run is scheduled for deletion, why
    delete_reason = models.CharField(null=True, max_length=1, choices=DELETE_CHOICES)

    # TODO to be replaced by new status field
    is_active = models.BooleanField(default=True)
    exit_type = models.CharField(null=True, max_length=1, choices=EXIT_TYPE_CHOICES)

    def get_events_of_type(self, event_types):
        """
        Gets all the events of the given type associated with this run
        """
        if not self.events:  # pragma: no cover
            return []

        type_names = [t.name for t in event_types]
        return [e for e in self.events if e[FlowRun.EVENT_TYPE] in type_names]

    def get_msg_events(self):
        """
        Gets all the messages associated with this run
        """
        return self.get_events_of_type((Events.msg_received, Events.msg_created))

    def get_events_by_step(self, msg_only=False):
        """
        Gets a map of step UUIDs to lists of events created at that step
        """
        events = self.get_msg_events() if msg_only else self.events
        events_by_step = defaultdict(list)
        for e in events:
            events_by_step[e[FlowRun.EVENT_STEP_UUID]].append(e)
        return events_by_step

    def get_messages(self):
        """
        Gets all the messages associated with this run
        """
        # need a data migration to go fix some old message events with uuid="None", until then filter them out
        msg_uuids = []
        for e in self.get_msg_events():
            msg_uuid = e["msg"].get("uuid")
            if msg_uuid and msg_uuid != "None":
                msg_uuids.append(msg_uuid)

        return Msg.objects.filter(uuid__in=msg_uuids)

    def release(self, delete_reason=None):
        """
        Permanently deletes this flow run
        """
        with transaction.atomic():
            if delete_reason:
                self.delete_reason = delete_reason
                self.save(update_fields=["delete_reason"])

            # clear any runs that reference us
            FlowRun.objects.filter(parent=self).update(parent=None)

            # and any recent runs
            for recent in FlowPathRecentRun.objects.filter(run=self):
                recent.release()

            if (
                delete_reason == FlowRun.DELETE_FOR_USER
                and self.session is not None
                and self.session.status == FlowSession.STATUS_WAITING
            ):
                mailroom.queue_interrupt(self.org, session=self.session)

            self.delete()

    def update_expiration(self, point_in_time):
        """
        Set our expiration according to the flow settings
        """
        if self.flow.expires_after_minutes:
            self.expires_on = point_in_time + timedelta(minutes=self.flow.expires_after_minutes)
            self.modified_on = timezone.now()

            # save our updated fields
            self.save(update_fields=["expires_on", "modified_on"])

        # parent should always have a later expiration than the children
        if self.parent:
            self.parent.update_expiration(self.expires_on)

    def as_archive_json(self):
        def convert_step(step):
            return {"node": step[FlowRun.PATH_NODE_UUID], "time": step[FlowRun.PATH_ARRIVED_ON]}

        def convert_result(result):
            return {
                "name": result.get(FlowRun.RESULT_NAME),
                "node": result.get(FlowRun.RESULT_NODE_UUID),
                "time": result[FlowRun.RESULT_CREATED_ON],
                "input": result.get(FlowRun.RESULT_INPUT),
                "value": result[FlowRun.RESULT_VALUE],
                "category": result.get(FlowRun.RESULT_CATEGORY),
            }

        return {
            "id": self.id,
            "uuid": str(self.uuid),
            "flow": {"uuid": str(self.flow.uuid), "name": self.flow.name},
            "contact": {"uuid": str(self.contact.uuid), "name": self.contact.name},
            "responded": self.responded,
            "path": [convert_step(s) for s in self.path],
            "values": {k: convert_result(r) for k, r in self.results.items()} if self.results else {},
            "events": self.events,
            "created_on": self.created_on.isoformat(),
            "modified_on": self.modified_on.isoformat(),
            "exited_on": self.exited_on.isoformat() if self.exited_on else None,
            "exit_type": self.exit_type,
            "submitted_by": self.submitted_by.username if self.submitted_by else None,
        }

    def __str__(self):  # pragma: no cover
        return f"FlowRun[uuid={self.uuid}, flow={self.flow.uuid}]"


class RuleSet(models.Model):
    TYPE_WAIT_MESSAGE = "wait_message"

    # Ussd
    TYPE_WAIT_USSD_MENU = "wait_menu"
    TYPE_WAIT_USSD = "wait_ussd"

    # Calls
    TYPE_WAIT_RECORDING = "wait_recording"
    TYPE_WAIT_DIGIT = "wait_digit"
    TYPE_WAIT_DIGITS = "wait_digits"

    # Surveys
    TYPE_WAIT_PHOTO = "wait_photo"
    TYPE_WAIT_VIDEO = "wait_video"
    TYPE_WAIT_AUDIO = "wait_audio"
    TYPE_WAIT_GPS = "wait_gps"

    TYPE_AIRTIME = "airtime"
    TYPE_WEBHOOK = "webhook"
    TYPE_RESTHOOK = "resthook"
    TYPE_FLOW_FIELD = "flow_field"
    TYPE_FORM_FIELD = "form_field"
    TYPE_CONTACT_FIELD = "contact_field"
    TYPE_EXPRESSION = "expression"
    TYPE_GROUP = "group"
    TYPE_RANDOM = "random"
    TYPE_SUBFLOW = "subflow"

    CONFIG_WEBHOOK = "webhook"
    CONFIG_WEBHOOK_ACTION = "webhook_action"
    CONFIG_WEBHOOK_HEADERS = "webhook_headers"
    CONFIG_RESTHOOK = "resthook"

    TYPE_MEDIA = (TYPE_WAIT_PHOTO, TYPE_WAIT_GPS, TYPE_WAIT_VIDEO, TYPE_WAIT_AUDIO, TYPE_WAIT_RECORDING)

    TYPE_WAIT = (
        TYPE_WAIT_MESSAGE,
        TYPE_WAIT_RECORDING,
        TYPE_WAIT_DIGIT,
        TYPE_WAIT_DIGITS,
        TYPE_WAIT_PHOTO,
        TYPE_WAIT_VIDEO,
        TYPE_WAIT_AUDIO,
        TYPE_WAIT_GPS,
    )

    TYPE_CHOICES = (
        (TYPE_WAIT_MESSAGE, "Wait for message"),
        (TYPE_WAIT_USSD_MENU, "Wait for USSD menu"),
        (TYPE_WAIT_USSD, "Wait for USSD message"),
        (TYPE_WAIT_RECORDING, "Wait for recording"),
        (TYPE_WAIT_DIGIT, "Wait for digit"),
        (TYPE_WAIT_DIGITS, "Wait for digits"),
        (TYPE_SUBFLOW, "Subflow"),
        (TYPE_WEBHOOK, "Webhook"),
        (TYPE_RESTHOOK, "Resthook"),
        (TYPE_AIRTIME, "Transfer Airtime"),
        (TYPE_FORM_FIELD, "Split by message form"),
        (TYPE_CONTACT_FIELD, "Split on contact field"),
        (TYPE_EXPRESSION, "Split by expression"),
        (TYPE_RANDOM, "Split Randomly"),
    )

    uuid = models.CharField(max_length=36, unique=True)

    flow = models.ForeignKey(Flow, on_delete=models.PROTECT, related_name="rule_sets", null=True)

    label = models.CharField(max_length=64, null=True, blank=True, help_text=_("The label for this field"))

    operand = models.CharField(
        max_length=128,
        null=True,
        blank=True,
        help_text=_("The value that rules will be run against, if None defaults to @step.value"),
    )

    webhook_url = models.URLField(
        null=True,
        blank=True,
        max_length=255,
        help_text=_("The URL that will be called with the user's response before we run our rules"),
    )

    webhook_action = models.CharField(
        null=True, blank=True, max_length=8, default="POST", help_text=_("How the webhook should be executed")
    )

    rules = JSONAsTextField(help_text=_("The JSON encoded actions for this action set"), default=list)

    finished_key = models.CharField(
        max_length=1, null=True, blank=True, help_text="During IVR, this is the key to indicate we are done waiting"
    )

    value_type = models.CharField(
        max_length=1,
        choices=Value.TYPE_CHOICES,
        default=Value.TYPE_TEXT,
        help_text="The type of value this ruleset saves",
    )

    ruleset_type = models.CharField(max_length=16, choices=TYPE_CHOICES, null=True, help_text="The type of ruleset")

    response_type = models.CharField(max_length=1, help_text="The type of response that is being saved")

    config = JSONAsTextField(
        null=True,
        verbose_name=_("Ruleset Configuration"),
        default=dict,
        help_text=_("RuleSet type specific configuration"),
    )

    x = models.IntegerField()
    y = models.IntegerField()

    created_on = models.DateTimeField(auto_now_add=True, help_text=_("When this ruleset was originally created"))
    modified_on = models.DateTimeField(auto_now=True, help_text=_("When this ruleset was last modified"))

    def get_value_type(self):
        """
        Determines the value type that this ruleset will generate.
        """
        # we keep track of specialized rule types we see
        value_type = None

        for rule in self.get_rules():
            if isinstance(rule.test, legacy.TrueTest):
                continue

            rule_type = None

            if isinstance(rule.test, legacy.NumericTest):
                rule_type = Value.TYPE_NUMBER

            elif isinstance(rule.test, legacy.DateTest):
                rule_type = Value.TYPE_DATETIME

            elif isinstance(rule.test, legacy.HasStateTest):
                rule_type = Value.TYPE_STATE

            elif isinstance(rule.test, legacy.HasDistrictTest):
                rule_type = Value.TYPE_DISTRICT

            elif isinstance(rule.test, legacy.HasWardTest):
                rule_type = Value.TYPE_WARD

            # this either isn't one of our value types or we have more than one type in this ruleset
            if not rule_type or (value_type and rule_type != value_type):
                return Value.TYPE_TEXT

            value_type = rule_type

        return value_type if value_type else Value.TYPE_TEXT

    def get_rules(self):
        return legacy.Rule.from_json_array(self.flow.org, self.rules)

    def as_json(self):
        return dict(
            uuid=self.uuid,
            x=self.x,
            y=self.y,
            label=self.label,
            rules=self.rules,
            finished_key=self.finished_key,
            ruleset_type=self.ruleset_type,
            response_type=self.response_type,
            operand=self.operand,
            config=self.config,
        )

    def __str__(self):  # pragma: no cover
        if self.label:
            return "RuleSet: %s - %s" % (self.uuid, self.label)
        else:
            return "RuleSet: %s" % (self.uuid,)


class ActionSet(models.Model):
    uuid = models.CharField(max_length=36, unique=True)
    flow = models.ForeignKey(Flow, on_delete=models.PROTECT, related_name="action_sets")

    destination = models.CharField(max_length=36, null=True)
    destination_type = models.CharField(max_length=1, null=True)

    exit_uuid = models.CharField(max_length=36, null=True)  # needed for migrating to new engine

    actions = JSONAsTextField(help_text=_("The JSON encoded actions for this action set"), default=dict)

    x = models.IntegerField()
    y = models.IntegerField()

    created_on = models.DateTimeField(auto_now_add=True, help_text=_("When this action was originally created"))
    modified_on = models.DateTimeField(auto_now=True, help_text=_("When this action was last modified"))

    def get_actions(self):
        return legacy.Action.from_json_array(self.flow.org, self.actions)

    def as_json(self):
        return dict(
            uuid=self.uuid,
            x=self.x,
            y=self.y,
            destination=self.destination,
            actions=self.actions,
            exit_uuid=self.exit_uuid,
        )

    def __str__(self):  # pragma: no cover
        return "ActionSet: %s" % (self.uuid,)


class FlowRevision(SmartModel):
    """
    JSON definitions for previous flow revisions
    """

    LAST_TRIM_KEY = "temba:last_flow_revision_trim"

    flow = models.ForeignKey(Flow, on_delete=models.PROTECT, related_name="revisions")

    definition = JSONAsTextField(help_text=_("The JSON flow definition"), default=dict)

    spec_version = models.CharField(
        default=Flow.FINAL_LEGACY_VERSION, max_length=8, help_text=_("The flow version this definition is in")
    )

    revision = models.IntegerField(null=True, help_text=_("Revision number for this definition"))

    @classmethod
    def trim(cls, since):
        """
        For any flow that has a new revision since the passed in date, trim revisions
        :param since: datetime of when to trim
        :return: The number of trimmed revisions
        """
        count = 0

        # find all flows with revisions since the passed in date
        for fr in FlowRevision.objects.filter(created_on__gt=since).distinct("flow_id").only("flow_id"):
            # trim that flow
            count += FlowRevision.trim_for_flow(fr.flow_id)

        return count

    @classmethod
    def trim_for_flow(cls, flow_id):
        """
        Trims the revisions for the passed in flow.

        Our logic is:
         * always keep last 25 revisions
         * for any revision beyond those, collapse to first revision for that day

        :param flow: the id of the flow to trim revisions for
        :return: the number of trimmed revisions
        """
        # find what date cutoff we will use for "25 most recent"
        cutoff = FlowRevision.objects.filter(flow=flow_id).order_by("-created_on")[24:25]

        # fewer than 25 revisions
        if not cutoff:
            return 0

        cutoff = cutoff[0].created_on

        # find the ids of the first revision for each day starting at the cutoff
        keepers = (
            FlowRevision.objects.filter(flow=flow_id, created_on__lt=cutoff)
            .annotate(created_date=TruncDate("created_on"))
            .values("created_date")
            .annotate(max_id=Max("id"))
            .values_list("max_id", flat=True)
        )

        # delete the rest
        return FlowRevision.objects.filter(flow=flow_id, created_on__lt=cutoff).exclude(id__in=keepers).delete()[0]

    @classmethod
    def is_legacy_definition(cls, definition):
        return Flow.DEFINITION_SPEC_VERSION not in definition

    @classmethod
    def validate_legacy_definition(cls, definition):

        if definition[Flow.FLOW_TYPE] not in (Flow.TYPE_MESSAGE, Flow.TYPE_VOICE, Flow.TYPE_SURVEY, "F"):
            raise ValueError(_("Unsupported flow type"))

        non_localized_error = _("Malformed flow, encountered non-localized definition")

        # should always have a base_language
        if Flow.BASE_LANGUAGE not in definition or not definition[Flow.BASE_LANGUAGE]:
            raise ValueError(non_localized_error)

        # language should match values in definition
        base_language = definition[Flow.BASE_LANGUAGE]

        def validate_localization(lang_dict):

            # must be a dict
            if not isinstance(lang_dict, dict):
                raise ValueError(non_localized_error)

            # and contain the base_language
            if base_language not in lang_dict:  # pragma: needs cover
                raise ValueError(non_localized_error)

        for actionset in definition[Flow.ACTION_SETS]:
            for action in actionset["actions"]:
                if "msg" in action and action["type"] != "email":
                    validate_localization(action["msg"])

        for ruleset in definition[Flow.RULE_SETS]:
            for rule in ruleset["rules"]:
                validate_localization(rule["category"])

    @classmethod
    def migrate_export(cls, org, exported_json, same_site, version, legacy=False):
        # use legacy migrations to get export to final legacy version
        if version < Version(Flow.FINAL_LEGACY_VERSION):
            from temba.flows.legacy import exports

            exported_json = exports.migrate(org, exported_json, same_site, version)

        if legacy:
            return exported_json

        # use mailroom to get export to current spec version
        migrated_flows = []
        for flow_def in exported_json[Org.EXPORT_FLOWS]:
            migrated_flows.append(mailroom.get_client().flow_migrate(flow_def))

        exported_json[Org.EXPORT_FLOWS] = migrated_flows

        return exported_json

    @classmethod
    def migrate_definition(cls, json_flow, flow, to_version=Flow.CURRENT_SPEC_VERSION):
        from temba.flows.legacy import migrations

        # migrate any legacy versions forward
        if Flow.VERSION in json_flow:
            versions = legacy.get_versions_after(json_flow[Flow.VERSION])
            for version in versions:
                version_slug = version.replace(".", "_")
                migrate_fn = getattr(migrations, "migrate_to_version_%s" % version_slug, None)

                if migrate_fn:
                    json_flow = migrate_fn(json_flow, flow)
                    json_flow[Flow.VERSION] = version

                if version == to_version:
                    break

        # migrate using goflow for anything newer
        if Version(to_version) >= Version(Flow.INITIAL_GOFLOW_VERSION):
            json_flow = mailroom.get_client().flow_migrate(json_flow, to_version)

        return json_flow

    def get_definition_json(self, to_version=Flow.CURRENT_SPEC_VERSION):
        definition = self.definition

        # if it's previous to version 6, wrap the definition to
        # mirror our exports for those versions
        if Version(self.spec_version) < Version("6"):
            definition = dict(
                definition=self.definition,
                flow_type=self.flow.flow_type,
                expires=self.flow.expires_after_minutes,
                id=self.flow.pk,
                revision=self.revision,
                uuid=self.flow.uuid,
            )

        # make sure old revisions migrate properly
        if Version(self.spec_version) <= Version(Flow.FINAL_LEGACY_VERSION):
            definition[Flow.VERSION] = self.spec_version

        # migrate our definition if necessary
        if self.spec_version != to_version:
            if Flow.METADATA not in definition:
                definition[Flow.METADATA] = {}

            definition[Flow.METADATA][Flow.METADATA_REVISION] = self.revision
            definition = FlowRevision.migrate_definition(definition, self.flow, to_version)

        # update variables from our db into our revision
        flow = self.flow
        definition[Flow.DEFINITION_NAME] = flow.name
        definition[Flow.DEFINITION_UUID] = flow.uuid
        definition[Flow.DEFINITION_REVISION] = self.revision
        definition[Flow.DEFINITION_EXPIRE_AFTER_MINUTES] = flow.expires_after_minutes

        return definition

    def as_json(self):
        name = self.created_by.get_full_name()
        return dict(
            user=dict(email=self.created_by.email, name=name),
            created_on=json.encode_datetime(self.created_on, micros=True),
            id=self.pk,
            version=self.spec_version,
            revision=self.revision,
        )

    def release(self):
        self.delete()


class FlowCategoryCount(SquashableModel):
    """
    Maintains counts for categories across all possible results in a flow
    """

    SQUASH_OVER = ("flow_id", "node_uuid", "result_key", "result_name", "category_name")

    flow = models.ForeignKey(Flow, on_delete=models.PROTECT, related_name="category_counts")

    # the UUID of the node where this result was created
    node_uuid = models.UUIDField(db_index=True)

    # the key and name of the result in the flow
    result_key = models.CharField(max_length=128)
    result_name = models.CharField(max_length=128)

    # the name of the category
    category_name = models.CharField(max_length=128)

    # the number of results with this category
    count = models.IntegerField(default=0)

    @classmethod
    def get_squash_query(cls, distinct_set):
        sql = """
        WITH removed as (
          DELETE FROM %(table)s WHERE "id" IN (
            SELECT "id" FROM %(table)s
              WHERE "flow_id" = %%s AND "node_uuid" = %%s AND "result_key" = %%s AND "result_name" = %%s AND "category_name" = %%s
              LIMIT 10000
          ) RETURNING "count"
        )
        INSERT INTO %(table)s("flow_id", "node_uuid", "result_key", "result_name", "category_name", "count", "is_squashed")
        VALUES (%%s, %%s, %%s, %%s, %%s, GREATEST(0, (SELECT SUM("count") FROM removed)), TRUE);
        """ % {
            "table": cls._meta.db_table
        }

        params = (
            distinct_set.flow_id,
            distinct_set.node_uuid,
            distinct_set.result_key,
            distinct_set.result_name,
            distinct_set.category_name,
        ) * 2
        return sql, params

    def __str__(self):
        return "%s: %s" % (self.category_name, self.count)


class FlowPathCount(SquashableModel):
    """
    Maintains hourly counts of flow paths
    """

    SQUASH_OVER = ("flow_id", "from_uuid", "to_uuid", "period")

    flow = models.ForeignKey(Flow, on_delete=models.PROTECT, related_name="path_counts")

    # the exit UUID of the node this path segment starts with
    from_uuid = models.UUIDField()

    # the UUID of the node this path segment ends with
    to_uuid = models.UUIDField()

    # the hour in which this activity occurred
    period = models.DateTimeField()

    # the number of runs that tooks this path segment in that period
    count = models.IntegerField(default=0)

    @classmethod
    def get_squash_query(cls, distinct_set):
        sql = """
        WITH removed as (
            DELETE FROM %(table)s WHERE "flow_id" = %%s AND "from_uuid" = %%s AND "to_uuid" = %%s AND "period" = date_trunc('hour', %%s) RETURNING "count"
        )
        INSERT INTO %(table)s("flow_id", "from_uuid", "to_uuid", "period", "count", "is_squashed")
        VALUES (%%s, %%s, %%s, date_trunc('hour', %%s), GREATEST(0, (SELECT SUM("count") FROM removed)), TRUE);
        """ % {
            "table": cls._meta.db_table
        }

        params = (distinct_set.flow_id, distinct_set.from_uuid, distinct_set.to_uuid, distinct_set.period) * 2
        return sql, params

    @classmethod
    def get_totals(cls, flow):
        counts = cls.objects.filter(flow=flow)
        totals = list(counts.values_list("from_uuid", "to_uuid").annotate(replies=Sum("count")))
        return {"%s:%s" % (t[0], t[1]): t[2] for t in totals}

    def __str__(self):  # pragma: no cover
        return f"FlowPathCount({self.flow_id}) {self.from_uuid}:{self.to_uuid} {self.period} count: {self.count}"

    class Meta:
        index_together = ["flow", "from_uuid", "to_uuid", "period"]


class FlowPathRecentRun(models.Model):
    """
    Maintains recent runs for a flow path segment
    """

    PRUNE_TO = 5
    LAST_PRUNED_KEY = "last_recentrun_pruned"

    id = models.BigAutoField(auto_created=True, primary_key=True, verbose_name="ID")

    # the node and step UUIDs of the start of the path segment
    from_uuid = models.UUIDField()
    from_step_uuid = models.UUIDField()

    # the node and step UUIDs of the end of the path segment
    to_uuid = models.UUIDField()
    to_step_uuid = models.UUIDField()

    run = models.ForeignKey(FlowRun, on_delete=models.PROTECT, related_name="recent_runs")

    # when the run visited this path segment
    visited_on = models.DateTimeField(default=timezone.now)

    def release(self):
        self.delete()

    @classmethod
    def get_recent(cls, exit_uuids, to_uuid, limit=PRUNE_TO):
        """
        Gets the recent runs for the given flow segments
        """
        recent = (
            cls.objects.filter(from_uuid__in=exit_uuids, to_uuid=to_uuid).select_related("run").order_by("-visited_on")
        )
        if limit:
            recent = recent[:limit]

        results = []
        for r in recent:
            msg_events_by_step = r.run.get_events_by_step(msg_only=True)
            msg_event = None

            # find the last message event in the run before this step
            before_step = False
            for step in reversed(r.run.path):
                step_uuid = step[FlowRun.PATH_STEP_UUID]
                if step_uuid == str(r.from_step_uuid):
                    before_step = True

                if before_step:
                    msg_events = msg_events_by_step[step_uuid]
                    if msg_events:
                        msg_event = msg_events[-1]
                        break

            if msg_event:
                results.append({"run": r.run, "text": msg_event["msg"]["text"], "visited_on": r.visited_on})

        return results

    @classmethod
    def prune(cls):
        """
        Removes old recent run records leaving only PRUNE_TO most recent for each segment
        """
        last_id = cache.get(cls.LAST_PRUNED_KEY, -1)

        newest = cls.objects.order_by("-id").values("id").first()
        newest_id = newest["id"] if newest else -1

        sql = """
            DELETE FROM %(table)s WHERE id IN (
              SELECT id FROM (
                  SELECT
                    r.id,
                    dense_rank() OVER (PARTITION BY from_uuid, to_uuid ORDER BY visited_on DESC) AS pos
                  FROM %(table)s r
                  WHERE (from_uuid, to_uuid) IN (
                    -- get the unique segments added to since last prune
                    SELECT DISTINCT from_uuid, to_uuid FROM %(table)s WHERE id > %(last_id)d
                  )
              ) s WHERE s.pos > %(limit)d
            )""" % {
            "table": cls._meta.db_table,
            "last_id": last_id,
            "limit": cls.PRUNE_TO,
        }

        cursor = db_connection.cursor()
        cursor.execute(sql)

        cache.set(cls.LAST_PRUNED_KEY, newest_id)

        return cursor.rowcount  # number of deleted entries

    def __str__(self):  # pragma: no cover
        return f"run={self.run.uuid} flow={self.run.flow.uuid} segment={self.to_uuid}→{self.from_uuid}"

    class Meta:
        indexes = [models.Index(fields=["from_uuid", "to_uuid", "-visited_on"])]


class FlowNodeCount(SquashableModel):
    """
    Maintains counts of unique contacts at each flow node.
    """

    SQUASH_OVER = ("node_uuid",)

    flow = models.ForeignKey(Flow, on_delete=models.PROTECT, related_name="node_counts")

    # the UUID of the node
    node_uuid = models.UUIDField(db_index=True)

    # the number of contacts/runs currently at that node
    count = models.IntegerField(default=0)

    @classmethod
    def get_squash_query(cls, distinct_set):
        sql = """
        WITH removed as (
            DELETE FROM %(table)s WHERE "node_uuid" = %%s RETURNING "count"
        )
        INSERT INTO %(table)s("flow_id", "node_uuid", "count", "is_squashed")
        VALUES (%%s, %%s, GREATEST(0, (SELECT SUM("count") FROM removed)), TRUE);
        """ % {
            "table": cls._meta.db_table
        }

        return sql, (distinct_set.node_uuid, distinct_set.flow_id, distinct_set.node_uuid)

    @classmethod
    def get_totals(cls, flow):
        totals = list(cls.objects.filter(flow=flow).values_list("node_uuid").annotate(replies=Sum("count")))
        return {str(t[0]): t[1] for t in totals if t[1]}


class FlowRunCount(SquashableModel):
    """
    Maintains counts of different states of exit types of flow runs on a flow. These are calculated
    via triggers on the database.
    """

    SQUASH_OVER = ("flow_id", "exit_type")

    flow = models.ForeignKey(Flow, on_delete=models.PROTECT, related_name="exit_counts")

    # the type of exit
    exit_type = models.CharField(null=True, max_length=1, choices=FlowRun.EXIT_TYPE_CHOICES)

    # the number of runs that exited with that exit type
    count = models.IntegerField(default=0)

    @classmethod
    def get_squash_query(cls, distinct_set):
        if distinct_set.exit_type:
            sql = """
            WITH removed as (
                DELETE FROM %(table)s WHERE "flow_id" = %%s AND "exit_type" = %%s RETURNING "count"
            )
            INSERT INTO %(table)s("flow_id", "exit_type", "count", "is_squashed")
            VALUES (%%s, %%s, GREATEST(0, (SELECT SUM("count") FROM removed)), TRUE);
            """ % {
                "table": cls._meta.db_table
            }

            params = (distinct_set.flow_id, distinct_set.exit_type) * 2
        else:
            sql = """
            WITH removed as (
                DELETE FROM %(table)s WHERE "flow_id" = %%s AND "exit_type" IS NULL RETURNING "count"
            )
            INSERT INTO %(table)s("flow_id", "exit_type", "count", "is_squashed")
            VALUES (%%s, NULL, GREATEST(0, (SELECT SUM("count") FROM removed)), TRUE);
            """ % {
                "table": cls._meta.db_table
            }

            params = (distinct_set.flow_id,) * 2

        return sql, params

    @classmethod
    def get_totals(cls, flow):
        totals = list(cls.objects.filter(flow=flow).values_list("exit_type").annotate(replies=Sum("count")))
        totals = {t[0]: t[1] for t in totals}

        # for convenience, ensure dict contains all possible states
        all_states = (None, FlowRun.EXIT_TYPE_COMPLETED, FlowRun.EXIT_TYPE_EXPIRED, FlowRun.EXIT_TYPE_INTERRUPTED)
        totals = {s: totals.get(s, 0) for s in all_states}

        # we record active runs as exit_type=None but replace with actual constant for clarity
        totals[FlowRun.STATE_ACTIVE] = totals[None]
        del totals[None]

        return totals

    def __str__(self):  # pragma: needs cover
        return "RunCount[%d:%s:%d]" % (self.flow_id, self.exit_type, self.count)

    class Meta:
        index_together = ("flow", "exit_type")


class ExportFlowResultsTask(BaseExportTask):
    """
    Container for managing our export requests
    """

    analytics_key = "flowresult_export"
    email_subject = "Your results export from %s is ready"
    email_template = "flows/email/flow_export_download"

    INCLUDE_MSGS = "include_msgs"
    CONTACT_FIELDS = "contact_fields"
    GROUP_MEMBERSHIPS = "group_memberships"
    RESPONDED_ONLY = "responded_only"
    EXTRA_URNS = "extra_urns"
    FLOWS = "flows"

    MAX_GROUP_MEMBERSHIPS_COLS = 25
    MAX_CONTACT_FIELDS_COLS = 10

    flows = models.ManyToManyField(Flow, related_name="exports", help_text=_("The flows to export"))

    config = JSONAsTextField(null=True, default=dict, help_text=_("Any configuration options for this flow export"))

    @classmethod
    def create(cls, org, user, flows, contact_fields, responded_only, include_msgs, extra_urns, group_memberships):
        config = {
            ExportFlowResultsTask.INCLUDE_MSGS: include_msgs,
            ExportFlowResultsTask.CONTACT_FIELDS: [c.id for c in contact_fields],
            ExportFlowResultsTask.RESPONDED_ONLY: responded_only,
            ExportFlowResultsTask.EXTRA_URNS: extra_urns,
            ExportFlowResultsTask.GROUP_MEMBERSHIPS: [g.id for g in group_memberships],
        }

        export = cls.objects.create(org=org, created_by=user, modified_by=user, config=config)
        for flow in flows:
            export.flows.add(flow)

        return export

    def get_email_context(self, branding):
        context = super().get_email_context(branding)
        context["flows"] = self.flows.all()
        return context

    def _get_runs_columns(self, extra_urn_columns, groups, contact_fields, result_fields, show_submitted_by=False):
        columns = []

        if show_submitted_by:
            columns.append("Submitted By")

        columns.append("Contact UUID")
        columns.append("ID" if self.org.is_anon else "URN")

        for extra_urn in extra_urn_columns:
            columns.append(extra_urn["label"])

        columns.append("Name")

        for gr in groups:
            columns.append("Group:%s" % gr.name)

        for cf in contact_fields:
            columns.append("Field:%s" % cf.label)

        columns.append("Started")
        columns.append("Modified")
        columns.append("Exited")
        columns.append("Run UUID")

        for result_field in result_fields:
            field_name, flow_name = result_field["name"], result_field["flow_name"]
            columns.append(f"{field_name} (Category) - {flow_name}")
            columns.append(f"{field_name} (Value) - {flow_name}")
            columns.append(f"{field_name} (Text) - {flow_name}")

        return columns

    def _add_runs_sheet(self, book, columns):
        name = "Runs (%d)" % (book.num_runs_sheets + 1) if book.num_runs_sheets > 0 else "Runs"
        sheet = book.add_sheet(name, index=book.num_runs_sheets)
        book.num_runs_sheets += 1

        self.append_row(sheet, columns)
        return sheet

    def _add_msgs_sheet(self, book):
        name = "Messages (%d)" % (book.num_msgs_sheets + 1) if book.num_msgs_sheets > 0 else "Messages"
        index = book.num_runs_sheets + book.num_msgs_sheets
        sheet = book.add_sheet(name, index)
        book.num_msgs_sheets += 1

        headers = ["Contact UUID", "URN", "Name", "Date", "Direction", "Message", "Attachments", "Channel"]

        self.append_row(sheet, headers)
        return sheet

    def write_export(self):
        config = self.config
        include_msgs = config.get(ExportFlowResultsTask.INCLUDE_MSGS, False)
        responded_only = config.get(ExportFlowResultsTask.RESPONDED_ONLY, True)
        contact_field_ids = config.get(ExportFlowResultsTask.CONTACT_FIELDS, [])
        extra_urns = config.get(ExportFlowResultsTask.EXTRA_URNS, [])
        group_memberships = config.get(ExportFlowResultsTask.GROUP_MEMBERSHIPS, [])

        contact_fields = ContactField.user_fields.active_for_org(org=self.org).filter(id__in=contact_field_ids)

        groups = ContactGroup.user_groups.filter(
            org=self.org, id__in=group_memberships, status=ContactGroup.STATUS_READY, is_active=True
        )

        # get all result saving nodes across all flows being exported
        show_submitted_by = False
        result_fields = []
        flows = list(self.flows.filter(is_active=True))
        for flow in flows:
            for result_field in flow.metadata["results"]:
                if not result_field["name"].startswith("_"):
                    result_field = result_field.copy()
                    result_field["flow_uuid"] = flow.uuid
                    result_field["flow_name"] = flow.name
                    result_fields.append(result_field)

            if flow.flow_type == Flow.TYPE_SURVEY:
                show_submitted_by = True

        extra_urn_columns = []
        if not self.org.is_anon:
            for extra_urn in extra_urns:
                label = f"URN:{extra_urn.capitalize()}"
                extra_urn_columns.append(dict(label=label, scheme=extra_urn))

        runs_columns = self._get_runs_columns(
            extra_urn_columns, groups, contact_fields, result_fields, show_submitted_by=show_submitted_by
        )

        book = XLSXBook()
        book.num_runs_sheets = 0
        book.num_msgs_sheets = 0

        # the current sheets
        book.current_runs_sheet = self._add_runs_sheet(book, runs_columns)
        book.current_msgs_sheet = None

        # for tracking performance
        total_runs_exported = 0
        temp_runs_exported = 0
        start = time.time()

        for batch in self._get_run_batches(flows, responded_only):
            self._write_runs(
                book,
                batch,
                include_msgs,
                extra_urn_columns,
                groups,
                contact_fields,
                show_submitted_by,
                runs_columns,
                result_fields,
            )

            total_runs_exported += len(batch)

            if (total_runs_exported - temp_runs_exported) > ExportFlowResultsTask.LOG_PROGRESS_PER_ROWS:
                mins = (time.time() - start) / 60
                logger.info(
                    f"Results export #{self.id} for org #{self.org.id}: exported {total_runs_exported} in {mins:.1f} mins"
                )

                temp_runs_exported = total_runs_exported

                self.modified_on = timezone.now()
                self.save(update_fields=["modified_on"])

        temp = NamedTemporaryFile(delete=True)
        book.finalize(to_file=temp)
        temp.flush()
        return temp, "xlsx"

    def _get_run_batches(self, flows, responded_only):
        logger.info(f"Results export #{self.id} for org #{self.org.id}: fetching runs from archives to export...")

        # firstly get runs from archives
        from temba.archives.models import Archive

        # get the earliest created date of the flows being exported
        earliest_created_on = None
        for flow in flows:
            if earliest_created_on is None or flow.created_on < earliest_created_on:
                earliest_created_on = flow.created_on

        records = Archive.iter_all_records(self.org, Archive.TYPE_FLOWRUN, after=earliest_created_on)
        flow_uuids = {str(flow.uuid) for flow in flows}
        seen = set()

        for record_batch in chunk_list(records, 1000):
            matching = []
            for record in record_batch:
                if record["flow"]["uuid"] in flow_uuids and (not responded_only or record["responded"]):
                    seen.add(record["id"])
                    matching.append(record)

            yield matching

        # secondly get runs from database
        runs = FlowRun.objects.filter(flow__in=flows).order_by("modified_on")
        if responded_only:
            runs = runs.filter(responded=True)
        run_ids = array(str("l"), runs.values_list("id", flat=True))

        logger.info(
            f"Results export #{self.id} for org #{self.org.id}: found {len(run_ids)} runs in database to export"
        )

        for id_batch in chunk_list(run_ids, 1000):
            run_batch = (
                FlowRun.objects.filter(id__in=id_batch).select_related("contact", "flow").order_by("modified_on", "pk")
            )

            # convert this batch of runs to same format as records in our archives
            yield [run.as_archive_json() for run in run_batch if run.id not in seen]

    def _write_runs(
        self,
        book,
        runs,
        include_msgs,
        extra_urn_columns,
        groups,
        contact_fields,
        show_submitted_by,
        runs_columns,
        result_fields,
    ):
        """
        Writes a batch of run JSON blobs to the export
        """
        # get all the contacts referenced in this batch
        contact_uuids = {r["contact"]["uuid"] for r in runs}
        contacts = Contact.objects.filter(org=self.org, uuid__in=contact_uuids).prefetch_related("all_groups")
        contacts_by_uuid = {str(c.uuid): c for c in contacts}

        for run in runs:
            contact = contacts_by_uuid.get(run["contact"]["uuid"])

            # get this run's results by node name(ruleset label)
            run_values = run["values"]
            if isinstance(run_values, list):
                results_by_key = {key: result for item in run_values for key, result in item.items()}
            else:
                results_by_key = {key: result for key, result in run_values.items()}

            # generate contact info columns
            contact_values = [
                contact.uuid,
                f"{contact.id:010d}" if self.org.is_anon else contact.get_urn_display(org=self.org, formatted=False),
            ]

            for extra_urn_column in extra_urn_columns:
                urn_display = contact.get_urn_display(org=self.org, formatted=False, scheme=extra_urn_column["scheme"])
                contact_values.append(urn_display)

            contact_values.append(self.prepare_value(contact.name))
            contact_groups_ids = [g.id for g in contact.all_groups.all()]
            for gr in groups:
                contact_values.append(gr.id in contact_groups_ids)

            for cf in contact_fields:
                field_value = contact.get_field_display(cf)
                contact_values.append(self.prepare_value(field_value))

            # generate result columns for each ruleset
            result_values = []
            for n, result_field in enumerate(result_fields):
                node_result = {}
                # check the result by ruleset label if the flow is the same
                if result_field["flow_uuid"] == run["flow"]["uuid"]:
                    node_result = results_by_key.get(result_field["key"], {})
                node_category = node_result.get("category", "")
                node_value = node_result.get("value", "")
                node_input = node_result.get("input", "")
                result_values += [node_category, node_value, node_input]

            if book.current_runs_sheet.num_rows >= self.MAX_EXCEL_ROWS:  # pragma: no cover
                book.current_runs_sheet = self._add_runs_sheet(book, runs_columns)

            # build the whole row
            runs_sheet_row = []

            if show_submitted_by:
                runs_sheet_row.append(run.get("submitted_by") or "")

            runs_sheet_row += contact_values
            runs_sheet_row += [
                iso8601.parse_date(run["created_on"]),
                iso8601.parse_date(run["modified_on"]),
                iso8601.parse_date(run["exited_on"]) if run["exited_on"] else None,
                run["uuid"],
            ]
            runs_sheet_row += result_values

            self.append_row(book.current_runs_sheet, runs_sheet_row)

            # write out any message associated with this run
            if include_msgs and not self.org.is_anon:
                self._write_run_messages(book, run, contact)

    def _write_run_messages(self, book, run, contact):
        """
        Writes out any messages associated with the given run
        """
        for event in run["events"] or []:
            if event["type"] == Events.msg_received.name:
                msg_direction = "IN"
            elif event["type"] == Events.msg_created.name:
                msg_direction = "OUT"
            else:  # pragma: no cover
                continue

            msg = event["msg"]
            msg_text = msg.get("text", "")
            msg_created_on = iso8601.parse_date(event["created_on"])
            msg_channel = msg.get("channel")
            msg_attachments = [attachment.url for attachment in Attachment.parse_all(msg.get("attachments", []))]

            if "urn" in msg:
                msg_urn = URN.format(msg["urn"], formatted=False)
            else:
                msg_urn = ""

            if not book.current_msgs_sheet or book.current_msgs_sheet.num_rows >= self.MAX_EXCEL_ROWS:
                book.current_msgs_sheet = self._add_msgs_sheet(book)

            self.append_row(
                book.current_msgs_sheet,
                [
                    str(contact.uuid),
                    msg_urn,
                    self.prepare_value(contact.name),
                    msg_created_on,
                    msg_direction,
                    msg_text,
                    ", ".join(msg_attachments),
                    msg_channel["name"] if msg_channel else "",
                ],
            )


@register_asset_store
class ResultsExportAssetStore(BaseExportAssetStore):
    model = ExportFlowResultsTask
    key = "results_export"
    directory = "results_exports"
    permission = "flows.flow_export_results"
    extensions = ("xlsx",)


class FlowStart(models.Model):
    STATUS_PENDING = "P"
    STATUS_STARTING = "S"
    STATUS_COMPLETE = "C"
    STATUS_FAILED = "F"

    STATUS_CHOICES = (
        (STATUS_PENDING, _("Pending")),
        (STATUS_STARTING, _("Starting")),
        (STATUS_COMPLETE, _("Complete")),
        (STATUS_FAILED, _("Failed")),
    )

    TYPE_MANUAL = "M"
    TYPE_API = "A"
    TYPE_API_ZAPIER = "Z"
    TYPE_FLOW_ACTION = "F"
    TYPE_TRIGGER = "T"

    TYPE_CHOICES = (
        (TYPE_MANUAL, "Manual"),
        (TYPE_API, "API"),
        (TYPE_API_ZAPIER, "Zapier"),
        (TYPE_FLOW_ACTION, "Flow Action"),
        (TYPE_TRIGGER, "Trigger"),
    )

    # the uuid of this start
    uuid = models.UUIDField(unique=True, default=uuid4)

    # the org the flow belongs to
    org = models.ForeignKey(Org, on_delete=models.PROTECT, related_name="flow_starts")

    # the flow that should be started
    flow = models.ForeignKey(Flow, on_delete=models.PROTECT, related_name="starts")

    # the type of start
    start_type = models.CharField(max_length=1, choices=TYPE_CHOICES)

    # the groups that should be considered for start in this flow
    groups = models.ManyToManyField(ContactGroup)

    # the individual contacts that should be considered for start in this flow
    contacts = models.ManyToManyField(Contact)

    # the query (if any) that should be used to select contacts to start
    query = models.TextField(null=True)

    # whether to restart contacts that have already participated in this flow
    restart_participants = models.BooleanField(default=True)

    # whether to start contacts in this flow that are active in other flows
    include_active = models.BooleanField(default=True)

    # the campaign event that started this flow start (if any)
    campaign_event = models.ForeignKey(
        "campaigns.CampaignEvent", null=True, on_delete=models.PROTECT, related_name="flow_starts"
    )

    # any channel connections associated with this flow start
    connections = models.ManyToManyField(ChannelConnection, related_name="starts")

    # the current status of this flow start
    status = models.CharField(max_length=1, default=STATUS_PENDING, choices=STATUS_CHOICES)

    # any extra parameters that should be passed as trigger params for this flow start
    extra = JSONAsTextField(null=True, default=dict)

    # the parent run's summary if there is one
    parent_summary = JSONField(null=True)

    # the session history if there is some
    session_history = JSONField(null=True)

    # who created this flow start
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT, related_name="flow_starts"
    )

    # when this flow start was created
    created_on = models.DateTimeField(default=timezone.now, editable=False)

    # when this flow start was last modified
    modified_on = models.DateTimeField(default=timezone.now, editable=False)

    # the number of de-duped contacts that might be started, depending on options above
    contact_count = models.IntegerField(default=0, null=True)

    @classmethod
    def create(
        cls,
        flow,
        user,
        start_type=TYPE_MANUAL,
        groups=None,
        contacts=None,
        query=None,
        restart_participants=True,
        extra=None,
        include_active=True,
        campaign_event=None,
    ):
        if contacts is None:  # pragma: needs cover
            contacts = []

        if groups is None:  # pragma: needs cover
            groups = []

        start = FlowStart.objects.create(
            org=flow.org,
            flow=flow,
            start_type=start_type,
            restart_participants=restart_participants,
            include_active=include_active,
            campaign_event=campaign_event,
            query=query,
            extra=extra,
            created_by=user,
        )

        for contact in contacts:
            start.contacts.add(contact)

        for group in groups:
            start.groups.add(group)

        return start

    def async_start(self):
        on_transaction_commit(lambda: mailroom.queue_flow_start(self))

    def release(self):
        with transaction.atomic():
            self.groups.clear()
            self.contacts.clear()
            self.connections.clear()
            FlowRun.objects.filter(start=self).update(start=None)
            FlowStartCount.objects.filter(start=self).delete()
            self.delete()

    def __str__(self):  # pragma: no cover
        return f"FlowStart[id={self.id}, flow={self.flow.uuid}]"

    class Meta:
        indexes = [
            # used for the flow start log page
            models.Index(
                name="flows_flowstarts_org_created",
                fields=["org", "-created_on"],
                condition=Q(created_by__isnull=False),
            ),
            # used by the flow_starts API endpoint
            models.Index(
                name="flows_flowstarts_org_modified",
                fields=["org", "-modified_on"],
                condition=Q(created_by__isnull=False),
            ),
        ]


class FlowStartCount(SquashableModel):
    """
    Maintains count of how many runs a FlowStart has created.
    """

    SQUASH_OVER = ("start_id",)

    start = models.ForeignKey(FlowStart, on_delete=models.PROTECT, related_name="counts", db_index=True)
    count = models.IntegerField(default=0)

    @classmethod
    def get_squash_query(cls, distinct_set):
        sql = """
        WITH deleted as (
            DELETE FROM %(table)s WHERE "start_id" = %%s RETURNING "count"
        )
        INSERT INTO %(table)s("start_id", "count", "is_squashed")
        VALUES (%%s, GREATEST(0, (SELECT SUM("count") FROM deleted)), TRUE);
        """ % {
            "table": cls._meta.db_table
        }

        return sql, (distinct_set.start_id,) * 2

    @classmethod
    def get_count(cls, start):
        count = start.counts.aggregate(count_sum=Sum("count"))["count_sum"]
        return count if count else 0

    @classmethod
    def bulk_annotate(cls, starts):
        counts = (
            cls.objects.filter(start_id__in=[s.id for s in starts])
            .values("start_id")
            .order_by("start_id")
            .annotate(count=Sum("count"))
        )
        counts_by_start = {c["start_id"]: c["count"] for c in counts}

        for start in starts:
            start.run_count = counts_by_start.get(start.id, 0)

    def __str__(self):  # pragma: needs cover
        return f"FlowStartCount[start={self.start_id}, count={self.count}]"


class FlowLabel(models.Model):
    """
    A label applied to a flow rather than a message
    """

    org = models.ForeignKey(Org, on_delete=models.PROTECT, related_name="flow_labels")

    uuid = models.CharField(max_length=36, unique=True, db_index=True, default=generate_uuid)

    name = models.CharField(max_length=64, verbose_name=_("Name"), help_text=_("The name of this flow label"))

    parent = models.ForeignKey(
        "FlowLabel", on_delete=models.PROTECT, verbose_name=_("Parent"), null=True, related_name="children"
    )

    @classmethod
    def create(cls, org, base, parent=None):

        base = base.strip()

        # truncate if necessary
        if len(base) > 32:
            base = base[:32]

        # find the next available label by appending numbers
        count = 2
        while FlowLabel.objects.filter(org=org, name=base, parent=parent):
            # make room for the number
            if len(base) >= 32:
                base = base[:30]
            last = str(count - 1)
            if base.endswith(last):
                base = base[: -len(last)]
            base = "%s %d" % (base.strip(), count)
            count += 1

        return FlowLabel.objects.create(org=org, name=base, parent=parent)

    def get_flows_count(self):
        """
        Returns the count of flows tagged with this label or one of its children
        """
        return self.get_flows().count()

    def get_flows(self):
        return (
            Flow.objects.filter(Q(labels=self) | Q(labels__parent=self))
            .filter(is_active=True, is_archived=False)
            .distinct()
        )

    def toggle_label(self, flows, add):
        changed = []

        for flow in flows:
            # if we are adding the flow label and this flow doesnt have it, add it
            if add:
                if not flow.labels.filter(pk=self.pk):
                    flow.labels.add(self)
                    changed.append(flow.pk)

            # otherwise, remove it if not already present
            else:
                if flow.labels.filter(pk=self.pk):
                    flow.labels.remove(self)
                    changed.append(flow.pk)

        return changed

    def __str__(self):
        if self.parent:
            return "%s > %s" % (self.parent, self.name)
        return self.name

    class Meta:
        unique_together = ("name", "parent", "org")


__flow_users = None


def clear_flow_users():
    global __flow_users
    __flow_users = None


def get_flow_user(org):
    global __flow_users
    if not __flow_users:
        __flow_users = {}

    branding = org.get_branding()
    username = "%s_flow" % branding["slug"]
    flow_user = __flow_users.get(username)

    # not cached, let's look it up
    if not flow_user:
        email = branding["support_email"]
        flow_user = User.objects.filter(username=username).first()
        if flow_user:  # pragma: needs cover
            __flow_users[username] = flow_user
        else:
            # doesn't exist for this brand, create it
            flow_user = User.objects.create_user(username, email, first_name="System Update")
            flow_user.groups.add(Group.objects.get(name="Service Users"))
            __flow_users[username] = flow_user

    return flow_user
