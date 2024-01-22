from datetime import date, datetime, timezone as tzone

from django.core import mail
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from temba.contacts.models import ContactExport, ContactImport
from temba.flows.models import ExportFlowResultsTask
from temba.msgs.models import ExportMessagesTask
from temba.orgs.models import OrgRole
from temba.tests import CRUDLTestMixin, TembaTest, matchers
from temba.tickets.models import TicketExport

from .incidents.builtin import OrgFlaggedIncidentType
from .models import Incident, Notification
from .tasks import send_notification_emails, squash_notification_counts
from .types.builtin import ExportFinishedNotificationType


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


class IncidentCRUDLTest(TembaTest, CRUDLTestMixin):
    def test_list(self):
        list_url = reverse("notifications.incident_list")

        # create 2 org flagged incidents (1 ended, 1 ongoing)
        incident1 = OrgFlaggedIncidentType.get_or_create(self.org)
        OrgFlaggedIncidentType.get_or_create(self.org).end()
        incident2 = OrgFlaggedIncidentType.get_or_create(self.org)

        # create 2 flow webhook incidents (1 ended, 1 ongoing)
        incident3 = Incident.objects.create(
            org=self.org,
            incident_type="webhooks:unhealthy",
            scope="",
            started_on=timezone.now(),
            ended_on=timezone.now(),
        )
        incident4 = Incident.objects.create(org=self.org, incident_type="webhooks:unhealthy", scope="")

        # main list items are the ended incidents
        response = self.assertListFetch(
            list_url, allow_viewers=False, allow_editors=False, context_objects=[incident3, incident1]
        )

        # with ongoing ones in separate list
        self.assertEqual({incident4, incident2}, set(response.context["ongoing"]))


