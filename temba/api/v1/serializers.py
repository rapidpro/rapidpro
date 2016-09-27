from __future__ import absolute_import, unicode_literals

import json
import phonenumbers

from django.conf import settings
from django.utils import timezone
from django.contrib.auth.models import User
from django.utils.translation import ugettext_lazy as _
from rest_framework import serializers
from temba.campaigns.models import Campaign, CampaignEvent
from temba.channels.models import Channel, ChannelEvent
from temba.contacts.models import Contact, ContactField, ContactGroup, ContactURN, URN, TEL_SCHEME
from temba.flows.models import Flow, FlowRun, FlowStep, RuleSet, FlowRevision
from temba.locations.models import AdminBoundary
from temba.msgs.models import Broadcast, Label, Msg, INCOMING
from temba.orgs.models import CURRENT_EXPORT_VERSION, EARLIEST_IMPORT_VERSION
from temba.utils import datetime_to_json_date
from temba.values.models import Value

# Maximum number of items that can be passed to bulk action endpoint. We don't currently enforce this for messages but
# we may in the future.
MAX_BULK_ACTION_ITEMS = 100


def format_datetime(value):
    """
    Datetime fields are limited to millisecond accuracy for v1
    """
    return datetime_to_json_date(value, micros=False) if value else None


def validate_bulk_fetch(fetched, uuids):
    """
    Validates a bulk fetch of objects against the provided list of UUIDs
    """
    if len(fetched) != len(uuids):
        fetched_uuids = {c.uuid for c in fetched}
        invalid_uuids = [u for u in uuids if u not in fetched_uuids]
        if invalid_uuids:
            raise serializers.ValidationError("Some UUIDs are invalid: %s" % ', '.join(invalid_uuids))


# ------------------------------------------------------------------------------------------
# Field types
# ------------------------------------------------------------------------------------------

class DateTimeField(serializers.DateTimeField):
    """
    For backward compatibility, datetime fields are limited to millisecond accuracy
    """
    def to_representation(self, value):
        return format_datetime(value)


class StringArrayField(serializers.ListField):
    """
    List of strings or a single string
    """
    def __init__(self, **kwargs):
        super(StringArrayField, self).__init__(child=serializers.CharField(allow_blank=False), **kwargs)

    def to_internal_value(self, data):
        # accept single string
        if isinstance(data, basestring):
            data = [data]

        # don't allow dicts. This is a bug in ListField due to be fixed in 3.3.2
        # https://github.com/tomchristie/django-rest-framework/pull/3513
        elif isinstance(data, dict):
            raise serializers.ValidationError("Should be a list")

        return super(StringArrayField, self).to_internal_value(data)


class StringDictField(serializers.DictField):

    def __init__(self, **kwargs):
        super(StringDictField, self).__init__(child=serializers.CharField(), **kwargs)

    def to_internal_value(self, data):
        # enforce values must be strings, see https://github.com/tomchristie/django-rest-framework/pull/3394
        if isinstance(data, dict):
            for key, val in data.iteritems():
                if not isinstance(key, basestring) or not isinstance(val, basestring):
                    raise serializers.ValidationError("Both keys and values must be strings")

        return super(StringDictField, self).to_internal_value(data)


class PhoneArrayField(serializers.ListField):
    """
    List of phone numbers or a single phone number
    """
    def to_internal_value(self, data):
        if isinstance(data, basestring):
            return [URN.from_tel(data)]

        elif isinstance(data, list):
            if len(data) > 100:
                raise serializers.ValidationError("You can only specify up to 100 numbers at a time.")

            urns = []
            for phone in data:
                if not isinstance(phone, basestring):
                    raise serializers.ValidationError("Invalid phone: %s" % str(phone))
                urns.append(URN.from_tel(phone))

            return urns
        else:
            raise serializers.ValidationError("Invalid phone: %s" % data)


class ChannelField(serializers.PrimaryKeyRelatedField):

    def __init__(self, **kwargs):
        super(ChannelField, self).__init__(queryset=Channel.objects.filter(is_active=True), **kwargs)


class UUIDField(serializers.CharField):

    def __init__(self, **kwargs):
        super(UUIDField, self).__init__(max_length=36, **kwargs)


# ------------------------------------------------------------------------------------------
# Serializers
# ------------------------------------------------------------------------------------------

class ReadSerializer(serializers.ModelSerializer):
    """
    We deviate slightly from regular REST framework usage with distinct serializers for reading and writing
    """
    pass


class WriteSerializer(serializers.Serializer):
    """
    The normal REST framework way is to have the view decide if it's an update on existing instance or a create for a
    new instance. Since our logic for that gets relatively complex, we have the serializer make that call.
    """
    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user')
        self.org = kwargs.pop('org') if 'org' in kwargs else self.user.get_org()

        super(WriteSerializer, self).__init__(*args, **kwargs)

        self.instance = None

    def run_validation(self, data=serializers.empty):
        if not isinstance(data, dict):
            raise serializers.ValidationError(detail={'non_field_errors': ["Request body should be a single JSON object"]})

        return super(WriteSerializer, self).run_validation(data)


class MsgReadSerializer(ReadSerializer):
    id = serializers.SerializerMethodField()
    broadcast = serializers.SerializerMethodField()
    contact = serializers.SerializerMethodField('get_contact_uuid')
    urn = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()
    archived = serializers.SerializerMethodField()
    relayer = serializers.SerializerMethodField()
    type = serializers.SerializerMethodField()
    labels = serializers.SerializerMethodField()
    created_on = DateTimeField()
    sent_on = DateTimeField()
    delivered_on = serializers.SerializerMethodField()

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
        return obj.visibility == Msg.VISIBILITY_ARCHIVED

    def get_delivered_on(self, obj):
        return None

    def get_labels(self, obj):
        return [l.name for l in obj.labels.all()]

    class Meta:
        model = Msg
        fields = ('id', 'broadcast', 'contact', 'urn', 'status', 'type', 'labels', 'relayer',
                  'direction', 'archived', 'text', 'created_on', 'sent_on', 'delivered_on')


