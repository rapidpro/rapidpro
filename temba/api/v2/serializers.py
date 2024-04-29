import logging
import numbers
from collections import OrderedDict

import iso8601
import pycountry
import pytz
import regex
from rest_framework import serializers

from django.conf import settings
from django.contrib.auth.models import User

from temba import mailroom
from temba.api.models import Resthook, ResthookSubscriber, WebHookEvent
from temba.archives.models import Archive
from temba.campaigns.models import Campaign, CampaignEvent
from temba.channels.models import Channel, ChannelEvent
from temba.classifiers.models import Classifier
from temba.contacts.models import Contact, ContactField, ContactGroup
from temba.flows.models import Flow, FlowRun, FlowStart
from temba.globals.models import Global
from temba.locations.models import AdminBoundary
from temba.mailroom import modifiers
from temba.msgs.models import Broadcast, Label, Msg
from temba.orgs.models import Org, OrgRole
from temba.templates.models import Template, TemplateTranslation
from temba.tickets.models import Ticket, Ticketer, Topic
from temba.utils import json, on_transaction_commit
from temba.utils.fields import NameValidator

from . import fields
from .validators import UniqueForOrgValidator

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
        for (k, v) in extra.items():
            (normalized[normalize_key(k)], count) = _normalize_extra(v, count)

            if count >= settings.FLOW_START_PARAMS_SIZE:
                break

        return normalized, count

    elif isinstance(extra, list):
        count += 1
        normalized = OrderedDict()
        for (i, v) in enumerate(extra):
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


class BulkActionFailure:
    """
    Bulk action serializers can return a partial failure if some objects couldn't be acted on
    """

    def __init__(self, failures):
        self.failures = failures

    def as_json(self):
        return {"failures": self.failures}


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
        Broadcast.STATUS_INITIALIZING: "queued",
        Broadcast.STATUS_QUEUED: "queued",
        Broadcast.STATUS_SENT: "sent",
        Broadcast.STATUS_FAILED: "failed",
    }

    text = fields.TranslatableField()
    status = serializers.SerializerMethodField()
    urns = serializers.SerializerMethodField()
    contacts = fields.ContactField(many=True)
    groups = fields.ContactGroupField(many=True)
    created_on = serializers.DateTimeField(default_timezone=pytz.UTC)

    def get_status(self, obj):
        return self.STATUSES.get(obj.status, "sent")

    def get_urns(self, obj):
        if self.context["org"].is_anon:
            return None
        else:
            return obj.raw_urns or []

    class Meta:
        model = Broadcast
        fields = ("id", "urns", "contacts", "groups", "text", "status", "created_on")


class BroadcastWriteSerializer(WriteSerializer):
    text = fields.TranslatableField(required=True, max_length=Msg.MAX_TEXT_LEN)
    urns = fields.URNListField(required=False)
    contacts = fields.ContactField(many=True, required=False)
    groups = fields.ContactGroupField(many=True, required=False)
    ticket = fields.TicketField(required=False)

    def validate(self, data):
        if not (data.get("urns") or data.get("contacts") or data.get("groups")):
            raise serializers.ValidationError("Must provide either urns, contacts or groups")

        return data

    def save(self):
        """
        Create a new broadcast to send out
        """

        text, base_language = self.validated_data["text"]

        # create the broadcast
        broadcast = Broadcast.create(
            self.context["org"],
            self.context["user"],
            text=text,
            base_language=base_language,
            groups=self.validated_data.get("groups", []),
            contacts=self.validated_data.get("contacts", []),
            urns=self.validated_data.get("urns", []),
            template_state=Broadcast.TEMPLATE_STATE_UNEVALUATED,
            ticket=self.validated_data.get("ticket"),
        )

        # send it
        on_transaction_commit(lambda: broadcast.send_async())

        return broadcast


