from __future__ import absolute_import, unicode_literals

from rest_framework import serializers
from temba.contacts.models import Contact, ContactField, ContactGroup
from temba.flows.models import FlowRun, ACTION_SET, RULE_SET
from temba.msgs.models import Broadcast, Msg, Label, STATUS_CONFIG, ARCHIVED, INCOMING, OUTGOING, INBOX, FLOW, IVR, PENDING, QUEUED
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
    def save(self, **kwargs):  # pragma: no cover
        raise ValueError("Can't call save on a read serializer")


# ============================================================
# Serializers (A-Z)
# ============================================================

class BroadcastReadSerializer(ReadSerializer):
    STATUSES = {s[0]: s[2] for s in STATUS_CONFIG}

    urns = serializers.SerializerMethodField()
    contacts = serializers.SerializerMethodField()
    groups = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()

    def get_urns(self, obj):
        if obj.org.is_anon:
            return []
        else:
            return [urn.urn for urn in obj.urns.all()]

    def get_contacts(self, obj):
        return [{'uuid': c.uuid, 'name': c.name} for c in obj.contacts.all()]

    def get_groups(self, obj):
        return [{'uuid': g.uuid, 'name': g.name} for g in obj.groups.all()]

    def get_status(self, obj):
        return self.STATUSES.get(obj.status)

    class Meta:
        model = Broadcast
        fields = ('id', 'urns', 'contacts', 'groups', 'text', 'created_on', 'status')


class ContactReadSerializer(ReadSerializer):
    name = serializers.SerializerMethodField()
    language = serializers.SerializerMethodField()
    urns = serializers.SerializerMethodField()
    groups = serializers.SerializerMethodField()
    fields = serializers.SerializerMethodField('get_contact_fields')
    blocked = serializers.SerializerMethodField()
    failed = serializers.SerializerMethodField()

    def get_name(self, obj):
        return obj.name if obj.is_active else None

    def get_language(self, obj):
        return obj.language if obj.is_active else None

    def get_urns(self, obj):
        if obj.org.is_anon or not obj.is_active:
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

    def get_failed(self, obj):
        return obj.is_failed if obj.is_active else None

    class Meta:
        model = Contact
        fields = ('uuid', 'name', 'language', 'urns', 'groups', 'fields', 'blocked', 'failed',
                  'created_on', 'modified_on')


class ContactFieldReadSerializer(ReadSerializer):
    VALUE_TYPES = {c[0]: c[2] for c in Value.TYPE_CONFIG}

    value_type = serializers.SerializerMethodField()

    def get_value_type(self, obj):
        return self.VALUE_TYPES.get(obj.value_type)

    class Meta:
        model = ContactField
        fields = ('key', 'label', 'value_type')


class ContactGroupReadSerializer(ReadSerializer):
    class Meta:
        model = ContactGroup
        fields = ('uuid', 'name', 'count')


class FlowRunReadSerializer(ReadSerializer):
    NODE_TYPES = {
        RULE_SET: 'ruleset',
        ACTION_SET: 'actionset'
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
        steps = []
        for step in obj.steps.all():
            val = step.rule_decimal_value if step.rule_decimal_value is not None else step.rule_value
            steps.append({'type': self.NODE_TYPES.get(step.step_type),
                          'node': step.step_uuid,
                          'arrived_on': format_datetime(step.arrived_on),
                          'left_on': format_datetime(step.left_on),
                          'text': step.get_text(),
                          'value': val,
                          'category': step.rule_category})
        return steps

    def get_exit_type(self, obj):
        return self.EXIT_TYPES.get(obj.exit_type)

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
    STATUSES = {s[0]: s[2] for s in STATUS_CONFIG}
    VISIBILITIES = {s[0]: s[2] for s in Msg.VISIBILITY_CONFIG}
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
        if obj.org.is_anon:
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
        return obj.visibility == ARCHIVED

    def get_visibility(self, obj):
        return self.VISIBILITIES.get(obj.visibility)

    def get_labels(self, obj):
        return [{'uuid': l.uuid, 'name': l.name} for l in obj.labels.all()]

    class Meta:
        model = Msg
        fields = ('id', 'broadcast', 'contact', 'urn', 'channel',
                  'direction', 'type', 'status', 'archived', 'visibility', 'text', 'labels',
                  'created_on', 'sent_on', 'modified_on')
