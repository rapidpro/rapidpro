from __future__ import unicode_literals

import json
import phonenumbers

from django.core.exceptions import ValidationError
from django.conf import settings
from django.db.models import Q
from django.utils.translation import ugettext_lazy as _
from rest_framework import serializers
from temba.campaigns.models import Campaign, CampaignEvent, FLOW_EVENT, MESSAGE_EVENT
from temba.channels.models import Channel
from temba.contacts.models import Contact, ContactField, ContactGroup, ContactURN, TEL_SCHEME
from temba.flows.models import Flow, FlowRun, RuleSet
from temba.locations.models import AdminBoundary
from temba.msgs.models import Msg, Call, Broadcast, INITIALIZING
from temba.values.models import Value, VALUE_TYPE_CHOICES


class DictionaryField(serializers.WritableField):

    def to_native(self, obj):  # pragma: no cover
        raise ValidationError("Reading of extra field not supported")

    def from_native(self, data):
        if isinstance(data, dict):
            for key in data.keys():
                value = data[key]

                if not isinstance(value, basestring):
                    raise ValidationError("Invalid, keys and values must both be strings: %s" % unicode(value))
            return data
        else:
            raise ValidationError("Invalid, must be dictionary: %s" % data)


class StringArrayField(serializers.WritableField):

    def to_native(self, obj):  # pragma: no cover
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

class WriteSerializer(serializers.Serializer):

    def restore_fields(self, data, files):

        if not isinstance(data, dict):
            self._errors['non_field_errors'] = ['Request body should be a single JSON object']
            return {}

        return super(WriteSerializer, self).restore_fields(data, files)

class MsgReadSerializer(serializers.ModelSerializer):
    id = serializers.SerializerMethodField('get_id')
    contact = serializers.SerializerMethodField('get_contact_uuid')
    urn = serializers.SerializerMethodField('get_urn')
    status = serializers.SerializerMethodField('get_status')
    relayer = serializers.SerializerMethodField('get_relayer')
    relayer_phone = serializers.SerializerMethodField('get_relayer_phone')
    phone = serializers.SerializerMethodField('get_phone')  # deprecated
    type = serializers.SerializerMethodField('get_type')
    labels = serializers.SerializerMethodField('get_labels')

    def get_id(self, obj):
        return obj.pk

    def get_type(self, obj):
        return obj.msg_type

    def get_urn(self, obj):
        if obj.org.is_anon:
            return None
        return obj.contact_urn.urn

    def get_contact_uuid(self, obj):
        return obj.contact.uuid

    def get_phone(self, obj):
        return obj.contact.get_urn_display(org=obj.org, scheme=TEL_SCHEME, full=True)

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

    def get_status(self, obj):
        if obj.status in ['Q', 'P']:
            return 'Q'
        return obj.status

    def get_labels(self, obj):
        return [l.name for l in obj.labels.all()]

    class Meta:
        model = Msg
        fields = ('id', 'contact', 'urn', 'status', 'type', 'labels', 'relayer', 'relayer_phone', 'phone', 'direction', 'text', 'created_on', 'sent_on', 'delivered_on')
        read_only_fields = ('direction', 'created_on', 'sent_on', 'delivered_on')


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
    name = serializers.Field(source='name')
    uuid = serializers.Field(source='uuid')
    language = serializers.Field(source='language')
    group_uuids = serializers.SerializerMethodField('get_group_uuids')
    urns = serializers.SerializerMethodField('get_urns')
    fields = serializers.SerializerMethodField('get_contact_fields')
    phone = serializers.SerializerMethodField('get_tel')  # deprecated, use urns
    groups = serializers.SerializerMethodField('get_groups')  # deprecated, use group_uuids

    def get_groups(self, obj):
        return [_.name for _ in obj.groups.all()]

    def get_group_uuids(self, obj):
        return [_.uuid for _ in obj.groups.all()]

    def get_urns(self, obj):
        if obj.org.is_anon:
            return dict()

        return [urn.urn for urn in obj.urns.all()]

    def get_contact_fields(self, obj):
        fields = dict()
        for contact_field in ContactField.objects.filter(org=obj.org, is_active=True):
            fields[contact_field.key] = obj.get_field_display(contact_field.key)
        return fields

    def get_tel(self, obj):
        return obj.get_urn_display(obj.org, scheme=TEL_SCHEME, full=True)

    class Meta:
        model = Contact
        fields = ('uuid', 'name', 'language', 'group_uuids', 'urns', 'fields', 'modified_on', 'phone', 'groups')


