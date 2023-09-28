from unittest.mock import patch

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
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
                "channelback_files": True,
                "push_client_id": "temba",
                "urls": {
                    "admin_ui": "https://app.rapidpro.io/tickets/types/zendesk/admin_ui",
                    "channelback_url": "https://app.rapidpro.io/mr/tickets/types/zendesk/channelback",
                    "event_callback_url": "https://app.rapidpro.io/mr/tickets/types/zendesk/event_callback",
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

    def test_file_view(self):
        # save a text file as an attachment to storage
        path = f"attachments/{self.org.id}/01c1/1aa4/01c11aa4.txt"
        if not default_storage.exists(path):
            default_storage.save(path, ContentFile(b"HELLO"))

        file_url = reverse("tickets.types.zendesk.file_callback", args=[f"{self.org.id}/01c1/1aa4/01c11aa4.txt"])
        response = self.client.post(file_url)

        self.assertEqual(200, response.status_code)
        self.assertEqual(b"HELLO", b"".join(response.streaming_content))