class MsgBulkActionSerializer(WriteSerializer):
    messages = serializers.ListField(required=True, child=serializers.IntegerField())
    action = serializers.CharField(required=True)
    label = serializers.CharField(required=False)
    label_uuid = serializers.CharField(required=False)

    def __init__(self, *args, **kwargs):
        super(MsgBulkActionSerializer, self).__init__(*args, **kwargs)
        self.label_obj = None

    def validate_action(self, value):
        if value not in ('label', 'unlabel', 'archive', 'unarchive', 'delete'):
            raise serializers.ValidationError("Invalid action name: %s" % value)
        return value

    def validate_label(self, value):
        if value:
            if not Label.is_valid_name(value):
                raise serializers.ValidationError("Label name must not be blank or begin with + or -")
            self.label_obj = Label.get_or_create(self.org, self.user, value, None)
        return value

    def validate_label_uuid(self, value):
        if value:
            self.label_obj = Label.label_objects.filter(org=self.org, uuid=value).first()
            if not self.label_obj:
                raise serializers.ValidationError("No such label with UUID: %s" % value)
        return value

    def validate(self, data):
        label_provided = data.get('label') or data.get('label_uuid')
        if data['action'] in ('label', 'unlabel') and not label_provided:
            raise serializers.ValidationError("For action %s you should also specify label or label_uuid" % data['action'])
        elif data['action'] in ('archive', 'unarchive', 'delete') and label_provided:
            raise serializers.ValidationError("For action %s you should not specify label or label_uuid" % data['action'])
        return data

    def save(self):
        msg_ids = self.validated_data['messages']
        action = self.validated_data['action']

        # fetch messages to be modified
        msgs = Msg.objects.filter(org=self.org, direction=INCOMING, pk__in=msg_ids).exclude(visibility=Msg.VISIBILITY_DELETED)
        msgs = msgs.select_related('contact')

        if action == 'label':
            self.label_obj.toggle_label(msgs, add=True)
        elif action == 'unlabel':
            self.label_obj.toggle_label(msgs, add=False)
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


class LabelReadSerializer(ReadSerializer):
    uuid = serializers.ReadOnlyField()
    name = serializers.ReadOnlyField()
    count = serializers.SerializerMethodField()

    def get_count(self, obj):
        return obj.get_visible_count()

    class Meta:
        model = Label
        fields = ('uuid', 'name', 'count')


class LabelWriteSerializer(WriteSerializer):
    uuid = serializers.CharField(required=False)
    name = serializers.CharField(required=True)

    def validate_uuid(self, value):
        if value:
            self.instance = Label.label_objects.filter(org=self.org, uuid=value).first()
            if not self.instance:
                raise serializers.ValidationError("No such message label with UUID: %s" % value)

        return value

    def validate(self, data):
        uuid = data.get('uuid')
        name = data.get('name')

        if Label.label_objects.filter(org=self.org, name=name).exclude(uuid=uuid).exists():
            raise serializers.ValidationError("Label name must be unique")

        return data

    def save(self):
        name = self.validated_data.get('name')

        if self.instance:
            self.instance.name = name
            self.instance.save()
            return self.label
        else:
            return Label.get_or_create(self.org, self.user, name)


class ContactReadSerializer(ReadSerializer):
    name = serializers.SerializerMethodField()
    uuid = serializers.ReadOnlyField()
    language = serializers.SerializerMethodField()
    group_uuids = serializers.SerializerMethodField()
    urns = serializers.SerializerMethodField()
    fields = serializers.SerializerMethodField('get_contact_fields')
    blocked = serializers.SerializerMethodField()
    failed = serializers.SerializerMethodField()
    phone = serializers.SerializerMethodField('get_tel')  # deprecated, use urns
    groups = serializers.SerializerMethodField()  # deprecated, use group_uuids
    modified_on = DateTimeField()

    def get_name(self, obj):
        return obj.name if obj.is_active else None

    def get_language(self, obj):
        return obj.language if obj.is_active else None

    def get_blocked(self, obj):
        return obj.is_blocked if obj.is_active else None

    def get_failed(self, obj):
        return obj.is_stopped if obj.is_active else None

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
        return obj.get_urn_display(obj.org, scheme=TEL_SCHEME, formatted=False) if obj.is_active else None

    class Meta:
        model = Contact
        fields = ('uuid', 'name', 'language', 'group_uuids', 'urns', 'fields',
                  'blocked', 'failed', 'modified_on', 'phone', 'groups')


