import requests

from django.conf import settings
from django.forms import ValidationError
from django.urls import re_path
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from temba.channels.types.whatsapp_cloud.views import ClaimView, ClearSessionToken, RequestCode, VerifyCode
from temba.contacts.models import URN
from temba.request_logs.models import HTTPLog
from temba.utils.whatsapp.views import SyncLogsView, TemplatesView

from ...models import ChannelType


class WhatsAppCloudType(ChannelType):
    """
    A WhatsApp Cloud Channel Type
    """

    extra_links = [
        dict(label=_("Message Templates"), view_name="channels.types.whatsapp_cloud.templates"),
        dict(label=_("Verify Number"), view_name="channels.types.whatsapp_cloud.request_code"),
    ]

    code = "WAC"
    category = ChannelType.Category.SOCIAL_MEDIA
    beta_only = True

    courier_url = r"^wac/receive"

    name = "WhatsApp Cloud"
    icon = "icon-whatsapp"

    show_config_page = False

    claim_blurb = _("If you have an enterprise WhatsApp account, you can connect it to communicate with your contacts")
    claim_view = ClaimView

    schemes = [URN.WHATSAPP_SCHEME]
    max_length = 4096

    redact_values = (settings.WHATSAPP_ADMIN_SYSTEM_USER_TOKEN,)

    def get_urls(self):
        return [
            self.get_claim_url(),
            re_path(r"^clear_session_token$", ClearSessionToken.as_view(), name="clear_session_token"),
            re_path(r"^(?P<uuid>[a-z0-9\-]+)/templates$", TemplatesView.as_view(), name="templates"),
            re_path(r"^(?P<uuid>[a-z0-9\-]+)/sync_logs$", SyncLogsView.as_view(), name="sync_logs"),
            re_path(r"^(?P<uuid>[a-z0-9\-]+)/request_code$", RequestCode.as_view(), name="request_code"),
            re_path(r"^(?P<uuid>[a-z0-9\-]+)/verify_code$", VerifyCode.as_view(), name="verify_code"),
        ]

    def activate(self, channel):
        waba_id = channel.config.get("wa_waba_id")
        wa_pin = channel.config.get("wa_pin")

        headers = {"Authorization": f"Bearer {settings.WHATSAPP_ADMIN_SYSTEM_USER_TOKEN}"}

        # Subscribe to events
        url = f"https://graph.facebook.com/v13.0/{waba_id}/subscribed_apps"
        resp = requests.post(url, headers=headers)

        if resp.status_code != 200:  # pragma: no cover
            raise ValidationError(_("Unable to subscribe to app to WABA with ID %s" % waba_id))

        # register numbers
        url = f"https://graph.facebook.com/v13.0/{channel.address}/register"
        data = {"messaging_product": "whatsapp", "pin": wa_pin}

        resp = requests.post(url, data=data, headers=headers)

        if resp.status_code != 200:  # pragma: no cover
            raise ValidationError(
                _("Unable to register phone with ID %s from WABA with ID %s" % (channel.address, waba_id))
            )

    def get_api_templates(self, channel):
        if not settings.WHATSAPP_ADMIN_SYSTEM_USER_TOKEN:  # pragma: no cover
            return [], False

        waba_id = channel.config.get("wa_waba_id", None)
        if not waba_id:  # pragma: no cover
            return [], False

        start = timezone.now()
        try:
            template_data = []
            url = f"https://graph.facebook.com/v14.0/{waba_id}/message_templates"

            headers = {"Authorization": f"Bearer {settings.WHATSAPP_ADMIN_SYSTEM_USER_TOKEN}"}
            while url:
                resp = requests.get(url, params=dict(limit=255), headers=headers)
                HTTPLog.from_response(HTTPLog.WHATSAPP_TEMPLATES_SYNCED, resp, start, timezone.now(), channel=channel)
                if resp.status_code != 200:  # pragma: no cover
                    return [], False

                template_data.extend(resp.json()["data"])
                url = resp.json().get("paging", {}).get("next", None)
            return template_data, True
        except requests.RequestException as e:
            HTTPLog.from_exception(HTTPLog.WHATSAPP_TEMPLATES_SYNCED, e, start, channel=channel)
            return [], False
