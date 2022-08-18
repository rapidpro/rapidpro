from django.urls import reverse

from temba.tests import CRUDLTestMixin, TembaTest

from .models import Policy


class PolicyCRUDLTest(CRUDLTestMixin, TembaTest):
    def setUp(self):
        super().setUp()

        self.privacy = Policy.objects.create(
            policy_type=Policy.TYPE_PRIVACY,
            body="Privacy matters",
            summary="Summary",
            requires_consent=True,
            created_by=self.admin,
            modified_by=self.admin,
        )

        Policy.objects.create(
            policy_type=Policy.TYPE_TOS,
            body="These are the terms",
            summary="You need to accept these",
            requires_consent=True,
            created_by=self.admin,
            modified_by=self.admin,
        )

        Policy.objects.create(
            policy_type=Policy.TYPE_COOKIE,
            body="C is for Cookie",
            summary="That's good enough for me!",
            requires_consent=False,
            created_by=self.admin,
            modified_by=self.admin,
        )

    def test_list(self):
        list_url = reverse("policies.policy_list")

        # anybody including non-logged-in users can list our policies
        response = self.client.get(list_url)
        self.assertEqual(3, response.context["object_list"].count())
        self.assertNotIn("needs_consent", response.context)

        # logged in users consent details for the two that require it
        self.login(self.admin)
        response = self.client.get(list_url)
        self.assertEqual(3, response.context["object_list"].count())
        self.assertEqual(2, response.context["needs_consent"].count())
        self.assertNotIn("consent_date", response.context)

        # give our consent
        self.client.post(reverse("policies.policy_give_consent"), dict(consent=True))
        response = self.client.get(list_url)
        self.assertEqual(0, response.context["needs_consent"].count())
        self.assertIsNotNone(response.context["consent_date"])

        # now revoke it
        self.client.post(reverse("policies.policy_give_consent"), dict(consent=False))
        response = self.client.get(list_url)
        self.assertEqual(2, response.context["needs_consent"].count())
        self.assertNotIn("consent_date", response.context)

    def test_read(self):
        privacy_url = reverse("policies.policy_read", args=["privacy"])

        # anybody including non-logged-in users can read our policies
        response = self.client.get(privacy_url)
        self.assertContains(response, "Privacy matters")

    def test_history(self):
        privacy_url = reverse("policies.policy_history", args=[self.privacy.id])

        self.assertStaffOnly(privacy_url)

    def test_admin(self):
        admin_url = reverse("policies.policy_admin")

        response = self.assertStaffOnly(admin_url)
        self.assertEqual(3, response.context["active_policies"].count())

        # publishing a new policy should deactivate the previous one
        self.client.post(
            reverse("policies.policy_create"),
            {
                "policy_type": "privacy",
                "body": "My privacy policy update",
                "summary": "the summary",
                "requires_consent": True,
            },
        )

        response = self.client.get(admin_url)
        self.assertEqual(3, response.context["active_policies"].count())
        self.assertEqual(1, response.context["object_list"].count())
