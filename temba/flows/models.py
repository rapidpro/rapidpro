import logging
from array import array
from collections import defaultdict
from datetime import datetime

import iso8601
import pytz
from django_redis import get_redis_connection
from packaging.version import Version
from smartmin.models import SmartModel
from xlsxlite.writer import XLSXBook

from django.conf import settings
from django.contrib.auth.models import Group, User
from django.contrib.postgres.fields import ArrayField
from django.core.files.temp import NamedTemporaryFile
from django.db import models, transaction
from django.db.models import Max, Prefetch, Q, Sum
from django.db.models.functions import Lower, TruncDate
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from temba import mailroom
from temba.assets.models import register_asset_store
from temba.channels.models import Channel
from temba.classifiers.models import Classifier
from temba.contacts import search
from temba.contacts.models import Contact, ContactField, ContactGroup
from temba.globals.models import Global
from temba.msgs.models import Label
from temba.orgs.models import DependencyMixin, Org
from temba.templates.models import Template
from temba.tickets.models import Ticketer, Topic
from temba.utils import analytics, chunk_list, json, on_transaction_commit, s3
from temba.utils.export import BaseExportAssetStore, BaseItemWithContactExport
from temba.utils.models import JSONAsTextField, JSONField, LegacyUUIDMixin, SquashableModel, TembaModel
from temba.utils.uuid import uuid4

from . import legacy

logger = logging.getLogger(__name__)


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


