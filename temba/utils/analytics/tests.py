from unittest.mock import MagicMock, patch

from django.contrib.auth.models import AnonymousUser
from django.template import Engine, Template
from django.urls import reverse

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
    def test_get_hook_html(self, mock_get_backends):
        good = MagicMock()
        good.slug = "good"
        good.get_hook_template.return_value = "good/frame_top.html"
        mock_get_backends.return_value = [BadBackend(), good]

        real_get_template = Engine.get_default().get_template

        def get_template(name):
            if name == "good/frame_top.html":
                return Template('<script>alert("good")</script>\n')
            elif name == "bad/frame_top.html":
                return Template('<script>alert("bad")</script>\n')
            else:
                return real_get_template(name)

        with patch("django.template.engine.Engine.get_template", wraps=get_template):
            self.login(self.admin)
            response = self.client.get(reverse("orgs.org_home"))

            self.assertContains(
                response,
                """<!-- begin hook for bad -->
<script>alert("bad")</script>
<!-- end hook for bad -->
<!-- begin hook for good -->
<script>alert("good")</script>
<!-- end hook for good -->""",
            )


class BadBackend(AnalyticsBackend):
    slug = "bad"
    hook_templates = {"frame-top": "bad/frame_top.html"}

    def gauge(self, event: str, value):
        raise ValueError("boom")

    def track(self, user, event: str, properties: dict):
        raise ValueError("boom")

    def identify(self, user, brand, org):
        raise ValueError("boom")

    def change_consent(self, user, consent: bool):
        raise ValueError("boom")
