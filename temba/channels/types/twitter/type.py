import logging

from django.forms import ValidationError
from django.urls import reverse
from django.utils.translation import ugettext_lazy as _

from temba.contacts.models import URN

from ...models import ChannelType
from .client import TwitterClient
from .views import ClaimView, UpdateForm

logger = logging.getLogger(__name__)


class TwitterType(ChannelType):
    """
    A Twitter channel which uses Twitter's Account Activity API to send and receive direct messages.
    """

    code = "TWT"
    category = ChannelType.Category.SOCIAL_MEDIA

    courier_url = r"^twt/(?P<uuid>[a-z0-9\-]+)/receive$"

    name = "Twitter"
    icon = "icon-twitter"

    claim_blurb = _(
        "Send and receive messages on Twitter using their %(link)s API. You will have to apply for Twitter API access "
        "and create a Twitter application."
    ) % {
        "link": '<a href="https://developer.twitter.com/en/docs/accounts-and-users/subscribe-account-activity/overview">Twitter Activity</a>'
    }
    claim_view = ClaimView
    update_form = UpdateForm

    schemes = [URN.TWITTER_SCHEME, URN.TWITTERID_SCHEME]
    show_config_page = False
    free_sending = True
    async_activation = False
    attachment_support = True

    redact_response_keys = {"urn"}
    redact_request_keys = {"sender_id", "name", "screen_name", "profile_image_url", "profile_image_url_https"}

    def activate(self, channel):
        config = channel.config
        client = TwitterClient(
            config["api_key"], config["api_secret"], config["access_token"], config["access_token_secret"]
        )

        callback_url = "https://%s%s" % (channel.callback_domain, reverse("courier.twt", args=[channel.uuid]))
        try:
            # check for existing hooks, if there is just one, remove it
            hooks = client.get_webhooks(config["env_name"])
            if len(hooks) == 1:
                client.delete_webhook(config["env_name"], hooks[0]["id"])

            resp = client.register_webhook(config["env_name"], callback_url)
            channel.config["webhook_id"] = resp["id"]
            channel.save(update_fields=["config"])
            client.subscribe_to_webhook(config["env_name"])
        except Exception as e:  # pragma: no cover
            logger.error(f"Unable to activate TwitterActivity: {str(e)}", exc_info=True)
            raise ValidationError(e)

    def deactivate(self, channel):
        config = channel.config
        if "webhook_id" in config:
            client = TwitterClient(
                config["api_key"], config["api_secret"], config["access_token"], config["access_token_secret"]
            )
            client.delete_webhook(config["env_name"], config["webhook_id"])
