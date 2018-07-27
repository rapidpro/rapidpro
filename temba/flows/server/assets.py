from django.conf import settings

from temba.utils.dates import datetime_to_ms

ASSET_HOST = "http://localhost:8000" if settings.TESTING else ("https://%s" % settings.HOSTNAME)


class AssetType:
    name = None
    queryset = None

    def get_all(self, org):
        pass

    def get_url(self, org, simulator):
        pass

    def _get_timestamp(self, org):
        last_modified = self.get_all(org).order_by("modified_on").last()
        return datetime_to_ms(last_modified.modified_on) if last_modified else 0


class ChannelSetAssetType(AssetType):
    name = "channel_set"

    def get_all(self, org):
        return org.channels.all()

    def get_url(self, org, simulator):
        return f"{ASSET_HOST}/{org.id}/{self._get_timestamp(org)}/channel/?simulator={1 if simulator else 0}"


class FieldSetAssetType(AssetType):
    name = "field_set"

    def get_all(self, org):
        return org.contactfields.all()

    def get_url(self, org, simulator):
        return f"{ASSET_HOST}/{org.id}/{self._get_timestamp(org)}/field/"


class FlowAssetType(AssetType):
    name = "flow"

    def get_all(self, org):
        return org.flows.all()

    def get_url(self, org, simulator):
        return f"{ASSET_HOST}/{org.id}/{self._get_timestamp(org)}/flow/{{uuid}}/"


class GroupSetAssetType(AssetType):
    name = "group_set"

    def get_all(self, org):
        return org.all_groups(manager="user_groups").all()

    def get_url(self, org, simulator):
        return f"{ASSET_HOST}/{org.id}/{self._get_timestamp(org)}/group/"


class LabelSetAssetType(AssetType):
    name = "label_set"

    def get_all(self, org):
        return org.label_set(manager="label_objects").all()

    def get_url(self, org, simulator):
        return f"{ASSET_HOST}/{org.id}/{self._get_timestamp(org)}/label/"


class ResthookSetAssetType(AssetType):
    name = "resthook_set"

    def get_all(self, org):
        return org.resthooks.all()

    def get_url(self, org, simulator):
        return f"{ASSET_HOST}/{org.id}/{self._get_timestamp(org)}/resthook/"


ASSET_TYPES = [cls() for cls in AssetType.__subclasses__()]


def get_asset_server(org, simulator=False):
    return {at.name: at.get_url(org, simulator) for at in ASSET_TYPES}
