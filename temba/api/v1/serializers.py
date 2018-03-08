# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import json
import phonenumbers
import six
import regex

from django.contrib.auth.models import User
from django.utils.translation import ugettext_lazy as _
from rest_framework import serializers
from temba.channels.models import Channel
from temba.contacts.models import Contact, ContactField, ContactGroup, ContactURN, URN, TEL_SCHEME
from temba.flows.models import Flow, FlowRun, FlowStep, RuleSet, FlowRevision
from temba.locations.models import AdminBoundary
from temba.msgs.models import Broadcast, Msg
from temba.orgs.models import get_current_export_version
from temba.utils.dates import datetime_to_json_date
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
    if len(fetched) != len(uuids):  # pragma: no cover
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
        if isinstance(data, six.string_types):
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
            for key, val in six.iteritems(data):
                if not isinstance(key, six.string_types) or not isinstance(val, six.string_types):
                    raise serializers.ValidationError("Both keys and values must be strings")

        return super(StringDictField, self).to_internal_value(data)


class PhoneArrayField(serializers.ListField):
    """
    List of phone numbers or a single phone number
    """
    def to_internal_value(self, data):
        if isinstance(data, six.string_types):
            return [URN.from_tel(data)]

        elif isinstance(data, list):
            if len(data) > 100:
                raise serializers.ValidationError("You can only specify up to 100 numbers at a time.")

            urns = []
            for phone in data:
                if not isinstance(phone, six.string_types):  # pragma: no cover
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

        return [six.text_type(urn) for urn in obj.get_urns()]

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
    name = serializers.CharField(required=False, allow_blank=True, max_length=64)
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
        self.new_fields = []

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
                    scheme, path, display = URN.to_parts(normalized)
                    # for backwards compatibility we don't validate phone numbers here
                    if scheme != TEL_SCHEME and not URN.validate(normalized):  # pragma: needs cover
                        raise ValueError()
                except ValueError:
                    raise serializers.ValidationError("Invalid URN: '%s'" % urn)

                self.parsed_urns.append(normalized)

        return value

    def validate_fields(self, value):
        if value:
            org_fields = self.context['contact_fields']

            for field_key, field_val in value.items():
                if field_key in Contact.RESERVED_FIELD_KEYS:
                    raise serializers.ValidationError("Invalid contact field key: '%s' is a reserved word" % field_key)
                for field in org_fields:
                    # TODO get users to stop writing fields via labels
                    if field.key == field_key or field.label == field_key:
                        break
                else:
                    self.new_fields.append(field_key)

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
                normalized_urn = URN.identity(URN.normalize(parsed_urn, country))
                urn = ContactURN.objects.filter(org=self.org, identity__exact=normalized_urn).first()
                if urn and urn.contact:
                    urn_contacts.add(urn.contact)

            if len(urn_contacts) > 1:
                raise serializers.ValidationError(_("URNs are used by multiple contacts"))

            contact_by_urns = urn_contacts.pop() if len(urn_contacts) > 0 else None

            if self.instance and contact_by_urns and contact_by_urns != self.instance:  # pragma: no cover
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

        # treat empty names as None
        if not name:
            name = None

        changed = []

        if self.instance:
            if self.parsed_urns is not None:
                self.instance.update_urns(self.user, self.parsed_urns)

            # update our name and language
            if name != self.instance.name:
                self.instance.name = name
                changed.append('name')
        else:
            self.instance = Contact.get_or_create_by_urns(self.org, self.user, name, urns=self.parsed_urns,
                                                          language=language, force_urn_update=True)

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
                elif self.new_fields and key in self.new_fields:
                    new_field = ContactField.get_or_create(org=self.org, user=self.user,
                                                           key=regex.sub('[^A-Za-z0-9]+', '_', key).lower(),
                                                           label=key)
                    self.instance.set_field(self.user, new_field.key, value)

                # TODO as above, need to get users to stop updating via label
                existing_by_label = ContactField.get_by_label(self.org, key)
                if existing_by_label:
                    self.instance.set_field(self.user, existing_by_label.key, value)

        # update our contact's groups
        if self.group_objs is not None:
            self.instance.update_static_groups(self.user, self.group_objs)

        return self.instance


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

        fields_count = ContactField.objects.filter(org=self.org).count()
        if not self.instance and fields_count >= ContactField.MAX_ORG_CONTACTFIELDS:
            raise serializers.ValidationError('This org has %s contact fields and the limit is %s. '
                                              'You must delete existing ones before '
                                              'you can create new ones.' % (fields_count,
                                                                            ContactField.MAX_ORG_CONTACTFIELDS))

        data['key'] = key
        return data

    def save(self):
        key = self.validated_data.get('key')
        label = self.validated_data.get('label')
        value_type = self.validated_data.get('value_type')

        return ContactField.get_or_create(self.org, self.user, key, label, value_type=value_type)


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
        return obj.get_run_stats()['total']

    def get_labels(self, obj):
        return [l.name for l in obj.labels.all()]

    def get_completed_runs(self, obj):
        return obj.get_run_stats()['completed']

    def get_participants(self, obj):
        return None

    def get_rulesets(self, obj):
        rulesets = list()

        obj.ensure_current_version()

        for ruleset in obj.rule_sets.all().order_by('y'):  # pragma: needs cover

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


