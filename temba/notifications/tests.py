from django.urls import reverse

from temba.channels.models import Alert
from temba.contacts.models import ContactImport, ExportContactsTask
from temba.orgs.models import OrgRole
from temba.tests import TembaTest, matchers

from .models import Log


class NotificationTest(TembaTest):
    def assert_notifications(self, expected_log_json: dict, users_notified: set):
        # check last log
        log = Log.objects.order_by("id").last()
        self.assertEqual(expected_log_json, log.as_json())

        # check who was notified
        self.assertEqual(users_notified, {n.user for n in log.notifications.all()})

    def test_channel_alert(self):
        alert = Alert.create_and_send(self.channel, Alert.TYPE_POWER)
        Log.channel_alert(alert)

        self.assert_notifications(
            {
                "type": "channel:alert",
                "created_on": matchers.ISODate(),
                "created_by": None,
                "alert": {
                    "type": "P",
                    "channel": {"uuid": str(self.channel.uuid), "name": "Test Channel"},
                },
            },
            {self.admin},
        )

    def test_imports(self):
        self.org.add_user(self.editor, OrgRole.ADMINISTRATOR)  # upgrade editor to administrator

        imp = ContactImport.objects.create(
            org=self.org, mappings={}, num_records=5, created_by=self.editor, modified_by=self.editor
        )

        Log.import_started(imp)

        # we don't notify the user that started the import but we do notify other admins
        self.assert_notifications(
            {
                "type": "import:started",
                "created_on": matchers.ISODate(),
                "created_by": {"email": "Editor@nyaruka.com", "name": ""},
                "import": {"num_records": 5},
            },
            {self.admin},
        )

        # mailroom will create this log when it's complete
        Log._create(imp.org, imp.created_by, "import:completed", contact_import=imp)

        # we only notify the user that started the import
        self.assert_notifications(
            {
                "type": "import:completed",
                "created_on": matchers.ISODate(),
                "created_by": {"email": "Editor@nyaruka.com", "name": ""},
                "import": {"num_records": 5},
            },
            {self.editor},
        )

    def test_exports(self):
        self.org.add_user(self.editor, OrgRole.ADMINISTRATOR)  # upgrade editor to administrator

        export = ExportContactsTask.create(self.org, self.editor)

        Log.export_started(export)

        # we don't notify the user that started the export but we do notify other admins
        self.assert_notifications(
            {
                "type": "export:started",
                "created_on": matchers.ISODate(),
                "created_by": {"email": "Editor@nyaruka.com", "name": ""},
                "export": {"type": "contact"},
            },
            {self.admin},
        )

        Log.export_completed(export)

        # we only notify the user that started the export
        self.assert_notifications(
            {
                "type": "export:completed",
                "created_on": matchers.ISODate(),
                "created_by": {"email": "Editor@nyaruka.com", "name": ""},
                "export": {
                    "type": "contact",
                    "download_url": f"https://app.rapidpro.io/assets/download/contact_export/{export.id}/",
                },
            },
            {self.editor},
        )


class LogCRUDLTest(TembaTest):
    def test_list(self):
        list_url = reverse("notifications.log_list")

        # simulate an export being performed
        export = ExportContactsTask.create(self.org, self.editor)
        Log.export_started(export)
        Log.export_completed(export)

        # org users can't view this
        self.login(self.admin)
        self.assertLoginRedirect(self.client.get(list_url))

        # but a customer support account servicing an org can
        self.customer_support.is_staff = True
        self.customer_support.save()
        self.login(self.customer_support)
        self.client.post(reverse("orgs.org_service"), dict(organization=self.org.id))

        response = self.client.get(list_url)
        self.assertEqual(
            {
                "results": [
                    {
                        "type": "export:completed",
                        "created_on": matchers.ISODate(),
                        "created_by": {"email": "Editor@nyaruka.com", "name": ""},
                        "export": {
                            "type": "contact",
                            "download_url": f"https://app.rapidpro.io/assets/download/contact_export/{export.id}/",
                        },
                    },
                    {
                        "type": "export:started",
                        "created_on": matchers.ISODate(),
                        "export": {"type": "contact"},
                        "created_by": {"email": "Editor@nyaruka.com", "name": ""},
                    },
                ]
            },
            response.json(),
        )


class NotificationCRUDLTest(TembaTest):
    def test_list(self):
        list_url = reverse("notifications.notification_list")

        # simulate an export being performed
        export = ExportContactsTask.create(self.org, self.editor)
        Log.export_started(export)
        Log.export_completed(export)

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
                        "log": {
                            "type": "export:completed",
                            "created_on": matchers.ISODate(),
                            "created_by": {"email": "Editor@nyaruka.com", "name": ""},
                            "export": {
                                "type": "contact",
                                "download_url": f"https://app.rapidpro.io/assets/download/contact_export/{export.id}/",
                            },
                        },
                        "is_seen": False,
                    }
                ]
            },
            response.json(),
        )
