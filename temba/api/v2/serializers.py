import logging
import numbers
from collections import OrderedDict
from datetime import timezone as tzone

import iso8601
import pycountry
import regex
from rest_framework import serializers

from django.conf import settings
from django.contrib.auth.models import User

from temba import mailroom
from temba.archives.models import Archive
from temba.campaigns.models import Campaign, CampaignEvent
from temba.channels.models import Channel, ChannelEvent
from temba.classifiers.models import Classifier
from temba.contacts.models import URN, Contact, ContactField, ContactGroup, ContactNote, ContactURN
from temba.flows.models import Flow, FlowRun, FlowStart
from temba.globals.models import Global
from temba.locations.models import AdminBoundary
from temba.mailroom import modifiers
from temba.msgs.models import Broadcast, Label, Media, Msg, OptIn
from temba.orgs.models import Org, OrgRole
from temba.tickets.models import Ticket, Topic
from temba.utils import json
from temba.utils.fields import NameValidator

from ..models import BulkActionFailure, Resthook, ResthookSubscriber, WebHookEvent
from ..validators import UniqueForOrgValidator
from . import fields

INVALID_EXTRA_KEY_CHARS = regex.compile(r"[^a-zA-Z0-9_]")

logger = logging.getLogger(__name__)


def format_datetime(value):
    """
    Datetime fields are formatted with microsecond accuracy for v2
    """
    return json.encode_datetime(value, micros=True) if value else None


def normalize_extra(extra):
    """
    Normalizes a dict of extra passed to the flow start endpoint. We need to do this for backwards compatibility with
    old engine.
    """

    return _normalize_extra(extra, -1)[0]


def _normalize_extra(extra, count):
    def normalize_key(key):
        return INVALID_EXTRA_KEY_CHARS.sub("_", key)[:255]

    if isinstance(extra, str):
        return extra[:640], count + 1

    elif isinstance(extra, numbers.Number) or isinstance(extra, bool):
        return extra, count + 1

    elif isinstance(extra, dict):
        count += 1
        normalized = OrderedDict()
        for k, v in extra.items():
            (normalized[normalize_key(k)], count) = _normalize_extra(v, count)

            if count >= settings.FLOW_START_PARAMS_SIZE:
                break

        return normalized, count

    elif isinstance(extra, list):
        count += 1
        normalized = OrderedDict()
        for i, v in enumerate(extra):
            (normalized[str(i)], count) = _normalize_extra(v, count)

            if count >= settings.FLOW_START_PARAMS_SIZE:
                break

        return normalized, count

    elif extra is None:
        return "", count + 1

    else:  # pragma: no cover
        raise ValueError("Unsupported type %s in extra" % str(type(extra)))


class ReadSerializer(serializers.ModelSerializer):
    """
    We deviate slightly from regular REST framework usage with distinct serializers for reading and writing
    """

    def save(self, **kwargs):  # pragma: no cover
        raise ValueError("Can't call save on a read serializer")


class WriteSerializer(serializers.Serializer):
    """
    The normal REST framework way is to have the view decide if it's an update on existing instance or a create for a
    new instance. Since our logic for that gets relatively complex, we have the serializer make that call.
    """

    def run_validation(self, data=serializers.empty):
        if not isinstance(data, dict):
            raise serializers.ValidationError(
                detail={"non_field_errors": ["Request body should be a single JSON object"]}
            )

        if self.context["org"].is_flagged or self.context["org"].is_suspended:
            msg = Org.BLOCKER_FLAGGED if self.context["org"].is_flagged else Org.BLOCKER_SUSPENDED
            raise serializers.ValidationError(detail={"non_field_errors": [msg]})

        return super().run_validation(data)


# ============================================================
# Serializers (A-Z)
# ============================================================


class AdminBoundaryReadSerializer(ReadSerializer):
    parent = serializers.SerializerMethodField()
    aliases = serializers.SerializerMethodField()
    geometry = serializers.SerializerMethodField()

    def get_parent(self, obj):
        return {"osm_id": obj.parent.osm_id, "name": obj.parent.name} if obj.parent else None

    def get_aliases(self, obj):
        return [alias.name for alias in obj.aliases.all()]

    def get_geometry(self, obj):
        if self.context["include_geometry"] and obj.simplified_geometry:
            return json.loads(obj.simplified_geometry.geojson)
        else:
            return None

    class Meta:
        model = AdminBoundary
        fields = ("osm_id", "name", "parent", "level", "aliases", "geometry")


class ArchiveReadSerializer(ReadSerializer):
    period = serializers.SerializerMethodField()
    download_url = serializers.SerializerMethodField()

    PERIODS = {Archive.PERIOD_DAILY: "daily", Archive.PERIOD_MONTHLY: "monthly"}

    def get_period(self, obj):
        return self.PERIODS.get(obj.period)

    def get_download_url(self, obj):
        return obj.get_download_link()

    class Meta:
        model = Archive
        fields = ("archive_type", "start_date", "period", "record_count", "size", "hash", "download_url")


class BroadcastReadSerializer(ReadSerializer):
    STATUSES = {
        Broadcast.STATUS_PENDING: "pending",
        Broadcast.STATUS_QUEUED: "queued",
        Broadcast.STATUS_STARTED: "started",
        Broadcast.STATUS_COMPLETED: "completed",
        Broadcast.STATUS_FAILED: "failed",
        Broadcast.STATUS_INTERRUPTED: "interrupted",
    }

    status = serializers.SerializerMethodField()
    progress = serializers.SerializerMethodField()
    urns = serializers.SerializerMethodField()
    contacts = fields.ContactField(many=True)
    groups = fields.ContactGroupField(many=True)
    text = serializers.SerializerMethodField()
    attachments = serializers.SerializerMethodField()
    base_language = fields.LanguageField()
    created_on = serializers.DateTimeField(default_timezone=tzone.utc)

    def get_text(self, obj):
        return {lang: trans.get("text") for lang, trans in obj.translations.items()}

    def get_attachments(self, obj):
        return {lang: trans.get("attachments", []) for lang, trans in obj.translations.items()}

    def get_status(self, obj):
        return self.STATUSES[obj.status]

    def get_progress(self, obj):
        return {"total": obj.contact_count or -1, "started": obj.msg_count}

    def get_urns(self, obj):
        if self.context["org"].is_anon:
            return None
        else:
            return obj.urns or []

    class Meta:
        model = Broadcast
        fields = (
            "id",
            "status",
            "progress",
            "urns",
            "contacts",
            "groups",
            "text",
            "attachments",
            "base_language",
            "created_on",
        )


