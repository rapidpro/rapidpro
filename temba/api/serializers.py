from __future__ import unicode_literals

import json
import phonenumbers

from django.core.exceptions import ValidationError
from django.conf import settings
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import ugettext_lazy as _
from rest_framework import serializers
from temba.campaigns.models import Campaign, CampaignEvent, FLOW_EVENT, MESSAGE_EVENT
from temba.channels.models import Channel
from temba.contacts.models import Contact, ContactField, ContactGroup, ContactURN, TEL_SCHEME
from temba.flows.models import Flow, FlowRun, FlowStep
from temba.locations.models import AdminBoundary
from temba.msgs.models import Msg, Call, Broadcast, Label, ARCHIVED, DELETED, INCOMING
from temba.values.models import VALUE_TYPE_CHOICES

# Maximum number of items that can be passed to bulk action endpoint. We don't currently enforce this for messages but
# we may in the future.
MAX_BULK_ACTION_ITEMS = 100


# ------------------------------------------------------------------------------------------
# Field types
# ------------------------------------------------------------------------------------------

class DictionaryField(serializers.WritableField):

    def to_native(self, obj):
        raise ValidationError("Reading of extra field not supported")

    def from_native(self, data):
        if isinstance(data, dict):
            for key in data.keys():
                value = data[key]

                if not isinstance(key, basestring) or not isinstance(value, basestring):
                    raise ValidationError("Invalid, keys and values must both be strings: %s" % unicode(value))
            return data
        else:
            raise ValidationError("Invalid, must be dictionary: %s" % data)


class IntegerArrayField(serializers.WritableField):

    def to_native(self, obj):
        raise ValidationError("Reading of integer array field not supported")

    def from_native(self, data):
        # single number case, this is ok
        if isinstance(data, int) or isinstance(data, long):
            return [data]
        # it's a list, make sure they are all numbers
        elif isinstance(data, list):
            for value in data:
                if not (isinstance(value, int) or isinstance(value, long)):
                    raise ValidationError("Invalid, values must be integers or longs: %s" % unicode(value))
            return data
        # none of the above, error
        else:
            raise ValidationError("Invalid, must be array: %s" % data)


class StringArrayField(serializers.WritableField):

    def to_native(self, obj):
        raise ValidationError("Reading of string array field not supported")

    def from_native(self, data):
        # single string case, this is ok
        if isinstance(data, basestring):
            return [data]
        # it's a list, make sure they are all strings
        elif isinstance(data, list):
            for value in data:
                if not isinstance(value, basestring):
                    raise ValidationError("Invalid, values must be strings: %s" % unicode(value))
            return data
        # none of the above, error
        else:
            raise ValidationError("Invalid, must be array: %s" % data)


class PhoneArrayField(serializers.WritableField):

    def to_native(self, obj):
        raise ValidationError("Reading of phone field not supported")

    def from_native(self, data):
        if isinstance(data, basestring):
            return [(TEL_SCHEME, data)]
        elif isinstance(data, list):
            if len(data) > 100:
                raise ValidationError("You can only specify up to 100 numbers at a time.")

            urns = []
            for phone in data:
                if not isinstance(phone, basestring):
                    raise ValidationError("Invalid phone: %s" % str(phone))
                urns.append((TEL_SCHEME, phone))

            return urns
        else:
            raise ValidationError("Invalid phone: %s" % data)


class FlowField(serializers.PrimaryKeyRelatedField):

    def __init__(self, **kwargs):
        super(FlowField, self).__init__(queryset=Flow.objects.filter(is_active=True), **kwargs)


class ChannelField(serializers.PrimaryKeyRelatedField):

    def __init__(self, **kwargs):
        super(ChannelField, self).__init__(queryset=Channel.objects.filter(is_active=True), **kwargs)


class UUIDField(serializers.CharField):

    def __init__(self, **kwargs):
        super(UUIDField, self).__init__(max_length=36, **kwargs)


# ------------------------------------------------------------------------------------------
# Serializers
# ------------------------------------------------------------------------------------------

class WriteSerializer(serializers.Serializer):

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user')
        self.org = kwargs.pop('org') if 'org' in kwargs else self.user.get_org()

        super(WriteSerializer, self).__init__(*args, **kwargs)

    def restore_fields(self, data, files):

        if not isinstance(data, dict):
            self._errors['non_field_errors'] = ["Request body should be a single JSON object"]
            return {}

        return super(WriteSerializer, self).restore_fields(data, files)


class MsgReadSerializer(serializers.ModelSerializer):
    id = serializers.SerializerMethodField('get_id')
    broadcast = serializers.SerializerMethodField('get_broadcast')
    contact = serializers.SerializerMethodField('get_contact_uuid')
    urn = serializers.SerializerMethodField('get_urn')
    status = serializers.SerializerMethodField('get_status')
    archived = serializers.SerializerMethodField('get_archived')
    relayer = serializers.SerializerMethodField('get_relayer')
    type = serializers.SerializerMethodField('get_type')
    labels = serializers.SerializerMethodField('get_labels')

    def get_id(self, obj):
        return obj.pk

    def get_broadcast(self, obj):
        return obj.broadcast_id

    def get_type(self, obj):
        return obj.msg_type

    def get_urn(self, obj):
        if obj.org.is_anon:
            return None
        elif obj.contact_urn:
            return obj.contact_urn.urn
        else:
            return None

    def get_contact_uuid(self, obj):
        return obj.contact.uuid

    def get_relayer(self, obj):
        return obj.channel_id

    def get_status(self, obj):
        # PENDING and QUEUED are same as far as users are concerned
        return 'Q' if obj.status in ['Q', 'P'] else obj.status

    def get_archived(self, obj):
        return obj.visibility == ARCHIVED

    def get_labels(self, obj):
        return [l.name for l in obj.labels.all()]

    class Meta:
        model = Msg
        fields = ('id', 'broadcast', 'contact', 'urn', 'status', 'type', 'labels', 'relayer',
                  'direction', 'archived', 'text', 'created_on', 'sent_on', 'delivered_on')


class MsgBulkActionSerializer(WriteSerializer):
    messages = IntegerArrayField(required=True)
    action = serializers.CharField(required=True)
    label = serializers.CharField(required=False)
    label_uuid = serializers.CharField(required=False)

    def validate(self, attrs):
        label_provided = attrs.get('label', None) or attrs.get('label_uuid', None)
        if attrs['action'] in ('label', 'unlabel') and not label_provided:
            raise ValidationError("For action %s you should also specify label or label_uuid" % attrs['action'])
        elif attrs['action'] in ('archive', 'unarchive', 'delete') and label_provided:
            raise ValidationError("For action %s you should not specify label or label_uuid" % attrs['action'])
        return attrs

    def validate_action(self, attrs, source):
        if attrs[source] not in ('label', 'unlabel', 'archive', 'unarchive', 'delete'):
            raise ValidationError("Invalid action name: %s" % attrs[source])
        return attrs

    def validate_label(self, attrs, source):
        label_name = attrs.get(source, None)
        if label_name:
            if not Label.is_valid_name(label_name):
                raise ValidationError("Label name must not be blank or begin with + or -")

            attrs['label'] = Label.get_or_create(self.org, self.user, label_name, None)
        return attrs

    def validate_label_uuid(self, attrs, source):
        label_uuid = attrs.get(source, None)
        if label_uuid:
            label = Label.label_objects.filter(org=self.org, uuid=label_uuid).first()
            if not label:
                raise ValidationError("No such label with UUID: %s" % label_uuid)
            attrs['label'] = label
        return attrs

    def restore_object(self, attrs, instance=None):
        if instance:  # pragma: no cover
            raise ValidationError("Invalid operation")

        msg_ids = attrs['messages']
        action = attrs['action']

        msgs = Msg.objects.filter(org=self.org, direction=INCOMING, pk__in=msg_ids).exclude(visibility=DELETED)
        msgs = msgs.select_related('contact')

        if action == 'label':
            attrs['label'].toggle_label(msgs, add=True)
        elif action == 'unlabel':
            attrs['label'].toggle_label(msgs, add=False)
        else:
            # these are in-efficient but necessary to keep cached counts correct. In future if counts are completely
            # driven by triggers these could be replaced with queryset bulk update operations.
            for msg in msgs:
                if action == 'archive':
                    msg.archive()
                elif action == 'unarchive':
                    msg.restore()
                elif action == 'delete':
                    msg.release()

    class Meta:
        fields = ('messages', 'action', 'label', 'label_uuid')


