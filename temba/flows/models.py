import os
import logging
import time
import requests
import zipfile

from array import array
from collections import OrderedDict, defaultdict
from datetime import date, timedelta
from decimal import Decimal
from enum import Enum
from io import BytesIO
from urllib.parse import urlparse
from urllib.request import urlopen
from uuid import uuid4

import iso8601
import phonenumbers
import regex
import boto3
from django.db.models.functions import TruncDate
from django_redis import get_redis_connection
from packaging.version import Version
from PIL import Image, ExifTags
from sorl.thumbnail import get_thumbnail
from smartmin.models import SmartModel
from temba_expressions.utils import tokenize
from xlsxlite.writer import XLSXBook

from django.conf import settings
from django.contrib.auth.models import Group, User
from django.core.cache import cache
from django.core.files.temp import NamedTemporaryFile
from django.db import connection as db_connection, models, transaction
from django.db.models import Max, Q, Sum

from django.db.models.signals import post_save
from django.dispatch import receiver
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import ugettext_lazy as _

from temba import mailroom
from temba.airtime.models import AirtimeTransfer
from temba.assets.models import register_asset_store
from temba.channels.models import Channel, ChannelConnection
from temba.contacts.models import Contact, ContactField, ContactGroup, ContactURN, URN
from temba.locations.models import AdminBoundary
from temba.links.models import LinkContacts
from temba.msgs.models import DELIVERED, PENDING, Broadcast, Label, Msg
from temba.orgs.models import Org
from temba.links.models import Link
from temba.classifiers.models import Classifier
from temba.globals.models import Global
from temba.templates.models import Template
from temba.utils import analytics, chunk_list, json, on_transaction_commit
from temba.utils.dates import str_to_datetime, datetime_to_str
from temba.utils.email import is_valid_address
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
from temba.values.constants import Value

from . import legacy

logger = logging.getLogger(__name__)

FLOW_DEFAULT_EXPIRES_AFTER = 60 * 12
START_FLOW_BATCH_SIZE = 500


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
FLOW_LOCK_KEY = "org:%d:lock:flow:%d:%s"

FLOW_PROP_CACHE_KEY = "org:%d:cache:flow:%d:%s"
FLOW_PROP_CACHE_TTL = 24 * 60 * 60 * 7  # 1 week

UNREAD_FLOW_RESPONSES = "unread_flow_responses"

FLOW_BATCH = "flow_batch"


class FlowLock(Enum):
    """
    Locks that are flow specific
    """

    participation = 1
    definition = 3


class FlowPropsCache(Enum):
    """
    Properties of a flow that we cache
    """

    terminal_nodes = 1
    category_nodes = 2


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
    WEBHOOK_URL = "webhook"
    WEBHOOK_ACTION = "webhook_action"
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

    # items in Flow.metadata
    METADATA = "metadata"
    METADATA_SAVED_ON = "saved_on"
    METADATA_NAME = "name"
    METADATA_REVISION = "revision"
    METADATA_EXPIRES = "expires"
    METADATA_RESULTS = "results"
    METADATA_DEPENDENCIES = "dependencies"
    METADATA_WAITING_EXIT_UUIDS = "waiting_exit_uuids"
    METADATA_PARENT_REFS = "parent_refs"
    METADATA_ISSUES = "issues"

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

    VERSIONS = [
        "1",
        "2",
        "3",
        "4",
        "5",
        "6",
        "7",
        "8",
        "9",
        "10",
        "10.1",
        "10.2",
        "10.3",
        "10.4",
        "11.0",
        "11.1",
        "11.2",
        "11.3",
        "11.4",
        "11.5",
        "11.6",
        "11.7",
        "11.8",
        "11.9",
        "11.10",
        "11.11",
        "11.12",
    ]

    FINAL_LEGACY_VERSION = VERSIONS[-1]
    GOFLOW_VERSION = "13.0.0"
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

        analytics.track(user.username, "nyaruka.flow_created", dict(name=name))
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
    def is_before_version(cls, to_check, version):
        if str(to_check) not in Flow.VERSIONS:
            return False

        return Version(str(to_check)) < Version(str(version))

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
    def get_node(cls, flow, uuid, destination_type):

        if not uuid or not destination_type:
            return None

        if destination_type == Flow.NODE_TYPE_RULESET:
            node = RuleSet.get(flow, uuid)
        else:
            node = ActionSet.get(flow, uuid)

        if node:
            node.flow = flow
        return node

    @classmethod
    def handle_call(cls, call, text=None, saved_media_url=None, hangup=False, resume=False):
        run = (
            FlowRun.objects.filter(connection=call, is_active=True)
            .select_related("org")
            .order_by("-created_on")
            .first()
        )

        # what we will send back
        voice_response = call.channel.generate_ivr_response()

        if run is None:  # pragma: no cover
            voice_response.hangup()
            return voice_response

        flow = run.flow

        # make sure we have the latest version
        flow.ensure_current_version()

        run.voice_response = voice_response

        # create a message to hold our inbound message
        from temba.msgs.models import IVR

        if text or saved_media_url:

            # we don't have text for media, so lets use the media value there too
            if saved_media_url and ":" in saved_media_url:
                text = saved_media_url.partition(":")[2]

            msg = Msg.create_incoming(
                call.channel,
                str(call.contact_urn),
                text,
                status=PENDING,
                msg_type=IVR,
                attachments=[saved_media_url] if saved_media_url else None,
                connection=run.connection,
            )
        else:
            msg = Msg(org=call.org, contact=call.contact, text="", id=0)

        # find out where we last left off
        last_step = run.path[-1] if run.path else None

        # if we are just starting the flow, create our first step
        if not last_step:
            # lookup our entry node
            destination = ActionSet.objects.filter(flow=run.flow, uuid=flow.entry_uuid).first()
            if not destination:
                destination = RuleSet.objects.filter(flow=run.flow, uuid=flow.entry_uuid).first()

            # and add our first step for our run
            if destination:
                flow.add_step(run, destination, [])
        else:
            destination = Flow.get_node(run.flow, last_step[FlowRun.PATH_NODE_UUID], Flow.NODE_TYPE_RULESET)

        if not destination:  # pragma: no cover
            voice_response.hangup()
            run.set_completed(exit_uuid=None)
            return voice_response

        # go and actually handle wherever we are in the flow
        (handled, msgs) = Flow.handle_destination(
            destination, run, msg, user_input=text is not None, resume_parent_run=resume
        )

        # if we stopped needing user input (likely), then wrap our response accordingly
        voice_response = Flow.wrap_voice_response_with_input(call, run, voice_response)

        # if we handled it, mark it so
        if handled and msg.id:
            from temba.msgs import legacy

            legacy.mark_handled(msg)

        # if we didn't handle it, this is a good time to hangup
        if not handled or hangup:
            voice_response.hangup()
            run.set_completed(exit_uuid=None)

        return voice_response

    @classmethod
    def wrap_voice_response_with_input(cls, call, run, voice_response):
        """ Finds where we are in the flow and wraps our voice_response with whatever comes next """
        last_step = run.path[-1]
        destination = Flow.get_node(run.flow, last_step[FlowRun.PATH_NODE_UUID], Flow.NODE_TYPE_RULESET)

        if isinstance(destination, RuleSet):
            response = call.channel.generate_ivr_response()
            callback = "https://%s%s" % (run.org.get_brand_domain(), reverse("ivr.ivrcall_handle", args=[call.pk]))
            gather = destination.get_voice_input(response, action=callback)

            # recordings have to be tacked on last
            if destination.ruleset_type == RuleSet.TYPE_WAIT_RECORDING:
                voice_response.record(action=callback)

            elif destination.ruleset_type == RuleSet.TYPE_SUBFLOW:
                voice_response.redirect(url=callback)

            elif gather and hasattr(gather, "document"):  # voicexml case
                gather.join(voice_response)

                voice_response = response

            elif gather:  # TwiML case
                # nest all of our previous verbs in our gather
                for verb in voice_response.verbs:
                    gather.append(verb)

                voice_response = response

                # append a redirect at the end in case the user sends #
                voice_response.redirect(url=callback + "?empty=1")

        return voice_response

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
    def find_and_handle(
        cls,
        msg,
        started_flows=None,
        voice_response=None,
        triggered_start=False,
        resume_parent_run=False,
        user_input=True,
        trigger_send=True,
        continue_parent=True,
    ):

        if started_flows is None:
            started_flows = []

        for run in FlowRun.get_active_for_contact(msg.contact):
            flow = run.flow
            flow.ensure_current_version()

            # it's possible Flow.start is in the process of creating a run for this contact, in which case
            # record this message has handled so it doesn't start any new flows
            if not run.path:
                if run.created_on > timezone.now() - timedelta(minutes=10):
                    return True, []
                else:
                    return False, []

            last_step = run.path[-1]
            destination = Flow.get_node(flow, last_step[FlowRun.PATH_NODE_UUID], Flow.NODE_TYPE_RULESET)

            # this node doesn't exist anymore, mark it as left so they leave the flow
            if not destination:  # pragma: no cover
                run.set_completed(exit_uuid=None)
                return True, []

            (handled, msgs) = Flow.handle_destination(
                destination,
                run,
                msg,
                started_flows,
                user_input=user_input,
                triggered_start=triggered_start,
                resume_parent_run=resume_parent_run,
                trigger_send=trigger_send,
                continue_parent=continue_parent,
            )

            if handled:
                analytics.gauge("temba.run_resumes")
                return True, msgs

        return False, []

    @classmethod
    def handle_destination(
        cls,
        destination,
        run,
        msg,
        started_flows=None,
        user_input=False,
        triggered_start=False,
        trigger_send=True,
        resume_parent_run=False,
        continue_parent=True,
    ):

        if started_flows is None:
            started_flows = []

        def add_to_path(path, uuid):
            if uuid in path:
                path.append(uuid)
                raise FlowException("Flow cycle detected at runtime: %s" % path)
            path.append(uuid)

        start_time = time.time()
        path = []
        msgs = []

        # lookup our next destination
        handled = False

        while destination:
            result = {"handled": False}

            if destination.get_step_type() == Flow.NODE_TYPE_RULESET:
                should_pause = False

                # check if we need to stop
                if destination.is_pause():
                    should_pause = True

                if user_input or not should_pause:
                    result = Flow.handle_ruleset(destination, run, msg, started_flows, resume_parent_run)
                    add_to_path(path, destination.uuid)

                    # add any messages generated by this ruleset
                    msgs += result.get("msgs", [])

                # if we used this input, then mark our user input as used
                if should_pause:
                    user_input = False

                    # once we handle user input, reset our path
                    path = []

            elif destination.get_step_type() == Flow.NODE_TYPE_ACTIONSET:
                result = Flow.handle_actionset(destination, run, msg, started_flows)
                add_to_path(path, destination.uuid)

                # add any generated messages to be sent at once
                msgs += result.get("msgs", [])

            # if this is a triggered start, we only consider user input on the first step, so clear it now
            if triggered_start:
                user_input = False

            # lookup our next destination
            destination = result.get("destination", None)

            # if any one of our destinations handled us, consider it handled
            if result.get("handled", False):
                handled = True

            resume_parent_run = False

        # if we have a parent to continue, do so
        if getattr(run, "continue_parent", False) and continue_parent:
            msgs += FlowRun.continue_parent_flow_run(run, trigger_send=False, continue_parent=True)

        if handled:
            analytics.gauge("temba.flow_execution", time.time() - start_time)

        # send any messages generated
        if msgs and trigger_send:
            msgs.sort(key=lambda message: message.created_on)
            Msg.objects.filter(id__in=[m.id for m in msgs]).exclude(status=DELIVERED).update(status=PENDING)
            run.flow.org.trigger_send(msgs)

        return handled, msgs

    @classmethod
    def handle_actionset(cls, actionset, run, msg, started_flows):

        # not found, escape out, but we still handled this message, user is now out of the flow
        if not actionset:  # pragma: no cover
            run.set_completed(exit_uuid=None)
            return dict(handled=True, destination=None, destination_type=None)

        # actually execute all the actions in our actionset
        msgs = actionset.execute_actions(run, msg, started_flows)
        run.add_messages([m for m in msgs if not getattr(m, "from_other_run", False)])

        # and onto the destination
        destination = Flow.get_node(actionset.flow, actionset.destination, actionset.destination_type)
        if destination:
            run.flow.add_step(run, destination, exit_uuid=actionset.exit_uuid)
        else:
            run.set_completed(exit_uuid=actionset.exit_uuid)

        return dict(handled=True, destination=destination, msgs=msgs)

    @classmethod
    def handle_ruleset(cls, ruleset, run, msg_in, started_flows, resume_parent_run=False):
        msgs_out = []
        result_input = str(msg_in)

        if ruleset.ruleset_type == RuleSet.TYPE_SUBFLOW:
            if not resume_parent_run:
                flow_uuid = ruleset.config.get("flow").get("uuid")
                flow = Flow.objects.filter(org=run.org, uuid=flow_uuid).first()
                flow.org = run.org
                message_context = run.flow.build_expressions_context(run.contact, msg_in, run=run)

                # our extra will be the current flow variables
                extra = message_context.get("extra", {})
                extra["flow"] = message_context.get("flow", {})

                if msg_in.id:
                    run.add_messages([msg_in])
                    run.update_expiration(timezone.now())

                if flow:
                    child_runs = flow.start(
                        [],
                        [run.contact],
                        started_flows=started_flows,
                        restart_participants=True,
                        extra=extra,
                        parent_run=run,
                        interrupt=False,
                    )

                    child_run = child_runs[0] if child_runs else None

                    if child_run:
                        msgs_out += child_run.start_msgs
                        continue_parent = getattr(child_run, "continue_parent", False)
                    else:  # pragma: no cover
                        continue_parent = False

                    # it's possible that one of our children interrupted us with a start flow action
                    run.refresh_from_db(fields=("is_active",))
                    if continue_parent and run.is_active:
                        started_flows.remove(flow.id)

                        run.child_context = child_run.build_expressions_context(contact_context=str(run.contact.uuid))
                        run.save(update_fields=("child_context",))
                    else:
                        return dict(handled=True, destination=None, destination_type=None, msgs=msgs_out)

            else:
                child_run = FlowRun.objects.filter(parent=run, contact=run.contact).order_by("created_on").last()
                run.child_context = child_run.build_expressions_context(contact_context=str(run.contact.uuid))
                run.save(update_fields=("child_context",))

        # find a matching rule
        result_rule, result_value, result_input = ruleset.find_matching_rule(run, msg_in)

        flow = ruleset.flow

        # add the message to our step
        if msg_in.id:
            run.add_messages([msg_in])
            run.update_expiration(timezone.now())

        if ruleset.ruleset_type in RuleSet.TYPE_MEDIA and msg_in.attachments:
            # store the media path as the value
            result_value = msg_in.attachments[0].split(":", 1)[1]

        ruleset.save_run_value(run, result_rule, result_value, result_input, org=flow.org)

        # no destination for our rule?  we are done, though we did handle this message, user is now out of the flow
        if not result_rule.destination:
            run.set_completed(exit_uuid=result_rule.uuid)
            return dict(handled=True, destination=None, destination_type=None, msgs=msgs_out)

        # Create the step for our destination
        destination = Flow.get_node(flow, result_rule.destination, result_rule.destination_type)
        if destination:
            flow.add_step(run, destination, exit_uuid=result_rule.uuid)

        return dict(handled=True, destination=destination, msgs=msgs_out)

    @classmethod
    def apply_action_label(cls, user, flows, label, add):  # pragma: needs cover
        return label.toggle_label(flows, add)

    @classmethod
    def apply_action_archive(cls, user, flows):
        changed = []

        for flow in flows:

            # don't archive flows that belong to campaigns
            from temba.campaigns.models import CampaignEvent

            if not CampaignEvent.objects.filter(
                is_active=True, flow=flow, campaign__org=user.get_org(), campaign__is_archived=False
            ).exists():
                flow.archive()
                changed.append(flow.pk)

        return changed

    @classmethod
    def apply_action_restore(cls, user, flows):
        changed = []
        for flow in flows:
            try:
                flow.restore()
                changed.append(flow.pk)
            except FlowException:  # pragma: no cover
                pass
        return changed

    @classmethod
    def get_versions_before(cls, version_number):  # pragma: no cover
        # older flows had numeric versions, lets make sure we are dealing with strings
        version_number = Version(f"{version_number}")
        return [v for v in Flow.VERSIONS if Version(v) < version_number]

    @classmethod
    def get_versions_after(cls, version_number):
        # older flows had numeric versions, lets make sure we are dealing with strings
        version_number = Version(f"{version_number}")
        return [v for v in Flow.VERSIONS if Version(v) > version_number]

    def as_select2(self):
        return dict(id=self.uuid, text=self.name)

    def get_trigger_params(self):
        flow_json = self.as_json()
        rule = r"@trigger.params.([a-zA-Z0-9_]+)"
        matches = regex.finditer(rule, json.dumps(flow_json), regex.MULTILINE | regex.IGNORECASE)
        params = [match.group() for match in matches]
        return list(set(params))

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
        lock_key = FLOW_LOCK_KEY % (self.org_id, self.id, FLOW_LOCK_TTL)
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

    def get_images_count(self):
        return self.flow_images.filter(is_active=True).count()

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
        self.save_revision(user, cloned_definition, validate=False)

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

            elif ruleset["ruleset_type"] == RuleSet.TYPE_SHORTEN_URL:
                link_data = None
                if "links" in flow_json:
                    for link in flow_json["links"]:
                        if link["uuid"] == ruleset["config"][RuleSet.TYPE_SHORTEN_URL]["id"]:
                            link_data = link
                            break

                if link_data:
                    created_link = Link.objects.create(
                        org=self.org,
                        name=link_data["name"],
                        destination=link_data["destination"],
                        created_by=self.org.get_user(),
                        modified_by=self.org.get_user(),
                    )
                    ruleset["config"][RuleSet.TYPE_SHORTEN_URL]["id"] = created_link.uuid

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

    def async_start(
        self, user, groups, contacts, query=None, restart_participants=False, include_active=True, params=None
    ):
        """
        Causes us to schedule a flow to start in a background thread.
        """

        flow_start = FlowStart.objects.create(
            flow=self,
            restart_participants=restart_participants,
            include_active=include_active,
            created_by=user,
            modified_by=user,
            query=query,
            extra=params,
        )

        contact_ids = [c.id for c in contacts]
        flow_start.contacts.add(*contact_ids)

        group_ids = [g.id for g in groups]
        flow_start.groups.add(*group_ids)

        on_transaction_commit(lambda: flow_start.async_start())

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
    def get_metadata(cls, flow_info):
        return {
            Flow.METADATA_RESULTS: flow_info[Flow.INSPECT_RESULTS],
            Flow.METADATA_DEPENDENCIES: flow_info[Flow.INSPECT_DEPENDENCIES],
            Flow.METADATA_WAITING_EXIT_UUIDS: flow_info[Flow.INSPECT_WAITING_EXITS],
            Flow.METADATA_PARENT_REFS: flow_info[Flow.INSPECT_PARENT_REFS],
            Flow.METADATA_ISSUES: flow_info[Flow.INSPECT_ISSUES],
        }

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
                self.save_revision(get_flow_user(self.org), flow_def, validate=False)

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

    def save_revision(self, user, definition, validate=True):
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
            self.metadata = Flow.get_metadata(flow_info)
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


