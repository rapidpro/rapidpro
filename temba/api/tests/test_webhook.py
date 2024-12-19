from datetime import timedelta

from django.test import override_settings
from django.utils import timezone

from temba.api.models import Resthook, WebHookEvent
from temba.api.tasks import trim_webhook_events
from temba.tests import TembaTest


class WebHookTest(TembaTest):
    def test_trim_events_and_results(self):
        five_hours_ago = timezone.now() - timedelta(hours=5)

        # create some events
        resthook = Resthook.get_or_create(org=self.org, slug="registration", user=self.admin)
        WebHookEvent.objects.create(org=self.org, resthook=resthook, data={}, created_on=five_hours_ago)

        with override_settings(RETENTION_PERIODS={"webhookevent": None}):
            trim_webhook_events()
            self.assertTrue(WebHookEvent.objects.all())

        with override_settings(RETENTION_PERIODS={"webhookevent": timedelta(hours=12)}):  # older than our event
            trim_webhook_events()
            self.assertTrue(WebHookEvent.objects.all())

        with override_settings(RETENTION_PERIODS={"webhookevent": timedelta(hours=2)}):
            trim_webhook_events()
            self.assertFalse(WebHookEvent.objects.all())
