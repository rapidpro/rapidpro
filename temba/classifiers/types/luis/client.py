from urllib.parse import urlencode

import requests

from django.utils import timezone


class AuthoringClient:
    """
    LUIS Authoring REST API v2
    """

    AUTH_HEADER = "Ocp-Apim-Subscription-Key"

    def __init__(self, endpoint: str, key: str):
        self.endpoint = endpoint
        self.key = key
        self.logs = []

    def get_app(self, app_id: str) -> dict:
        """
        https://westus.dev.cognitive.microsoft.com/docs/services/5890b47c39e2bb17b84a55ff/operations/5890b47c39e2bb052c5b9c37
        """
        return self._request(f"apps/{app_id}")

    def get_version_intents(self, app_id: str, version: str) -> list:
        """
        https://westus.dev.cognitive.microsoft.com/docs/services/5890b47c39e2bb17b84a55ff/operations/5890b47c39e2bb052c5b9c0d
        """
        return self._request(f"apps/{app_id}/versions/{version}/intents")

    def _request(self, path: str):
        url = f"{self.endpoint}luis/api/v2.0/{path}"

        start = timezone.now()
        response = requests.get(f"{self.endpoint}luis/api/v2.0/{path}", headers={self.AUTH_HEADER: self.key})
        elapsed = (timezone.now() - start).total_seconds() * 1000

        self.logs.append({"url": url, "response": response, "elapsed": elapsed})

        response.raise_for_status()
        return response.json()


class PredictionClient:
    """
    LUIS Prediction REST API v3
    """

    AUTH_HEADER = "Ocp-Apim-Subscription-Key"

    def __init__(self, endpoint: str, key: str):
        self.endpoint = endpoint
        self.key = key

    def predict(self, app_id: str, slot: str, query: str) -> dict:
        """
        https://westcentralus.dev.cognitive.microsoft.com/docs/services/luis-endpoint-api-v3-0/operations/5cb0a91e54c9db63d589f433
        """
        return self._request(f"apps/{app_id}/slots/{slot}/predict", {"query": query})

    def _request(self, path: str, params: dict):
        params["subscription-key"] = self.key
        response = requests.get(f"{self.endpoint}luis/prediction/v3.0/{path}?{urlencode(params)}")
        response.raise_for_status()
        return response.json()