class ContactWriteSerializer(WriteSerializer):
    uuid = serializers.CharField(required=False, max_length=36)
    name = serializers.CharField(required=False, max_length=64)
    language = serializers.CharField(required=False, min_length=3, max_length=3, allow_null=True)
    urns = StringArrayField(required=False)
    group_uuids = StringArrayField(required=False)
    fields = StringDictField(required=False)
    phone = serializers.CharField(required=False, max_length=16)  # deprecated, use urns
    groups = StringArrayField(required=False)  # deprecated, use group_uuids

    def __init__(self, *args, **kwargs):
        super(ContactWriteSerializer, self).__init__(*args, **kwargs)
        self.parsed_urns = None
        self.group_objs = None

    def validate_uuid(self, value):
        if value:
            self.instance = Contact.objects.filter(org=self.org, uuid=value, is_active=True).first()
            if not self.instance:
                raise serializers.ValidationError("Unable to find contact with UUID: %s" % value)

        return value

    def validate_phone(self, value):
        if value:
            try:
                normalized = phonenumbers.parse(value, None)
                if not phonenumbers.is_possible_number(normalized):
                    raise serializers.ValidationError("Invalid phone number: '%s'" % value)
            except Exception:
                raise serializers.ValidationError("Invalid phone number: '%s'" % value)

            e164_number = phonenumbers.format_number(normalized, phonenumbers.PhoneNumberFormat.E164)
            self.parsed_urns = [URN.from_tel(e164_number)]
        return value

    def validate_urns(self, value):
        if value is not None:
            self.parsed_urns = []
            for urn in value:
                try:
                    normalized = URN.normalize(urn)
                    scheme, path = URN.to_parts(normalized)
                    # for backwards compatibility we don't validate phone numbers here
                    if scheme != TEL_SCHEME and not URN.validate(normalized):
                        raise ValueError()
                except ValueError:
                    raise serializers.ValidationError("Invalid URN: '%s'" % urn)

                self.parsed_urns.append(normalized)

        return value

    def validate_fields(self, value):
        if value:
            org_fields = self.context['contact_fields']

            for field_key, field_val in value.items():
                if field_key in Contact.RESERVED_FIELDS:
                    raise serializers.ValidationError("Invalid contact field key: '%s' is a reserved word" % field_key)
                for field in org_fields:
                    # TODO get users to stop writing fields via labels
                    if field.key == field_key or field.label == field_key:
                        break
                else:
                    raise serializers.ValidationError("Invalid contact field key: '%s'" % field_key)

        return value

    def validate_groups(self, value):
        if value is not None:
            self.group_objs = []
            for name in value:
                if not ContactGroup.is_valid_name(name):
                    raise serializers.ValidationError(_("Invalid group name: '%s'") % name)
                self.group_objs.append(ContactGroup.get_or_create(self.org, self.user, name))

        return value

    def validate_group_uuids(self, value):
        if value is not None:
            self.group_objs = []
            for uuid in value:
                group = ContactGroup.user_groups.filter(uuid=uuid, org=self.org).first()
                if not group:
                    raise serializers.ValidationError(_("Unable to find contact group with uuid: %s") % uuid)

                self.group_objs.append(group)

        return value

    def validate(self, data):
        if data.get('urns') is not None and data.get('phone') is not None:
            raise serializers.ValidationError("Cannot provide both urns and phone parameters together")

        if data.get('group_uuids') is not None and data.get('groups') is not None:
            raise serializers.ValidationError("Parameter groups is deprecated and can't be used together with group_uuids")

        if self.org.is_anon and self.instance and self.parsed_urns is not None:
            raise serializers.ValidationError("Cannot update contact URNs on anonymous organizations")

        if self.parsed_urns is not None:
            # look up these URNs, keeping track of the contacts that are connected to them
            urn_contacts = set()
            country = self.org.get_country_code()

            for parsed_urn in self.parsed_urns:
                normalized_urn = URN.normalize(parsed_urn, country)
                urn = ContactURN.objects.filter(org=self.org, urn__exact=normalized_urn).first()
                if urn and urn.contact:
                    urn_contacts.add(urn.contact)

            if len(urn_contacts) > 1:
                raise serializers.ValidationError(_("URNs are used by multiple contacts"))

            contact_by_urns = urn_contacts.pop() if len(urn_contacts) > 0 else None

            if self.instance and contact_by_urns and contact_by_urns != self.instance:
                raise serializers.ValidationError(_("URNs are used by other contacts"))
        else:
            contact_by_urns = None

        contact = self.instance or contact_by_urns

        # if contact is blocked, they can't be added to groups
        if contact and contact.is_blocked and self.group_objs:
            raise serializers.ValidationError("Cannot add blocked contact to groups")

        return data

    def save(self):
        """
        Update our contact
        """
        name = self.validated_data.get('name')
        fields = self.validated_data.get('fields')
        language = self.validated_data.get('language')

        changed = []

        if self.instance:
            if self.parsed_urns is not None:
                self.instance.update_urns(self.user, self.parsed_urns)

            # update our name and language
            if name != self.instance.name:
                self.instance.name = name
                changed.append('name')
        else:
            self.instance = Contact.get_or_create(self.org, self.user, name, urns=self.parsed_urns, language=language)

        # Contact.get_or_create doesn't nullify language so do that here
        if 'language' in self.validated_data and language is None:
            self.instance.language = language.lower() if language else None
            self.instance.save()

        # save our contact if it changed
        if changed:
            self.instance.save(update_fields=changed)

        # update our fields
        if fields is not None:
            for key, value in fields.items():
                existing_by_key = ContactField.objects.filter(org=self.org, key__iexact=key, is_active=True).first()
                if existing_by_key:
                    self.instance.set_field(self.user, existing_by_key.key, value)
                    continue

                # TODO as above, need to get users to stop updating via label
                existing_by_label = ContactField.get_by_label(self.org, key)
                if existing_by_label:
                    self.instance.set_field(self.user, existing_by_label.key, value)

        # update our contact's groups
        if self.group_objs is not None:
            self.instance.update_static_groups(self.user, self.group_objs)

        return self.instance


class ContactBulkActionSerializer(WriteSerializer):
    ADD = 'add'
    REMOVE = 'remove'
    BLOCK = 'block'
    UNBLOCK = 'unblock'
    EXPIRE = 'expire'
    ARCHIVE = 'archive'
    DELETE = 'delete'

    ACTIONS = (ADD, REMOVE, BLOCK, UNBLOCK, EXPIRE, ARCHIVE, DELETE)

    contacts = StringArrayField(required=True)
    action = serializers.CharField(required=True)
    group = serializers.CharField(required=False)
    group_uuid = serializers.CharField(required=False)

    def __init__(self, *args, **kwargs):
        super(ContactBulkActionSerializer, self).__init__(*args, **kwargs)
        self.group_obj = None

    def validate_contacts(self, value):
        if len(value) > MAX_BULK_ACTION_ITEMS:
            raise serializers.ValidationError("Maximum of %d contacts allowed" % MAX_BULK_ACTION_ITEMS)

        contacts = list(Contact.objects.filter(org=self.org, is_test=False, is_active=True, uuid__in=value))

        # check for UUIDs that didn't resolve to a valid contact
        validate_bulk_fetch(contacts, value)

        return contacts

    def validate_action(self, value):
        if value not in self.ACTIONS:
            raise serializers.ValidationError("Invalid action name: %s" % value)
        return value

    def validate_group(self, value):
        if value:
            self.group_obj = ContactGroup.get_user_group(self.org, value)
            if not self.group_obj:
                raise serializers.ValidationError("No such group: %s" % value)
            elif self.group_obj.is_dynamic:
                raise serializers.ValidationError("Can't add or remove contacts from a dynamic group")
        return value

    def validate_group_uuid(self, value):
        if value:
            self.group_obj = ContactGroup.user_groups.filter(org=self.org, uuid=value).first()
            if not self.group_obj:
                raise serializers.ValidationError("No such group with UUID: %s" % value)
        return value

    def validate(self, data):
        contacts = data['contacts']
        action = data['action']

        if action in (self.ADD, self.REMOVE) and not self.group_obj:
            raise serializers.ValidationError("For action %s you should also specify group or group_uuid" % action)
        elif action in (self.BLOCK, self.UNBLOCK, self.EXPIRE, self.ARCHIVE, self.DELETE) and self.group_obj:
            raise serializers.ValidationError("For action %s you should not specify group or group_uuid" % action)

        if action == self.ADD:
            # if adding to a group, check for blocked contacts
            blocked_uuids = {c.uuid for c in contacts if c.is_blocked}
            if blocked_uuids:
                raise serializers.ValidationError("Blocked cannot be added to groups: %s" % ', '.join(blocked_uuids))

        return data

    def save(self):
        contacts = self.validated_data['contacts']
        action = self.validated_data['action']

        if action == self.ADD:
            self.group_obj.update_contacts(self.user, contacts, add=True)
        elif action == self.REMOVE:
            self.group_obj.update_contacts(self.user, contacts, add=False)
        elif action == self.EXPIRE:
            FlowRun.expire_all_for_contacts(contacts)
        elif action == self.ARCHIVE:
            Msg.archive_all_for_contacts(contacts)
        else:
            for contact in contacts:
                if action == self.BLOCK:
                    contact.block(self.user)
                elif action == self.UNBLOCK:
                    contact.unblock(self.user)
                elif action == self.DELETE:
                    contact.release(self.user)

    class Meta:
        fields = ('contacts', 'action', 'group', 'group_uuid')


