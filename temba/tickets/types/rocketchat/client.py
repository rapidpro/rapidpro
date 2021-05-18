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
        kwargs["Authorization"] = f"Token {self.secret}"
        kwargs["Content-Type"] = f"application/json"
        return kwargs

    def _request(self, method, url, **kwargs):
        kwargs["headers"] = self.headers(**(kwargs.get("headers") or {}))
        kwargs.setdefault("timeout", 30)
        try:
            return getattr(requests, method)(url, **kwargs)
        except Timeout as err:
            raise ClientError(_("Connection to RocketChat is taking too long.")) from err
        except Exception as err:
            raise ClientError() from err

    def put(self, url, **kwargs):
        return self._request("put", url, **kwargs)

    def settings(self, webhook_url: str):
        response = self.put(f"{self.base_url}/settings", data=json.dumps({"webhook": {"url": webhook_url}}),)
        if response.status_code != 204:
            raise ClientError(response=response)
