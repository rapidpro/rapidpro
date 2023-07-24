from datetime import date

from django.test import override_settings
from django.urls import reverse

from temba.contacts.models import ExportContactsTask
from temba.flows.models import ExportFlowResultsTask
from temba.msgs.models import ExportMessagesTask, SystemLabel
from temba.orgs.models import OrgRole
from temba.tests import TembaTest
from temba.tickets.models import ExportTicketsTask

from .checks import storage


class AssetTest(TembaTest):
    def tearDown(self):
        self.clear_storage()

    def test_download(self):
        # create a message export
        message_export_task = ExportMessagesTask.create(
            self.org, self.admin, start_date=date.today(), end_date=date.today(), system_label=SystemLabel.TYPE_INBOX
        )

        response = self.client.get(
            reverse("assets.download", kwargs=dict(type="message_export", pk=message_export_task.pk))
        )
        self.assertLoginRedirect(response)

        self.login(self.admin)

        # asset doesn't exist yet
        response = self.client.get(
            reverse("assets.download", kwargs=dict(type="message_export", pk=message_export_task.pk))
        )
        self.assertContains(response, "File not found", status_code=200)

        # specify wrong asset type so db object won't exist
        response = self.client.get(
            reverse("assets.download", kwargs=dict(type="contact_export", pk=message_export_task.pk))
        )
        self.assertContains(response, "File not found", status_code=200)

        # create asset and request again with correct type
        message_export_task.perform()

        response = self.client.get(
            reverse("assets.download", kwargs=dict(type="message_export", pk=message_export_task.pk))
        )
        self.assertContains(response, "Your download should start automatically", status_code=200)

        # check direct download stream
        response = self.client.get(
            reverse("assets.stream", kwargs=dict(type="message_export", pk=message_export_task.pk))
        )
        self.assertEqual(response.status_code, 200)

        # create contact export and check that we can access it
        contact_export_task = ExportContactsTask.create(self.org, self.admin)
        contact_export_task.perform()

        response = self.client.get(
            reverse("assets.download", kwargs=dict(type="contact_export", pk=contact_export_task.pk))
        )
        self.assertContains(response, "Your download should start automatically", status_code=200)

        # create flow results export and check that we can access it
        flow = self.create_flow("Test")
        results_export_task = ExportFlowResultsTask.create(
            self.org,
            self.admin,
            start_date=date.today(),
            end_date=date.today(),
            flows=[flow],
            with_fields=(),
            with_groups=(),
            responded_only=False,
            extra_urns=(),
        )
        results_export_task.flows.add(flow)
        results_export_task.perform()

        response = self.client.get(
            reverse("assets.download", kwargs=dict(type="results_export", pk=results_export_task.pk))
        )
        self.assertContains(response, "Your download should start automatically", status_code=200)

        # create ticket export and check that we can access it
        ticket_export_task = ExportTicketsTask.create(
            self.org, self.admin, start_date=date.today(), end_date=date.today()
        )
        ticket_export_task.perform()

        response = self.client.get(
            reverse("assets.download", kwargs=dict(type="ticket_export", pk=ticket_export_task.pk))
        )
        self.assertContains(response, "Your download should start automatically", status_code=200)

        # add our admin to another org
        self.org2.add_user(self.admin, OrgRole.ADMINISTRATOR)

        s = self.client.session
        s["org_id"] = self.org2.id
        s.save()

        # as this asset belongs to org #1, request will have that context
        response = self.client.get(
            reverse("assets.download", kwargs=dict(type="message_export", pk=message_export_task.pk))
        )
        self.assertEqual(200, response.status_code)
        request = response.context_data["view"].request
        self.assertEqual(self.admin, request.user)
        self.assertEqual(self.org, request.org)

    def test_stream(self):
        # create a message export
        message_export_task = ExportMessagesTask.create(
            self.org, self.admin, start_date=date.today(), end_date=date.today(), system_label=SystemLabel.TYPE_INBOX
        )

        # try as anon
        response = self.client.get(
            reverse("assets.stream", kwargs=dict(type="message_export", pk=message_export_task.pk))
        )
        self.assertLoginRedirect(response)

        self.login(self.admin)

        # try with invalid object id
        response = self.client.get(reverse("assets.stream", kwargs=dict(type="message_export", pk=1234567890)))
        self.assertEqual(response.status_code, 404)

        # try before asset is generated
        response = self.client.get(
            reverse("assets.stream", kwargs=dict(type="message_export", pk=message_export_task.pk))
        )
        self.assertEqual(response.status_code, 404)

        # create asset and request again
        message_export_task.perform()

        response = self.client.get(
            reverse("assets.stream", kwargs=dict(type="message_export", pk=message_export_task.pk))
        )
        self.assertEqual(response.status_code, 200)


class SystemChecksTest(TembaTest):
    def test_storage(self):
        self.assertEqual(len(storage(None)), 0)

        with override_settings(STORAGES={"default": "x", "staticfiles": "x"}):
            self.assertEqual(storage(None)[0].msg, "Missing 'archives' storage config.")
            self.assertEqual(storage(None)[1].msg, "Missing 'logs' storage config.")
            self.assertEqual(storage(None)[2].msg, "Missing 'public' storage config.")

        with override_settings(STORAGE_URL=None):
            self.assertEqual(storage(None)[0].msg, "No storage URL set.")

        with override_settings(STORAGE_URL="http://example.com/uploads/"):
            self.assertEqual(storage(None)[0].msg, "Storage URL shouldn't end with trailing slash.")
