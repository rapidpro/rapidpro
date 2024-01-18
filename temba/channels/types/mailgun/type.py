from django.utils.translation import gettext_lazy as _

from temba.contacts.models import URN

from ...models import ChannelType, ConfigUI
from .views import ClaimView


class MailgunType(ChannelType):
    """
    A Mailgun email channel.
    """

    code = "MLG"
    name = "Mailgun"
    category = ChannelType.Category.API

    courier_url = r"^mlg/(?P<uuid>[a-z0-9\-]+)/receive$"
    schemes = [URN.EMAIL_SCHEME]

    claim_blurb = _("Add a %(link)s channel to send and receive messages as emails.") % {
        "link": '<a target="_blank" href="https://mailgun.com/">Mailgun</a>'
    }
    claim_view = ClaimView

    config_ui = ConfigUI(
        blurb=_(
            "To finish configuring this channel, you'll need to add the following webhook to your domain for the "
            "Delivered Messages event type."
        ),
        endpoints=[
            ConfigUI.Endpoint(
                courier="receive",
                label=_("Delivery URL"),
                help=_("Webhook URL for the Delivered Messages event type."),
            ),
        ],
    )

    def is_available_to(self, org, user):
        return user.is_staff, user.is_staff
