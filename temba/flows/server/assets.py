from django.conf import settings
from django.db.models import Prefetch

from temba.utils.cache import get_cacheable
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
ASSET_BASE = f"{ASSET_HOST}/flow/assets"
ASSET_TIMESTAMP_CACHE_TTL = 30


class AssetType:
    """
    Base class of asset types
    """

    name = None
    serializer = None

    def is_supported(self, org):
        """
        Whether this type is supported for the given org (e.g. not all orgs have location support)
        """
        return True

    def get_set_url(self, org, simulator=False):
        """
        Gets the URL of a single item of this type
        """
        return f"{ASSET_BASE}/{org.id}/{self._get_timestamp(org)}/{self.name}/"

    def get_item_url(self, org, uuid):
        """
        Gets the URL of a single item of this type
        """
        return f"{self.get_set_url(org)}{uuid}/"

    def get_all(self, org):
        """
        Gets the query set of all items of this type for the given org
        """

    def get_set(self, org):
        """
        Gets the active set of this type
        """
        return self.get_all(org).filter(is_active=True).order_by("id")

    def get_item(self, org, uuid):
        """
        Gets a single item of this type
        """
        return self.get_set(org).get(uuid=uuid)

    def serialize_set(self, org, simulator=False):
        """
        Serializes the active set of this type
        """
        return [type(self).serializer(o) for o in self.get_set(org)]

    def bundle_set(self, org, simulator=False):
        """
        Serializes and bundles the active set of this type as an asset for inclusion in a flow server request
        """
        return {
            "type": self.name,
            "url": self.get_set_url(org, simulator),
            "content": self.serialize_set(org, simulator),
        }

    def serialize_item(self, org, uuid):
        """
        Serializes a single item of this type
        """
        return type(self).serializer(self.get_item(org, uuid))

    def bundle_item(self, org, uuid):
        """
        Serializes and bundles a single item of this type as an asset for inclusion in a flow server request
        """
        return {"type": self.name, "url": self.get_item_url(org, uuid), "content": self.serialize_item(org, uuid)}

    def _get_timestamp(self, org):
        """
        Gets the cached timestamp to use for this type of asset in the given org
        """

        def recalculate():
            last_modified = self.get_all(org).order_by("modified_on").last()
            timestamp = datetime_to_ms(last_modified.modified_on) if last_modified else 0
            return timestamp, ASSET_TIMESTAMP_CACHE_TTL

        return get_cacheable(f"assets_timestamp:{org.id}:{self.name}", recalculate)


class ChannelType(AssetType):
    name = "channel"
    serializer = serialize_channel

    def get_all(self, org):
        return org.channels.all()

    def get_set_url(self, org, simulator=False):
        url = super().get_set_url(org)
        if simulator:
            url += "?simulator=1"
        return url

    def serialize_set(self, org, simulator=False):
        from temba.channels.models import Channel

        serialized = super().serialize_set(org, simulator)
        if simulator:
            serialized.append(Channel.SIMULATOR_CHANNEL)

        return serialized


class FieldType(AssetType):
    name = "field"
    serializer = serialize_field

    def get_all(self, org):
        return org.contactfields(manager="user_fields").all()


class FlowType(AssetType):
    name = "flow"
    serializer = serialize_flow

    def get_all(self, org):
        return org.flows.filter(is_active=True, is_system=False)


class GroupType(AssetType):
    name = "group"
    serializer = serialize_group

    def get_all(self, org):
        return org.all_groups(manager="user_groups").all()


class LabelType(AssetType):
    name = "label"
    serializer = serialize_label

    def get_all(self, org):
        return org.label_set(manager="label_objects").all()


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
        )


ASSET_TYPES = [cls() for cls in AssetType.__subclasses__()]
ASSET_TYPES_BY_NAME = {at.name: at for at in ASSET_TYPES}


def get_asset_type(t):
    return ASSET_TYPES_BY_NAME[t if isinstance(t, str) else t.name]


def get_asset_urls(org, simulator=False):
    return {at.name: at.get_set_url(org, simulator) for at in ASSET_TYPES if at.is_supported(org)}