@receiver(post_save, sender=Flow)
def update_related_flows(sender, instance, created, **kwargs):
    dependent_flows = instance.dependent_flows.all()
    if created:
        return

    def flow_dependency_filter(dependency):
        if dependency.get("type") == "flow":
            return dependency.get("uuid") == instance.uuid
        return False

    def flow_node_filter(node):
        flow_types = ("enter_flow", "start_session")
        if "actions" in node and len(node["actions"]) > 0 and node["actions"][0]["type"] in flow_types:
            return node["actions"][0]["flow"]["uuid"] == instance.uuid
        return False

    def general_flow_update(flow):
        dependencies = filter(flow_dependency_filter, flow.metadata.get("dependencies", []))
        for dependency in dependencies:
            dependency.update({"name": instance.name})
        flow.save()

    def legacy_flow_update(flow):
        actionsets = flow.action_sets.filter(actions__icontains=instance.uuid)
        for actionset in actionsets:
            actions = actionset.actions
            for action in actions:
                if action.get("type") in ("flow", "trigger-flow") and action.get("flow"):
                    action["flow"].update({"name": instance.name})

        ActionSet.objects.bulk_update(actionsets, ["actions"])

        rulesets = flow.rule_sets.filter(config__icontains=instance.uuid)
        for ruleset in rulesets:
            config = ruleset.config
            if "flow" in config:
                config["flow"].update({"name": instance.name})

        RuleSet.objects.bulk_update(rulesets, ["config"])

    def next_flow_update(flow):
        flow_revision = flow.revisions.order_by("revision").last()
        nodes = filter(flow_node_filter, flow_revision.definition.get("nodes", []))
        for node in nodes:
            action = node["actions"][0]
            action.update({"flow": {"uuid": instance.uuid, "name": instance.name}})
        flow_revision.save()

    for flow in dependent_flows:
        next_flow_update(flow)
        legacy_flow_update(flow)
        general_flow_update(flow)


class FlowImage(models.Model):
    uuid = models.UUIDField(unique=True, default=uuid4)
    org = models.ForeignKey(Org, related_name="flow_images", db_index=False, on_delete=models.CASCADE)
    flow = models.ForeignKey(Flow, related_name="flow_images", on_delete=models.CASCADE)
    contact = models.ForeignKey(Contact, related_name="flow_images", on_delete=models.CASCADE)
    name = models.CharField(help_text="Image name", max_length=255)
    path = models.CharField(help_text="Image URL", max_length=255)
    path_thumbnail = models.CharField(help_text="Image thumbnail URL", max_length=255, null=True)
    exif = models.TextField(blank=True, null=True, help_text=_("A JSON representation the exif"))
    created_on = models.DateTimeField(
        default=timezone.now, editable=False, blank=True, help_text="When this item was originally created"
    )
    modified_on = models.DateTimeField(
        default=timezone.now, editable=False, blank=True, help_text="When this item was last modified"
    )
    is_active = models.BooleanField(
        default=True, help_text="Whether this item is active, use this instead of deleting"
    )

    @classmethod
    def apply_action_archive(cls, user, objects):
        changed = []
        for item in objects:
            item.archive()
            changed.append(item.pk)
        return changed

    @classmethod
    def apply_action_restore(cls, user, objects):
        changed = []
        for item in objects:
            item.restore()
            changed.append(item.pk)
        return changed

    @classmethod
    def apply_action_delete(cls, user, objects):
        changed = []
        for item in objects:
            changed.append(item.pk)
            item.delete()
        return changed

    def archive(self):
        self.is_active = False
        self.save(update_fields=["is_active"])

    def restore(self):
        self.is_active = True
        self.save(update_fields=["is_active"])

    def get_exif(self):
        return json.loads(self.exif) if self.exif else dict()

    def get_url(self):
        if "amazonaws.com" in self.path or self.path.startswith("http"):
            return self.path
        protocol = "https" if settings.IS_PROD else "http"
        image_url = "%s://%s/%s" % (protocol, settings.AWS_BUCKET_DOMAIN, self.path)
        return image_url

    def get_full_path(self):
        url_path = urlparse(self.path)
        if all([url_path.scheme, url_path.netloc]):
            return self.path
        return "%s/%s" % (settings.MEDIA_ROOT, self.path)

    def get_permalink(self):
        protocol = "https" if settings.IS_PROD else "http"
        return "%s://%s%s" % (protocol, settings.HOSTNAME, reverse("flows.flowimage_read", args=[self.uuid]))

    def set_deleted(self):
        self.is_active = False
        self.save(update_fields=["is_active"])

    def is_playable(self):
        extension = self.path.split(".")[-1]
        return True if extension in ["avi", "flv", "wmv", "mp4", "mov", "3gp"] else False

    def get_content_type(self):
        if self.is_playable():
            extension = self.path.split(".")[-1]
            mime_types = {
                "avi": "video/x-msvideo",
                "flv": "video/x-flv",
                "wmv": "video/x-ms-wmv",
                "mp4": "video/mp4",
                "mov": "video/quicktime",
                "3gp": "video/3gpp",
            }
            return mime_types.get(extension, "video/mp4")
        else:
            return None

    def __str__(self):
        return self.name