class ChannelEventReadSerializer(ReadSerializer):
    TYPES = {t[0]: t[2] for t in ChannelEvent.TYPE_CONFIG}

    type = serializers.SerializerMethodField()
    contact = fields.ContactField()
    channel = fields.ChannelField()
    extra = serializers.SerializerMethodField()
    created_on = serializers.DateTimeField(default_timezone=pytz.UTC)
    occurred_on = serializers.DateTimeField(default_timezone=pytz.UTC)

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
    created_on = serializers.DateTimeField(default_timezone=pytz.UTC)

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
    created_on = serializers.DateTimeField(default_timezone=pytz.UTC)

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
    message = fields.TranslatableField(required=False, max_length=Msg.MAX_TEXT_LEN)
    flow = fields.FlowField(required=False)

    def validate_unit(self, value):
        return self.UNITS[value]

    def validate_campaign(self, value):
        if self.instance and value and self.instance.campaign != value:
            raise serializers.ValidationError("Cannot change campaign for existing events")
        return value

    def validate(self, data):
        message = data.get("message")
        flow = data.get("flow")

        if message and not flow:
            translations, base_language = message
            if not translations[base_language]:
                raise serializers.ValidationError("Message text is required")

        if (message and flow) or (not message and not flow):
            raise serializers.ValidationError("Flow UUID or a message text required.")

        return data

    def save(self):
        """
        Create or update our campaign event
        """
        campaign = self.validated_data.get("campaign")
        offset = self.validated_data.get("offset")
        unit = self.validated_data.get("unit")
        delivery_hour = self.validated_data.get("delivery_hour")
        relative_to = self.validated_data.get("relative_to")
        message = self.validated_data.get("message")
        flow = self.validated_data.get("flow")

        if self.instance:

            # we dont update, we only create
            self.instance = self.instance.recreate()

            # we are being set to a flow
            if flow:
                self.instance.flow = flow
                self.instance.event_type = CampaignEvent.TYPE_FLOW
                self.instance.message = None

            # we are being set to a message
            else:
                translations, base_language = message
                self.instance.message = translations

                # if we aren't currently a message event, we need to create our hidden message flow
                if self.instance.event_type != CampaignEvent.TYPE_MESSAGE:
                    self.instance.flow = Flow.create_single_message(
                        self.context["org"], self.context["user"], translations, base_language
                    )
                    self.instance.event_type = CampaignEvent.TYPE_MESSAGE

                # otherwise, we can just update that flow
                else:
                    # set our single message on our flow
                    self.instance.flow.update_single_message_flow(self.context["user"], translations, base_language)

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
                    self.context["org"], self.context["user"], campaign, relative_to, offset, unit, flow, delivery_hour
                )
            else:
                translations, base_language = message

                self.instance = CampaignEvent.create_message_event(
                    self.context["org"],
                    self.context["user"],
                    campaign,
                    relative_to,
                    offset,
                    unit,
                    translations,
                    delivery_hour,
                    base_language,
                )

            self.instance.update_flow_name()

        # create our event fires for this event in the background
        self.instance.schedule_async()

        return self.instance


class ChannelReadSerializer(ReadSerializer):
    country = serializers.SerializerMethodField()
    device = serializers.SerializerMethodField()
    created_on = serializers.DateTimeField(default_timezone=pytz.UTC)
    last_seen = serializers.DateTimeField(default_timezone=pytz.UTC)

    def get_country(self, obj):
        return str(obj.country) if obj.country else None

    def get_device(self, obj):
        if not obj.is_android():
            return None

        return {
            "name": obj.device,
            "power_level": obj.get_last_power(),
            "power_status": obj.get_last_power_status(),
            "power_source": obj.get_last_power_source(),
            "network_type": obj.get_last_network_type(),
        }

    class Meta:
        model = Channel
        fields = ("uuid", "name", "address", "country", "device", "last_seen", "created_on")


