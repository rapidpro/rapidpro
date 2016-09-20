from __future__ import absolute_import, unicode_literals

import json
import six

from django.forms import ValidationError
from rest_framework import serializers
from temba.api.models import Resthook, ResthookSubscriber, WebHookEvent
from temba.campaigns.models import Campaign, CampaignEvent
from temba.channels.models import Channel, ChannelEvent
from temba.contacts.models import Contact, ContactField, ContactGroup
from temba.flows.models import Flow, FlowRun, FlowStep, FlowStart
from temba.locations.models import AdminBoundary
from temba.msgs.models import Broadcast, Msg, Label, STATUS_CONFIG, INCOMING, OUTGOING, INBOX, FLOW, IVR, PENDING
from temba.msgs.models import QUEUED
from temba.utils import datetime_to_json_date
from temba.values.models import Value

from . import fields


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


class WriteSerializer(serializers.Serializer):
    """
    The normal REST framework way is to have the view decide if it's an update on existing instance or a create for a
    new instance. Since our logic for that gets relatively complex, we have the serializer make that call.
    """

    def __init__(self, *args, **kwargs):
        super(WriteSerializer, self).__init__(*args, **kwargs)
        self.instance = None

    def run_validation(self, data=serializers.empty):
        if not isinstance(data, dict):
            raise serializers.ValidationError(detail={
                'non_field_errors': ["Request body should be a single JSON object"]
            })

        return super(WriteSerializer, self).run_validation(data)


# ============================================================
# Serializers (A-Z)
# ============================================================

class AdminBoundaryReadSerializer(ReadSerializer):
    parent = serializers.SerializerMethodField()
    aliases = serializers.SerializerMethodField()
    geometry = serializers.SerializerMethodField()

    def get_parent(self, obj):
        return {'osm_id': obj.parent.osm_id, 'name': obj.parent.name} if obj.parent else None

    def get_aliases(self, obj):
        return [alias.name for alias in obj.aliases.all()]

    def get_geometry(self, obj):
        if self.context['include_geometry'] and obj.simplified_geometry:
            return json.loads(obj.simplified_geometry.geojson)
        else:
            return None

    class Meta:
        model = AdminBoundary
        fields = ('osm_id', 'name', 'parent', 'level', 'aliases', 'geometry')


class BroadcastReadSerializer(ReadSerializer):
    urns = serializers.SerializerMethodField()
    contacts = fields.ContactField(many=True)
    groups = fields.ContactGroupField(many=True)

    def get_urns(self, obj):
        if self.context['org'].is_anon:
            return None
        else:
            return [urn.urn for urn in obj.urns.all()]

    class Meta:
        model = Broadcast
        fields = ('id', 'urns', 'contacts', 'groups', 'text', 'created_on')


class BroadcastWriteSerializer(WriteSerializer):
    text = serializers.CharField(required=True, max_length=480)
    urns = fields.URNListField(required=False)
    contacts = fields.ContactField(many=True, required=False)
    groups = fields.ContactGroupField(many=True, required=False)
    channel = fields.ChannelField(required=False)

    def validate(self, data):
        if self.context['org'].is_suspended():
            raise serializers.ValidationError("Sorry, your account is currently suspended. "
                                              "To enable sending messages, please contact support.")

        if not (data.get('urns') or data.get('contacts') or data.get('groups')):
            raise serializers.ValidationError("Must provide either urns, contacts or groups")

        return data

    def save(self):
        """
        Create a new broadcast to send out
        """
        from temba.msgs.tasks import send_broadcast_task

        recipients = self.validated_data.get('contacts', []) + self.validated_data.get('groups', [])

        for urn in self.validated_data.get('urns', []):
            # create contacts for URNs if necessary
            contact = Contact.get_or_create(self.context['org'], self.context['user'], urns=[urn])
            contact_urn = contact.urn_objects[urn]
            recipients.append(contact_urn)

        # create the broadcast
        broadcast = Broadcast.create(self.context['org'], self.context['user'], self.validated_data['text'],
                                     recipients=recipients, channel=self.validated_data.get('channel'))

        # send in task
        send_broadcast_task.delay(broadcast.id)
        return broadcast


