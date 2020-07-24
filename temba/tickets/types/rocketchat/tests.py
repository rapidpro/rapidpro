import uuid

from django.urls import reverse
from requests.exceptions import Timeout
from unittest.mock import patch

from temba.tests import MockResponse, TembaTest
from temba.tickets.models import Ticketer
from temba.utils.text import random_string

from .client import Client, ClientError
from .type import RocketChatType
from .views import SECRET_LENGTH, ConnectView


class RocketChatMixin(TembaTest):
    def setUp(self):
        super().setUp()
        self.connect_url = reverse("tickets.types.rocketchat.connect")
        self.domain = "rocketchat-domain.com"
        self.app_id = f"{uuid.uuid4()}"
        self.secret = random_string(SECRET_LENGTH)
        self.secret2 = random_string(SECRET_LENGTH)
        self.insecure_url = self.build_url("{app_id}", secure=False)
        self.secure_url = self.build_url("{app_id}")

    def build_url(self, template: str, secure=True):
        return f"http{'s' if secure else ''}://{self.domain}/{template.format(app_id=self.app_id)}"

    def new_ticketer(self, config=None) -> Ticketer:
        return Ticketer.create(
            org=self.org,
            user=self.user,
            ticketer_type=RocketChatType.slug,
            name="Name",
            config=config or {}
        )


class ClientTest(RocketChatMixin):
    def test_secret_check_success(self):
        with patch("requests.get") as mock_request:
            mock_request.return_value = MockResponse(200, {})
            try:
                Client(self.secure_url, self.secret).secret_check()
            except ClientError:
                self.fail("The status 200 should not raise exceptions")

    def test_secret_check_fail(self):
        for status in range(201, 599):
            with patch("requests.get") as mock_request:
                mock_request.return_value = MockResponse(status, {})
                with self.assertRaises(ClientError, msg=f"The status {status} must be invalid"):
                    Client(self.secure_url, self.secret).secret_check()

    @patch("requests.get")
    def test_secret_check_timeout(self, mock_request):
        def side_effect(*arg, **kwargs):
            raise Timeout()

        mock_request.side_effect = side_effect
        with self.assertRaises(ClientError):
            Client(self.secure_url, self.secret).secret_check()

    @patch("requests.post")
    def test_settings_success(self, mock_request):
        mock_request.return_value = MockResponse(204, {})
        try:
            Client(self.secure_url, self.secret).settings(self.domain, self.new_ticketer())
        except ClientError:
            self.fail("The status 204 should not raise exceptions")

    def test_settings_fail(self):
        ticketer = self.new_ticketer()
        for status in range(200, 599):
            if status == 204:
                continue
            with patch("requests.post") as mock_request:
                mock_request.return_value = MockResponse(status, {})
                with self.assertRaises(ClientError, msg=f"The status {status} must be invalid"):
                    Client(self.secure_url, self.secret).settings(self.domain, ticketer)


class RocketChatTypeTest(RocketChatMixin):
    @patch("temba.orgs.models.Org.get_brand_domain")
    def test_callback_url(self, mock_brand_domain):
        ticketer = self.new_ticketer()
        domains = [("https://", "test.domain.com"), ("", "http://test.domain.com"), ("", "https://test.domain.com")]
        for scheme, domain in domains:
            mock_brand_domain.return_value = domain
            self.assertEqual(
                RocketChatType.callback_url(ticketer),
                f"{scheme}{domain}/mr/tickets/types/rocketchat/{ticketer.uuid}/event"
            )

        mock_brand_domain.return_value = "test.domain.com"
        domains = [("https://", "req.domain.com"), ("", "http://req.domain.com"), ("", "https://requestreq.domain.com")]
        for scheme, domain in domains:
            self.assertEqual(
                RocketChatType.callback_url(ticketer, domain),
                f"{scheme}{domain}/mr/tickets/types/rocketchat/{ticketer.uuid}/event"
            )


class RocketChatViewTest(RocketChatMixin):
    @patch("random.choice")
    def test_session_key(self, mock_choices):
        choices = (c for c in self.secret)
        mock_choices.side_effect = lambda letters: next(choices)
        self.client.force_login(self.admin)
        response = self.client.get(self.connect_url)
        self.assertEqual(
            response.wsgi_request.session.get(ConnectView.SESSION_KEY),
            self.secret
        )
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
            response.context_data["form"].initial.get("secret"),
            self.secret,
        )

        configure()
        self.client.force_login(self.admin)
        with patch("temba.tickets.types.rocketchat.views.ConnectView.derive_initial") as mock_initial:
            mock_initial.return_value = {"secret": self.secret2}
            response = self.client.get(self.connect_url)
        self.assertEqual(
            response.context_data["form"].initial.get("secret"),
            self.secret2,
        )

    @patch("temba.tickets.types.rocketchat.client.Client.settings")
    @patch("temba.tickets.types.rocketchat.client.Client.secret_check")
    @patch("socket.gethostbyname")
    @patch("random.choice")
    def test_form_valid(self, mock_choices, mock_socket, mock_secret, mock_settings):
        def settings_effect(domain, ticketer):
            nonlocal object
            object = ticketer

        choices = (c for c in self.secret)
        object = None
        mock_choices.side_effect = lambda letters: next(choices)
        mock_settings.side_effect = settings_effect
        mock_socket.return_value = "192.55.123.1"  # Fake IP

        self.client.force_login(self.admin)
        response = self.client.post(self.connect_url, {
            "secret": self.secret,
            "base_url": self.secure_url
        })
        self.assertIsInstance(object, Ticketer)
        self.assertEqual(object.ticketer_type, RocketChatType.slug)
        self.assertRedirect(response, reverse("tickets.ticket_filter", args=[object.uuid]))

        expected = f"{RocketChatType.name}: {self.domain}"
        self.assertTrue(object.name.startswith(
            expected[:Ticketer._meta.get_field("name").max_length - 4]
        ), f"\nExpected: {expected}\nGot: {object.name}")