class ClassifierReadSerializer(ReadSerializer):
    type = serializers.SerializerMethodField()
    intents = serializers.SerializerMethodField()
    created_on = serializers.DateTimeField(default_timezone=pytz.UTC)

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
    created_on = serializers.DateTimeField(default_timezone=pytz.UTC)
    modified_on = serializers.DateTimeField(default_timezone=pytz.UTC)
    last_seen_on = serializers.DateTimeField(default_timezone=pytz.UTC)

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

        return [urn.get_for_api() for urn in obj.get_urns()]

    def get_groups(self, obj):
        if not obj.is_active:
            return []

        groups = obj.prefetched_groups if hasattr(obj, "prefetched_groups") else obj.get_groups()
        return [{"uuid": g.uuid, "name": g.name} for g in groups]

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
    urns = fields.URNListField(required=False)
    groups = fields.ContactGroupField(many=True, required=False, allow_dynamic=False)
    fields = fields.LimitedDictField(required=False, child=serializers.CharField(allow_blank=True, allow_null=True))

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

        # if creating a contact, URNs can't belong to other contacts
        if not self.instance:
            for urn in value:
                if Contact.from_urn(org, urn):
                    raise serializers.ValidationError("URN belongs to another contact: %s" % urn)

        return value

    def validate(self, data):
        if self.instance and not self.instance.is_active:
            raise serializers.ValidationError("Inactive contacts can't be modified.")

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

        # create new contact
        else:
            self.instance = Contact.create(
                self.context["org"],
                self.context["user"],
                name,
                language,
                urns or [],
                custom_fields or {},
                groups or [],
            )

        return self.instance


class ContactFieldReadSerializer(ReadSerializer):
    VALUE_TYPES = {
        ContactField.TYPE_TEXT: "text",
        ContactField.TYPE_NUMBER: "numeric",
        ContactField.TYPE_DATETIME: "datetime",
        ContactField.TYPE_STATE: "state",
        ContactField.TYPE_DISTRICT: "district",
        ContactField.TYPE_WARD: "ward",
    }

    type = serializers.SerializerMethodField()
    featured = serializers.SerializerMethodField()
    usages = serializers.SerializerMethodField()

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

    def get_label(self, obj):
        return obj.name

    def get_value_type(self, obj):
        return self.VALUE_TYPES[obj.value_type]

    class Meta:
        model = ContactField
        fields = ("key", "name", "type", "featured", "priority", "usages", "label", "value_type")


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
        # count may be cached on the object
        return obj.count if hasattr(obj, "count") else obj.get_member_count()

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
    created_on = serializers.DateTimeField(default_timezone=pytz.UTC)
    modified_on = serializers.DateTimeField(default_timezone=pytz.UTC)

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
    created_on = serializers.DateTimeField(default_timezone=pytz.UTC)
    modified_on = serializers.DateTimeField(default_timezone=pytz.UTC)
    exited_on = serializers.DateTimeField(default_timezone=pytz.UTC)

    def get_start(self, obj):
        return {"uuid": str(obj.start.uuid)} if obj.start else None

    def get_path(self, obj):
        if not self.context["include_paths"]:
            return None

        def convert_step(step):
            arrived_on = iso8601.parse_date(step[FlowRun.PATH_ARRIVED_ON])
            return {"node": step[FlowRun.PATH_NODE_UUID], "time": format_datetime(arrived_on)}

        return [convert_step(s) for s in obj.path]

    def get_values(self, obj):
        def convert_result(result):
            created_on = iso8601.parse_date(result[FlowRun.RESULT_CREATED_ON])
            return {
                "value": result[FlowRun.RESULT_VALUE],
                "category": result.get(FlowRun.RESULT_CATEGORY),
                "node": result[FlowRun.RESULT_NODE_UUID],
                "time": format_datetime(created_on),
                "input": result.get(FlowRun.RESULT_INPUT),
                "name": result.get(FlowRun.RESULT_NAME),
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
        FlowStart.STATUS_STARTING: "starting",
        FlowStart.STATUS_COMPLETE: "complete",
        FlowStart.STATUS_FAILED: "failed",
    }

    flow = fields.FlowField()
    status = serializers.SerializerMethodField()
    groups = fields.ContactGroupField(many=True)
    contacts = fields.ContactField(many=True)
    exclude_active = serializers.SerializerMethodField()
    extra = serializers.JSONField(required=False)
    params = serializers.JSONField(required=False, source="extra")
    created_on = serializers.DateTimeField(default_timezone=pytz.UTC)
    modified_on = serializers.DateTimeField(default_timezone=pytz.UTC)

    def get_status(self, obj):
        return self.STATUSES.get(obj.status)

    def get_exclude_active(self, obj):
        return not obj.include_active

    class Meta:
        model = FlowStart
        fields = (
            "id",
            "uuid",
            "flow",
            "status",
            "groups",
            "contacts",
            "restart_participants",
            "exclude_active",
            "extra",
            "params",
            "created_on",
            "modified_on",
        )


