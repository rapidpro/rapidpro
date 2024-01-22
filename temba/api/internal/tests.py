from django.urls import reverse

from temba.api.tests import APITestMixin
from temba.contacts.models import ContactExport
from temba.notifications.types import ExportFinishedNotificationType
from temba.tests import TembaTest, matchers


class EndpointsTest(APITestMixin, TembaTest):
    def test_notifications(self):
        endpoint_url = reverse("api.internal.notifications") + ".json"

        self.assertPostNotAllowed(endpoint_url)
        self.assertDeleteNotAllowed(endpoint_url)

        # simulate an export finishing
        export = ContactExport.create(self.org, self.admin)
        ExportFinishedNotificationType.create(export)

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
                    "target_url": f"/export/download/{export.uuid}/",
                    "is_seen": False,
                    "export": {"type": "contact", "num_records": None},
                },
            ],
        )

        # notifications are user specific
        self.assertGet(endpoint_url, [self.agent, self.user, self.editor], results=[])
