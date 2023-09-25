import requests

from django.utils.translation import gettext_lazy as _

from temba.contacts.models import URN
from temba.triggers.models import Trigger

from ...models import Channel, ChannelType, ConfigUI
from .views import ClaimView


class FacebookLegacyType(ChannelType):
    """
    A Facebook channel
    """

    code = "FB"
    name = "Facebook"
    category = ChannelType.Category.SOCIAL_MEDIA

    courier_url = r"^fb/(?P<uuid>[a-z0-9\-]+)/receive"
    schemes = [URN.FACEBOOK_SCHEME]

    claim_blurb = _(
        """Add a <a target="_blank" href="http://facebook.com">Facebook</a> bot to send and receive messages on behalf """
        """of one of your Facebook pages for free. You will need to create a Facebook application on their """
        """<a target="_blank" href="http://developers.facebook.com">developers</a> site first."""
    )
    claim_view = ClaimView

    config_ui = ConfigUI()  # has own template

    def deactivate(self, channel):
        config = channel.config
        requests.delete(
            "https://graph.facebook.com/v3.3/me/subscribed_apps",
            params={"access_token": config[Channel.CONFIG_AUTH_TOKEN]},
        )

    def activate_trigger(self, trigger):
        # if this is new conversation trigger, register for the FB callback
        if trigger.trigger_type == Trigger.TYPE_NEW_CONVERSATION:
            self._set_call_to_action(trigger.channel, "get_started")

    def deactivate_trigger(self, trigger):
        # for any new conversation triggers, clear out the call to action payload
        if trigger.trigger_type == Trigger.TYPE_NEW_CONVERSATION:
            self._set_call_to_action(trigger.channel, None)

    def is_available_to(self, org, user):
        return False, False

    @staticmethod
    def _set_call_to_action(channel, payload):
        # register for get_started events
        url = "https://graph.facebook.com/v3.3/%s/thread_settings" % channel.address
        body = {"setting_type": "call_to_actions", "thread_state": "new_thread", "call_to_actions": []}

        # if we have a payload, set it, otherwise, clear it
        if payload:
            body["call_to_actions"].append({"payload": payload})

        access_token = channel.config[Channel.CONFIG_AUTH_TOKEN]

        response = requests.post(
            url, json=body, params={"access_token": access_token}, headers={"Content-Type": "application/json"}
        )

        if payload and response.status_code != 200:  # pragma: no cover
            raise Exception("Unable to update call to action: %s" % response.text)