class FlowStartWriteSerializer(WriteSerializer):
    flow = fields.FlowField()
    contacts = fields.ContactField(many=True, required=False)
    groups = fields.ContactGroupField(many=True, required=False)
    urns = fields.URNListField(required=False)
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
        restart_participants = self.validated_data.get("restart_participants", True)
        exclude_active = self.validated_data.get("exclude_active", False)
        extra = self.validated_data.get("extra")

        params = self.validated_data.get("params")
        if params:
            extra = params

        # ok, let's go create our flow start, the actual starting will happen in our view
        return FlowStart.create(
            self.validated_data["flow"],
            self.context["user"],
            start_type=FlowStart.TYPE_API_ZAPIER if self.context["is_zapier"] else FlowStart.TYPE_API,
            restart_participants=restart_participants,
            include_active=not exclude_active,
            contacts=contacts,
            groups=groups,
            urns=urns,
            extra=extra,
        )


class GlobalReadSerializer(ReadSerializer):
    modified_on = serializers.DateTimeField(default_timezone=pytz.UTC)

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


class MsgReadSerializer(ReadSerializer):
    STATUSES = {
        Msg.STATUS_PENDING: "queued",  # same as far as users are concerned
        Msg.STATUS_HANDLED: "handled",
        Msg.STATUS_QUEUED: "queued",
        Msg.STATUS_WIRED: "wired",
        Msg.STATUS_SENT: "sent",
        Msg.STATUS_DELIVERED: "delivered",
        Msg.STATUS_ERRORED: "errored",
        Msg.STATUS_FAILED: "failed",
    }
    TYPES = {Msg.TYPE_INBOX: "inbox", Msg.TYPE_FLOW: "flow", Msg.TYPE_IVR: "ivr"}
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
    labels = fields.LabelField(many=True)
    media = serializers.SerializerMethodField()  # deprecated
    created_on = serializers.DateTimeField(default_timezone=pytz.UTC)
    modified_on = serializers.DateTimeField(default_timezone=pytz.UTC)
    sent_on = serializers.DateTimeField(default_timezone=pytz.UTC)

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
            "attachments",
            "created_on",
            "sent_on",
            "modified_on",
            "media",
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
            if msg and msg.direction != "I":
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
        else:
            for msg in messages:
                if action == self.ARCHIVE and msg.visibility == Msg.VISIBILITY_VISIBLE:
                    msg.archive()
                elif action == self.RESTORE and msg.visibility == Msg.VISIBILITY_ARCHIVED:
                    msg.restore()
                elif action == self.DELETE and msg.visibility in (Msg.VISIBILITY_VISIBLE, Msg.VISIBILITY_ARCHIVED):
                    msg.delete(soft=True)

        return BulkActionFailure(missing_message_ids) if missing_message_ids else None


class ResthookReadSerializer(ReadSerializer):
    resthook = serializers.SerializerMethodField()
    created_on = serializers.DateTimeField(default_timezone=pytz.UTC)
    modified_on = serializers.DateTimeField(default_timezone=pytz.UTC)

    def get_resthook(self, obj):
        return obj.slug

    class Meta:
        model = Resthook
        fields = ("resthook", "modified_on", "created_on")


