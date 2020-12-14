import requests


class ClientError(Exception):
    def __init__(self, response):
        self.response = response


class Client:
    def __init__(self, subdomain):
        self.subdomain = subdomain

    def get_oauth_token(self, client_id, client_secret, code, redirect_uri):
        response = requests.post(
            f"https://{self.subdomain}.zendesk.com/oauth/tokens",
            json={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "scope": "read write",
            },
        )
        if response.status_code != 200:
            raise ClientError(response)

        return response.json().get("access_token")