class ContactGroupReadSerializer(ReadSerializer):
    uuid = serializers.ReadOnlyField()
    name = serializers.ReadOnlyField()
    size = serializers.SerializerMethodField()
    group = serializers.ReadOnlyField(source='id')  # deprecated, use uuid

    def get_size(self, obj):
        return obj.get_member_count()

    class Meta:
        model = ContactGroup
        fields = ('group', 'uuid', 'name', 'size')


class ContactFieldReadSerializer(ReadSerializer):
    key = serializers.ReadOnlyField()
    label = serializers.ReadOnlyField()
    value_type = serializers.ReadOnlyField()

    class Meta:
        model = ContactField
        fields = ('key', 'label', 'value_type')


class ContactFieldWriteSerializer(WriteSerializer):
    key = serializers.CharField(required=False)
    label = serializers.CharField(required=True)
    value_type = serializers.CharField(required=True)

    def validate_key(self, value):
        if value and not ContactField.is_valid_key(value):
            raise serializers.ValidationError("Field is invalid or a reserved name")
        return value

    def validate_label(self, value):
        if value and not ContactField.is_valid_label(value):
            raise serializers.ValidationError("Field can only contain letters, numbers and hypens")
        return value

    def validate_value_type(self, value):
        if value and value not in [t for t, label in Value.TYPE_CHOICES]:
            raise serializers.ValidationError("Invalid field value type")
        return value

    def validate(self, data):
        key = data.get('key')
        label = data.get('label')

        if not key:
            key = ContactField.make_key(label)
            if not ContactField.is_valid_key(key):
                raise serializers.ValidationError(_("Generated key for '%s' is invalid or a reserved name") % label)

        data['key'] = key
        return data

    def save(self):
        key = self.validated_data.get('key')
        label = self.validated_data.get('label')
        value_type = self.validated_data.get('value_type')

        return ContactField.get_or_create(self.org, self.user, key, label, value_type=value_type)


class CampaignEventReadSerializer(ReadSerializer):
    campaign_uuid = serializers.SerializerMethodField()
    flow_uuid = serializers.SerializerMethodField()
    relative_to = serializers.SerializerMethodField()
    event = serializers.ReadOnlyField(source='pk')  # deprecated, use uuid
    campaign = serializers.SerializerMethodField()  # deprecated, use campaign_uuid
    flow = serializers.SerializerMethodField()  # deprecated, use flow_uuid
    created_on = DateTimeField()

    def get_campaign_uuid(self, obj):
        return obj.campaign.uuid

    def get_flow_uuid(self, obj):
        return obj.flow.uuid if obj.event_type == CampaignEvent.TYPE_FLOW else None

    def get_campaign(self, obj):
        return obj.campaign_id

    def get_flow(self, obj):
        return obj.flow_id if obj.event_type == CampaignEvent.TYPE_FLOW else None

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

    def __init__(self, *args, **kwargs):
        super(CampaignEventWriteSerializer, self).__init__(*args, **kwargs)
        self.campaign_obj = None
        self.flow_obj = None

    def validate_event(self, value):
        if value:
            self.instance = CampaignEvent.objects.filter(pk=value, is_active=True, campaign__org=self.org).first()
            if not self.instance:
                raise serializers.ValidationError("No event with id %d" % value)
        return value

    def validate_uuid(self, value):
        if value:
            self.instance = CampaignEvent.objects.filter(uuid=value, is_active=True, campaign__org=self.org).first()
            if not self.instance:
                raise serializers.ValidationError("No event with UUID %s" % value)
        return value

    def validate_campaign(self, value):
        if value:
            self.campaign_obj = Campaign.get_campaigns(self.org).filter(pk=value).first()
            if not self.campaign_obj:
                raise serializers.ValidationError("No campaign with id %d" % value)
        return value

    def validate_campaign_uuid(self, value):
        if value:
            self.campaign_obj = Campaign.get_campaigns(self.org).filter(uuid=value).first()
            if not self.campaign_obj:
                raise serializers.ValidationError("No campaign with UUID %s" % value)
        return value

    def validate_unit(self, value):
        if value not in ["M", "H", "D", "W"]:
            raise serializers.ValidationError("Must be one of M, H, D or W for Minute, Hour, Day or Week")
        return value

    def validate_delivery_hour(self, value):
        if value < -1 or value > 23:
            raise serializers.ValidationError("Must be either -1 (for same hour) or 0-23")
        return value

    def validate_flow(self, value):
        if value:
            self.flow_obj = Flow.objects.filter(pk=value, is_active=True, org=self.org).first()
            if not self.flow_obj:
                raise serializers.ValidationError("No flow with id %d" % value)
        return value

    def validate_flow_uuid(self, value):
        if value:
            self.flow_obj = Flow.objects.filter(uuid=value, is_active=True, org=self.org).first()
            if not self.flow_obj:
                raise serializers.ValidationError("No flow with UUID %s" % value)
        return value

    def validate_relative_to(self, value):
        # ensure field either exists or can be created
        relative_to = ContactField.get_by_label(self.org, value)
        if not relative_to:
            key = ContactField.make_key(value)
            if not ContactField.is_valid_key(key):
                raise serializers.ValidationError(_("Cannot create contact field with key '%s'") % key)
        return value

    def validate(self, data):
        if not (data.get('message') or self.flow_obj):
            raise serializers.ValidationError("Must specify either a flow or a message for the event")

        if data.get('message') and self.flow_obj:
            raise serializers.ValidationError("Events cannot have both a message and a flow")

        if self.instance and self.campaign_obj:
            raise serializers.ValidationError("Cannot specify campaign if updating an existing event")

        return data

    def save(self):
        """
        Create or update our campaign
        """
        offset = self.validated_data.get('offset')
        unit = self.validated_data.get('unit')
        delivery_hour = self.validated_data.get('delivery_hour')
        relative_to_label = self.validated_data.get('relative_to')
        message = self.validated_data.get('message')

        # ensure contact field exists
        relative_to = ContactField.get_by_label(self.org, relative_to_label)
        if not relative_to:
            key = ContactField.make_key(relative_to_label)
            relative_to = ContactField.get_or_create(self.org, self.user, key, relative_to_label)

        if self.instance:
            # we are being set to a flow
            if self.flow_obj:
                self.instance.flow = self.flow_obj
                self.instance.event_type = CampaignEvent.TYPE_FLOW
                self.instance.message = None

            # we are being set to a message
            else:
                self.instance.message = message

                # if we aren't currently a message event, we need to create our hidden message flow
                if self.instance.event_type != CampaignEvent.TYPE_MESSAGE:
                    self.instance.flow = Flow.create_single_message(self.org, self.user, self.instance.message)
                    self.instance.event_type = CampaignEvent.TYPE_MESSAGE

                # otherwise, we can just update that flow
                else:
                    # set our single message on our flow
                    self.instance.flow.update_single_message_flow(message=message)

            # update our other attributes
            self.instance.offset = offset
            self.instance.unit = unit
            self.instance.delivery_hour = delivery_hour
            self.instance.relative_to = relative_to
            self.instance.save()
            self.instance.update_flow_name()

        else:
            if self.flow_obj:
                self.instance = CampaignEvent.create_flow_event(self.org, self.user, self.campaign_obj, relative_to,
                                                                offset, unit, self.flow_obj, delivery_hour)
            else:
                self.instance = CampaignEvent.create_message_event(self.org, self.user, self.campaign_obj, relative_to,
                                                                   offset, unit, message, delivery_hour)
            self.instance.update_flow_name()

        return self.instance


