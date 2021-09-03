from datetime import datetime

from django.urls import reverse

from temba.channels.models import Alert
from temba.contacts.models import ContactImport, ExportContactsTask
from temba.orgs.models import OrgRole
from temba.tests import TembaTest, matchers

from .models import Notification


class NotificationTest(TembaTest):
    def assert_notifications(self, *, after: datetime, expected_json: dict, expected_users: set):
        notifications = Notification.objects.filter(created_on__gt=after)

        self.assertEqual(len(expected_users), notifications.count(), "notification count mismatch")

        actual_users = set()

        for notification in notifications:
            self.assertEqual(expected_json, notification.as_json())
            actual_users.add(notification.user)

        # check who was notified
        self.assertEqual(expected_users, actual_users)

    def test_channel_alert(self):
        self.org.add_user(self.editor, OrgRole.ADMINISTRATOR)  # upgrade editor to administrator

        alert1 = Alert.create_and_send(self.channel, Alert.TYPE_POWER)

        self.assert_notifications(
            after=alert1.created_on,
            expected_json={
                "type": "channel:alert",
                "created_on": matchers.ISODate(),
                "target_url": f"/channels/channel/read/{self.channel.uuid}/",
                "is_seen": False,
                "channel": {"uuid": str(self.channel.uuid), "name": "Test Channel"},
            },
            expected_users={self.admin, self.editor},
        )

        # creating another alert for the same channel won't create any new notifications
        alert2 = Alert.create_and_send(self.channel, Alert.TYPE_POWER)

        self.assert_notifications(after=alert2.created_on, expected_json={}, expected_users=set())

        # if a user clears their notifications however, they will get new ones for this channel
        self.admin.notifications.update(is_seen=True)

        alert3 = Alert.create_and_send(self.channel, Alert.TYPE_POWER)

        self.assert_notifications(
            after=alert3.created_on,
            expected_json={
                "type": "channel:alert",
                "created_on": matchers.ISODate(),
                "target_url": f"/channels/channel/read/{self.channel.uuid}/",
                "is_seen": False,
                "channel": {"uuid": str(self.channel.uuid), "name": "Test Channel"},
            },
            expected_users={self.admin},
        )

        # an alert for a different channel will also create new notifications
        vonage = self.create_channel("NX", "Vonage", "1234")
        alert4 = Alert.create_and_send(vonage, Alert.TYPE_POWER)

        self.assert_notifications(
            after=alert4.created_on,
            expected_json={
                "type": "channel:alert",
                "created_on": matchers.ISODate(),
                "target_url": f"/channels/channel/read/{vonage.uuid}/",
                "is_seen": False,
                "channel": {"uuid": str(vonage.uuid), "name": "Vonage"},
            },
            expected_users={self.admin, self.editor},
        )

    def test_export_completed(self):
        export = ExportContactsTask.create(self.org, self.editor)
        Notification.export_completed(export)

        # we only notify the user that started the export
        self.assert_notifications(
            after=export.created_on,
            expected_json={
                "type": "export:completed",
                "created_on": matchers.ISODate(),
                "target_url": f"/assets/download/contact_export/{export.id}/",
                "is_seen": False,
                "export": {"type": "contact"},
            },
            expected_users={self.editor},
        )

    def test_import_completed(self):
        imp = ContactImport.objects.create(
            org=self.org, mappings={}, num_records=5, created_by=self.editor, modified_by=self.editor
        )

        # mailroom will create these notifications when it's complete
        Notification._create_all(imp.org, "import:completed", [self.editor], target_id=imp.id, contact_import=imp)

        # we only notify the user that started the import
        self.assert_notifications(
            after=imp.created_on,
            expected_json={
                "type": "import:completed",
                "created_on": matchers.ISODate(),
                "target_url": f"/contactimport/read/{imp.id}/",
                "is_seen": False,
                "import": {"num_records": 5},
            },
            expected_users={self.editor},
        )


class NotificationCRUDLTest(TembaTest):
    def test_list(self):
        list_url = reverse("notifications.notification_list")

        # simulate an export completing
        export = ExportContactsTask.create(self.org, self.editor)
        Notification.export_completed(export)

        # not access for anon
        self.assertLoginRedirect(self.client.get(list_url))

        # check for user with no notifications
        self.login(self.user)
        response = self.client.get(list_url)
        self.assertEqual({"results": []}, response.json())

        # check for editor who should have an export completed notification
        self.login(self.editor)
        response = self.client.get(list_url)
        self.assertEqual(
            {
                "results": [
                    {
                        "type": "export:completed",
                        "created_on": matchers.ISODate(),
                        "target_url": f"/assets/download/contact_export/{export.id}/",
                        "is_seen": False,
                        "export": {"type": "contact"},
                    }
                ]
            },
            response.json(),
        )
