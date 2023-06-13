from django.urls import reverse

from temba.api.v2.tests import APITest
from temba.contacts.models import ExportContactsTask
from temba.notifications.models import Notification
from temba.notifications.types import ExportFinishedNotificationType
from temba.tests import matchers


class EndpointsTest(APITest):
    def test_notifications(self):
        notifications_url = reverse("api.internal.notifications")

        self.assertEndpointAccess(notifications_url, viewer_get=200, admin_get=200, agent_get=200)

        # simulate an export finishing
        export = ExportContactsTask.create(self.org, self.admin)
        ExportFinishedNotificationType.create(export)

        # and org being suspended
        self.org.suspend()

        response = self.getJSON(notifications_url, readonly_models={Notification})

        self.assertEqual(200, response.status_code)
        self.assertEqual(
            {
                "next": None,
                "previous": None,
                "results": [
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
                        "target_url": f"/assets/download/contact_export/{export.id}/",
                        "is_seen": False,
                        "export": {"type": "contact"},
                    },
                ],
            },
            response.json(),
        )

        # notifications are user specific
        self.login(self.editor)

        response = self.getJSON(notifications_url, readonly_models={Notification})

        self.assertEqual(200, response.status_code)
        self.assertEqual(
            {"next": None, "previous": None, "results": []},
            response.json(),
        )