class ContactWriteSerializer(WriteSerializer):
    uuid = serializers.CharField(required=False, max_length=36)
    name = serializers.CharField(required=False, max_length=64)
    language = serializers.CharField(required=False, max_length=4)
    urns = StringArrayField(required=False)
    group_uuids = StringArrayField(required=False)
    fields = DictionaryField(required=False)
    phone = serializers.CharField(required=False, max_length=16)  # deprecated, use urns
    groups = StringArrayField(required=False)  # deprecated, use group_uuids

    def __init__(self, *args, **kwargs):
        if 'user' in kwargs:
            self.user = kwargs['user']
            self.org = kwargs.get('org', self.user.get_org())
            del kwargs['user']
            if 'org' in kwargs:
                del kwargs['org']

        super(ContactWriteSerializer, self).__init__(*args, **kwargs)

    def validate(self, attrs):
        urns = attrs.get('urns', [])
        phone = attrs.get('phone', None)
        uuid = attrs.get('uuid', None)

        if (not urns and not phone and not uuid) or (urns and phone):
            raise ValidationError("Must provide either urns, phone or uuid but only one of each")

        if attrs.get('group_uuids', []) and attrs.get('groups', []):
            raise ValidationError("Parameter groups is deprecated and can't be used together with group_uuids")

        if uuid:
            if phone:
                urns = [(TEL_SCHEME, attrs['phone'])]

            if urns:
                urns_strings = ["%s:%s" % u for u in urns]
                urn_query = Q(pk__lt=0)
                for urn_string in urns_strings:
                    urn_query |= Q(urns__urn__iexact=urn_string)

                other_contacts = Contact.objects.filter(org=self.org)
                other_contacts = other_contacts.filter(urn_query).distinct()
                other_contacts = other_contacts.exclude(uuid=uuid)
                if other_contacts:
                    if phone:
                        raise ValidationError(_("phone %s is used by another contact") % phone)
                    raise ValidationError(_("URNs %s are used by other contacts") % urns_strings)

        return attrs

    def validate_language(self, attrs, source):
        if 'language' in attrs:
            language = attrs.get(source, None)
            supported_languages = [l.iso_code for l in self.user.get_org().languages.all()]

            if language:
                # no languages configured
                if not supported_languages:
                    raise ValidationError(_("You do not have any languages configured for your organization."))

                # is it one of the languages on this org?
                if not language.lower() in supported_languages:
                    raise ValidationError(_("Language code '%s' is not one of supported for organization. (%s)") %
                                          (language, ",".join(supported_languages)))

                attrs['language'] = language.lower()
            else:
                attrs['language'] = None

        return attrs

    def validate_uuid(self, attrs, source):
        uuid = attrs.get(source, '')
        if uuid:
            contact = Contact.objects.filter(org=self.user.get_org(), uuid=uuid, is_active=True).first()
            if not contact:
                raise ValidationError("Unable to find contact with UUID: %s" % uuid)

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
            attrs['phone'] = phone
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

        attrs['urns'] = urns
        return attrs

    def validate_fields(self, attrs, source):
        fields = attrs.get(source, {}).items()
        if fields:
            org_fields = list(ContactField.objects.filter(org=self.user.get_org(), is_active=True))

            for key, value in attrs.get(source, {}).items():
                for field in org_fields:
                    # TODO get users to stop writing fields via labels
                    if field.key == key or field.label == key:
                        break
                else:
                    raise ValidationError("Invalid contact field key: '%s'" % key)

        return attrs

    def validate_group_uuids(self, attrs, source):
        group_uuids = attrs.get(source, None)
        if group_uuids is not None:
            groups = []
            for uuid in group_uuids:
                group = ContactGroup.objects.filter(uuid=uuid, org=self.org, is_active=True).first()
                if not group:
                    raise ValidationError(_("Unable to find contact group with uuid: %s") % uuid)

                groups.append(group)

            attrs['group_uuids'] = groups
        return attrs

    def restore_object(self, attrs, instance=None):
        """
        Update our contact
        """
        if instance:  # pragma: no cover
            raise ValidationError("Invalid operation")

        org = self.user.get_org()
        if org.is_anon:
            raise ValidationError("Cannot update contacts on anonymous organizations")

        uuid = attrs.get('uuid', None)
        if uuid:
            contact = Contact.objects.get(uuid=uuid, org=org, is_active=True)

        urns = attrs.get('urns', None)
        phone = attrs.get('phone', None)

        # user didn't specify either urns or phone, stick to what already exists
        if urns is None and phone is None:
            urns = [(u.scheme, u.path) for u in contact.urns.all()]

        # user only specified phone, build our urns from it
        if phone:
            urns = [(TEL_SCHEME, attrs['phone'])]

        if uuid:
            contact.update_urns(urns)
        else:
            contact = Contact.get_or_create(org, self.user, urns=urns, uuid=uuid)

        changed = []

        # update our name and language
        if attrs.get('name', None):
            contact.name = attrs['name']
            changed.append('name')

        if 'language' in attrs:
            contact.language = attrs['language']
            changed.append('language')

        # save our contact if it changed
        if changed:
            contact.save(update_fields=changed)

        # update our fields
        fields = attrs.get('fields', None)
        if not fields is None:
            for key, value in fields.items():
                existing_by_key = ContactField.objects.filter(org=self.user.get_org(), key__iexact=key, is_active=True).first()
                if existing_by_key:
                    contact.set_field(existing_by_key.key, value)
                    continue

                # TODO as above, need to get users to stop updating via label
                existing_by_label = ContactField.objects.filter(org=self.user.get_org(), label__iexact=key, is_active=True).first()
                if existing_by_label:
                    contact.set_field(existing_by_label.key, value)

        # update our groups by UUID or name (deprecated)
        group_uuids = attrs.get('group_uuids', None)
        group_names = attrs.get('groups', None)

        if not group_uuids is None:
            contact.update_groups(group_uuids)

        elif not group_names is None:
            # by name creates groups if necessary
            groups = [ContactGroup.get_or_create(self.user.get_org(), self.user, name) for name in group_names]
            contact.update_groups(groups)

        return contact


