from django.urls import reverse

from temba.tests import TembaTest


class DashboardTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.user = self.create_user("tito")

    def create_activity(self):
        # and some message and call activity
        joe = self.create_contact("Joe", phone="+593979099111")
        self.create_outgoing_msg(joe, "Tea of coffee?")
        self.create_incoming_msg(joe, "Coffee")
        self.create_outgoing_msg(joe, "OK")
        self.create_outgoing_msg(joe, "Wanna hang?", voice=True)
        self.create_incoming_msg(joe, "Sure", voice=True)

    def test_dashboard_home(self):
        dashboard_url = reverse("dashboard.dashboard_home")

        # visit this page without authenticating
        response = self.client.get(dashboard_url, follow=True)

        # nope! cannot visit dashboard.
        self.assertRedirects(response, "/users/login/?next=%s" % dashboard_url)

        self.login(self.admin)
        response = self.client.get(dashboard_url, follow=True)

        # yep! it works
        self.assertEqual(response.request["PATH_INFO"], dashboard_url)

    def test_message_history(self):
        url = reverse("dashboard.dashboard_message_history")

        # visit this page without authenticating
        response = self.client.get(url, follow=True)

        # nope!
        self.assertRedirects(response, "/users/login/?next=%s" % url)

        self.login(self.admin)
        self.create_activity()
        response = self.client.get(url).json()

        # in, out
        self.assertEqual(2, len(response))

        # incoming messages
        self.assertEqual(2, response[0]["data"][0][1])

        # outgoing messages
        self.assertEqual(3, response[1]["data"][0][1])

    def test_workspace_stats(self):
        url = reverse("dashboard.dashboard_workspace_stats")

        # visit this page without authenticating
        response = self.client.get(url, follow=True)

        # nope!
        self.assertRedirects(response, "/users/login/?next=%s" % url)

        self.login(self.admin)
        self.create_activity()
        response = self.client.get(url).json()

        self.assertEqual(5, len(response))
        self.assertEqual(2, response[0]["data"][0][1])  # incoming messages
        self.assertEqual(3, response[2]["data"][0][1])  # outgoing messages
        self.assertEqual(5, response[4]["data"][0][1])  # total messages

    def test_channel_types_stats(self):
        url = reverse("dashboard.dashboard_channel_types_stats")

        # visit this page without authenticating
        response = self.client.get(url, follow=True)

        # nope!
        self.assertRedirects(response, "/users/login/?next=%s" % url)

        self.login(self.admin)
        self.create_activity()
        types = ["T", "TWT", "FB", "NX", "AT", "KN"]
        michael = self.create_contact("Michael", urns=["twitter:mjackson"])
        for t in types:
            channel = self.create_channel(t, f"Test Channel {t}", f"{t}:1234")
            self.create_outgoing_msg(michael, f"Message on {t}", channel=channel)
        response = self.client.get(url).json()

        self.assertEqual([1, 1], response[0]["data"])
        self.assertEqual("Android Incoming", response[0]["name"])
        self.assertEqual([2, 1], response[1]["data"])
        self.assertEqual("Android Outgoing", response[1]["name"])
        self.assertEqual([], response[2]["data"])
        self.assertEqual("Africa's Talking Incoming", response[2]["name"])
        self.assertEqual([1], response[3]["data"])
        self.assertEqual("Africa's Talking Outgoing", response[3]["name"])
        self.assertEqual([], response[4]["data"])
        self.assertEqual("Facebook Incoming", response[4]["name"])
        self.assertEqual([1], response[5]["data"])
        self.assertEqual("Facebook Outgoing", response[5]["name"])
        self.assertEqual([], response[6]["data"])
        self.assertEqual("Kannel Incoming", response[6]["name"])
        self.assertEqual([1], response[7]["data"])
        self.assertEqual("Kannel Outgoing", response[7]["name"])
        self.assertEqual([], response[8]["data"])
        self.assertEqual("Vonage Incoming", response[8]["name"])
        self.assertEqual([1], response[9]["data"])
        self.assertEqual("Vonage Outgoing", response[9]["name"])
        self.assertEqual([], response[10]["data"])
        self.assertEqual("Twilio Incoming", response[10]["name"])
        self.assertEqual([1], response[11]["data"])
        self.assertEqual("Twilio Outgoing", response[11]["name"])
        self.assertEqual([], response[12]["data"])
        self.assertEqual("Twitter Incoming", response[12]["name"])
        self.assertEqual([1], response[13]["data"])
        self.assertEqual("Twitter Outgoing", response[13]["name"])

    def test_range_details(self):
        url = reverse("dashboard.dashboard_range_details")

        # visit this page without authenticating
        response = self.client.get(url, follow=True)

        # nope!
        self.assertRedirects(response, "/users/login/?next=%s" % url)

        self.login(self.admin)
        self.create_activity()

        types = ["T", "TWT", "FB", "NX", "AT", "KN"]
        michael = self.create_contact("Michael", urns=["twitter:mjackson"])
        for t in types:
            channel = self.create_channel(t, f"Test Channel {t}", f"{t}:1234")
            self.create_outgoing_msg(michael, f"Message on {t}", channel=channel)
        response = self.client.get(url)

        # org message activity
        self.assertEqual(11, response.context["orgs"][0]["count_sum"])
        self.assertEqual("Nyaruka", response.context["orgs"][0]["channel__org__name"])

        # our pie chart
        self.assertEqual(5, response.context["channel_types"][0]["count_sum"])
        self.assertEqual("Android", response.context["channel_types"][0]["channel__name"])
        self.assertEqual(7, len(response.context["channel_types"]))
        self.assertEqual("Other", response.context["channel_types"][6]["channel__name"])