# Removing images files for Flow Images
@receiver(models.signals.post_delete, sender=FlowImage)
def auto_delete_file_on_delete(sender, instance, **kwargs):
    """
    Deletes file from filesystem
    when corresponding `MediaFile` object is deleted.
    """
    s3 = (
        boto3.resource(
            "s3", aws_access_key_id=settings.AWS_ACCESS_KEY_ID, aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY
        )
        if settings.DEFAULT_FILE_STORAGE == "storages.backends.s3boto3.S3Boto3Storage"
        else None
    )
    if instance.path:
        if os.path.isfile(instance.get_full_path()):
            os.remove(instance.get_full_path())
        elif s3 and "s3.amazonaws.com" in instance.path:
            key = instance.path.replace("https://%s/" % settings.AWS_BUCKET_DOMAIN, "")
            obj = s3.Object(settings.AWS_STORAGE_BUCKET_NAME, key)
            obj.delete()


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
    RESULT_CORRECTED = "corrected"

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
                "corrected": result.get(FlowRun.RESULT_CORRECTED),
            }

        return {
            "id": self.id,
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
    TYPE_LOOKUP = "lookup"
    TYPE_FLOW_FIELD = "flow_field"
    TYPE_FORM_FIELD = "form_field"
    TYPE_CONTACT_FIELD = "contact_field"
    TYPE_EXPRESSION = "expression"
    TYPE_GROUP = "group"
    TYPE_RANDOM = "random"
    TYPE_SUBFLOW = "subflow"
    TYPE_SHORTEN_URL = "shorten_url"

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
        (TYPE_LOOKUP, "Lookup"),
        (TYPE_AIRTIME, "Transfer Airtime"),
        (TYPE_FORM_FIELD, "Split by message form"),
        (TYPE_CONTACT_FIELD, "Split on contact field"),
        (TYPE_EXPRESSION, "Split by expression"),
        (TYPE_RANDOM, "Split Randomly"),
        (TYPE_SHORTEN_URL, "Shorten Trackable Link"),
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

    def get_voice_input(self, voice_response, action=None):

        # recordings aren't wrapped input they get tacked on at the end
        if self.ruleset_type in [RuleSet.TYPE_WAIT_RECORDING, RuleSet.TYPE_SUBFLOW]:
            return voice_response
        elif self.ruleset_type == RuleSet.TYPE_WAIT_DIGITS:
            return voice_response.gather(finish_on_key=self.finished_key, timeout=120, action=action)
        else:
            # otherwise we assume it's single digit entry
            return voice_response.gather(num_digits=1, timeout=120, action=action)

    def is_pause(self):
        return self.ruleset_type in RuleSet.TYPE_WAIT

    def find_matching_rule(self, run, msg):
        orig_text = None
        if msg:
            orig_text = msg.text

        msg.contact = run.contact
        context = run.flow.build_expressions_context(run.contact, msg, run=run)

        if self.ruleset_type in [RuleSet.TYPE_WEBHOOK, RuleSet.TYPE_RESTHOOK]:
            urls = []
            header = {}
            action = "POST"
            resthook = None

            # figure out which URLs will be called
            if self.ruleset_type == RuleSet.TYPE_WEBHOOK:
                resthook = None
                urls = [self.config[RuleSet.CONFIG_WEBHOOK]]
                action = self.config[RuleSet.CONFIG_WEBHOOK_ACTION]

                if RuleSet.CONFIG_WEBHOOK_HEADERS in self.config:
                    headers = self.config[RuleSet.CONFIG_WEBHOOK_HEADERS]
                    for item in headers:
                        header[item.get("name")] = item.get("value")

            elif self.ruleset_type == RuleSet.TYPE_RESTHOOK:
                from temba.api.models import Resthook

                # look up the rest hook
                resthook_slug = self.config[RuleSet.CONFIG_RESTHOOK]
                resthook = Resthook.get_or_create(run.org, resthook_slug, run.flow.created_by)
                urls = resthook.get_subscriber_urls()

                # no urls? use None, as our empty case
                if not urls:
                    urls = [None]

            # track our last successful and failed webhook calls
            last_success, last_failure = None, None

            for url in urls:
                (evaled_url, errors) = Msg.evaluate_template(url, context, org=run.flow.org, url_encode=True)
                result = legacy.call_webhook(run, evaled_url, self, msg, action, resthook=resthook, headers=header)

                # our subscriber is no longer interested, remove this URL as a subscriber
                if resthook and url and result.status_code == 410:
                    resthook.remove_subscriber(url, run.flow.created_by)
                    result.status_code = 200

                if url is None:
                    continue

                as_json = {
                    "input": f"{action} {evaled_url}",
                    "status_code": result.status_code,
                    "body": result.response,
                }

                if 200 <= result.status_code < 300 or result.status_code == 410:
                    last_success = as_json
                else:
                    last_failure = as_json

            # if we have a failed call, use that, if not the last call, if no calls then mock a successful one
            use_call = last_failure or last_success
            if not use_call:
                use_call = {"input": "", "status_code": 200, "body": _("No subscribers to this event")}

            # find our matching rule, we pass in the status from our calls
            for rule in self.get_rules():
                (result, value) = rule.matches(run, msg, context, str(use_call["status_code"]))
                if result > 0:
                    return rule, str(use_call["status_code"]), use_call["input"]

        elif self.ruleset_type == RuleSet.TYPE_SHORTEN_URL:
            url = f"https://firebasedynamiclinks.googleapis.com/v1/shortLinks?key={settings.FDL_API_KEY}"
            headers = {"Content-Type": "application/json"}

            config = self.config[RuleSet.TYPE_SHORTEN_URL]
            item_uuid = config.get("id")
            item = Link.objects.filter(uuid=item_uuid, org=run.flow.org).first()

            if item:
                long_url = "%s?contact=%s" % (item.get_url(), run.contact.uuid)
                data = json.dumps(
                    {"longDynamicLink": "%s/?link=%s" % (settings.FDL_URL, long_url), "suffix": {"option": "SHORT"}}
                )

                response = requests.post(url, data=data, headers=headers, timeout=10)

                for rule in self.get_rules():
                    (result, value) = rule.matches(run, msg, context, str(response.status_code))
                    response_json = response.json()
                    run.update_fields(response_json)
                    if result > 0:
                        short_url = response_json.get("shortLink")
                        return rule, str(response.status_code), short_url

            else:
                return None, None, None

        else:
            # if it's a form field, construct an expression accordingly
            if self.ruleset_type == RuleSet.TYPE_FORM_FIELD:
                delim = self.config.get("field_delimiter", " ")
                self.operand = '@(FIELD(%s, %d, "%s"))' % (
                    self.operand[1:],
                    self.config.get("field_index", 0) + 1,
                    delim,
                )

            # if we have a custom operand, figure that out
            operand = None
            if self.operand:
                (operand, errors) = Msg.evaluate_template(self.operand, context, org=run.flow.org)
            elif msg:
                operand = str(msg)

            if self.ruleset_type == RuleSet.TYPE_AIRTIME:

                airtime = AirtimeTransfer.trigger_airtime_event(self.flow.org, self, run.contact, msg)

                # rebuild our context again, the webhook may have populated something
                context = run.flow.build_expressions_context(run.contact, msg)

                # airtime test evaluate against the status of the airtime
                operand = airtime.status

            elif self.ruleset_type == RuleSet.TYPE_SUBFLOW:
                # lookup the subflow run
                subflow_run = FlowRun.objects.filter(parent=run).order_by("-created_on").first()
                if subflow_run:
                    if subflow_run.exit_type == FlowRun.EXIT_TYPE_COMPLETED:
                        operand = "completed"
                    elif subflow_run.exit_type == FlowRun.EXIT_TYPE_EXPIRED:
                        operand = "expired"

            elif self.ruleset_type == RuleSet.TYPE_GROUP:
                # this won't actually be used by the rules, but will end up in the results
                operand = run.contact.get_display(for_expressions=True) or ""

            try:
                rules = self.get_rules()
                for rule in rules:
                    (result, value) = rule.matches(run, msg, context, operand)
                    if result:
                        # treat category as the base category
                        return rule, value, operand
            finally:
                if msg:
                    msg.text = orig_text

        return None, None, None  # pragma: no cover

    def save_run_value(self, run, rule, raw_value, raw_input, org=None):
        org = org or self.flow.org
        contact_language = run.contact.language if run.contact.language in org.get_language_codes() else None

        run.save_run_result(
            name=self.label,
            node_uuid=self.uuid,
            category=rule.get_category_name(run.flow.base_language),
            category_localized=rule.get_category_name(run.flow.base_language, contact_language),
            raw_value=raw_value,
            raw_input=raw_input,
        )

    def get_step_type(self):
        return Flow.NODE_TYPE_RULESET

    def get_rules_dict(self):
        return self.rules

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
            migrated_flow = mailroom.get_client().flow_migrate(flow_def)
            if version <= Version(Flow.FINAL_LEGACY_VERSION):
                migrated_flow = cls.migrate_issues(migrated_flow)
            migrated_flows.append(migrated_flow)

        exported_json[Org.EXPORT_FLOWS] = migrated_flows

        return exported_json

    @classmethod
    def migrate_issues(cls, flow_definition):
        # hotfix to be able import legacy flows with correct timeout
        for item in flow_definition.get("nodes", []):
            timeout = item.get("router", {}).get("wait", {}).get("timeout")
            if timeout is not None and timeout.get("seconds"):
                timeout["seconds"] //= 60
                item["router"]["wait"]["timeout"].update(timeout)

            for action in item.get("actions", []):
                if action.get("type") == "send_email":
                    for idx, attachment in enumerate(action.get("attachments", [])):
                        [content_type, url] = str(attachment).split(":", 1)
                        url = str(url).replace(
                            "https://attachments", f"https://{settings.AWS_BUCKET_DOMAIN}/attachments"
                        )
                        action["attachments"][idx] = f"{content_type}:{url}"
                elif action.get("type") == "call_giftcard":
                    action["giftcard_type"] = "GIFTCARD_ASSIGNING"

        return flow_definition

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

        for result_field in result_fields:
            field_name, flow_name = result_field["name"], result_field["flow_name"]
            columns.append(f"{field_name} (Category) - {flow_name}")
            columns.append(f"{field_name} (Value) - {flow_name}")
            columns.append(f"{field_name} (Text) - {flow_name}")
            columns.append(f"{field_name} (Corrected) - {flow_name}")

        return columns

    def _add_runs_sheet(self, book, columns):
        name = "Runs (%d)" % (book.num_runs_sheets + 1) if book.num_runs_sheets > 0 else "Runs"
        sheet = book.add_sheet(name, index=book.num_runs_sheets)
        book.num_runs_sheets += 1

        self.append_row(sheet, columns)
        return sheet

    def _add_msgs_sheet(self, book):
        name = "Messages (%d)" % (book.num_msgs_sheets + 1) if book.num_msgs_sheets > 0 else "Messages"
        index = book.num_runs_sheets + book.num_msgs_sheets + book.num_links_sheets
        sheet = book.add_sheet(name, index)
        book.num_msgs_sheets += 1

        headers = ["Contact UUID", "URN", "Name", "Date", "Direction", "Message", "Channel"]

        self.append_row(sheet, headers)
        return sheet

    def _add_links_sheet(self, book):
        name = "Links (%d)" % (book.num_links_sheets + 1) if book.num_links_sheets > 0 else "Links"
        index = book.num_runs_sheets + book.num_msgs_sheets + book.num_links_sheets
        sheet = book.add_sheet(name, index)
        book.num_msgs_sheets += 1

        headers = ["Contact UUID", "Name", "Date", "Destination Link"]

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
        book.num_links_sheets = 0

        # the current sheets
        book.current_runs_sheet = self._add_runs_sheet(book, runs_columns)
        book.current_msgs_sheet = None
        book.current_links_sheet = None

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

        for flow in flows:
            self._write_related_trackable_links(book, flow)

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

        earliest_day = earliest_created_on.date()
        earliest_month = date(earliest_day.year, earliest_day.month, 1)

        archives = (
            Archive.objects.filter(org=self.org, archive_type=Archive.TYPE_FLOWRUN, record_count__gt=0, rollup=None)
            .filter(
                Q(period=Archive.PERIOD_MONTHLY, start_date__gte=earliest_month)
                | Q(period=Archive.PERIOD_DAILY, start_date__gte=earliest_day)
            )
            .order_by("start_date")
        )

        flow_uuids = {str(flow.uuid) for flow in flows}
        last_modified_on = None

        for archive in archives:
            for record_batch in chunk_list(archive.iter_records(), 1000):
                matching = []
                for record in record_batch:
                    modified_on = iso8601.parse_date(record["modified_on"])
                    if last_modified_on is None or last_modified_on < modified_on:
                        last_modified_on = modified_on

                    if record["flow"]["uuid"] in flow_uuids and (not responded_only or record["responded"]):
                        matching.append(record)
                yield matching

        # secondly get runs from database
        runs = FlowRun.objects.filter(flow__in=flows).order_by("modified_on")
        if last_modified_on:
            runs = runs.filter(modified_on__gt=last_modified_on)
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
            yield [run.as_archive_json() for run in run_batch]

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
                node_corrected = node_result.get("corrected", "")
                result_values += [node_category, node_value, node_input, node_corrected]

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
                    msg_channel["name"] if msg_channel else "",
                ],
            )

    def _write_related_trackable_links(self, book, flow):
        additional_filters = {}
        if flow.is_archived:
            additional_filters["created_on__lt"] = flow.modified_on

        links = LinkContacts.objects.filter(
            link__related_flow=flow, is_active=True, **additional_filters
        ).select_related("contact", "link")

        if not links:
            return

        if not book.current_links_sheet or book.current_links_sheet.num_rows >= self.MAX_EXCEL_ROWS:
            book.current_links_sheet = self._add_links_sheet(book)

        for clicked_link in links:
            self.append_row(
                book.current_links_sheet,
                [
                    str(clicked_link.contact.uuid),
                    clicked_link.contact.get_display(),
                    datetime_to_str(
                        clicked_link.created_on, format="%m-%d-%Y %H:%M:%S", tz=clicked_link.link.org.timezone
                    ),
                    clicked_link.link.destination,
                ],
            )