class BroadcastWriteSerializer(WriteSerializer):
    urns = serializers.ListField(required=False, child=fields.URNField(), max_length=100)
    contacts = fields.ContactField(many=True, required=False)
    groups = fields.ContactGroupField(many=True, required=False)
    text = fields.TranslatedTextField(required=False, max_length=Msg.MAX_TEXT_LEN)
    attachments = fields.TranslatedAttachmentsField(required=False)
    base_language = fields.LanguageField(required=False)

    def validate(self, data):
        text = data.get("text")
        attachments = data.get("attachments")
        base_language = data.get("base_language")

        if not (data.get("text") or data.get("attachments")):
            raise serializers.ValidationError("Must provide either text or attachments.")

        if not (data.get("urns") or data.get("contacts") or data.get("groups")):
            raise serializers.ValidationError("Must provide either urns, contacts or groups.")

        if base_language:
            if base_language not in text:
                raise serializers.ValidationError("No text translation provided in base language.")

            if attachments and base_language not in attachments:
                raise serializers.ValidationError("No attachment translations provided in base language.")

        return data

    def save(self):
        """
        Create a new broadcast to send out
        """
        base_language = self.validated_data.get("base_language")

        text = self.validated_data.get("text")
        attachments = self.validated_data.get("attachments", {})

        # merge text and attachments into single dict of translations
        translations = {}
        if text:
            translations = {lang: {"text": t} for lang, t in text.items()}

        if attachments:
            for lang, atts in attachments.items():
                if lang not in translations:
                    translations[lang] = {}

                # TODO update broadcast sending to allow media objects to stay as UUIDs for longer
                translations[lang]["attachments"] = [str(m) for m in atts]

        if not base_language:
            base_language = next(iter(translations))

        return Broadcast.create(
            self.context["org"],
            self.context["user"],
            translations,
            base_language=base_language,
            groups=self.validated_data.get("groups", []),
            contacts=self.validated_data.get("contacts", []),
            urns=self.validated_data.get("urns", []),
        )


class ChannelEventReadSerializer(ReadSerializer):
    TYPES = {t[0]: t[2] for t in ChannelEvent.TYPE_CONFIG}

    type = serializers.SerializerMethodField()
    contact = fields.ContactField()
    channel = fields.ChannelField()
    extra = serializers.SerializerMethodField()
    created_on = serializers.DateTimeField(default_timezone=tzone.utc)
    occurred_on = serializers.DateTimeField(default_timezone=tzone.utc)

    def get_type(self, obj):
        return self.TYPES.get(obj.event_type)

    def get_extra(self, obj):
        return obj.extra

    class Meta:
        model = ChannelEvent
        fields = ("id", "type", "contact", "channel", "extra", "occurred_on", "created_on")


class CampaignReadSerializer(ReadSerializer):
    archived = serializers.ReadOnlyField(source="is_archived")
    group = fields.ContactGroupField()
    created_on = serializers.DateTimeField(default_timezone=tzone.utc)

    class Meta:
        model = Campaign
        fields = ("uuid", "name", "archived", "group", "created_on")


class CampaignWriteSerializer(WriteSerializer):
    name = serializers.CharField(
        required=True,
        max_length=Campaign.MAX_NAME_LEN,
        validators=[
            NameValidator(Campaign.MAX_NAME_LEN),
            UniqueForOrgValidator(queryset=Campaign.objects.filter(is_active=True)),
        ],
    )
    group = fields.ContactGroupField(required=True)

    def save(self):
        """
        Create or update our campaign
        """
        name = self.validated_data.get("name")
        group = self.validated_data.get("group")

        if self.instance:
            self.instance.name = name
            self.instance.group = group
            self.instance.save(update_fields=("name", "group"))
        else:
            self.instance = Campaign.create(self.context["org"], self.context["user"], name, group)

        return self.instance


class CampaignEventReadSerializer(ReadSerializer):
    UNITS = {
        CampaignEvent.UNIT_MINUTES: "minutes",
        CampaignEvent.UNIT_HOURS: "hours",
        CampaignEvent.UNIT_DAYS: "days",
        CampaignEvent.UNIT_WEEKS: "weeks",
    }

    campaign = fields.CampaignField()
    flow = serializers.SerializerMethodField()
    relative_to = fields.ContactFieldField()
    unit = serializers.SerializerMethodField()
    created_on = serializers.DateTimeField(default_timezone=tzone.utc)

    def get_flow(self, obj):
        if obj.event_type == CampaignEvent.TYPE_FLOW:
            return {"uuid": obj.flow.uuid, "name": obj.flow.name}
        else:
            return None

    def get_unit(self, obj):
        return self.UNITS[obj.unit]

    class Meta:
        model = CampaignEvent
        fields = (
            "uuid",
            "campaign",
            "relative_to",
            "offset",
            "unit",
            "delivery_hour",
            "flow",
            "message",
            "created_on",
        )


class CampaignEventWriteSerializer(WriteSerializer):
    UNITS = {v: k for k, v in CampaignEventReadSerializer.UNITS.items()}

    campaign = fields.CampaignField(required=True)
    offset = serializers.IntegerField(required=True)
    unit = serializers.ChoiceField(required=True, choices=list(UNITS.keys()))
    delivery_hour = serializers.IntegerField(required=True, min_value=-1, max_value=23)
    relative_to = fields.ContactFieldField(required=True)
    message = fields.TranslatedTextField(required=False, max_length=Msg.MAX_TEXT_LEN)
    flow = fields.FlowField(required=False)

    def validate_unit(self, value):
        return self.UNITS[value]

    def validate_campaign(self, value):
        if self.instance and value and self.instance.campaign != value:
            raise serializers.ValidationError("Cannot change campaign for existing events")
        return value

    def validate_message(self, value):
        if value and not value.get(self.context["org"].flow_languages[0]):
            raise serializers.ValidationError("Message text in default flow language is required.")

        return value

    def validate(self, data):
        message = data.get("message")
        flow = data.get("flow")

        if (message and flow) or (not message and not flow):
            raise serializers.ValidationError("Flow or a message text required.")

        return data

    def save(self):
        """
        Create or update our campaign event
        """

        org = self.context["org"]
        user = self.context["user"]
        base_language = org.flow_languages[0]

        campaign = self.validated_data.get("campaign")
        offset = self.validated_data.get("offset")
        unit = self.validated_data.get("unit")
        delivery_hour = self.validated_data.get("delivery_hour")
        relative_to = self.validated_data.get("relative_to")
        message = self.validated_data.get("message")
        flow = self.validated_data.get("flow")

        if self.instance:
            self.instance = self.instance.recreate()  # don't update but re-create to invalidate existing event fires

            # we are being set to a flow
            if flow:
                self.instance.flow = flow
                self.instance.event_type = CampaignEvent.TYPE_FLOW
                self.instance.message = None

            # we are being set to a message
            else:
                self.instance.message = message

                # if we aren't currently a message event, we need to create our hidden message flow
                if self.instance.event_type != CampaignEvent.TYPE_MESSAGE:
                    self.instance.flow = Flow.create_single_message(org, user, message, base_language)
                    self.instance.event_type = CampaignEvent.TYPE_MESSAGE

                # otherwise, we can just update that flow
                else:
                    # set our single message on our flow
                    self.instance.flow.update_single_message_flow(user, message, base_language)

            # update our other attributes
            self.instance.offset = offset
            self.instance.unit = unit
            self.instance.delivery_hour = delivery_hour
            self.instance.relative_to = relative_to
            self.instance.save()
            self.instance.update_flow_name()

        else:
            if flow:
                self.instance = CampaignEvent.create_flow_event(
                    org, user, campaign, relative_to, offset, unit, flow, delivery_hour
                )
            else:
                self.instance = CampaignEvent.create_message_event(
                    org, user, campaign, relative_to, offset, unit, message, delivery_hour, base_language
                )

            self.instance.update_flow_name()

        # create our event fires for this event in the background
        self.instance.schedule_async()

        return self.instance


