from unittest.mock import call, patch

import vonage

from django.urls import reverse

from temba.channels.models import Channel
from temba.tests import MockResponse, TembaTest

from .client import VonageClient
from .type import VonageType


class VonageTypeTest(TembaTest):
    @patch("temba.channels.types.vonage.client.VonageClient.create_application")
    @patch("temba.channels.types.vonage.client.VonageClient.get_numbers")
    @patch("temba.channels.types.vonage.client.VonageClient.buy_number")
    @patch("temba.channels.types.vonage.client.VonageClient.update_number")
    def test_claim(self, mock_update_number, mock_buy_number, mock_get_numbers, mock_create_application):
        self.login(self.admin)

        claim_url = reverse("channels.types.vonage.claim")

        # remove any existing channels
        self.org.channels.update(is_active=False)

        # make sure Vonage is on the claim page
        response = self.client.get(reverse("channels.channel_claim"))
        self.assertContains(response, "Vonage")

        response = self.client.get(claim_url)
        self.assertEqual(response.status_code, 302)
        response = self.client.get(claim_url, follow=True)
        self.assertEqual(response.request["PATH_INFO"], reverse("channels.types.vonage.connect"))

        # check the connect view has no initial set
        response = self.client.get(reverse("channels.types.vonage.connect"))
        self.assertEqual(200, response.status_code)
        self.assertEqual(list(response.context["form"].fields.keys()), ["api_key", "api_secret", "loc"])
        self.assertFalse(response.context["form"].initial)

        # attach a Vonage account to the session
        session = self.client.session
        session[VonageType.SESSION_API_KEY] = "key123"
        session[VonageType.SESSION_API_SECRET] = "sesame"
        session.save()

        # hit the claim page, should now have a claim link
        response = self.client.get(reverse("channels.channel_claim"))
        self.assertContains(response, claim_url)

        # try adding a shortcode
        mock_get_numbers.side_effect = [
            [],
            [{"features": ["SMS"], "type": "mobile-lvn", "country": "US", "msisdn": "8080"}],
            [{"features": ["SMS"], "type": "mobile-lvn", "country": "US", "msisdn": "8080"}],
        ]

        response = self.client.post(claim_url, {"country": "US", "phone_number": "8080"})
        self.assertRedirects(response, reverse("public.public_welcome") + "?success")
        channel = Channel.objects.filter(address="8080").first()
        self.assertIn(Channel.ROLE_SEND, channel.role)
        self.assertIn(Channel.ROLE_RECEIVE, channel.role)
        self.assertNotIn(Channel.ROLE_ANSWER, channel.role)
        self.assertNotIn(Channel.ROLE_CALL, channel.role)
        Channel.objects.all().delete()

        # try buying a number not on the account
        mock_get_numbers.side_effect = [
            [],
            [],
            [{"features": ["SMS"], "type": "mobile", "country": "US", "msisdn": "+12065551212"}],
        ]

        response = self.client.post(claim_url, {"country": "US", "phone_number": "+12065551212"})
        self.assertRedirects(response, reverse("public.public_welcome") + "?success")

        channel = Channel.objects.filter(address="+12065551212").first()
        self.assertIn(Channel.ROLE_SEND, channel.role)
        self.assertIn(Channel.ROLE_RECEIVE, channel.role)
        Channel.objects.all().delete()

        # try failing to buy a number not on the account
        mock_get_numbers.side_effect = [[], []]
        mock_buy_number.side_effect = vonage.ClientError("nope")

        response = self.client.post(claim_url, {"country": "US", "phone_number": "+12065551212"})
        self.assertTrue(response.context["form"].errors)
        self.assertContains(
            response,
            "There was a problem claiming that number, "
            "please check the balance on your account. "
            "Note that you can only claim numbers after "
            "adding credit to your Vonage account.",
        )
        Channel.objects.all().delete()

        # let's add a number already connected to the account
        mock_get_numbers.side_effect = [
            [{"features": ["SMS", "VOICE"], "type": "mobile-lvn", "country": "US", "msisdn": "13607884540"}],
            [{"features": ["SMS", "VOICE"], "type": "mobile-lvn", "country": "US", "msisdn": "13607884540"}],
        ]
        mock_create_application.return_value = ("myappid", "private")

        # make sure our number appears on the claim page
        response = self.client.get(claim_url)
        self.assertNotIn("account_trial", response.context)
        self.assertContains(response, "360-788-4540")

        # claim it
        response = self.client.post(claim_url, {"country": "US", "phone_number": "13607884540"})
        self.assertRedirects(response, reverse("public.public_welcome") + "?success")

        # make sure it is actually connected
        channel = Channel.objects.get(channel_type="NX", org=self.org)
        self.assertIn(Channel.ROLE_SEND, channel.role)
        self.assertIn(Channel.ROLE_RECEIVE, channel.role)
        self.assertIn(Channel.ROLE_ANSWER, channel.role)
        self.assertIn(Channel.ROLE_CALL, channel.role)

        self.assertEqual(channel.config[VonageType.CONFIG_API_KEY], "key123")
        self.assertEqual(channel.config[VonageType.CONFIG_API_SECRET], "sesame")
        self.assertEqual(channel.config[VonageType.CONFIG_APP_ID], "myappid")
        self.assertEqual(channel.config[VonageType.CONFIG_APP_PRIVATE_KEY], "private")

        # check the connect view has no initial set
        response = self.client.get(reverse("channels.types.vonage.connect"))
        self.assertEqual(302, response.status_code)
        self.assertRedirects(response, reverse("channels.types.vonage.claim"))

        response = self.client.get(reverse("channels.types.vonage.connect") + "?reset_creds=reset")
        self.assertEqual(200, response.status_code)
        self.assertEqual(list(response.context["form"].fields.keys()), ["api_key", "api_secret", "loc"])

        # test the update page for vonage
        update_url = reverse("channels.channel_update", args=[channel.pk])
        response = self.client.get(update_url)

        # try changing our address
        updated = response.context["form"].initial

        response = self.client.post(update_url, updated)
        channel = Channel.objects.get(pk=channel.id)

        self.assertEqual("+13607884540", channel.address)

        # add a canada number
        mock_get_numbers.side_effect = None
        mock_get_numbers.return_value = [
            {"features": ["SMS"], "type": "mobile-lvn", "country": "CA", "msisdn": "15797884540"}
        ]

        # make sure our number appears on the claim page
        response = self.client.get(claim_url)
        self.assertNotIn("account_trial", response.context)
        self.assertContains(response, "579-788-4540")

        # claim it
        response = self.client.post(claim_url, {"country": "CA", "phone_number": "15797884540"})
        self.assertRedirects(response, reverse("public.public_welcome") + "?success")

        # make sure it is actually connected
        self.assertTrue(Channel.objects.filter(channel_type="NX", org=self.org, address="+15797884540").first())

        # as is our old one
        self.assertTrue(Channel.objects.filter(channel_type="NX", org=self.org, address="+13607884540").first())

        config_url = reverse("channels.channel_configuration", args=[channel.uuid])
        response = self.client.get(config_url)
        self.assertEqual(200, response.status_code)

        self.assertContains(response, reverse("courier.nx", args=[channel.uuid, "receive"]))
        self.assertContains(response, reverse("courier.nx", args=[channel.uuid, "status"]))
        self.assertContains(response, reverse("mailroom.ivr_handler", args=[channel.uuid, "incoming"]))

    @patch("temba.channels.types.vonage.client.VonageClient.check_credentials")
    def test_vonage_connect(self, mock_check_credentials):
        self.login(self.admin)

        connect_url = reverse("channels.types.vonage.connect")

        self.assertNotIn(VonageType.SESSION_API_KEY, self.client.session)
        self.assertNotIn(VonageType.SESSION_API_SECRET, self.client.session)

        response = self.client.get(connect_url)
        self.assertEqual(200, response.status_code)
        self.assertEqual(list(response.context["form"].fields.keys()), ["api_key", "api_secret", "loc"])
        self.assertFalse(response.context["form"].initial)

        # try posting without an account token
        post_data = {"api_key": "key"}
        response = self.client.post(connect_url, post_data)
        self.assertFormError(response.context["form"], "api_secret", "This field is required.")

        # simulate invalid credentials on both pages
        mock_check_credentials.return_value = False

        response = self.client.post(connect_url, {"api_key": "key", "api_secret": "secret"})
        self.assertContains(response, "Your API key and secret seem invalid.")
        self.assertNotIn(VonageType.SESSION_API_KEY, self.client.session)
        self.assertNotIn(VonageType.SESSION_API_SECRET, self.client.session)

        # ok, now with a success
        mock_check_credentials.return_value = True

        response = self.client.post(connect_url, {"api_key": "key", "api_secret": "secret"})
        self.assertEqual(response.status_code, 302)

        response = self.client.post(connect_url, {"api_key": "key", "api_secret": "secret"}, follow=True)
        self.assertEqual(response.request["PATH_INFO"], reverse("channels.types.vonage.claim"))

        self.assertIn(VonageType.SESSION_API_KEY, self.client.session)
        self.assertIn(VonageType.SESSION_API_SECRET, self.client.session)
        self.assertEqual(self.client.session[VonageType.SESSION_API_KEY], "key")
        self.assertEqual(self.client.session[VonageType.SESSION_API_SECRET], "secret")

    @patch("temba.channels.types.vonage.client.VonageClient.search_numbers")
    def test_search(self, mock_search_numbers):
        self.login(self.admin)
        self.org.channels.update(is_active=False)
        self.channel = Channel.create(
            self.org,
            self.user,
            "RW",
            "NX",
            None,
            "+250788123123",
            uuid="00000000-0000-0000-0000-000000001234",
            config={VonageType.CONFIG_API_KEY: "1234", VonageType.CONFIG_API_SECRET: "secret"},
        )

        # attach a Vonage account to the session
        session = self.client.session
        session[VonageType.SESSION_API_KEY] = "1234"
        session[VonageType.SESSION_API_SECRET] = "secret"
        session.save()

        search_url = reverse("channels.types.vonage.search")

        response = self.client.get(search_url)
        self.assertEqual(["country", "pattern", "loc"], list(response.context["form"].fields.keys()))

        mock_search_numbers.return_value = [
            {"features": ["SMS", "VOICE"], "type": "mobile-lvn", "country": "US", "msisdn": "13607884540"},
            {"features": ["SMS", "VOICE"], "type": "mobile-lvn", "country": "US", "msisdn": "13607884550"},
        ]

        response = self.client.post(search_url, {"country": "US", "pattern": "360"})

        self.assertEqual(["+1 360-788-4540", "+1 360-788-4550"], response.json())

    def test_deactivate(self):
        channel = self.org.channels.all().first()
        channel.channel_type = "NX"
        channel.config = {
            VonageType.CONFIG_APP_ID: "myappid",
            VonageType.CONFIG_API_KEY: "api_key",
            VonageType.CONFIG_API_SECRET: "api_secret",
            VonageType.CONFIG_APP_PRIVATE_KEY: "secret",
        }
        channel.save(update_fields=("channel_type", "config"))

        # mock a 404 response from Vonage during deactivation
        with patch("vonage.ApplicationV2.delete_application") as mock_delete_application:
            mock_delete_application.side_effect = vonage.ClientError("404 response")

            # releasing shouldn't blow up on auth failures
            channel.release(self.admin)
            channel.refresh_from_db()

            self.assertFalse(channel.is_active)

            mock_delete_application.assert_called_once_with(application_id="myappid")

    def test_update(self):
        update_url = reverse("channels.channel_update", args=[self.channel.id])

        self.login(self.admin)
        response = self.client.get(update_url)
        self.assertEqual(["name", "allow_international", "loc"], list(response.context["form"].fields.keys()))

    def test_get_error_ref_url(self):
        self.assertEqual(
            "https://developer.vonage.com/messaging/sms/guides/troubleshooting-sms",
            VonageType().get_error_ref_url(None, "send:7"),
        )
        self.assertEqual(
            "https://developer.vonage.com/messaging/sms/guides/delivery-receipts",
            VonageType().get_error_ref_url(None, "dlr:8"),
        )
        self.assertIsNone(VonageType().get_error_ref_url(None, "x:9"))


class ClientTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.client = VonageClient("abc123", "asecret")

    @patch("vonage.Client.get_balance")
    def test_check_credentials(self, mock_get_balance):
        mock_get_balance.side_effect = vonage.AuthenticationError("401 not allowed")

        self.assertFalse(self.client.check_credentials())

        mock_get_balance.side_effect = None
        mock_get_balance.return_value = "12.35"

        self.assertTrue(self.client.check_credentials())

    @patch("vonage.Client.get_account_numbers")
    def test_get_numbers(self, mock_get_account_numbers):
        mock_get_account_numbers.return_value = {"count": 2, "numbers": ["23463", "568658"]}

        self.assertEqual(self.client.get_numbers(pattern="+593"), ["23463", "568658"])

        mock_get_account_numbers.assert_called_once_with(params={"size": 10, "pattern": "593"})

    @patch("vonage.Client.get_available_numbers")
    def test_search_numbers(self, mock_get_available_numbers):
        mock_get_available_numbers.side_effect = [
            {"count": 2, "numbers": ["23463", "568658"]},
            {"count": 1, "numbers": ["34636"]},
        ]

        self.assertEqual(["23463", "568658", "34636"], self.client.search_numbers(country="EC", pattern="+593"))

        mock_get_available_numbers.assert_has_calls(
            [
                call(country_code="EC", pattern="+593", search_pattern=1, features="SMS", country="EC"),
                call(country_code="EC", pattern="+593", search_pattern=1, features="VOICE", country="EC"),
            ]
        )

    @patch("vonage.Client.buy_number")
    def test_buy_number(self, mock_buy_number):
        self.client.buy_number(country="US", number="+12345")

        mock_buy_number.assert_called_once_with(params={"msisdn": "12345", "country": "US"})

    @patch("vonage.Client.update_number")
    def test_update_number(self, mock_update_number):
        self.client.update_number(country="US", number="+12345", mo_url="http://test", app_id="ID123")

        mock_update_number.assert_called_once_with(
            params={"moHttpUrl": "http://test", "msisdn": "12345", "country": "US", "app_id": "ID123"}
        )

    @patch("vonage.ApplicationV2.create_application")
    def test_create_application(self, mock_create_application):
        mock_create_application.return_value = {"id": "myappid", "keys": {"private_key": "tejh42gf3"}}

        app_id, app_private_key = self.client.create_application("rapidpro.io", "702cb3b5-8fec-4974-a87a-75234117c768")
        self.assertEqual(app_id, "myappid")
        self.assertEqual(app_private_key, "tejh42gf3")

        app_data = {
            "name": "rapidpro.io/702cb3b5-8fec-4974-a87a-75234117c768",
            "capabilities": {
                "voice": {
                    "webhooks": {
                        "answer_url": {
                            "address": "https://rapidpro.io/mr/ivr/c/702cb3b5-8fec-4974-a87a-75234117c768/incoming",
                            "http_method": "POST",
                        },
                        "event_url": {
                            "address": "https://rapidpro.io/mr/ivr/c/702cb3b5-8fec-4974-a87a-75234117c768/status",
                            "http_method": "POST",
                        },
                    }
                }
            },
        }

        mock_create_application.assert_called_once_with(application_data=app_data)

    @patch("vonage.ApplicationV2.delete_application")
    def test_delete_application(self, mock_delete_application):
        self.client.delete_application("myappid")

        mock_delete_application.assert_called_once_with(application_id="myappid")

    @patch("temba.channels.types.vonage.client.VonageClient.RATE_LIMIT_BACKOFFS", [0.1, 0.1])
    @patch("requests.sessions.Session.get")
    def test_retry(self, mock_get):
        mock_get.side_effect = [
            MockResponse(429, "<html>429 Too Many Requests</html>", headers={"Content-Type": "text/html"}),
            MockResponse(429, "<html>429 Too Many Requests</html>", headers={"Content-Type": "text/html"}),
            MockResponse(429, "<html>429 Too Many Requests</html>", headers={"Content-Type": "text/html"}),
            MockResponse(429, "<html>429 Too Many Requests</html>", headers={"Content-Type": "text/html"}),
            MockResponse(200, '{"count": 1, "numbers": ["12345"]}', headers={"Content-Type": "application/json"}),
            MockResponse(200, '{"count": 1, "numbers": ["23456"]}', headers={"Content-Type": "application/json"}),
        ]

        # should retry twice and give up
        with self.assertRaises(vonage.ClientError):
            self.client.get_numbers()

        self.assertEqual(3, mock_get.call_count)
        mock_get.reset_mock()

        # should retry once and then succeed
        self.assertEqual(["12345"], self.client.get_numbers())
        self.assertEqual(2, mock_get.call_count)
        mock_get.reset_mock()

        # should succeed without any retries
        self.assertEqual(["23456"], self.client.get_numbers())
        self.assertEqual(1, mock_get.call_count)