@register_asset_store
class ResultsExportAssetStore(BaseExportAssetStore):
    model = ExportFlowResultsTask
    key = "results_export"
    directory = "results_exports"
    permission = "flows.flow_export_results"
    extensions = ("xlsx",)


class ExportFlowImagesTask(BaseExportTask):
    """
    Container for managing our flow images download requests
    """

    analytics_key = "flowimages_download"
    email_subject = "Your download file from %s is ready"
    email_template = "flowimages/email/flowimages_download"

    files = models.TextField(help_text=_("Array as text of the files ID to download in a zip file"))

    file_path = models.CharField(null=True, help_text=_("Path to downloadable file"), max_length=255)
    file_downloaded = models.NullBooleanField(default=False, help_text=_("If the file was downloaded"))
    cleaned = models.NullBooleanField(default=False, help_text=_("If the file was removed after downloaded"))

    @classmethod
    def create(cls, org, user, files):
        dict_files = json.dumps(dict(files=files))
        return cls.objects.create(org=org, created_by=user, modified_by=user, files=dict_files)

    def write_export(self):
        files = json.loads(self.files)
        files_obj = FlowImage.objects.filter(id__in=files.get("files")).order_by("-created_on")

        stream = BytesIO()
        zf = zipfile.ZipFile(stream, "w")

        for file in files_obj:
            fpath = file.get_full_path()
            url_path = urlparse(fpath)
            if all([url_path.scheme, url_path.netloc]):
                with NamedTemporaryFile(delete=True) as local_copy:
                    local_copy.write(urlopen(fpath).read())
                    local_copy.flush()
                    zf.write(local_copy.name, arcname=os.path.basename(url_path.path))
            else:
                fdir, fname = os.path.split(fpath)
                # Add file, at correct path
                zf.write(fpath, arcname=fname)

        zf.close()

        temp = NamedTemporaryFile(delete=True)
        temp.write(stream.getvalue())
        temp.flush()
        return temp, "zip"


@register_asset_store
class FlowImagesExportAssetStore(BaseExportAssetStore):
    model = ExportFlowImagesTask
    key = "flowimages_download"
    directory = "flowimages_download"
    permission = "flows.flowimage_download"
    extensions = ("zip",)


class FlowStart(models.Model):
    STATUS_PENDING = "P"
    STATUS_STARTING = "S"
    STATUS_COMPLETE = "C"
    STATUS_FAILED = "F"

    STATUS_CHOICES = (
        (STATUS_PENDING, "Pending"),
        (STATUS_STARTING, "Starting"),
        (STATUS_COMPLETE, "Complete"),
        (STATUS_FAILED, "Failed"),
    )

    # the uuid of this start
    uuid = models.UUIDField(unique=True, default=uuid4)

    # the flow that should be started
    flow = models.ForeignKey(Flow, on_delete=models.PROTECT, related_name="starts")

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

    # the parent flow's summary if there is one
    parent_summary = JSONField(null=True)

    # who created this flow start
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT, related_name="%(app_label)s_%(class)s_creations"
    )

    # when this flow start was created
    created_on = models.DateTimeField(default=timezone.now, editable=False)

    # deprecated fields
    is_active = models.BooleanField(default=True, null=True)

    modified_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True)

    modified_on = models.DateTimeField(default=timezone.now, editable=False, null=True)

    contact_count = models.IntegerField(default=0, null=True)

    @classmethod
    def create(
        cls,
        flow,
        user,
        groups=None,
        contacts=None,
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
            flow=flow,
            restart_participants=restart_participants,
            include_active=include_active,
            campaign_event=campaign_event,
            extra=extra,
            created_by=user,
            created_on=timezone.now(),
        )

        for contact in contacts:
            start.contacts.add(contact)

        for group in groups:
            start.groups.add(group)

        return start

    def async_start(self):
        mailroom.queue_flow_start(self)

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
        count = FlowStartCount.objects.filter(start=start).aggregate(count_sum=Sum("count"))["count_sum"]
        return count if count else 0

    @classmethod
    def populate_for_start(cls, start):
        FlowStartCount.objects.filter(start=start).delete()
        return FlowStartCount.objects.create(start=start, count=start.runs.count())

    def __str__(self):  # pragma: needs cover
        return "FlowStartCount[%d:%d]" % (self.start_id, self.count)


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


class Action(object):
    """
    Base class for actions that can be added to an action set and executed during a flow run
    """

    TYPE = "type"
    UUID = "uuid"

    __action_mapping = None

    def __init__(self, uuid):
        self.uuid = uuid if uuid else str(uuid4())

    @classmethod
    def from_json(cls, org, json_obj):
        if not cls.__action_mapping:
            cls.__action_mapping = {
                ReplyAction.TYPE: ReplyAction,
                SendAction.TYPE: SendAction,
                AddToGroupAction.TYPE: AddToGroupAction,
                DeleteFromGroupAction.TYPE: DeleteFromGroupAction,
                AddLabelAction.TYPE: AddLabelAction,
                EmailAction.TYPE: EmailAction,
                SaveToContactAction.TYPE: SaveToContactAction,
                SetLanguageAction.TYPE: SetLanguageAction,
                SetChannelAction.TYPE: SetChannelAction,
                StartFlowAction.TYPE: StartFlowAction,
                SayAction.TYPE: SayAction,
                PlayAction.TYPE: PlayAction,
                TriggerFlowAction.TYPE: TriggerFlowAction,
            }

        action_type = json_obj.get(cls.TYPE)
        if not action_type:  # pragma: no cover
            raise FlowException("Action definition missing 'type' attribute: %s" % json_obj)

        if action_type not in cls.__action_mapping:  # pragma: no cover
            raise FlowException("Unknown action type '%s' in definition: '%s'" % (action_type, json_obj))

        return cls.__action_mapping[action_type].from_json(org, json_obj)

    @classmethod
    def from_json_array(cls, org, json_arr):
        actions = []
        for inner in json_arr:
            action = Action.from_json(org, inner)
            if action:
                actions.append(action)
        return actions


class EmailAction(Action):
    """
    Sends an email to someone
    """

    TYPE = "email"
    EMAILS = "emails"
    SUBJECT = "subject"
    MESSAGE = "msg"

    def __init__(self, uuid, emails, subject, message):
        super().__init__(uuid)

        if not emails:
            raise FlowException("Email actions require at least one recipient")

        self.emails = emails
        self.subject = subject
        self.message = message

    @classmethod
    def from_json(cls, org, json_obj):
        emails = json_obj.get(EmailAction.EMAILS)
        message = json_obj.get(EmailAction.MESSAGE)
        subject = json_obj.get(EmailAction.SUBJECT)
        return cls(json_obj.get(cls.UUID), emails, subject, message)

    def as_json(self):
        return dict(type=self.TYPE, uuid=self.uuid, emails=self.emails, subject=self.subject, msg=self.message)

    def execute(self, run, context, actionset_uuid, msg):
        from .tasks import send_email_action_task

        # build our message from our flow variables
        (message, errors) = Msg.evaluate_template(self.message, context, org=run.flow.org)
        (subject, errors) = Msg.evaluate_template(self.subject, context, org=run.flow.org)

        # make sure the subject is single line; replace '\t\n\r\f\v' to ' '
        subject = regex.sub(r"\s+", " ", subject, regex.V0)

        valid_addresses = []
        invalid_addresses = []
        for email in self.emails:
            if email.startswith("@"):
                # a valid email will contain @ so this is very likely to generate evaluation errors
                (address, errors) = Msg.evaluate_template(email, context, org=run.flow.org)
            else:
                address = email

            address = address.strip()

            if is_valid_address(address):
                valid_addresses.append(address)
            else:
                invalid_addresses.append(address)

        if valid_addresses:
            on_transaction_commit(
                lambda: send_email_action_task.delay(run.flow.org.id, valid_addresses, subject, message)
            )
        return []


class AddToGroupAction(Action):
    """
    Adds the user to a group
    """

    TYPE = "add_group"
    GROUP = "group"
    GROUPS = "groups"

    def __init__(self, uuid, groups):
        super().__init__(uuid)

        self.groups = groups

    @classmethod
    def from_json(cls, org, json_obj):
        return cls(json_obj.get(cls.UUID), cls.get_groups(org, json_obj))

    @classmethod
    def get_groups(cls, org, json_obj):

        # for backwards compatibility
        group_data = json_obj.get(AddToGroupAction.GROUP, None)
        if not group_data:
            group_data = json_obj.get(AddToGroupAction.GROUPS)
        else:
            group_data = [group_data]

        groups = []

        for g in group_data:
            if isinstance(g, dict):
                group_uuid = g.get("uuid", None)
                group_name = g.get("name")

                group = ContactGroup.get_or_create(org, org.created_by, group_name, uuid=group_uuid)
                groups.append(group)
            else:
                if g and g[0] == "@":
                    groups.append(g)
                else:  # pragma: needs cover
                    group = ContactGroup.get_user_group(org, g)
                    if group:
                        groups.append(group)
                    else:
                        groups.append(ContactGroup.create_static(org, org.get_user(), g))
        return groups

    def as_json(self):
        groups = []
        for g in self.groups:
            if isinstance(g, ContactGroup):
                groups.append(dict(uuid=g.uuid, name=g.name))
            else:
                groups.append(g)

        return dict(type=self.get_type(), uuid=self.uuid, groups=groups)

    def get_type(self):
        return AddToGroupAction.TYPE

    def execute(self, run, context, actionset_uuid, msg):
        contact = run.contact
        add = AddToGroupAction.TYPE == self.get_type()
        user = get_flow_user(run.org)

        if contact:
            for group in self.groups:
                if not isinstance(group, ContactGroup):
                    (value, errors) = Msg.evaluate_template(group, context, org=run.flow.org)
                    group = None

                    if not errors:
                        group = ContactGroup.get_user_group(contact.org, value)

                if group:
                    # TODO should become a failure (because it should be impossible) and not just a simulator error
                    if group.is_dynamic:
                        # report to sentry
                        logger.error(
                            "Attempt to add/remove contacts on dynamic group '%s' [%d] "
                            "in flow '%s' [%d] for org '%s' [%d]"
                            % (group.name, group.pk, run.flow.name, run.flow.pk, run.org.name, run.org.pk)
                        )
                        continue  # pragma: can't cover

                    group.org = run.org
                    group.update_contacts(user, [contact], add)

        return []


class DeleteFromGroupAction(AddToGroupAction):
    """
    Removes the user from a group
    """

    TYPE = "del_group"

    def get_type(self):
        return DeleteFromGroupAction.TYPE

    def as_json(self):
        groups = []
        for g in self.groups:
            if isinstance(g, ContactGroup):
                groups.append(dict(uuid=g.uuid, name=g.name))
            else:
                groups.append(g)

        return dict(type=self.get_type(), uuid=self.uuid, groups=groups)

    @classmethod
    def from_json(cls, org, json_obj):
        return cls(json_obj.get(cls.UUID), cls.get_groups(org, json_obj))

    def execute(self, run, context, actionset, msg):
        if len(self.groups) == 0:
            contact = run.contact
            user = get_flow_user(run.org)
            if contact:
                # remove from all active and inactive user-defined, static groups
                for group in ContactGroup.user_groups.filter(
                    org=contact.org, group_type=ContactGroup.TYPE_USER_DEFINED, query__isnull=True
                ):
                    group.update_contacts(user, [contact], False)
            return []
        return AddToGroupAction.execute(self, run, context, actionset, msg)


