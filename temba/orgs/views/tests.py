from django.urls import reverse

from temba.tests import TembaTest


class OrgPermsMixinTest(TembaTest):
    def test_has_permission(self):
        create_url = reverse("tickets.topic_create")

        # no anon access
        self.assertLoginRedirect(self.client.get(create_url))

        # no agent role access to this specific view
        self.login(self.agent)
        self.assertLoginRedirect(self.client.get(create_url))

        # editor role does have access tho
        self.login(self.editor)
        self.assertEqual(200, self.client.get(create_url).status_code)

        # staff can't access without org
        self.login(self.customer_support)
        self.assertLoginRedirect(self.client.get(create_url))

        self.login(self.customer_support, choose_org=self.org)
        self.assertEqual(200, self.client.get(create_url).status_code)

        # staff still can't POST
        self.assertLoginRedirect(self.client.post(create_url, {"name": "Sales"}))

        # but superuser can
        self.customer_support.is_superuser = True
        self.customer_support.save(update_fields=("is_superuser",))

        self.assertEqual(200, self.client.get(create_url).status_code)
        self.assertRedirect(self.client.post(create_url, {"name": "Sales"}), "hide")
