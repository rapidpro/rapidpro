from datetime import timedelta

from django.contrib.auth.models import Group
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from temba.api.models import APIToken, Resthook, WebHookEvent, WebHookResult
from temba.api.tasks import trim_webhook_event_task
from temba.tests import TembaTest


class APITokenTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.admins_group = Group.objects.get(name="Administrators")
        self.editors_group = Group.objects.get(name="Editors")
        self.surveyors_group = Group.objects.get(name="Surveyors")

        self.org2.surveyors.add(self.admin)  # our admin can act as surveyor for other org

    def test_get_or_create(self):
        token1 = APIToken.get_or_create(self.org, self.admin)
        self.assertEqual(token1.org, self.org)
        self.assertEqual(token1.user, self.admin)
        self.assertEqual(token1.role, self.admins_group)
        self.assertTrue(token1.key)
        self.assertEqual(str(token1), token1.key)

        # tokens for different roles with same user should differ
        token2 = APIToken.get_or_create(self.org, self.admin, self.admins_group)
        token3 = APIToken.get_or_create(self.org, self.admin, self.editors_group)
        token4 = APIToken.get_or_create(self.org, self.admin, self.surveyors_group)

        self.assertEqual(token1, token2)
        self.assertNotEqual(token1, token3)
        self.assertNotEqual(token1, token4)
        self.assertNotEqual(token1.key, token3.key)

        # tokens with same role for different users should differ
        token5 = APIToken.get_or_create(self.org, self.editor)

        self.assertNotEqual(token3, token5)

        APIToken.get_or_create(self.org, self.surveyor)

        # can't create token for viewer users or other users using viewers role
        self.assertRaises(ValueError, APIToken.get_or_create, self.org, self.admin, Group.objects.get(name="Viewers"))
        self.assertRaises(ValueError, APIToken.get_or_create, self.org, self.user)

    def test_get_orgs_for_role(self):
        self.assertEqual(set(APIToken.get_orgs_for_role(self.admin, self.admins_group)), {self.org})
        self.assertEqual(set(APIToken.get_orgs_for_role(self.admin, self.surveyors_group)), {self.org, self.org2})

    def test_get_allowed_roles(self):
        self.assertEqual(
            set(APIToken.get_allowed_roles(self.org, self.admin)),
            {self.admins_group, self.editors_group, self.surveyors_group},
        )
        self.assertEqual(
            set(APIToken.get_allowed_roles(self.org, self.editor)), {self.editors_group, self.surveyors_group}
        )
        self.assertEqual(set(APIToken.get_allowed_roles(self.org, self.surveyor)), {self.surveyors_group})
        self.assertEqual(set(APIToken.get_allowed_roles(self.org, self.user)), set())

        # user from another org has no API roles
        self.assertEqual(set(APIToken.get_allowed_roles(self.org, self.admin2)), set())

    def test_get_default_role(self):
        self.assertEqual(APIToken.get_default_role(self.org, self.admin), self.admins_group)
        self.assertEqual(APIToken.get_default_role(self.org, self.editor), self.editors_group)
        self.assertEqual(APIToken.get_default_role(self.org, self.surveyor), self.surveyors_group)
        self.assertIsNone(APIToken.get_default_role(self.org, self.user))

        # user from another org has no API roles
        self.assertIsNone(APIToken.get_default_role(self.org, self.admin2))


class WebHookTest(TembaTest):
    def test_trim_events_and_results(self):
        five_hours_ago = timezone.now() - timedelta(hours=5)

        # create some events and results
        resthook = Resthook.get_or_create(org=self.org, slug="registration", user=self.admin)
        WebHookEvent.objects.create(org=self.org, resthook=resthook, data={}, created_on=five_hours_ago)
        WebHookResult.objects.create(org=self.org, status_code=200, created_on=five_hours_ago)

        with override_settings(SUCCESS_LOGS_TRIM_TIME=0):
            trim_webhook_event_task()
            self.assertTrue(WebHookEvent.objects.all())
            self.assertTrue(WebHookResult.objects.all())

        with override_settings(SUCCESS_LOGS_TRIM_TIME=12):
            trim_webhook_event_task()
            self.assertTrue(WebHookEvent.objects.all())
            self.assertTrue(WebHookResult.objects.all())

        with override_settings(SUCCESS_LOGS_TRIM_TIME=2):
            trim_webhook_event_task()
            self.assertFalse(WebHookEvent.objects.all())
            self.assertFalse(WebHookResult.objects.all())

        WebHookEvent.objects.create(org=self.org, resthook=resthook, data={}, created_on=five_hours_ago)
        WebHookResult.objects.create(org=self.org, status_code=200, created_on=five_hours_ago)
        WebHookResult.objects.create(org=self.org, status_code=401, created_on=five_hours_ago)

        with override_settings(ALL_LOGS_TRIM_TIME=0):
            trim_webhook_event_task()
            self.assertTrue(WebHookEvent.objects.all())
            self.assertTrue(WebHookResult.objects.all())

        with override_settings(ALL_LOGS_TRIM_TIME=12):
            trim_webhook_event_task()
            self.assertTrue(WebHookEvent.objects.all())
            self.assertTrue(WebHookResult.objects.all())

        with override_settings(ALL_LOGS_TRIM_TIME=2):
            trim_webhook_event_task()
            self.assertFalse(WebHookEvent.objects.all())
            self.assertFalse(WebHookResult.objects.all())


class WebHookCRUDLTest(TembaTest):
    def test_list(self):
        res1 = WebHookResult.objects.create(org=self.org, status_code=200, created_on=timezone.now())
        res2 = WebHookResult.objects.create(org=self.org, status_code=201, created_on=timezone.now())
        res3 = WebHookResult.objects.create(org=self.org, status_code=202, created_on=timezone.now())
        res4 = WebHookResult.objects.create(org=self.org, status_code=404, created_on=timezone.now())

        # create result for other org
        WebHookResult.objects.create(org=self.org2, status_code=200, created_on=timezone.now())

        url = reverse("api.webhookresult_list")

        response = self.fetch_protected(url, self.admin)
        self.assertEqual([res4, res3, res2, res1], list(response.context["object_list"]))