class AddLabelAction(Action):
    """
    Add a label to the incoming message
    """

    TYPE = "add_label"
    LABELS = "labels"

    def __init__(self, uuid, labels):
        super().__init__(uuid)

        self.labels = labels

    @classmethod
    def from_json(cls, org, json_obj):
        labels_data = json_obj.get(cls.LABELS)

        labels = []
        for label_data in labels_data:
            if isinstance(label_data, dict):
                label_uuid = label_data.get("uuid", None)
                label_name = label_data.get("name")

                if label_uuid and Label.label_objects.filter(org=org, uuid=label_uuid).first():
                    label = Label.label_objects.filter(org=org, uuid=label_uuid).first()
                    if label:
                        labels.append(label)
                else:  # pragma: needs cover
                    labels.append(Label.get_or_create(org, org.get_user(), label_name))

            elif isinstance(label_data, str):
                if label_data and label_data[0] == "@":
                    # label name is a variable substitution
                    labels.append(label_data)
                else:  # pragma: needs cover
                    labels.append(Label.get_or_create(org, org.get_user(), label_data))
            else:  # pragma: needs cover
                raise ValueError("Label data must be a dict or string")

        return cls(json_obj.get(cls.UUID), labels)

    def as_json(self):
        labels = []
        for action_label in self.labels:
            if isinstance(action_label, Label):
                labels.append(dict(uuid=action_label.uuid, name=action_label.name))
            else:
                labels.append(action_label)

        return dict(type=self.get_type(), uuid=self.uuid, labels=labels)

    def get_type(self):
        return AddLabelAction.TYPE

    def execute(self, run, context, actionset_uuid, msg):
        for label in self.labels:
            if not isinstance(label, Label):
                contact = run.contact
                (value, errors) = Msg.evaluate_template(label, context, org=run.flow.org)

                if not errors:
                    label = Label.label_objects.filter(org=contact.org, name__iexact=value.strip()).first()
                else:  # pragma: needs cover
                    label = None

            if label and msg and msg.pk:
                label.toggle_label([msg], True)

        return []


class SayAction(Action):
    """
    Voice action for reading some text to a user
    """

    TYPE = "say"
    MESSAGE = "msg"
    RECORDING = "recording"

    def __init__(self, uuid, msg, recording):
        super().__init__(uuid)

        self.msg = msg
        self.recording = recording

    @classmethod
    def from_json(cls, org, json_obj):
        return cls(json_obj.get(cls.UUID), json_obj.get(cls.MESSAGE), json_obj.get(cls.RECORDING))

    def as_json(self):
        return dict(type=self.TYPE, uuid=self.uuid, msg=self.msg, recording=self.recording)

    def execute(self, run, context, actionset_uuid, event):

        media_url = None
        if self.recording:

            # localize our recording
            recording = run.flow.get_localized_text(self.recording, run.contact)

            # if we have a localized recording, create the url
            if recording:  # pragma: needs cover
                media_url = f"{settings.STORAGE_URL}/{recording}"

        # localize the text for our message, need this either way for logging
        message = run.flow.get_localized_text(self.msg, run.contact)
        (message, errors) = Msg.evaluate_template(message, context)

        msg = run.create_outgoing_ivr(message, media_url, run.connection)

        if msg:
            return [msg]
        else:  # pragma: needs cover
            # no message, possibly failed loop detection
            run.voice_response.say(_("Sorry, an invalid flow has been detected. Good bye."))
            return []


class PlayAction(Action):
    """
    Voice action for reading some text to a user
    """

    TYPE = "play"
    URL = "url"

    def __init__(self, uuid, url):
        super().__init__(uuid)

        self.url = url

    @classmethod
    def from_json(cls, org, json_obj):
        return cls(json_obj.get(cls.UUID), json_obj.get(cls.URL))

    def as_json(self):
        return dict(type=self.TYPE, uuid=self.uuid, url=self.url)

    def execute(self, run, context, actionset_uuid, event):
        (recording_url, errors) = Msg.evaluate_template(self.url, context)
        msg = run.create_outgoing_ivr(_("Played contact recording"), recording_url, run.connection)

        if msg:
            return [msg]
        else:  # pragma: needs cover
            # no message, possibly failed loop detection
            run.voice_response.say(_("Sorry, an invalid flow has been detected. Good bye."))
            return []


class ReplyAction(Action):
    """
    Simple action for sending back a message
    """

    TYPE = "reply"
    MESSAGE = "msg"
    MSG_TYPE = None
    MEDIA = "media"
    SEND_ALL = "send_all"
    QUICK_REPLIES = "quick_replies"

    def __init__(self, uuid, msg=None, media=None, quick_replies=None, send_all=False):
        super().__init__(uuid)

        self.msg = msg
        self.media = media if media else {}
        self.send_all = send_all
        self.quick_replies = quick_replies if quick_replies else []

    @classmethod
    def from_json(cls, org, json_obj):
        # assert we have some kind of message in this reply
        msg = json_obj.get(cls.MESSAGE)
        if isinstance(msg, dict):
            if not msg:
                raise FlowException("Invalid reply action, empty message dict")

            if not any([v for v in msg.values()]):
                raise FlowException("Invalid reply action, missing at least one message")
        elif not msg:
            raise FlowException("Invalid reply action, no message")

        return cls(
            json_obj.get(cls.UUID),
            msg=json_obj.get(cls.MESSAGE),
            media=json_obj.get(cls.MEDIA, None),
            quick_replies=json_obj.get(cls.QUICK_REPLIES),
            send_all=json_obj.get(cls.SEND_ALL, False),
        )

    def as_json(self):
        return dict(
            type=self.TYPE,
            uuid=self.uuid,
            msg=self.msg,
            media=self.media,
            quick_replies=self.quick_replies,
            send_all=self.send_all,
        )

    @staticmethod
    def get_translated_quick_replies(metadata, run):
        """
        Gets the appropriate metadata translation for the given contact
        """
        language_metadata = []
        for item in metadata:
            text = run.flow.get_localized_text(text_translations=item, contact=run.contact)
            language_metadata.append(text)

        return language_metadata

    def execute(self, run, context, actionset_uuid, msg):
        replies = []

        if self.msg or self.media:
            user = get_flow_user(run.org)

            text = ""
            if self.msg:
                text = run.flow.get_localized_text(self.msg, run.contact)

            quick_replies = []
            if self.quick_replies:
                quick_replies = ReplyAction.get_translated_quick_replies(self.quick_replies, run)

            attachments = None
            if self.media:
                # localize our media attachment
                media_type, media_url = run.flow.get_localized_text(self.media, run.contact).split(":", 1)

                # if we have a localized media, create the url
                if media_url and len(media_type.split("/")) > 1:
                    abs_url = f"{settings.STORAGE_URL}/{media_url}"
                    attachments = [f"{media_type}:{abs_url}"]
                else:
                    attachments = [f"{media_type}:{media_url}"]

            if msg and msg.id:
                replies = msg.reply(
                    text,
                    user,
                    trigger_send=False,
                    expressions_context=context,
                    connection=run.connection,
                    msg_type=self.MSG_TYPE,
                    quick_replies=quick_replies,
                    attachments=attachments,
                    send_all=self.send_all,
                    sent_on=None,
                )
            else:
                # if our run has been responded to or any of our parent runs have
                # been responded to consider us interactive with high priority
                high_priority = run.get_session_responded()
                replies = run.contact.send(
                    text,
                    user,
                    trigger_send=False,
                    expressions_context=context,
                    connection=run.connection,
                    msg_type=self.MSG_TYPE,
                    attachments=attachments,
                    quick_replies=quick_replies,
                    sent_on=None,
                    all_urns=self.send_all,
                    high_priority=high_priority,
                )
        return replies


class VariableContactAction(Action):
    """
    Base action that resolves variables into contacts. Used for actions that take
    SendAction, TriggerAction, etc
    """

    CONTACTS = "contacts"
    GROUPS = "groups"
    VARIABLES = "variables"
    PHONE = "phone"
    PATH = "path"
    SCHEME = "scheme"
    URNS = "urns"
    NAME = "name"
    ID = "id"

    def __init__(self, uuid, groups, contacts, variables):
        super().__init__(uuid)

        self.groups = groups
        self.contacts = contacts
        self.variables = variables

    @classmethod
    def parse_groups(cls, org, json_obj):
        # we actually instantiate our contacts here
        groups = []
        for group_data in json_obj.get(VariableContactAction.GROUPS):
            group_uuid = group_data.get(VariableContactAction.UUID, None)
            group_name = group_data.get(VariableContactAction.NAME)

            # flows from when true deletion was allowed need this
            if not group_name:
                group_name = "Missing"

            group = ContactGroup.get_or_create(org, org.get_user(), group_name, uuid=group_uuid)
            groups.append(group)

        return groups

    @classmethod
    def parse_contacts(cls, org, json_obj):
        contacts = []
        for contact in json_obj.get(VariableContactAction.CONTACTS):
            name = contact.get(VariableContactAction.NAME, None)
            phone = contact.get(VariableContactAction.PHONE, None)
            contact_uuid = contact.get(VariableContactAction.UUID, None)

            urns = []
            for urn in contact.get(VariableContactAction.URNS, []):
                scheme = urn.get(VariableContactAction.SCHEME)
                path = urn.get(VariableContactAction.PATH)

                if scheme and path:
                    urns.append(URN.from_parts(scheme, path))

            if phone:  # pragma: needs cover
                urns.append(URN.from_tel(phone))

            contact = Contact.objects.filter(uuid=contact_uuid, org=org).first()

            if not contact:
                contact = Contact.get_or_create_by_urns(org, org.created_by, name=None, urns=urns)

                # if they don't have a name use the one in our action
                if name and not contact.name:  # pragma: needs cover
                    contact.name = name
                    contact.save(update_fields=["name"], handle_update=True)

            if contact:
                contacts.append(contact)

        return contacts

    @classmethod
    def parse_variables(cls, org, json_obj):
        variables = []
        if VariableContactAction.VARIABLES in json_obj:
            variables = list(_.get(VariableContactAction.ID) for _ in json_obj.get(VariableContactAction.VARIABLES))
        return variables

    def build_groups_and_contacts(self, run, msg):
        expressions_context = run.flow.build_expressions_context(run.contact, msg, run=run)
        contacts = list(self.contacts)
        groups = list(self.groups)

        # see if we've got groups or contacts
        for variable in self.variables:
            # this is a marker for a new contact
            if variable == "@new_contact":
                contacts.append(Contact.get_or_create_by_urns(run.org, get_flow_user(run.org), name=None, urns=()))

            # other type of variable, perform our substitution
            else:
                (variable, errors) = Msg.evaluate_template(variable, expressions_context, org=run.flow.org)

                # Check for possible contact uuid and use its contact
                contact_variable_by_uuid = Contact.objects.filter(uuid=variable, org=run.flow.org).first()
                if contact_variable_by_uuid:
                    contacts.append(contact_variable_by_uuid)
                    continue

                variable_group = ContactGroup.get_user_group(run.flow.org, name=variable)
                if variable_group:  # pragma: needs cover
                    groups.append(variable_group)
                else:
                    country = run.flow.org.get_country_code()
                    (number, valid) = URN.normalize_number(variable, country)
                    if number and valid:
                        contact, contact_urn = Contact.get_or_create(
                            run.org, URN.from_tel(number), user=get_flow_user(run.org)
                        )
                        contacts.append(contact)

        return groups, contacts


class TriggerFlowAction(VariableContactAction):
    """
    Action that starts a set of contacts down another flow
    """

    TYPE = "trigger-flow"

    def __init__(self, uuid, flow, groups, contacts, variables):
        super().__init__(uuid, groups, contacts, variables)

        self.flow = flow

    @classmethod
    def from_json(cls, org, json_obj):
        flow_json = json_obj.get("flow")
        flow_uuid = flow_json.get("uuid")
        flow = Flow.objects.filter(org=org, is_active=True, is_archived=False, uuid=flow_uuid).first()

        # it is possible our flow got deleted
        if not flow:
            return None

        groups = VariableContactAction.parse_groups(org, json_obj)
        contacts = VariableContactAction.parse_contacts(org, json_obj)
        variables = VariableContactAction.parse_variables(org, json_obj)

        return cls(json_obj.get(cls.UUID), flow, groups, contacts, variables)

    def as_json(self):
        contact_ids = [dict(uuid=_.uuid, name=_.name) for _ in self.contacts]
        group_ids = [dict(uuid=_.uuid, name=_.name) for _ in self.groups]
        variables = [dict(id=_) for _ in self.variables]

        return dict(
            type=self.TYPE,
            uuid=self.uuid,
            flow=dict(uuid=self.flow.uuid, name=self.flow.name),
            contacts=contact_ids,
            groups=group_ids,
            variables=variables,
        )

    def execute(self, run, context, actionset_uuid, msg):
        if self.flow:
            (groups, contacts) = self.build_groups_and_contacts(run, msg)
            # start our contacts down the flow
            # our extra will be our flow variables in our message context
            extra = context.get("extra", dict())
            child_runs = self.flow.start(
                groups, contacts, restart_participants=True, started_flows=[run.flow.pk], extra=extra, parent_run=run
            )

            # build up all the msgs that where sent by our flow
            msgs = []
            for run in child_runs:
                msgs += run.start_msgs

            return msgs
        else:  # pragma: no cover
            return []


