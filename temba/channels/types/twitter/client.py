from urllib.parse import quote_plus

from twython import Twython


class TwitterClient(Twython):
    def get_webhooks(self, env_name):
        """
        Returns the webhooks currently active for this app. (Twitter claims there can only be one)
        Docs: https://developer.twitter.com/en/docs/accounts-and-users/subscribe-account-activity/api-reference/aaa-standard-all
        """
        return self.get("https://api.twitter.com/1.1/account_activity/all/%s/webhooks.json" % env_name)

    def delete_webhook(self, env_name, webhook_id):
        """
        Deletes the webhook for the current app / user and passed in environment name.
        Docs: https://developer.twitter.com/en/docs/accounts-and-users/subscribe-account-activity/api-reference/aaa-standard-all
        """
        self.request(
            "https://api.twitter.com/1.1/account_activity/all/%s/webhooks/%s.json" % (env_name, webhook_id),
            method="DELETE",
        )

    def register_webhook(self, env_name, url):
        """
        Registers a new webhook URL for the given application context.
        Docs: https://developer.twitter.com/en/docs/accounts-and-users/subscribe-account-activity/api-reference/aaa-standard-all
        """
        set_webhook_url = "https://api.twitter.com/1.1/account_activity/all/%s/webhooks.json?url=%s" % (
            env_name,
            quote_plus(url),
        )
        return self.post(set_webhook_url)

    def subscribe_to_webhook(self, env_name):
        """
        Subscribes all user's events for this apps webhook
        Docs: https://developer.twitter.com/en/docs/accounts-and-users/subscribe-account-activity/api-reference/aaa-standard-all
        """
        return self.post("https://api.twitter.com/1.1/account_activity/all/%s/subscriptions.json" % env_name)
