import time

import nexmo

from django.urls import reverse


class NexmoClient:
    """
    Wrapper for the actual Nexmo client that adds some functionality and retries
    """

    RATE_LIMIT_PAUSE = 2

    def __init__(self, api_key, api_secret):
        self.base = nexmo.Client(api_key, api_secret)

    def check_credentials(self):
        try:
            self.base.get_balance()
            return True
        except nexmo.AuthenticationError:
            return False

    def get_numbers(self, pattern=None, size=10):
        params = {"size": size}
        if pattern:
            params["pattern"] = str(pattern).strip("+")

        response = self._with_retry("get_account_numbers", params=params)

        return response["numbers"] if int(response.get("count", 0)) else []

    def search_numbers(self, country, pattern):

        response = self._with_retry(
            "get_available_numbers",
            country_code=country,
            pattern=pattern,
            search_pattern=1,
            features="SMS",
            country=country,
        )

        numbers = []
        if int(response.get("count", 0)):
            numbers += response["numbers"]

        response = self._with_retry(
            "get_available_numbers",
            country_code=country,
            pattern=pattern,
            search_pattern=1,
            features="VOICE",
            country=country,
        )

        if int(response.get("count", 0)):
            numbers += response["numbers"]

        return numbers

    def buy_number(self, country, number):
        params = dict(msisdn=number.lstrip("+"), country=country)

        self._with_retry("buy_number", params=params)

    def update_number(self, country, number, mo_url, app_id):
        number = number.lstrip("+")
        params = dict(moHttpUrl=mo_url, msisdn=number, country=country)

        if app_id:
            params["app_id"] = app_id
            params["voiceCallbackType"] = "tel"
            params["voiceCallbackValue"] = number

        self._with_retry("update_number", params=params)

    def create_application(self, domain, channel_uuid):
        name = "%s/%s" % (domain, channel_uuid)
        answer_url = reverse("mailroom.ivr_handler", args=[channel_uuid, "incoming"])
        event_url = reverse("mailroom.ivr_handler", args=[channel_uuid, "status"])

        response = self._with_retry(
            "create_application",
            params={
                "name": name,
                "type": "voice",
                "answer_url": f"https://{domain}{answer_url}",
                "answer_method": "POST",
                "event_url": f"https://{domain}{event_url}",
                "event_method": "POST",
            },
        )

        app_id = response.get("id")
        app_private_key = response.get("keys", {}).get("private_key")
        return app_id, app_private_key

    def delete_application(self, app_id):
        try:
            self._with_retry("delete_application", application_id=app_id)
        except nexmo.ClientError:
            # possible application no longer exists
            pass

    def _with_retry(self, action, **kwargs):
        """
        Utility to perform something using the Nexmo API, and if it errors with a rate-limit response, try again
        after a small delay.
        """
        func = getattr(self.base, action)

        try:
            return func(**kwargs)
        except nexmo.ClientError as e:
            message = str(e)
            if message.startswith("420") or message.startswith("429"):
                time.sleep(NexmoClient.RATE_LIMIT_PAUSE)
                return func(**kwargs)
            else:  # pragma: no cover
                raise e