class ChannelEventReadSerializer(ReadSerializer):
    TYPES = ReadSerializer.extract_constants(ChannelEvent.TYPE_CONFIG)

    type = serializers.SerializerMethodField()
    contact = fields.ContactField()
    channel = fields.ChannelField()

    def get_type(self, obj):
        return self.TYPES.get(obj.event_type)

    class Meta:
        model = ChannelEvent
        fields = ('id', 'type', 'contact', 'channel', 'time', 'duration', 'created_on')


class CampaignReadSerializer(ReadSerializer):
    group = fields.ContactGroupField()

    class Meta:
        model = Campaign
        fields = ('uuid', 'name', 'group', 'created_on')


class CampaignEventReadSerializer(ReadSerializer):
    UNITS = ReadSerializer.extract_constants(CampaignEvent.UNIT_CONFIG)

    campaign = fields.CampaignField()
    flow = serializers.SerializerMethodField()
    relative_to = serializers.SerializerMethodField()
    unit = serializers.SerializerMethodField()

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
        return six.text_type(obj.country) if obj.country else None

    def get_device(self, obj):
        if obj.channel_type != Channel.TYPE_ANDROID:
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


class ContactWriteSerializer(WriteSerializer):
    uuid = serializers.UUIDField(required=False)
    urn = fields.URNField(required=False)
    name = serializers.CharField(required=False, max_length=64, allow_null=True)
    language = serializers.CharField(required=False, min_length=3, max_length=3, allow_null=True)
    urns = fields.URNListField(required=False)
    groups = fields.ContactGroupField(many=True, required=False)
    fields = serializers.DictField(required=False)

    def __init__(self, *args, **kwargs):
        super(ContactWriteSerializer, self).__init__(*args, **kwargs)

    def validate_uuid(self, value):
        self.instance = Contact.objects.filter(org=self.context['org'], uuid=value, is_active=True).first()
        if not self.instance:
            raise serializers.ValidationError("No such contact with UUID: %s" % value)

    def validate_urn(self, value):
        if self.context['org'].is_anon:
            raise serializers.ValidationError("Referencing by URN not allowed for anonymous organizations")

        self.instance = Contact.from_urn(self.context['org'], value)
        return value

    def validate_groups(self, value):
        for group in value:
            if group.is_dynamic:
                raise serializers.ValidationError("Can't add contact to dynamic group with UUID: %s" % group.uuid)

        return value

    def validate_fields(self, value):
        valid_keys = {f.key for f in self.context['contact_fields']}

        for field_key, field_val in value.items():
            if field_key not in valid_keys:
                raise serializers.ValidationError("Invalid contact field key: %s" % field_key)

        return value

    def validate(self, data):
        org = self.context['org']

        # we don't allow updating of contact URNs for anon orgs - tho we do allow creation of contacts with URNs
        if org.is_anon and self.instance and data.get('urns'):
            raise serializers.ValidationError("Updating contact URNs not allowed for anonymous organizations")

        # if creating a contact, urns can't include URNs which are already taken
        if not self.instance and 'urns' in data:
            country_code = org.get_country_code()
            for urn in data['urns']:
                if Contact.from_urn(org, urn, country_code):
                    raise serializers.ValidationError("Contact URN belongs to another contact: %s" % urn)

        # if contact is blocked, they can't be added to groups
        if self.instance and (self.instance.is_blocked or self.instance.is_stopped) and data['groups']:
            raise serializers.ValidationError("Blocked or stopped contacts can't be added to groups")

        return data

    def save(self):
        """
        Update our contact
        """
        name = self.validated_data.get('name')
        language = self.validated_data.get('language')
        urns = self.validated_data.get('urns')
        groups = self.validated_data.get('groups')
        fields = self.validated_data.get('fields')

        changed = []

        if self.instance:
            # update our name and language
            if 'name' in self.validated_data and name != self.instance.name:
                self.instance.name = name
                changed.append('name')
            if 'language' in self.validated_data and language != self.instance.language:
                self.instance.language = language
                changed.append('language')

            if 'urns' in self.validated_data and urns is not None:
                self.instance.update_urns(self.context['user'], urns)

            if changed:
                self.instance.save(update_fields=changed)
        else:
            if urns is None:
                # if user is using URN as identifier, ok to create contact from it if they don't already exist
                urn_as_id = self.validated_data.get('urn')
                if urn_as_id:
                    urns = [urn_as_id]
                else:
                    urns = []

            self.instance = Contact.get_or_create(self.context['org'], self.context['user'], name, urns=urns, language=language)

        # update our fields
        if fields is not None:
            for key, value in fields.items():
                self.instance.set_field(self.context['user'], key, value)

        # update our groups
        if groups is not None:
            self.instance.update_static_groups(self.context['user'], groups)

        return self.instance


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