class ContactFieldReadSerializer(serializers.ModelSerializer):
    key = serializers.Field(source='key')
    label = serializers.Field(source='label')
    value_type = serializers.Field(source='value_type')

    class Meta:
        model = ContactField
        fields = ('key', 'label', 'value_type')


class ContactFieldWriteSerializer(serializers.Serializer):
    key = serializers.CharField(required=False)
    label = serializers.CharField(required=True)
    value_type = serializers.CharField(required=True)

    def __init__(self, *args, **kwargs):
        if 'user' in kwargs:
            self.user = kwargs['user']
            del kwargs['user']

        super(ContactFieldWriteSerializer, self).__init__(*args, **kwargs)

    def validate_key(self, attrs, source):
        key = attrs.get(source, '')
        if key:
            # if key is specified, then we're updating a field, so key must exist
            if not ContactField.objects.filter(org=self.user.get_org(), key=key).exists():
                raise ValidationError("No such contact field key")
        return attrs

    def validate_value_type(self, attrs, source):
        value_type = attrs.get(source, '')
        if value_type:
            if not value_type in [t for t, label in VALUE_TYPE_CHOICES]:
                raise ValidationError("Invalid field value type")
        return attrs

    def restore_object(self, attrs, instance=None):
        """
        Update our contact field
        """
        if instance:  # pragma: no cover
            raise ValidationError("Invalid operation")

        org = self.user.get_org()
        key = attrs.get('key', None)
        label = attrs.get('label')
        value_type = attrs.get('value_type')

        if not key:
            key = ContactField.api_make_key(label)

        return ContactField.get_or_create(org, key, label, value_type=value_type)


class PhoneField(serializers.WritableField):

    def to_native(self, obj):  # pragma: no cover
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