@override_settings(SEND_EMAILS=True)
class NotificationTest(TembaTest):
    def assert_notifications(self, *, after: datetime = None, expected_json: dict, expected_users: set, email: True):
        notifications = Notification.objects.all()
        if after:
            notifications = notifications.filter(created_on__gt=after)

        self.assertEqual(len(expected_users), notifications.count(), "notification count mismatch")

        actual_users = set()

        for notification in notifications:
            self.assertEqual(expected_json, notification.as_json())
            self.assertEqual(email, notification.email_status == Notification.EMAIL_STATUS_PENDING)
            actual_users.add(notification.user)

        # check who was notified
        self.assertEqual(expected_users, actual_users)

    def test_contact_export_finished(self):
        export = ContactExport.create(self.org, self.editor)
        export.perform()

        ExportFinishedNotificationType.create(export)

        self.assertFalse(self.editor.notifications.get(export=export).is_seen)

        # we only notify the user that started the export
        self.assert_notifications(
            after=export.created_on,
            expected_json={
                "type": "export:finished",
                "created_on": matchers.ISODate(),
                "target_url": f"/export/download/{export.uuid}/",
                "is_seen": False,
                "export": {"type": "contact", "num_records": 0},
            },
            expected_users={self.editor},
            email=True,
        )

        send_notification_emails()

        self.assertEqual(1, len(mail.outbox))
        self.assertEqual("[Nyaruka] Your contact export is ready", mail.outbox[0].subject)
        self.assertEqual(["editor@nyaruka.com"], mail.outbox[0].recipients())

        # calling task again won't send more emails
        send_notification_emails()

        self.assertEqual(1, len(mail.outbox))

        # if a user visits the export download page, their notification for that export is now read
        self.login(self.editor)
        self.client.get(reverse("orgs.export_download", args=[export.uuid]))

        self.assertTrue(self.editor.notifications.get(export=export).is_seen)

    def test_message_export_finished(self):
        export = ExportMessagesTask.create(
            self.org, self.editor, start_date=date.today(), end_date=date.today(), system_label="I"
        )
        export.perform()

        ExportFinishedNotificationType.create(export)

        self.assertFalse(self.editor.notifications.get(message_export=export).is_seen)

        # we only notify the user that started the export
        self.assert_notifications(
            after=export.created_on,
            expected_json={
                "type": "export:finished",
                "created_on": matchers.ISODate(),
                "target_url": f"/assets/download/message_export/{export.id}/",
                "is_seen": False,
                "export": {"type": "message", "num_records": 0},
            },
            expected_users={self.editor},
            email=True,
        )

        send_notification_emails()

        self.assertEqual(1, len(mail.outbox))
        self.assertEqual("[Nyaruka] Your message export is ready", mail.outbox[0].subject)
        self.assertEqual(["editor@nyaruka.com"], mail.outbox[0].recipients())

    def test_results_export_finished(self):
        flow1 = self.create_flow("Test Flow 1")
        flow2 = self.create_flow("Test Flow 2")
        export = ExportFlowResultsTask.create(
            self.org,
            self.editor,
            start_date=date.today(),
            end_date=date.today(),
            flows=[flow1, flow2],
            with_fields=(),
            with_groups=(),
            responded_only=True,
            extra_urns=(),
        )
        export.perform()

        ExportFinishedNotificationType.create(export)

        self.assertFalse(self.editor.notifications.get(results_export=export).is_seen)

        # we only notify the user that started the export
        self.assert_notifications(
            after=export.created_on,
            expected_json={
                "type": "export:finished",
                "created_on": matchers.ISODate(),
                "target_url": f"/assets/download/results_export/{export.id}/",
                "is_seen": False,
                "export": {"type": "results", "num_records": 0},
            },
            expected_users={self.editor},
            email=True,
        )

        send_notification_emails()

        self.assertEqual(1, len(mail.outbox))
        self.assertEqual("[Nyaruka] Your results export is ready", mail.outbox[0].subject)
        self.assertEqual(["editor@nyaruka.com"], mail.outbox[0].recipients())
        self.assertIn("Test Flow 1", mail.outbox[0].body)
        self.assertIn("Test Flow 2", mail.outbox[0].body)

    def test_export_finished(self):
        export = TicketExport.create(self.org, self.editor, start_date=date.today(), end_date=date.today())
        export.perform()

        ExportFinishedNotificationType.create(export)

        self.assertFalse(self.editor.notifications.get(export=export).is_seen)

        # we only notify the user that started the export
        self.assert_notifications(
            after=export.created_on,
            expected_json={
                "type": "export:finished",
                "created_on": matchers.ISODate(),
                "target_url": f"/export/download/{export.uuid}/",
                "is_seen": False,
                "export": {"type": "ticket", "num_records": 0},
            },
            expected_users={self.editor},
            email=True,
        )

        send_notification_emails()

        self.assertEqual(1, len(mail.outbox))
        self.assertEqual("[Nyaruka] Your ticket export is ready", mail.outbox[0].subject)
        self.assertEqual(["editor@nyaruka.com"], mail.outbox[0].recipients())

        # if a user visits the export download page, their notification for that export is now read
        self.login(self.editor)
        self.client.get(reverse("orgs.export_download", args=[export.uuid]))

        self.assertTrue(self.editor.notifications.get(export=export).is_seen)

    def test_import_finished(self):
        imp = ContactImport.objects.create(
            org=self.org, mappings={}, num_records=5, created_by=self.editor, modified_by=self.editor
        )

        # mailroom will create these notifications when it's complete
        Notification.create_all(
            imp.org, "import:finished", scope=f"contact:{imp.id}", users=[self.editor], contact_import=imp
        )
        self.assertFalse(self.editor.notifications.get(contact_import=imp).is_seen)

        # we only notify the user that started the import
        self.assert_notifications(
            after=imp.created_on,
            expected_json={
                "type": "import:finished",
                "created_on": matchers.ISODate(),
                "target_url": f"/contactimport/read/{imp.id}/",
                "is_seen": False,
                "import": {"type": "contact", "num_records": 5},
            },
            expected_users={self.editor},
            email=False,
        )

        # if a user visits the import read page, their notification for that import is now read
        self.login(self.editor)
        self.client.get(reverse("contacts.contactimport_read", args=[imp.id]))

        self.assertTrue(self.editor.notifications.get(contact_import=imp).is_seen)

    def test_tickets_opened(self):
        # mailroom will create these notifications
        Notification.create_all(self.org, "tickets:opened", scope="", users=[self.agent, self.editor])

        self.assert_notifications(
            expected_json={
                "type": "tickets:opened",
                "created_on": matchers.ISODate(),
                "target_url": "/ticket/unassigned/",
                "is_seen": False,
            },
            expected_users={self.agent, self.editor},
            email=False,
        )

        # if a user visits the unassigned tickets page, their notification is now read
        self.login(self.agent)
        self.client.get("/ticket/unassigned/")

        self.assertTrue(self.agent.notifications.get().is_seen)
        self.assertFalse(self.editor.notifications.get().is_seen)

    def test_tickets_activity(self):
        # mailroom will create these notifications
        Notification.create_all(self.org, "tickets:activity", scope="", users=[self.agent, self.editor])

        self.assert_notifications(
            expected_json={
                "type": "tickets:activity",
                "created_on": matchers.ISODate(),
                "target_url": "/ticket/mine/",
                "is_seen": False,
            },
            expected_users={self.agent, self.editor},
            email=False,
        )

        # if a user visits their assigned tickets page, their notification is now read
        self.login(self.agent)
        self.client.get("/ticket/mine/")

        self.assertTrue(self.agent.notifications.get().is_seen)
        self.assertFalse(self.editor.notifications.get().is_seen)

    def test_incident_started(self):
        self.org.add_user(self.editor, OrgRole.ADMINISTRATOR)  # upgrade editor to administrator

        OrgFlaggedIncidentType.get_or_create(self.org)

        self.assert_notifications(
            expected_json={
                "type": "incident:started",
                "created_on": matchers.ISODate(),
                "target_url": "/incident/",
                "is_seen": False,
                "incident": {
                    "type": "org:flagged",
                    "started_on": matchers.ISODate(),
                    "ended_on": None,
                },
            },
            expected_users={self.editor, self.admin},
            email=True,
        )

        send_notification_emails()

        self.assertEqual(2, len(mail.outbox))
        self.assertEqual("[Nyaruka] Incident: Workspace Flagged", mail.outbox[0].subject)
        self.assertEqual(["admin@nyaruka.com"], mail.outbox[0].recipients())
        self.assertEqual("[Nyaruka] Incident: Workspace Flagged", mail.outbox[1].subject)
        self.assertEqual(["editor@nyaruka.com"], mail.outbox[1].recipients())

        # if a user visits the incident page, all incident notifications are now read
        self.login(self.editor)
        self.client.get("/incident/")

        self.assertTrue(self.editor.notifications.get().is_seen)
        self.assertFalse(self.admin.notifications.get().is_seen)

    def test_get_unseen_count(self):
        imp = ContactImport.objects.create(
            org=self.org, mappings={}, num_records=5, created_by=self.editor, modified_by=self.editor
        )
        Notification.create_all(
            imp.org, "import:finished", scope=f"contact:{imp.id}", users=[self.editor], contact_import=imp, medium="UE"
        )
        Notification.create_all(self.org, "tickets:opened", scope="", users=[self.agent, self.editor], medium="UE")
        Notification.create_all(self.org, "tickets:activity", scope="", users=[self.agent, self.editor], medium="UE")
        Notification.create_all(self.org, "tickets:reply", scope="12", users=[self.editor], medium="E")  # email only
        Notification.create_all(
            self.org2, "tickets:activity", scope="", users=[self.editor], medium="UE"
        )  # different org

        self.assertEqual(2, Notification.get_unseen_count(self.org, self.agent))
        self.assertEqual(3, Notification.get_unseen_count(self.org, self.editor))
        self.assertEqual(0, Notification.get_unseen_count(self.org2, self.agent))
        self.assertEqual(1, Notification.get_unseen_count(self.org2, self.editor))

        Notification.mark_seen(self.org, "tickets:activity", scope="", user=self.agent)

        self.assertEqual(1, Notification.get_unseen_count(self.org, self.agent))
        self.assertEqual(3, Notification.get_unseen_count(self.org, self.editor))
        self.assertEqual(0, Notification.get_unseen_count(self.org2, self.agent))
        self.assertEqual(1, Notification.get_unseen_count(self.org2, self.editor))

        Notification.objects.filter(org=self.org, user=self.editor, notification_type="tickets:opened").delete()

        self.assertEqual(1, Notification.get_unseen_count(self.org, self.agent))
        self.assertEqual(2, Notification.get_unseen_count(self.org, self.editor))
        self.assertEqual(0, Notification.get_unseen_count(self.org2, self.agent))
        self.assertEqual(1, Notification.get_unseen_count(self.org2, self.editor))

        squash_notification_counts()

        self.assertEqual(1, Notification.get_unseen_count(self.org, self.agent))
        self.assertEqual(2, Notification.get_unseen_count(self.org, self.editor))
        self.assertEqual(0, Notification.get_unseen_count(self.org2, self.agent))
        self.assertEqual(1, Notification.get_unseen_count(self.org2, self.editor))
