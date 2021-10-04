import requests

from django.conf.urls import url
from django.forms import ValidationError
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import ugettext_lazy as _

from temba.channels.models import Channel
from temba.channels.types.dialog360.views import ClaimView
from temba.contacts.models import URN
from temba.request_logs.models import HTTPLog
from temba.utils.whatsapp import update_api_version
from temba.utils.whatsapp.views import SyncLogsView, TemplatesView

from ...models import ChannelType


class Dialog360Type(ChannelType):
    """
    A 360 Dialog Channel Type
    """

    extra_links = [dict(name=_("Message Templates"), link="channels.types.dialog360.templates")]

    code = "D3"
    category = ChannelType.Category.SOCIAL_MEDIA

    courier_url = r"^d3/(?P<uuid>[a-z0-9\-]+)/(?P<action>receive)$"

    name = "360Dialog WhatsApp"
    icon = "icon-whatsapp"

    claim_blurb = _(
        "Activate your own enterprise WhatsApp account in %(link)s to communicate with your contacts. "
    ) % {"link": '<a href="https://www.360dialog.com/">360Dialog</a>'}
    claim_view = ClaimView

    schemes = [URN.WHATSAPP_SCHEME]
    max_length = 4096
    attachment_support = True

    def get_urls(self):
        return [
            self.get_claim_url(),
            url(r"^(?P<uuid>[a-z0-9\-]+)/templates$", TemplatesView.as_view(), name="templates"),
            url(r"^(?P<uuid>[a-z0-9\-]+)/sync_logs$", SyncLogsView.as_view(), name="sync_logs"),
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

        start = timezone.now()
        try:

            templates_url = "%s/v1/configs/templates" % channel.config.get(Channel.CONFIG_BASE_URL, "")

            response = requests.get(templates_url, headers=self.get_headers(channel))
            elapsed = (timezone.now() - start).total_seconds() * 1000
            HTTPLog.create_from_response(
                HTTPLog.WHATSAPP_TEMPLATES_SYNCED, templates_url, response, channel=channel, request_time=elapsed
            )

            if response.status_code != 200:  # pragma: no cover
                return [], False

            template_data = response.json()["waba_templates"]
            return template_data, True
        except requests.RequestException as e:
            HTTPLog.create_from_exception(HTTPLog.WHATSAPP_TEMPLATES_SYNCED, templates_url, e, start, channel=channel)
            return [], False

    def check_health(self, channel):
        response = requests.get(
            channel.config[Channel.CONFIG_BASE_URL] + "/v1/health", headers=self.get_headers(channel)
        )

        if response.status_code != 200:
            raise requests.RequestException("Could not check api status", response=response)

        return response
