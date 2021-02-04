import requests
from requests.exceptions import Timeout

from django.utils.translation import gettext_lazy as _

from temba.utils import json


class ClientError(Exception):
    def __init__(self, msg=None, response=None):
        super().__init__(*((msg,) or ()))
        self.msg = msg
        self.response = response


class Client:
    def __init__(self, base_url: str, secret: str):
        self.base_url = base_url.rstrip("/")
        self.secret = secret

    def headers(self, **kwargs):
        return {
            "Content-Type": "application/json",
            "Authorization": f"Token {self.secret}",
        }

    def _request(self, method, url, timeout_msg=None, **kwargs):
        kwargs["headers"] = self.headers()
        kwargs.setdefault("timeout", 30)
        try:
            return getattr(requests, method)(url, **kwargs)
        except Timeout as err:
            raise ClientError(timeout_msg or _("Connection to RocketChat is taking too long.")) from err
        except Exception as err:
            raise ClientError() from err

    def put(self, url, timeout_msg=None, **kwargs):
        return self._request("put", url, timeout_msg, **kwargs)

    def settings(self, webhook_url: str, bot_username: str):
        payload = {
            "webhook": {"url": webhook_url},
            "bot": {"username": bot_username},
        }

        response = self.put(
            f"{self.base_url}/settings",
            _("Unable to configure. Connection to RocketChat is taking too long."),
            data=json.dumps(payload),
        )
        if response.status_code != 204:
            raise ClientError(response=response)
