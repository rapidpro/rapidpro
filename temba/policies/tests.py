from django.urls import reverse

from temba.tests import TembaTest

from .models import Policy


class PolicyViewTest(TembaTest):
    def setUp(self):
        super().setUp()

        Policy.objects.create(
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

    def test_policy_list(self):

        # non logged in users can visit
        response = self.client.get(reverse("policies.policy_list"))
        self.assertEqual(3, response.context["object_list"].count())
        self.assertNotIn("needs_consent", response.context)

        # logged in users consent details for the two that require it
        self.login(self.admin)
        response = self.client.get(reverse("policies.policy_list"))
        self.assertEqual(3, response.context["object_list"].count())
        self.assertEqual(2, response.context["needs_consent"].count())
        self.assertNotIn("consent_date", response.context)

        # give our consent
        self.client.post(reverse("policies.policy_give_consent"), dict(consent=True))
        response = self.client.get(reverse("policies.policy_list"))
        self.assertEqual(0, response.context["needs_consent"].count())
        self.assertIsNotNone(response.context["consent_date"])

        # now revoke it
        self.client.post(reverse("policies.policy_give_consent"), dict(consent=False))
        response = self.client.get(reverse("policies.policy_list"))
        self.assertEqual(2, response.context["needs_consent"].count())
        self.assertNotIn("consent_date", response.context)

    def test_read(self):
        # anybody can read our policies
        response = self.client.get(reverse("policies.policy_read", args=["privacy"]))
        self.assertContains(response, "Privacy matters")

    def test_admin(self):

        # have to be logged in
        response = self.client.get(reverse("policies.policy_admin"))
        self.assertRedirect(response, reverse("users.user_login"))

        # logged in admins cant see it either
        self.login(self.admin)
        response = self.client.get(reverse("policies.policy_admin"))
        self.assertRedirect(response, reverse("users.user_login"))

        # but customer service users can manage policies
        self.login(self.customer_support)
        response = self.client.get(reverse("policies.policy_admin"))
        self.assertEqual(200, response.status_code)
        self.assertEqual(3, response.context["active_policies"].count())

        # publishing a new policy should deactivate the previous one
        post_data = dict(
            policy_type="privacy", body="My privacy policy update", summary="the summary", requires_consent=True
        )
        self.client.post(reverse("policies.policy_create"), post_data)
        response = self.client.get(reverse("policies.policy_admin"))
        self.assertEqual(3, response.context["active_policies"].count())
        self.assertEqual(1, response.context["object_list"].count())

    # def test_consent_middleware(self):
    # middleware should reroute to ask for consent
    # self.login(self.admin)
    # response = self.client.get(reverse('msgs.msg_inbox'))
    # self.assertRedirect(response, reverse('policies.policy_list'))
