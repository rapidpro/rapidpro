from unittest.mock import MagicMock, patch

from django.contrib.auth.models import AnonymousUser

from temba.tests import TembaTest
from temba.utils import analytics

from .base import AnalyticsBackend


class AnalyticsTest(TembaTest):
    def setUp(self):
        super().setUp()

    @patch("temba.utils.analytics.base.get_backends")
    def test_gauge(self, mock_get_backends):
        good = MagicMock()
        mock_get_backends.return_value = [BadBackend(), good]

        analytics.gauge("foo_level", 123)

        good.gauge.assert_called_once_with("foo_level", 123)

    @patch("temba.utils.analytics.base.get_backends")
    def test_track(self, mock_get_backends):
        good = MagicMock()
        mock_get_backends.return_value = [BadBackend(), good]

        analytics.track(self.user, "foo_created", {"foo_id": 234})

        good.track.assert_called_once_with(self.user, "foo_created", {"foo_id": 234})
        good.track.reset_mock()

        # anonymous user is a noop
        analytics.track(AnonymousUser(), "foo_created", {"foo_id": 234})

        good.track.assert_not_called()

    @patch("temba.utils.analytics.base.get_backends")
    def test_identify(self, mock_get_backends):
        good = MagicMock()
        mock_get_backends.return_value = [BadBackend(), good]

        analytics.identify(self.user, {"name": "Cool"}, self.org)

        good.identify.assert_called_once_with(self.user, {"name": "Cool"}, self.org)

    @patch("temba.utils.analytics.base.get_backends")
    def test_change_consent(self, mock_get_backends):
        good = MagicMock()
        mock_get_backends.return_value = [BadBackend(), good]

        analytics.change_consent(self.user, True)

        good.change_consent.assert_called_once_with(self.user, True)

    @patch("temba.utils.analytics.base.get_backends")
    def test_get_template_html(self, mock_get_backends):
        good = MagicMock()
        good.slug = "good"
        good.get_template_html.return_value = '<script>alert("good")</script>'
        mock_get_backends.return_value = [BadBackend(), good]

        self.assertEqual(
            """<!-- begin hook for bad -->
<script>alert("bad")</script>
<!-- end hook for bad -->
<!-- begin hook for good -->
<script>alert("good")</script>
<!-- end hook for good -->
""",
            analytics.get_template_html("login"),
        )


class BadBackend(AnalyticsBackend):
    slug = "bad"

    def gauge(self, event: str, value):
        raise ValueError("boom")

    def track(self, user, event: str, properties: dict):
        raise ValueError("boom")

    def identify(self, user, brand, org):
        raise ValueError("boom")

    def change_consent(self, user, consent: bool):
        raise ValueError("boom")

    def get_template_html(self, hook) -> str:
        return '<script>alert("bad")</script>'
