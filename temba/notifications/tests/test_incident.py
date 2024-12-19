from datetime import datetime, timezone as tzone

from temba.notifications.models import Incident, Notification
from temba.tests import TembaTest, matchers


class IncidentTest(TembaTest):
    def test_create(self):
        # we use a unique constraint to enforce uniqueness on org+type+scope for ongoing incidents which allows use of
        # INSERT .. ON CONFLICT DO NOTHING
        incident1 = Incident.get_or_create(self.org, "org:flagged", scope="scope1")

        # try to create another for the same scope
        incident2 = Incident.get_or_create(self.org, "org:flagged", scope="scope1")

        # different scope
        incident3 = Incident.get_or_create(self.org, "org:flagged", scope="scope2")

        self.assertEqual(incident1, incident2)
        self.assertNotEqual(incident1, incident3)

        # each created incident creates a notification for the workspace admin
        self.assertEqual(2, Notification.objects.count())
        self.assertEqual(1, incident1.notifications.count())
        self.assertEqual(1, incident2.notifications.count())

        # check that once incident 1 ends, new incidents can be created for same scope
        incident1.end()

        incident4 = Incident.get_or_create(self.org, "org:flagged", scope="scope1")

        self.assertNotEqual(incident1, incident4)
        self.assertEqual(3, Notification.objects.count())
        self.assertEqual(1, incident4.notifications.count())

    def test_org_flagged(self):
        self.org.flag()

        incident = Incident.objects.get()
        self.assertEqual("org:flagged", incident.incident_type)
        self.assertEqual({self.admin}, set(n.user for n in incident.notifications.all()))

        self.assertEqual(
            {"type": "org:flagged", "started_on": matchers.ISODate(), "ended_on": None}, incident.as_json()
        )

        self.org.unflag()

        incident = Incident.objects.get()  # still only have 1 incident, but now it has ended
        self.assertEqual("org:flagged", incident.incident_type)
        self.assertIsNotNone(incident.ended_on)

    def test_org_suspended(self):
        self.org.suspend()

        incident = Incident.objects.get()
        self.assertEqual("org:suspended", incident.incident_type)
        self.assertEqual({self.admin}, set(n.user for n in incident.notifications.all()))

        self.assertEqual(
            {"type": "org:suspended", "started_on": matchers.ISODate(), "ended_on": None}, incident.as_json()
        )

        self.org.unsuspend()

        incident = Incident.objects.get()  # still only have 1 incident, but now it has ended
        self.assertEqual("org:suspended", incident.incident_type)
        self.assertIsNotNone(incident.ended_on)

    def test_webhooks_unhealthy(self):
        incident = Incident.objects.create(  # mailroom will create these
            org=self.org,
            incident_type="webhooks:unhealthy",
            scope="",
            started_on=datetime(2021, 11, 12, 14, 23, 30, 123456, tzinfo=tzone.utc),
        )

        self.assertEqual(
            {
                "type": "webhooks:unhealthy",
                "started_on": "2021-11-12T14:23:30.123456+00:00",
                "ended_on": None,
            },
            incident.as_json(),
        )
