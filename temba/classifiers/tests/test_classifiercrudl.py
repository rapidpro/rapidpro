from unittest.mock import patch

from django.urls import reverse
from django.utils import timezone

from temba.classifiers.models import Classifier
from temba.classifiers.types.luis import LuisType
from temba.classifiers.types.wit import WitType
from temba.tests import CRUDLTestMixin, TembaTest
from temba.utils.views.mixins import TEMBA_MENU_SELECTION


class ClassifierCRUDLTest(TembaTest, CRUDLTestMixin):
    def setUp(self):
        super().setUp()

        # create some classifiers
        self.c1 = Classifier.create(self.org, self.admin, WitType.slug, "Booker", {}, sync=False)
        self.c1.intents.create(name="book_flight", external_id="book_flight", created_on=timezone.now(), is_active=True)
        self.c1.intents.create(
            name="book_hotel", external_id="754569408690131", created_on=timezone.now(), is_active=False
        )
        self.c1.intents.create(
            name="book_car", external_id="754569408690533", created_on=timezone.now(), is_active=True
        )

        self.c2 = Classifier.create(self.org, self.admin, WitType.slug, "Feelings", {}, sync=False)

        self.c3 = Classifier.create(self.org, self.admin, WitType.slug, "Old Booker", {}, sync=False)
        self.c3.is_active = False
        self.c3.save()

        # on another org
        self.other_org = Classifier.create(self.org2, self.admin, LuisType.slug, "Org2 Booker", {}, sync=False)

        self.flow = self.create_flow("Color Flow")
        self.flow.classifier_dependencies.add(self.c1)

    def test_views(self):
        # fetch workspace menu
        self.login(self.admin)

        # no menu on main settings
        self.assertContentMenu(
            reverse("orgs.org_workspace"),
            self.admin,
            [],
        )

        read_url = reverse("classifiers.classifier_read", args=[self.c1.uuid])

        # read page
        response = self.client.get(read_url)
        self.assertEqual(f"/settings/classifiers/{self.c1.uuid}", response.headers[TEMBA_MENU_SELECTION])

        # contains intents
        self.assertContains(response, "book_flight")
        self.assertNotContains(response, "book_hotel")
        self.assertContains(response, "book_car")

        self.assertContentMenu(read_url, self.admin, ["Log", "Sync", "Delete"])

        self.c1.intents.all().delete()

        with patch("temba.classifiers.models.Classifier.sync") as mock_sync:
            # request a sync
            response = self.client.post(reverse("classifiers.classifier_sync", args=[self.c1.id]), follow=True)
            self.assertEqual(200, response.status_code)
            self.assertToast(response, "info", "Your classifier has been synced.")
            mock_sync.assert_called_once()

            mock_sync.side_effect = ValueError("BOOM")

            response = self.client.post(reverse("classifiers.classifier_sync", args=[self.c1.id]), follow=True)
            self.assertEqual(200, response.status_code)
            self.assertToast(response, "error", "Unable to sync classifier. See the log for details.")

    def test_read(self):
        read_url = reverse("classifiers.classifier_read", args=[self.c1.uuid])

        self.assertRequestDisallowed(read_url, [None, self.agent, self.admin2])
        response = self.assertReadFetch(read_url, [self.user, self.editor, self.admin], context_object=self.c1)

        # lists active intents
        self.assertContains(response, "book_flight")
        self.assertNotContains(response, "book_hotel")
        self.assertContains(response, "book_car")

        self.assertContentMenu(read_url, self.admin, ["Log", "Sync", "Delete"])

    def test_delete(self):
        delete_url = reverse("classifiers.classifier_delete", args=[self.c2.uuid])

        self.assertRequestDisallowed(delete_url, [None, self.user, self.editor, self.agent, self.admin2])

        # fetch delete modal
        response = self.assertDeleteFetch(delete_url, [self.admin])
        self.assertContains(response, "You are about to delete")

        response = self.assertDeleteSubmit(delete_url, self.admin, object_deactivated=self.c2, success_status=200)
        self.assertEqual("/org/workspace/", response["X-Temba-Success"])

        # should see warning if global is being used
        delete_url = reverse("classifiers.classifier_delete", args=[self.c1.uuid])

        self.assertFalse(self.flow.has_issues)

        response = self.assertDeleteFetch(delete_url, [self.admin])
        self.assertContains(response, "is used by the following items but can still be deleted:")
        self.assertContains(response, "Color Flow")

        response = self.assertDeleteSubmit(delete_url, self.admin, object_deactivated=self.c1, success_status=200)
        self.assertEqual("/org/workspace/", response["X-Temba-Success"])

        self.flow.refresh_from_db()
        self.assertTrue(self.flow.has_issues)
        self.assertNotIn(self.c1, self.flow.classifier_dependencies.all())
