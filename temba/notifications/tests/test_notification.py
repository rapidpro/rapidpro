from datetime import date, datetime, timedelta

from django.core import mail
from django.urls import reverse
from django.utils import timezone

from temba.contacts.models import ContactExport, ContactImport
from temba.flows.models import ResultsExport
from temba.msgs.models import MessageExport
from temba.notifications.incidents.builtin import ChannelTemplatesFailedIncidentType, OrgFlaggedIncidentType
from temba.notifications.models import Notification
from temba.notifications.tasks import send_notification_emails, trim_notifications
from temba.notifications.types.builtin import (
    ExportFinishedNotificationType,
    InvitationAcceptedNotificationType,
    UserEmailNotificationType,
    UserPasswordNotificationType,
)
from temba.orgs.models import Invitation, ItemCount, OrgRole
from temba.tests import TembaTest, matchers
from temba.tickets.models import TicketExport


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
        self.assertEqual(["editor@textit.com"], mail.outbox[0].recipients())

        # calling task again won't send more emails
        send_notification_emails()

        self.assertEqual(1, len(mail.outbox))

        # if a user visits the export download page, their notification for that export is now read
        self.login(self.editor)
        self.client.get(reverse("orgs.export_download", args=[export.uuid]))

        self.assertTrue(self.editor.notifications.get(export=export).is_seen)

    def test_message_export_finished(self):
        export = MessageExport.create(
            self.org, self.editor, start_date=date.today(), end_date=date.today(), system_label="I"
        )
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
                "export": {"type": "message", "num_records": 0},
            },
            expected_users={self.editor},
            email=True,
        )

        send_notification_emails()

        self.assertEqual(1, len(mail.outbox))
        self.assertEqual("[Nyaruka] Your message export is ready", mail.outbox[0].subject)
        self.assertEqual(["editor@textit.com"], mail.outbox[0].recipients())

        # calling task again won't send more emails
        send_notification_emails()

        self.assertEqual(1, len(mail.outbox))

        # if a user visits the export download page, their notification for that export is now read
        self.login(self.editor)
        self.client.get(reverse("orgs.export_download", args=[export.uuid]))

        self.assertTrue(self.editor.notifications.get(export=export).is_seen)

    def test_results_export_finished(self):
        flow1 = self.create_flow("Test Flow 1")
        flow2 = self.create_flow("Test Flow 2")
        export = ResultsExport.create(
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

        self.assertFalse(self.editor.notifications.get(export=export).is_seen)

        # we only notify the user that started the export
        self.assert_notifications(
            after=export.created_on,
            expected_json={
                "type": "export:finished",
                "created_on": matchers.ISODate(),
                "target_url": f"/export/download/{export.uuid}/",
                "is_seen": False,
                "export": {"type": "results", "num_records": 0},
            },
            expected_users={self.editor},
            email=True,
        )

        send_notification_emails()

        self.assertEqual(1, len(mail.outbox))
        self.assertEqual("[Nyaruka] Your results export is ready", mail.outbox[0].subject)
        self.assertEqual(["editor@textit.com"], mail.outbox[0].recipients())

        # calling task again won't send more emails
        send_notification_emails()

        self.assertEqual(1, len(mail.outbox))

        # if a user visits the export download page, their notification for that export is now read
        self.login(self.editor)
        self.client.get(reverse("orgs.export_download", args=[export.uuid]))

        self.assertTrue(self.editor.notifications.get(export=export).is_seen)

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
        self.assertEqual(["editor@textit.com"], mail.outbox[0].recipients())

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

    def test_channel_templates_failed(self):
        self.org.add_user(self.editor, OrgRole.ADMINISTRATOR)  # upgrade editor to administrator

        ChannelTemplatesFailedIncidentType.get_or_create(channel=self.channel)

        self.assert_notifications(
            expected_json={
                "type": "incident:started",
                "created_on": matchers.ISODate(),
                "target_url": f"/channels/channel/read/{self.channel.uuid}/",
                "is_seen": False,
                "incident": {
                    "type": "channel:templates_failed",
                    "started_on": matchers.ISODate(),
                    "ended_on": None,
                },
            },
            expected_users={self.editor, self.admin},
            email=True,
        )

        send_notification_emails()

        self.assertEqual(2, len(mail.outbox))

        self.assertEqual(2, len(mail.outbox))
        self.assertEqual("[Nyaruka] Incident: WhatsApp Templates Sync Failed", mail.outbox[0].subject)
        self.assertEqual(["admin@textit.com"], mail.outbox[0].recipients())
        self.assertEqual("[Nyaruka] Incident: WhatsApp Templates Sync Failed", mail.outbox[1].subject)
        self.assertEqual(["editor@textit.com"], mail.outbox[1].recipients())

        # if a user visits the incident page, all incident notifications are now read
        self.login(self.editor)
        self.client.get(f"/channels/channel/read/{self.channel.uuid}/")

        self.assertTrue(self.editor.notifications.get().is_seen)
        self.assertFalse(self.admin.notifications.get().is_seen)

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
        self.assertEqual(["admin@textit.com"], mail.outbox[0].recipients())
        self.assertEqual("[Nyaruka] Incident: Workspace Flagged", mail.outbox[1].subject)
        self.assertEqual(["editor@textit.com"], mail.outbox[1].recipients())

        # if a user visits the incident page, all incident notifications are now read
        self.login(self.editor)
        self.client.get("/incident/")

        self.assertTrue(self.editor.notifications.get().is_seen)
        self.assertFalse(self.admin.notifications.get().is_seen)

    def test_user_email(self):
        UserEmailNotificationType.create(self.org, self.editor, "prevaddr@trileet.com")

        self.assert_notifications(
            expected_json={
                "type": "user:email",
                "created_on": matchers.ISODate(),
                "target_url": None,
                "is_seen": True,
            },
            expected_users={self.editor},
            email=True,
        )

        send_notification_emails()

        self.assertEqual(1, len(mail.outbox))
        self.assertEqual("[Nyaruka] Your email has been changed", mail.outbox[0].subject)
        self.assertEqual(["prevaddr@trileet.com"], mail.outbox[0].recipients())  # previous address
        self.assertIn("Your email has been changed to editor@textit.com", mail.outbox[0].body)  # new address

    def test_user_password(self):
        UserPasswordNotificationType.create(self.org, self.editor)

        self.assert_notifications(
            expected_json={
                "type": "user:password",
                "created_on": matchers.ISODate(),
                "target_url": None,
                "is_seen": True,
            },
            expected_users={self.editor},
            email=True,
        )

        send_notification_emails()

        self.assertEqual(1, len(mail.outbox))
        self.assertEqual("[Nyaruka] Your password has been changed", mail.outbox[0].subject)
        self.assertEqual(["editor@textit.com"], mail.outbox[0].recipients())
        self.assertIn("Your password has been changed.", mail.outbox[0].body)

    def test_invitation_accepted(self):
        invitation = Invitation.create(self.org, self.admin, "bob@textit.com", OrgRole.ADMINISTRATOR)
        user = self.create_user("bob@textit.com")
        invitation.accept(user)

        InvitationAcceptedNotificationType.create(invitation, user)

        self.assert_notifications(
            expected_json={
                "type": "invitation:accepted",
                "created_on": matchers.ISODate(),
                "target_url": None,
                "is_seen": True,
            },
            expected_users={self.admin},
            email=True,
        )

        send_notification_emails()

        self.assertEqual(1, len(mail.outbox))
        self.assertEqual("[Nyaruka] New user joined your workspace", mail.outbox[0].subject)
        self.assertEqual(["admin@textit.com"], mail.outbox[0].recipients())  # only the other admins
        self.assertIn("User bob@textit.com accepted an invitation to join your workspace.", mail.outbox[0].body)

    def test_counts(self):
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

        def assert_count(org, user, expected: int):
            self.assertEqual(expected, Notification.get_unseen_count(org, user))

        assert_count(self.org, self.agent, 2)
        assert_count(self.org, self.editor, 3)
        assert_count(self.org2, self.agent, 0)
        assert_count(self.org2, self.editor, 1)

        Notification.mark_seen(self.org, self.agent, "tickets:activity", scope="")

        assert_count(self.org, self.agent, 1)
        assert_count(self.org, self.editor, 3)
        assert_count(self.org2, self.agent, 0)
        assert_count(self.org2, self.editor, 1)

        Notification.objects.filter(org=self.org, user=self.editor, notification_type="tickets:opened").delete()

        assert_count(self.org, self.agent, 1)
        assert_count(self.org, self.editor, 2)
        assert_count(self.org2, self.agent, 0)
        assert_count(self.org2, self.editor, 1)

        ItemCount.squash()

        assert_count(self.org, self.agent, 1)
        assert_count(self.org, self.editor, 2)
        assert_count(self.org2, self.agent, 0)
        assert_count(self.org2, self.editor, 1)

        Notification.mark_seen(self.org, self.editor)

        assert_count(self.org, self.agent, 1)
        assert_count(self.org, self.editor, 0)
        assert_count(self.org2, self.agent, 0)
        assert_count(self.org2, self.editor, 1)

    def test_trim_task(self):
        self.org.suspend()
        self.org.unsuspend()

        notification1 = self.admin.notifications.order_by("id").last()

        self.org.suspend()
        self.org.unsuspend()

        notification2 = self.admin.notifications.order_by("id").last()

        notification1.created_on = timezone.now() - timedelta(days=33)
        notification1.save(update_fields=("created_on",))

        trim_notifications()

        self.assertFalse(Notification.objects.filter(id=notification1.id).exists())
        self.assertTrue(Notification.objects.filter(id=notification2.id).exists())
