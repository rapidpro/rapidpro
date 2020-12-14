from django.utils.translation import ugettext_lazy as _

from temba.contacts.models import URN

from ...models import ChannelType
from .views import ClaimView


class FirebaseCloudMessagingType(ChannelType):
    """
    A Firebase Cloud Messaging channel (https://firebase.google.com/docs/cloud-messaging/)
    """

    code = "FCM"
    category = ChannelType.Category.API

    courier_url = r"^fcm/(?P<uuid>[a-z0-9\-]+)/(?P<action>register|receive)$"

    name = "Firebase Cloud Messaging"
    icon = "icon-fcm"

    claim_blurb = _(
        "Add a %(link)s channel to send and receive messages. Your users will need an App to send and receive messages."
    ) % {"link": '<a href="https://firebase.google.com/docs/cloud-messaging/">Firebase Cloud Messaging</a>'}
    claim_view = ClaimView

    schemes = [URN.FCM_SCHEME]
    max_length = 10000
    attachment_support = False
    free_sending = True
    quick_reply_text_size = 36

    configuration_blurb = _(
        "To use your Firebase Cloud Messaging channel you'll have to POST to the following URLs with the "
        "parameters below."
    )

    configuration_urls = (
        dict(
            label=_("Contact Register"),
            url="https://{{ channel.callback_domain }}{% url 'courier.fcm' channel.uuid 'register' %}",
            description=_(
                "To register contacts, POST to the following URL with the parameters urn, "
                "fcm_token and optionally name."
            ),
        ),
        dict(
            label=_("Receive URL"),
            url="https://{{ channel.callback_domain }}{% url 'courier.fcm' channel.uuid 'receive' %}",
            description=_(
                "To handle incoming messages, POST to the following URL with the parameters from, msg and fcm_token."
            ),
        ),
    )