class LabelReadSerializer(serializers.ModelSerializer):
    uuid = serializers.Field(source='uuid')
    name = serializers.Field(source='name')
    count = serializers.SerializerMethodField('get_count')

    def get_count(self, obj):
        return obj.get_visible_count()

    class Meta:
        model = Label
        fields = ('uuid', 'name', 'count')


class LabelWriteSerializer(WriteSerializer):
    uuid = serializers.CharField(required=False)
    name = serializers.CharField(required=True)

    def validate_uuid(self, attrs, source):
        uuid = attrs.get(source, None)

        if uuid and not Label.label_objects.filter(org=self.org, uuid=uuid).exists():
            raise ValidationError("No such message label with UUID: %s" % uuid)

        return attrs

    def validate_name(self, attrs, source):
        uuid = attrs.get('uuid', None)
        name = attrs.get(source, None)

        if Label.label_objects.filter(org=self.org, name=name).exclude(uuid=uuid).exists():
            raise ValidationError("Label name must be unique")

        return attrs

    def restore_object(self, attrs, instance=None):
        if instance:  # pragma: no cover
            raise ValidationError("Invalid operation")

        uuid = attrs.get('uuid', None)
        name = attrs.get('name', None)

        if uuid:
            existing = Label.label_objects.get(org=self.org, uuid=uuid)
            existing.name = name
            existing.save()
            return existing
        else:
            return Label.get_or_create(self.org, self.user, name)


class ContactGroupReadSerializer(serializers.ModelSerializer):
    group = serializers.Field(source='id')  # deprecated, use uuid
    uuid = serializers.Field(source='uuid')
    name = serializers.Field(source='name')
    size = serializers.SerializerMethodField('get_size')

    def get_size(self, obj):
        return obj.get_member_count()

    class Meta:
        model = ContactGroup
        fields = ('group', 'uuid', 'name', 'size')


class ContactReadSerializer(serializers.ModelSerializer):
    name = serializers.SerializerMethodField('get_name')
    uuid = serializers.Field(source='uuid')
    language = serializers.SerializerMethodField('get_language')
    group_uuids = serializers.SerializerMethodField('get_group_uuids')
    urns = serializers.SerializerMethodField('get_urns')
    fields = serializers.SerializerMethodField('get_contact_fields')
    phone = serializers.SerializerMethodField('get_tel')  # deprecated, use urns
    groups = serializers.SerializerMethodField('get_groups')  # deprecated, use group_uuids
    blocked = serializers.SerializerMethodField('is_blocked')
    failed = serializers.SerializerMethodField('is_failed')

    def get_name(self, obj):
        return obj.name if obj.is_active else None

    def get_language(self, obj):
        return obj.language if obj.is_active else None

    def is_blocked(self, obj):
        return obj.is_blocked if obj.is_active else None

    def is_failed(self, obj):
        return obj.is_failed if obj.is_active else None

    def get_groups(self, obj):
        if not obj.is_active:
            return []

        groups = obj.prefetched_user_groups if hasattr(obj, 'prefetched_user_groups') else obj.user_groups.all()
        return [_.name for _ in groups]

    def get_group_uuids(self, obj):
        if not obj.is_active:
            return []

        groups = obj.prefetched_user_groups if hasattr(obj, 'prefetched_user_groups') else obj.user_groups.all()
        return [_.uuid for _ in groups]

    def get_urns(self, obj):
        if obj.org.is_anon or not obj.is_active:
            return []

        return [urn.urn for urn in obj.get_urns()]

    def get_contact_fields(self, obj):
        fields = dict()
        if not obj.is_active:
            return fields

        for contact_field in self.context['contact_fields']:
            value = obj.get_field(contact_field.key)
            fields[contact_field.key] = Contact.serialize_field_value(contact_field, value)
        return fields

    def get_tel(self, obj):
        return obj.get_urn_display(obj.org, scheme=TEL_SCHEME, full=True) if obj.is_active else None

    class Meta:
        model = Contact
        fields = ('uuid', 'name', 'language', 'group_uuids', 'urns', 'fields',
                  'blocked', 'failed', 'modified_on', 'phone', 'groups')


