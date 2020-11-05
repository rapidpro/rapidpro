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
        self.admin_auth_token = "abc123"
        self.admin_user_id = "123"
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
                RocketChatType.CONFIG_ADMIN_AUTH_TOKEN: self.admin_auth_token,
                RocketChatType.CONFIG_ADMIN_USER_ID: self.admin_user_id,
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
            Client(self.secure_url, self.secret).settings("http://temba.io/c/1234-5678", "test-bot")
        except ClientError:
            self.fail("The status 204 should not raise exceptions")

    def test_settings_fail(self):
        for status in range(200, 599):
            if status == 204:
                continue
            with patch("requests.put") as mock_request:
                mock_request.return_value = MockResponse(status, {})
                with self.assertRaises(ClientError, msg=f"The status{status} must be invalid"):
                    Client(self.secure_url, self.secret).settings("http://temba.io/c/1234-5678", "test-bot")

    @patch("requests.put")
    def test_settings_exceptions(self, mock_request):
        for err in [Timeout(), Exception()]:

            def side_effect(*arg, **kwargs):
                raise err

            mock_request.side_effect = side_effect
            with self.assertRaises(ClientError):
                Client(self.secure_url, self.secret).settings("http://temba.io/c/1234-5678", "test-bot")


class RocketChatViewTest(RocketChatMixin):
    def check_exceptions(self, mock_choices, mock_request, timeout_msg, exception_msg):
        mock_choices.side_effect = lambda letters: next(choices)

        self.client.force_login(self.admin)
        check = [(Timeout(), timeout_msg), (Exception(), exception_msg)]
        for err, msg in check:

            def side_effect(*args, **kwargs):
                raise err

            mock_request.side_effect = side_effect
            choices = (c for c in self.secret)
            data = {
                "secret": self.secret,
                "base_url": self.new_url("valid.com", path=f"/{self.app_id}"),
                "bot_username": self.bot_username,
                "admin_auth_token": self.admin_auth_token,
                "admin_user_id": self.admin_user_id,
            }

            response = self.client.post(self.claim_url, data=data)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(response.context["messages"]), 1)
            self.assertEqual([f"{m}" for m in response.context["messages"]][0], msg)

    def new_form_data(self, path=None, scheme=None) -> dict:
        if path or scheme:
            base_url = self.new_url("valid.com", path=path, scheme=scheme)
        else:
            base_url = self.secure_url

        return {
            "secret": self.secret,
            "base_url": base_url,
            "bot_username": self.bot_username,
            "admin_auth_token": self.admin_auth_token,
            "admin_user_id": self.admin_user_id,
        }

    @patch("socket.gethostbyname")
    @patch("random.choice")
    def submit_form(self, data, mock_choices, mock_socket):
        choices = (c for c in self.secret)
        mock_choices.side_effect = lambda letters: next(choices)

        self.client.force_login(self.admin)

        return self.client.post(self.claim_url, data=data)

    @patch("random.choice")
    def test_session_key(self, mock_choices):
        choices = (c for c in self.secret)
        mock_choices.side_effect = lambda letters: next(choices)
        self.client.force_login(self.admin)
        response = self.client.get(self.claim_url)
        self.assertEqual(response.wsgi_request.session.get(ClaimView.SESSION_KEY), self.secret)
        response.wsgi_request.session.pop(ClaimView.SESSION_KEY, None)

    @patch("random.choice")
    def test_form_initial(self, mock_choices):
        def configure():
            choices = (c for c in self.secret)
            mock_choices.side_effect = lambda letters: next(choices)

        configure()
        self.client.force_login(self.admin)
        response = self.client.get(self.claim_url)
        self.assertEqual(
            response.context_data["form"].initial.get("secret"), self.secret,
        )

        configure()
        with patch("temba.channels.types.rocketchat.views.ClaimView.derive_initial") as mock_initial:
            mock_initial.return_value = {"secret": self.secret2}
            response = self.client.get(self.claim_url)
        self.assertEqual(
            response.context_data["form"].initial.get("secret"), self.secret2,
        )

    @patch("temba.channels.types.rocketchat.client.Client.settings")
    def test_form_valid(self, mock_settings):
        max_length = Channel._meta.get_field("name").max_length
        for p in ["/{}", "/{}/", "/{}/path", "/path/{}/"]:
            path = p.format(self.app_id)
            data = self.new_form_data(path, "https")

            response = self.submit_form(data)

            self.assertEqual(response.status_code, 302)

            channel = Channel.objects.order_by("id").last()

            self.assertRedirect(response, reverse("channels.channel_read", args=[channel.uuid]))

            domain = data["base_url"].replace("http://", "").replace("https://", "").split("/")[0]
            expected = f"{RocketChatType.name}: {domain}"
            if len(expected) > max_length:
                expected = f"{expected[:max_length-3]}..."
            self.assertEqual(channel.name, expected)
            self.assertFalse(channel.config[RocketChatType.CONFIG_BASE_URL].endswith("/"))

    @patch("temba.channels.types.rocketchat.client.Client.settings")
    def test_form_invalid_base_url(self, mock_settings):
        def settings_effect(domain, channel):
            nonlocal _channel
            _channel = channel

        mock_settings.side_effect = settings_effect

        data = self.new_form_data()
        _channel: Channel = None

        response = self.submit_form(data)
        # retry with same base_url
        response = self.submit_form(data)
        self.assertFormError(response, "form", "base_url", "There is already a channel configured for this URL.")

        data.pop("base_url")
        response = self.submit_form(data)
        self.assertFormError(response, "form", "base_url", "This field is required.")

        data["base_url"] = ""
        response = self.submit_form(data)
        self.assertFormError(response, "form", "base_url", "This field is required.")

        data["base_url"] = "domain"
        response = self.submit_form(data)
        self.assertFormError(response, "form", "base_url", "Enter a valid URL.")

        data["base_url"] = "domain.com"
        response = self.submit_form(data)
        self.assertFormError(response, "form", "base_url", "Invalid URL http://domain.com")

    def test_form_invalid_secret(self):
        data = self.new_form_data()

        data.pop("secret")
        response = self.submit_form(data)
        self.assertFormError(response, "form", None, "Invalid secret code.")

        data["secret"] = ""
        response = self.submit_form(data)
        self.assertFormError(response, "form", None, "Invalid secret code.")

        data["secret"] = self.secret2
        response = self.submit_form(data)
        self.assertFormError(response, "form", None, "Secret code change detected.")

    def test_form_invalid_bot_username(self):
        data = self.new_form_data()

        data.pop("bot_username")
        response = self.submit_form(data)
        self.assertFormError(response, "form", "bot_username", "This field is required.")

        data["bot_username"] = ""
        response = self.submit_form(data)
        self.assertFormError(response, "form", "bot_username", "This field is required.")

    def test_form_invalid_admin_auth_token(self):
        data = self.new_form_data()

        data.pop("admin_auth_token")
        response = self.submit_form(data)
        self.assertFormError(response, "form", "admin_auth_token", "This field is required.")

        data["admin_auth_token"] = ""
        response = self.submit_form(data)
        self.assertFormError(response, "form", "admin_auth_token", "This field is required.")

    def test_form_invalid_admin_user_id(self):
        data = self.new_form_data()

        data.pop("admin_user_id")
        response = self.submit_form(data)
        self.assertFormError(response, "form", "admin_user_id", "This field is required.")

        data["admin_user_id"] = ""
        response = self.submit_form(data)
        self.assertFormError(response, "form", "admin_user_id", "This field is required.")

    @patch("socket.gethostbyname")
    @patch("random.choice")
    @patch("requests.put")
    def test_settings_exception(self, mock_request, mock_choices, mock_socket):
        mock_socket.return_value = "192.168.123.45"  # Fake IP
        self.check_exceptions(
            mock_choices,
            mock_request,
            "Unable to configure. Connection to RocketChat is taking too long.",
            "Configuration has failed",
        )
