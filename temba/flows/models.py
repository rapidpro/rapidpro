import logging
import time
from array import array
from collections import defaultdict
from datetime import timedelta
from enum import Enum
from typing import Dict

import iso8601
import regex
from django_redis import get_redis_connection
from packaging.version import Version
from smartmin.models import SmartModel
from xlsxlite.writer import XLSXBook

from django.conf import settings
from django.contrib.auth.models import Group, User
from django.contrib.postgres.fields import ArrayField
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
from temba.utils.export import BaseExportAssetStore, BaseExportTask
from temba.utils.models import (
    JSONAsTextField,
    JSONField,
    RequireUpdateFieldsMixin,
    SquashableModel,
    TembaModel,
    generate_uuid,
)
from temba.utils.uuid import uuid4

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
    CONTACT_CREATION = "contact_creation"
    CONTACT_PER_RUN = "run"
    CONTACT_PER_LOGIN = "login"

    # items in metadata
    METADATA_RESULTS = "results"
    METADATA_DEPENDENCIES = "dependencies"
    METADATA_WAITING_EXIT_UUIDS = "waiting_exit_uuids"
    METADATA_PARENT_REFS = "parent_refs"
    METADATA_ISSUES = "issues"
    METADATA_IVR_RETRY = "ivr_retry"

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

    FINAL_LEGACY_VERSION = legacy.VERSIONS[-1]
    INITIAL_GOFLOW_VERSION = "13.0.0"  # initial version of flow spec to use new engine
    CURRENT_SPEC_VERSION = "13.1.0"  # current flow spec version

    DEFAULT_EXPIRES_AFTER = 60 * 24 * 7  # 1 week

    name = models.CharField(max_length=64, help_text=_("The name for this flow"))

    labels = models.ManyToManyField(
        "FlowLabel", related_name="flows", verbose_name=_("Labels"), blank=True, help_text=_("Any labels on this flow")
    )

    org = models.ForeignKey(Org, on_delete=models.PROTECT, related_name="flows")

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
        max_length=4,
        null=True,
        blank=True,
        help_text=_("The authoring language, additional languages can be added later"),
        default="base",
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

        created_flows = []
        db_types = {value: key for key, value in Flow.GOFLOW_TYPES.items()}

        # fetch or create all the flow db objects
        for flow_def in export_json[Org.EXPORT_FLOWS]:
            flow_version = Version(flow_def[Flow.DEFINITION_SPEC_VERSION])
            flow_type = db_types[flow_def[Flow.DEFINITION_TYPE]]
            flow_uuid = flow_def[Flow.DEFINITION_UUID]
            flow_name = flow_def[Flow.DEFINITION_NAME]
            flow_expires = flow_def.get(Flow.DEFINITION_EXPIRE_AFTER_MINUTES, Flow.DEFAULT_EXPIRES_AFTER)

            flow = None
            flow_name = flow_name[:64].strip()

            if flow_type == Flow.TYPE_VOICE:
                flow_expires = min([flow_expires, 15])  # voice flow expiration can't be more than 15 minutes

            # check if we can find that flow by UUID first
            if same_site:
                flow = org.flows.filter(is_active=True, uuid=flow_uuid).first()

            # if it's not of our world, let's try by name
            if not flow:
                flow = org.flows.filter(is_active=True, name=flow_name).first()

            if flow:
                flow.name = Flow.get_unique_name(org, flow_name, ignore=flow)
                flow.version_number = flow_version
                flow.expires_after_minutes = flow_expires
                flow.save(update_fields=("name", "expires_after_minutes"))
            else:
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
        flow_json = flow.get_definition()

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

        definition = Flow.migrate_definition(definition, flow=None)

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

    def is_legacy(self) -> bool:
        """
        Returns whether this flow still uses a legacy definition
        """
        return Version(self.version_number) < Version(Flow.INITIAL_GOFLOW_VERSION)

    def as_export_ref(self) -> Dict:
        return {Flow.DEFINITION_UUID: str(self.uuid), Flow.DEFINITION_NAME: self.name}

    @classmethod
    def get_metadata(cls, flow_info, previous=None) -> Dict:
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

    def ensure_current_version(self):
        """
        Makes sure the flow is at the latest spec version
        """

        # nothing to do if flow is already at the target version
        if Version(self.version_number) >= Version(Flow.CURRENT_SPEC_VERSION):
            return

        with self.lock():
            revision = self.get_current_revision()
            flow_def = revision.get_migrated_definition()

            self.save_revision(user=None, definition=flow_def)
            self.refresh_from_db()

    def get_definition(self) -> Dict:
        """
        Returns the current definition of this flow
        """
        rev = self.get_current_revision()

        assert rev, "can't get definition of flow with no revisions"

        # update metadata in definition from database object as it may be out of date
        definition = rev.definition

        if self.is_legacy():
            if "metadata" not in definition:
                definition["metadata"] = {}
            definition["metadata"]["uuid"] = self.uuid
            definition["metadata"]["name"] = self.name
            definition["metadata"]["revision"] = rev.revision
            definition["metadata"]["expires"] = self.expires_after_minutes
        else:
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

        if user is None:
            is_system_rev = True
            user = get_flow_user(self.org)
        else:
            is_system_rev = False

        with transaction.atomic():
            # update our flow fields
            self.base_language = definition.get(Flow.DEFINITION_LANGUAGE, None)
            self.version_number = Flow.CURRENT_SPEC_VERSION
            self.metadata = Flow.get_metadata(flow_info, self.metadata)
            self.modified_by = user
            self.modified_on = timezone.now()
            fields = ["base_language", "version_number", "metadata", "modified_by", "modified_on"]

            if not is_system_rev:
                self.saved_by = user
                self.saved_on = timezone.now()
                fields += ["saved_by", "saved_on"]

            self.save(update_fields=fields)

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

    @classmethod
    def migrate_definition(cls, flow_def, flow, to_version=None):
        if not to_version:
            to_version = cls.CURRENT_SPEC_VERSION

        if "version" in flow_def:
            flow_def = legacy.migrate_definition(flow_def, flow=flow)

        if "metadata" not in flow_def:
            flow_def["metadata"] = {}

        # ensure definition has a valid expiration
        expires = flow_def["metadata"].get("expires", 0)
        if expires <= 0 or expires > (30 * 24 * 60):
            flow_def["metadata"]["expires"] = Flow.DEFAULT_EXPIRES_AFTER

        # migrate using goflow for anything newer
        if Version(to_version) >= Version(Flow.INITIAL_GOFLOW_VERSION):
            flow_def = mailroom.get_client().flow_migrate(flow_def, to_version)

        return flow_def

    @classmethod
    def migrate_export(cls, org, exported_json, same_site, version):
        # use legacy migrations to get export to final legacy version
        if version < Version(Flow.FINAL_LEGACY_VERSION):
            from temba.flows.legacy import exports

            exported_json = exports.migrate(org, exported_json, same_site, version)

        migrated_flows = []
        for flow_def in exported_json[Org.EXPORT_FLOWS]:
            migrated_def = Flow.migrate_definition(flow_def, flow=None)
            migrated_flows.append(migrated_def)

        exported_json[Org.EXPORT_FLOWS] = migrated_flows

        return exported_json

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
    def validate_legacy_definition(cls, definition):
        if definition["flow_type"] not in (Flow.TYPE_MESSAGE, Flow.TYPE_VOICE, Flow.TYPE_SURVEY, "F"):
            raise ValueError("unsupported flow type")

        # should always have a base_language
        if "base_language" not in definition or not definition["base_language"]:
            raise ValueError("non-localized flow definition")

        # language should match values in definition
        base_language = definition["base_language"]

        def validate_localization(lang_dict):
            # must be a dict
            if not isinstance(lang_dict, dict):
                raise ValueError("non-localized flow definition")

            # and contain the base_language
            if base_language not in lang_dict:  # pragma: needs cover
                raise ValueError("non-localized flow definition")

        for actionset in definition["action_sets"]:
            for action in actionset["actions"]:
                if "msg" in action and action["type"] != "email":
                    validate_localization(action["msg"])

        for ruleset in definition["rule_sets"]:
            for rule in ruleset["rules"]:
                validate_localization(rule["category"])

    def get_migrated_definition(self, to_version: str = Flow.CURRENT_SPEC_VERSION) -> Dict:
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
            definition["version"] = self.spec_version

            if "metadata" not in definition:
                definition["metadata"] = {}
            definition["metadata"]["revision"] = self.revision

        # migrate our definition if necessary
        if self.spec_version != to_version:
            definition = Flow.migrate_definition(definition, self.flow, to_version)

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

    # the individual URNs that should be considered for start in this flow
    urns = ArrayField(models.TextField(), null=True)

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
        groups=(),
        contacts=(),
        urns=(),
        query=None,
        restart_participants=True,
        extra=None,
        include_active=True,
        campaign_event=None,
    ):
        start = FlowStart.objects.create(
            org=flow.org,
            flow=flow,
            start_type=start_type,
            restart_participants=restart_participants,
            include_active=include_active,
            campaign_event=campaign_event,
            urns=list(urns),
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