class SetLanguageAction(Action):
    """
    Action that sets the language for a contact
    """

    TYPE = "lang"
    LANG = "lang"
    NAME = "name"

    def __init__(self, uuid, lang, name):
        super().__init__(uuid)

        self.lang = lang
        self.name = name

    @classmethod
    def from_json(cls, org, json_obj):
        return cls(json_obj.get(cls.UUID), json_obj.get(cls.LANG), json_obj.get(cls.NAME))

    def as_json(self):
        return dict(type=self.TYPE, uuid=self.uuid, lang=self.lang, name=self.name)

    def execute(self, run, context, actionset_uuid, msg):
        old_value = run.contact.language

        if len(self.lang) != 3:
            new_lang = None
        else:
            new_lang = self.lang

        if old_value != new_lang:
            run.contact.language = new_lang
            run.contact.save(update_fields=["language"], handle_update=True)

        return []


class StartFlowAction(Action):
    """
    Action that starts the contact into another flow
    """

    TYPE = "flow"
    FLOW = "flow"
    NAME = "name"

    def __init__(self, uuid, flow):
        super().__init__(uuid)

        self.flow = flow

    @classmethod
    def from_json(cls, org, json_obj):
        flow_obj = json_obj.get(cls.FLOW)
        flow_uuid = flow_obj.get("uuid")

        flow = Flow.objects.filter(org=org, is_active=True, is_archived=False, uuid=flow_uuid).first()

        # it is possible our flow got deleted
        if not flow:
            return None
        else:
            return cls(json_obj.get(cls.UUID), flow)

    def as_json(self):
        return dict(type=self.TYPE, uuid=self.uuid, flow=dict(uuid=self.flow.uuid, name=self.flow.name))

    def execute(self, run, context, actionset_uuid, msg, started_flows):
        msgs = []

        # our extra will be our flow variables in our message context
        extra = context.get("extra", dict())

        # if they are both flow runs, just redirect the call
        if run.flow.flow_type == Flow.TYPE_VOICE and self.flow.flow_type == Flow.TYPE_VOICE:
            new_run = self.flow.start(
                [], [run.contact], started_flows=started_flows, restart_participants=True, extra=extra, parent_run=run
            )[0]
            url = "https://%s%s" % (
                new_run.org.get_brand_domain(),
                reverse("ivr.ivrcall_handle", args=[new_run.connection.pk]),
            )
            run.voice_response.redirect(url)
        else:
            child_runs = self.flow.start(
                [], [run.contact], started_flows=started_flows, restart_participants=True, extra=extra, parent_run=run
            )
            for run in child_runs:
                for msg in run.start_msgs:
                    msg.from_other_run = True
                    msgs.append(msg)

        return msgs


class SaveToContactAction(Action):
    """
    Action to save a variable substitution to a field on a contact
    """

    TYPE = "save"
    FIELD = "field"
    LABEL = "label"
    VALUE = "value"

    def __init__(self, uuid, label, field, value):
        super().__init__(uuid)

        self.label = label
        self.field = field
        self.value = value

    @classmethod
    def get_label(cls, org, field, label=None):

        # make sure this field exists
        if field == "name":
            label = "Contact Name"
        elif field == "first_name":
            label = "First Name"
        elif field == "tel_e164":
            label = "Phone Number"
        elif field in ContactURN.CONTEXT_KEYS_TO_SCHEME.keys():
            label = str(ContactURN.CONTEXT_KEYS_TO_LABEL[field])
        else:
            contact_field = ContactField.user_fields.filter(org=org, key=field).first()

            if not contact_field:
                contact_field = ContactField.get_or_create(org, get_flow_user(org), field, label)

            label = contact_field.label

        return label

    @classmethod
    def from_json(cls, org, json_obj):
        # they are creating a new field
        label = json_obj.get(cls.LABEL)
        field = json_obj.get(cls.FIELD)
        value = json_obj.get(cls.VALUE)

        if label and label.startswith("[_NEW_]"):
            label = label[7:]

        # create our contact field if necessary
        if not field:
            field = ContactField.make_key(label)

        # look up our label
        label = cls.get_label(org, field, label)

        return cls(json_obj.get(cls.UUID), label, field, value)

    def as_json(self):
        return dict(type=self.TYPE, uuid=self.uuid, label=self.label, field=self.field, value=self.value)

    def execute(self, run, context, actionset_uuid, msg):
        # evaluate our value
        contact = run.contact
        user = get_flow_user(run.org)
        (value, errors) = Msg.evaluate_template(self.value, context, org=run.flow.org)

        value = value.strip()

        if self.field == "name":
            new_value = value[:128]
            contact.name = new_value
            contact.modified_by = user
            contact.save(update_fields=("name", "modified_by", "modified_on"), handle_update=True)

        elif self.field == "first_name":
            new_value = value[:128]
            contact.set_first_name(new_value)
            contact.modified_by = user
            contact.save(update_fields=("name", "modified_by", "modified_on"), handle_update=True)

        elif self.field in ContactURN.CONTEXT_KEYS_TO_SCHEME.keys():
            new_value = value[:128]

            # add in our new urn number
            scheme = ContactURN.CONTEXT_KEYS_TO_SCHEME[self.field]

            # trim off '@' for twitter handles
            if self.field == "twitter":  # pragma: needs cover
                if len(new_value) > 0:
                    if new_value[0] == "@":
                        new_value = new_value[1:]

            # only valid urns get added, sorry
            new_urn = None
            if new_value:
                new_urn = URN.normalize(URN.from_parts(scheme, new_value))
                if not URN.validate(new_urn, contact.org.get_country_code()):  # pragma: no cover
                    new_urn = False

            if new_urn:
                urns = [str(urn) for urn in contact.urns.all()]
                urns += [new_urn]
                contact.update_urns(user, urns)

        else:
            new_value = value[: Value.MAX_VALUE_LEN]
            contact.set_field(user, self.field, new_value)

        return []


class SetChannelAction(Action):
    """
    Action which sets the preferred channel to use for this Contact. If the contact has no URNs that match
    the Channel being set then this is a no-op.
    """

    TYPE = "channel"
    CHANNEL = "channel"
    NAME = "name"

    def __init__(self, uuid, channel):
        super().__init__(uuid)

        self.channel = channel

    @classmethod
    def from_json(cls, org, json_obj):
        channel_uuid = json_obj.get(SetChannelAction.CHANNEL)

        if channel_uuid:
            channel = Channel.objects.filter(org=org, is_active=True, uuid=channel_uuid).first()
        else:  # pragma: needs cover
            channel = None
        return cls(json_obj.get(cls.UUID), channel)

    def as_json(self):
        channel_uuid = self.channel.uuid if self.channel else None
        channel_name = (
            "%s: %s" % (self.channel.get_channel_type_display(), self.channel.get_address_display())
            if self.channel
            else None
        )
        return dict(type=self.TYPE, uuid=self.uuid, channel=channel_uuid, name=channel_name)

    def execute(self, run, context, actionset_uuid, msg):
        # if we found the channel to set
        if self.channel:
            run.contact.set_preferred_channel(self.channel)
            return []
        else:
            return []


class SendAction(VariableContactAction):
    """
    Action which sends a message to a specified set of contacts and groups.
    """

    TYPE = "send"
    MESSAGE = "msg"
    MEDIA = "media"

    def __init__(self, uuid, msg, groups, contacts, variables, media=None):
        super().__init__(uuid, groups, contacts, variables)

        self.msg = msg
        self.media = media if media else {}

    @classmethod
    def from_json(cls, org, json_obj):
        groups = VariableContactAction.parse_groups(org, json_obj)
        contacts = VariableContactAction.parse_contacts(org, json_obj)
        variables = VariableContactAction.parse_variables(org, json_obj)

        return cls(
            json_obj.get(cls.UUID),
            json_obj.get(cls.MESSAGE),
            groups,
            contacts,
            variables,
            json_obj.get(cls.MEDIA, None),
        )

    def as_json(self):
        contact_ids = [dict(uuid=_.uuid) for _ in self.contacts]
        group_ids = [dict(uuid=_.uuid, name=_.name) for _ in self.groups]
        variables = [dict(id=_) for _ in self.variables]

        return dict(
            type=self.TYPE,
            uuid=self.uuid,
            msg=self.msg,
            contacts=contact_ids,
            groups=group_ids,
            variables=variables,
            media=self.media,
        )

    def execute(self, run, context, actionset_uuid, msg):
        if self.msg or self.media:
            flow = run.flow
            (groups, contacts) = self.build_groups_and_contacts(run, msg)

            # no-op if neither text nor media are defined in the flow base language
            if not (self.msg.get(flow.base_language) or self.media.get(flow.base_language)):
                return list()

            broadcast = Broadcast.create(
                flow.org,
                flow.modified_by,
                self.msg,
                groups=groups,
                contacts=contacts,
                media=self.media,
                base_language=flow.base_language,
            )
            broadcast.send(expressions_context=context)

        return []


class Rule(object):
    def __init__(self, uuid, category, destination, destination_type, test, label=None):
        self.uuid = uuid
        self.category = category
        self.destination = destination
        self.destination_type = destination_type
        self.test = test
        self.label = label

    def get_category_name(self, flow_lang, contact_lang=None):
        if not self.category:  # pragma: needs cover
            if isinstance(self.test, BetweenTest):
                return "%s-%s" % (self.test.min, self.test.max)

        # return the category name for the flow language version
        if isinstance(self.category, dict):
            category = None
            if contact_lang:
                category = self.category.get(contact_lang)

            if not category and flow_lang:
                category = self.category.get(flow_lang)

            if not category:  # pragma: needs cover
                category = list(self.category.values())[0]

            return category

        return self.category  # pragma: needs cover

    def matches(self, run, sms, context, text):
        return self.test.evaluate(run, sms, context, text)

    def as_json(self):
        return dict(
            uuid=self.uuid,
            category=self.category,
            destination=self.destination,
            destination_type=self.destination_type,
            test=self.test.as_json(),
            label=self.label,
        )

    @classmethod
    def from_json_array(cls, org, json):
        rules = []
        for rule in json:
            category = rule.get("category", None)

            if isinstance(category, dict):
                # prune all of our translations to 36
                for k, v in category.items():
                    if isinstance(v, str):
                        category[k] = v[:36]
            elif category:
                category = category[:36]

            destination = rule.get("destination", None)
            destination_type = None

            # determine our destination type, if its not set its an action set
            if destination:
                destination_type = rule.get("destination_type", Flow.NODE_TYPE_ACTIONSET)

            rules.append(
                Rule(
                    rule.get("uuid"),
                    category,
                    destination,
                    destination_type,
                    Test.from_json(org, rule["test"]),
                    rule.get("label"),
                )
            )

        return rules


class Test(object):
    TYPE = "type"
    __test_mapping = None

    @classmethod
    def from_json(cls, org, json_dict):
        if not cls.__test_mapping:
            cls.__test_mapping = {
                AirtimeStatusTest.TYPE: AirtimeStatusTest,
                AndTest.TYPE: AndTest,
                BetweenTest.TYPE: BetweenTest,
                ContainsAnyTest.TYPE: ContainsAnyTest,
                ContainsOnlyPhraseTest.TYPE: ContainsOnlyPhraseTest,
                ContainsPhraseTest.TYPE: ContainsPhraseTest,
                ContainsTest.TYPE: ContainsTest,
                DateAfterTest.TYPE: DateAfterTest,
                DateBeforeTest.TYPE: DateBeforeTest,
                DateEqualTest.TYPE: DateEqualTest,
                EqTest.TYPE: EqTest,
                FalseTest.TYPE: FalseTest,
                GtTest.TYPE: GtTest,
                GteTest.TYPE: GteTest,
                DateTest.TYPE: DateTest,
                HasDistrictTest.TYPE: HasDistrictTest,
                HasEmailTest.TYPE: HasEmailTest,
                HasStateTest.TYPE: HasStateTest,
                HasWardTest.TYPE: HasWardTest,
                InGroupTest.TYPE: InGroupTest,
                LtTest.TYPE: LtTest,
                LteTest.TYPE: LteTest,
                NotEmptyTest.TYPE: NotEmptyTest,
                NumberTest.TYPE: NumberTest,
                OrTest.TYPE: OrTest,
                PhoneTest.TYPE: PhoneTest,
                PhotoTest.TYPE: PhotoTest,
                RegexTest.TYPE: RegexTest,
                StartsWithTest.TYPE: StartsWithTest,
                SubflowTest.TYPE: SubflowTest,
                TimeoutTest.TYPE: TimeoutTest,
                TrueTest.TYPE: TrueTest,
                WebhookStatusTest.TYPE: WebhookStatusTest,
            }

        type = json_dict.get(cls.TYPE, None)
        if not type:  # pragma: no cover
            raise FlowException("Test definition missing 'type' field: %s", json_dict)

        if type not in cls.__test_mapping:  # pragma: no cover
            raise FlowException("Unknown type: '%s' in definition: %s" % (type, json_dict))

        return cls.__test_mapping[type].from_json(org, json_dict)

    @classmethod
    def from_json_array(cls, org, json):
        tests = []
        for inner in json:
            tests.append(Test.from_json(org, inner))

        return tests

    def evaluate(self, run, sms, context, text):  # pragma: no cover
        """
        Where the work happens, subclasses need to be able to evalute their Test
        according to their definition given the passed in message. Tests do not have
        side effects.
        """
        raise FlowException(
            "Subclasses must implement evaluate, returning a tuple containing 1 or 0 and the value tested"
        )