class ContactWriteSerializer(WriteSerializer):
    uuid = serializers.CharField(required=False, max_length=36)
    name = serializers.CharField(required=False, max_length=64)
    language = serializers.CharField(required=False, min_length=3, max_length=3)
    urns = StringArrayField(required=False)
    group_uuids = StringArrayField(required=False)
    fields = DictionaryField(required=False)
    phone = serializers.CharField(required=False, max_length=16)  # deprecated, use urns
    groups = StringArrayField(required=False)  # deprecated, use group_uuids

    def validate(self, attrs):
        contact = attrs.get('contact')
        urns = attrs.get('urn_objs', [])
        groups = attrs.get('group_objs')

        if attrs.get('urns') is not None and attrs.get('phone') is not None:
            raise ValidationError("Cannot provide both urns and phone parameters together")

        if attrs.get('group_uuids') is not None and attrs.get('groups') is not None:
            raise ValidationError("Parameter groups is deprecated and can't be used together with group_uuids")

        if urns:
            urns_strings = ["%s:%s" % u for u in urns]
            urn_query = Q(pk__lt=0)
            for urn_string in urns_strings:
                urn_query |= Q(urns__urn__iexact=urn_string)

            urn_contacts = Contact.objects.filter(org=self.org).filter(urn_query).distinct()
            if len(urn_contacts) > 1:
                raise ValidationError(_("URNs %s are used by multiple contacts") % urns_strings)

            contact_by_urns = urn_contacts[0] if len(urn_contacts) > 0 else None

            if contact and contact_by_urns != contact:
                raise ValidationError(_("URNs %s are used by other contacts") % urns_strings)
        else:
            contact_by_urns = None

        contact = contact or contact_by_urns

        # if contact is blocked, they can't be added to groups
        if contact and contact.is_blocked and groups:
            raise ValidationError("Cannot add blocked contact to groups")

        return attrs

    def validate_uuid(self, attrs, source):
        uuid = attrs.get(source, '')
        if uuid:
            contact = Contact.objects.filter(org=self.org, uuid=uuid, is_active=True).first()
            if not contact:
                raise ValidationError("Unable to find contact with UUID: %s" % uuid)

            attrs['contact'] = contact

        return attrs

    def validate_phone(self, attrs, source):
        phone = attrs.get(source, None)
        if phone:
            try:
                normalized = phonenumbers.parse(phone, None)
                if not phonenumbers.is_possible_number(normalized):
                    raise ValidationError("Invalid phone number: '%s'" % phone)
            except:  # pragma: no cover
                raise ValidationError("Invalid phone number: '%s'" % phone)

            phone = phonenumbers.format_number(normalized, phonenumbers.PhoneNumberFormat.E164)
            attrs['urn_objs'] = [(TEL_SCHEME, phone)]
        return attrs

    def validate_urns(self, attrs, source):
        urns = None
        request_urns = attrs.get(source, None)

        if request_urns is not None:
            urns = []
            for urn in request_urns:
                try:
                    parsed = ContactURN.parse_urn(urn)
                except ValueError:
                    raise ValidationError("Unable to parse URN: '%s'" % urn)

                norm_scheme, norm_path = ContactURN.normalize_urn(parsed.scheme, parsed.path)

                if not ContactURN.validate_urn(norm_scheme, norm_path):
                    raise ValidationError("Invalid URN: '%s'" % urn)

                urns.append((norm_scheme, norm_path))

        attrs['urn_objs'] = urns
        return attrs

    def validate_fields(self, attrs, source):
        fields = attrs.get(source, {}).items()
        if fields:
            org_fields = self.context['contact_fields']

            for key, value in attrs.get(source, {}).items():
                for field in org_fields:
                    # TODO get users to stop writing fields via labels
                    if field.key == key or field.label == key:
                        break
                else:
                    raise ValidationError("Invalid contact field key: '%s'" % key)

        return attrs

    def validate_groups(self, attrs, source):
        group_names = attrs.get(source, None)
        if group_names is not None:
            groups = []
            for name in group_names:
                if not ContactGroup.is_valid_name(name):
                    raise ValidationError(_("Invalid group name: '%s'") % name)
                groups.append(ContactGroup.get_or_create(self.org, self.user, name))

            attrs['group_objs'] = groups
        return attrs

    def validate_group_uuids(self, attrs, source):
        group_uuids = attrs.get(source, None)
        if group_uuids is not None:
            groups = []
            for uuid in group_uuids:
                group = ContactGroup.user_groups.filter(uuid=uuid, org=self.org, is_active=True).first()
                if not group:
                    raise ValidationError(_("Unable to find contact group with uuid: %s") % uuid)

                groups.append(group)

            attrs['group_objs'] = groups
        return attrs

    def restore_object(self, attrs, instance=None):
        """
        Update our contact
        """
        if instance:  # pragma: no cover
            raise ValidationError("Invalid operation")

        contact = attrs.get('contact')
        urns = attrs.get('urn_objs', None)

        if self.org.is_anon and contact:
            raise ValidationError("Cannot update contacts on anonymous organizations, can only create")

        if contact:
            if urns is not None:
                contact.update_urns(urns)
        else:
            contact = Contact.get_or_create(self.org, self.user, urns=urns)

        changed = []

        # update our name and language
        if attrs.get('name', None):
            contact.name = attrs['name']
            changed.append('name')

        if 'language' in attrs:
            contact.language = attrs['language'].lower() if attrs['language'] else None
            changed.append('language')

        # save our contact if it changed
        if changed:
            contact.save(update_fields=changed)

        # update our fields
        fields = attrs.get('fields', None)
        if fields is not None:
            for key, value in fields.items():
                existing_by_key = ContactField.objects.filter(org=self.org, key__iexact=key, is_active=True).first()
                if existing_by_key:
                    contact.set_field(existing_by_key.key, value)
                    continue

                # TODO as above, need to get users to stop updating via label
                existing_by_label = ContactField.get_by_label(self.org, key)
                if existing_by_label:
                    contact.set_field(existing_by_label.key, value)

        # update our contact's groups
        groups = attrs.get('group_objs')
        if groups is not None:
            contact.update_groups(groups)

        return contact


class ContactFieldReadSerializer(serializers.ModelSerializer):
    key = serializers.Field(source='key')
    label = serializers.Field(source='label')
    value_type = serializers.Field(source='value_type')

    class Meta:
        model = ContactField
        fields = ('key', 'label', 'value_type')


class ContactFieldWriteSerializer(WriteSerializer):
    key = serializers.CharField(required=False)
    label = serializers.CharField(required=True)
    value_type = serializers.CharField(required=True)

    def validate_key(self, attrs, source):
        key = attrs.get(source, None)
        if key and not ContactField.is_valid_key(key):
            raise ValidationError("Field key is invalid or is a reserved name")
        return attrs

    def validate_label(self, attrs, source):
        label = attrs.get(source, None)
        if label and not ContactField.is_valid_label(label):
            raise ValidationError("Invalid Field label: Field labels can only contain letters, numbers and hypens")
        return attrs

    def validate_value_type(self, attrs, source):
        value_type = attrs.get(source, '')
        if value_type and value_type not in [t for t, label in VALUE_TYPE_CHOICES]:
            raise ValidationError("Invalid field value type")
        return attrs

    def validate(self, attrs):
        key = attrs.get('key', None)
        label = attrs.get('label')

        if not key:
            key = ContactField.make_key(label)
            if not ContactField.is_valid_key(key):
                raise ValidationError(_("Generated key for '%s' is invalid or a reserved name") % label)

        attrs['key'] = key
        return attrs

    def restore_object(self, attrs, instance=None):
        """
        Update our contact field
        """
        if instance:  # pragma: no cover
            raise ValidationError("Invalid operation")

        key = attrs.get('key')
        label = attrs.get('label')
        value_type = attrs.get('value_type')

        return ContactField.get_or_create(self.org, key, label, value_type=value_type)


class ContactBulkActionSerializer(WriteSerializer):
    contacts = StringArrayField(required=True)
    action = serializers.CharField(required=True)
    group = serializers.CharField(required=False)
    group_uuid = serializers.CharField(required=False)

    def validate(self, attrs):
        contacts = attrs.get('contacts', [])
        action = attrs.get('action')
        group_provided = attrs.get('group') or attrs.get('group_uuid')

        if action in ('add', 'remove') and not group_provided:
            raise ValidationError("For action %s you should also specify group or group_uuid" % attrs['action'])
        elif action in ('block', 'unblock', 'expire', 'delete') and group_provided:
            raise ValidationError("For action %s you should not specify group or group_uuid" % attrs['action'])

        if action == 'add':
            # if adding to a group, check for blocked contacts
            blocked_uuids = {c.uuid for c in contacts if c.is_blocked}
            if blocked_uuids:
                raise ValidationError("Blocked cannot be added to groups: %s" % ', '.join(blocked_uuids))

        return attrs

    def validate_contacts(self, attrs, source):
        contact_uuids = attrs.get(source, [])
        if len(contact_uuids) > MAX_BULK_ACTION_ITEMS:
            raise ValidationError("Maximum of %d contacts allowed" % MAX_BULK_ACTION_ITEMS)

        contacts = Contact.objects.filter(org=self.org, is_test=False, is_active=True, uuid__in=contact_uuids)

        # check for UUIDs that didn't resolve to a valid contact
        if len(contacts) != len(contact_uuids):
            fetched_uuids = {c.uuid for c in contacts}
            invalid_uuids = [u for u in contact_uuids if u not in fetched_uuids]
            raise ValidationError("Some contacts are invalid: %s" % ', '.join(invalid_uuids))

        attrs['contacts'] = contacts
        return attrs

    def validate_action(self, attrs, source):
        if attrs[source] not in ('add', 'remove', 'block', 'unblock', 'expire', 'delete'):
            raise ValidationError("Invalid action name: %s" % attrs[source])
        return attrs

    def validate_group(self, attrs, source):
        group_name = attrs.get(source, None)
        if group_name:
            group = ContactGroup.user_groups.filter(org=self.org, name=group_name, is_active=True).first()
            if not group:
                raise ValidationError("No such group: %s" % group_name)

            attrs['group'] = group
        return attrs

    def validate_group_uuid(self, attrs, source):
        group_uuid = attrs.get(source, None)
        if group_uuid:
            group = ContactGroup.user_groups.filter(org=self.org, uuid=group_uuid).first()
            if not group:
                raise ValidationError("No such group with UUID: %s" % group_uuid)
            attrs['group'] = group
        return attrs

    def restore_object(self, attrs, instance=None):
        if instance:  # pragma: no cover
            raise ValidationError("Invalid operation")

        contacts = attrs['contacts']
        action = attrs['action']

        if action == 'add':
            attrs['group'].update_contacts(contacts, add=True)
        elif action == 'remove':
            attrs['group'].update_contacts(contacts, add=False)
        elif action == 'expire':
            FlowRun.expire_all_for_contacts(contacts)
        else:
            for contact in contacts:
                if action == 'block':
                    contact.block()
                elif action == 'unblock':
                    contact.unblock()
                elif action == 'delete':
                    contact.release()

    class Meta:
        fields = ('contacts', 'action', 'group', 'group_uuid')


