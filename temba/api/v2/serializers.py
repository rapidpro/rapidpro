from __future__ import absolute_import, unicode_literals

from rest_framework import serializers
from temba.campaigns.models import Campaign, CampaignEvent
from temba.channels.models import Channel, ChannelEvent, ANDROID
from temba.contacts.models import Contact, ContactField, ContactGroup
from temba.flows.models import Flow, FlowRun, FlowStep
from temba.msgs.models import Broadcast, Msg, Label, STATUS_CONFIG, INCOMING, OUTGOING, INBOX, FLOW, IVR, PENDING
from temba.msgs.models import QUEUED
from temba.utils import datetime_to_json_date
from temba.values.models import Value


def format_datetime(value):
    """
    Datetime fields are formatted with microsecond accuracy for v2
    """
    return datetime_to_json_date(value, micros=True) if value else None


class ReadSerializer(serializers.ModelSerializer):
    """
    We deviate slightly from regular REST framework usage with distinct serializers for reading and writing
    """
    @staticmethod
    def extract_constants(config):
        return {t[0]: t[2] for t in config}

    def save(self, **kwargs):  # pragma: no cover
        raise ValueError("Can't call save on a read serializer")


class UUIDField(serializers.CharField):

    def __init__(self, **kwargs):
        super(UUIDField, self).__init__(max_length=36, **kwargs)


# ============================================================
# Serializers (A-Z)
# ============================================================

class BroadcastReadSerializer(ReadSerializer):
    urns = serializers.SerializerMethodField()
    contacts = serializers.SerializerMethodField()
    groups = serializers.SerializerMethodField()

    def get_urns(self, obj):
        if self.context['org'].is_anon:
            return None
        else:
            return [urn.urn for urn in obj.urns.all()]

    def get_contacts(self, obj):
        return [{'uuid': c.uuid, 'name': c.name} for c in obj.contacts.all()]

    def get_groups(self, obj):
        return [{'uuid': g.uuid, 'name': g.name} for g in obj.groups.all()]

    class Meta:
        model = Broadcast
        fields = ('id', 'urns', 'contacts', 'groups', 'text', 'created_on')


class ChannelEventReadSerializer(ReadSerializer):
    TYPES = ReadSerializer.extract_constants(ChannelEvent.TYPE_CONFIG)

    type = serializers.SerializerMethodField()
    contact = serializers.SerializerMethodField()
    channel = serializers.SerializerMethodField()

    def get_type(self, obj):
        return self.TYPES.get(obj.event_type)

    def get_contact(self, obj):
        return {'uuid': obj.contact.uuid, 'name': obj.contact.name}

    def get_channel(self, obj):
        return {'uuid': obj.channel.uuid, 'name': obj.channel.name}

    class Meta:
        model = ChannelEvent
        fields = ('id', 'type', 'contact', 'channel', 'time', 'duration', 'created_on')


class CampaignReadSerializer(ReadSerializer):
    group = serializers.SerializerMethodField()

    def get_group(self, obj):
        return {'uuid': obj.group.uuid, 'name': obj.group.name}

    class Meta:
        model = Campaign
        fields = ('uuid', 'name', 'group', 'created_on')


class CampaignEventReadSerializer(ReadSerializer):
    UNITS = ReadSerializer.extract_constants(CampaignEvent.UNIT_CONFIG)

    campaign = serializers.SerializerMethodField()
    flow = serializers.SerializerMethodField()
    relative_to = serializers.SerializerMethodField()
    unit = serializers.SerializerMethodField()

    def get_campaign(self, obj):
        return {'uuid': obj.campaign.uuid, 'name': obj.campaign.name}

    def get_flow(self, obj):
        if obj.event_type == CampaignEvent.TYPE_FLOW:
            return {'uuid': obj.flow.uuid, 'name': obj.flow.name}
        else:
            return None

    def get_relative_to(self, obj):
        return {'key': obj.relative_to.key, 'label': obj.relative_to.label}

    def get_unit(self, obj):
        return self.UNITS.get(obj.unit)

    class Meta:
        model = CampaignEvent
        fields = ('uuid', 'campaign', 'relative_to', 'offset', 'unit', 'delivery_hour', 'flow', 'message', 'created_on')


