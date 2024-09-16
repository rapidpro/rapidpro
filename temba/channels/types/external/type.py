import json
from urllib.parse import quote_plus
from xml.sax.saxutils import escape

from django.utils.translation import gettext_lazy as _

from ...models import Channel, ChannelType, ConfigUI
from .views import ClaimView, UpdateForm


class ExternalType(ChannelType):
    """
    A external channel which speaks our own API language
    """

    code = "EX"
    name = "External API"
    category = ChannelType.Category.PHONE

    courier_url = r"^ex/(?P<uuid>[a-z0-9\-]+)/(?P<action>sent|delivered|failed|received|receive|stopped)$"
    schemes = None  # can be any scheme

    claim_blurb = _("Use our pluggable API to connect an external service you already have.")
    claim_view = ClaimView

    config_ui = ConfigUI()  # has own template

    update_form = UpdateForm

    CONFIG_SEND_AUTHORIZATION = "send_authorization"
    CONFIG_MAX_LENGTH = "max_length"
    CONFIG_SEND_METHOD = "method"
    CONFIG_SEND_BODY = "body"
    CONFIG_MT_RESPONSE_CHECK = "mt_response_check"
    CONFIG_CONTENT_TYPE = "content_type"

    CONFIG_DEFAULT_SEND_BODY = (
        "id={{id}}&text={{text}}&to={{to}}&to_no_plus={{to_no_plus}}&from={{from}}&from_no_plus={{from_no_plus}}"
        "&channel={{channel}}"
    )

    @classmethod
    def replace_variables(cls, text, variables, content_type=Channel.CONTENT_TYPE_URLENCODED):
        for key in variables.keys():
            replacement = str(variables[key])

            # encode based on our content type
            if content_type == Channel.CONTENT_TYPE_URLENCODED:
                replacement = quote_plus(replacement)

            # if this is JSON, need to wrap in quotes (and escape them)
            elif content_type == Channel.CONTENT_TYPE_JSON:
                replacement = json.dumps(replacement)

            # XML needs to be escaped
            elif content_type == Channel.CONTENT_TYPE_XML:
                replacement = escape(replacement)

            text = text.replace("{{%s}}" % key, replacement)

        return text

    def get_config_ui_context(self, channel):
        context = super().get_config_ui_context(channel)

        config = channel.config
        send_method = config.get(ExternalType.CONFIG_SEND_METHOD)
        send_url = config[Channel.CONFIG_SEND_URL]
        send_body = config.get(ExternalType.CONFIG_SEND_BODY, ExternalType.CONFIG_DEFAULT_SEND_BODY)

        example_payload = {
            "to": "+250788123123",
            "to_no_plus": "250788123123",
            "text": "Love is patient. Love is kind.",
            "from": channel.address,
            "from_no_plus": channel.address.lstrip("+"),
            "id": "1241244",
            "channel": str(channel.id),
        }

        content_type = config.get(ExternalType.CONFIG_CONTENT_TYPE, Channel.CONTENT_TYPE_URLENCODED)
        context["example_content_type"] = "Content-Type: " + Channel.CONTENT_TYPES.get(content_type, content_type)
        context["example_url"] = ExternalType.replace_variables(send_url, example_payload)
        context["example_body"] = ExternalType.replace_variables(send_body, example_payload, content_type)

        quick_replies_payload = {}

        if (send_method == "POST" or send_method == "PUT") and content_type == Channel.CONTENT_TYPE_JSON:
            quick_replies_payload["quick_replies"] = '["One","Two","Three"]'
        elif (send_method == "POST" or send_method == "PUT") and content_type == Channel.CONTENT_TYPE_XML:
            quick_replies_payload["quick_replies"] = "<item>One</item><item>Two</item><item>Three</item>"
        else:
            quick_replies_payload["quick_replies"] = "&quick_reply=One&quick_reply=Two&quick_reply=Three"

        context["example_url"] = ExternalType.replace_variables(
            context["example_url"], quick_replies_payload, "don't encode"
        )
        context["example_body"] = ExternalType.replace_variables(
            context["example_body"], quick_replies_payload, "don't encode"
        )
        return context