class CampaignEventSerializer(serializers.ModelSerializer):
    campaign_uuid = serializers.SerializerMethodField('get_campaign_uuid')
    flow_uuid = serializers.SerializerMethodField('get_flow_uuid')
    relative_to = serializers.SerializerMethodField('get_relative_to')
    event = serializers.SerializerMethodField('get_event')  # deprecated, use uuid
    campaign = serializers.SerializerMethodField('get_campaign')  # deprecated, use campaign_uuid
    flow = serializers.SerializerMethodField('get_flow')  # deprecated, use flow_uuid

    def get_campaign_uuid(self, obj):
        return obj.campaign.uuid

    def get_flow_uuid(self, obj):
        return obj.flow.uuid if obj.event_type == FLOW_EVENT else None

    def get_campaign(self, obj):
        return obj.campaign.pk

    def get_event(self, obj):
        return obj.pk

    def get_flow(self, obj):
        return obj.flow_id if obj.event_type == FLOW_EVENT else None

    def get_relative_to(self, obj):
        return obj.relative_to.label

    class Meta:
        model = CampaignEvent
        fields = ('uuid', 'campaign_uuid', 'flow_uuid', 'relative_to', 'offset', 'unit', 'delivery_hour', 'message',
                  'created_on', 'event', 'campaign', 'flow')


class CampaignEventWriteSerializer(WriteSerializer):
    uuid = UUIDField(required=False)
    campaign_uuid = UUIDField(required=False)
    offset = serializers.IntegerField(required=True)
    unit = serializers.CharField(required=True, max_length=1)
    delivery_hour = serializers.IntegerField(required=True)
    relative_to = serializers.CharField(required=True, min_length=3, max_length=64)
    message = serializers.CharField(required=False, max_length=320)
    flow_uuid = UUIDField(required=False)
    event = serializers.IntegerField(required=False)  # deprecated, use uuid
    campaign = serializers.IntegerField(required=False)  # deprecated, use campaign_uuid
    flow = serializers.IntegerField(required=False)  # deprecated, use flow_uuid

    def validate_event(self, attrs, source):
        event_id = attrs.get(source, None)
        if event_id:
            event = CampaignEvent.objects.filter(pk=event_id, is_active=True, campaign__org=self.org).first()
            if event:
                attrs['event_obj'] = event
            else:
                raise ValidationError("No event with id %d" % event_id)

        return attrs

    def validate_uuid(self, attrs, source):
        uuid = attrs.get(source, None)
        if uuid:
            event = CampaignEvent.objects.filter(uuid=uuid, is_active=True, campaign__org=self.org).first()
            if event:
                attrs['event_obj'] = event
            else:
                raise ValidationError("No event with UUID %s" % uuid)

        return attrs

    def validate_campaign(self, attrs, source):
        campaign_id = attrs.get(source, None)
        if campaign_id:
            campaign = Campaign.get_campaigns(self.org).filter(pk=campaign_id).first()
            if campaign:
                attrs['campaign_obj'] = campaign
            else:
                raise ValidationError("No campaign with id %d" % campaign_id)

        return attrs

    def validate_campaign_uuid(self, attrs, source):
        campaign_uuid = attrs.get(source, None)
        if campaign_uuid:
            campaign = Campaign.get_campaigns(self.org).filter(uuid=campaign_uuid).first()
            if campaign:
                attrs['campaign_obj'] = campaign
            else:
                raise ValidationError("No campaign with UUID %s" % campaign_uuid)

        return attrs

    def validate_unit(self, attrs, source):
        unit = attrs[source]

        if unit not in ["M", "H", "D", "W"]:
            raise ValidationError("Unit must be one of M, H, D or W for Minute, Hour, Day or Week")

        return attrs

    def validate_delivery_hour(self, attrs, source):
        delivery_hour = attrs[source]

        if delivery_hour < -1 or delivery_hour > 23:
            raise ValidationError("Delivery hour must be either -1 (for same hour) or 0-23")

        return attrs

    def validate_flow(self, attrs, source):
        flow_id = attrs.get(source, None)
        if flow_id:
            flow = Flow.objects.filter(pk=flow_id, is_active=True, org=self.org).first()
            if flow:
                attrs['flow_obj'] = flow
            else:
                raise ValidationError("No flow with id %d" % flow_id)

        return attrs

    def validate_flow_uuid(self, attrs, source):
        flow_uuid = attrs.get(source, None)
        if flow_uuid:
            flow = Flow.objects.filter(uuid=flow_uuid, is_active=True, org=self.org).first()
            if flow:
                attrs['flow_obj'] = flow
            else:
                raise ValidationError("No flow with UUID %s" % flow_uuid)

        return attrs

    def validate(self, attrs):
        if not (attrs.get('message', None) or attrs.get('flow_obj', None)):
            raise ValidationError("Must specify either a flow or a message for the event")

        if attrs.get('message', None) and attrs.get('flow_obj', None):
            raise ValidationError("Events cannot have both a message and a flow")

        if attrs.get('event_obj', None) and attrs.get('campaign_obj', None):
            raise ValidationError("Cannot specify campaign if updating an existing event")

        return attrs

    def restore_object(self, attrs, instance=None):
        """
        Create or update our campaign
        """
        if instance:  # pragma: no cover
            raise ValidationError("Invalid operation")

        # parse our arguments
        event = attrs.get('event_obj', None)
        campaign = attrs.get('campaign_obj')
        offset = attrs.get('offset')
        unit = attrs.get('unit')
        delivery_hour = attrs.get('delivery_hour')
        relative_to_label = attrs.get('relative_to')
        message = attrs.get('message', None)
        flow = attrs.get('flow_obj', None)

        # ensure contact field exists
        relative_to = ContactField.get_by_label(self.org, relative_to_label)
        if not relative_to:
            key = ContactField.make_key(relative_to_label)
            if not ContactField.is_valid_key(key):
                raise ValidationError(_("Cannot create contact field with key '%s'") % key)
            relative_to = ContactField.get_or_create(self.org, key, relative_to_label)

        if event:
            # we are being set to a flow
            if flow:
                event.flow = flow
                event.event_type = FLOW_EVENT
                event.message = None

            # we are being set to a message
            else:
                event.message = message

                # if we aren't currently a message event, we need to create our hidden message flow
                if event.event_type != MESSAGE_EVENT:
                    event.flow = Flow.create_single_message(self.org, self.user, event.message)
                    event.event_type = MESSAGE_EVENT

                # otherwise, we can just update that flow
                else:
                    # set our single message on our flow
                    event.flow.update_single_message_flow(message=attrs['message'])

            # update our other attributes
            event.offset = offset
            event.unit = unit
            event.delivery_hour = delivery_hour
            event.relative_to = relative_to
            event.save()
            event.update_flow_name()

        else:
            if flow:
                event = CampaignEvent.create_flow_event(self.org, self.user, campaign,
                                                        relative_to, offset, unit, flow, delivery_hour)
            else:
                event = CampaignEvent.create_message_event(self.org, self.user, campaign,
                                                           relative_to, offset, unit, message, delivery_hour)
            event.update_flow_name()

        return event


