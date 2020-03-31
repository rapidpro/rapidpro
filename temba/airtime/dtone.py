import hashlib
import time

import requests

from django.utils.encoding import force_bytes, force_text


class DTOneClient:
    API_URL = "https://airtime-api.dtone.com/cgi-bin/shop/topup"

    def __init__(self, login, token):
        self.login = login
        self.token = token

    def ping(self):
        return self._request(action="ping")

    def check_wallet(self):
        return self._request(action="check_wallet")

    def _request(self, **kwargs):
        key = self._request_key()
        md5 = hashlib.md5()
        md5.update(force_bytes(self.login + self.token + key))
        md5 = md5.hexdigest()

        response = requests.post(self.API_URL, {"login": self.login, "key": key, "md5": md5, **kwargs})

        lines = force_text(response.content).split("\r\n")
        parsed = {}

        for elt in lines:
            if elt and elt.find("=") > 0:
                key, val = tuple(elt.split("="))
                parsed[key] = val

        return parsed

    def _request_key(self):
        """
        Every request needs a unique, sequential key value
        """
        return str(int(time.time() * 1000))