class ChannelReadSerializer(ReadSerializer):
    country = serializers.SerializerMethodField()
    device = serializers.SerializerMethodField()
    created_on = serializers.DateTimeField(default_timezone=tzone.utc)
    last_seen = serializers.DateTimeField(default_timezone=tzone.utc)

    def get_country(self, obj):
        return str(obj.country) if obj.country else None

    def get_device(self, obj):
        if not obj.is_android:
            return None

        return {
            "name": obj.device,
            "power_level": obj.last_sync.power_level if obj.last_sync else -1,
            "power_status": obj.last_sync.power_status if obj.last_sync else None,
            "power_source": obj.last_sync.power_source if obj.last_sync else None,
            "network_type": obj.last_sync.network_type if obj.last_sync else None,
        }

    class Meta:
        model = Channel
        fields = ("uuid", "name", "address", "country", "device", "last_seen", "created_on")


class ClassifierReadSerializer(ReadSerializer):
    type = serializers.SerializerMethodField()
    intents = serializers.SerializerMethodField()
    created_on = serializers.DateTimeField(default_timezone=tzone.utc)

    def get_type(self, obj):
        return obj.classifier_type

    def get_intents(self, obj):
        return [i.name for i in obj.intents.filter(is_active=True).order_by("name")]

    class Meta:
        model = Classifier
        fields = ("uuid", "name", "type", "intents", "created_on")


class ContactReadSerializer(ReadSerializer):
    STATUSES = {
        Contact.STATUS_ACTIVE: "active",
        Contact.STATUS_BLOCKED: "blocked",
        Contact.STATUS_STOPPED: "stopped",
        Contact.STATUS_ARCHIVED: "archived",
    }

    name = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()
    language = serializers.SerializerMethodField()
    flow = fields.FlowField(source="current_flow")
    urns = serializers.SerializerMethodField()
    groups = serializers.SerializerMethodField()
    fields = serializers.SerializerMethodField("get_contact_fields")
    notes = serializers.SerializerMethodField()
    created_on = serializers.DateTimeField(default_timezone=tzone.utc)
    modified_on = serializers.DateTimeField(default_timezone=tzone.utc)
    last_seen_on = serializers.DateTimeField(default_timezone=tzone.utc)

    blocked = serializers.SerializerMethodField()  # deprecated
    stopped = serializers.SerializerMethodField()  # deprecated

    def __init__(self, *args, context, **kwargs):
        super().__init__(*args, context=context, **kwargs)

        # remove anon_display field if org isn't anon
        if not context["org"].is_anon:
            self.fields.pop("anon_display")

    def get_name(self, obj):
        return obj.name if obj.is_active else None

    def get_status(self, obj):
        return self.STATUSES[obj.status] if obj.is_active else None

    def get_language(self, obj):
        return obj.language if obj.is_active else None

    def get_urns(self, obj):
        if not obj.is_active:
            return []

        urns = obj.expanded_urns if hasattr(obj, "expanded_urns") else obj.get_urns()

        return [fields.serialize_urn(self.context["org"], urn) for urn in urns]

    def get_groups(self, obj):
        if not obj.is_active:
            return []

        groups = obj.prefetched_groups if hasattr(obj, "prefetched_groups") else obj.get_groups()
        return [{"uuid": g.uuid, "name": g.name} for g in groups]

    def get_notes(self, obj):
        if not obj.is_active:
            return []
        return [
            {
                "text": note.text,
                "created_on": note.created_on,
                "created_by": {"email": note.created_by.email, "name": note.created_by.name},
            }
            for note in obj.notes.all()
        ]

    def get_contact_fields(self, obj):
        if not obj.is_active:
            return {}

        fields = {}
        for contact_field in self.context["contact_fields"]:
            fields[contact_field.key] = obj.get_field_serialized(contact_field)
        return fields

    def get_blocked(self, obj):
        return obj.status == Contact.STATUS_BLOCKED if obj.is_active else None

    def get_stopped(self, obj):
        return obj.status == Contact.STATUS_STOPPED if obj.is_active else None

    class Meta:
        model = Contact
        fields = (
            "uuid",
            "name",
            "anon_display",
            "status",
            "language",
            "urns",
            "groups",
            "notes",
            "fields",
            "flow",
            "created_on",
            "modified_on",
            "last_seen_on",
            "blocked",
            "stopped",
        )