class CampaignSerializer(serializers.ModelSerializer):
    group_uuid = serializers.SerializerMethodField('get_group_uuid')
    group = serializers.SerializerMethodField('get_group')  # deprecated, use group_uuid
    campaign = serializers.SerializerMethodField('get_campaign')  # deprecated, use uuid

    def get_group_uuid(self, obj):
        return obj.group.uuid

    def get_campaign(self, obj):
        return obj.pk

    def get_group(self, obj):
        return obj.group.name

    class Meta:
        model = Campaign
        fields = ('uuid', 'name', 'group_uuid', 'created_on', 'campaign', 'group')


class CampaignWriteSerializer(WriteSerializer):
    uuid = UUIDField(required=False)
    name = serializers.CharField(required=True, max_length=64)
    group_uuid = UUIDField(required=False)
    campaign = serializers.IntegerField(required=False)  # deprecated, use uuid
    group = serializers.CharField(required=False, max_length=64)  # deprecated, use group_uuid

    def validate_uuid(self, attrs, source):
        uuid = attrs.get(source, None)
        if uuid:
            campaign = Campaign.get_campaigns(self.org).filter(uuid=uuid).first()
            if campaign:
                attrs['campaign_obj'] = campaign
            else:
                raise ValidationError("No campaign with UUID %s" % uuid)

        return attrs

    def validate_campaign(self, attrs, source):
        campaign_id = attrs.get(source, None)
        if campaign_id:
            campaign = Campaign.get_campaigns(self.org).filter(pk=campaign_id).first()
            if campaign:
                attrs['campaign_obj'] = campaign
            else:
                raise ValidationError("No campaign with id %d" % campaign_id)

        return attrs

    def validate_group_uuid(self, attrs, source):
        group_uuid = attrs.get(source, None)
        if group_uuid:
            group = ContactGroup.user_groups.filter(org=self.org, is_active=True, uuid=group_uuid).first()
            if group:
                attrs['group_obj'] = group
            else:
                raise ValidationError("No contact group with UUID %s" % group_uuid)

        return attrs

    def validate(self, attrs):
        if not attrs.get('group', None) and not attrs.get('group_uuid', None):
            raise ValidationError("Must specify either group name or group_uuid")

        if attrs.get('campaign', None) and attrs.get('uuid', None):
            raise ValidationError("Can't specify both campaign and uuid")

        return attrs

    def restore_object(self, attrs, instance=None):
        """
        Create or update our campaign
        """
        if instance:  # pragma: no cover
            raise ValidationError("Invalid operation")

        if 'group_obj' in attrs:
            group = attrs['group_obj']
        else:
            group = ContactGroup.get_or_create(self.org, self.user, attrs['group'])

        campaign = attrs.get('campaign_obj', None)

        if campaign:
            campaign.name = attrs['name']
            campaign.group = group
            campaign.save()
        else:
            campaign = Campaign.create(self.org, self.user, attrs['name'], group)

        return campaign


class FlowDefinitionWriteSerializer(WriteSerializer):
    version = serializers.WritableField(required=True)
    metadata = serializers.WritableField(required=False)
    base_language = serializers.WritableField(required=False)
    flow_type = serializers.WritableField(required=False)
    action_sets = serializers.WritableField(required=False)
    rule_sets = serializers.WritableField(required=False)
    entry = serializers.WritableField(required=False)

    # old versions had different top level elements
    uuid = serializers.WritableField(required=False)
    definition = serializers.WritableField(required=False)
    name = serializers.WritableField(required=False)
    id = serializers.WritableField(required=False)

    def validate_version(self, attrs, source):
        version = attrs.get(source)
        from temba.orgs.models import CURRENT_EXPORT_VERSION, EARLIEST_IMPORT_VERSION
        if version > CURRENT_EXPORT_VERSION or version < EARLIEST_IMPORT_VERSION:
            raise ValidationError("Flow version %s not supported" % version)
        return attrs

    def validate_name(self, attrs, source):
        version = attrs.get('version')
        if version < 7:
            name = attrs.get(source)
            if not name:
                raise ValidationError("This field is required for version %s" % version)
        return attrs

    def validate_metadata(self, attrs, source):

        # only required starting at version 7
        version = attrs.get('version')
        if version >= 7:
            metadata = attrs.get(source)
            if metadata:
                if 'name' not in metadata:
                    raise ValidationError("Name is missing from metadata")

                uuid = metadata.get('uuid', None)
                if uuid and not Flow.objects.filter(org=self.org, uuid=uuid).exists():
                    raise ValidationError("No such flow with UUID: %s" % uuid)
            else:
                raise ValidationError("This field is required for version %s" % version)

        return attrs

    def validate_flow_type(self, attrs, source):
        flow_type = attrs.get(source, None)

        if flow_type and flow_type not in [choice[0] for choice in Flow.FLOW_TYPES]:
            raise ValidationError("Invalid flow type: %s" % flow_type)

        return attrs

    def validate_definition(self, attrs, source):
        definition = attrs.get(source, None)
        version = attrs.get('version')
        if version < 7 and not definition:
            attrs['definition'] = dict(action_sets=[], rule_sets=[])
        return attrs

    def restore_object(self, flow_json, instance=None):
        """
        Update our flow
        """
        if instance:  # pragma: no cover
            raise ValidationError("Invalid operation")

        # first, migrate our definition forward if necessary
        from temba.orgs.models import CURRENT_EXPORT_VERSION
        from temba.flows.models import FlowRevision
        version = flow_json.get('version', CURRENT_EXPORT_VERSION)
        if version < CURRENT_EXPORT_VERSION:
            flow_json = FlowRevision.migrate_definition(flow_json, version, CURRENT_EXPORT_VERSION)

        # previous to version 7, uuid could be supplied on the outer element
        uuid = flow_json.get('metadata').get('uuid', flow_json.get('uuid', None))
        name = flow_json.get('metadata').get('name')

        if uuid:
            flow = Flow.objects.get(org=self.org, uuid=uuid)
            flow.name = name

            flow_type = flow_json.get('flow_type', None)
            if flow_type:
                flow.flow_type = flow_type

            flow.save()
        else:
            flow_type = flow_json.get('flow_type', Flow.FLOW)
            flow = Flow.create(self.org, self.user, name, flow_type)

        flow.update(flow_json, self.user, force=True)
        return flow

class FlowReadSerializer(serializers.ModelSerializer):
    uuid = serializers.Field(source='uuid')
    archived = serializers.Field(source='is_archived')
    expires = serializers.Field(source='expires_after_minutes')
    labels = serializers.SerializerMethodField('get_labels')
    rulesets = serializers.SerializerMethodField('get_rulesets')
    runs = serializers.SerializerMethodField('get_runs')
    completed_runs = serializers.SerializerMethodField('get_completed_runs')
    participants = serializers.SerializerMethodField('get_participants')
    flow = serializers.Field(source='id')  # deprecated, use uuid

    def get_runs(self, obj):
        return obj.get_total_runs()

    def get_labels(self, obj):
        return [l.name for l in obj.labels.all()]

    def get_completed_runs(self, obj):
        return obj.get_completed_runs()

    def get_participants(self, obj):
        return obj.get_total_contacts()

    def get_rulesets(self, obj):
        rulesets = list()

        obj.ensure_current_version()

        from temba.flows.models import RuleSet

        for ruleset in obj.rule_sets.all().order_by('y'):

            # backwards compat for old response types
            response_type = 'C'
            if ruleset.ruleset_type == RuleSet.TYPE_WAIT_DIGITS:
                response_type = 'K'
            elif ruleset.ruleset_type == RuleSet.TYPE_WAIT_DIGIT:
                response_type = 'M'
            elif ruleset.ruleset_type == RuleSet.TYPE_WAIT_RECORDING:
                response_type = 'R'
            elif len(ruleset.get_rules()) == 1:
                response_type = 'O'

            rulesets.append(dict(node=ruleset.uuid,
                                 label=ruleset.label,
                                 ruleset_type=ruleset.ruleset_type,
                                 response_type=response_type, # deprecated
                                 id=ruleset.id))  # deprecated

        return rulesets

    class Meta:
        model = Flow
        fields = ('uuid', 'archived', 'expires', 'name', 'labels', 'participants', 'runs', 'completed_runs', 'rulesets',
                  'created_on', 'flow')


