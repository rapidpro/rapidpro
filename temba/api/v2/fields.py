from __future__ import unicode_literals

import six

from rest_framework import serializers

from temba.campaigns.models import Campaign
from temba.channels.models import Channel
from temba.contacts.models import Contact, ContactGroup, URN
from temba.flows.models import Flow

# maximum number of items in a posted list
MAX_LIST_SIZE = 100


class LimitedWriteListField(serializers.ListField):
    """
    A list field which can be only be written to with a limited number of items
    """
    def to_internal_value(self, data):
        if hasattr(data, '__len__') and len(data) >= MAX_LIST_SIZE:
            raise serializers.ValidationError("Exceeds maximum list size of %d" % MAX_LIST_SIZE)

        return super(LimitedWriteListField, self).to_internal_value(data)


class URNField(serializers.CharField):
    max_length = 255

    def to_representation(self, obj):
        if self.context['org'].is_anon:
            return None
        else:
            return six.text_type(obj)

    def to_internal_value(self, data):
        try:
            country_code = self.context['org'].get_country_code()
            normalized = URN.normalize(data, country_code=country_code)
            if not URN.validate(normalized):
                raise ValueError()
        except ValueError:
            raise serializers.ValidationError("Invalid URN: %s" % data)

        return normalized


class URNListField(LimitedWriteListField):
    child = URNField()


class TembaModelField(serializers.UUIDField):
    model = None
    model_manager = 'objects'

    def to_representation(self, obj):
        return {'uuid': obj.uuid, 'name': obj.name}

    def to_internal_value(self, data):
        uuid = super(TembaModelField, self).to_internal_value(data)

        manager = getattr(self.model, self.model_manager)
        obj = manager.filter(org=self.context['org'], uuid=uuid, is_active=True).first()

        if not obj:
            raise serializers.ValidationError("No such object with UUID: %s" % uuid)

        return obj


class CampaignField(TembaModelField):
    model = Campaign


class ChannelField(TembaModelField):
    model = Channel


class ContactField(TembaModelField):
    model = Contact


class ContactGroupField(TembaModelField):
    model = ContactGroup
    model_manager = 'user_groups'


class FlowField(TembaModelField):
    model = Flow
