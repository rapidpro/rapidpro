from unittest.mock import patch

from django.test.utils import override_settings
from django.urls import reverse

from temba.tests import MockResponse, TembaTest
from temba.utils import json

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
        with override_settings(ZENDESK_CLIENT_ID="temba", ZENDESK_CLIENT_SECRET=""):
            self.assertFalse(ZendeskType().is_available())
        with override_settings(ZENDESK_CLIENT_ID="temba", ZENDESK_CLIENT_SECRET="sesame"):
            self.assertTrue(ZendeskType().is_available())

    @override_settings(ZENDESK_CLIENT_ID="temba", ZENDESK_CLIENT_SECRET="sesame")
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

    @override_settings(ZENDESK_CLIENT_ID="temba")
    def test_manifest_view(self):
        response = self.client.get(reverse("tickets.types.zendesk.manifest"))

        self.assertEqual(
            {
                "name": "RapidPro",
                "id": "app.rapidpro.io",
                "author": "Nyaruka",
                "version": "v0.0.1",
                "channelback_files": False,
                "push_client_id": "temba",
                "urls": {
                    "admin_ui": f"https://app.rapidpro.io/tickets/types/zendesk/admin_ui",
                    "channelback_url": f"https://app.rapidpro.io/mr/tickets/types/zendesk/channelback",
                    "event_callback_url": f"https://app.rapidpro.io/mr/tickets/types/zendesk/event_callback",
                },
            },
            response.json(),
        )

    def test_admin_ui_view(self):
        admin_url = reverse("tickets.types.zendesk.admin_ui")

        ticketer = Ticketer.create(
            self.org,
            self.admin,
            ticketer_type=ZendeskType.slug,
            name="Existing",
            config={"oauth_token": "236272", "secret": "SECRET346", "subdomain": "example"},
        )

        # this view can only be POST'ed to
        response = self.client.get(admin_url)
        self.assertEqual(405, response.status_code)

        # simulate initial POST from Zendesk
        response = self.client.post(
            admin_url,
            {
                "name": "",
                "subdomain": "example",
                "metadata": "",
                "state": "",
                "return_url": "https://example.zendesk.com",
                "locale": "en-US",
                "instance_push_id": "push1234",
                "zendesk_access_token": "sesame",
            },
            HTTP_REFERER="https://example.zendesk.com/channels",
        )

        self.assertEqual(200, response.status_code)
        self.assertContains(response, "This will connect your account to Zendesk")
        self.assertNotContains(response, "This field is required.")

        self.assertEqual(
            ["name", "secret", "return_url", "subdomain", "locale", "instance_push_id", "zendesk_access_token", "loc"],
            list(response.context["form"].fields.keys()),
        )

        # try submitting with blank values for the two visible fields
        response = self.client.post(
            admin_url,
            {
                "name": "",
                "secret": "",
                "return_url": "https://example.zendesk.com",
                "subdomain": "example",
                "locale": "en-US",
                "instance_push_id": "push1234",
                "zendesk_access_token": "sesame",
            },
        )
        self.assertFormError(response, "form", "name", "This field is required.")
        self.assertFormError(response, "form", "secret", "This field is required.")

        # try submitting with incorrect secret
        response = self.client.post(
            admin_url,
            {
                "name": "My Channel",
                "secret": "CHEF",
                "return_url": "https://example.zendesk.com",
                "subdomain": "example",
                "locale": "en-US",
                "instance_push_id": "push1234",
                "zendesk_access_token": "sesame",
            },
        )
        self.assertFormError(response, "form", "secret", "Secret is incorrect.")

        # try submitting with correct secret
        response = self.client.post(
            admin_url,
            {
                "name": "My Channel",
                "secret": "SECRET346",
                "return_url": "https://example.zendesk.com",
                "subdomain": "example",
                "locale": "en-US",
                "instance_push_id": "push1234",
                "zendesk_access_token": "sesame",
            },
        )

        # ticketer config should be updated with push credentials
        ticketer.refresh_from_db()
        self.assertEqual(
            {
                "oauth_token": "236272",
                "secret": "SECRET346",
                "subdomain": "example",
                "push_id": "push1234",
                "push_token": "sesame",
            },
            ticketer.config,
        )

        # should use the special return template to POST back to Zendesk
        self.assertEqual(200, response.status_code)
        self.assertEqual("My Channel", response.context["name"])
        self.assertEqual(
            {"ticketer": str(ticketer.uuid), "secret": "SECRET346"}, json.loads(response.context["metadata"])
        )
