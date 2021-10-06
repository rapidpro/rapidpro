import requests

from django.conf.urls import url
from django.forms import ValidationError
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import ugettext_lazy as _

from temba.channels.models import Channel
from temba.channels.types.whatsapp.views import ClaimView
from temba.contacts.models import URN
from temba.request_logs.models import HTTPLog
from temba.templates.models import TemplateTranslation
from temba.utils.whatsapp import update_api_version
from temba.utils.whatsapp.views import RefreshView, SyncLogsView, TemplatesView

from ...models import ChannelType

CONFIG_FB_BUSINESS_ID = "fb_business_id"
CONFIG_FB_ACCESS_TOKEN = "fb_access_token"
CONFIG_FB_NAMESPACE = "fb_namespace"
CONFIG_FB_TEMPLATE_LIST_DOMAIN = "fb_template_list_domain"

TEMPLATE_LIST_URL = "https://%s/v3.3/%s/message_templates"


class WhatsAppType(ChannelType):
    """
    A WhatsApp Channel Type
    """

    extra_links = [dict(name=_("Message Templates"), link="channels.types.whatsapp.templates")]

    code = "WA"
    category = ChannelType.Category.SOCIAL_MEDIA
    beta_only = True

    courier_url = r"^wa/(?P<uuid>[a-z0-9\-]+)/(?P<action>receive)$"

    name = "WhatsApp"
    icon = "icon-whatsapp"

    claim_blurb = _("If you have an enterprise WhatsApp account, you can connect it to communicate with your contacts")
    claim_view = ClaimView

    schemes = [URN.WHATSAPP_SCHEME]
    max_length = 4096
    attachment_support = True

    def get_urls(self):
        return [
            self.get_claim_url(),
            url(r"^(?P<uuid>[a-z0-9\-]+)/refresh$", RefreshView.as_view(), name="refresh"),
            url(r"^(?P<uuid>[a-z0-9\-]+)/templates$", TemplatesView.as_view(), name="templates"),
            url(r"^(?P<uuid>[a-z0-9\-]+)/sync_logs$", SyncLogsView.as_view(), name="sync_logs"),
        ]

    def deactivate(self, channel):
        # deactivate all translations associated with us
        TemplateTranslation.trim(channel, [])

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

    def get_api_templates(self, channel):
        if (
            CONFIG_FB_BUSINESS_ID not in channel.config or CONFIG_FB_ACCESS_TOKEN not in channel.config
        ):  # pragma: no cover
            return [], False

        start = timezone.now()
        try:
            # Retrieve the template domain, fallback to the default for channels
            # that have been setup earlier for backwards compatibility
            facebook_template_domain = channel.config.get(CONFIG_FB_TEMPLATE_LIST_DOMAIN, "graph.facebook.com")
            facebook_business_id = channel.config.get(CONFIG_FB_BUSINESS_ID)
            url = TEMPLATE_LIST_URL % (facebook_template_domain, facebook_business_id)
            template_data = []
            while url:
                response = requests.get(
                    url, params=dict(access_token=channel.config[CONFIG_FB_ACCESS_TOKEN], limit=255)
                )
                elapsed = (timezone.now() - start).total_seconds() * 1000
                HTTPLog.create_from_response(
                    HTTPLog.WHATSAPP_TEMPLATES_SYNCED, url, response, channel=channel, request_time=elapsed
                )

                if response.status_code != 200:  # pragma: no cover
                    return [], False

                template_data.extend(response.json()["data"])
                url = response.json().get("paging", {}).get("next", None)
            return template_data, True
        except requests.RequestException as e:
            HTTPLog.create_from_exception(HTTPLog.WHATSAPP_TEMPLATES_SYNCED, url, e, start, channel=channel)
            return [], False

    def check_health(self, channel):
        headers = self.get_api_headers(channel)

        try:
            response = requests.get(channel.config[Channel.CONFIG_BASE_URL] + "/v1/health", headers=headers)
        except Exception as ex:
            raise Exception(f"Could not establish a connection with the WhatsApp server: {ex}")

        if response.status_code >= 400:
            raise requests.RequestException(f"Error checking API health: {response.content}", response=response)

        return response
