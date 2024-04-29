from datetime import timedelta

from django.contrib.auth.models import Group
from django.test import override_settings
from django.utils import timezone

from temba.api.models import APIToken, Resthook, WebHookEvent
from temba.api.tasks import trim_webhook_events
from temba.orgs.models import OrgRole
from temba.tests import TembaTest


class APITokenTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.admins_group = Group.objects.get(name="Administrators")
        self.editors_group = Group.objects.get(name="Editors")
        self.surveyors_group = Group.objects.get(name="Surveyors")

        self.org2.add_user(self.admin, OrgRole.SURVEYOR)  # our admin can act as surveyor for other org

    def test_get_or_create(self):
        token1 = APIToken.get_or_create(self.org, self.admin)
        self.assertEqual(self.org, token1.org)
        self.assertEqual(self.admin, token1.user)
        self.assertEqual(self.admins_group, token1.role)
        self.assertTrue(token1.key)
        self.assertEqual(str(token1), token1.key)

        # tokens for different roles with same user should differ
        token2 = APIToken.get_or_create(self.org, self.admin, role=OrgRole.ADMINISTRATOR)
        token3 = APIToken.get_or_create(self.org, self.admin, role=OrgRole.EDITOR)
        token4 = APIToken.get_or_create(self.org, self.admin, role=OrgRole.SURVEYOR)
        token5 = APIToken.get_or_create(self.org, self.admin, prometheus=True)

        self.assertEqual(token1, token2)
        self.assertNotEqual(token1, token3)
        self.assertNotEqual(token1, token4)
        self.assertNotEqual(token1.key, token3.key)

        self.assertEqual(self.editors_group, token3.role)
        self.assertEqual(self.surveyors_group, token4.role)
        self.assertEqual(Group.objects.get(name="Prometheus"), token5.role)

        # tokens with same role for different users should differ
        token6 = APIToken.get_or_create(self.org, self.editor)

        self.assertNotEqual(token3, token6)

        APIToken.get_or_create(self.org, self.surveyor)

        # can't create token for viewer users or other users using viewers role
        self.assertRaises(ValueError, APIToken.get_or_create, self.org, self.admin, role=OrgRole.VIEWER)
        self.assertRaises(ValueError, APIToken.get_or_create, self.org, self.user)

    def test_get_orgs_for_role(self):
        self.assertEqual(set(APIToken.get_orgs_for_role(self.admin, OrgRole.ADMINISTRATOR)), {self.org})
        self.assertEqual(set(APIToken.get_orgs_for_role(self.admin, OrgRole.SURVEYOR)), {self.org, self.org2})

    def test_is_valid(self):
        token1 = APIToken.get_or_create(self.org, self.admin, role=OrgRole.ADMINISTRATOR)
        token2 = APIToken.get_or_create(self.org, self.admin, role=OrgRole.EDITOR)
        token3 = APIToken.get_or_create(self.org, self.admin, role=OrgRole.SURVEYOR)
        token4 = APIToken.get_or_create(self.org, self.admin, prometheus=True)

        # demote admin to an editor
        self.org.add_user(self.admin, OrgRole.EDITOR)
        self.admin.refresh_from_db()

        self.assertFalse(token1.is_valid())
        self.assertTrue(token2.is_valid())
        self.assertTrue(token3.is_valid())
        self.assertFalse(token4.is_valid())

    def test_get_default_role(self):
        self.assertEqual(APIToken.get_default_role(self.org, self.admin), OrgRole.ADMINISTRATOR)
        self.assertEqual(APIToken.get_default_role(self.org, self.editor), OrgRole.EDITOR)
        self.assertEqual(APIToken.get_default_role(self.org, self.surveyor), OrgRole.SURVEYOR)
        self.assertIsNone(APIToken.get_default_role(self.org, self.user))

        # user from another org has no API roles in this org
        self.assertIsNone(APIToken.get_default_role(self.org, self.admin2))


class WebHookTest(TembaTest):
    def test_trim_events_and_results(self):
        five_hours_ago = timezone.now() - timedelta(hours=5)

        # create some events
        resthook = Resthook.get_or_create(org=self.org, slug="registration", user=self.admin)
        WebHookEvent.objects.create(org=self.org, resthook=resthook, data={}, created_on=five_hours_ago)

        with override_settings(RETENTION_PERIODS={"webhookevent": None}):
            trim_webhook_events()
            self.assertTrue(WebHookEvent.objects.all())

        with override_settings(RETENTION_PERIODS={"webhookevent": timedelta(hours=12)}):  # older than our event
            trim_webhook_events()
            self.assertTrue(WebHookEvent.objects.all())

        with override_settings(RETENTION_PERIODS={"webhookevent": timedelta(hours=2)}):
            trim_webhook_events()
            self.assertFalse(WebHookEvent.objects.all())
