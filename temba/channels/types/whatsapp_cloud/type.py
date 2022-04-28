import requests

from django.conf import settings
from django.forms import ValidationError
from django.utils.translation import gettext_lazy as _

from temba.channels.types.whatsapp_cloud.views import ClaimView
from temba.contacts.models import URN

from ...models import ChannelType


class WhatsAppCloudType(ChannelType):
    """
    A WhatsApp Cloud Channel Type
    """

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

    def activate(self, channel):
        waba_id = channel.config.get("wa_waba_id")
        waba_currency = channel.config.get("wa_currency")
        waba_business_id = channel.config.get("wa_business_id")

        # Assigh system user to WABA
        url = f"https://graph.facebook.com/v13.0/{waba_id}/assigned_users"
        params = {"user": f"{settings.WHATSAPP_ADMIN_SYSTEM_USER_ID}", "tasks": ["MANAGE"]}
        headers = {"Authorization": f"Bearer {settings.WHATSAPP_ADMIN_SYSTEM_USER_TOKEN}"}

        resp = requests.post(url, params=params, headers=headers)

        if resp.status_code != 200:
            raise ValidationError(_("Unable to add system user to %s" % waba_id))

        if waba_business_id != settings.WHATSAPP_FACEBOOK_BUSINESS_ID:
            # Get credit line ID
            url = f"https://graph.facebook.com/v13.0/{settings.WHATSAPP_FACEBOOK_BUSINESS_ID}/extendedcredits"
            params = {"fields": "id,legal_entity_name"}
            resp = requests.get(url, params=params, headers=headers)

            if resp.status_code != 200:
                raise ValidationError(_("Unable to fetch credit line ID"))

            data = resp.json().get("data", [])
            if data:
                credit_line_id = data[0].get("id", None)

            url = f"https://graph.facebook.com/v13.0/{credit_line_id}/whatsapp_credit_sharing_and_attach"
            params = {"waba_id": waba_id, "waba_currency": waba_currency}
            resp = requests.post(url, params=params, headers=headers)

            if resp.status_code != 200:
                raise ValidationError(_("Unable to assign credit line ID"))

        # Subscribe to events
        url = f"https://graph.facebook.com/v13.0/{waba_id}/subscribed_apps"
        resp = requests.post(url, headers=headers)

        if resp.status_code != 200:
            raise ValidationError(_("Unable to subscribe to app to WABA with ID %s" % waba_id))
