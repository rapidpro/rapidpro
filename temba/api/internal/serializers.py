from datetime import timezone as tzone

from rest_framework import serializers

from temba.templates.models import Template


class ModelAsJsonSerializer(serializers.BaseSerializer):
    def to_representation(self, instance):
        return instance.as_json()


class TemplateReadSerializer(serializers.ModelSerializer):
    translations = serializers.SerializerMethodField()
    modified_on = serializers.DateTimeField(default_timezone=tzone.utc)
    created_on = serializers.DateTimeField(default_timezone=tzone.utc)

    def get_translations(self, obj):
        translations = []
        for trans in obj.translations.all():
            translations.append(
                {
                    "channel": {"uuid": str(trans.channel.uuid), "name": trans.channel.name},
                    "namespace": trans.namespace,
                    "locale": trans.locale,
                    "status": trans.get_status_display(),
                    "components": trans.components,
                }
            )

        return translations

    class Meta:
        model = Template
        fields = ("uuid", "name", "translations", "created_on", "modified_on")
