from django.contrib.auth.models import Group
from django.urls import reverse

from temba.tests import TembaTest

from ...models import IntegrationType


class ChatbaseTypeTest(TembaTest):
    def test_account(self):
        # beta gated for now
        Group.objects.get(name="Beta").user_set.add(self.admin)

        self.assertFalse(self.org.get_integrations(IntegrationType.Category.MONITORING))

        self.login(self.admin)

        account_url = reverse("integrations.chatbase.account")
        home_url = reverse("orgs.org_home")

        response = self.client.get(home_url)
        self.assertContains(response, "Connect your Chatbase account.")

        # formax includes form to connect account
        response = self.client.get(account_url, HTTP_X_FORMAX=True)
        self.assertEqual(
            ["agent_name", "api_key", "version", "disconnect", "loc"], list(response.context["form"].fields.keys())
        )

        # try to submit with missing fields
        response = self.client.post(account_url, {"version": "1.0", "disconnect": "false"})
        self.assertFormError(response, "form", "__all__", "Missing agent name or API key.")
        self.assertFalse(self.org.get_integrations(IntegrationType.Category.MONITORING))

        # now try with valid data
        self.client.post(
            account_url, {"agent_name": "Jim", "api_key": "key123", "version": "1.0", "disconnect": "false"}
        )

        # account should now be connected
        self.org.refresh_from_db()
        self.assertTrue(self.org.get_integrations(IntegrationType.Category.MONITORING))
        self.assertEqual("Jim", self.org.config["CHATBASE_AGENT_NAME"])
        self.assertEqual("key123", self.org.config["CHATBASE_API_KEY"])
        self.assertEqual("1.0", self.org.config["CHATBASE_VERSION"])

        # and that stated on home page
        response = self.client.get(home_url)
        self.assertContains(response, "Connected to your Chatbase account as agent <b>Jim</b>.")

        # formax includes the disconnect link
        response = self.client.get(account_url, HTTP_X_FORMAX=True)
        self.assertContains(response, f"{account_url}?disconnect=true")

        # now disconnect
        response = self.client.post(account_url, {"disconnect": "true"})
        self.assertNoFormErrors(response)

        self.org.refresh_from_db()
        self.assertFalse(self.org.get_integrations(IntegrationType.Category.MONITORING))
        self.assertNotIn("CHATBASE_AGENT_NAME", self.org.config)
        self.assertNotIn("CHATBASE_API_KEY", self.org.config)
        self.assertNotIn("CHATBASE_VERSION", self.org.config)
