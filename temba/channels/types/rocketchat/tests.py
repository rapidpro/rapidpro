import re
import uuid
from secrets import token_urlsafe
from unittest.mock import patch

from requests.exceptions import Timeout

from django.urls import reverse

from temba.channels.models import Channel
from temba.tests import MockResponse, TembaTest
from temba.utils.text import random_string

from .client import Client, ClientError
from .type import RocketChatType
from .views import SECRET_LENGTH, ClaimView


class RocketChatMixin(TembaTest):
    def setUp(self):
        super().setUp()
        self.claim_url = reverse("channels.types.rocketchat.claim")
        self.app_id = f"{uuid.uuid4()}"
        self.bot_username = "test-bot"
        self.secret = random_string(SECRET_LENGTH)
        self.secret2 = random_string(SECRET_LENGTH)

        self.domain = self.new_url("rocketchat-domain.com", scheme="")
        self.insecure_url = self.new_url(self.domain, path=f"/{self.app_id}", unique=False)
        self.secure_url = self.new_url(self.domain, path=f"/{self.app_id}", scheme="https", unique=False)

    @staticmethod
    def new_url(domain, path=None, scheme="http", unique=True):
        url = f"{domain}{path or ''}"
        if unique:
            subdomain = re.sub(r"[^a-zA-Z0-9]", "", token_urlsafe(10))
            url = f"{subdomain}.{url}"
        if scheme:
            return f"{scheme}://{url}".lower()
        return url.lower()

    def new_channel(self, config=None) -> Channel:
        if not config:
            config = {
                RocketChatType.CONFIG_BASE_URL: self.new_url(self.domain),
                RocketChatType.CONFIG_BOT_USERNAME: self.bot_username,
                RocketChatType.CONFIG_SECRET: self.secret,
            }
        return Channel.create(
            org=self.org,
            user=self.user,
            country=None,
            channel_type=RocketChatType.code,
            name="Name",
            config=config or {},
        )


class ClientTest(RocketChatMixin):
    @patch("requests.put")
    def test_settings_success(self, mock_request):
        mock_request.return_value = MockResponse(204, {})
        try:
            Client(self.secure_url, self.bot_username, self.secret).settings(self.domain, self.new_channel())
        except ClientError:
            self.fail("The status 204 should not raise exceptions")

    def test_settings_fail(self):
        channel = self.new_channel()
        for status in range(200, 599):
            if status == 204:
                continue
            with patch("requests.put") as mock_request:
                mock_request.return_value = MockResponse(status, {})
                with self.assertRaises(ClientError, msg=f"The status{status} must be invalid"):
                    Client(self.secure_url, self.bot_username, self.secret).settings(self.domain, channel)

    @patch("requests.put")
    def test_settings_exceptions(self, mock_request):
        for err in [Timeout(), Exception()]:

            def side_effect(*arg, **kwargs):
                raise err

            mock_request.side_effect = side_effect
            channel = self.new_channel()
            with self.assertRaises(ClientError):
                Client(self.secure_url, self.bot_username, self.secret).settings(self.domain, channel)


class RocketChatTypeTest(RocketChatMixin):
    @patch("temba.orgs.models.Org.get_brand_domain")
    def test_callback_url(self, mock_brand_domain):
        channel = self.new_channel()
        domains = [("https://", "test.domain.com"), ("", "http://test.domain.com"), ("", "https://test.domain.com")]
        for scheme, domain in domains:
            mock_brand_domain.return_value = domain
            self.assertEqual(
                RocketChatType.callback_url(channel), f"{scheme}{domain}{reverse('courier.rc', args=[channel.uuid])}"
            )

    @patch("temba.orgs.models.Org.get_brand_domain")
    def test_calllback_url_exception(self, mock_brand_domain):
        mock_brand_domain.return_value = ""
        channel = self.new_channel()
        with self.assertRaises(ValueError):
            RocketChatType.callback_url(channel)