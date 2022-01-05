from unittest.mock import patch

from django.conf import settings

from temba.tests import TembaTest

from .backend import LibratoBackend


class LibratoTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.backend = LibratoBackend("acme", "sesame")

    @patch("librato_bg.client.Client.gauge")
    def test_gauge(self, mock_client_gauge):
        self.backend.gauge("temba.foo_level", 12)

        mock_client_gauge.assert_called_once_with(
            "temba.foo_level", 12, f"{settings.MACHINE_HOSTNAME}.{settings.HOSTNAME}"
        )

    def test_track(self):
        self.backend.track(self.admin, "foo_created", {"foo_id": "345"})  # noop

    def test_identify(self):
        self.backend.identify(self.user, {"name": "Cool"}, self.org)  # noop

    def test_change_consent(self):
        self.backend.change_consent(self.agent, True)  # noop

    def test_get_hook_template(self):
        # no hooks..
        self.assertIsNone(self.backend.get_hook_template("login"))
        self.assertIsNone(self.backend.get_hook_template("frame-top"))
        self.assertEqual({}, self.backend.get_hook_context(None))  # none
