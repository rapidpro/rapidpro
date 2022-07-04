import requests

from django.conf import settings
from django.forms import ValidationError
from django.urls import re_path
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from temba.channels.types.whatsapp_cloud.views import ClaimView
from temba.contacts.models import URN
from temba.request_logs.models import HTTPLog
from temba.utils.whatsapp.views import SyncLogsView, TemplatesView

from ...models import ChannelType


class WhatsAppCloudType(ChannelType):
    """
    A WhatsApp Cloud Channel Type
    """

    extra_links = [dict(name=_("Message Templates"), link="channels.types.whatsapp_cloud.templates")]

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
    attachment_support = True

    def is_available_to(self, user):
        return False, False

    def get_urls(self):
        return [
            self.get_claim_url(),
            re_path(r"^(?P<uuid>[a-z0-9\-]+)/templates$", TemplatesView.as_view(), name="templates"),
            re_path(r"^(?P<uuid>[a-z0-9\-]+)/sync_logs$", SyncLogsView.as_view(), name="sync_logs"),
        ]

    def activate(self, channel):
        waba_id = channel.config.get("wa_waba_id")
        waba_currency = channel.config.get("wa_currency")
        waba_business_id = channel.config.get("wa_business_id")

        # Assigh system user to WABA
        url = f"https://graph.facebook.com/v13.0/{waba_id}/assigned_users"
        params = {"user": f"{settings.WHATSAPP_ADMIN_SYSTEM_USER_ID}", "tasks": ["MANAGE"]}
        headers = {"Authorization": f"Bearer {settings.WHATSAPP_ADMIN_SYSTEM_USER_TOKEN}"}

        resp = requests.post(url, params=params, headers=headers)

        if resp.status_code != 200:  # pragma: no cover
            raise ValidationError(_("Unable to add system user to %s" % waba_id))

        if waba_business_id != settings.WHATSAPP_FACEBOOK_BUSINESS_ID:
            # Get credit line ID
            url = f"https://graph.facebook.com/v13.0/{settings.WHATSAPP_FACEBOOK_BUSINESS_ID}/extendedcredits"
            params = {"fields": "id,legal_entity_name"}
            resp = requests.get(url, params=params, headers=headers)

            if resp.status_code != 200:  # pragma: no cover
                raise ValidationError(_("Unable to fetch credit line ID"))

            data = resp.json().get("data", [])
            if data:
                credit_line_id = data[0].get("id", None)

            url = f"https://graph.facebook.com/v13.0/{credit_line_id}/whatsapp_credit_sharing_and_attach"
            params = {"waba_id": waba_id, "waba_currency": waba_currency}
            resp = requests.post(url, params=params, headers=headers)

            if resp.status_code != 200:  # pragma: no cover
                raise ValidationError(_("Unable to assign credit line ID"))

        # Subscribe to events
        url = f"https://graph.facebook.com/v13.0/{waba_id}/subscribed_apps"
        resp = requests.post(url, headers=headers)

        if resp.status_code != 200:  # pragma: no cover
            raise ValidationError(_("Unable to subscribe to app to WABA with ID %s" % waba_id))

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
                elapsed = (timezone.now() - start).total_seconds() * 1000
                HTTPLog.create_from_response(
                    HTTPLog.WHATSAPP_TEMPLATES_SYNCED, url, resp, channel=channel, request_time=elapsed
                )
                if resp.status_code != 200:  # pragma: no cover
                    return [], False

                template_data.extend(resp.json()["data"])
                url = resp.json().get("paging", {}).get("next", None)
            return template_data, True
        except requests.RequestException as e:
            HTTPLog.create_from_exception(HTTPLog.WHATSAPP_TEMPLATES_SYNCED, url, e, start, channel=channel)
            return [], False
