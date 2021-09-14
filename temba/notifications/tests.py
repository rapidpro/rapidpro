from datetime import datetime

from django.urls import reverse

from temba.channels.models import Alert
from temba.contacts.models import ContactImport, ExportContactsTask
from temba.orgs.models import OrgRole
from temba.tests import TembaTest, matchers

from .models import Notification
from .tasks import squash_notificationcounts


class NotificationTest(TembaTest):
    def assert_notifications(self, *, after: datetime = None, expected_json: dict, expected_users: set):
        notifications = Notification.objects.all()
        if after:
            notifications = notifications.filter(created_on__gt=after)

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

        # if a user visits the channel read page, their notification for that channel is now read
        self.login(self.admin)
        self.client.get(reverse("channels.channel_read", kwargs={"uuid": vonage.uuid}))

        self.assertTrue(self.admin.notifications.get(channel=vonage).is_seen)
        self.assertFalse(self.editor.notifications.get(channel=vonage).is_seen)

    def test_export_finished(self):
        export = ExportContactsTask.create(self.org, self.editor)
        export.perform()

        Notification.export_finished(export)

        self.assertFalse(self.editor.notifications.get(contact_export=export).is_seen)

        # we only notify the user that started the export
        self.assert_notifications(
            after=export.created_on,
            expected_json={
                "type": "export:finished",
                "created_on": matchers.ISODate(),
                "target_url": f"/assets/download/contact_export/{export.id}/",
                "is_seen": False,
                "export": {"type": "contact"},
            },
            expected_users={self.editor},
        )

        # if a user visits the export download page, their notification for that export is now read
        self.login(self.editor)
        self.client.get(export.get_download_url())

        self.assertTrue(self.editor.notifications.get(contact_export=export).is_seen)

    def test_import_finished(self):
        imp = ContactImport.objects.create(
            org=self.org, mappings={}, num_records=5, created_by=self.editor, modified_by=self.editor
        )

        # mailroom will create these notifications when it's complete
        Notification._create_all(
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
                "import": {"num_records": 5},
            },
            expected_users={self.editor},
        )

        # if a user visits the import read page, their notification for that import is now read
        self.login(self.editor)
        self.client.get(reverse("contacts.contactimport_read", args=[imp.id]))

        self.assertTrue(self.editor.notifications.get(contact_import=imp).is_seen)

    def test_tickets_opened(self):
        # mailroom will create these notifications
        Notification._create_all(self.org, "tickets:opened", scope="", users=[self.agent, self.editor])

        self.assert_notifications(
            expected_json={
                "type": "tickets:opened",
                "created_on": matchers.ISODate(),
                "target_url": "/ticket/unassigned/",
                "is_seen": False,
            },
            expected_users={self.agent, self.editor},
        )

        # if a user visits the unassigned tickets page, their notification is now read
        self.login(self.agent)
        self.client.get("/ticket/unassigned/")

        self.assertTrue(self.agent.notifications.get().is_seen)
        self.assertFalse(self.editor.notifications.get().is_seen)

    def test_tickets_activity(self):
        # mailroom will create these notifications
        Notification._create_all(self.org, "tickets:activity", scope="", users=[self.agent, self.editor])

        self.assert_notifications(
            expected_json={
                "type": "tickets:activity",
                "created_on": matchers.ISODate(),
                "target_url": "/ticket/mine/",
                "is_seen": False,
            },
            expected_users={self.agent, self.editor},
        )

        # if a user visits their assigned tickets page, their notification is now read
        self.login(self.agent)
        self.client.get("/ticket/mine/")

        self.assertTrue(self.agent.notifications.get().is_seen)
        self.assertFalse(self.editor.notifications.get().is_seen)

    def test_get_unseen_count(self):
        imp = ContactImport.objects.create(
            org=self.org, mappings={}, num_records=5, created_by=self.editor, modified_by=self.editor
        )
        Notification._create_all(
            imp.org, "import:finished", scope=f"contact:{imp.id}", users=[self.editor], contact_import=imp
        )
        Notification._create_all(self.org, "tickets:opened", scope="", users=[self.agent, self.editor])
        Notification._create_all(self.org, "tickets:activity", scope="", users=[self.agent, self.editor])
        Notification._create_all(self.org2, "tickets:activity", scope="", users=[self.editor])  # different org

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

        squash_notificationcounts()

        self.assertEqual(1, Notification.get_unseen_count(self.org, self.agent))
        self.assertEqual(2, Notification.get_unseen_count(self.org, self.editor))
        self.assertEqual(0, Notification.get_unseen_count(self.org2, self.agent))
        self.assertEqual(1, Notification.get_unseen_count(self.org2, self.editor))


class NotificationCRUDLTest(TembaTest):
    def test_list(self):
        list_url = reverse("notifications.notification_list")

        # simulate an export finishing
        export = ExportContactsTask.create(self.org, self.editor)
        Notification.export_finished(export)

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
                        "type": "export:finished",
                        "created_on": matchers.ISODate(),
                        "target_url": f"/assets/download/contact_export/{export.id}/",
                        "is_seen": False,
                        "export": {"type": "contact"},
                    }
                ]
            },
            response.json(),
        )
