from __future__ import absolute_import, unicode_literals

from django.core.urlresolvers import reverse
from temba.contacts.models import ExportContactsTask
from temba.flows.models import ExportFlowResultsTask
from temba.msgs.models import ExportMessagesTask
from temba.tests import TembaTest


class AssetTest(TembaTest):

    def tearDown(self):
        self.clear_storage()

    def test_download(self):
        # create a message export
        message_export_task = ExportMessagesTask.objects.create(org=self.org, host='rapidpro.io',
                                                                created_by=self.admin, modified_by=self.admin)

        response = self.client.get(reverse('assets.download',
                                           kwargs=dict(type='message_export', pk=message_export_task.pk)))
        self.assertLoginRedirect(response)

        self.login(self.admin)

        # asset doesn't exist yet
        response = self.client.get(reverse('assets.download',
                                           kwargs=dict(type='message_export', pk=message_export_task.pk)))
        self.assertContains(response, "File not found", status_code=200)

        # specify wrong asset type so db object won't exist
        response = self.client.get(reverse('assets.download',
                                           kwargs=dict(type='contact_export', pk=message_export_task.pk)))
        self.assertContains(response, "File not found", status_code=200)

        # create asset and request again with correct type
        message_export_task.do_export()

        response = self.client.get(reverse('assets.download',
                                           kwargs=dict(type='message_export', pk=message_export_task.pk)))
        self.assertContains(response, "Your download should start automatically", status_code=200)

        # check direct download stream
        response = self.client.get(reverse('assets.stream',
                                           kwargs=dict(type='message_export', pk=message_export_task.pk)))
        self.assertEqual(response.status_code, 200)

        # create contact export and check that we can access it
        contact_export_task = ExportContactsTask.objects.create(org=self.org, host='rapidpro.io',
                                                                created_by=self.admin, modified_by=self.admin)
        contact_export_task.do_export()

        response = self.client.get(reverse('assets.download',
                                           kwargs=dict(type='contact_export', pk=contact_export_task.pk)))
        self.assertContains(response, "Your download should start automatically", status_code=200)

        # create flow results export and check that we can access it
        flow = self.create_flow()
        results_export_task = ExportFlowResultsTask.objects.create(org=self.org, host='rapidpro.io',
                                                                   created_by=self.admin, modified_by=self.admin)
        results_export_task.flows.add(flow)
        results_export_task.do_export()

        response = self.client.get(reverse('assets.download',
                                           kwargs=dict(type='results_export', pk=results_export_task.pk)))
        self.assertContains(response, "Your download should start automatically", status_code=200)

        # add our admin to another org
        self.create_secondary_org()
        self.org2.administrators.add(self.admin)

        self.admin.set_org(self.org2)
        s = self.client.session
        s['org_id'] = self.org2.pk
        s.save()

        # as this asset belongs to org #1, request will have that context
        response = self.client.get(reverse('assets.download',
                                           kwargs=dict(type='message_export', pk=message_export_task.pk)))
        self.assertEquals(200, response.status_code)
        user = response.context_data['view'].request.user
        self.assertEquals(user, self.admin)
        self.assertEquals(user.get_org(), self.org)

    def test_stream(self):
        # create a message export
        message_export_task = ExportMessagesTask.objects.create(org=self.org, host='rapidpro.io',
                                                                created_by=self.admin, modified_by=self.admin)

        # create asset and request again with correct type
        message_export_task.do_export()

        response = self.client.get(reverse('assets.stream',
                                           kwargs=dict(type='message_export', pk=message_export_task.pk)))

        self.assertLoginRedirect(response)

        self.login(self.admin)

        # check direct download stream
        response = self.client.get(reverse('assets.stream',
                                           kwargs=dict(type='message_export', pk=message_export_task.pk)))
        self.assertEqual(response.status_code, 200)