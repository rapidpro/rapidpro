from rest_framework import serializers


class ModelAsJsonSerializer(serializers.BaseSerializer):
    def to_representation(self, instance):
        return instance.as_json()
