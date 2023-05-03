from django.db import connection
from django.urls import reverse

from temba.contacts.models import ExportContactsTask
from temba.notifications.models import Notification
from temba.notifications.types import ExportFinishedNotificationType
from temba.tests import TembaTest, matchers


class EndpointsTest(TembaTest):
    def setUp(self):
        super().setUp()

        # this is needed to prevent REST framework from rolling back transaction created around each unit test
        connection.settings_dict["ATOMIC_REQUESTS"] = False

    def tearDown(self):
        super().tearDown()

        connection.settings_dict["ATOMIC_REQUESTS"] = True

    def test_notifications(self):
        endpoint_url = reverse("api.internal.notifications") + ".json"

        response = self.client.get(endpoint_url)
        self.assertEqual(403, response.status_code)

        # simulate an export finishing
        export = ExportContactsTask.create(self.org, self.admin)
        ExportFinishedNotificationType.create(export)

        # and org being suspended
        self.org.suspend()

        self.login(self.admin)

        with self.mockReadOnly(assert_models={Notification}):
            response = self.client.get(endpoint_url)

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

        with self.mockReadOnly(assert_models={Notification}):
            response = self.client.get(endpoint_url)

        self.assertEqual(200, response.status_code)
        self.assertEqual(
            {"next": None, "previous": None, "results": []},
            response.json(),
        )