class CampaignReadSerializer(ReadSerializer):
    group_uuid = serializers.SerializerMethodField()
    group = serializers.SerializerMethodField()  # deprecated, use group_uuid
    campaign = serializers.ReadOnlyField(source='pk')  # deprecated, use uuid
    created_on = DateTimeField()

    def get_group_uuid(self, obj):
        return obj.group.uuid

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

    def __init__(self, *args, **kwargs):
        super(CampaignWriteSerializer, self).__init__(*args, **kwargs)
        self.group_obj = None

    def validate_uuid(self, value):
        if value:
            self.instance = Campaign.get_campaigns(self.org).filter(uuid=value).first()
            if not self.instance:
                raise serializers.ValidationError("No campaign with UUID %s" % value)
        return value

    def validate_campaign(self, value):
        if value:
            self.instance = Campaign.get_campaigns(self.org).filter(pk=value).first()
            if not self.instance:
                raise serializers.ValidationError("No campaign with id %d" % value)
        return value

    def validate_group_uuid(self, value):
        if value:
            self.group_obj = ContactGroup.user_groups.filter(org=self.org, uuid=value).first()
            if not self.group_obj:
                raise serializers.ValidationError("No contact group with UUID %s" % value)
        return value

    def validate_group(self, value):
        if value:
            if ContactGroup.is_valid_name(value):
                self.group_obj = ContactGroup.get_or_create(self.org, self.user, value)
            else:
                raise serializers.ValidationError("Invalid group name: %s" % value)
        return value

    def validate(self, data):
        if not data.get('group') and not data.get('group_uuid'):
            raise serializers.ValidationError("Must specify either group name or group_uuid")

        if data.get('campaign') and data.get('uuid'):
            raise serializers.ValidationError("Can't specify both campaign and uuid")

        return data

    def save(self):
        """
        Create or update our campaign
        """
        name = self.validated_data.get('name')

        if self.instance:
            self.instance.name = name
            self.instance.group = self.group_obj
            self.instance.save()
        else:
            self.instance = Campaign.create(self.org, self.user, name, self.group_obj)

        return self.instance


class FlowReadSerializer(ReadSerializer):
    uuid = serializers.ReadOnlyField()
    archived = serializers.ReadOnlyField(source='is_archived')
    expires = serializers.ReadOnlyField(source='expires_after_minutes')
    labels = serializers.SerializerMethodField()
    rulesets = serializers.SerializerMethodField()
    runs = serializers.SerializerMethodField()
    completed_runs = serializers.SerializerMethodField()
    participants = serializers.SerializerMethodField()
    created_on = DateTimeField()
    flow = serializers.ReadOnlyField(source='id')  # deprecated, use uuid

    def get_runs(self, obj):
        return obj.get_total_runs()

    def get_labels(self, obj):
        return [l.name for l in obj.labels.all()]

    def get_completed_runs(self, obj):
        return obj.get_completed_runs()

    def get_participants(self, obj):
        return None

    def get_rulesets(self, obj):
        rulesets = list()

        obj.ensure_current_version()

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
                                 response_type=response_type,  # deprecated
                                 id=ruleset.id))  # deprecated
        return rulesets

    class Meta:
        model = Flow
        fields = ('uuid', 'archived', 'expires', 'name', 'labels', 'runs', 'completed_runs', 'participants', 'rulesets',
                  'created_on', 'flow')


class FlowWriteSerializer(WriteSerializer):
    version = serializers.IntegerField(required=True)
    metadata = serializers.DictField(required=False)
    base_language = serializers.CharField(required=False, min_length=3, max_length=3)
    flow_type = serializers.CharField(required=False, max_length=1)
    action_sets = serializers.ListField(required=False)
    rule_sets = serializers.ListField(required=False)
    entry = UUIDField(required=False)

    # old versions had different top level elements
    uuid = UUIDField(required=False)
    definition = serializers.DictField(required=False)
    name = serializers.CharField(required=False)
    id = serializers.IntegerField(required=False)

    def validate_version(self, value):
        if value > CURRENT_EXPORT_VERSION or value < EARLIEST_IMPORT_VERSION:
            raise serializers.ValidationError("Flow version %s not supported" % value)
        return value

    def validate_flow_type(self, value):
        if value and value not in [choice[0] for choice in Flow.FLOW_TYPES]:
            raise serializers.ValidationError("Invalid flow type: %s" % value)
        return value

    def validate(self, data):
        version = data.get('version')

        if version < 7:
            if not data.get('name'):
                raise serializers.ValidationError("This field is required for version %s" % version)
            if not data.get('definition'):
                data['definition'] = dict(action_sets=[], rule_sets=[])

        if version >= 7:
            # only required starting at version 7
            metadata = data.get('metadata')
            if metadata:
                if 'name' not in metadata:
                    raise serializers.ValidationError("Name is missing from metadata")

                uuid = metadata.get('uuid', None)
                if uuid and not Flow.objects.filter(org=self.org, is_active=True, uuid=uuid).exists():
                    raise serializers.ValidationError("No such flow with UUID: %s" % uuid)
            else:
                raise serializers.ValidationError("Metadata field is required for version %s" % version)

        return data

    def save(self):
        """
        Update our flow
        """
        flow_json = self.validated_data

        if 'metadata' not in flow_json:
            flow_json['metadata'] = dict(name=flow_json.get('name', None), uuid=flow_json.get('uuid', None))

        # previous to version 7, uuid could be supplied on the outer element
        uuid = flow_json.get('metadata').get('uuid', flow_json.get('uuid', None))
        name = flow_json.get('metadata').get('name')

        if uuid:
            flow = Flow.objects.filter(org=self.org, uuid=uuid).first()
            flow.name = name
            flow_type = flow_json.get('flow_type', None)
            if flow_type:
                flow.flow_type = flow_type

            flow.save()
        else:
            flow_type = flow_json.get('flow_type', Flow.FLOW)
            flow = Flow.create(self.org, self.user, name, flow_type)

        # first, migrate our definition forward if necessary
        version = flow_json.get('version', CURRENT_EXPORT_VERSION)
        if version < CURRENT_EXPORT_VERSION:
            flow_json = FlowRevision.migrate_definition(flow_json, flow, version, CURRENT_EXPORT_VERSION)

        flow.update(flow_json, self.user, force=True)
        return flow


