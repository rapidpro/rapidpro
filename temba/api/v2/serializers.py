from __future__ import absolute_import, unicode_literals

from rest_framework import serializers
from temba.flows.models import FlowRun


class ReadSerializer(serializers.ModelSerializer):
    """
    We deviate slightly from regular REST framework usage with distinct serializers for reading and writing
    """
    pass


class FlowRunReadSerializer(ReadSerializer):
    flow = serializers.SerializerMethodField()
    values = serializers.SerializerMethodField()
    steps = serializers.SerializerMethodField()
    contact = serializers.SerializerMethodField()
    completed = serializers.SerializerMethodField()

    def get_flow(self, obj):
        return obj.flow.uuid

    def get_contact(self, obj):
        return obj.contact.uuid

    def get_completed(self, obj):
        return obj.is_completed()

    def get_values(self, obj):
        results = obj.flow.get_results(obj.contact, run=obj)
        if results:
            return results[0]['values']
        else:
            return []

    class Meta:
        model = FlowRun
        fields = ('id', 'flow', 'contact', 'completed', 'values', 'steps',
                  'created_on', 'modified_on', 'expires_on', 'expired_on')
