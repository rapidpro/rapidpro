from django.urls import reverse
from django.utils import timezone

from temba.api.tests import APITestMixin
from temba.contacts.models import ContactExport
from temba.notifications.types import ExportFinishedNotificationType
from temba.tests import TembaTest, matchers
from temba.tickets.models import TicketExport


class EndpointsTest(APITestMixin, TembaTest):
    def test_notifications(self):
        endpoint_url = reverse("api.internal.notifications") + ".json"

        self.assertPostNotAllowed(endpoint_url)

        # simulate an export finishing
        export1 = ContactExport.create(self.org, self.admin)
        ExportFinishedNotificationType.create(export1)

        # simulate an export by another user finishing
        export2 = TicketExport.create(self.org, self.editor, start_date=timezone.now(), end_date=timezone.now())
        ExportFinishedNotificationType.create(export2)

        # and org being suspended
        self.org.suspend()

        self.assertGet(
            endpoint_url,
            [self.admin],
            results=[
                {
                    "type": "incident:started",
                    "created_on": matchers.ISODate(),
                    "target_url": "/incident/",
                    "is_seen": False,
                    "incident": {
                        "type": "org:suspended",
                        "started_on": matchers.ISODate(),
                        "ended_on": None,
                    },
                },
                {
                    "type": "export:finished",
                    "created_on": matchers.ISODate(),
                    "target_url": f"/export/download/{export1.uuid}/",
                    "is_seen": False,
                    "export": {"type": "contact", "num_records": None},
                },
            ],
        )

        # notifications are user specific
        self.assertGet(
            endpoint_url,
            [self.editor],
            results=[
                {
                    "type": "export:finished",
                    "created_on": matchers.ISODate(),
                    "target_url": f"/export/download/{export2.uuid}/",
                    "is_seen": False,
                    "export": {"type": "ticket", "num_records": None},
                },
            ],
        )

        # a DELETE marks all notifications as seen
        self.assertDelete(endpoint_url, self.admin)

        self.assertEqual(0, self.admin.notifications.filter(is_seen=False).count())
        self.assertEqual(2, self.admin.notifications.filter(is_seen=True).count())
        self.assertEqual(1, self.editor.notifications.filter(is_seen=False).count())