class ChannelReadSerializer(ReadSerializer):
    country = serializers.SerializerMethodField()
    device = serializers.SerializerMethodField()

    def get_country(self, obj):
        return unicode(obj.country) if obj.country else None

    def get_device(self, obj):
        if obj.channel_type != ANDROID:
            return None

        return {
            'name': obj.device,
            'power_level': obj.get_last_power(),
            'power_status': obj.get_last_power_status(),
            'power_source': obj.get_last_power_source(),
            'network_type': obj.get_last_network_type()
        }

    class Meta:
        model = Channel
        fields = ('uuid', 'name', 'address', 'country', 'device', 'last_seen', 'created_on')


class ContactReadSerializer(ReadSerializer):
    name = serializers.SerializerMethodField()
    language = serializers.SerializerMethodField()
    urns = serializers.SerializerMethodField()
    groups = serializers.SerializerMethodField()
    fields = serializers.SerializerMethodField('get_contact_fields')
    blocked = serializers.SerializerMethodField()
    stopped = serializers.SerializerMethodField()

    def get_name(self, obj):
        return obj.name if obj.is_active else None

    def get_language(self, obj):
        return obj.language if obj.is_active else None

    def get_urns(self, obj):
        if self.context['org'].is_anon or not obj.is_active:
            return []

        return [urn.urn for urn in obj.get_urns()]

    def get_groups(self, obj):
        if not obj.is_active:
            return []

        groups = obj.prefetched_user_groups if hasattr(obj, 'prefetched_user_groups') else obj.user_groups.all()
        return [{'uuid': g.uuid, 'name': g.name} for g in groups]

    def get_contact_fields(self, obj):
        if not obj.is_active:
            return {}

        fields = {}
        for contact_field in self.context['contact_fields']:
            value = obj.get_field(contact_field.key)
            fields[contact_field.key] = Contact.serialize_field_value(contact_field, value)
        return fields

    def get_blocked(self, obj):
        return obj.is_blocked if obj.is_active else None

    def get_stopped(self, obj):
        return obj.is_stopped if obj.is_active else None

    class Meta:
        model = Contact
        fields = ('uuid', 'name', 'language', 'urns', 'groups', 'fields', 'blocked', 'stopped',
                  'created_on', 'modified_on')


class ContactFieldReadSerializer(ReadSerializer):
    VALUE_TYPES = ReadSerializer.extract_constants(Value.TYPE_CONFIG)

    value_type = serializers.SerializerMethodField()

    def get_value_type(self, obj):
        return self.VALUE_TYPES.get(obj.value_type)

    class Meta:
        model = ContactField
        fields = ('key', 'label', 'value_type')


class ContactGroupReadSerializer(ReadSerializer):
    count = serializers.SerializerMethodField()

    def get_count(self, obj):
        return obj.get_member_count()

    class Meta:
        model = ContactGroup
        fields = ('uuid', 'name', 'query', 'count')


class FlowReadSerializer(ReadSerializer):
    archived = serializers.ReadOnlyField(source='is_archived')
    labels = serializers.SerializerMethodField()
    expires = serializers.ReadOnlyField(source='expires_after_minutes')
    runs = serializers.SerializerMethodField()

    def get_labels(self, obj):
        return [{'uuid': l.uuid, 'name': l.name} for l in obj.labels.all()]

    def get_runs(self, obj):
        return {
            'completed': obj.get_completed_runs(),
            'interrupted': obj.get_interrupted_runs(),
            'expired': obj.get_expired_runs()
        }

    class Meta:
        model = Flow
        fields = ('uuid', 'name', 'archived', 'labels', 'expires', 'runs', 'created_on')