class ContactWriteSerializer(WriteSerializer):
    name = serializers.CharField(required=False, max_length=64, allow_null=True)
    language = serializers.CharField(required=False, min_length=3, max_length=3, allow_null=True)
    note = serializers.CharField(required=False, max_length=ContactNote.MAX_LENGTH, allow_blank=True)
    urns = serializers.ListField(required=False, child=fields.URNField(), max_length=100)
    groups = fields.ContactGroupField(many=True, required=False, allow_dynamic=False)
    fields = fields.LimitedDictField(
        required=False, child=serializers.CharField(allow_blank=True, allow_null=True), max_length=100
    )

    urn_errors = {"invalid": "URN is not valid.", "taken": "URN is in use by another contact."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def validate_language(self, value):
        if value and not pycountry.languages.get(alpha_3=value):
            raise serializers.ValidationError("Not a valid ISO639-3 language code.")

        return value

    def validate_groups(self, value):
        # only active contacts can be added to groups
        if self.instance and (self.instance.status != Contact.STATUS_ACTIVE) and value:
            raise serializers.ValidationError("Non-active contacts can't be added to groups")

        return value

    def validate_fields(self, value):
        fields_by_key = {f.key: f for f in self.context["contact_fields"]}
        values_by_field = {}

        for field_key, field_val in value.items():
            field_obj = fields_by_key.get(field_key)
            if not field_obj:
                raise serializers.ValidationError(f"Invalid contact field key: {field_key}")

            if field_obj.get_access(self.context["user"]) != ContactField.ACCESS_EDIT:
                raise serializers.ValidationError(f"Editing of '{field_key}' values disallowed for current user.")

            values_by_field[field_obj] = field_val

        return values_by_field

    def validate_urns(self, value):
        org = self.context["org"]

        # this field isn't allowed if we are looking up by URN in the URL
        if "urns__identity" in self.context["lookup_values"]:
            raise serializers.ValidationError("Field not allowed when using URN in URL")

        # or for updates by anonymous organizations (we do allow creation of contacts with URNs)
        if org.is_anon and self.instance:
            raise serializers.ValidationError("Updating URNs not allowed for anonymous organizations")

        return value

    def validate(self, data):
        if self.instance and not self.instance.is_active:
            raise serializers.ValidationError("Deleted contacts can't be modified.")

        # we allow creation of contacts by URN used for lookup
        if not data.get("urns") and "urns__identity" in self.context["lookup_values"] and not self.instance:
            url_urn = self.context["lookup_values"]["urns__identity"]

            data["urns"] = [fields.validate_urn(url_urn)]

        return data

    def save(self):
        """
        Update our contact
        """
        name = self.validated_data.get("name")
        language = self.validated_data.get("language")
        urns = self.validated_data.get("urns")
        groups = self.validated_data.get("groups")
        custom_fields = self.validated_data.get("fields")
        note = self.validated_data.get("note")

        mods = []

        # update an existing contact
        if self.instance:
            # update our name and language
            if "name" in self.validated_data and name != self.instance.name:
                mods.append(modifiers.Name(name=name))
            if "language" in self.validated_data and language != self.instance.language:
                mods.append(modifiers.Language(language=language))

            if "urns" in self.validated_data and urns is not None:
                mods += self.instance.update_urns(urns)

            # update our fields
            if custom_fields is not None:
                mods += self.instance.update_fields(values=custom_fields)

            # update our groups
            if groups is not None:
                mods += self.instance.update_static_groups(groups)

            if mods:
                self.instance.modify(self.context["user"], mods)

            if note is not None:
                self.instance.set_note(self.context["user"], note)

        # create new contact
        else:
            self.instance = Contact.create(
                self.context["org"],
                self.context["user"],
                name=name,
                language=language,
                status=Contact.STATUS_ACTIVE,
                urns=urns or [],
                fields=custom_fields or {},
                groups=groups or [],
            )

        return self.instance

    def urn_exception(self, ex):
        return {"urns": {ex.index: [self.urn_errors[ex.code]]}}


class ContactFieldReadSerializer(ReadSerializer):
    VALUE_TYPES = {
        ContactField.TYPE_TEXT: "text",
        ContactField.TYPE_NUMBER: "numeric",
        ContactField.TYPE_DATETIME: "datetime",
        ContactField.TYPE_STATE: "state",
        ContactField.TYPE_DISTRICT: "district",
        ContactField.TYPE_WARD: "ward",
    }
    ACCESS_TYPES = {
        ContactField.ACCESS_NONE: "none",
        ContactField.ACCESS_VIEW: "view",
        ContactField.ACCESS_EDIT: "edit",
    }

    type = serializers.SerializerMethodField()
    featured = serializers.SerializerMethodField()
    usages = serializers.SerializerMethodField()
    agent_access = serializers.SerializerMethodField()

    # for backwards compatibility
    label = serializers.SerializerMethodField()
    value_type = serializers.SerializerMethodField()

    def get_type(self, obj):
        return ContactField.ENGINE_TYPES[obj.value_type]

    def get_featured(self, obj):
        return obj.show_in_table

    def get_usages(self, obj):
        return {
            "flows": getattr(obj, "flow_count", 0),
            "groups": getattr(obj, "group_count", 0),
            "campaign_events": getattr(obj, "campaignevent_count", 0),
        }

    def get_agent_access(self, obj):
        return self.ACCESS_TYPES[obj.agent_access]

    def get_label(self, obj):
        return obj.name

    def get_value_type(self, obj):
        return self.VALUE_TYPES[obj.value_type]

    class Meta:
        model = ContactField
        fields = ("key", "name", "type", "featured", "priority", "usages", "agent_access", "label", "value_type")


class ContactFieldWriteSerializer(WriteSerializer):
    TYPES = {v: k for k, v in ContactField.ENGINE_TYPES.items()}
    VALUE_TYPES = {v: k for k, v in ContactFieldReadSerializer.VALUE_TYPES.items()}

    name = serializers.CharField(
        required=False,
        max_length=ContactField.MAX_NAME_LEN,
        validators=[
            UniqueForOrgValidator(ContactField.objects.filter(is_active=True), ignore_case=True, model_field="name")
        ],
    )
    type = serializers.ChoiceField(required=False, choices=list(TYPES.keys()))

    # for backwards compatibility
    label = serializers.CharField(
        required=False,
        max_length=ContactField.MAX_NAME_LEN,
        validators=[
            UniqueForOrgValidator(ContactField.objects.filter(is_active=True), ignore_case=True, model_field="name")
        ],
    )
    value_type = serializers.ChoiceField(required=False, choices=list(VALUE_TYPES.keys()))

    def validate_name(self, value):
        if not ContactField.is_valid_name(value):
            raise serializers.ValidationError("Can only contain letters, numbers and hypens.")

        key = ContactField.make_key(value)
        if not ContactField.is_valid_key(key):
            raise serializers.ValidationError('Generated key "%s" is invalid or a reserved name.' % key)

        return value

    def validate_type(self, value):
        if self.instance and self.instance.campaign_events.filter(is_active=True).exists() and value != "datetime":
            raise serializers.ValidationError("Can't change type of date field being used by campaign events.")

        return self.TYPES.get(value, self.VALUE_TYPES.get(value))

    def validate_label(self, value):
        return self.validate_name(value)

    def validate_value_type(self, value):
        return self.validate_type(value)

    def validate(self, data):
        if not data.get("name") and not data.get("label"):
            raise serializers.ValidationError("Field 'name' is required.")

        if not data.get("type") and not data.get("value_type"):
            raise serializers.ValidationError("Field 'type' is required.")

        return data

    def save(self):
        org = self.context["org"]
        user = self.context["user"]
        name = self.validated_data.get("name") or self.validated_data.get("label")
        value_type = self.validated_data.get("type") or self.validated_data.get("value_type")

        if self.instance:
            self.instance.name = name
            self.instance.value_type = value_type
            self.instance.save(update_fields=("name", "value_type"))
            return self.instance
        else:
            return ContactField.create(org, user, name, value_type=value_type)


class ContactGroupReadSerializer(ReadSerializer):
    status = serializers.SerializerMethodField()
    system = serializers.ReadOnlyField(source="is_system")
    count = serializers.SerializerMethodField()

    STATUSES = {
        ContactGroup.STATUS_INITIALIZING: "initializing",
        ContactGroup.STATUS_EVALUATING: "evaluating",
        ContactGroup.STATUS_READY: "ready",
    }

    def get_status(self, obj):
        return self.STATUSES[obj.status]

    def get_count(self, obj):
        return obj.count

    class Meta:
        model = ContactGroup
        fields = ("uuid", "name", "query", "status", "system", "count")


class ContactGroupWriteSerializer(WriteSerializer):
    name = serializers.CharField(
        required=True,
        max_length=ContactGroup.MAX_NAME_LEN,
        validators=[
            NameValidator(ContactGroup.MAX_NAME_LEN),
            UniqueForOrgValidator(queryset=ContactGroup.objects.filter(is_active=True), ignore_case=True),
        ],
    )

    def save(self):
        name = self.validated_data.get("name")

        if self.instance:
            self.instance.name = name
            self.instance.save(update_fields=("name",))
            return self.instance
        else:
            return ContactGroup.get_or_create(self.context["org"], self.context["user"], name)


class ContactBulkActionSerializer(WriteSerializer):
    ADD = "add"
    REMOVE = "remove"
    BLOCK = "block"
    UNBLOCK = "unblock"
    INTERRUPT = "interrupt"
    ARCHIVE_MESSAGES = "archive_messages"
    DELETE = "delete"
    ARCHIVE = "archive"  # backward compatibility

    ACTIONS = (ADD, REMOVE, BLOCK, UNBLOCK, INTERRUPT, ARCHIVE_MESSAGES, DELETE, ARCHIVE)
    ACTIONS_WITH_GROUP = (ADD, REMOVE)

    contacts = fields.ContactField(many=True)
    action = serializers.ChoiceField(required=True, choices=ACTIONS)
    group = fields.ContactGroupField(required=False, allow_dynamic=False)

    def validate_contacts(self, value):
        if not value:
            raise serializers.ValidationError("Contacts can't be empty.")
        return value

    def validate(self, data):
        contacts = data["contacts"]
        action = data["action"]
        group = data.get("group")

        if action in self.ACTIONS_WITH_GROUP and not group:
            raise serializers.ValidationError('For action "%s" you should also specify a group' % action)
        elif action not in self.ACTIONS_WITH_GROUP and group:
            raise serializers.ValidationError('For action "%s" you should not specify a group' % action)

        if action == self.ADD:
            # if adding to a group, check for non-active contacts
            invalid_uuids = {c.uuid for c in contacts if c.status != Contact.STATUS_ACTIVE}
            if invalid_uuids:
                raise serializers.ValidationError(
                    "Non-active contacts cannot be added to groups: %s" % ", ".join(invalid_uuids)
                )

        return data

    def save(self):
        user = self.context["user"]
        contacts = self.validated_data["contacts"]
        action = self.validated_data["action"]
        group = self.validated_data.get("group")

        if action == self.ADD:
            Contact.bulk_change_group(user, contacts, group, add=True)
        elif action == self.REMOVE:
            Contact.bulk_change_group(user, contacts, group, add=False)
        elif action == self.INTERRUPT:
            mailroom.queue_interrupt(self.context["org"], contacts=contacts)
        elif action == self.ARCHIVE_MESSAGES or action == self.ARCHIVE:
            Msg.archive_all_for_contacts(contacts)
        elif action == self.BLOCK:
            Contact.bulk_change_status(user, contacts, modifiers.Status.BLOCKED)
        elif action == self.UNBLOCK:
            Contact.bulk_change_status(user, contacts, modifiers.Status.ACTIVE)
        elif action == self.DELETE:
            for contact in contacts:
                contact.release(user)


class FlowReadSerializer(ReadSerializer):
    FLOW_TYPES = {
        Flow.TYPE_MESSAGE: "message",
        Flow.TYPE_VOICE: "voice",
        Flow.TYPE_BACKGROUND: "background",
        Flow.TYPE_SURVEY: "survey",
    }

    type = serializers.SerializerMethodField()
    archived = serializers.ReadOnlyField(source="is_archived")
    labels = serializers.SerializerMethodField()
    expires = serializers.ReadOnlyField(source="expires_after_minutes")
    runs = serializers.SerializerMethodField()
    results = serializers.SerializerMethodField()
    parent_refs = serializers.SerializerMethodField()
    created_on = serializers.DateTimeField(default_timezone=tzone.utc)
    modified_on = serializers.DateTimeField(default_timezone=tzone.utc)

    def get_type(self, obj):
        return self.FLOW_TYPES.get(obj.flow_type)

    def get_labels(self, obj):
        return [{"uuid": str(lb.uuid), "name": lb.name} for lb in obj.labels.all()]

    def get_runs(self, obj):
        return obj.get_run_stats()["status"]

    def get_results(self, obj):
        return obj.metadata.get(Flow.METADATA_RESULTS, [])

    def get_parent_refs(self, obj):
        return obj.metadata.get(Flow.METADATA_PARENT_REFS, [])

    class Meta:
        model = Flow
        fields = (
            "uuid",
            "name",
            "type",
            "archived",
            "labels",
            "expires",
            "runs",
            "results",
            "parent_refs",
            "created_on",
            "modified_on",
        )


class FlowRunReadSerializer(ReadSerializer):
    EXIT_TYPES = {
        FlowRun.STATUS_COMPLETED: "completed",
        FlowRun.STATUS_INTERRUPTED: "interrupted",
        FlowRun.STATUS_EXPIRED: "expired",
        FlowRun.STATUS_FAILED: "failed",
    }

    flow = fields.FlowField()
    contact = fields.ContactField(as_summary=True)
    start = serializers.SerializerMethodField()
    path = serializers.SerializerMethodField()
    values = serializers.SerializerMethodField()
    exit_type = serializers.SerializerMethodField()
    created_on = serializers.DateTimeField(default_timezone=tzone.utc)
    modified_on = serializers.DateTimeField(default_timezone=tzone.utc)
    exited_on = serializers.DateTimeField(default_timezone=tzone.utc)

    def get_start(self, obj):
        return {"uuid": str(obj.start.uuid)} if obj.start else None

    def get_path(self, obj):
        if not self.context["include_paths"]:
            return None

        def convert_step(step):
            arrived_on = iso8601.parse_date(step["arrived_on"])
            return {"node": step["node_uuid"], "time": format_datetime(arrived_on)}

        return [convert_step(s) for s in obj.path]

    def get_values(self, obj):
        def convert_result(result):
            return {
                "value": result["value"],
                "category": result.get("category"),
                "node": result["node_uuid"],
                "time": format_datetime(iso8601.parse_date(result["created_on"])),
                "input": result.get("input"),
                "name": result.get("name"),
            }

        return {k: convert_result(r) for k, r in obj.results.items()}

    def get_exit_type(self, obj):
        return self.EXIT_TYPES.get(obj.status)

    class Meta:
        model = FlowRun
        fields = (
            "id",
            "uuid",
            "flow",
            "contact",
            "start",
            "responded",
            "path",
            "values",
            "created_on",
            "modified_on",
            "exited_on",
            "exit_type",
        )


class FlowStartReadSerializer(ReadSerializer):
    STATUSES = {
        FlowStart.STATUS_PENDING: "pending",
        FlowStart.STATUS_QUEUED: "queued",
        FlowStart.STATUS_STARTED: "started",
        FlowStart.STATUS_COMPLETED: "completed",
        FlowStart.STATUS_FAILED: "failed",
        FlowStart.STATUS_INTERRUPTED: "interrupted",
    }

    flow = fields.FlowField()
    status = serializers.SerializerMethodField()
    progress = serializers.SerializerMethodField()
    groups = fields.ContactGroupField(many=True)
    contacts = fields.ContactField(many=True)
    params = serializers.JSONField(required=False)
    created_on = serializers.DateTimeField(default_timezone=tzone.utc)
    modified_on = serializers.DateTimeField(default_timezone=tzone.utc)

    # deprecated
    extra = serializers.JSONField(required=False, source="params")
    restart_participants = serializers.SerializerMethodField()
    exclude_active = serializers.SerializerMethodField()

    def get_status(self, obj):
        return self.STATUSES.get(obj.status)

    def get_progress(self, obj):
        return {"total": obj.contact_count or -1, "started": obj.run_count}

    def get_restart_participants(self, obj):
        return not (obj.exclusions and obj.exclusions.get(FlowStart.EXCLUSION_STARTED_PREVIOUSLY, False))

    def get_exclude_active(self, obj):
        return obj.exclusions and obj.exclusions.get(FlowStart.EXCLUSION_IN_A_FLOW, False)

    class Meta:
        model = FlowStart
        fields = (
            "uuid",
            "flow",
            "status",
            "progress",
            "groups",
            "contacts",
            "params",
            "created_on",
            "modified_on",
            # deprecated
            "id",
            "extra",
            "restart_participants",
            "exclude_active",
        )


class FlowStartWriteSerializer(WriteSerializer):
    flow = fields.FlowField()
    contacts = fields.ContactField(many=True, required=False)
    groups = fields.ContactGroupField(many=True, required=False)
    urns = serializers.ListField(required=False, child=fields.URNField(), max_length=100)
    restart_participants = serializers.BooleanField(required=False)
    exclude_active = serializers.BooleanField(required=False)
    extra = serializers.JSONField(required=False)
    params = serializers.JSONField(required=False)

    def validate_extra(self, value):
        # request is parsed by DRF.JSONParser, and if extra is a valid json it gets deserialized as dict
        # in any other case we need to raise a ValidationError
        if not isinstance(value, dict):
            raise serializers.ValidationError("Must be a valid JSON object")

        return normalize_extra(value)

    def validate_params(self, value):
        return self.validate_extra(value)

    def validate(self, data):
        # need at least one of urns, groups or contacts
        args = data.get("groups", []) + data.get("contacts", []) + data.get("urns", [])
        if not args:
            raise serializers.ValidationError("Must specify at least one group, contact or URN")

        return data

    def save(self):
        urns = self.validated_data.get("urns", [])
        contacts = self.validated_data.get("contacts", [])
        groups = self.validated_data.get("groups", [])
        exclusions = {
            FlowStart.EXCLUSION_STARTED_PREVIOUSLY: not self.validated_data.get("restart_participants", True),
            FlowStart.EXCLUSION_IN_A_FLOW: self.validated_data.get("exclude_active", False),
        }
        params = self.validated_data.get("params") or self.validated_data.get("extra")

        # ok, let's go create our flow start, the actual starting will happen in our view
        return FlowStart.create(
            self.validated_data["flow"],
            self.context["user"],
            start_type=FlowStart.TYPE_API_ZAPIER if self.context["is_zapier"] else FlowStart.TYPE_API,
            contacts=contacts,
            groups=groups,
            urns=urns,
            exclusions=exclusions,
            params=params,
        )


class GlobalReadSerializer(ReadSerializer):
    modified_on = serializers.DateTimeField(default_timezone=tzone.utc)

    class Meta:
        model = Global
        fields = ("key", "name", "value", "modified_on")


class GlobalWriteSerializer(WriteSerializer):
    value = serializers.CharField(required=True, max_length=Global.MAX_VALUE_LEN)
    name = serializers.CharField(
        required=False,
        max_length=Global.MAX_NAME_LEN,
        validators=[UniqueForOrgValidator(queryset=Global.objects.filter(is_active=True), ignore_case=True)],
    )

    def validate_name(self, value):
        if not Global.is_valid_name(value):
            raise serializers.ValidationError("Name contains illegal characters.")
        key = Global.make_key(value)
        if not Global.is_valid_key(key):
            raise serializers.ValidationError("Name creates Key that is invalid")
        return value

    def validate(self, data):
        if not self.instance and not data.get("name"):
            raise serializers.ValidationError("Name is required when creating new global.")

        return data

    def save(self):
        value = self.validated_data["value"]
        if self.instance:
            self.instance.value = value
            self.instance.save(update_fields=("value", "modified_on"))
            return self.instance
        else:
            name = self.validated_data["name"]
            key = Global.make_key(name)
            return Global.get_or_create(self.context["org"], self.context["user"], key, name, value)


class LabelReadSerializer(ReadSerializer):
    count = serializers.SerializerMethodField()

    def get_count(self, obj):
        # count may be cached on the object
        return obj.count if hasattr(obj, "count") else obj.get_visible_count()

    class Meta:
        model = Label
        fields = ("uuid", "name", "count")


class LabelWriteSerializer(WriteSerializer):
    name = serializers.CharField(
        required=True,
        max_length=Label.MAX_NAME_LEN,
        validators=[
            NameValidator(Label.MAX_NAME_LEN),
            UniqueForOrgValidator(queryset=Label.objects.filter(is_active=True), ignore_case=True),
        ],
    )

    def save(self):
        name = self.validated_data.get("name")

        if self.instance:
            self.instance.name = name
            self.instance.save(update_fields=("name",))
            return self.instance
        else:
            return Label.create(self.context["org"], self.context["user"], name)


class MediaReadSerializer(ReadSerializer):
    class Meta:
        model = Media
        fields = ("uuid", "content_type", "url", "filename", "size")


class MediaWriteSerializer(WriteSerializer):
    file = serializers.FileField()

    def validate_file(self, value):
        if not Media.is_allowed_type(value.content_type):
            raise serializers.ValidationError("Unsupported file type.")

        if value.size > Media.MAX_UPLOAD_SIZE:
            limit_MB = Media.MAX_UPLOAD_SIZE / (1024 * 1024)
            raise serializers.ValidationError(f"Limit for file uploads is {limit_MB} MB.")

        return value

    def save(self):
        file = self.validated_data["file"]
        return Media.from_upload(self.context["org"], self.context["user"], file)


class MsgReadSerializer(ReadSerializer):
    TYPES = {Msg.TYPE_TEXT: "text", Msg.TYPE_OPTIN: "optin", Msg.TYPE_VOICE: "voice"}
    STATUSES = {
        Msg.STATUS_PENDING: "queued",  # same as far as users are concerned
        Msg.STATUS_HANDLED: "handled",
        Msg.STATUS_INITIALIZING: "queued",
        Msg.STATUS_QUEUED: "queued",
        Msg.STATUS_WIRED: "wired",
        Msg.STATUS_SENT: "sent",
        Msg.STATUS_DELIVERED: "delivered",
        Msg.STATUS_READ: "read",
        Msg.STATUS_ERRORED: "errored",
        Msg.STATUS_FAILED: "failed",
    }
    VISIBILITIES = {  # deleted messages should never be exposed over API
        Msg.VISIBILITY_VISIBLE: "visible",
        Msg.VISIBILITY_ARCHIVED: "archived",
    }

    broadcast = serializers.SerializerMethodField()
    contact = fields.ContactField()
    urn = fields.URNField(source="contact_urn")
    channel = fields.ChannelField()
    direction = serializers.SerializerMethodField()
    type = serializers.SerializerMethodField()
    attachments = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()
    archived = serializers.SerializerMethodField()
    visibility = serializers.SerializerMethodField()
    labels = serializers.SerializerMethodField()
    flow = fields.FlowField()
    media = serializers.SerializerMethodField()  # deprecated
    created_on = serializers.DateTimeField(default_timezone=tzone.utc)
    modified_on = serializers.DateTimeField(default_timezone=tzone.utc)
    sent_on = serializers.DateTimeField(default_timezone=tzone.utc)

    def get_broadcast(self, obj):
        return obj.broadcast_id

    def get_direction(self, obj):
        return "in" if obj.direction == Msg.DIRECTION_IN else "out"

    def get_type(self, obj):
        return self.TYPES.get(obj.msg_type)

    def get_status(self, obj):
        return self.STATUSES.get(obj.status)

    def get_attachments(self, obj):
        return [a.as_json() for a in obj.get_attachments()]

    def get_media(self, obj):
        return obj.attachments[0] if obj.attachments else None

    def get_archived(self, obj):
        return obj.visibility == Msg.VISIBILITY_ARCHIVED

    def get_visibility(self, obj):
        return self.VISIBILITIES.get(obj.visibility)

    def get_labels(self, obj):
        # to optimize the POST case that creates an outgoing message, don't even try to look for labels
        if obj.direction == Msg.DIRECTION_IN:
            return [{"uuid": str(lb.uuid), "name": lb.name} for lb in obj.labels.all()]
        else:
            return []

    class Meta:
        model = Msg
        fields = (
            "id",
            "broadcast",
            "contact",
            "urn",
            "channel",
            "direction",
            "type",
            "status",
            "archived",
            "visibility",
            "text",
            "labels",
            "flow",
            "attachments",
            "created_on",
            "sent_on",
            "modified_on",
            "media",
        )


class MsgWriteSerializer(WriteSerializer):
    contact = fields.ContactField()
    text = serializers.CharField(required=False, max_length=Msg.MAX_TEXT_LEN)
    attachments = fields.MediaField(required=False, many=True, max_items=Msg.MAX_ATTACHMENTS)
    ticket = fields.TicketField(required=False)

    def validate(self, data):
        if not (data.get("text") or data.get("attachments")):
            raise serializers.ValidationError("Must provide either text or attachments.")

        return data

    def save(self):
        org = self.context["org"]
        user = self.context["user"]
        contact = self.validated_data["contact"]
        text = self.validated_data.get("text")
        attachments = [str(m) for m in self.validated_data.get("attachments", [])]
        ticket = self.validated_data.get("ticket")

        resp = mailroom.get_client().msg_send(org, user, contact, text or "", attachments, ticket)

        # to avoid fetching the new msg from the database, construct transient instances to pass to the serializer
        channel = Channel(uuid=resp["channel"]["uuid"], name=resp["channel"]["name"]) if resp.get("channel") else None
        contact = Contact(uuid=resp["contact"]["uuid"], name=resp["contact"]["name"])

        if resp.get("urn"):
            urn_scheme, urn_path, _, urn_display = URN.to_parts(resp["urn"])
            contact_urn = ContactURN(scheme=urn_scheme, path=urn_path, display=urn_display)
        else:
            contact_urn = None

        return Msg(
            id=resp["id"],
            org=org,
            contact=contact,
            contact_urn=contact_urn,
            channel=channel,
            direction=Msg.DIRECTION_OUT,
            msg_type=Msg.TYPE_TEXT,
            status=resp["status"],
            visibility=Msg.VISIBILITY_VISIBLE,
            text=resp.get("text"),
            attachments=resp.get("attachments"),
            created_on=iso8601.parse_date(resp["created_on"]),
            modified_on=iso8601.parse_date(resp["modified_on"]),
        )


class MsgBulkActionSerializer(WriteSerializer):
    LABEL = "label"
    UNLABEL = "unlabel"
    ARCHIVE = "archive"
    RESTORE = "restore"
    DELETE = "delete"

    ACTIONS = (LABEL, UNLABEL, ARCHIVE, RESTORE, DELETE)
    ACTIONS_WITH_LABEL = (LABEL, UNLABEL)

    messages = fields.MessageField(many=True)
    action = serializers.ChoiceField(required=True, choices=ACTIONS)
    label = fields.LabelField(required=False)
    label_name = serializers.CharField(
        required=False, max_length=Label.MAX_NAME_LEN, validators=[NameValidator(max_length=Label.MAX_NAME_LEN)]
    )

    def validate_messages(self, value):
        for msg in value:
            if msg and msg.direction != Msg.DIRECTION_IN:
                raise serializers.ValidationError("Not an incoming message: %d" % msg.id)

        return value

    def validate(self, data):
        action = data["action"]
        label = data.get("label")
        label_name = data.get("label_name")

        if label and label_name:
            raise serializers.ValidationError("Can't specify both label and label_name.")

        if action in self.ACTIONS_WITH_LABEL and not (label or label_name):
            raise serializers.ValidationError('For action "%s" you should also specify a label' % action)
        elif action not in self.ACTIONS_WITH_LABEL and (label or label_name):
            raise serializers.ValidationError('For action "%s" you should not specify a label' % action)

        return data

    def save(self):
        action = self.validated_data["action"]
        label = self.validated_data.get("label")
        label_name = self.validated_data.get("label_name")

        requested_message_ids = self.initial_data["messages"]
        requested_messages = self.validated_data["messages"]

        # requested_messages contains nones where msg no longer exists so compile lists of real messages and missing ids
        messages = []
        missing_message_ids = []
        for m, msg in enumerate(requested_messages):
            if msg is not None:
                messages.append(msg)
            else:
                missing_message_ids.append(requested_message_ids[m])

        if action == self.LABEL:
            if not label:
                label, _ = Label.import_def(self.context["org"], self.context["user"], {"name": label_name})
            if label:
                label.toggle_label(messages, add=True)
        elif action == self.UNLABEL:
            if not label:
                label = Label.get_active_for_org(self.context["org"]).filter(name=label_name).first()

            if label:
                label.toggle_label(messages, add=False)
        elif action == self.DELETE:
            Msg.bulk_soft_delete(messages)
        else:
            for msg in messages:
                if action == self.ARCHIVE and msg.visibility == Msg.VISIBILITY_VISIBLE:
                    msg.archive()
                elif action == self.RESTORE and msg.visibility == Msg.VISIBILITY_ARCHIVED:
                    msg.restore()

        return BulkActionFailure(missing_message_ids) if missing_message_ids else None


class OptInReadSerializer(ReadSerializer):
    created_on = serializers.DateTimeField(default_timezone=tzone.utc)

    class Meta:
        model = Topic
        fields = ("uuid", "name", "created_on")


class OptInWriteSerializer(WriteSerializer):
    name = serializers.CharField(
        required=True,
        max_length=OptIn.MAX_NAME_LEN,
        validators=[
            NameValidator(OptIn.MAX_NAME_LEN),
            UniqueForOrgValidator(queryset=OptIn.objects.filter(is_active=True), ignore_case=True),
        ],
    )

    def save(self):
        name = self.validated_data["name"]
        return OptIn.create(self.context["org"], self.context["user"], name)


class ResthookReadSerializer(ReadSerializer):
    resthook = serializers.SerializerMethodField()
    created_on = serializers.DateTimeField(default_timezone=tzone.utc)
    modified_on = serializers.DateTimeField(default_timezone=tzone.utc)

    def get_resthook(self, obj):
        return obj.slug

    class Meta:
        model = Resthook
        fields = ("resthook", "modified_on", "created_on")


class ResthookSubscriberReadSerializer(ReadSerializer):
    resthook = serializers.SerializerMethodField()
    created_on = serializers.DateTimeField(default_timezone=tzone.utc)

    def get_resthook(self, obj):
        return obj.resthook.slug

    class Meta:
        model = ResthookSubscriber
        fields = ("id", "resthook", "target_url", "created_on")


class ResthookSubscriberWriteSerializer(WriteSerializer):
    resthook = serializers.CharField(required=True)
    target_url = serializers.URLField(required=True)

    def validate_resthook(self, value):
        resthook = Resthook.objects.filter(is_active=True, org=self.context["org"], slug=value).first()
        if not resthook:
            raise serializers.ValidationError("No resthook with slug: %s" % value)
        return resthook

    def validate(self, data):
        resthook = data["resthook"]
        target_url = data["target_url"]

        # make sure this combination doesn't already exist
        if ResthookSubscriber.objects.filter(
            resthook=resthook, target_url=target_url, is_active=True
        ):  # pragma: needs cover
            raise serializers.ValidationError("URL is already subscribed to this event.")

        return data

    def save(self):
        resthook = self.validated_data["resthook"]
        target_url = self.validated_data["target_url"]
        return resthook.add_subscriber(target_url, self.context["user"])


class WebHookEventReadSerializer(ReadSerializer):
    resthook = serializers.SerializerMethodField()
    data = serializers.SerializerMethodField()
    created_on = serializers.DateTimeField(default_timezone=tzone.utc)

    def get_resthook(self, obj):
        return obj.resthook.slug

    def get_data(self, obj):
        return obj.data

    class Meta:
        model = WebHookEvent
        fields = ("resthook", "data", "created_on")


class TicketReadSerializer(ReadSerializer):
    STATUSES = {Ticket.STATUS_OPEN: "open", Ticket.STATUS_CLOSED: "closed"}

    contact = fields.ContactField()
    status = serializers.SerializerMethodField()
    topic = fields.TopicField()
    assignee = fields.UserField()
    opened_on = serializers.DateTimeField(default_timezone=tzone.utc)
    opened_by = fields.UserField()
    opened_in = fields.FlowField()
    modified_on = serializers.DateTimeField(default_timezone=tzone.utc)
    closed_on = serializers.DateTimeField(default_timezone=tzone.utc)
    body = serializers.SerializerMethodField()  # deprecated

    def get_status(self, obj):
        return self.STATUSES.get(obj.status)

    def get_body(self, obj):
        return None

    class Meta:
        model = Ticket
        fields = (
            "uuid",
            "contact",
            "status",
            "topic",
            "body",
            "assignee",
            "opened_on",
            "opened_by",
            "opened_in",
            "modified_on",
            "closed_on",
        )


class TicketBulkActionSerializer(WriteSerializer):
    ACTION_ASSIGN = "assign"
    ACTION_ADD_NOTE = "add_note"
    ACTION_CHANGE_TOPIC = "change_topic"
    ACTION_CLOSE = "close"
    ACTION_REOPEN = "reopen"
    ACTION_CHOICES = (ACTION_ASSIGN, ACTION_ADD_NOTE, ACTION_CHANGE_TOPIC, ACTION_CLOSE, ACTION_REOPEN)

    tickets = fields.TicketField(many=True)
    action = serializers.ChoiceField(required=True, choices=ACTION_CHOICES)
    assignee = fields.UserField(required=False, allow_null=True, assignable_only=True)
    topic = fields.TopicField(required=False)
    note = serializers.CharField(required=False, max_length=Ticket.MAX_NOTE_LENGTH)

    def validate(self, data):
        action = data["action"]

        if action == self.ACTION_ASSIGN and "assignee" not in data:
            raise serializers.ValidationError('For action "%s" you must specify the assignee' % action)
        elif action == self.ACTION_ADD_NOTE and not data.get("note"):
            raise serializers.ValidationError('For action "%s" you must specify the note' % action)
        elif action == self.ACTION_CHANGE_TOPIC and not data.get("topic"):
            raise serializers.ValidationError('For action "%s" you must specify the topic' % action)

        return data

    def save(self):
        org = self.context["org"]
        user = self.context["user"]
        tickets = self.validated_data["tickets"]
        action = self.validated_data["action"]
        assignee = self.validated_data.get("assignee")
        note = self.validated_data.get("note")
        topic = self.validated_data.get("topic")

        if action == self.ACTION_ASSIGN:
            Ticket.bulk_assign(org, user, tickets, assignee=assignee)
        elif action == self.ACTION_ADD_NOTE:
            Ticket.bulk_add_note(org, user, tickets, note=note)
        elif action == self.ACTION_CHANGE_TOPIC:
            Ticket.bulk_change_topic(org, user, tickets, topic=topic)
        elif action == self.ACTION_CLOSE:
            Ticket.bulk_close(org, user, tickets)
        elif action == self.ACTION_REOPEN:
            Ticket.bulk_reopen(org, user, tickets)


class TopicReadSerializer(ReadSerializer):
    created_on = serializers.DateTimeField(default_timezone=tzone.utc)
    counts = serializers.SerializerMethodField()
    system = serializers.SerializerMethodField()

    def get_counts(self, obj):
        return {"open": obj.open_count, "closed": obj.closed_count}

    def get_system(self, obj):
        return obj.is_default

    class Meta:
        model = Topic
        fields = ("uuid", "name", "counts", "system", "created_on")


class TopicWriteSerializer(WriteSerializer):
    name = serializers.CharField(
        required=True,
        max_length=Topic.MAX_NAME_LEN,
        validators=[
            NameValidator(Topic.MAX_NAME_LEN),
            UniqueForOrgValidator(queryset=Topic.objects.filter(is_active=True), ignore_case=True),
        ],
    )

    def save(self):
        name = self.validated_data["name"]

        if self.instance:
            self.instance.name = name
            self.instance.save(update_fields=("name",))
            return self.instance
        else:
            return Topic.create(self.context["org"], self.context["user"], name)


class UserReadSerializer(ReadSerializer):
    ROLES = {
        OrgRole.ADMINISTRATOR: "administrator",
        OrgRole.EDITOR: "editor",
        OrgRole.VIEWER: "viewer",
        OrgRole.AGENT: "agent",
    }

    avatar = serializers.SerializerMethodField()
    role = serializers.SerializerMethodField()
    created_on = serializers.DateTimeField(default_timezone=tzone.utc, source="date_joined")

    def get_avatar(self, obj):
        settings = obj.settings
        return settings.avatar.url if settings and settings.avatar else None

    def get_role(self, obj):
        role = self.context["user_roles"][obj]
        return self.ROLES[role]

    class Meta:
        model = User
        fields = ("email", "first_name", "last_name", "role", "created_on", "avatar")


class WorkspaceReadSerializer(ReadSerializer):
    DATE_STYLES = {
        Org.DATE_FORMAT_DAY_FIRST: "day_first",
        Org.DATE_FORMAT_MONTH_FIRST: "month_first",
        Org.DATE_FORMAT_YEAR_FIRST: "year_first",
    }

    country = serializers.SerializerMethodField()
    languages = serializers.SerializerMethodField()
    timezone = serializers.SerializerMethodField()
    date_style = serializers.SerializerMethodField()
    anon = serializers.SerializerMethodField()

    credits = serializers.SerializerMethodField()  # deprecated
    primary_language = serializers.SerializerMethodField()  # deprecated

    def get_country(self, obj):
        return obj.default_country_code

    def get_languages(self, obj):
        return obj.flow_languages

    def get_timezone(self, obj):
        return str(obj.timezone)

    def get_date_style(self, obj):
        return self.DATE_STYLES.get(obj.date_format)

    def get_anon(self, obj):
        return obj.is_anon

    def get_credits(self, obj):
        return {"used": -1, "remaining": -1}  # for backwards compatibility

    def get_primary_language(self, obj):
        return obj.flow_languages[0]

    class Meta:
        model = Org
        fields = (
            "uuid",
            "name",
            "country",
            "languages",
            "timezone",
            "date_style",
            "anon",
            "credits",
            "primary_language",
        )
