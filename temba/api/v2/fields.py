from __future__ import unicode_literals

import six

from rest_framework import serializers

from temba.campaigns.models import Campaign
from temba.channels.models import Channel
from temba.contacts.models import Contact, ContactGroup, URN
from temba.flows.models import Flow

# maximum number of items in a posted list
MAX_LIST_SIZE = 100


def validate_list_size(value):
    if hasattr(value, '__len__') and len(value) >= MAX_LIST_SIZE:
        raise serializers.ValidationError("Exceeds maximum list size of %d" % MAX_LIST_SIZE)


class LimitedListField(serializers.ListField):
    """
    A list field which can be only be written to with a limited number of items
    """
    def to_internal_value(self, data):
        validate_list_size(data)

        return super(LimitedListField, self).to_internal_value(data)


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


class URNListField(LimitedListField):
    child = URNField()


class TembaModelField(serializers.RelatedField):
    model = None
    model_manager = 'objects'

    class LimitedSizeList(serializers.ManyRelatedField):
        def run_validation(self, data=serializers.empty):
            validate_list_size(data)

            return super(TembaModelField.LimitedSizeList, self).run_validation(data)

    @classmethod
    def many_init(cls, *args, **kwargs):
        """
        Overrided to provide a custom ManyRelated which limits number of items
        """
        list_kwargs = {'child_relation': cls(*args, **kwargs)}
        for key in kwargs.keys():
            if key in serializers.MANY_RELATION_KWARGS:
                list_kwargs[key] = kwargs[key]
        return TembaModelField.LimitedSizeList(**list_kwargs)

    def get_queryset(self):
        # we use our own fetching logic in to_internal_value
        return self.model.none()

    def to_representation(self, obj):
        return {'uuid': obj.uuid, 'name': obj.name}

    def to_internal_value(self, data):
        manager = getattr(self.model, self.model_manager)
        obj = manager.filter(org=self.context['org'], uuid=data, is_active=True).first()

        if not obj:
            raise serializers.ValidationError("No such object with UUID: %s" % data)

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