class FlowRunReadSerializer(ReadSerializer):
    run = serializers.ReadOnlyField(source='id')
    flow_uuid = serializers.SerializerMethodField()
    values = serializers.SerializerMethodField()
    steps = serializers.SerializerMethodField()
    contact = serializers.SerializerMethodField('get_contact_uuid')
    completed = serializers.SerializerMethodField('is_completed')
    created_on = DateTimeField()
    modified_on = DateTimeField()
    expires_on = DateTimeField()
    expired_on = serializers.SerializerMethodField()
    flow = serializers.SerializerMethodField()  # deprecated, use flow_uuid

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

    def get_expired_on(self, obj):
        return format_datetime(obj.exited_on) if obj.exit_type == FlowRun.EXIT_TYPE_EXPIRED else None

    class Meta:
        model = FlowRun
        fields = ('flow_uuid', 'flow', 'run', 'contact', 'completed', 'values',
                  'steps', 'created_on', 'modified_on', 'expires_on', 'expired_on')


class FlowRunWriteSerializer(WriteSerializer):
    flow = UUIDField(required=True)
    contact = UUIDField(required=True)
    started = serializers.DateTimeField(required=True)
    completed = serializers.BooleanField(required=False)
    steps = serializers.ListField()
    submitted_by = serializers.CharField(required=False)

    revision = serializers.IntegerField(required=False)  # for backwards compatibility
    version = serializers.IntegerField(required=False)  # for backwards compatibility

    def __init__(self, *args, **kwargs):
        super(FlowRunWriteSerializer, self).__init__(*args, **kwargs)
        self.contact_obj = None
        self.flow_obj = None
        self.submitted_by_obj = None

    def validate_submitted_by(self, value):
        if value:
            user = User.objects.filter(username=value).first()
            if user and self.org in user.get_user_orgs():
                self.submitted_by_obj = user
            else:
                raise serializers.ValidationError("Invalid submitter id, user doesn't exist")

    def validate_flow(self, value):
        if value:
            self.flow_obj = Flow.objects.filter(org=self.org, uuid=value).first()
            if not self.flow_obj:
                raise serializers.ValidationError(_("Unable to find contact with uuid: %s") % value)

            if self.flow_obj.is_archived:
                raise serializers.ValidationError("You cannot start an archived flow.")
        return value

    def validate_contact(self, value):
        if value:
            self.contact_obj = Contact.objects.filter(uuid=value, org=self.org, is_active=True).first()
            if not self.contact_obj:
                raise serializers.ValidationError(_("Unable to find contact with uuid: %s") % value)
        return value

    def validate(self, data):
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
                if self.is_ruleset():
                    return FlowStep.TYPE_RULE_SET
                else:
                    return FlowStep.TYPE_ACTION_SET

        steps = data.get('steps')
        revision = data.get('revision', data.get('version'))

        if not revision:
            raise serializers.ValidationError("Missing 'revision' field")

        flow_revision = self.flow_obj.revisions.filter(revision=revision).first()

        if not flow_revision:
            raise serializers.ValidationError("Invalid revision: %s" % revision)

        definition = json.loads(flow_revision.definition)

        # make sure we are operating off a current spec
        definition = FlowRevision.migrate_definition(definition, self.flow_obj, self.flow_obj.version_number, CURRENT_EXPORT_VERSION)

        for step in steps:
            node_obj = None
            key = 'rule_sets' if 'rule' in step else 'action_sets'

            for json_node in definition[key]:
                if json_node['uuid'] == step['node']:
                    node_obj = VersionNode(json_node, 'rule' in step)
                    break

            if not node_obj:
                raise serializers.ValidationError("No such node with UUID %s in flow '%s'" % (step['node'], self.flow_obj.name))
            else:
                rule = step.get('rule', None)
                if rule:
                    media = rule.get('media', None)
                    if media:
                        (media_type, media_path) = media.split(':', 1)
                        if media_type != 'geo':
                            media_type_parts = media_type.split('/')

                            error = None
                            if len(media_type_parts) != 2:
                                error = (media_type, media)

                            if media_type_parts[0] not in Msg.MEDIA_TYPES:
                                error = (media_type_parts[0], media)

                            if error:
                                raise serializers.ValidationError("Invalid media type '%s': %s" % error)

                step['node'] = node_obj

        return data

    def save(self):
        started = self.validated_data['started']
        steps = self.validated_data.get('steps', [])
        completed = self.validated_data.get('completed', False)

        # look for previous run with this contact and flow
        run = FlowRun.objects.filter(org=self.org, contact=self.contact_obj, submitted_by=self.submitted_by_obj,
                                     flow=self.flow_obj, created_on=started).order_by('-modified_on').first()

        if not run:
            run = FlowRun.create(self.flow_obj, self.contact_obj.pk, created_on=started, submitted_by=self.submitted_by_obj)

        step_objs = []
        previous_rule = None
        for step in steps:
            step_obj = FlowStep.from_json(step, self.flow_obj, run, previous_rule)
            previous_rule = step_obj.rule_uuid
            step_objs.append(step_obj)

        if completed:
            final_step = step_objs[len(step_objs) - 1] if step_objs else None
            completed_on = steps[len(steps) - 1]['arrived_on'] if steps else None

            run.set_completed(final_step, completed_on=completed_on)
        else:
            run.modified_on = timezone.now()
            run.save(update_fields=('modified_on',))

        return run


