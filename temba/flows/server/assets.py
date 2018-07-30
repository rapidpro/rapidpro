from django.conf import settings
from django.db.models import Prefetch

from temba.api.models import ResthookSubscriber
from temba.utils.dates import datetime_to_ms

ASSET_HOST = "http://localhost:8000" if settings.TESTING else ("https://%s" % settings.HOSTNAME)


class AssetType:
    name = None
    serializer = None

    def is_supported(self, org):
        return True

    def get_url(self, org, simulator):
        pass


class SetType(AssetType):
    """
    An asset type which is a set of things, e.g. group_set, label_set
    """

    def get_all(self, org):
        pass

    def get_active(self, org):
        return self.get_all(org).filter(is_active=True)

    def serialize_active(self, org, simulator=False):
        return {
            "type": self.name,
            "url": self.get_url(org, simulator),
            "content": [self.serializer(o) for o in self.get_active(org)],
        }

    def _get_timestamp(self, org):
        last_modified = self.get_all(org).order_by("modified_on").last()
        return datetime_to_ms(last_modified.modified_on) if last_modified else 0


class SetItemType(SetType):
    """
    An asset type which is a single item from a set of things, e.g. flow
    """

    def get_item(self, org, uuid):
        return self.get_active(org).filter(uuid=uuid)

    def serialize_item(self, org, uuid):
        return {
            "type": self.name,
            "url": self.get_url(org, simulator=False),
            "content": self.serializer(self.get_item(org, uuid)),
        }


class ChannelSetType(SetType):
    name = "channel_set"

    def get_all(self, org):
        return org.channels.all()

    def get_url(self, org, simulator):
        return f"{ASSET_HOST}/{org.id}/{self._get_timestamp(org)}/channel/?simulator={1 if simulator else 0}"


class FieldSetType(SetType):
    name = "field_set"

    def get_all(self, org):
        return org.contactfields.all()

    def get_url(self, org, simulator):
        return f"{ASSET_HOST}/{org.id}/{self._get_timestamp(org)}/field/"


class FlowType(SetItemType):
    name = "flow"

    def get_all(self, org):
        return org.flows.all()

    def get_url(self, org, simulator):
        return f"{ASSET_HOST}/{org.id}/{self._get_timestamp(org)}/flow/{{uuid}}/"


class GroupSetType(SetType):
    name = "group_set"

    def get_all(self, org):
        return org.all_groups(manager="user_groups").all()

    def get_url(self, org, simulator):
        return f"{ASSET_HOST}/{org.id}/{self._get_timestamp(org)}/group/"


class LabelSetType(SetType):
    name = "label_set"

    def get_all(self, org):
        return org.label_set(manager="label_objects").all()

    def get_url(self, org, simulator):
        return f"{ASSET_HOST}/{org.id}/{self._get_timestamp(org)}/label/"


class LocationHierarchyType(AssetType):
    name = "location_hierarchy"

    def is_supported(self, org):
        return bool(org.country_id)

    def get_url(self, org, simulator):
        return f"{ASSET_HOST}/{org.id}/location_hierarchy/"


class ResthookSetType(SetType):
    name = "resthook_set"

    def get_all(self, org):
        return org.resthooks.prefetch_related(
            Prefetch("subscribers", ResthookSubscriber.objects.filter(is_active=True).order_by("created_on"))
        )

    def get_url(self, org, simulator):
        return f"{ASSET_HOST}/{org.id}/{self._get_timestamp(org)}/resthook/"


ASSET_TYPES = [cls() for cls in AssetType.__subclasses__()]


def get_asset_urls(org, simulator=False):
    return {at.name: at.get_url(org, simulator) for at in ASSET_TYPES if at.is_supported(org)}