class FlowRunStartSerializer(WriteSerializer):
    flow_uuid = serializers.CharField(required=False, max_length=36)
    groups = StringArrayField(required=False)
    contacts = StringArrayField(required=False)
    extra = DictionaryField(required=False)
    restart_participants = serializers.BooleanField(required=False, default=True)
    flow = FlowField(required=False)  # deprecated, use flow_uuid
    contact = StringArrayField(required=False)  # deprecated, use contacts
    phone = PhoneArrayField(required=False)  # deprecated

    def validate(self, attrs):
        if not (attrs.get('flow', None) or attrs.get('flow_uuid', None)):
            raise ValidationError("Use flow_uuid to specify which flow to start")
        return attrs

    def validate_flow_uuid(self, attrs, source):
        flow_uuid = attrs.get(source, None)
        if flow_uuid:
            flow = Flow.objects.get(uuid=flow_uuid)
            if flow.is_archived:
                raise ValidationError("You cannot start an archived flow.")

            # do they have permission to use this flow?
            if self.org != flow.org:
                raise ValidationError("Invalid UUID '%s' - flow does not exist." % flow.uuid)

            attrs['flow'] = flow
        return attrs

    def validate_flow(self, attrs, source):
        flow = attrs.get(source, None)
        if flow:
            if flow.is_archived:
                raise ValidationError("You cannot start an archived flow.")

            # do they have permission to use this flow?
            if self.org != flow.org:
                raise ValidationError("Invalid pk '%d' - flow does not exist." % flow.id)

        return attrs

    def validate_groups(self, attrs, source):
        groups = []
        for uuid in attrs.get(source, []):
            group = ContactGroup.user_groups.filter(uuid=uuid, org=self.org, is_active=True).first()
            if not group:
                raise ValidationError(_("Unable to find contact group with uuid: %s") % uuid)

            groups.append(group)

        attrs['groups'] = groups
        return attrs

    def validate_contacts(self, attrs, source):
        contacts = []
        uuids = attrs.get(source, [])
        if uuids:
            for uuid in uuids:
                contact = Contact.objects.filter(uuid=uuid, org=self.org, is_active=True).first()
                if not contact:
                    raise ValidationError(_("Unable to find contact with uuid: %s") % uuid)

                contacts.append(contact)

            attrs['contacts'] = contacts
        return attrs

    def validate_contact(self, attrs, source):  # deprecated, use contacts
        contacts = []
        uuids = attrs.get(source, [])
        if uuids:
            for uuid in attrs.get(source, []):
                contact = Contact.objects.filter(uuid=uuid, org=self.org, is_active=True).first()
                if not contact:
                    raise ValidationError(_("Unable to find contact with uuid: %s") % uuid)

                contacts.append(contact)

            attrs['contacts'] = contacts
        return attrs

    def validate_phone(self, attrs, source):  # deprecated, use contacts
        if self.org.is_anon:
            raise ValidationError("Cannot start flows for anonymous organizations")

        numbers = attrs.get(source, [])
        if numbers:
            # get a channel
            channel = self.org.get_send_channel(TEL_SCHEME)

            if channel:
                # check our numbers for validity
                for tel, phone in numbers:
                    try:
                        normalized = phonenumbers.parse(phone, channel.country.code)
                        if not phonenumbers.is_possible_number(normalized):
                            raise ValidationError("Invalid phone number: '%s'" % phone)
                    except:
                        raise ValidationError("Invalid phone number: '%s'" % phone)
            else:
                raise ValidationError("You cannot start a flow for a phone number without a phone channel")

        return attrs

    def save(self):
        pass

    def restore_object(self, attrs, instance=None):
        """
        Actually start our flows for each contact
        """
        if instance:  # pragma: no cover
            raise ValidationError("Invalid operation")

        flow = attrs['flow']
        groups = attrs.get('groups', [])
        contacts = attrs.get('contacts', [])
        extra = attrs.get('extra', None)
        restart_participants = attrs.get('restart_participants', True)

        # include contacts created/matched via deprecated phone field
        phone_urns = attrs.get('phone', [])
        if phone_urns:
            channel = self.org.get_send_channel(TEL_SCHEME)
            for urn in phone_urns:
                # treat each URN as separate contact
                contact = Contact.get_or_create(channel.org, self.user, urns=[urn])
                contacts.append(contact)

        if contacts or groups:
            return flow.start(groups, contacts, restart_participants=restart_participants, extra=extra)
        else:
            return []


class FlowRunWriteSerializer(WriteSerializer):
    flow = serializers.CharField(required=True, max_length=36)
    contact = serializers.CharField(required=True, max_length=36)
    started = serializers.DateTimeField(required=True)
    completed = serializers.BooleanField(required=False)
    steps = serializers.WritableField()

    revision = serializers.IntegerField(required=False) # for backwards compatibility
    version = serializers.IntegerField(required=False) # for backwards compatibility

    def validate_steps(self, attrs, source):

        class VersionNode:
            def __init__(self, node, is_ruleset):
                self.node = node
                self.uuid = node['uuid']
                self.ruleset = is_ruleset

            def is_ruleset(self):
                return self.ruleset

            def is_pause(self):
                from temba.flows.models import RuleSet
                return self.node['ruleset_type'] in RuleSet.TYPE_WAIT

            def get_step_type(self):
                from temba.flows.models import RULE_SET, ACTION_SET
                if self.is_ruleset():
                    return RULE_SET
                else:
                    return ACTION_SET

        steps = attrs.get(source)
        flow = attrs.get('flow')
        revision = attrs.get('revision', attrs.get('version'))

        if not revision:
            raise ValidationError("Missing 'revision' field")

        flow_revision = flow.revisions.filter(revision=revision).first()

        if not flow_revision:
            raise ValidationError("Invalid revision: %s" % revision)

        definition = json.loads(flow_revision.definition)

        # make sure we are operating off a current spec
        from temba.flows.models import FlowRevision, CURRENT_EXPORT_VERSION
        definition = FlowRevision.migrate_definition(definition, flow.version_number, CURRENT_EXPORT_VERSION)

        for step in steps:
            node_obj = None
            key = 'rule_sets' if 'rule' in step else 'action_sets'
            for json_node in definition[key]:
                if json_node['uuid'] == step['node']:
                    node_obj = VersionNode(json_node, 'rule' in step)
                    break

            if not node_obj:
                raise ValidationError("No such node with UUID %s in flow '%s'" % (step['node'], flow.name))
            else:
                step['node'] = node_obj

        return attrs

    def validate_flow(self, attrs, source):
        flow_uuid = attrs.get(source, None)
        if flow_uuid:
            flow = Flow.objects.get(uuid=flow_uuid)
            if flow.is_archived:
                raise ValidationError("You cannot start an archived flow.")

            # do they have permission to use this flow?
            if self.org != flow.org:
                raise ValidationError("Invalid UUID '%s' - flow does not exist." % flow.uuid)

            attrs['flow'] = flow
        return attrs

    def validate_contact(self, attrs, source):
        contact_uuid = attrs.get(source, None)
        if contact_uuid:
            contact = Contact.objects.filter(uuid=contact_uuid, org=self.org, is_active=True).first()
            if not contact:
                raise ValidationError(_("Unable to find contact with uuid: %s") % contact_uuid)

            attrs['contact'] = contact
        return attrs

    def save(self):
        pass

    def restore_object(self, attrs, instance=None):
        if instance:  # pragma: no cover
            raise ValidationError("Invalid operation")

        flow = attrs['flow']
        contact = attrs.get('contact')
        started = attrs['started']
        steps = attrs.get('steps', [])
        completed = attrs.get('completed', False)

        # look for previous run with this contact and flow
        run = FlowRun.objects.filter(org=self.org, contact=contact,
                                     flow=flow, created_on=started).order_by('-modified_on').first()

        if not run:
            run = FlowRun.create(flow, contact, created_on=started)
            flow.update_start_counts([contact])

        step_objs = []
        previous_rule = None
        for step in steps:
            step_obj = FlowStep.from_json(step, flow, run, previous_rule)
            previous_rule = step_obj.rule_uuid
            step_objs.append(step_obj)

        if completed:
            final_step = step_objs[len(step_objs) - 1] if step_objs else None
            completed_on = steps[len(steps) - 1]['arrived_on'] if steps else None

            run.set_completed(True, final_step, completed_on=completed_on)
        else:
            run.modified_on = timezone.now()
            run.save(update_fields=('modified_on',))

        return run


