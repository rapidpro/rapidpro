import requests

from django.conf import settings
from django.forms import ValidationError
from django.urls import re_path
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from temba.contacts.models import URN
from temba.request_logs.models import HTTPLog

from ...models import ChannelType
from .views import ClaimView, ClearSessionToken, Connect, RequestCode, VerifyCode


class WhatsAppType(ChannelType):
    """
    A WhatsApp Cloud Channel Type
    """

    SESSION_USER_TOKEN = "WHATSAPP_CLOUD_USER_TOKEN"

    code = "WAC"
    name = "WhatsApp"
    category = ChannelType.Category.SOCIAL_MEDIA
    beta_only = True

    unique_addresses = True

    courier_url = r"^wac/receive"
    schemes = [URN.WHATSAPP_SCHEME]
    template_type = "whatsapp"

    claim_blurb = _("If you have an enterprise WhatsApp account, you can connect it to communicate with your contacts")
    claim_view = ClaimView

    menu_items = [dict(label=_("Verify Number"), view_name="channels.types.whatsapp.request_code")]

    def get_urls(self):
        return [
            self.get_claim_url(),
            re_path(
                r"^clear_session_token/$", ClearSessionToken.as_view(channel_type=self), name="clear_session_token"
            ),
            re_path(
                r"^(?P<uuid>[a-z0-9\-]+)/request_code/$", RequestCode.as_view(channel_type=self), name="request_code"
            ),
            re_path(r"^(?P<uuid>[a-z0-9\-]+)/verify_code/$", VerifyCode.as_view(channel_type=self), name="verify_code"),
            re_path(r"^connect/$", Connect.as_view(channel_type=self), name="connect"),
        ]

    def activate(self, channel):
        waba_id = channel.config.get("wa_waba_id")
        wa_pin = channel.config.get("wa_pin")

        headers = {"Authorization": f"Bearer {settings.WHATSAPP_ADMIN_SYSTEM_USER_TOKEN}"}

        # Subscribe to events
        url = f"https://graph.facebook.com/v18.0/{waba_id}/subscribed_apps"
        resp = requests.post(url, headers=headers)

        if resp.status_code != 200:  # pragma: no cover
            raise ValidationError(_("Unable to subscribe to app to WABA with ID %s" % waba_id))

        # register numbers
        url = f"https://graph.facebook.com/v18.0/{channel.address}/register"
        data = {"messaging_product": "whatsapp", "pin": wa_pin}

        resp = requests.post(url, data=data, headers=headers)

        if resp.status_code != 200:  # pragma: no cover
            raise ValidationError(
                _("Unable to register phone with ID %s from WABA with ID %s" % (channel.address, waba_id))
            )

    def fetch_templates(self, channel) -> list:
        waba_id = channel.config["wa_waba_id"]
        url = f"https://graph.facebook.com/v18.0/{waba_id}/message_templates"
        headers = {"Authorization": f"Bearer {settings.WHATSAPP_ADMIN_SYSTEM_USER_TOKEN}"}
        templates = []

        while url:
            start = timezone.now()
            try:
                response = requests.get(url, params={"limit": 255}, headers=headers)
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

    def get_redact_values(self, channel) -> tuple:
        """
        Gets the values to redact from logs
        """
        return (settings.WHATSAPP_ADMIN_SYSTEM_USER_TOKEN,)
