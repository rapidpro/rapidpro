
from django.conf import settings
from django.utils.translation import ugettext_lazy as _

from ...models import Channel, ChannelType
from .views import ClaimView


class ExternalType(ChannelType):
    """
    A external channel which speaks our own API language
    """

    code = "EX"
    category = ChannelType.Category.PHONE

    courier_url = r"^ex/(?P<uuid>[a-z0-9\-]+)/(?P<action>sent|delivered|failed|received|receive)$"

    name = "External API"
    icon = "icon-power-cord"

    claim_blurb = _("""Use our pluggable API to connect an external service you already have.""")
    claim_view = ClaimView

    schemes = None  # can be any scheme
    max_length = 160
    attachment_support = False

    def get_configuration_context_dict(self, channel):
        context = dict(channel=channel, ip_addresses=settings.IP_ADDRESSES)

        config = channel.config
        send_url = config[Channel.CONFIG_SEND_URL]
        send_body = config.get(Channel.CONFIG_SEND_BODY, Channel.CONFIG_DEFAULT_SEND_BODY)

        example_payload = {
            "to": "+250788123123",
            "to_no_plus": "250788123123",
            "text": "Love is patient. Love is kind.",
            "from": channel.address,
            "from_no_plus": channel.address.lstrip("+"),
            "id": "1241244",
            "channel": str(channel.id),
        }

        content_type = config.get(Channel.CONFIG_CONTENT_TYPE, Channel.CONTENT_TYPE_URLENCODED)
        context["example_content_type"] = "Content-Type: " + Channel.CONTENT_TYPES[content_type]
        context["example_url"] = Channel.replace_variables(send_url, example_payload)
        context["example_body"] = Channel.replace_variables(send_body, example_payload, content_type)

        return context