class ResthookSubscriberReadSerializer(ReadSerializer):
    resthook = serializers.SerializerMethodField()
    created_on = serializers.DateTimeField(default_timezone=pytz.UTC)

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
    created_on = serializers.DateTimeField(default_timezone=pytz.UTC)

    def get_resthook(self, obj):
        return obj.resthook.slug

    def get_data(self, obj):
        return obj.data

    class Meta:
        model = WebHookEvent
        fields = ("resthook", "data", "created_on")


class TemplateReadSerializer(ReadSerializer):
    translations = serializers.SerializerMethodField()
    modified_on = serializers.DateTimeField(default_timezone=pytz.UTC)
    created_on = serializers.DateTimeField(default_timezone=pytz.UTC)

    def get_translations(self, obj):
        translations = []
        for translation in (
            TemplateTranslation.objects.filter(template=obj, is_active=True)
            .order_by("language")
            .select_related("channel")
        ):
            translations.append(
                {
                    "language": translation.language,
                    "content": translation.content,
                    "namespace": translation.namespace,
                    "variable_count": translation.variable_count,
                    "status": translation.get_status_display(),
                    "channel": {"uuid": translation.channel.uuid, "name": translation.channel.name},
                }
            )

        return translations

    class Meta:
        model = Template
        fields = ("uuid", "name", "translations", "created_on", "modified_on")


class TicketerReadSerializer(ReadSerializer):
    type = serializers.SerializerMethodField()
    created_on = serializers.DateTimeField(default_timezone=pytz.UTC)

    def get_type(self, obj):
        return obj.ticketer_type

    class Meta:
        model = Ticketer
        fields = ("uuid", "name", "type", "created_on")


class TicketReadSerializer(ReadSerializer):
    STATUSES = {Ticket.STATUS_OPEN: "open", Ticket.STATUS_CLOSED: "closed"}

    ticketer = fields.TicketerField()
    contact = fields.ContactField()
    status = serializers.SerializerMethodField()
    topic = fields.TopicField()
    assignee = fields.UserField()
    opened_on = serializers.DateTimeField(default_timezone=pytz.UTC)
    opened_by = fields.UserField()
    opened_in = fields.FlowField()
    modified_on = serializers.DateTimeField(default_timezone=pytz.UTC)
    closed_on = serializers.DateTimeField(default_timezone=pytz.UTC)

    def get_status(self, obj):
        return self.STATUSES.get(obj.status)

    class Meta:
        model = Ticket
        fields = (
            "uuid",
            "ticketer",
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
    note = serializers.CharField(required=False, max_length=Ticket.MAX_NOTE_LEN)

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
            Ticket.bulk_assign(org, user, tickets, assignee=assignee, note=note)
        elif action == self.ACTION_ADD_NOTE:
            Ticket.bulk_add_note(org, user, tickets, note=note)
        elif action == self.ACTION_CHANGE_TOPIC:
            Ticket.bulk_change_topic(org, user, tickets, topic=topic)
        elif action == self.ACTION_CLOSE:
            Ticket.bulk_close(org, user, tickets)
        elif action == self.ACTION_REOPEN:
            Ticket.bulk_reopen(org, user, tickets)


class TopicReadSerializer(ReadSerializer):
    created_on = serializers.DateTimeField(default_timezone=pytz.UTC)
    system = serializers.SerializerMethodField()

    def get_system(self, obj):
        return obj.is_default

    class Meta:
        model = Topic
        fields = ("uuid", "name", "system", "created_on")


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
        OrgRole.SURVEYOR: "surveyor",
    }

    role = serializers.SerializerMethodField()
    created_on = serializers.DateTimeField(default_timezone=pytz.UTC, source="date_joined")

    def get_role(self, obj):
        role = self.context["user_roles"][obj]
        return self.ROLES[role]

    class Meta:
        model = User
        fields = ("email", "first_name", "last_name", "role", "created_on")


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
