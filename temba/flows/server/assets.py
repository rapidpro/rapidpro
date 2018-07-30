from django.conf import settings
from django.db.models import Prefetch

from temba.utils.dates import datetime_to_ms

from .serialize import (
    serialize_channel,
    serialize_field,
    serialize_flow,
    serialize_group,
    serialize_label,
    serialize_location_hierarchy,
    serialize_resthook,
)

ASSET_HOST = "http://localhost:8000" if settings.TESTING else ("https://%s" % settings.HOSTNAME)


class AssetType:
    name = None
    serializer = None

    def is_supported(self, org):
        return True

    def get_set_url(self, org, simulator=False):
        return f"{ASSET_HOST}/{org.id}/{self._get_timestamp(org)}/{self.name}/"

    def get_item_url(self, org, uuid):
        return f"{self.get_set_url(org)}{uuid}/"

    def get_all(self, org):
        pass

    def get_set(self, org):
        return self.get_all(org).filter(is_active=True)

    def get_item(self, org, uuid):
        return self.get_set(org).get(uuid=uuid)

    def serialize_set(self, org, simulator=False):
        return {
            "type": self.name,
            "url": self.get_set_url(org, simulator),
            "content": [type(self).serializer(o) for o in self.get_set(org)],
        }

    def serialize_item(self, org, uuid):
        return {
            "type": self.name,
            "url": self.get_item_url(org, uuid),
            "content": type(self).serializer(self.get_item(org, uuid)),
        }

    def _get_timestamp(self, org):
        last_modified = self.get_all(org).order_by("modified_on").last()
        return datetime_to_ms(last_modified.modified_on) if last_modified else 0


class ChannelType(AssetType):
    name = "channel"
    serializer = serialize_channel

    def get_all(self, org):
        return org.channels.order_by("id")

    def get_set_url(self, org, simulator=False):
        url = super().get_set_url(org)
        if simulator:
            url += "?simulator=1"
        return url

    def serialize_set(self, org, simulator=False):
        from temba.channels.models import Channel

        serialized = super().serialize_set(org, simulator)
        if simulator:
            serialized["content"].append(Channel.SIMULATOR_CHANNEL)

        return serialized


class FieldType(AssetType):
    name = "field"
    serializer = serialize_field

    def get_all(self, org):
        return org.contactfields.order_by("id")


class FlowType(AssetType):
    name = "flow"
    serializer = serialize_flow

    def get_all(self, org):
        return org.flows.order_by("id")


class GroupType(AssetType):
    name = "group"
    serializer = serialize_group

    def get_all(self, org):
        return org.all_groups(manager="user_groups").order_by("id")


class LabelType(AssetType):
    name = "label"
    serializer = serialize_label

    def get_all(self, org):
        return org.label_set(manager="label_objects").order_by("id")


class LocationHierarchyType(AssetType):
    name = "location_hierarchy"
    serializer = serialize_location_hierarchy

    def is_supported(self, org):
        return bool(org.country_id)

    def get_set(self, org):
        return [org]

    def _get_timestamp(self, org):
        return 1


class ResthookType(AssetType):
    name = "resthook"
    serializer = serialize_resthook

    def get_all(self, org):
        from temba.api.models import ResthookSubscriber

        return org.resthooks.prefetch_related(
            Prefetch("subscribers", ResthookSubscriber.objects.filter(is_active=True).order_by("created_on"))
        ).order_by("id")


ASSET_TYPES = [cls() for cls in AssetType.__subclasses__()]
ASSET_TYPES_BY_NAME = {at.name: at for at in ASSET_TYPES}


def get_asset_type(name):
    return ASSET_TYPES_BY_NAME[name]


def get_asset_urls(org, simulator=False):
    return {at.name: at.get_set_url(org, simulator) for at in ASSET_TYPES if at.is_supported(org)}