class ContactGroupWriteSerializer(WriteSerializer):
    uuid = fields.ContactGroupField(required=False)
    name = serializers.CharField(required=True, max_length=ContactGroup.MAX_NAME_LEN)

    def validate_name(self, value):
        if not ContactGroup.is_valid_name(value):
            raise serializers.ValidationError("Name contains illegal characters or is longer than %d characters"
                                              % ContactGroup.MAX_NAME_LEN)
        return value

    def validate(self, data):
        instance = data.get('uuid')
        name = data.get('name')

        if not instance and ContactGroup.user_groups.filter(org=self.context['org'], name=name).exists():
            raise serializers.ValidationError("Name must be unique")

        return data

    def save(self):
        instance = self.validated_data.get('uuid')
        name = self.validated_data.get('name')

        if instance:
            instance.name = name
            instance.save(update_fields=('name',))
            return instance
        else:
            return ContactGroup.get_or_create(self.context['org'], self.context['user'], name)


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

    flow = fields.FlowField()
    contact = fields.ContactField()
    steps = serializers.SerializerMethodField()
    exit_type = serializers.SerializerMethodField()

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


class FlowStartReadSerializer(ReadSerializer):
    STATUSES = {
        FlowStart.STATUS_PENDING: 'pending',
        FlowStart.STATUS_STARTING: 'starting',
        FlowStart.STATUS_COMPLETE: 'complete',
        FlowStart.STATUS_FAILED: 'failed'
    }

    flow = fields.FlowField()
    status = serializers.SerializerMethodField()
    groups = fields.ContactGroupField(many=True)
    contacts = fields.ContactField(many=True)
    extra = serializers.SerializerMethodField()

    def get_status(self, obj):
        return FlowStartReadSerializer.STATUSES.get(obj.status)

    def get_extra(self, obj):
        if not obj.extra:
            return None
        else:
            return json.loads(obj.extra)

    class Meta:
        model = FlowStart
        fields = ('id', 'flow', 'status', 'groups', 'contacts', 'restart_participants', 'extra', 'created_on', 'modified_on')


class FlowStartWriteSerializer(WriteSerializer):
    flow = fields.FlowField()
    contacts = fields.ContactField(many=True, required=False)
    groups = fields.ContactGroupField(many=True, required=False)
    urns = fields.URNListField(required=False)
    restart_participants = serializers.BooleanField(required=False)
    extra = serializers.JSONField(required=False)

    def validate_extra(self, value):
        if not value:
            return None
        else:
            return FlowRun.normalize_fields(value)[0]

    def validate(self, data):
        # need at least one of urns, groups or contacts
        args = data.get('groups', []) + data.get('contacts', []) + data.get('urns', [])
        if not args:
            raise ValidationError("Must specify at least one group, contact or URN")

        return data

    def save(self):
        urns = self.validated_data.get('urns', [])
        contacts = self.validated_data.get('contacts', [])
        groups = self.validated_data.get('groups', [])
        restart_participants = self.validated_data.get('restart_participants', True)
        extra = self.validated_data.get('extra')

        # convert URNs to contacts
        for urn in urns:
            contact = Contact.get_or_create(self.context['org'], self.context['user'], urns=[urn])
            contacts.append(contact)

        # ok, let's go create our flow start, the actual starting will happen in our view
        return FlowStart.create(self.validated_data['flow'], self.context['user'],
                                restart_participants=restart_participants,
                                contacts=contacts, groups=groups, extra=extra)


