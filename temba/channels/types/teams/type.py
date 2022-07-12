from django.utils.translation import gettext_lazy as _

from temba.contacts.models import URN

from ...models import ChannelType
from .views import ClaimView


class TeamsType(ChannelType):
    """
    A Teams bot channel
    """

    CONFIG_TEAMS_APPLICATION_ID = "appID"
    CONFIG_TEAMS_BOT_ID = "botID"
    CONFIG_TEAMS_TENANT_ID = "tenantID"
    CONFIG_TEAMS_APPLICATION_PASSWORD = "app_password"
    CONFIG_TEAMS_BOT_NAME = "bot_name"

    code = "TM"
    category = ChannelType.Category.SOCIAL_MEDIA
    name = "Teams"
    icon = "icon-power-cord"
    schemes = [URN.TEAMS_SCHEME]
    attachment_support = True

    courier_url = r"^tm/(?P<uuid>[a-z0-9\-]+)/receive$"

    claim_blurb = _("Add a %(link)s bot to send and receive messages to Microsoft Teams users.") % {
        "link": '<a href="https://teams.microsoft.com">Microsoft Teams</a>'
    }
    claim_view = ClaimView
