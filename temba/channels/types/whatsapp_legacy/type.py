import base64

import requests

from django.forms import ValidationError
from django.urls import re_path, reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from temba.channels.models import Channel
from temba.channels.types.whatsapp_legacy.views import ClaimView
from temba.contacts.models import URN
from temba.request_logs.models import HTTPLog
from temba.utils.whatsapp import update_api_version
from temba.utils.whatsapp.views import RefreshView

from ...models import ChannelType, ConfigUI

CONFIG_FB_BUSINESS_ID = "fb_business_id"
CONFIG_FB_ACCESS_TOKEN = "fb_access_token"
CONFIG_FB_NAMESPACE = "fb_namespace"
CONFIG_FB_TEMPLATE_LIST_DOMAIN = "fb_template_list_domain"
CONFIG_FB_TEMPLATE_API_VERSION = "fb_template_list_domain_api_version"

TEMPLATE_LIST_URL = "https://%s/%s/%s/message_templates"


class WhatsAppLegacyType(ChannelType):
    """
    A WhatsApp Channel Type
    """

    code = "WA"
    name = "WhatsApp Legacy"
    category = ChannelType.Category.SOCIAL_MEDIA
    beta_only = True

    unique_addresses = True

    courier_url = r"^wa/(?P<uuid>[a-z0-9\-]+)/(?P<action>receive)$"
    schemes = [URN.WHATSAPP_SCHEME]
    template_type = "whatsapp"

    claim_blurb = _("If you have an enterprise WhatsApp account, you can connect it to communicate with your contacts")
    claim_view = ClaimView

    config_ui = ConfigUI()  # has own template

    def get_urls(self):
        return [
            self.get_claim_url(),
            re_path(r"^(?P<uuid>[a-z0-9\-]+)/refresh$", RefreshView.as_view(channel_type=self), name="refresh"),
        ]

    def get_api_headers(self, channel):
        return {"Authorization": "Bearer %s" % channel.config[Channel.CONFIG_AUTH_TOKEN]}

    def activate(self, channel):
        domain = channel.org.get_brand_domain()
        headers = self.get_api_headers(channel)

        # first set our callbacks
        payload = {"webhooks": {"url": "https://" + domain + reverse("courier.wa", args=[channel.uuid, "receive"])}}
        resp = requests.patch(
            channel.config[Channel.CONFIG_BASE_URL] + "/v1/settings/application", json=payload, headers=headers
        )

        if resp.status_code != 200:
            raise ValidationError(_("Unable to register callbacks: %s") % resp.content)

        # update our quotas so we can send at 15/s
        payload = {
            "messaging_api_rate_limit": ["15", "54600", "1000000"],
            "contacts_scrape_rate_limit": "1000000",
            "contacts_api_rate_limit": ["15", "54600", "1000000"],
        }
        resp = requests.patch(
            channel.config[Channel.CONFIG_BASE_URL] + "/v1/settings/application", json=payload, headers=headers
        )

        if resp.status_code != 200:
            raise ValidationError(_("Unable to configure channel: %s") % resp.content)

        update_api_version(channel)

    def fetch_templates(self, channel) -> list:
        # Retrieve the template domain, fallback to the default for channels that have been setup earlier for backwards
        # compatibility
        facebook_template_domain = channel.config.get(CONFIG_FB_TEMPLATE_LIST_DOMAIN, "graph.facebook.com")
        facebook_business_id = channel.config.get(CONFIG_FB_BUSINESS_ID)
        facebook_template_api_version = channel.config.get(CONFIG_FB_TEMPLATE_API_VERSION, "v14.0")
        url = TEMPLATE_LIST_URL % (facebook_template_domain, facebook_template_api_version, facebook_business_id)
        templates = []

        while url:
            start = timezone.now()
            try:
                response = requests.get(
                    url, params={"access_token": channel.config[CONFIG_FB_ACCESS_TOKEN], "limit": 255}
                )
                response.raise_for_status()
                HTTPLog.from_response(
                    HTTPLog.WHATSAPP_TEMPLATES_SYNCED, response, start, timezone.now(), channel=channel
                )

                templates.extend(response.json()["data"])
                url = response.json().get("paging", {}).get("next", None)
            except requests.RequestException as e:
                HTTPLog.from_exception(HTTPLog.WHATSAPP_TEMPLATES_SYNCED, e, start, channel=channel)
                raise e

        return templates

    def check_health(self, channel):
        headers = self.get_api_headers(channel)

        try:
            response = requests.get(channel.config[Channel.CONFIG_BASE_URL] + "/v1/health", headers=headers)
        except Exception as ex:
            raise Exception(f"Could not establish a connection with the WhatsApp server: {ex}")

        if response.status_code >= 400:
            raise requests.RequestException(f"Error checking API health: {response.content}", response=response)

        return response

    def get_redact_values(self, channel) -> tuple:
        """
        Gets the values to redact from logs
        """
        credentials_base64 = base64.b64encode(
            f"{channel.config[Channel.CONFIG_USERNAME]}:{channel.config[Channel.CONFIG_PASSWORD]}".encode()
        ).decode()
        return (
            channel.config[CONFIG_FB_ACCESS_TOKEN],
            channel.config[Channel.CONFIG_PASSWORD],
            credentials_base64,
        )