class BoundarySerializer(serializers.ModelSerializer):
    boundary = serializers.SerializerMethodField('get_boundary')
    parent = serializers.SerializerMethodField('get_parent')
    geometry = serializers.SerializerMethodField('get_geometry')
    in_country = serializers.SerializerMethodField('get_country')

    def get_parent(self, obj):
        return obj.parent.osm_id if obj.parent else None

    def get_geometry(self, obj):
        return json.loads(obj.simplified_geometry.geojson)

    def get_boundary(self, obj):
        return obj.osm_id

    def get_country(self, obj):
        return obj.in_country if obj.in_country else None

    class Meta:
        model = AdminBoundary
        fields = ('boundary', 'name', 'level', 'parent', 'geometry', 'in_country')


class AliasSerializer(BoundarySerializer):

    aliases = serializers.SerializerMethodField('get_aliases')

    def get_aliases(self, obj):
        return [alias.name for alias in obj.aliases.all()]

    class Meta:
        model = AdminBoundary
        fields = ('boundary', 'name', 'level', 'parent', 'aliases')


class FlowRunReadSerializer(serializers.ModelSerializer):
    run = serializers.Field(source='id')
    flow_uuid = serializers.SerializerMethodField('get_flow_uuid')
    values = serializers.SerializerMethodField('get_values')
    steps = serializers.SerializerMethodField('get_steps')
    contact = serializers.SerializerMethodField('get_contact_uuid')
    completed = serializers.SerializerMethodField('is_completed')
    expires_on = serializers.Field(source='expires_on')
    expired_on = serializers.Field(source='expired_on')
    flow = serializers.SerializerMethodField('get_flow')  # deprecated, use flow_uuid

    def get_flow(self, obj):
        return obj.flow_id

    def get_flow_uuid(self, obj):
        return obj.flow.uuid

    def get_contact_uuid(self, obj):
        return obj.contact.uuid

    def is_completed(self, obj):
        return obj.is_completed()

    def get_values(self, obj):
        results = obj.flow.get_results(obj.contact, run=obj)
        if results:
            return results[0]['values']
        else:
            return []

    def get_steps(self, obj):
        steps = []
        for step in obj.steps.all():
            steps.append(dict(type=step.step_type,
                              node=step.step_uuid,
                              arrived_on=step.arrived_on,
                              left_on=step.left_on,
                              text=step.get_text(),
                              value=unicode(step.rule_value)))

        return steps

    class Meta:
        model = FlowRun
        fields = ('flow_uuid', 'flow', 'run', 'contact', 'completed', 'values',
                  'steps', 'created_on', 'modified_on', 'expires_on', 'expired_on')


class BroadcastReadSerializer(serializers.ModelSerializer):
    id = serializers.Field(source='id')
    urns = serializers.SerializerMethodField('get_urns')
    contacts = serializers.SerializerMethodField('get_contacts')
    groups = serializers.SerializerMethodField('get_groups')
    text = serializers.Field(source='text')
    created_on = serializers.Field(source='created_on')
    status = serializers.Field(source='status')

    def get_urns(self, obj):
        return [urn.urn for urn in obj.urns.all()]

    def get_contacts(self, obj):
        return [contact.uuid for contact in obj.contacts.all()]

    def get_groups(self, obj):
        return [group.uuid for group in obj.groups.all()]

    class Meta:
        model = Broadcast
        fields = ('id', 'urns', 'contacts', 'groups', 'text', 'created_on', 'status')


class BroadcastCreateSerializer(WriteSerializer):
    urns = StringArrayField(required=False)
    contacts = StringArrayField(required=False)
    groups = StringArrayField(required=False)
    text = serializers.CharField(required=True, max_length=480)
    channel = ChannelField(required=False)

    def validate(self, attrs):
        if not (attrs.get('urns', []) or attrs.get('contacts', None) or attrs.get('groups', [])):
            raise ValidationError("Must provide either urns, contacts or groups")
        return attrs

    def validate_urns(self, attrs, source):
        # if we have tel URNs, we may need a country to normalize by
        tel_sender = self.org.get_send_channel(TEL_SCHEME)
        country = tel_sender.country if tel_sender else None

        urns = []
        for urn in attrs.get(source, []):
            try:
                parsed = ContactURN.parse_urn(urn)
            except ValueError, e:
                raise ValidationError(e.message)

            norm_scheme, norm_path = ContactURN.normalize_urn(parsed.scheme, parsed.path, country)
            if not ContactURN.validate_urn(norm_scheme, norm_path):
                raise ValidationError("Invalid URN: '%s'" % urn)
            urns.append((norm_scheme, norm_path))

        attrs[source] = urns
        return attrs

    def validate_contacts(self, attrs, source):
        contacts = []
        for uuid in attrs.get(source, []):
            contact = Contact.objects.filter(uuid=uuid, org=self.org, is_active=True).first()
            if not contact:
                raise ValidationError(_("Unable to find contact with uuid: %s") % uuid)
            contacts.append(contact)

        attrs[source] = contacts
        return attrs

    def validate_groups(self, attrs, source):
        groups = []
        for uuid in attrs.get(source, []):
            group = ContactGroup.user_groups.filter(uuid=uuid, org=self.org, is_active=True).first()
            if not group:
                raise ValidationError(_("Unable to find contact group with uuid: %s") % uuid)
            groups.append(group)

        attrs[source] = groups
        return attrs

    def validate_channel(self, attrs, source):
        channel = attrs.get(source, None)

        if channel:
            # do they have permission to use this channel?
            if not (channel.is_active and channel.org == self.org):
                raise ValidationError("Invalid pk '%d' - object does not exist." % channel.id)
        return attrs

    def restore_object(self, attrs, instance=None):
        """
        Create a new broadcast to send out
        """
        from temba.msgs.tasks import send_broadcast_task

        if instance:  # pragma: no cover
            raise ValidationError("Invalid operation")

        recipients = attrs.get('contacts') + attrs.get('groups')

        for urn in attrs.get('urns'):
            # create contacts for URNs if necessary
            contact = Contact.get_or_create(self.org, self.user, urns=[urn])
            contact_urn = contact.urn_objects[urn]
            recipients.append(contact_urn)

        # create the broadcast
        broadcast = Broadcast.create(self.org, self.user, attrs['text'],
                                     recipients=recipients, channel=attrs['channel'])

        # send in task
        send_broadcast_task.delay(broadcast.id)
        return broadcast