class FlowRunStartSerializer(WriteSerializer):
    flow_uuid = serializers.CharField(required=False, max_length=36)
    groups = StringArrayField(required=False)
    contacts = StringArrayField(required=False)
    extra = StringDictField(required=False)
    restart_participants = serializers.BooleanField(required=False, default=True)
    flow = serializers.IntegerField(required=False)  # deprecated, use flow_uuid
    contact = StringArrayField(required=False)  # deprecated, use contacts
    phone = PhoneArrayField(required=False)  # deprecated

    def __init__(self, *args, **kwargs):
        super(FlowRunStartSerializer, self).__init__(*args, **kwargs)
        self.flow_obj = None
        self.group_objs = []
        self.contact_objs = []

    def validate_flow(self, value):
        if value:
            self.flow_obj = Flow.objects.filter(pk=value, is_active=True, org=self.org).first()
            if not self.flow_obj:
                raise serializers.ValidationError("No flow with id %d" % value)
        return value

    def validate_flow_uuid(self, value):
        if value:
            self.flow_obj = Flow.objects.filter(uuid=value, is_active=True, org=self.org).first()
            if not self.flow_obj:
                raise serializers.ValidationError("No flow with UUID %s" % value)
        return value

    def validate_groups(self, value):
        if value:
            for uuid in value:
                group = ContactGroup.user_groups.filter(uuid=uuid, org=self.org).first()
                if not group:
                    raise serializers.ValidationError(_("Unable to find contact group with uuid: %s") % uuid)
                self.group_objs.append(group)
        return value

    def validate_contacts(self, value):
        if value:
            for uuid in value:
                contact = Contact.objects.filter(uuid=uuid, org=self.org, is_active=True).first()
                if not contact:
                    raise serializers.ValidationError(_("Unable to find contact with uuid: %s") % uuid)
                self.contact_objs.append(contact)
        return value

    def validate_contact(self, value):  # deprecated, use contacts
        if value:
            for uuid in value:
                contact = Contact.objects.filter(uuid=uuid, org=self.org, is_active=True).first()
                if not contact:
                    raise serializers.ValidationError(_("Unable to find contact with uuid: %s") % uuid)
                self.contact_objs.append(contact)
        return value

    def validate_phone(self, value):  # deprecated, use contacts
        if self.org.is_anon:
            raise serializers.ValidationError("Cannot start flows by phone for anonymous organizations")

        if value:
            # check that we have some way of sending messages
            channel = self.org.get_channel_for_role(Channel.ROLE_SEND, TEL_SCHEME)

            # get our country
            country = self.org.get_country_code()

            if channel:
                # check our numbers for validity
                for urn in value:
                    tel, phone = URN.to_parts(urn)
                    try:
                        normalized = phonenumbers.parse(phone, country)
                        if not phonenumbers.is_possible_number(normalized):
                            raise serializers.ValidationError("Invalid phone number: '%s'" % phone)
                    except:
                        raise serializers.ValidationError("Invalid phone number: '%s'" % phone)
            else:
                raise serializers.ValidationError("You cannot start a flow for a phone number without a phone channel")

        return value

    def validate(self, data):
        if not self.flow_obj:
            raise serializers.ValidationError("Use flow_uuid to specify which flow to start")

        if self.flow_obj.is_archived:
            raise serializers.ValidationError("You cannot start an archived flow.")

        return data

    def save(self):
        """
        Actually start our flows for each contact
        """
        extra = self.validated_data.get('extra')
        restart_participants = self.validated_data.get('restart_participants', True)

        # include contacts created/matched via deprecated phone field
        phone_urns = self.validated_data.get('phone', [])
        if phone_urns:
            for urn in phone_urns:
                # treat each URN as separate contact
                self.contact_objs.append(Contact.get_or_create(self.org, self.user, urns=[urn]))

        try:
            # if we only have one contact and it is a test contact, then set simulation to true so our flow starts
            if len(self.contact_objs) == 1 and self.contact_objs[0].is_test:
                Contact.set_simulation(True)

            if self.group_objs or self.contact_objs:
                return self.flow_obj.start(self.group_objs, self.contact_objs,
                                           restart_participants=restart_participants, extra=extra)
            else:
                return []
        finally:
            # reset our simulation state
            Contact.set_simulation(False)


class BoundarySerializer(ReadSerializer):
    boundary = serializers.SerializerMethodField()
    parent = serializers.SerializerMethodField()
    geometry = serializers.SerializerMethodField()

    def get_parent(self, obj):
        return obj.parent.osm_id if obj.parent else None

    def get_geometry(self, obj):
        return json.loads(obj.simplified_geometry.geojson) if obj.simplified_geometry else None

    def get_boundary(self, obj):
        return obj.osm_id

    class Meta:
        model = AdminBoundary
        fields = ('boundary', 'name', 'level', 'parent', 'geometry')


class AliasSerializer(BoundarySerializer):
    aliases = serializers.SerializerMethodField()

    def get_aliases(self, obj):
        return [alias.name for alias in obj.aliases.all()]

    class Meta:
        model = AdminBoundary
        fields = ('boundary', 'name', 'level', 'parent', 'aliases')


class BroadcastReadSerializer(ReadSerializer):
    id = serializers.ReadOnlyField()
    urns = serializers.SerializerMethodField()
    contacts = serializers.SerializerMethodField()
    groups = serializers.SerializerMethodField()
    text = serializers.ReadOnlyField()
    created_on = DateTimeField()
    status = serializers.ReadOnlyField()

    def get_urns(self, obj):
        if obj.org.is_anon:
            return None
        else:
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

    def validate_urns(self, value):
        urns = []
        if value:
            # if we have tel URNs, we may need a country to normalize by
            country = self.org.get_country_code()

            for urn in value:
                try:
                    normalized = URN.normalize(urn, country)
                except ValueError, e:
                    raise serializers.ValidationError(e.message)

                if not URN.validate(normalized, country):
                    raise serializers.ValidationError("Invalid URN: '%s'" % urn)
                urns.append(normalized)

        return urns

    def validate_contacts(self, value):
        if value:
            contacts = list(Contact.objects.filter(uuid__in=value, org=self.org, is_active=True))

            # check for UUIDs that didn't resolve to a valid contact
            validate_bulk_fetch(contacts, value)
            return contacts
        return []

    def validate_groups(self, value):
        if value:
            groups = list(ContactGroup.user_groups.filter(uuid__in=value, org=self.org))

            # check for UUIDs that didn't resolve to a valid group
            validate_bulk_fetch(groups, value)
            return groups
        return []

    def validate_channel(self, value):
        if value:
            # do they have permission to use this channel?
            if value.org != self.org:
                raise serializers.ValidationError("Invalid pk '%d' - object does not exist." % value.id)
        return value

    def validate(self, data):
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
            contact = Contact.get_or_create(self.org, self.user, urns=[urn])
            contact_urn = contact.urn_objects[urn]
            recipients.append(contact_urn)

        # create the broadcast
        broadcast = Broadcast.create(self.org, self.user, self.validated_data['text'],
                                     recipients=recipients, channel=self.validated_data.get('channel'))

        # send in task
        send_broadcast_task.delay(broadcast.id)
        return broadcast