class FlowRunReadSerializer(ReadSerializer):
    NODE_TYPES = {
        FlowStep.TYPE_RULE_SET: 'ruleset',
        FlowStep.TYPE_ACTION_SET: 'actionset'
    }
    EXIT_TYPES = {
        FlowRun.EXIT_TYPE_COMPLETED: 'completed',
        FlowRun.EXIT_TYPE_INTERRUPTED: 'interrupted',
        FlowRun.EXIT_TYPE_EXPIRED: 'expired'
    }

    flow = serializers.SerializerMethodField()
    contact = serializers.SerializerMethodField()
    steps = serializers.SerializerMethodField()
    exit_type = serializers.SerializerMethodField()

    def get_flow(self, obj):
        return {'uuid': obj.flow.uuid, 'name': obj.flow.name}

    def get_contact(self, obj):
        return {'uuid': obj.contact.uuid, 'name': obj.contact.name}

    def get_steps(self, obj):
        # avoiding fetching org again
        run = obj
        run.org = self.context['org']

        steps = []
        for step in obj.steps.all():
            val = step.rule_decimal_value if step.rule_decimal_value is not None else step.rule_value
            steps.append({'type': self.NODE_TYPES.get(step.step_type),
                          'node': step.step_uuid,
                          'arrived_on': format_datetime(step.arrived_on),
                          'left_on': format_datetime(step.left_on),
                          'messages': self.get_step_messages(run, step),
                          'text': step.get_text(run=run),  # TODO remove
                          'value': val,
                          'category': step.rule_category})
        return steps

    def get_exit_type(self, obj):
        return self.EXIT_TYPES.get(obj.exit_type)

    @staticmethod
    def get_step_messages(run, step):
        messages = []
        for m in step.messages.all():
            messages.append({'id': m.id, 'broadcast': m.broadcast_id, 'text': m.text})

        for b in step.broadcasts.all():
            if b.purged:
                text = b.get_translated_text(run.contact, base_language=run.flow.base_language, org=run.org)
                messages.append({'id': None, 'broadcast': b.id, 'text': text})

        return messages

    class Meta:
        model = FlowRun
        fields = ('id', 'flow', 'contact', 'responded', 'steps',
                  'created_on', 'modified_on', 'exited_on', 'exit_type')


class LabelReadSerializer(ReadSerializer):
    count = serializers.SerializerMethodField()

    def get_count(self, obj):
        return obj.get_visible_count()

    class Meta:
        model = Label
        fields = ('uuid', 'name', 'count')


class MsgReadSerializer(ReadSerializer):
    STATUSES = ReadSerializer.extract_constants(STATUS_CONFIG)
    VISIBILITIES = ReadSerializer.extract_constants(Msg.VISIBILITY_CONFIG)
    DIRECTIONS = {
        INCOMING: 'in',
        OUTGOING: 'out'
    }
    MSG_TYPES = {
        INBOX: 'inbox',
        FLOW: 'flow',
        IVR: 'ivr'
    }

    broadcast = serializers.SerializerMethodField()
    contact = serializers.SerializerMethodField()
    urn = serializers.SerializerMethodField()
    channel = serializers.SerializerMethodField()
    direction = serializers.SerializerMethodField()
    type = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()
    archived = serializers.SerializerMethodField()
    visibility = serializers.SerializerMethodField()
    labels = serializers.SerializerMethodField()

    def get_broadcast(self, obj):
        return obj.broadcast_id

    def get_contact(self, obj):
        return {'uuid': obj.contact.uuid, 'name': obj.contact.name}

    def get_urn(self, obj):
        if self.context['org'].is_anon:
            return None
        elif obj.contact_urn_id:
            return obj.contact_urn.urn
        else:
            return None

    def get_channel(self, obj):
        return {'uuid': obj.channel.uuid, 'name': obj.channel.name} if obj.channel_id else None

    def get_direction(self, obj):
        return self.DIRECTIONS.get(obj.direction)

    def get_type(self, obj):
        return self.MSG_TYPES.get(obj.msg_type)

    def get_status(self, obj):
        # PENDING and QUEUED are same as far as users are concerned
        return self.STATUSES.get(QUEUED if obj.status == PENDING else obj.status)

    def get_archived(self, obj):
        return obj.visibility == Msg.VISIBILITY_ARCHIVED

    def get_visibility(self, obj):
        return self.VISIBILITIES.get(obj.visibility)

    def get_labels(self, obj):
        return [{'uuid': l.uuid, 'name': l.name} for l in obj.labels.all()]

    class Meta:
        model = Msg
        fields = ('id', 'broadcast', 'contact', 'urn', 'channel',
                  'direction', 'type', 'status', 'archived', 'visibility', 'text', 'labels',
                  'created_on', 'sent_on', 'modified_on')
