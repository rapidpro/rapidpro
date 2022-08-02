import requests


class DTOneClient:
    """
    Barebones client for DTOne
    """

    class Exception(BaseException):
        def __init__(self, errors):
            self.errors = errors

        def __str__(self):
            return ",".join([e["message"] for e in self.errors])

    API_URL = "https://dvs-api.dtone.com/v1/"

    def __init__(self, key, secret):
        self.key = key
        self.secret = secret

    def get_balances(self):
        """
        See https://dvs-api-doc.dtone.com/#tag/Balances
        """
        return self._get("balances")

    def _get(self, endpoint: str):
        response = requests.get(self.API_URL + endpoint, auth=(self.key, self.secret))
        if response.status_code // 100 != 2:
            raise DTOneClient.Exception(response.json()["errors"])

        return response.json()