class CampaignEventSerializer(serializers.ModelSerializer):
    event = serializers.SerializerMethodField('get_event')
    campaign = serializers.SerializerMethodField('get_campaign')
    flow = serializers.SerializerMethodField('get_flow')
    relative_to = serializers.SerializerMethodField('get_relative_to')

    def get_campaign(self, obj):
        return obj.campaign.pk

    def get_event(self, obj):
        return obj.pk

    def get_flow(self, obj):
        if obj.event_type == FLOW_EVENT:
            return obj.flow.pk
        else:
            return None

    def get_relative_to(self, obj):
        return obj.relative_to.label

    class Meta:
        model = CampaignEvent
        fields = ('event', 'campaign', 'relative_to', 'offset', 'unit', 'delivery_hour', 'message', 'flow', 'created_on')
        read_only_fields = ('offset', 'unit', 'message', 'delivery_hour', 'created_on')


class CampaignEventWriteSerializer(serializers.Serializer):
    campaign = serializers.IntegerField(required=False)
    event = serializers.IntegerField(required=False)
    offset = serializers.IntegerField(required=True)
    unit = serializers.CharField(required=True, max_length=1)
    delivery_hour = serializers.IntegerField(required=True)
    relative_to = serializers.CharField(required=True, min_length=3, max_length=64)
    message = serializers.CharField(required=False, max_length=320)
    flow = serializers.IntegerField(required=False)

    def __init__(self, *args, **kwargs):
        if 'user' in kwargs:
            self.user = kwargs['user']
            del kwargs['user']

        super(CampaignEventWriteSerializer, self).__init__(*args, **kwargs)

    def validate_campaign(self, attrs, source):
        if not source in attrs:
            return attrs

        # try to look up the campaign
        campaign_id = attrs[source]

        if not Campaign.objects.filter(pk=campaign_id, is_active=True, is_archived=False, org=self.user.get_org()):
            raise ValidationError("No campaign with id %d" % campaign_id)

        if 'event' in attrs:
            raise ValidationError("Cannot specify a campaign id if an event id is included")

        return attrs

    def validate_event(self, attrs, source):
        if not source in attrs:
            return attrs

        # try to look up the campaign
        event_id = attrs[source]

        if not CampaignEvent.objects.filter(pk=event_id, is_active=True, campaign__org=self.user.get_org()):
            raise ValidationError("No event with id %d" % event_id)

        if 'campaign' in attrs:
            raise ValidationError("Cannot specify an event id if a campaign id is included")

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
        if not source in attrs:
            return attrs

        # try to look up the flow
        event_id = attrs[source]

        if not Flow.objects.filter(pk=event_id, is_active=True, org=self.user.get_org()):
            raise ValidationError("No flow with id %d" % event_id)

        if 'message' in attrs:
            raise ValidationError("Events cannot have both a message and a flow")

        return attrs

    def restore_object(self, attrs, instance=None):
        """
        Create or update our campaign
        """
        if instance: # pragma: no cover
            raise ValidationError("Invalid operation")

        org = self.user.get_org()

        # parse our arguments
        message = attrs.get('message', None)
        flow = attrs.get('flow', None)

        if not message and not flow:
            raise ValidationError("Must specify either a flow or a message for the event")

        if message and flow:
            raise ValidationError("You cannot set both a flow and a message on an event, it must be only one")

        campaign_id = attrs.get('campaign', None)
        event_id = attrs.get('event', None)

        if not campaign_id and not event_id:
            raise ValidationError("You must specify either a campaign to create a new event, or an event to update")

        offset = attrs.get('offset')
        unit = attrs.get('unit')
        delivery_hour = attrs.get('delivery_hour')
        relative_to = attrs.get('relative_to')

        # load our contact field
        existing_field = ContactField.objects.filter(label=relative_to, org=org, is_active=True)

        if not existing_field:
            key = ContactField.api_make_key(relative_to)
            relative_to_field = ContactField.get_or_create(org, key, relative_to)
        else:
            relative_to_field = existing_field[0]

        if 'event' in attrs:
            event = CampaignEvent.objects.get(pk=attrs['event'], is_active=True, campaign__org=org)

            # we are being set to a flow
            if 'flow' in attrs:
                flow = Flow.objects.get(pk=attrs['flow'], is_active=True, org=org)
                event.flow = flow
                event.event_type = FLOW_EVENT
                event.message = None

            # we are being set to a message
            else:
                event.message = attrs['message']

                # if we aren't currently a message event, we need to create our hidden message flow
                if event.event_type != MESSAGE_EVENT:
                    event.flow = Flow.create_single_message(org, self.user, event.message)
                    event.event_type = MESSAGE_EVENT

                # otherwise, we can just update that flow
                else:
                    # set our single message on our flow
                    event.flow.update_single_message_flow(message=attrs['message'])

            # update our other attributes
            event.offset = offset
            event.unit = unit
            event.delivery_hour = delivery_hour
            event.relative_to = relative_to_field
            event.save()
            event.update_flow_name()

        else:
            campaign = Campaign.objects.get(pk=attrs['campaign'], is_active=True, org=org)
            event_type = MESSAGE_EVENT

            if 'flow' in attrs:
                flow = Flow.objects.get(pk=attrs['flow'], is_active=True, org=org)
                event_type = FLOW_EVENT
            else:
                flow = Flow.create_single_message(org, self.user, message)

            event = CampaignEvent.objects.create(campaign=campaign, relative_to=relative_to_field, offset=offset,
                                                 unit=unit, event_type=event_type, flow=flow, message=message,
                                                 created_by=self.user, modified_by=self.user)
            event.update_flow_name()

        return event

