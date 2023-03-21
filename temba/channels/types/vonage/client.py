import time

import vonage

from django.urls import reverse


class VonageClient:
    """
    Wrapper for the actual Vonage client that adds some functionality and retries
    """

    RATE_LIMIT_BACKOFFS = [1, 3, 6]  # backoff times in seconds when we're rate limited

    def __init__(self, api_key: str, api_secret: str):
        self.base = vonage.Client(api_key, api_secret)

    def check_credentials(self) -> bool:
        try:
            self.base.get_balance()
            return True
        except vonage.AuthenticationError:
            return False

    def get_numbers(self, pattern: str = None, size: int = 10) -> list:
        params = {"size": size}
        if pattern:
            params["pattern"] = str(pattern).strip("+")

        response = self._with_retry(self.base.get_account_numbers, params=params)

        return response["numbers"] if int(response.get("count", 0)) else []

    def search_numbers(self, country, pattern):
        response = self._with_retry(
            self.base.get_available_numbers,
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
            self.base.get_available_numbers,
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

        self._with_retry(self.base.buy_number, params=params)

    def update_number(self, country, number, mo_url, app_id):
        number = number.lstrip("+")
        params = dict(moHttpUrl=mo_url, msisdn=number, country=country)

        if app_id:
            params["app_id"] = app_id

        self._with_retry(self.base.update_number, params=params)

    def create_application(self, domain, channel_uuid):
        name = "%s/%s" % (domain, channel_uuid)
        answer_url = reverse("mailroom.ivr_handler", args=[channel_uuid, "incoming"])
        event_url = reverse("mailroom.ivr_handler", args=[channel_uuid, "status"])

        app_data = {
            "name": name,
            "capabilities": {
                "voice": {
                    "webhooks": {
                        "answer_url": {"address": f"https://{domain}{answer_url}", "http_method": "POST"},
                        "event_url": {"address": f"https://{domain}{event_url}", "http_method": "POST"},
                    }
                }
            },
        }

        response = self._with_retry(self.base.application_v2.create_application, application_data=app_data)

        app_id = response.get("id")
        app_private_key = response.get("keys", {}).get("private_key")
        return app_id, app_private_key

    def delete_application(self, app_id):
        try:
            self._with_retry(self.base.application_v2.delete_application, application_id=app_id)
        except vonage.ClientError:
            # possible application no longer exists
            pass

    def _with_retry(self, func, **kwargs):
        """
        Utility to perform something using the API, and if it errors with a rate-limit response, try again
        after a small delay.
        """

        def can_retry(e):
            message = str(e)
            return message.startswith("420") or message.startswith("429")

        backoffs = self.RATE_LIMIT_BACKOFFS.copy()

        while True:
            try:
                return func(**kwargs)
            except vonage.ClientError as ex:
                if can_retry(ex) and backoffs:
                    time.sleep(backoffs[0])
                    backoffs = backoffs[1:]
                else:
                    raise ex