class FlowRunReadSerializer(ReadSerializer):
    run = serializers.ReadOnlyField(source='id')
    flow_uuid = serializers.SerializerMethodField()
    contact = serializers.SerializerMethodField('get_contact_uuid')
    completed = serializers.SerializerMethodField('is_completed')
    created_on = DateTimeField()
    modified_on = DateTimeField()

    def get_flow_uuid(self, obj):
        return obj.flow.uuid

    def get_contact_uuid(self, obj):
        return obj.contact.uuid

    def is_completed(self, obj):
        return obj.is_completed()

    class Meta:
        model = FlowRun
        fields = ('flow_uuid', 'run', 'contact', 'completed', 'created_on', 'modified_on',)


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
            user = User.objects.filter(username__iexact=value).first()
            if user and self.org in user.get_user_orgs(self.org.brand):
                self.submitted_by_obj = user
            else:  # pragma: needs cover
                raise serializers.ValidationError("Invalid submitter id, user doesn't exist")

    def validate_flow(self, value):
        if value:
            self.flow_obj = Flow.objects.filter(org=self.org, uuid=value).first()
            if not self.flow_obj:  # pragma: needs cover
                raise serializers.ValidationError(_("Unable to find contact with uuid: %s") % value)

            if self.flow_obj.is_archived:  # pragma: needs cover
                raise serializers.ValidationError("You cannot start an archived flow.")
        return value

    def validate_contact(self, value):
        if value:
            self.contact_obj = Contact.objects.filter(uuid=value, org=self.org, is_active=True).first()
            if not self.contact_obj:  # pragma: needs cover
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

        if not revision:  # pragma: needs cover
            raise serializers.ValidationError("Missing 'revision' field")

        flow_revision = self.flow_obj.revisions.filter(revision=revision).first()

        if not flow_revision:
            raise serializers.ValidationError("Invalid revision: %s" % revision)

        definition = flow_revision.definition

        # make sure we are operating off a current spec
        definition = FlowRevision.migrate_definition(definition, self.flow_obj, get_current_export_version())

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
        run = (
            FlowRun.objects
            .filter(org=self.org, contact=self.contact_obj, flow=self.flow_obj, created_on=started, is_active=True)
            .order_by('-modified_on')
            .first()
        )

        if not run or run.submitted_by != self.submitted_by_obj:
            run = FlowRun.create(self.flow_obj, self.contact_obj, created_on=started, submitted_by=self.submitted_by_obj)

        step_objs = [FlowStep.from_json(step, self.flow_obj, run) for step in steps]

        if completed:
            final_step = step_objs[len(step_objs) - 1] if step_objs else None
            completed_on = steps[len(steps) - 1]['arrived_on'] if steps else None

            run.set_completed(final_step, completed_on=completed_on)
        else:
            run.save(update_fields=('modified_on',))

        return run


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
        return []  # pragma: needs cover

    def validate_urn(self, value):
        urns = []
        if value:
            # if we have tel URNs, we may need a country to normalize by
            country = self.org.get_country_code()

            for urn in value:
                try:
                    normalized = URN.normalize(urn, country)
                except ValueError as e:  # pragma: needs cover
                    raise serializers.ValidationError(six.text_type(e))

                if not URN.validate(normalized, country):  # pragma: needs cover
                    raise serializers.ValidationError("Invalid URN: '%s'" % urn)
                urns.append(normalized)

        return urns

    def validate(self, data):
        urns = data.get('urn', [])
        phones = data.get('phone', [])
        contacts = data.get('contact', [])
        channel = data.get('channel')

        if (not urns and not phones and not contacts) or (urns and phones):  # pragma: needs cover
            raise serializers.ValidationError("Must provide either urns or phone or contact and not both")

        if not channel:
            channel = Channel.objects.filter(is_active=True, org=self.org).order_by('-last_seen').first()
            if not channel:  # pragma: no cover
                raise serializers.ValidationError("There are no channels for this organization.")
            data['channel'] = channel

        if phones:
            if self.org.is_anon:  # pragma: needs cover
                raise serializers.ValidationError("Cannot create messages for anonymous organizations")

            # check our numbers for validity
            country = channel.country
            for urn in phones:
                try:
                    tel, phone, display = URN.to_parts(urn)
                    normalized = phonenumbers.parse(phone, country.code)
                    if not phonenumbers.is_possible_number(normalized):  # pragma: needs cover
                        raise serializers.ValidationError("Invalid phone number: '%s'" % phone)
                except Exception:
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
            contact, urn_obj = Contact.get_or_create(channel.org, urn, user=self.user)
            contacts.append(contact)

        # add any contacts specified by uuids
        uuid_contacts = self.validated_data.get('contact', [])
        for contact in uuid_contacts:
            contacts.append(contact)

        # create the broadcast
        broadcast = Broadcast.create(self.org, self.user, self.validated_data['text'],
                                     recipients=contacts, channel=channel)

        # send it
        broadcast.send(expressions_context={})
        return broadcast