class LabelReadSerializer(ReadSerializer):
    count = serializers.SerializerMethodField()

    def get_count(self, obj):
        return obj.get_visible_count()

    class Meta:
        model = Label
        fields = ('uuid', 'name', 'count')


class LabelWriteSerializer(WriteSerializer):
    uuid = fields.LabelField(required=False)
    name = serializers.CharField(required=True, max_length=Label.MAX_NAME_LEN)

    def validate_name(self, value):
        if not Label.is_valid_name(value):
            raise serializers.ValidationError("Name contains illegal characters or is longer than %d characters"
                                              % Label.MAX_NAME_LEN)
        return value

    def validate(self, data):
        instance = data.get('uuid')
        name = data.get('name')

        if not instance and Label.label_objects.filter(org=self.context['org'], name=name).exists():
            raise serializers.ValidationError("Name must be unique")

        return data

    def save(self):
        instance = self.validated_data.get('uuid')
        name = self.validated_data.get('name')

        if instance:
            instance.name = name
            instance.save(update_fields=('name',))
            return instance
        else:
            return Label.get_or_create(self.context['org'], self.context['user'], name)


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
    contact = fields.ContactField()
    urn = fields.URNField(source='contact_urn')
    channel = fields.ChannelField()
    direction = serializers.SerializerMethodField()
    type = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()
    archived = serializers.SerializerMethodField()
    visibility = serializers.SerializerMethodField()
    labels = fields.LabelField(many=True)

    def get_broadcast(self, obj):
        return obj.broadcast_id

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

    class Meta:
        model = Msg
        fields = ('id', 'broadcast', 'contact', 'urn', 'channel',
                  'direction', 'type', 'status', 'archived', 'visibility', 'text', 'labels',
                  'created_on', 'sent_on', 'modified_on')


class ResthookReadSerializer(ReadSerializer):
    resthook = serializers.SerializerMethodField()

    def get_resthook(self, obj):
        return obj.slug

    class Meta:
        model = Resthook
        fields = ('resthook', 'modified_on', 'created_on')


class ResthookSubscriberReadSerializer(ReadSerializer):
    resthook = serializers.SerializerMethodField()

    def get_resthook(self, obj):
        return obj.resthook.slug

    class Meta:
        model = ResthookSubscriber
        fields = ('id', 'resthook', 'target_url', 'created_on')


class ResthookSubscriberWriteSerializer(WriteSerializer):
    resthook = serializers.CharField(required=True)
    target_url = serializers.URLField(required=True)

    def validate_resthook(self, value):
        resthook = Resthook.objects.filter(is_active=True, org=self.context['org'], slug=value).first()
        if not resthook:
            raise serializers.ValidationError("No resthook with slug: %s" % value)
        return resthook

    def validate(self, data):
        resthook = data['resthook']
        target_url = data['target_url']

        # make sure this combination doesn't already exist
        if ResthookSubscriber.objects.filter(resthook=resthook, target_url=target_url, is_active=True):
            raise serializers.ValidationError("URL is already subscribed to this event.")

        return data

    def save(self):
        resthook = self.validated_data['resthook']
        target_url = self.validated_data['target_url']
        return resthook.add_subscriber(target_url, self.context['user'])


class WebHookEventReadSerializer(ReadSerializer):
    resthook = serializers.SerializerMethodField()
    data = serializers.SerializerMethodField()

    def get_resthook(self, obj):
        return obj.resthook.slug

    def get_data(self, obj):
        decoded = json.loads(obj.data)

        # also decode values and steps
        decoded['values'] = json.loads(decoded['values'])
        decoded['steps'] = json.loads(decoded['steps'])
        return decoded

    class Meta:
        model = WebHookEvent
        fields = ('resthook', 'data', 'created_on')
