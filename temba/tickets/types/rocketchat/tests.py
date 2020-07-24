import uuid
from unittest.mock import patch

from django.urls import reverse
from requests.exceptions import Timeout

from temba.tests import MockResponse, TembaTest
from temba.tickets.models import Ticketer
from temba.utils.text import random_string
from .client import Client, ClientError
from .type import RocketChatType
from .views import SECRET_LENGTH


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
