import requests

from django.forms import ValidationError
from django.urls import re_path, reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from temba.channels.models import Channel
from temba.channels.types.dialog360_legacy.views import ClaimView
from temba.contacts.models import URN
from temba.request_logs.models import HTTPLog
from temba.utils.whatsapp import update_api_version
from temba.utils.whatsapp.views import SyncLogsView, TemplatesView

from ...models import ChannelType, ConfigUI


class Dialog360LegacyType(ChannelType):
    """
    A 360 Dialog Channel Type
    """

    code = "D3"
    name = "360Dialog WhatsApp"
    category = ChannelType.Category.SOCIAL_MEDIA

    courier_url = r"^d3/(?P<uuid>[a-z0-9\-]+)/(?P<action>receive)$"
    schemes = [URN.WHATSAPP_SCHEME]

    claim_blurb = _("Activate your own enterprise WhatsApp account in %(link)s to communicate with your contacts. ") % {
        "link": '<a target="_blank" href="https://www.360dialog.com/">360Dialog</a>'
    }
    claim_view = ClaimView

    config_ui = ConfigUI()  # has own template

    menu_items = [dict(label=_("Message Templates"), view_name="channels.types.dialog360_legacy.templates")]

    def get_urls(self):
        return [
            self.get_claim_url(),
            re_path(r"^(?P<uuid>[a-z0-9\-]+)/templates$", TemplatesView.as_view(channel_type=self), name="templates"),
            re_path(r"^(?P<uuid>[a-z0-9\-]+)/sync_logs$", SyncLogsView.as_view(channel_type=self), name="sync_logs"),
        ]

    def get_headers(self, channel):
        return {"D360-API-KEY": channel.config[Channel.CONFIG_AUTH_TOKEN], "Content-Type": "application/json"}

    def activate(self, channel):
        domain = channel.org.get_brand_domain()

        # first set our callbacks
        payload = {"url": "https://" + domain + reverse("courier.d3", args=[channel.uuid, "receive"])}
        resp = requests.post(
            channel.config[Channel.CONFIG_BASE_URL] + "/v1/configs/webhook",
            json=payload,
            headers=self.get_headers(channel),
        )

        if resp.status_code != 200:
            raise ValidationError(_("Unable to register callbacks: %(resp)s"), params={"resp": resp.content})

        update_api_version(channel)

    def get_api_templates(self, channel):
        if Channel.CONFIG_AUTH_TOKEN not in channel.config:  # pragma: no cover
            return [], False

        templates_url = "%s/v1/configs/templates" % channel.config.get(Channel.CONFIG_BASE_URL, "")
        start = timezone.now()

        try:
            response = requests.get(templates_url, headers=self.get_headers(channel))
            HTTPLog.from_response(HTTPLog.WHATSAPP_TEMPLATES_SYNCED, response, start, timezone.now(), channel=channel)

            if response.status_code != 200:  # pragma: no cover
                return [], False

            template_data = response.json()["waba_templates"]
            return template_data, True
        except requests.RequestException as e:
            HTTPLog.from_exception(HTTPLog.WHATSAPP_TEMPLATES_SYNCED, e, start, channel=channel)
            return [], False

    def check_health(self, channel):
        response = requests.get(
            channel.config[Channel.CONFIG_BASE_URL] + "/v1/health", headers=self.get_headers(channel)
        )

        if response.status_code != 200:
            raise requests.RequestException("Could not check api status", response=response)

        return response

    def is_available_to(self, org, user):
        return False, False
