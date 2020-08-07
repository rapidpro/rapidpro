import requests
from django.utils.translation import gettext_lazy as _
from requests.exceptions import Timeout

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

    def _request(self, method, url, timeout_msg=None, **kwargs):
        kwargs["headers"] = self.headers(**(kwargs.get("headers") or {}))
        kwargs.setdefault("timeout", 30)
        try:
            return getattr(requests, method)(url, **kwargs)
        except Timeout as err:
            raise ClientError(timeout_msg or _("Connection to RocketChat is taking too long.")) from err
        except Exception as err:
            raise ClientError() from err

    def put(self, url, timeout_msg=None, **kwargs):
        return self._request("put", url, timeout_msg, **kwargs)

    def settings(self, domain, ticketer):
        from .type import RocketChatType

        response = self.put(
            f"{self.base_url}/settings",
            _("Unable to configure. Connection to RocketChat is taking too long."),
            data=json.dumps({"webhook": {"url": RocketChatType.callback_url(ticketer, domain)}}),
        )
        if response.status_code != 204:
            raise ClientError(response=response)
