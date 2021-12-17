from unittest.mock import patch

from django.conf import settings
from django.test.utils import override_settings

from temba.tests import TembaTest
from temba.utils import analytics


@override_settings(LIBRATO_USER="acme", LIBRATO_TOKEN="sesame")
class LibratoTest(TembaTest):
    @patch("librato_bg.client.Client.gauge")
    def test_gauge(self, mock_client_gauge):
        analytics.init()
        analytics.gauge("temba.foo_level", 12)
        mock_client_gauge.assert_called_with("temba.foo_level", 12, f"{settings.MACHINE_HOSTNAME}.{settings.HOSTNAME}")

    def test_track(self):
        analytics.init()
        analytics.track(self.admin, "foo_created", {})  # noop

    def test_identify(self):
        analytics.init()
        analytics.identify(self.user, "rapidpro.io", self.org)  # noop

    def test_change_consent(self):
        analytics.init()
        analytics.change_consent(self.agent, True)  # noop

    def test_get_template_context(self):
        analytics.init()
        self.assertEqual({}, analytics.context_processor(None))
