from django.utils.translation import gettext_lazy as _

from temba.contacts.models import URN

from ...models import ChannelType, ConfigUI
from .views import ClaimView


class FirebaseCloudMessagingType(ChannelType):
    """
    A Firebase Cloud Messaging channel (https://firebase.google.com/docs/cloud-messaging/)
    """

    code = "FCM"
    name = "Firebase Cloud Messaging"
    category = ChannelType.Category.API

    unique_addresses = True

    courier_url = r"^fcm/(?P<uuid>[a-z0-9\-]+)/(?P<action>register|receive)$"
    schemes = [URN.FCM_SCHEME]

    claim_blurb = _(
        "Add a %(link)s channel to send and receive messages. Your users will need an App to send and receive messages."
    ) % {
        "link": '<a target="_blank" href="https://firebase.google.com/docs/cloud-messaging/">Firebase Cloud Messaging</a>'
    }
    claim_view = ClaimView

    config_ui = ConfigUI(
        blurb=_(
            "To finish configuring this channel, you'll have to POST to the following URLs with the "
            "parameters below."
        ),
        endpoints=[
            ConfigUI.Endpoint(
                courier="register",
                label=_("Contact Register"),
                help=_(
                    "To register contacts, POST to the following URL with the parameters urn, "
                    "fcm_token and optionally name."
                ),
            ),
            ConfigUI.Endpoint(
                courier="receive",
                label=_("Receive URL"),
                help=_(
                    "To handle incoming messages, POST to the following URL with the parameters from, msg and fcm_token."
                ),
            ),
        ],
    )
