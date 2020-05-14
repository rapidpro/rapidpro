from unittest.mock import patch

from django.test.utils import override_settings
from django.urls import reverse

from temba.tests import MockResponse, TembaTest

from ...models import Ticketer
from .client import Client, ClientError
from .type import ZendeskType


class ClientTest(TembaTest):
    def test_get_oauth_token(self):
        client = Client("acme")

        with patch("requests.post") as mock_post:
            mock_post.return_value = MockResponse(200, '{"access_token": "987654321"}')

            token = client.get_oauth_token("123-abc", "sesame", "mycode", "http://backhere.com")

            self.assertEqual("987654321", token)
            mock_post.assert_called_once_with(
                "https://acme.zendesk.com/oauth/tokens",
                json={
                    "grant_type": "authorization_code",
                    "code": "mycode",
                    "client_id": "123-abc",
                    "client_secret": "sesame",
                    "redirect_uri": "http://backhere.com",
                    "scope": "read write",
                },
            )

            mock_post.return_value = MockResponse(400, "problem")

            with self.assertRaises(ClientError):
                client.get_oauth_token("123-abc", "sesame", "mycode", "http://backhere.com")


class ZendeskTypeTest(TembaTest):
    def test_is_available(self):
        with override_settings(ZENDESK_CLIENT_ID="", ZENDESK_CLIENT_SECRET=""):
            self.assertFalse(ZendeskType().is_available())
        with override_settings(ZENDESK_CLIENT_ID="1234567", ZENDESK_CLIENT_SECRET=""):
            self.assertFalse(ZendeskType().is_available())
        with override_settings(ZENDESK_CLIENT_ID="1234567", ZENDESK_CLIENT_SECRET="sesame"):
            self.assertTrue(ZendeskType().is_available())

    @override_settings(ZENDESK_CLIENT_ID="1234567", ZENDESK_CLIENT_SECRET="sesame")
    @patch("temba.tickets.types.zendesk.views.random_string")
    def test_connect(self, mock_random_string):
        mock_random_string.return_value = "RAND346"

        connect_url = reverse("tickets.types.zendesk.connect")

        response = self.client.get(connect_url)
        self.assertLoginRedirect(response)

        self.login(self.admin)

        response = self.client.get(connect_url)
        self.assertEqual(["subdomain", "loc"], list(response.context["form"].fields.keys()))

        # will fail as we don't have anything filled out
        response = self.client.post(connect_url, {})
        self.assertFormError(response, "form", "subdomain", ["This field is required."])

        # try with invalid subdomain
        response = self.client.post(connect_url, {"subdomain": "%x.&y"})
        self.assertFormError(response, "form", "subdomain", ["Not a valid subdomain name."])

        # try with subdomain already taken by this org
        Ticketer.create(
            self.org, self.admin, ticketer_type=ZendeskType.slug, name="Existing", config={"subdomain": "chispa"}
        )
        response = self.client.post(connect_url, {"subdomain": "chispa"})
        self.assertFormError(
            response, "form", "subdomain", ["There is already a ticketing service configured for this subdomain."]
        )

        # submitting with valid subdomain redirects us to Zendesk
        response = self.client.post(connect_url, {"subdomain": "acme"}, follow=False)
        self.assertIn("https://acme.zendesk.com/oauth/authorizations/new?response_type=code", response.url)

        # if user doesn't authenticate, Zendesk returns to us with an error, which we display to the user
        response = self.client.get(connect_url + "?error=auth&error_description=thing%20went%20wrong")
        self.assertContains(response, "thing went wrong")

        # if user does authenticate, zendesk gives us an authorization code which we use to request a token
        with patch("temba.tickets.types.zendesk.client.Client.get_oauth_token") as mock_get_oauth_token:
            # request to get token could fail
            mock_get_oauth_token.side_effect = ClientError("boom")

            response = self.client.get(connect_url + "?code=please&state=temba")
            self.assertContains(response, "Unable to request OAuth token.")

            # but if it succeeds...
            mock_get_oauth_token.side_effect = None
            mock_get_oauth_token.return_value = "236272"

            # ticketer will be created and user should be redirected to the configure page
            response = self.client.get(connect_url + "?code=please&state=temba")

            ticketer = Ticketer.objects.filter(ticketer_type="zendesk", is_active=True).order_by("id").last()
            self.assertEqual("Zendesk (temba)", ticketer.name)
            self.assertEqual({"oauth_token": "236272", "secret": "RAND346", "subdomain": "temba"}, ticketer.config)
            self.assertRedirect(response, reverse("tickets.types.zendesk.configure", args=[ticketer.uuid]))

    @override_settings(ZENDESK_APP_ID="temba")
    def test_configure(self):
        ticketer = Ticketer.create(
            self.org,
            self.admin,
            ticketer_type=ZendeskType.slug,
            name="Existing",
            config={"oauth_token": "236272", "secret": "SECRET346", "subdomain": "chispa"},
        )

        configure_url = reverse("tickets.types.zendesk.configure", args=[ticketer.uuid])

        response = self.client.get(configure_url)
        self.assertLoginRedirect(response)

        self.login(self.admin)

        response = self.client.get(configure_url)
        self.assertContains(response, "SECRET346")
        self.assertContains(response, "https://www.zendesk.com/apps/directory")