class Flow(LegacyUUIDMixin, TembaModel, DependencyMixin):
    CONTACT_CREATION = "contact_creation"
    CONTACT_PER_RUN = "run"
    CONTACT_PER_LOGIN = "login"

    # items in metadata
    METADATA_RESULTS = "results"
    METADATA_DEPENDENCIES = "dependencies"
    METADATA_WAITING_EXIT_UUIDS = "waiting_exit_uuids"
    METADATA_PARENT_REFS = "parent_refs"
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
    TYPE_BACKGROUND = "B"
    TYPE_SURVEY = "S"
    TYPE_VOICE = "V"
    TYPE_USSD = "U"

    TYPE_CHOICES = (
        (TYPE_MESSAGE, _("Messaging")),
        (TYPE_VOICE, _("Phone Call")),
        (TYPE_BACKGROUND, _("Background")),
        (TYPE_SURVEY, _("Surveyor")),
    )

    GOFLOW_TYPES = {
        TYPE_MESSAGE: "messaging",
        TYPE_BACKGROUND: "messaging_background",
        TYPE_SURVEY: "messaging_offline",
        TYPE_VOICE: "voice",
    }

    FINAL_LEGACY_VERSION = legacy.VERSIONS[-1]
    INITIAL_GOFLOW_VERSION = "13.0.0"  # initial version of flow spec to use new engine
    CURRENT_SPEC_VERSION = "13.1.0"  # current flow spec version

    EXPIRES_CHOICES = {
        TYPE_MESSAGE: (
            (5, _("After 5 minutes")),
            (10, _("After 10 minutes")),
            (15, _("After 15 minutes")),
            (30, _("After 30 minutes")),
            (60, _("After 1 hour")),
            (60 * 3, _("After 3 hours")),
            (60 * 6, _("After 6 hours")),
            (60 * 12, _("After 12 hours")),
            (60 * 18, _("After 18 hours")),
            (60 * 24, _("After 1 day")),
            (60 * 24 * 2, _("After 2 days")),
            (60 * 24 * 3, _("After 3 days")),
            (60 * 24 * 7, _("After 1 week")),
            (60 * 24 * 14, _("After 2 weeks")),
            (60 * 24 * 30, _("After 30 days")),
        ),
        TYPE_VOICE: (
            (1, _("After 1 minute")),
            (2, _("After 2 minutes")),
            (3, _("After 3 minutes")),
            (4, _("After 4 minutes")),
            (5, _("After 5 minutes")),
            (10, _("After 10 minutes")),
            (15, _("After 15 minutes")),
        ),
    }
    EXPIRES_DEFAULTS = {
        TYPE_MESSAGE: 60 * 24 * 7,  # 1 week
        TYPE_VOICE: 5,  # 5 minutes
        TYPE_BACKGROUND: 0,
        TYPE_SURVEY: 0,
    }

    org = models.ForeignKey(Org, on_delete=models.PROTECT, related_name="flows")
    labels = models.ManyToManyField("FlowLabel", related_name="flows")
    is_archived = models.BooleanField(default=False)
    flow_type = models.CharField(max_length=1, choices=TYPE_CHOICES, default=TYPE_MESSAGE)
    ignore_triggers = models.BooleanField(default=False, help_text=_("Ignore keyword triggers while in this flow."))

    # properties set from last revision
    expires_after_minutes = models.IntegerField(
        default=EXPIRES_DEFAULTS[TYPE_MESSAGE],
        help_text=_("Minutes of inactivity that will cause expiration from flow."),
    )
    base_language = models.CharField(
        max_length=4,  # until we fix remaining flows with "base"
        help_text=_("The authoring language, additional languages can be added later."),
        default="und",
    )
    version_number = models.CharField(default="0.0.0", max_length=8)  # no actual spec version until there's a revision

    # information from flow inspection
    metadata = JSONAsTextField(null=True, default=dict)  # additional information about the flow, e.g. possible results
    has_issues = models.BooleanField(default=False)

    saved_on = models.DateTimeField(auto_now_add=True)
    saved_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="flow_saves")

    # dependencies on other assets
    channel_dependencies = models.ManyToManyField(Channel, related_name="dependent_flows")
    classifier_dependencies = models.ManyToManyField(Classifier, related_name="dependent_flows")
    field_dependencies = models.ManyToManyField(ContactField, related_name="dependent_flows")
    flow_dependencies = models.ManyToManyField("Flow", related_name="dependent_flows")
    global_dependencies = models.ManyToManyField(Global, related_name="dependent_flows")
    group_dependencies = models.ManyToManyField(ContactGroup, related_name="dependent_flows")
    label_dependencies = models.ManyToManyField(Label, related_name="dependent_flows")
    template_dependencies = models.ManyToManyField(Template, related_name="dependent_flows")
    ticketer_dependencies = models.ManyToManyField(Ticketer, related_name="dependent_flows")
    topic_dependencies = models.ManyToManyField(Topic, related_name="dependent_flows")
    user_dependencies = models.ManyToManyField(User, related_name="dependent_flows")

    soft_dependent_types = {"flow", "campaign_event", "trigger"}  # it's all soft for flows

    @classmethod
    def create(
        cls,
        org,
        user,
        name,
        flow_type=TYPE_MESSAGE,
        expires_after_minutes=0,
        base_language="eng",
        create_revision=False,
        **kwargs,
    ):
        assert cls.is_valid_name(name), f"'{name}' is not a valid flow name"
        assert not expires_after_minutes or cls.is_valid_expires(flow_type, expires_after_minutes)

        flow = cls.objects.create(
            org=org,
            name=name,
            flow_type=flow_type,
            expires_after_minutes=expires_after_minutes or cls.EXPIRES_DEFAULTS[flow_type],
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

        analytics.track(user, "temba.flow_created", dict(name=name, uuid=flow.uuid))
        return flow

    @classmethod
    def create_single_message(cls, org, user, message, base_language):
        """
        Creates a special 'single message' flow
        """
        name = "Single Message (%s)" % str(uuid4())
        flow = Flow.create(org, user, name, flow_type=Flow.TYPE_BACKGROUND, is_system=True)
        flow.update_single_message_flow(user, message, base_language)
        return flow

    @property
    def engine_type(self):
        return Flow.GOFLOW_TYPES.get(self.flow_type, "")

    @classmethod
    def create_join_group(cls, org, user, group, response=None, start_flow=None):
        """
        Creates a special 'join group' flow
        """
        base_language = org.flow_languages[0]

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
    def import_flows(cls, org, user, export_json, dependency_mapping, same_site=False):
        """
        Import flows from our flow export file
        """

        created_flows = []
        db_types = {value: key for key, value in Flow.GOFLOW_TYPES.items()}

        # fetch or create all the flow db objects
        for flow_def in export_json["flows"]:
            flow_version = Version(flow_def[Flow.DEFINITION_SPEC_VERSION])
            flow_type = db_types[flow_def[Flow.DEFINITION_TYPE]]
            flow_uuid = flow_def[Flow.DEFINITION_UUID]
            flow_name = flow_def[Flow.DEFINITION_NAME]
            flow_expires = flow_def.get(Flow.DEFINITION_EXPIRE_AFTER_MINUTES, 0)

            flow = None
            flow_name = cls.clean_name(flow_name)

            # ensure expires is valid for the flow type
            if not cls.is_valid_expires(flow_type, flow_expires):
                flow_expires = cls.EXPIRES_DEFAULTS[flow_type]

            # check if we can find that flow by UUID first
            if same_site:
                flow = org.flows.filter(is_active=True, uuid=flow_uuid).first()

            # if it's not of our world, let's try by name
            if not flow:
                flow = org.flows.filter(is_active=True, name__iexact=flow_name).first()

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
                flow.restore(user)

            dependency_mapping[flow_uuid] = str(flow.uuid)
            created_flows.append((flow, flow_def))

        # import each definition (includes re-mapping dependency references)
        for flow, definition in created_flows:
            flow.import_definition(user, definition, dependency_mapping)

        # remap flow UUIDs in any campaign events
        for campaign in export_json.get("campaigns", []):
            for event in campaign["events"]:
                if "flow" in event:
                    flow_uuid = event["flow"]["uuid"]
                    if flow_uuid in dependency_mapping:
                        event["flow"]["uuid"] = dependency_mapping[flow_uuid]

        # remap flow UUIDs in any triggers
        for trigger in export_json.get("triggers", []):
            if "flow" in trigger:
                flow_uuid = trigger["flow"]["uuid"]
                if flow_uuid in dependency_mapping:
                    trigger["flow"]["uuid"] = dependency_mapping[flow_uuid]

        # return the created flows
        return [f[0] for f in created_flows]

    @classmethod
    def is_valid_expires(cls, flow_type: str, expires: int) -> bool:
        valid_expires = {c[0] for c in cls.EXPIRES_CHOICES.get(flow_type, ())}
        return not valid_expires or expires in valid_expires

    def clone(self, user):
        """
        Returns a clone of this flow
        """
        name = self.get_unique_name(self.org, f"Copy of {self.name}"[: self.MAX_NAME_LEN].strip())
        copy = Flow.create(
            self.org,
            user,
            name,
            flow_type=self.flow_type,
            expires_after_minutes=self.expires_after_minutes,
            base_language=self.base_language,
        )

        # import the original's definition into the copy
        flow_json = self.get_definition()
        copy.import_definition(user, flow_json, {})
        return copy

    @classmethod
    def export_translation(cls, org, flows, language):
        flow_ids = [f.id for f in flows]
        return mailroom.get_client().po_export(org.id, flow_ids, language=language)

    @classmethod
    def import_translation(cls, org, flows, language, po_data):
        flow_ids = [f.id for f in flows]
        response = mailroom.get_client().po_import(org.id, flow_ids, language=language, po_data=po_data)
        return {d["uuid"]: d for d in response["flows"]}

    @classmethod
    def apply_action_label(cls, user, flows, label):
        label.toggle_label(flows, add=True)

    @classmethod
    def apply_action_unlabel(cls, user, flows, label):
        label.toggle_label(flows, add=False)

    @classmethod
    def apply_action_archive(cls, user, flows):
        from temba.campaigns.models import CampaignEvent

        for flow in flows:
            # don't archive flows that belong to campaigns
            has_events = CampaignEvent.objects.filter(
                is_active=True, flow=flow, campaign__org=flow.org, campaign__is_archived=False
            ).exists()

            if not has_events:
                flow.archive(user)

    @classmethod
    def apply_action_restore(cls, user, flows):
        for flow in flows:
            try:
                flow.restore(user)
            except FlowException:  # pragma: no cover
                pass

    def get_attrs(self):
        icon = (
            "icon.flow_message"
            if self.flow_type == Flow.TYPE_MESSAGE
            else "icon.flow_ivr"
            if self.flow_type == Flow.TYPE_VOICE
            else "icon.flow"
        )

        return {"icon": icon, "type": self.flow_type, "uuid": self.uuid}

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

        for result_key, result_dict in results.items():
            for cat in result_dict["categories"]:
                if result_dict["total"]:
                    cat["pct"] = float(cat["count"]) / float(result_dict["total"])
                else:
                    cat["pct"] = 0

            result_dict["categories"] = sorted(result_dict["categories"], key=lambda d: d["name"])

        # order counts by their place on the flow
        result_list = []
        for key in keys:
            result = results.get(key)
            if result:
                result_list.append(result)

        return result_list

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

        # converts a dep ref {uuid|key, name, type, missing} to an importable partial definition {uuid|key, name}
        def ref_to_def(r: dict) -> dict:
            return {k: v for k, v in r.items() if k in ("uuid", "name", "key")}

        def deps_of_type(type_name: str):
            return [ref_to_def(d) for d in dependencies if d["type"] == type_name]

        # ensure all field dependencies exist
        for ref in deps_of_type("field"):
            ContactField.get_or_create(self.org, user, ref["key"], ref["name"])

        # ensure all group dependencies exist
        for ref in deps_of_type("group"):
            if ref["uuid"] not in dependency_mapping:
                group = ContactGroup.get_or_create(self.org, user, ref.get("name"), uuid=ref["uuid"])
                dependency_mapping[ref["uuid"]] = str(group.uuid)

        # ensure any label dependencies exist
        for ref in deps_of_type("label"):
            label, _ = Label.import_def(self.org, user, ref)
            dependency_mapping[ref["uuid"]] = str(label.uuid)

        # ensure any topic dependencies exist
        for ref in deps_of_type("topic"):
            topic, _ = Topic.import_def(self.org, user, ref)
            dependency_mapping[ref["uuid"]] = str(topic.uuid)

        # for dependencies we can't create, look for them by UUID (this is a clone in same workspace)
        # or name (this is an import from other workspace)
        dep_types = {
            "channel": self.org.channels.filter(is_active=True),
            "classifier": self.org.classifiers.filter(is_active=True),
            "flow": self.org.flows.filter(is_active=True),
            "template": self.org.templates.all(),
            "ticketer": self.org.ticketers.filter(is_active=True),
        }
        for dep_type, org_objs in dep_types.items():
            for ref in deps_of_type(dep_type):
                if ref["uuid"] in dependency_mapping:
                    continue

                obj = org_objs.filter(uuid=ref["uuid"]).first()
                if not obj and ref["name"]:
                    name = ref["name"]

                    # migrated legacy flows may have name as <type>: <name>
                    if dep_type == "channel" and ":" in name:
                        name = name.split(":")[-1].strip()

                    obj = org_objs.filter(name=name).first()

                dependency_mapping[ref["uuid"]] = str(obj.uuid) if obj else ref["uuid"]

        # clone definition so that all flow elements get new random UUIDs
        cloned_definition = mailroom.get_client().flow_clone(definition, dependency_mapping)
        if "revision" in cloned_definition:
            del cloned_definition["revision"]

        # save a new revision but we can't validate it just yet because we're in a transaction and mailroom
        # won't see any new database objects
        self.save_revision(user, cloned_definition)

    def archive(self, user):
        self.is_archived = True
        self.modified_by = user
        self.save(update_fields=("is_archived", "modified_by", "modified_on"))

        # queue mailroom to interrupt sessions where contact is currently in this flow
        mailroom.queue_interrupt(self.org, flow=self)

        # archive our triggers as well
        for trigger in self.triggers.all():
            trigger.archive(user)

    def restore(self, user):
        self.is_archived = False
        self.modified_by = user
        self.save(update_fields=("is_archived", "modified_by", "modified_on"))

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
            "type": "messaging_background",
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
        totals_by_status = FlowRunStatusCount.get_totals(self)
        total_runs = sum(totals_by_status.values())
        completed = totals_by_status.get(FlowRun.STATUS_COMPLETED, 0)

        return {
            "total": total_runs,
            "status": {
                "active": totals_by_status.get(FlowRun.STATUS_ACTIVE, 0),
                "waiting": totals_by_status.get(FlowRun.STATUS_WAITING, 0),
                "completed": completed,
                "expired": totals_by_status.get(FlowRun.STATUS_EXPIRED, 0),
                "interrupted": totals_by_status.get(FlowRun.STATUS_INTERRUPTED, 0),
                "failed": totals_by_status.get(FlowRun.STATUS_FAILED, 0),
            },
            "completion": int(completed * 100 // total_runs) if total_runs else 0,
        }

    def get_recent_contacts(self, exit_uuid: str, dest_uuid: str) -> list:
        r = get_redis_connection()
        key = f"recent_contacts:{exit_uuid}:{dest_uuid}"

        # fetch members of the sorted set from redis and save as tuples of (contact_id, operand, time)
        contact_ids = set()
        raw = []
        for member, score in r.zrange(key, start=0, end=-1, desc=True, withscores=True):
            rand, contact_id, operand = member.decode().split("|", maxsplit=2)
            contact_ids.add(int(contact_id))
            raw.append((int(contact_id), operand, datetime.utcfromtimestamp(score).replace(tzinfo=pytz.UTC)))

        # lookup all the referenced contacts
        contacts_by_id = {c.id: c for c in self.org.contacts.filter(id__in=contact_ids, is_active=True)}

        # if contact still exists, include in results
        recent = []
        for r in raw:
            contact = contacts_by_id.get(r[0])
            if contact:
                recent.append(
                    {
                        "contact": {"uuid": str(contact.uuid), "name": contact.get_display(org=self.org)},
                        "operand": r[1],
                        "time": r[2].isoformat(),
                    }
                )

        return recent

    def async_start(self, user, groups, contacts, query=None, restart_participants=False, include_active=True):
        """
        Causes us to schedule a flow to start in a background thread.
        """

        assert not self.org.is_flagged and not self.org.is_suspended, "flagged and suspended orgs can't start flows"

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

    @classmethod
    def get_metadata(cls, flow_info) -> dict:
        return {
            Flow.METADATA_RESULTS: flow_info[Flow.INSPECT_RESULTS],
            Flow.METADATA_DEPENDENCIES: flow_info[Flow.INSPECT_DEPENDENCIES],
            Flow.METADATA_WAITING_EXIT_UUIDS: flow_info[Flow.INSPECT_WAITING_EXITS],
            Flow.METADATA_PARENT_REFS: flow_info[Flow.INSPECT_PARENT_REFS],
        }

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

    def get_definition(self) -> dict:
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

    def save_revision(self, user, definition) -> tuple:
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
        issues = flow_info[Flow.INSPECT_ISSUES]

        if user is None:
            is_system_rev = True
            user = get_flow_user(self.org)
        else:
            is_system_rev = False

        with transaction.atomic():
            new_metadata = Flow.get_metadata(flow_info)

            # IVR retry is the only value in metadata that doesn't come from flow inspection
            if self.metadata and Flow.METADATA_IVR_RETRY in self.metadata:
                new_metadata[Flow.METADATA_IVR_RETRY] = self.metadata[Flow.METADATA_IVR_RETRY]

            # update our flow fields
            self.base_language = definition.get(Flow.DEFINITION_LANGUAGE, None)
            self.version_number = Flow.CURRENT_SPEC_VERSION
            self.has_issues = len(issues) > 0
            self.metadata = new_metadata
            self.modified_by = user
            self.modified_on = timezone.now()
            fields = ["base_language", "version_number", "has_issues", "metadata", "modified_by", "modified_on"]

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

        return revision, issues

    @classmethod
    def migrate_definition(cls, flow_def, flow, to_version=None):
        if not to_version:
            to_version = cls.CURRENT_SPEC_VERSION

        if "version" in flow_def:
            flow_def = legacy.migrate_definition(flow_def, flow=flow)

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
        for flow_def in exported_json["flows"]:
            migrated_def = Flow.migrate_definition(flow_def, flow=None)
            migrated_flows.append(migrated_def)

        exported_json["flows"] = migrated_flows

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
            "group": ContactGroup.get_groups(self.org).filter(uuid__in=identifiers["group"]),
            "label": Label.get_active_for_org(self.org).filter(uuid__in=identifiers["label"]),
            "template": self.org.templates.filter(uuid__in=identifiers["template"]),
            "ticketer": self.org.ticketers.filter(is_active=True, uuid__in=identifiers["ticketer"]),
            "topic": self.org.ticketers.filter(is_active=True, uuid__in=identifiers["topic"]),
            "user": self.org.users.filter(is_active=True, email__in=identifiers["user"]),
        }

        # reset the m2m for each type
        for type_name, objects in dep_objs.items():
            m2m = getattr(self, f"{type_name}_dependencies")
            m2m.clear()
            m2m.add(*objects)

    def get_dependents(self):
        dependents = super().get_dependents()
        dependents["campaign_event"] = self.campaign_events.filter(is_active=True)
        dependents["trigger"] = self.triggers.filter(is_active=True)
        return dependents

    def preview_start(self, *, include: mailroom.QueryInclusions, exclude: mailroom.QueryExclusions) -> tuple:
        """
        Generates a preview of the given start as a tuple of
            1) query of all recipients
            2) total contact count
            3) sample of the contacts (max 3)
            4) query metadata
        """
        preview = search.preview_start(self.org, self, include=include, exclude=exclude, sample_size=3)
        sample = (
            self.org.contacts.filter(id__in=preview.sample_ids)
            .order_by("id")
            .select_related("org")
            .prefetch_related("urns")
        )

        return preview.query, preview.total, sample, preview.metadata

    def release(self, user, *, interrupt_sessions: bool = True):
        """
        Releases this flow, marking it inactive. We interrupt all flow runs in a background process.
        We keep FlowRevisions and FlowStarts however.
        """

        super().release(user)

        self.name = self._deleted_name()
        self.is_active = False
        self.modified_by = user
        self.save(update_fields=("name", "is_active", "modified_by", "modified_on"))

        # release any campaign events that depend on this flow
        from temba.campaigns.models import CampaignEvent

        for event in CampaignEvent.objects.filter(flow=self, is_active=True):
            event.release(user)

        # release any triggers that depend on this flow
        for trigger in self.triggers.all():
            trigger.release(user)

        # release any starts
        for start in self.starts.all():
            start.release()

        self.channel_dependencies.clear()
        self.classifier_dependencies.clear()
        self.field_dependencies.clear()
        self.flow_dependencies.clear()
        self.global_dependencies.clear()
        self.group_dependencies.clear()
        self.label_dependencies.clear()
        self.template_dependencies.clear()
        self.ticketer_dependencies.clear()
        self.topic_dependencies.clear()
        self.user_dependencies.clear()

        # queue mailroom to interrupt sessions where contact is currently in this flow
        if interrupt_sessions:
            mailroom.queue_interrupt(self.org, flow=self)

    def delete(self):
        """
        Does actual deletion of this flow's data
        """

        assert not self.is_active, "can't delete flow which hasn't been released"

        # clear our association with any related sessions
        self.sessions.all().update(current_flow=None)

        # grab the ids of all our runs
        run_ids = self.runs.all().values_list("id", flat=True)

        # batch this for 1,000 runs at a time so we don't grab locks for too long
        for id_batch in chunk_list(run_ids, 1000):
            runs = FlowRun.objects.filter(id__in=id_batch)
            for run in runs:
                run.delete()

        for rev in self.revisions.all():
            rev.release()

        for trigger in self.triggers.all():
            trigger.delete()

        self.category_counts.all().delete()
        self.path_counts.all().delete()
        self.node_counts.all().delete()
        self.status_counts.all().delete()
        self.labels.clear()

        super().delete()

    class Meta:
        ordering = ("-modified_on",)
        verbose_name = _("Flow")
        verbose_name_plural = _("Flows")

        constraints = [models.UniqueConstraint("org", Lower("name"), name="unique_flow_names")]


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

    id = models.BigAutoField(primary_key=True)
    uuid = models.UUIDField(unique=True)
    org = models.ForeignKey(Org, related_name="sessions", on_delete=models.PROTECT)
    contact = models.ForeignKey("contacts.Contact", on_delete=models.PROTECT, related_name="sessions")
    status = models.CharField(max_length=1, choices=STATUS_CHOICES)

    # the modality of this session
    session_type = models.CharField(max_length=1, choices=Flow.TYPE_CHOICES, default=Flow.TYPE_MESSAGE)

    # the call used for flow sessions over IVR
    call = models.OneToOneField("ivr.Call", on_delete=models.PROTECT, null=True, related_name="session")

    # whether the contact has responded in this session
    responded = models.BooleanField(default=False)

    # the engine output of this session (either stored in this field or at the URL pointed to by output_url)
    output = JSONAsTextField(null=True, default=dict)
    output_url = models.URLField(null=True, max_length=2048)

    # when this session was created and ended
    created_on = models.DateTimeField(default=timezone.now)
    ended_on = models.DateTimeField(null=True)

    # if session is waiting for input...
    wait_started_on = models.DateTimeField(null=True)  # when it started waiting
    timeout_on = models.DateTimeField(null=True)  # when it should timeout (set by courier when last msg is sent)
    wait_expires_on = models.DateTimeField(null=True)  # when waiting run can be expired
    wait_resume_on_expire = models.BooleanField()  # whether wait expiration can resume a parent run

    # the flow of the waiting run
    current_flow = models.ForeignKey("flows.Flow", related_name="sessions", null=True, on_delete=models.PROTECT)

    @property
    def output_json(self):
        """
        Returns the output JSON for this session, loading it either from our DB field or S3 if stored there.
        """
        # if our output is stored on S3, fetch it from there
        if self.output_url:
            return json.loads(s3.get_body(self.output_url))

        # otherwise, read it from our DB field
        else:
            return self.output

    def delete(self):
        for run in self.runs.all():
            run.delete()

        super().delete()

    def __str__(self):  # pragma: no cover
        return str(self.contact)

    class Meta:
        indexes = [
            models.Index(
                name="flows_session_message_expires",
                fields=("wait_expires_on",),
                condition=Q(session_type=Flow.TYPE_MESSAGE, status="W", wait_expires_on__isnull=False),
            ),
            models.Index(
                name="flows_session_voice_expires",
                fields=("wait_expires_on",),
                condition=Q(session_type=Flow.TYPE_VOICE, status="W", wait_expires_on__isnull=False),
            ),
        ]
        constraints = [
            # ensure that waiting sessions have a wait started and expires
            models.CheckConstraint(
                check=~Q(status="W") | Q(wait_started_on__isnull=False, wait_expires_on__isnull=False),
                name="flows_session_waiting_has_started_and_expires",
            ),
            # ensure that non-waiting sessions have an ended_on
            models.CheckConstraint(
                check=Q(status="W") | Q(ended_on__isnull=False), name="flows_session_non_waiting_has_ended_on"
            ),
            # ensure that all sessions have output or output_url
            models.CheckConstraint(
                check=Q(output__isnull=False) | Q(output_url__isnull=False),
                name="flows_session_has_output_or_url",
            ),
        ]


class FlowRun(models.Model):
    """
    A single contact's journey through a flow. It records the path taken, results collected etc.
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

    EXIT_TYPE_COMPLETED = "C"
    EXIT_TYPE_INTERRUPTED = "I"
    EXIT_TYPE_EXPIRED = "E"
    EXIT_TYPE_FAILED = "F"
    EXIT_TYPE_CHOICES = (
        (EXIT_TYPE_COMPLETED, "Completed"),
        (EXIT_TYPE_INTERRUPTED, "Interrupted"),
        (EXIT_TYPE_EXPIRED, "Expired"),
        (EXIT_TYPE_FAILED, "Failed"),
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

    id = models.BigAutoField(primary_key=True)
    uuid = models.UUIDField(unique=True, default=uuid4)
    org = models.ForeignKey(Org, on_delete=models.PROTECT, related_name="runs", db_index=False)
    flow = models.ForeignKey(Flow, on_delete=models.PROTECT, related_name="runs")
    status = models.CharField(max_length=1, choices=STATUS_CHOICES)

    # contact isn't an index because we have flows_flowrun_contact_inc_flow below
    contact = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="runs", db_index=False)

    # session this run belongs to (can be null if session has been trimmed)
    session = models.ForeignKey(FlowSession, on_delete=models.PROTECT, related_name="runs", null=True)

    # when this run was created, last modified and exited
    created_on = models.DateTimeField(default=timezone.now)
    modified_on = models.DateTimeField(default=timezone.now)
    exited_on = models.DateTimeField(null=True)

    # true if the contact has responded in this run
    responded = models.BooleanField(default=False)

    # flow start which started the session this run belongs to
    start = models.ForeignKey("flows.FlowStart", on_delete=models.PROTECT, null=True, related_name="runs")

    # if this run is part of a Surveyor session, the user that submitted it
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, db_index=False)

    # results collected in this run keyed by snakified result name
    results = JSONAsTextField(null=True, default=dict)

    # path taken by this run through the flow
    path = JSONAsTextField(null=True, default=list)

    # current node location of this run in the flow
    current_node_uuid = models.UUIDField(null=True)

    # set when deleting to signal to db triggers that result category counts should be decremented
    delete_from_results = models.BooleanField(null=True)

    def as_archive_json(self):
        from temba.api.v2.views import FlowRunReadSerializer

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
            "created_on": self.created_on.isoformat(),
            "modified_on": self.modified_on.isoformat(),
            "exited_on": self.exited_on.isoformat() if self.exited_on else None,
            "exit_type": FlowRunReadSerializer.EXIT_TYPES.get(self.status),
            "submitted_by": self.submitted_by.username if self.submitted_by else None,
        }

    def delete(self, interrupt: bool = True):
        """
        Deletes this run, decrementing it from result category counts
        """
        with transaction.atomic():
            self.delete_from_results = True
            self.save(update_fields=("delete_from_results",))

            if interrupt and self.session and self.session.status == FlowSession.STATUS_WAITING:
                mailroom.queue_interrupt(self.org, session=self.session)

            super().delete()

    def __str__(self):  # pragma: no cover
        return f"FlowRun[uuid={self.uuid}, flow={self.flow.uuid}]"

    class Meta:
        indexes = [
            models.Index(
                name="flows_flowrun_contacts_at_node",
                fields=("org", "current_node_uuid"),
                condition=Q(status__in=("A", "W")),
                include=("contact",),
            ),
            models.Index(name="flows_flowrun_contact_inc_flow", fields=("contact",), include=("flow",)),
        ]
        constraints = [
            # all active/waiting runs must have a session
            models.CheckConstraint(
                check=~Q(status__in=("A", "W")) | Q(session__isnull=False),
                name="flows_run_active_or_waiting_has_session",
            ),
            # all non-active/waiting runs must have an exited_on
            models.CheckConstraint(
                check=Q(status__in=("A", "W")) | Q(exited_on__isnull=False),
                name="flows_run_inactive_has_exited_on",
            ),
        ]


class FlowExit:
    """
    A helper class used for building contact histories which simply wraps a run which may occur more than once in the
    same history as both a flow run start and an exit.
    """

    def __init__(self, run):
        self.run = run


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

    def get_migrated_definition(self, to_version: str = Flow.CURRENT_SPEC_VERSION) -> dict:
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

    squash_over = ("flow_id", "node_uuid", "result_key", "result_name", "category_name")

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

    squash_over = ("flow_id", "from_uuid", "to_uuid", "period")

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

    class Meta:
        index_together = ["flow", "from_uuid", "to_uuid", "period"]


class FlowNodeCount(SquashableModel):
    """
    Maintains counts of unique contacts at each flow node.
    """

    squash_over = ("node_uuid",)

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


class FlowRunStatusCount(SquashableModel):
    """
    Maintains counts of different statuses of flow runs for all flows. These are inserted via triggers on the database.
    """

    squash_over = ("flow_id", "status")

    flow = models.ForeignKey(Flow, on_delete=models.PROTECT, related_name="status_counts")
    status = models.CharField(max_length=1, choices=FlowRun.STATUS_CHOICES)
    count = models.IntegerField(default=0)

    @classmethod
    def get_squash_query(cls, distinct_set):
        sql = r"""
        WITH removed as (
            DELETE FROM flows_flowrunstatuscount WHERE "flow_id" = %s AND "status" = %s RETURNING "count"
        )
        INSERT INTO flows_flowrunstatuscount("flow_id", "status", "count", "is_squashed")
        VALUES (%s, %s, GREATEST(0, (SELECT SUM("count") FROM removed)), TRUE);
        """

        return sql, (distinct_set.flow_id, distinct_set.status) * 2

    @classmethod
    def get_totals(cls, flow):
        totals = list(cls.objects.filter(flow=flow).values_list("status").annotate(total=Sum("count")))
        return {t[0]: t[1] for t in totals}

    class Meta:
        indexes = [
            models.Index(fields=("flow", "status")),
            # for squashing task
            models.Index(name="flowrun_count_unsquashed", fields=("flow", "status"), condition=Q(is_squashed=False)),
        ]


class ExportFlowResultsTask(BaseItemWithContactExport):
    """
    Container for managing our export requests
    """

    analytics_key = "flowresult_export"
    notification_export_type = "results"

    RESPONDED_ONLY = "responded_only"
    EXTRA_URNS = "extra_urns"

    flows = models.ManyToManyField(Flow, related_name="exports", help_text=_("The flows to export"))

    # TODO backfill, for now overridden from base class to make nullable
    start_date = models.DateField(null=True)
    end_date = models.DateField(null=True)

    config = JSONAsTextField(null=True, default=dict, help_text=_("Any configuration options for this flow export"))

    @classmethod
    def create(cls, org, user, start_date, end_date, flows, with_fields, with_groups, responded_only, extra_urns):
        config = {ExportFlowResultsTask.RESPONDED_ONLY: responded_only, ExportFlowResultsTask.EXTRA_URNS: extra_urns}

        export = cls.objects.create(
            org=org, created_by=user, start_date=start_date, end_date=end_date, modified_by=user, config=config
        )
        export.flows.add(*flows)
        export.with_fields.add(*with_fields)
        export.with_groups.add(*with_groups)
        return export

    def _get_runs_columns(self, extra_urn_columns, result_fields, show_submitted_by=False):
        columns = []

        if show_submitted_by:
            columns.append("Submitted By")

        columns += self._get_contact_headers()

        for extra_urn in extra_urn_columns:
            columns.append(extra_urn["label"])

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

    def write_export(self):
        config = self.config
        responded_only = config.get(ExportFlowResultsTask.RESPONDED_ONLY, True)
        extra_urns = config.get(ExportFlowResultsTask.EXTRA_URNS, [])

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

        runs_columns = self._get_runs_columns(extra_urn_columns, result_fields, show_submitted_by=show_submitted_by)

        book = XLSXBook()
        book.num_runs_sheets = 0
        book.num_msgs_sheets = 0

        # the current sheets
        book.current_runs_sheet = self._add_runs_sheet(book, runs_columns)
        book.current_msgs_sheet = None

        start_date, end_date = self._get_date_range()

        for batch in self._get_run_batches(start_date, end_date, flows, responded_only):
            self._write_runs(
                book,
                batch,
                extra_urn_columns,
                show_submitted_by,
                runs_columns,
                result_fields,
            )

            self.modified_on = timezone.now()
            self.save(update_fields=("modified_on",))

        temp = NamedTemporaryFile(delete=True)
        book.finalize(to_file=temp)
        temp.flush()
        return temp, "xlsx"

    def _get_run_batches(self, start_date, end_date, flows, responded_only: bool):
        logger.info(f"Results export #{self.id} for org #{self.org.id}: fetching runs from archives to export...")

        # firstly get runs from archives
        from temba.archives.models import Archive

        # get the earliest created date of the flows being exported
        earliest_created_on = None
        for flow in flows:
            if earliest_created_on is None or flow.created_on < earliest_created_on:
                earliest_created_on = flow.created_on

        flow_uuids = [str(flow.uuid) for flow in flows]
        where = {"flow__uuid__in": flow_uuids}
        if responded_only:
            where["responded"] = True
        records = Archive.iter_all_records(
            self.org, Archive.TYPE_FLOWRUN, after=max(earliest_created_on, start_date), before=end_date, where=where
        )
        seen = set()

        for record_batch in chunk_list(records, 1000):
            matching = []
            for record in record_batch:
                seen.add(record["id"])
                matching.append(record)
            yield matching

        # secondly get runs from database
        runs = (
            FlowRun.objects.filter(created_on__gte=start_date, created_on__lte=end_date, flow__in=flows)
            .order_by("modified_on")
            .using("readonly")
        )
        if responded_only:
            runs = runs.filter(responded=True)
        run_ids = array(str("l"), runs.values_list("id", flat=True))

        logger.info(
            f"Results export #{self.id} for org #{self.org.id}: found {len(run_ids)} runs in database to export"
        )

        for id_batch in chunk_list(run_ids, 1000):
            run_batch = (
                FlowRun.objects.filter(id__in=id_batch)
                .order_by("modified_on", "id")
                .prefetch_related(
                    Prefetch("contact", Contact.objects.only("uuid", "name")),
                    Prefetch("flow", Flow.objects.only("uuid", "name")),
                )
                .using("readonly")
            )

            # convert this batch of runs to same format as records in our archives
            yield [run.as_archive_json() for run in run_batch if run.id not in seen]

    def _write_runs(
        self,
        book,
        runs,
        extra_urn_columns,
        show_submitted_by,
        runs_columns,
        result_fields,
    ):
        """
        Writes a batch of run JSON blobs to the export
        """
        # get all the contacts referenced in this batch
        contact_uuids = {r["contact"]["uuid"] for r in runs}
        contacts = (
            Contact.objects.filter(org=self.org, uuid__in=contact_uuids)
            .select_related("org")
            .prefetch_related("groups")
            .using("readonly")
        )
        contacts_by_uuid = {str(c.uuid): c for c in contacts}

        Contact.bulk_urn_cache_initialize(contacts, using="readonly")

        for run in runs:
            contact = contacts_by_uuid.get(run["contact"]["uuid"])

            # get this run's results by node name(ruleset label)
            run_values = run["values"]
            if isinstance(run_values, list):
                results_by_key = {key: result for item in run_values for key, result in item.items()}
            else:
                results_by_key = {key: result for key, result in run_values.items()}

            # generate contact info columns
            contact_values = self._get_contact_columns(contact)

            for extra_urn_column in extra_urn_columns:
                urn_display = contact.get_urn_display(org=self.org, formatted=False, scheme=extra_urn_column["scheme"])
                contact_values.append(urn_display)

            # generate result columns for each ruleset
            result_values = []
            for result_field in result_fields:
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


@register_asset_store
class ResultsExportAssetStore(BaseExportAssetStore):
    model = ExportFlowResultsTask
    key = "results_export"
    directory = "results_exports"
    permission = "flows.flow_export_results"
    extensions = ("xlsx",)


class FlowStart(models.Model):
    """
    A queuable request to start contacts and groups in a flow
    """

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

    uuid = models.UUIDField(unique=True, default=uuid4)
    org = models.ForeignKey(Org, on_delete=models.PROTECT, related_name="flow_starts")
    flow = models.ForeignKey(Flow, on_delete=models.PROTECT, related_name="starts")
    start_type = models.CharField(max_length=1, choices=TYPE_CHOICES)

    # who to start
    groups = models.ManyToManyField(ContactGroup)
    contacts = models.ManyToManyField(Contact)
    urns = ArrayField(models.TextField(), null=True)
    query = models.TextField(null=True)

    # whether to restart contacts that have already participated in this flow
    restart_participants = models.BooleanField(default=True)

    # whether to start contacts in this flow that are active in other flows
    include_active = models.BooleanField(default=True)

    # the campaign event that started this flow start (if any)
    campaign_event = models.ForeignKey(
        "campaigns.CampaignEvent", null=True, on_delete=models.PROTECT, related_name="flow_starts"
    )

    # any IVR calls associated with this flow start
    calls = models.ManyToManyField("ivr.Call", related_name="starts")

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
            self.calls.clear()
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
            # used by the flow_starts type filters page
            models.Index(name="flows_flowstart_org_start_type", fields=["org", "start_type", "-created_on"]),
        ]


class FlowStartCount(SquashableModel):
    """
    Maintains count of how many runs a FlowStart has created.
    """

    squash_over = ("start_id",)

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
        return cls.sum(start.counts.all())

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


class FlowLabel(TembaModel):
    """
    A label applied to a flow rather than a message
    """

    org = models.ForeignKey(Org, on_delete=models.PROTECT, related_name="flow_labels")

    # TODO drop
    parent = models.ForeignKey("FlowLabel", on_delete=models.PROTECT, null=True, related_name="children")

    @classmethod
    def create(cls, org, user, name: str):
        assert cls.is_valid_name(name), f"'{name}' is not a valid flow label name"
        assert not org.flow_labels.filter(name__iexact=name).exists()

        return cls.objects.create(org=org, name=name, created_by=user, modified_by=user)

    def get_flow_count(self):
        """
        Returns the count of flows tagged with this label or one of its children
        """
        return self.get_flows().count()

    def get_flows(self):
        return self.flows.filter(is_active=True, is_archived=False)

    def toggle_label(self, flows, *, add: bool):
        changed = []

        for flow in flows:
            # if we are adding the flow label and this flow doesnt have it, add it
            if add:
                if not flow.labels.filter(pk=self.id):
                    flow.labels.add(self)
                    changed.append(flow.id)

            # otherwise, remove it if not already present
            else:
                if flow.labels.filter(pk=self.id):
                    flow.labels.remove(self)
                    changed.append(flow.id)

        return changed

    def __str__(self):
        return self.name

    class Meta:
        constraints = [models.UniqueConstraint("org", Lower("name"), name="unique_flowlabel_names")]


__flow_users = None


def clear_flow_users():
    global __flow_users
    __flow_users = None


def get_flow_user(org):
    global __flow_users
    if not __flow_users:
        __flow_users = {}

    username = "%s_flow" % org.branding["slug"]
    flow_user = __flow_users.get(username)

    # not cached, let's look it up
    if not flow_user:
        email = org.branding["support_email"]
        flow_user = User.objects.filter(username=username).first()
        if flow_user:  # pragma: needs cover
            __flow_users[username] = flow_user
        else:
            # doesn't exist for this brand, create it
            flow_user = User.objects.create_user(username, email, first_name="System Update")
            flow_user.groups.add(Group.objects.get(name="Service Users"))
            __flow_users[username] = flow_user

    return flow_user
