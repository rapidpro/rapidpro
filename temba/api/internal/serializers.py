from datetime import timezone as tzone

from rest_framework import serializers

from temba.locations.models import AdminBoundary
from temba.templates.models import Template, TemplateTranslation


class ModelAsJsonSerializer(serializers.BaseSerializer):
    def to_representation(self, instance):
        return instance.as_json()


class LocationReadSerializer(serializers.ModelSerializer):
    class Meta:
        model = AdminBoundary
        fields = ("osm_id", "name", "path")


class TemplateReadSerializer(serializers.ModelSerializer):
    STATUSES = {
        TemplateTranslation.STATUS_PENDING: "pending",
        TemplateTranslation.STATUS_APPROVED: "approved",
        TemplateTranslation.STATUS_REJECTED: "rejected",
        TemplateTranslation.STATUS_PAUSED: "paused",
        TemplateTranslation.STATUS_DISABLED: "disabled",
        TemplateTranslation.STATUS_IN_APPEAL: "in_appeal",
    }

    base_translation = serializers.SerializerMethodField()
    modified_on = serializers.DateTimeField(default_timezone=tzone.utc)
    created_on = serializers.DateTimeField(default_timezone=tzone.utc)

    def get_base_translation(self, obj):
        return self._translation(obj.base_translation) if obj.base_translation else None

    def _translation(self, trans):
        return {
            "channel": {"uuid": str(trans.channel.uuid), "name": trans.channel.name},
            "namespace": trans.namespace,
            "locale": trans.locale,
            "status": self.STATUSES[trans.status],
            "components": trans.components,
            "variables": trans.variables,
            "supported": trans.is_supported,
            "compatible": trans.is_compatible,
        }

    class Meta:
        model = Template
        fields = ("uuid", "name", "base_translation", "created_on", "modified_on")
