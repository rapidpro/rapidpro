import re
import uuid
from secrets import token_urlsafe
from unittest.mock import patch

from requests.exceptions import Timeout

from django.contrib.auth.models import Group
from django.urls import reverse

from temba.tests import MockResponse, TembaTest
from temba.tickets.models import Ticketer
from temba.utils.text import random_string

from .client import Client, ClientError
from .type import RocketChatType
from .views import SECRET_LENGTH, ConnectView


class RocketChatTypeTest(TembaTest):
    def test_is_available_to(self):
        self.assertFalse(RocketChatType().is_available_to(self.admin))

        Group.objects.get(name="Beta").user_set.add(self.admin)

        self.assertTrue(RocketChatType().is_available_to(self.admin))


class RocketChatMixin(TembaTest):
    def setUp(self):
        super().setUp()
        self.connect_url = reverse("tickets.types.rocketchat.connect")
        self.app_id = f"{uuid.uuid4()}"
        self.secret = random_string(SECRET_LENGTH)
        self.secret2 = random_string(SECRET_LENGTH)

        self.domain = self.new_url("rocketchat-domain.com", scheme="")
        self.insecure_url = self.new_url(self.domain, path=f"/{self.app_id}", unique=False)
        self.secure_url = self.new_url(self.domain, path=f"/{self.app_id}", scheme="https", unique=False)

    @staticmethod
    def new_url(domain, path=None, scheme="https", unique=True):
        url = f"{domain}{path or ''}"
        if unique:
            unique = re.sub(r"[^\da-zA-Z]", "", token_urlsafe(10))
            url = f"{unique}.{url}"
        if scheme:
            return f"{scheme}://{url}".lower()
        return url.lower()

    def new_ticketer(self, config=None) -> Ticketer:
        return Ticketer.create(
            org=self.org, user=self.user, ticketer_type=RocketChatType.slug, name="Name", config=config or {}
        )


class ClientTest(RocketChatMixin):
    @patch("requests.put")
    def test_settings_success(self, mock_request):
        mock_request.return_value = MockResponse(204, {})
        try:
            Client(self.secure_url, self.secret).settings("http://temba.io/mr/tickets/1234-5678")
        except ClientError:
            self.fail("The status 204 should not raise exceptions")

    def test_settings_fail(self):
        for status in range(200, 599):
            if status == 204:
                continue
            with patch("requests.put") as mock_request:
                mock_request.return_value = MockResponse(status, {})
                with self.assertRaises(ClientError, msg=f"The status {status} must be invalid"):
                    Client(self.secure_url, self.secret).settings("http://temba.io/mr/tickets/1234-5678")

    @patch("requests.put")
    def test_settings_exceptions(self, mock_request):
        for err in [Timeout(), Exception()]:

            def side_effect(*arg, **kwargs):
                raise err

            mock_request.side_effect = side_effect
            with self.assertRaises(ClientError):
                Client(self.secure_url, self.secret).settings("http://temba.io/mr/tickets/1234-5678")


