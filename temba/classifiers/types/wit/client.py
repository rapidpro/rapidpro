from typing import List, Tuple

import requests


class Client:
    """
    Barebones API client for wit.ai
    """

    base_url = "https://api.wit.ai/"
    api_version = "20200513"

    def __init__(self, access_token: str):
        self.access_token = access_token

    def get_intents(self) -> Tuple[List, requests.Response]:
        return self._request("intents")

    def _request(self, endpoint: str):
        response = requests.get(
            f"{self.base_url}{endpoint}?v={self.api_version}", headers={"Authorization": f"Bearer {self.access_token}"}
        )

        response.raise_for_status()
        return response.json(), response