class MsgCreateSerializer(WriteSerializer):
    channel = ChannelField(required=False)
    text = serializers.CharField(required=True, max_length=480)
    urn = StringArrayField(required=False)
    contact = StringArrayField(required=False)
    phone = PhoneArrayField(required=False)

    def validate(self, attrs):
        urns = attrs.get('urn', [])
        phone = attrs.get('phone', None)
        contact = attrs.get('contact', [])
        if (not urns and not phone and not contact) or (urns and phone):
            raise ValidationError("Must provide either urns or phone or contact and not both")
        return attrs

    def validate_channel(self, attrs, source):
        channel = attrs[source]
        if not channel:
            channels = Channel.objects.filter(is_active=True, org=self.org).order_by('-last_seen')

            if not channels:
                raise ValidationError("There are no channels for this organization.")
            else:
                channel = channels[0]
                attrs[source] = channel

        # do they have permission to use this channel?
        if self.org != channel.org:
            raise ValidationError("Invalid pk '%d' - object does not exist." % channel.id)

        return attrs

    def validate_contact(self, attrs, source):
        contacts = []

        for uuid in attrs.get(source, []):
            contact = Contact.objects.filter(uuid=uuid, org=self.org, is_active=True).first()
            if not contact:
                raise ValidationError(_("Unable to find contact with uuid: %s") % uuid)

            contacts.append(contact)

        attrs['contact'] = contacts
        return attrs

    def validate_urn(self, attrs, source):
        urns = []

        if 'channel' in attrs and attrs['channel']:
            country = attrs['channel'].country

            for urn in attrs.get(source, []):
                parsed = ContactURN.parse_urn(urn)
                norm_scheme, norm_path = ContactURN.normalize_urn(parsed.scheme, parsed.path, country)
                if not ContactURN.validate_urn(norm_scheme, norm_path):
                    raise ValidationError("Invalid URN: '%s'" % urn)
                urns.append((norm_scheme, norm_path))
        else:
            raise ValidationError("You must specify a valid channel")

        attrs['urn'] = urns
        return attrs

    def validate_phone(self, attrs, source):
        if self.org.is_anon:
            raise ValidationError("Cannot create messages for anonymous organizations")

        if 'channel' in attrs and attrs['channel']:
            # check our numbers for validity
            country = attrs['channel'].country
            for tel, phone in attrs.get(source, []):
                try:
                    normalized = phonenumbers.parse(phone, country.code)
                    if not phonenumbers.is_possible_number(normalized):
                        raise ValidationError("Invalid phone number: '%s'" % phone)
                except:
                    raise ValidationError("Invalid phone number: '%s'" % phone)
        else:
            raise ValidationError("You must specify a valid channel")

        return attrs

    def restore_object(self, attrs, instance=None):
        """
        Create a new broadcast to send out
        """
        if instance: # pragma: no cover
            raise ValidationError("Invalid operation")

        if 'urn' in attrs and attrs['urn']:
            urns = attrs.get('urn', [])
        else:
            urns = attrs.get('phone', [])

        channel = attrs['channel']
        contacts = list()
        for urn in urns:
            # treat each urn as a separate contact
            contacts.append(Contact.get_or_create(channel.org, self.user, urns=[urn]))

        # add any contacts specified by uuids
        uuid_contacts = attrs.get('contact', [])
        for contact in uuid_contacts:
            contacts.append(contact)

        # create the broadcast
        broadcast = Broadcast.create(self.org, self.user, attrs['text'], recipients=contacts)

        # send it
        broadcast.send()
        return broadcast


class MsgCreateResultSerializer(serializers.ModelSerializer):
    messages = serializers.SerializerMethodField('get_messages')
    sms = serializers.SerializerMethodField('get_messages')  # deprecated

    def get_messages(self, obj):
        return [msg.id for msg in obj.get_messages()]

    class Meta:
        model = Broadcast
        fields = ('messages', 'sms')


class CallSerializer(serializers.ModelSerializer):
    call = serializers.SerializerMethodField('get_call')
    contact = serializers.SerializerMethodField('get_contact_uuid')
    created_on = serializers.Field(source='time')
    phone = serializers.SerializerMethodField('get_phone')
    relayer = serializers.SerializerMethodField('get_relayer')
    relayer_phone = serializers.SerializerMethodField('get_relayer_phone')

    def get_relayer_phone(self, obj):
        if obj.channel and obj.channel.address:
            return obj.channel.address
        else:
            return None

    def get_relayer(self, obj):
        if obj.channel:
            return obj.channel.pk
        else:
            return None

    def get_contact_uuid(self, obj):
        return obj.contact.uuid

    def get_phone(self, obj):
        return obj.contact.get_urn_display(org=obj.org, scheme=TEL_SCHEME, full=True)

    def get_call(self, obj):
        return obj.pk

    class Meta:
        model = Call
        fields = ('call', 'contact', 'relayer', 'relayer_phone', 'phone', 'created_on', 'duration', 'call_type')


class ChannelReadSerializer(serializers.ModelSerializer):
    relayer = serializers.SerializerMethodField('get_relayer')
    phone = serializers.SerializerMethodField('get_phone')
    power_level = serializers.Field(source='get_last_power')
    power_status = serializers.Field(source='get_last_power_status')
    power_source = serializers.Field(source='get_last_power_source')
    network_type = serializers.Field(source='get_last_network_type')
    pending_message_count = serializers.SerializerMethodField('get_unsent_count')

    def get_phone(self, obj):
        return obj.address

    def get_relayer(self, obj):
        return obj.pk

    def get_unsent_count(self, obj):
        return obj.get_unsent_messages().count()

    class Meta:
        model = Channel
        fields = ('relayer', 'phone', 'name', 'country', 'last_seen', 'power_level', 'power_status', 'power_source',
                  'network_type', 'pending_message_count')
        read_only_fields = ('last_seen',)


class ChannelClaimSerializer(WriteSerializer):
    claim_code = serializers.CharField(required=True, max_length=16)
    phone = serializers.CharField(required=True, max_length=16, source='number')
    name = serializers.CharField(required=False, max_length=64)

    def validate_claim_code(self, attrs, source):
        claim_code = attrs[source].strip()

        if not claim_code:
            raise ValidationError("Invalid claim code: '%s'" % claim_code)

        channel = Channel.objects.filter(claim_code=claim_code, is_active=True)
        if not channel:
            raise ValidationError("Invalid claim code: '%s'" % claim_code)

        attrs['channel'] = channel[0]
        return attrs

    def validate_phone(self, attrs, source):
        phone = attrs[source].strip()
        channel = attrs.get('channel', None)

        if not channel:
            return attrs

        try:
            normalized = phonenumbers.parse(phone, attrs['channel'].country.code)
            if not phonenumbers.is_possible_number(normalized):
                raise ValidationError("Invalid phone number: '%s'" % phone)
        except:  # pragma: no cover
            raise ValidationError("Invalid phone number: '%s'" % phone)

        phone = phonenumbers.format_number(normalized, phonenumbers.PhoneNumberFormat.E164)
        attrs['phone'] = phone

        return attrs

    def restore_object(self, attrs, instance=None):
        """
        Claim our channel
        """
        if instance: # pragma: no cover
            raise ValidationError("Invalid operation")

        channel = attrs['channel']
        if attrs.get('name', None):
            channel.name = attrs['name']

        channel.claim(self.org, attrs['phone'], self.user)

        if not settings.TESTING:  # pragma: no cover
            channel.trigger_sync()

        return attrs['channel']