class PhotoTest(Test):
    """
    Test for whether a response contains a photo
    """

    TYPE = "image"

    def __init__(self):
        pass

    @classmethod
    def from_json(cls, org, json):
        return cls()

    def as_json(self):  # pragma: needs cover
        return dict(type=self.TYPE)

    def evaluate(self, run, sms, context, text):
        image_url = None
        image = None
        text_split = []
        org = run.flow.org
        has_attachment = 1 if sms.attachments and len(sms.attachments) > 0 else 0

        if has_attachment:
            text_split = sms.attachments[0].split(":", 1)
            image = text_split[1]
            is_image = 1 if "image" in text_split[0] or "mp4" in text_split[0] else 0
        else:
            is_image = 0

        if is_image and not run.contact.is_test:
            if settings.DEFAULT_FILE_STORAGE == "storages.backends.s3boto3.S3Boto3Storage":
                media_path = image
                image = Org.get_temporary_file_from_url(media_url=image)
                image_path = image.file.name
                thumbnail_path = media_path
            else:
                media_path = image.split("media", 1)[1]
                image_path = "%s%s" % (settings.MEDIA_ROOT, media_path)
                media_path = media_path.replace("/", "", 1)
                thumbnail_path = image_path

            if text_split and "image" in text_split[0]:
                media_thumbnail = get_thumbnail(thumbnail_path, "50x50", crop="center", quality=99, format="PNG")
                media_thumbnail_path = media_thumbnail.url
            else:
                media_thumbnail_path = None

            try:
                img = Image.open(image_path)
                exif_data = img._getexif()
            except Exception:
                exif_data = {}

            exif = {ExifTags.TAGS[k]: v for k, v in exif_data.items() if k in ExifTags.TAGS} if exif_data else {}

            try:
                exif = json.dumps(exif)
            except Exception:
                exif = None

            file_name = media_path.split("/", -1)[-1]
            image_args = dict(
                org=org,
                flow=run.flow,
                contact=run.contact,
                path=media_path,
                exif=exif,
                path_thumbnail=media_thumbnail_path,
                name=file_name,
            )
            flow_image = FlowImage.objects.create(**image_args)
            image_url = flow_image.get_url()
        elif is_image:
            text_split = sms.attachments[0].split(":", 1)
            image_url = text_split[1]

        return is_image, image_url


class WebhookStatusTest(Test):
    """
    {op: 'webhook', status: 'success' }
    """

    TYPE = "webhook_status"
    STATUS = "status"

    STATUS_SUCCESS = "success"
    STATUS_FAILURE = "failure"

    def __init__(self, status):
        self.status = status

    @classmethod
    def from_json(cls, org, json):
        return WebhookStatusTest(json.get("status"))

    def as_json(self):  # pragma: needs cover
        return dict(type=WebhookStatusTest.TYPE, status=self.status)

    def evaluate(self, run, sms, context, text):
        # we treat any 20* return code as successful
        success = 200 <= int(text) < 300

        if success and self.status == WebhookStatusTest.STATUS_SUCCESS:
            return 1, text
        elif not success and self.status == WebhookStatusTest.STATUS_FAILURE:
            return 1, text
        else:
            return 0, None


class AirtimeStatusTest(Test):
    """
    {op: 'airtime_status'}
    """

    TYPE = "airtime_status"
    EXIT = "exit_status"

    STATUS_SUCCESS = "success"
    STATUS_FAILED = "failed"

    STATUS_MAP = {STATUS_SUCCESS: AirtimeTransfer.STATUS_SUCCESS, STATUS_FAILED: AirtimeTransfer.STATUS_FAILED}

    def __init__(self, exit_status):
        self.exit_status = exit_status

    @classmethod
    def from_json(cls, org, json):
        return AirtimeStatusTest(json.get("exit_status"))

    def as_json(self):  # pragma: needs cover
        return dict(type=AirtimeStatusTest.TYPE, exit_status=self.exit_status)

    def evaluate(self, run, sms, context, text):
        status = text
        if status and AirtimeStatusTest.STATUS_MAP[self.exit_status] == status:
            return 1, status
        return 0, None


class InGroupTest(Test):
    """
    { op: "in_group" }
    """

    TYPE = "in_group"
    NAME = "name"
    UUID = "uuid"
    TEST = "test"

    def __init__(self, group):
        self.group = group

    @classmethod
    def from_json(cls, org, json):
        group = json.get(InGroupTest.TEST)
        name = group.get(InGroupTest.NAME)
        uuid = group.get(InGroupTest.UUID)
        return InGroupTest(ContactGroup.get_or_create(org, org.created_by, name, uuid=uuid))

    def as_json(self):
        group = ContactGroup.get_or_create(
            self.group.org, self.group.org.created_by, self.group.name, uuid=self.group.uuid
        )
        return dict(type=InGroupTest.TYPE, test=dict(name=group.name, uuid=group.uuid))

    def evaluate(self, run, sms, context, text):
        if run.contact.user_groups.filter(id=self.group.id).first():
            return 1, self.group.name
        return 0, None


class SubflowTest(Test):
    """
    { op: "subflow" }
    """

    TYPE = "subflow"
    EXIT = "exit_type"

    TYPE_COMPLETED = "completed"
    TYPE_EXPIRED = "expired"

    def __init__(self, exit_type):
        self.exit_type = exit_type

    @classmethod
    def from_json(cls, org, json):
        return SubflowTest(json.get(SubflowTest.EXIT))

    def as_json(self):  # pragma: needs cover
        return dict(type=SubflowTest.TYPE, exit_type=self.exit_type)

    def evaluate(self, run, sms, context, text):
        if self.exit_type == text:
            return 1, self.exit_type
        return 0, None


class TimeoutTest(Test):
    """
    { op: "timeout", minutes: 60 }
    """

    TYPE = "timeout"
    MINUTES = "minutes"

    def __init__(self, minutes):
        self.minutes = minutes

    @classmethod
    def from_json(cls, org, json):
        return TimeoutTest(float(json.get(TimeoutTest.MINUTES)))

    def as_json(self):  # pragma: no cover
        return {"type": TimeoutTest.TYPE, TimeoutTest.MINUTES: self.minutes}

    def evaluate(self, run, sms, context, text):  # pragma: no cover
        pass


class TrueTest(Test):
    """
    { op: "true" }
    """

    TYPE = "true"

    def __init__(self):
        pass

    @classmethod
    def from_json(cls, org, json):
        return TrueTest()

    def as_json(self):
        return dict(type=TrueTest.TYPE)

    def evaluate(self, run, sms, context, text):
        return 1, text


class FalseTest(Test):
    """
    { op: "false" }
    """

    TYPE = "false"

    def __init__(self):
        pass

    @classmethod
    def from_json(cls, org, json):
        return FalseTest()

    def as_json(self):
        return dict(type=FalseTest.TYPE)

    def evaluate(self, run, sms, context, text):
        return 0, None


class AndTest(Test):
    """
    { op: "and",  "tests": [ ... ] }
    """

    TESTS = "tests"
    TYPE = "and"

    def __init__(self, tests):
        self.tests = tests

    @classmethod
    def from_json(cls, org, json):
        return AndTest(Test.from_json_array(org, json[cls.TESTS]))

    def as_json(self):
        return dict(type=AndTest.TYPE, tests=[_.as_json() for _ in self.tests])

    def evaluate(self, run, sms, context, text):  # pragma: needs cover
        matches = []
        for test in self.tests:
            (result, value) = test.evaluate(run, sms, context, text)
            if result:
                matches.append(value)
            else:
                return 0, None

        # all came out true, we are true
        return 1, " ".join(matches)


class OrTest(Test):
    """
    { op: "or",  "tests": [ ... ] }
    """

    TESTS = "tests"
    TYPE = "or"

    def __init__(self, tests):
        self.tests = tests

    @classmethod
    def from_json(cls, org, json):
        return OrTest(Test.from_json_array(org, json[cls.TESTS]))

    def as_json(self):
        return dict(type=OrTest.TYPE, tests=[_.as_json() for _ in self.tests])

    def evaluate(self, run, sms, context, text):  # pragma: needs cover
        for test in self.tests:
            (result, value) = test.evaluate(run, sms, context, text)
            if result:
                return result, value

        return 0, None


class NotEmptyTest(Test):
    """
    { op: "not_empty" }
    """

    TYPE = "not_empty"

    def __init__(self):  # pragma: needs cover
        pass

    @classmethod
    def from_json(cls, org, json):  # pragma: needs cover
        return NotEmptyTest()

    def as_json(self):  # pragma: needs cover
        return dict(type=NotEmptyTest.TYPE)

    def evaluate(self, run, sms, context, text):  # pragma: needs cover
        if text and len(text.strip()):
            return 1, text.strip()
        return 0, None


class ContainsTest(Test):
    """
    { op: "contains", "test": "red" }
    """

    TEST = "test"
    TYPE = "contains"

    def __init__(self, test):
        self.test = test

    @classmethod
    def from_json(cls, org, json):
        return cls(json[cls.TEST])

    def as_json(self):
        json = dict(type=ContainsTest.TYPE, test=self.test)
        return json

    def test_in_words(self, test, words, raw_words):
        matches = []
        for index, word in enumerate(words):
            if word == test:
                matches.append(index)
                continue

        return matches

    def evaluate(self, run, sms, context, text):
        # substitute any variables
        test = run.flow.get_localized_text(self.test, run.contact)
        test, errors = Msg.evaluate_template(test, context, org=run.flow.org)

        # tokenize our test
        tests = tokenize(test.lower())

        # tokenize our sms
        words = tokenize(text.lower())
        raw_words = tokenize(text)

        tests = [elt for elt in tests if elt != ""]
        words = [elt for elt in words if elt != ""]
        raw_words = [elt for elt in raw_words if elt != ""]

        # run through each of our tests
        matches = set()
        matched_tests = 0
        for test in tests:
            match = self.test_in_words(test, words, raw_words)
            if match:
                matched_tests += 1
                matches.update(match)

        # we are a match only if every test matches
        if matched_tests == len(tests):
            matches = sorted(list(matches))
            matched_words = " ".join([raw_words[idx] for idx in matches])
            return len(tests), matched_words
        else:
            return 0, None


class HasEmailTest(Test):
    """
    { op: "has_email" }
    """

    TYPE = "has_email"

    def __init__(self):
        pass

    @classmethod
    def from_json(cls, org, json):
        return cls()

    def as_json(self):
        return dict(type=self.TYPE)

    def evaluate(self, run, sms, context, text):
        # split on whitespace
        words = text.split()
        for word in words:
            word = word.strip(",.;:|()[]\"'<>?&*/\\")
            if is_valid_address(word):
                return 1, word

        return 0, None


class ContainsAnyTest(ContainsTest):
    """
    { op: "contains_any", "test": "red" }
    """

    TEST = "test"
    TYPE = "contains_any"

    def as_json(self):
        return dict(type=ContainsAnyTest.TYPE, test=self.test)

    def evaluate(self, run, sms, context, text):
        # substitute any variables
        test = run.flow.get_localized_text(self.test, run.contact)
        test, errors = Msg.evaluate_template(test, context, org=run.flow.org)

        # tokenize our test
        tests = tokenize(test.lower())

        # tokenize our sms
        words = tokenize(text.lower())
        raw_words = tokenize(text)

        tests = [elt for elt in tests if elt != ""]
        words = [elt for elt in words if elt != ""]
        raw_words = [elt for elt in raw_words if elt != ""]

        # run through each of our tests
        matches = set()
        for test in tests:
            match = self.test_in_words(test, words, raw_words)
            if match:
                matches.update(match)

        # we are a match if at least one test matches
        if matches:
            matches = sorted(list(matches))
            matched_words = " ".join([raw_words[idx] for idx in matches])
            return 1, matched_words
        else:
            return 0, None


class ContainsOnlyPhraseTest(ContainsTest):
    """
    { op: "contains_only_phrase", "test": "red" }
    """

    TEST = "test"
    TYPE = "contains_only_phrase"

    def as_json(self):
        return dict(type=ContainsOnlyPhraseTest.TYPE, test=self.test)

    def evaluate(self, run, sms, context, text):
        # substitute any variables
        test = run.flow.get_localized_text(self.test, run.contact)
        test, errors = Msg.evaluate_template(test, context, org=run.flow.org)

        # tokenize our test
        tests = tokenize(test.lower())

        # tokenize our sms
        words = tokenize(text.lower())
        raw_words = tokenize(text)

        # they are the same? then we matched
        if tests == words:
            return 1, " ".join(raw_words)
        else:
            return 0, None