class RocketChatViewTest(RocketChatMixin):
    def check_exceptions(self, mock_choices, mock_request, timeout_msg, exception_msg):
        mock_choices.side_effect = lambda letters: next(choices)

        self.client.force_login(self.admin)
        check = [(Timeout(), timeout_msg), (Exception(), exception_msg)]
        for err, msg in check:

            def side_effect(*arg, **kwargs):
                raise err

            mock_request.side_effect = side_effect
            choices = (c for c in self.secret)
            data = {
                "secret": self.secret,
                "base_url": self.new_url("valid.com", path=f"/{self.app_id}"),
                "admin_auth_token": "abc123",
                "admin_user_id": "123",
            }
            response = self.client.post(self.connect_url, data=data)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(response.context["messages"]), 1)
            self.assertEqual([f"{m}" for m in response.context["messages"]][0], msg)

    @patch("random.choice")
    def test_session_key(self, mock_choices):
        choices = (c for c in self.secret)
        mock_choices.side_effect = lambda letters: next(choices)
        self.client.force_login(self.admin)
        response = self.client.get(self.connect_url)
        self.assertEqual(response.wsgi_request.session.get(ConnectView.SESSION_KEY), self.secret)
        response.wsgi_request.session.pop(ConnectView.SESSION_KEY, None)

    @patch("random.choice")
    def test_form_initial(self, mock_choices):
        def configure():
            choices = (c for c in self.secret)
            mock_choices.side_effect = lambda letters: next(choices)

        configure()
        self.client.force_login(self.admin)
        response = self.client.get(self.connect_url)
        self.assertEqual(
            response.context_data["form"].initial.get("secret"), self.secret,
        )

        configure()
        with patch("temba.tickets.types.rocketchat.views.ConnectView.derive_initial") as mock_initial:
            mock_initial.return_value = {"secret": self.secret2}
            response = self.client.get(self.connect_url)
        self.assertEqual(
            response.context_data["form"].initial.get("secret"), self.secret2,
        )

    @patch("temba.tickets.types.rocketchat.client.Client.settings")
    @patch("socket.gethostbyname")
    @patch("random.choice")
    def test_form_valid(self, mock_choices, mock_socket, mock_settings):
        choices = (c for c in self.secret)
        mock_choices.side_effect = lambda letters: next(choices)
        mock_socket.return_value = "192.55.123.1"  # Fake IP

        self.client.force_login(self.admin)

        max_length = Ticketer._meta.get_field("name").max_length
        for path in [f"/{self.app_id}", f"/{self.app_id}/", f"/{self.app_id}/path", f"/path/{self.app_id}/"]:
            choices = (c for c in self.secret)
            data = {
                "secret": self.secret,
                "base_url": self.new_url("valid.com", path=path, scheme="https"),
                "admin_auth_token": "abc123",
                "admin_user_id": "123",
            }
            response = self.client.post(self.connect_url, data=data)

            self.assertEqual(response.status_code, 302)

            ticketer = Ticketer.objects.order_by("id").last()

            self.assertRedirect(response, reverse("tickets.ticket_filter", args=[ticketer.uuid]))

            domain = data["base_url"].replace("http://", "").replace("https://", "").split("/")[0]
            expected = f"{RocketChatType.name}: {domain}"
            if len(expected) > max_length:
                expected = f"{expected[:max_length - 3]}..."
            self.assertEqual(ticketer.name, expected)
            self.assertFalse(ticketer.config[RocketChatType.CONFIG_BASE_URL].endswith("/"))

    @patch("temba.tickets.types.rocketchat.client.Client.settings")
    @patch("socket.gethostbyname")
    @patch("random.choice")
    def test_form_invalid_url(self, mock_choices, mock_socket, mock_settings):
        mock_choices.side_effect = lambda letters: next(choices)
        mock_socket.return_value = "192.55.123.1"  # Fake IP

        self.client.force_login(self.admin)

        base = {"admin_auth_token": "abc123", "admin_user_id": "123"}

        choices = (c for c in self.secret)
        response = self.client.post(self.connect_url, {**base, "base_url": self.secure_url})
        self.assertFormError(response, "form", None, "Invalid secret code.")  # Hidden field

        choices = (c for c in self.secret)
        response = self.client.post(self.connect_url, {**base, "secret": "", "base_url": self.secure_url})
        self.assertFormError(response, "form", None, "Invalid secret code.")  # Hidden field

        choices = (c for c in self.secret)
        response = self.client.post(self.connect_url, {**base, "secret": self.secret2, "base_url": self.secure_url})
        self.assertFormError(response, "form", None, "Secret code change detected.")  # Hidden field

        choices = (c for c in self.secret)
        response = self.client.post(self.connect_url, {**base, "secret": self.secret})
        self.assertFormError(response, "form", "base_url", "This field is required.")

        choices = (c for c in self.secret)
        response = self.client.post(self.connect_url, {**base, "secret": self.secret, "base_url": ""})
        self.assertFormError(response, "form", "base_url", "This field is required.")

        choices = (c for c in self.secret)
        response = self.client.post(self.connect_url, data={**base, "secret": self.secret, "base_url": "domain"})
        self.assertFormError(response, "form", "base_url", "Enter a valid URL.")

        choices = (c for c in self.secret)
        response = self.client.post(self.connect_url, data={**base, "secret": self.secret, "base_url": "domain.com"})
        self.assertFormError(response, "form", "base_url", f"Invalid URL: http://domain.com")

        for path in [f"", f"/", f"/path", f"/path{self.app_id}/"]:
            for scheme in ["", "http", "https"]:
                choices = (c for c in self.secret)
                data = {
                    **base,
                    "secret": self.secret,
                    "base_url": self.new_url("invalid.com", path=path, scheme=scheme),
                }
                response = self.client.post(self.connect_url, data=data)

                url = data["base_url"]
                if not url.startswith("http"):
                    url = f"http://{url}"
                self.assertFormError(response, "form", "base_url", f"Invalid URL: {url}")

        choices = (c for c in self.secret)
        data = {**base, "secret": self.secret, "base_url": self.new_url("domain.com", path=f"/{self.app_id}")}
        self.new_ticketer({RocketChatType.CONFIG_BASE_URL: data["base_url"]})
        response = self.client.post(self.connect_url, data=data)
        self.assertFormError(
            response, "form", "base_url", "There is already a ticketing service configured for this URL."
        )

    @patch("socket.gethostbyname")
    @patch("random.choice")
    @patch("requests.put")
    def test_settings_exception(self, mock_request, mock_choices, mock_socket):
        mock_socket.return_value = "192.55.123.1"  # Fake IP
        self.check_exceptions(
            mock_choices, mock_request, "Connection to RocketChat is taking too long.", "Configuration has failed",
        )