class CampaignSerializer(serializers.ModelSerializer):
    campaign = serializers.SerializerMethodField('get_campaign')
    group = serializers.SerializerMethodField('get_group')

    def get_campaign(self, obj):
        return obj.pk

    def get_group(self, obj):
        return obj.group.name

    class Meta:
        model = Campaign
        fields = ('campaign', 'name', 'group', 'created_on')
        read_only_fields = ('name', 'created_on')


class CampaignWriteSerializer(serializers.Serializer):
    campaign = serializers.IntegerField(required=False)
    name = serializers.CharField(required=True, max_length=64)
    group = serializers.CharField(required=True, max_length=64)

    def __init__(self, *args, **kwargs):
        if 'user' in kwargs:
            self.user = kwargs['user']
            del kwargs['user']

        super(CampaignWriteSerializer, self).__init__(*args, **kwargs)

    def validate_campaign(self, attrs, source):
        if not source in attrs:
            return attrs

        # try to look up the campaign
        campaign_id = attrs[source]

        if not Campaign.objects.filter(pk=campaign_id, is_active=True, is_archived=False, org=self.user.get_org()):
            raise ValidationError("No campaign with id %d" % campaign_id)

        return attrs

    def restore_object(self, attrs, instance=None):
        """
        Create or update our campaign
        """
        if instance: # pragma: no cover
            raise ValidationError("Invalid operation")

        org = self.user.get_org()

        if 'campaign' in attrs:
            campaign = Campaign.objects.get(pk=attrs['campaign'], is_active=True, is_archived=False, org=org)
            campaign.name = attrs['name']
            campaign.group = ContactGroup.get_or_create(org, self.user, attrs['group'])
            campaign.save()

        else:
            group = ContactGroup.get_or_create(org, self.user, attrs['group'])
            campaign = Campaign.objects.create(name=attrs['name'], group=group, org=org,
                                               created_by=self.user, modified_by=self.user)

        return campaign


class FlowReadSerializer(serializers.ModelSerializer):
    uuid = serializers.Field(source='uuid')
    archived = serializers.Field(source='is_archived')
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
        for ruleset in obj.rule_sets.all().order_by('y'):
            rulesets.append(dict(label=ruleset.label, id=ruleset.id, node=ruleset.uuid))

        return rulesets

    class Meta:
        model = Flow
        fields = ('uuid', 'archived', 'name', 'labels', 'participants', 'runs', 'completed_runs', 'rulesets',
                  'created_on', 'flow')


class FlowField(serializers.PrimaryKeyRelatedField):

    def initialize(self, parent, field_name):
        self.queryset = Flow.objects.filter(is_active=True)
        super(FlowField, self).initialize(parent, field_name)


class ResultSerializer(serializers.Serializer):
    results = serializers.SerializerMethodField('get_results')

    def __init__(self, *args, **kwargs):
        self.ruleset = kwargs.get('ruleset', None)
        self.contact_field = kwargs.get('contact_field', None)
        self.segment = kwargs.get('segment', None)

        if 'ruleset' in kwargs: del kwargs['ruleset']
        if 'contact_field' in kwargs: del kwargs['contact_field']
        if 'segment' in kwargs: del kwargs['segment']

        super(ResultSerializer, self).__init__(*args, **kwargs)

    def get_results(self, obj):
        if self.ruleset:
            return Value.get_value_summary(ruleset=self.ruleset, segment=self.segment)
        else:
            return Value.get_value_summary(contact_field=self.contact_field, segment=self.segment)

    class Meta:
        model = RuleSet
        fields = ('results',)