class ContainsPhraseTest(ContainsTest):
    """
    { op: "contains_phrase", "test": "red" }
    """

    TEST = "test"
    TYPE = "contains_phrase"

    def as_json(self):
        return dict(type=ContainsPhraseTest.TYPE, test=self.test)

    def evaluate(self, run, sms, context, text):
        # substitute any variables
        test = run.flow.get_localized_text(self.test, run.contact)
        test, errors = Msg.evaluate_template(test, context, org=run.flow.org)

        # tokenize our test
        tests = tokenize(test.lower())
        if not tests:
            return True, ""

        # tokenize our sms
        words = tokenize(text.lower())
        raw_words = tokenize(text)

        # look for the phrase
        test_idx = 0
        matches = []
        for i in range(len(words)):
            if tests[test_idx] == words[i]:
                matches.append(raw_words[i])
                test_idx += 1
                if test_idx == len(tests):
                    break
            else:
                matches = []
                test_idx = 0

        # we found the phrase
        if test_idx == len(tests):
            matched_words = " ".join(matches)
            return 1, matched_words
        else:
            return 0, None


class StartsWithTest(Test):
    """
    { op: "starts", "test": "red" }
    """

    TEST = "test"
    TYPE = "starts"

    def __init__(self, test):
        self.test = test

    @classmethod
    def from_json(cls, org, json):
        return cls(json[cls.TEST])

    def as_json(self):  # pragma: needs cover
        return dict(type=StartsWithTest.TYPE, test=self.test)

    def evaluate(self, run, sms, context, text):
        # substitute any variables in our test
        test = run.flow.get_localized_text(self.test, run.contact)
        test, errors = Msg.evaluate_template(test, context, org=run.flow.org)

        # strip leading and trailing whitespace
        text = text.strip()

        # see whether we start with our test
        if text.lower().find(test.lower()) == 0:
            return 1, text[: len(test)]
        else:
            return 0, None


class HasStateTest(Test):
    TYPE = "state"

    def __init__(self):
        pass

    @classmethod
    def from_json(cls, org, json):
        return cls()

    def as_json(self):
        return dict(type=self.TYPE)

    def evaluate(self, run, sms, context, text):
        org = run.flow.org

        # if they removed their country since adding the rule
        if not org.country:
            return 0, None

        state = org.parse_location(text, AdminBoundary.LEVEL_STATE)
        if state:
            return 1, state[0]

        return 0, None


class HasDistrictTest(Test):
    TYPE = "district"
    TEST = "test"

    def __init__(self, state=None):
        self.state = state

    @classmethod
    def from_json(cls, org, json):
        return cls(json[cls.TEST])

    def as_json(self):
        return dict(type=self.TYPE, test=self.state)

    def evaluate(self, run, sms, context, text):

        # if they removed their country since adding the rule
        org = run.flow.org
        if not org.country:
            return 0, None

        # evaluate our district in case it has a replacement variable
        state, errors = Msg.evaluate_template(self.state, context, org=run.flow.org)

        parent = org.parse_location(state, AdminBoundary.LEVEL_STATE)
        if parent:
            district = org.parse_location(text, AdminBoundary.LEVEL_DISTRICT, parent[0])
            if district:
                return 1, district[0]
        district = org.parse_location(text, AdminBoundary.LEVEL_DISTRICT)

        # parse location when state contraint is not provided or available
        if (errors or not state) and len(district) == 1:
            return 1, district[0]

        return 0, None


class HasWardTest(Test):
    TYPE = "ward"
    STATE = "state"
    DISTRICT = "district"

    def __init__(self, state=None, district=None):
        self.state = state
        self.district = district

    @classmethod
    def from_json(cls, org, json):
        return cls(json[cls.STATE], json[cls.DISTRICT])

    def as_json(self):
        return dict(type=self.TYPE, state=self.state, district=self.district)

    def evaluate(self, run, sms, context, text):
        # if they removed their country since adding the rule
        org = run.flow.org
        if not org.country:  # pragma: needs cover
            return 0, None
        district = None

        # evaluate our district in case it has a replacement variable
        district_name, missing_district = Msg.evaluate_template(self.district, context, org=run.flow.org)
        state_name, missing_state = Msg.evaluate_template(self.state, context, org=run.flow.org)
        if (district_name and state_name) and (len(missing_district) == 0 and len(missing_state) == 0):
            state = org.parse_location(state_name, AdminBoundary.LEVEL_STATE)
            if state:
                district = org.parse_location(district_name, AdminBoundary.LEVEL_DISTRICT, state[0])
                if district:
                    ward = org.parse_location(text, AdminBoundary.LEVEL_WARD, district[0])
                    if ward:
                        return 1, ward[0]

        # parse location when district contraint is not provided or available
        ward = org.parse_location(text, AdminBoundary.LEVEL_WARD)
        if len(ward) == 1 and district is None:
            return 1, ward[0]

        return 0, None


class DateTest(Test):
    """
    Base class for those tests that check relative dates
    """

    TEST = None
    TYPE = "date"

    def __init__(self, test=None):
        self.test = test

    @classmethod
    def from_json(cls, org, json):
        if cls.TEST:
            return cls(json[cls.TEST])
        else:
            return cls()

    def as_json(self):
        if self.test:
            return dict(type=self.TYPE, test=self.test)
        else:
            return dict(type=self.TYPE)

    def evaluate_date_test(self, date_message, date_test):
        return date_message is not None

    def evaluate(self, run, sms, context, text):
        org = run.flow.org
        day_first = org.get_dayfirst()
        tz = org.timezone

        test, errors = Msg.evaluate_template(self.test, context, org=org)
        if not errors:
            date_message = str_to_datetime(text, tz=tz, dayfirst=day_first)
            date_test = str_to_datetime(test, tz=tz, dayfirst=day_first)

            if self.evaluate_date_test(date_message, date_test):
                return 1, date_message.astimezone(tz)

        return 0, None


class DateEqualTest(DateTest):
    TEST = "test"
    TYPE = "date_equal"

    def evaluate_date_test(self, date_message, date_test):
        return date_message and date_test and date_message.date() == date_test.date()


class DateAfterTest(DateTest):
    TEST = "test"
    TYPE = "date_after"

    def evaluate_date_test(self, date_message, date_test):
        return date_message and date_test and date_message >= date_test


class DateBeforeTest(DateTest):
    TEST = "test"
    TYPE = "date_before"

    def evaluate_date_test(self, date_message, date_test):
        return date_message and date_test and date_message <= date_test


class NumericTest(Test):
    """
    Base class for those tests that do numeric tests.
    """

    TEST = "test"
    TYPE = ""

    @classmethod
    def convert_to_decimal(cls, word):
        try:
            return (word, Decimal(word))
        except Exception as e:
            # does this start with a number?  just use that part if so
            match = regex.match(r"^[$£€]?([\d,][\d,\.]*([\.,]\d+)?)\D*$", word, regex.UNICODE | regex.V0)

            if match:
                return (match.group(1), Decimal(match.group(1)))
            else:
                raise e

    # test every word in the message against our test
    def evaluate(self, run, sms, context, text):
        text = text.replace(",", "")
        for word in regex.split(r"\s+", text, flags=regex.UNICODE | regex.V0):
            try:
                (word, decimal) = NumericTest.convert_to_decimal(word)
                if self.evaluate_numeric_test(run, context, decimal):
                    return 1, decimal
            except Exception:  # pragma: needs cover
                pass
        return 0, None


class BetweenTest(NumericTest):
    """
    Test whether we are between two numbers (inclusive)
    """

    MIN = "min"
    MAX = "max"
    TYPE = "between"

    def __init__(self, min_val, max_val):
        self.min = min_val
        self.max = max_val

    @classmethod
    def from_json(cls, org, json):
        return cls(json[cls.MIN], json[cls.MAX])

    def as_json(self):
        return dict(type=self.TYPE, min=self.min, max=self.max)

    def evaluate_numeric_test(self, run, context, decimal_value):
        min_val, min_errors = Msg.evaluate_template(self.min, context, org=run.flow.org)
        max_val, max_errors = Msg.evaluate_template(self.max, context, org=run.flow.org)

        if not min_errors and not max_errors:
            try:
                return Decimal(min_val) <= decimal_value <= Decimal(max_val)
            except Exception:  # pragma: needs cover
                pass

        return False  # pragma: needs cover


class NumberTest(NumericTest):
    """
    Tests that there is any number in the string.
    """

    TYPE = "number"

    def __init__(self):
        pass

    @classmethod
    def from_json(cls, org, json):
        return cls()

    def as_json(self):  # pragma: needs cover
        return dict(type=self.TYPE)

    def evaluate_numeric_test(self, run, context, decimal_value):
        return True


class SimpleNumericTest(NumericTest):
    """
    Base class for those tests that do a numeric test with a single value
    """

    TEST = "test"
    TYPE = ""

    def __init__(self, test):
        self.test = test

    @classmethod
    def from_json(cls, org, json):
        return cls(json[cls.TEST])

    def as_json(self):
        return dict(type=self.TYPE, test=self.test)

    def evaluate_numeric_test(self, message_numeric, test_numeric):  # pragma: no cover
        raise FlowException("Evaluate numeric test needs to be defined by subclass")

    # test every word in the message against our test
    def evaluate(self, run, sms, context, text):
        test, errors = Msg.evaluate_template(str(self.test), context, org=run.flow.org)

        text = text.replace(",", "")
        for word in regex.split(r"\s+", text, flags=regex.UNICODE | regex.V0):
            try:
                (word, decimal) = NumericTest.convert_to_decimal(word)
                if self.evaluate_numeric_test(decimal, Decimal(test)):
                    return 1, decimal
            except Exception:
                pass
        return 0, None


class GtTest(SimpleNumericTest):
    TEST = "test"
    TYPE = "gt"

    def evaluate_numeric_test(self, message_numeric, test_numeric):
        return message_numeric > test_numeric


class GteTest(SimpleNumericTest):
    TEST = "test"
    TYPE = "gte"

    def evaluate_numeric_test(self, message_numeric, test_numeric):
        return message_numeric >= test_numeric


class LtTest(SimpleNumericTest):
    TEST = "test"
    TYPE = "lt"

    def evaluate_numeric_test(self, message_numeric, test_numeric):
        return message_numeric < test_numeric


class LteTest(SimpleNumericTest):
    TEST = "test"
    TYPE = "lte"

    def evaluate_numeric_test(self, message_numeric, test_numeric):  # pragma: needs cover
        return message_numeric <= test_numeric


class EqTest(SimpleNumericTest):
    TEST = "test"
    TYPE = "eq"

    def evaluate_numeric_test(self, message_numeric, test_numeric):
        return message_numeric == test_numeric


class PhoneTest(Test):
    """
    Test for whether a response contains a phone number
    """

    TYPE = "phone"

    def __init__(self):
        pass

    @classmethod
    def from_json(cls, org, json):
        return cls()

    def as_json(self):  # pragma: needs cover
        return dict(type=self.TYPE)

    def evaluate(self, run, sms, context, text):
        org = run.flow.org

        # try to find a phone number in the text we have been sent
        country_code = org.get_country_code()
        if not country_code:  # pragma: needs cover
            country_code = "US"

        number = None
        matches = phonenumbers.PhoneNumberMatcher(text, country_code)

        # try it as an international number if we failed
        if not matches.has_next():  # pragma: needs cover
            matches = phonenumbers.PhoneNumberMatcher("+" + text, country_code)

        for match in matches:
            number = phonenumbers.format_number(match.number, phonenumbers.PhoneNumberFormat.E164)

        return number, number


class RegexTest(Test):  # pragma: needs cover
    """
    Test for whether a response matches a regular expression
    """

    TEST = "test"
    TYPE = "regex"

    def __init__(self, test):
        self.test = test

    @classmethod
    def from_json(cls, org, json):
        return cls(json[cls.TEST])

    def as_json(self):
        return dict(type=self.TYPE, test=self.test)

    def evaluate(self, run, sms, context, text):
        try:
            test = run.flow.get_localized_text(self.test, run.contact)

            # check whether we match
            rexp = regex.compile(test, regex.UNICODE | regex.IGNORECASE | regex.MULTILINE | regex.V0)
            match = rexp.search(text)

            # if so, $0 will be what we return
            if match:
                return_match = match.group(0)

                # build up a dictionary that contains indexed group matches
                group_dict = {}
                for idx in range(rexp.groups + 1):
                    group_dict[str(idx)] = match.group(idx)

                # set it on run@extra
                run.update_fields(group_dict)

                # return all matched values
                return True, return_match

        except Exception as e:
            logger.error(f"Unable to evaluate RegexTest: {str(e)}", exc_info=True)

        return False, None
