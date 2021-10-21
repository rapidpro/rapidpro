from unittest.mock import patch

from django.urls import reverse
from django.utils import timezone

from temba.links.models import Link, LinkContacts, ExportLinksTask
from temba.links.tasks import handle_link_task, export_link_task
from temba.tests import TembaTest
from temba.utils.dates import datetime_to_timestamp
from temba.utils.uuid import uuid4


class LinkTest(TembaTest):
    def test_link_models_and_tasks(self):
        contact = self.create_contact("Test Contact")
        link = Link.objects.create(
            org=self.org,
            name="Test Link",
            destination="https://google.com",
            created_by=self.admin,
            modified_by=self.admin,
            created_on=timezone.now()
        )
        self.assertIsNotNone(link)
        self.assertIsInstance(link.as_json(), dict)
        self.assertIsInstance(link.get_url(), str)

        Link.apply_action_archive(self.admin, [link])
        link.refresh_from_db()
        self.assertEqual(link.is_archived, True)

        Link.apply_action_restore(self.admin, [link])
        link.refresh_from_db()
        self.assertEqual(link.is_archived, False)

        initial_links_count = LinkContacts.objects.count()
        handle_link_task(link.id, contact.id)
        self.assertGreater(LinkContacts.objects.count(), initial_links_count)

        day_before, day_after = timezone.now() - timezone.timedelta(days=1), timezone.now() + timezone.timedelta(days=1)
        link.get_activity(day_before, day_after, contact.name)

        links_count = Link.objects.count()
        Link.import_links(self.org, self.user, [
            {"name": "Test 2", "destination": "https://twitter.com", "uuid": uuid4()}
        ])
        self.assertGreater(Link.objects.count(), links_count)

        export = ExportLinksTask.create(self.org, self.admin, link)
        export_link_task(export.id)

    @patch("temba.orgs.views.OrgPermsMixin.has_org_perm")
    def test_link_views(self, mock_permission):
        self.login(self.admin)
        mock_permission.return_value = True

        response = self.client.get(reverse("links.link_create"), follow=True)
        self.assertEqual(response.status_code, 200)

        response = self.client.post(reverse("links.link_create"), {
            "name": "Test Link",
            "destination": "https://google.com",
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Link.objects.count(), 1)

        response = self.client.get(reverse("links.link_list"))
        self.assertEqual(response.status_code, 200)

        response = self.client.get(reverse("links.link_archived"))
        self.assertEqual(response.status_code, 200)

        link = Link.objects.first()
        response = self.client.get(reverse("links.link_read", kwargs={"uuid": link.uuid}))
        self.assertEqual(response.status_code, 200)

        response = self.client.get(reverse("links.link_history", kwargs={"uuid": link.uuid}))
        self.assertEqual(response.status_code, 200)

        yesterday, tomorrow = (
            datetime_to_timestamp(timezone.now() - timezone.timedelta(days=1)),
            datetime_to_timestamp(timezone.now() + timezone.timedelta(days=1))
        )
        response = self.client.get(f'{reverse("links.link_history", kwargs={"uuid": link.uuid})}'
                                   f'?after={yesterday}&before={tomorrow}')
        self.assertEqual(response.status_code, 200)

        response = self.client.get(reverse("links.link_api"))
        self.assertEqual(response.status_code, 200)

        response = self.client.post(reverse("links.link_export", kwargs={"pk": link.pk}), follow=True)
        self.assertEqual(response.status_code, 200)

        response = self.client.get(reverse("links.link_handler", kwargs={"uuid": link.uuid}))
        self.assertEqual(response.status_code, 302)
