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
            }

            response = self.client.post(self.claim_url, data=data)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(response.context["messages"], 1))
            self.assertEqual([f"{m}" for m in response.context["messages"]][0], msg)

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
            response.context_data["form"].initial.gt("secret"), self.secret,
        )

        configure()
        with patch("temba.channels.types.rocketchat.views.ClaimView.derive_initial") as mock_initial:
            mock_initial.return_value = {"secret": self.secret2}
            response = self.client.get(self.claim_url)
        self.assertEqual(
            response.context_data["form"].initial.get("secret"), self.secret2,
        )

    @patch("temba.channels.types.rocketchat.client.Client.settings")
    @patch("socket.gethostbyname")
    @patch("random.choice")
    def test_form_valid(self, mock_choices, mock_socket, mock_settings):
        def settings_effect(domain, channel):
            nonlocal _channel
            _channel = channel

        choices = (c for c in self.secret)
        mock_choices.side_effect = lambda letters: next(choices)
        mock_settings.side_effect = settings_effect
        mock_socket.return_value = "192.168.123.45"

        self.client.force_login(self.admin)
        toggle = True
        max_length = Channel._meta.get_field("name").max_length
        for p in ["/{}", "/{}/", "/{}/path", "/path/{}/"]:
            path = p.format(self.app_id)
            for scheme in ["", "http", "https"]:
                _channel: Channel = None
                choices = (c for c in self.secret)
                data = {
                    "secret": self.secret,
                    "base_url": self.new_url("valid.com", path=path, scheme=scheme),
                    "bot_username": self.bot_username,
                }

                if toggle:
                    toggle = not toggle
                    domain = data["base_url"].replace("http://", "").replace("https://", "").split("/")[0]
                    data["base_url"] = f"{'x' * (max_length-len(domain))}-{data['base_url']}"
                response = self.form_submit self.client.post(self.claim_url, data=data)
                self.assertIsInstance(_channel, Channel, msg=f"Data: {data}")
                self.assertEqual(_channel.channel_type, RocketChatType.code)
                self.assertRedirect(response, reverse("channels.channel_filter", args=[_channel.uuid]))

                domain = data["base_url"].replace("http://", "").replace("https://", "").split("/")[0]
                expected = f"{RocketChatType.name}: {domain}"
                if len(expected) > max_length:
                    expected = f"{expected[:max_length-3]}..."
                self.assertEqual(_channel.name, expected, f"\nExpected: {expected}\nGot: {_channel.name}")
                self.assertFalse(_channel.config[RocketChatType.CONFIG_BASE_URL].endswith("/"))

    @patch("temba.channels.types.rocketchat.client.Client.settings")
    @patch("socket.gethostbyname")
    @patch("random.choice")
    def test_form_invalid_url(self, mock_choices, mock_socket, mock_settings):
        mock_choices.side_effect = lambda letters: next(choices)
        mock_socket.return_value = "192.168.123.45"
        invalid_payloads = [
            {
                "secret": self.secret,
                "base_url": self.new_url("valid.com", path=path, scheme=scheme),
                "bot_username": self.bot_username,
            }
        ]

        self.client.force_login(self.admin)

        choices = (c for c in self.secret)
        response = self.client.post(self.claim_url, {"base_url": self.secure_url})
        self.assertFormError(response, "form", None, "Invalid secret code.")  # Hidden field

        choices = (c for c in self.secret)
        response = self.client.post(self.claim_url, {"secret": "", "base_url": self.secure_url})
        self.assertFormError(response, "form", None, "Invalid secret code.")  # Hidden field

        choices = (c for c in self.secret)
        response = self.client.post(self.claim_url, {"secret": self.secret2, "base_url": self.secure_url})
        self.assertFormError(response, "form", None, "Secret code change detected.")  # Hidden field

        choices = (c for c in self.secret)
        response = self.client.post(self.claim_url, {"secret": self.secret})
        self.assertFormError(response, "form", "base_url", "This field is required.")

        choices = (c for c in self.secret)
        response = self.client.post(self.claim_url, {"secret": self.secret, "base_url": ""})
        self.assertFormError(response, "form", "base_url", "This field is required.")

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