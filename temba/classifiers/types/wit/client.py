import requests


class Client:
    """
    Barebones API client for wit.ai
    """

    base_url = "https://api.wit.ai/"
    api_version = "20200513"

    def __init__(self, access_token: str):
        self.access_token = access_token

    def get_intents(self):
        return self._request("intents")

    def _request(self, endpoint: str):
        return requests.get(
            f"{self.base_url}{endpoint}?v={self.api_version}", headers={"Authorization": f"Bearer {self.access_token}"}
        )