class FlowRunStartSerializer(serializers.Serializer):
    flow_uuid = serializers.CharField(required=False, max_length=36)
    groups = StringArrayField(required=False)
    contacts = StringArrayField(required=False)
    extra = DictionaryField(required=False)
    restart_participants = serializers.BooleanField(required=False, default=True)
    flow = FlowField(required=False, queryset=Flow.objects.filter(pk=-1))  # deprecated, use flow_uuid
    contact = StringArrayField(required=False)  # deprecated, use contacts
    phone = PhoneField(required=False)  # deprecated

    def __init__(self, *args, **kwargs):
        if 'user' in kwargs:
            self.user = kwargs['user']
            self.org = kwargs.get('org', self.user.get_org())
            del kwargs['user']
            if 'org' in kwargs: del kwargs['org']

        super(FlowRunStartSerializer, self).__init__(*args, **kwargs)

    def validate(self, attrs):
        if not (attrs.get('flow', None) or attrs.get('flow_uuid', None)):
            raise ValidationError("Use flow_uuid to specify which flow to start")
        return attrs

    def validate_flow_uuid(self, attrs, source):
        org = self.org
        flow_uuid = attrs.get(source, None)
        if flow_uuid:
            flow = Flow.objects.get(uuid=flow_uuid)
            if flow.is_archived:
                raise ValidationError("You cannot start an archived flow.")

            # do they have permission to use this flow?
            if org != flow.org:
                raise ValidationError("Invalid UUID '%s' - flow does not exist." % flow.uuid)

            attrs['flow'] = flow
        return attrs

    def validate_flow(self, attrs, source):
        org = self.org
        flow = attrs.get(source, None)
        if flow:
            if flow.is_archived:
                raise ValidationError("You cannot start an archived flow.")

            # do they have permission to use this flow?
            if org != flow.org:
                raise ValidationError("Invalid pk '%d' - flow does not exist." % flow.id)

        return attrs

    def validate_groups(self, attrs, source):
        groups = []
        for uuid in attrs.get(source, []):
            group = ContactGroup.objects.filter(uuid=uuid, org=self.org, is_active=True).first()
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
        org = self.org
        if org.is_anon:
            raise ValidationError("Cannot start flows for anonymous organizations")

        # get a channel
        channel = self.org.get_send_channel(TEL_SCHEME)

        if channel:
            # check our numbers for validity
            for tel, phone in attrs.get(source, []):
                try:
                    normalized = phonenumbers.parse(phone, channel.country.code)
                    if not phonenumbers.is_possible_number(normalized):
                        raise ValidationError("Invalid phone number: '%s'" % phone)
                except:
                    raise ValidationError("Invalid phone number: '%s'" % phone)
        else:
            raise ValidationError("You cannot start flows without at least one channel configured")

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


class BoundarySerializer(serializers.ModelSerializer):
    boundary = serializers.SerializerMethodField('get_boundary')
    parent = serializers.SerializerMethodField('get_parent')
    geometry = serializers.SerializerMethodField('get_geometry')

    def get_parent(self, obj):
        return obj.parent.osm_id if obj.parent else None

    def get_geometry(self, obj):
        return json.loads(obj.simplified_geometry.geojson)

    def get_boundary(self, obj):
        return obj.osm_id

    class Meta:
        model = AdminBoundary
        fields = ('boundary', 'name', 'level', 'parent', 'geometry')
        read_only_fields = ('name',)


