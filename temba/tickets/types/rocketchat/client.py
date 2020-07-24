import requests
from django.utils.translation import gettext_lazy as _
from requests.exceptions import Timeout


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

    def get(self, url, timeout_msg=None, **kwargs):
        return self._request("get", url, timeout_msg, **kwargs)

    def post(self, url, timeout_msg=None, **kwargs):
        return self._request("post", url, timeout_msg, **kwargs)

    def secret_check(self):
        response = self.get(
            f"{self.base_url}/secret.check",
            _("Unable to validate the secret code. Connection to RocketChat is taking too long.")
        )
        if response.status_code != 200:
            raise ClientError(response=response)

    def settings(self, domain, ticketer):
        from .type import RocketChatType

        response = self.post(
            f"{self.base_url}/settings",
            _("Unable to configure. Connection to RocketChat is taking too long."),
            data={"webhook": {"url": RocketChatType.callback_url(ticketer, domain)}}
        )
        if response.status_code != 204:
            raise ClientError(response=response)
