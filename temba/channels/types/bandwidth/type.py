import xml.etree.ElementTree as ET

import requests

from django.forms import ValidationError
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from temba.contacts.models import URN

from ...models import Channel, ChannelType
from .views import ClaimView


class BandwidthType(ChannelType):
    """
    An Bandwidth channel type (https://www.bandwidth.com/)
    """

    code = "BW"
    name = "Bandwidth"
    category = ChannelType.Category.PHONE

    beta_only = True

    courier_url = r"^bw/(?P<uuid>[a-z0-9\-]+)/(?P<action>receive|status)$"

    schemes = [URN.TEL_SCHEME]
    max_length = 2048

    claim_view = ClaimView
    claim_blurb = _("If you have an %(link)s number, you can quickly connect it using their APIs.") % {
        "link": '<a href="https://www.bandwidth.com/">Bandwidth</a>'
    }

    show_config_page = False

    configuration_blurb = _(
        "To finish configuring your Bandwidth connection you need to set the following URLs in your "
        "Bandwidth account settings."
    )

    configuration_urls = (
        dict(
            label=_("Inbound Message Webhook URL"),
            url="https://{{ channel.callback_domain }}{% url 'courier.bw' channel.uuid 'receive' %}",
        ),
        dict(
            label=_("Outbound Message Webhook URL"),
            url="https://{{ channel.callback_domain }}{% url 'courier.bw' channel.uuid 'status' %}",
        ),
    )

    def activate(self, channel):
        domain = channel.org.get_brand_domain()
        receive_url = "https://" + domain + reverse("courier.bw", args=[channel.uuid, "receive"])
        status_url = "https://" + domain + reverse("courier.bw", args=[channel.uuid, "status"])

        account_id = channel.config.get("account_id")

        application = ET.Element("Application")
        ET.SubElement(application, "ServiceType", text="Messaging-V2")
        ET.SubElement(application, "AppName", text=f"{domain}/{channel.uuid}")
        ET.SubElement(application, "InboundCallbackUrl", text=receive_url)
        ET.SubElement(application, "OutboundCallbackUrl", text=status_url)
        request_callback_types = ET.SubElement(application, "RequestedCallbackTypes")
        ET.SubElement(request_callback_types, "CallbackType", text="message-delivered")
        ET.SubElement(request_callback_types, "CallbackType", text="message-failed")
        ET.SubElement(request_callback_types, "CallbackType", text="message-sending")

        url = f"https://dashboard.bandwidth.com/api/accounts/{account_id}/applications"

        resp = requests.post(
            url,
            data=ET.tostring(application),
            auth=(channel.config.get(Channel.CONFIG_USERNAME), channel.config.get(Channel.CONFIG_PASSWORD)),
        )

        if resp.status_code not in [200, 201, 202]:  # pragma: no cover
            raise ValidationError(_("Unable to create bandwidth application"))

        resp_root = ET.fromstring(resp.content)
        application_id_elt = resp_root.find("Application").find("ApplicationId")

        channel.config["application_id"] = application_id_elt.text

        channel.save(update_fields=("config",))