class MsgCreateSerializer(WriteSerializer):
    channel = ChannelField(required=False)
    text = serializers.CharField(required=True, max_length=480)
    urn = StringArrayField(required=False)
    contact = StringArrayField(required=False)
    phone = PhoneArrayField(required=False)

    def validate_channel(self, value):
        if value:
            # do they have permission to use this channel?
            if value.org != self.org:
                raise serializers.ValidationError("Invalid pk '%d' - object does not exist." % value.id)
        return value

    def validate_contact(self, value):
        if value:
            contacts = list(Contact.objects.filter(uuid__in=value, org=self.org, is_active=True))

            # check for UUIDs that didn't resolve to a valid contact
            validate_bulk_fetch(contacts, value)
            return contacts
        return []

    def validate_urn(self, value):
        urns = []
        if value:
            # if we have tel URNs, we may need a country to normalize by
            country = self.org.get_country_code()

            for urn in value:
                try:
                    normalized = URN.normalize(urn, country)
                except ValueError, e:
                    raise serializers.ValidationError(e.message)

                if not URN.validate(normalized, country):
                    raise serializers.ValidationError("Invalid URN: '%s'" % urn)
                urns.append(normalized)

        return urns

    def validate(self, data):
        urns = data.get('urn', [])
        phones = data.get('phone', [])
        contacts = data.get('contact', [])
        channel = data.get('channel')

        if (not urns and not phones and not contacts) or (urns and phones):
            raise serializers.ValidationError("Must provide either urns or phone or contact and not both")

        if not channel:
            channel = Channel.objects.filter(is_active=True, org=self.org).order_by('-last_seen').first()
            if not channel:
                raise serializers.ValidationError("There are no channels for this organization.")
            data['channel'] = channel

        if phones:
            if self.org.is_anon:
                raise serializers.ValidationError("Cannot create messages for anonymous organizations")

            # check our numbers for validity
            country = channel.country
            for urn in phones:
                try:
                    tel, phone = URN.to_parts(urn)
                    normalized = phonenumbers.parse(phone, country.code)
                    if not phonenumbers.is_possible_number(normalized):
                        raise serializers.ValidationError("Invalid phone number: '%s'" % phone)
                except:
                    raise serializers.ValidationError("Invalid phone number: '%s'" % phone)

        return data

    def save(self):
        """
        Create a new broadcast to send out
        """
        if 'urn' in self.validated_data and self.validated_data['urn']:
            urns = self.validated_data.get('urn')
        else:
            urns = self.validated_data.get('phone', [])

        channel = self.validated_data.get('channel')
        contacts = list()
        for urn in urns:
            # treat each urn as a separate contact
            contacts.append(Contact.get_or_create(channel.org, self.user, urns=[urn]))

        # add any contacts specified by uuids
        uuid_contacts = self.validated_data.get('contact', [])
        for contact in uuid_contacts:
            contacts.append(contact)

        # create the broadcast
        broadcast = Broadcast.create(self.org, self.user, self.validated_data['text'], recipients=contacts)

        # send it
        broadcast.send()
        return broadcast


class MsgCreateResultSerializer(ReadSerializer):
    messages = serializers.SerializerMethodField()
    sms = serializers.SerializerMethodField('get_messages')  # deprecated

    def get_messages(self, obj):
        return [msg.id for msg in obj.get_messages()]

    class Meta:
        model = Broadcast
        fields = ('messages', 'sms')


class ChannelEventSerializer(ReadSerializer):
    call = serializers.SerializerMethodField()
    call_type = serializers.SerializerMethodField()
    contact = serializers.SerializerMethodField('get_contact_uuid')
    created_on = DateTimeField(source='time')
    phone = serializers.SerializerMethodField()
    relayer = serializers.SerializerMethodField()
    relayer_phone = serializers.SerializerMethodField()

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
        return obj.contact.get_urn_display(org=obj.org, scheme=TEL_SCHEME, formatted=False)

    def get_call(self, obj):
        return obj.pk

    def get_call_type(self, obj):
        return obj.event_type

    class Meta:
        model = ChannelEvent
        fields = ('call', 'contact', 'relayer', 'relayer_phone', 'phone', 'created_on', 'duration', 'call_type')


class ChannelReadSerializer(ReadSerializer):
    relayer = serializers.SerializerMethodField()
    phone = serializers.SerializerMethodField()
    country = serializers.SerializerMethodField()
    power_level = serializers.ReadOnlyField(source='get_last_power')
    power_status = serializers.ReadOnlyField(source='get_last_power_status')
    power_source = serializers.ReadOnlyField(source='get_last_power_source')
    network_type = serializers.ReadOnlyField(source='get_last_network_type')
    pending_message_count = serializers.SerializerMethodField('get_unsent_count')
    last_seen = DateTimeField()

    def get_phone(self, obj):
        return obj.address

    def get_relayer(self, obj):
        return obj.pk

    def get_unsent_count(self, obj):
        return obj.get_unsent_messages().count()

    def get_country(self, obj):
        return unicode(obj.country) if obj.country else None

    class Meta:
        model = Channel
        fields = ('relayer', 'phone', 'name', 'country', 'last_seen', 'power_level', 'power_status', 'power_source',
                  'network_type', 'pending_message_count')


class ChannelClaimSerializer(WriteSerializer):
    claim_code = serializers.CharField(required=True, allow_blank=False, max_length=16)
    phone = serializers.CharField(required=True, max_length=16)
    name = serializers.CharField(required=False, max_length=64)

    def validate_claim_code(self, value):
        claim_code = value.strip()

        self.instance = Channel.objects.filter(claim_code=claim_code, is_active=True).first()
        if not self.instance:
            raise serializers.ValidationError("Invalid claim code: '%s'" % claim_code)

        return value

    def validate_phone(self, value):
        if value:
            value = value.strip()
        return value

    def validate(self, data):
        phone = data.get('phone')

        try:
            normalized = phonenumbers.parse(phone, self.instance.country.code)
            if not phonenumbers.is_possible_number(normalized):
                raise serializers.ValidationError("Invalid phone number: '%s'" % phone)
        except:  # pragma: no cover
            raise serializers.ValidationError("Invalid phone number: '%s'" % phone)

        data['phone'] = phonenumbers.format_number(normalized, phonenumbers.PhoneNumberFormat.E164)
        return data

    def save(self):
        """
        Claim our channel
        """
        phone = self.validated_data['phone']
        name = self.validated_data.get('name')

        if name:
            self.instance.name = name

        self.instance.claim(self.org, self.user, phone)

        if not settings.TESTING:  # pragma: no cover
            self.instance.trigger_sync()

        return self.instance