class FlowRunReadSerializer(serializers.ModelSerializer):
    run = serializers.Field(source='id')
    flow_uuid = serializers.SerializerMethodField('get_flow_uuid')
    values = serializers.SerializerMethodField('get_values')
    steps = serializers.SerializerMethodField('get_steps')
    contact = serializers.SerializerMethodField('get_contact_uuid')
    completed = serializers.SerializerMethodField('is_completed')
    flow = serializers.SerializerMethodField('get_flow')  # deprecated, use flow_uuid
    phone = serializers.SerializerMethodField('get_phone')  # deprecated, use contact

    def get_flow(self, obj):
        return obj.flow_id

    def get_phone(self, obj):
        return obj.contact.get_urn_display(org=obj.flow.org, scheme=TEL_SCHEME, full=True)

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
        for step in obj.steps.all().order_by('arrived_on'):
            steps.append(dict(type=step.step_type,
                              node=step.step_uuid,
                              arrived_on=step.arrived_on,
                              left_on=step.left_on,
                              text=step.get_text(),
                              value=unicode(step.rule_value)))

        return steps

    class Meta:
        model = FlowRun
        fields = ('flow_uuid', 'flow', 'run', 'contact', 'completed', 'phone', 'values', 'steps', 'created_on')
        read_only_fields = ('created_on',)


class ChannelField(serializers.PrimaryKeyRelatedField):

    def initialize(self, parent, field_name):
        self.queryset = Channel.objects.filter(is_active=True)
        super(ChannelField, self).initialize(parent, field_name)


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


class BroadcastCreateSerializer(serializers.Serializer):
    urns = StringArrayField(required=False)
    contacts = StringArrayField(required=False)
    groups = StringArrayField(required=False)
    text = serializers.CharField(required=True, max_length=480)
    channel = ChannelField(queryset=Channel.objects.filter(pk=-1), required=False)

    def __init__(self, *args, **kwargs):
        if 'user' in kwargs:
            self.user = kwargs.pop('user')
            self.org = self.user.get_org()

        super(BroadcastCreateSerializer, self).__init__(*args, **kwargs)

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
            group = ContactGroup.objects.filter(uuid=uuid, org=self.org, is_active=True).first()
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


class MsgCreateSerializer(serializers.Serializer):
    channel = ChannelField(queryset=Channel.objects.filter(pk=-1), required=False)
    text = serializers.CharField(required=True, max_length=480)
    urn = StringArrayField(required=False)
    contact = StringArrayField(required=False)
    phone = PhoneField(required=False)

    def __init__(self, *args, **kwargs):
        if 'user' in kwargs:
            self.user = kwargs['user']
            self.org = kwargs.get('org', self.user.get_org())
            del kwargs['user']
            if 'org' in kwargs: del kwargs['org']

        super(MsgCreateSerializer, self).__init__(*args, **kwargs)

    def validate(self, attrs):
        urns = attrs.get('urn', [])
        phone = attrs.get('phone', None)
        contact = attrs.get('contact', [])
        if (not urns and not phone and not contact) or (urns and phone):
            raise ValidationError("Must provide either urns or phone or contact and not both")
        return attrs

    def validate_channel(self, attrs, source):
        # load their org
        org = self.org

        channel = attrs[source]
        if not channel:
            channels = Channel.objects.filter(is_active=True, org=org).order_by('-last_seen')

            if not channels:
                raise ValidationError("There are no channels for this organization.")
            else:
                channel = channels[0]
                attrs[source] = channel

        # do they have permission to use this channel?
        if org != channel.org:
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

        user = self.user
        org = self.org

        if 'urn' in attrs and attrs['urn']:
            urns = attrs.get('urn', [])
        else:
            urns = attrs.get('phone', [])

        channel = attrs['channel']
        contacts = list()
        for urn in urns:
            # treat each urn as a separate contact
            contacts.append(Contact.get_or_create(channel.org, user, urns=[urn]))

        # add any contacts specified by uuids
        uuid_contacts = attrs.get('contact', [])
        for contact in uuid_contacts:
            contacts.append(contact)

        # create the broadcast
        broadcast = Broadcast.create(org, user, attrs['text'], recipients=contacts)

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
        read_only_fields = ('contact', 'duration', 'call_type')


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


class ChannelClaimSerializer(serializers.Serializer):
    claim_code = serializers.CharField(required=True, max_length=16)
    phone = serializers.CharField(required=True, max_length=16, source='number')
    name = serializers.CharField(required=False, max_length=64)

    def __init__(self, *args, **kwargs):
        if 'user' in kwargs:
            self.user = kwargs['user']
            del kwargs['user']

        super(ChannelClaimSerializer, self).__init__(*args, **kwargs)

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

        channel.claim(self.user.get_org(), attrs['phone'], self.user)

        if not settings.TESTING:  # pragma: no cover
            channel.trigger_sync()

        return attrs['channel']


