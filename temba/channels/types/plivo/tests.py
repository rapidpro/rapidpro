from unittest.mock import patch

from django.urls import reverse

from temba.channels.models import Channel
from temba.tests import MockResponse, TembaTest
from temba.utils import json

from .type import PlivoType


class PlivoTypeTest(TembaTest):
    def test_claim(self):
        self.login(self.admin)

        # remove any existing channels
        self.org.channels.update(is_active=False)

        connect_plivo_url = reverse("channels.types.plivo.connect")
        claim_plivo_url = reverse("channels.types.plivo.claim")

        # make sure plivo is on the claim page
        response = self.client.get(reverse("channels.channel_claim"))
        self.assertContains(response, "Plivo")
        self.assertContains(response, claim_plivo_url)

        with patch("requests.get") as plivo_get:
            plivo_get.return_value = MockResponse(400, {})
            response = self.client.get(claim_plivo_url)

            self.assertEqual(response.status_code, 302)

            response = self.client.get(claim_plivo_url, follow=True)

            self.assertEqual(response.request["PATH_INFO"], connect_plivo_url)

        with patch("requests.get") as plivo_get:
            plivo_get.return_value = MockResponse(400, json.dumps(dict()))

            # try hit the claim page, should be redirected; no credentials in session
            response = self.client.get(claim_plivo_url, follow=True)
            self.assertNotIn("account_trial", response.context)
            self.assertEqual(response.request["PATH_INFO"], connect_plivo_url)

        # let's add a number already connected to the account
        with patch("requests.get") as plivo_get:
            with patch("requests.post") as plivo_post:
                plivo_get.return_value = MockResponse(
                    200,
                    json.dumps(
                        dict(
                            objects=[
                                dict(number="16062681435", region="California, UNITED STATES"),
                                dict(number="8080", region="GUADALAJARA, MEXICO"),
                            ]
                        )
                    ),
                )
                plivo_post.return_value = MockResponse(202, json.dumps(dict(status="changed", app_id="app-id")))

                # make sure our numbers appear on the claim page
                response = self.client.get(claim_plivo_url)
                self.assertContains(response, "+1 606-268-1435")
                self.assertContains(response, "8080")
                self.assertContains(response, "US")
                self.assertContains(response, "MX")

                # claim it the US number
                session = self.client.session
                session[PlivoType.CONFIG_AUTH_ID] = "auth-id"
                session[PlivoType.CONFIG_AUTH_TOKEN] = "auth-token"
                session.save()

                self.assertTrue(PlivoType.CONFIG_AUTH_ID in self.client.session)
                self.assertTrue(PlivoType.CONFIG_AUTH_TOKEN in self.client.session)

                response = self.client.post(claim_plivo_url, dict(phone_number="+1 606-268-1435", country="US"))
                self.assertRedirects(response, reverse("public.public_welcome") + "?success")

                # make sure it is actually connected
                channel = Channel.objects.get(channel_type="PL", org=self.org)
                self.assertEqual(channel.role, Channel.ROLE_SEND + Channel.ROLE_RECEIVE)
                self.assertEqual(
                    channel.config,
                    {
                        PlivoType.CONFIG_AUTH_ID: "auth-id",
                        PlivoType.CONFIG_AUTH_TOKEN: "auth-token",
                        PlivoType.CONFIG_APP_ID: "app-id",
                        Channel.CONFIG_CALLBACK_DOMAIN: "app.rapidpro.io",
                    },
                )
                self.assertEqual(channel.address, "+16062681435")
                # no more credential in the session
                self.assertFalse(PlivoType.CONFIG_AUTH_ID in self.client.session)
                self.assertFalse(PlivoType.CONFIG_AUTH_TOKEN in self.client.session)

                response = self.client.get(reverse("channels.types.plivo.connect"))
                self.assertEqual(302, response.status_code)
                self.assertRedirects(response, reverse("channels.types.plivo.claim"))

                response = self.client.get(reverse("channels.types.plivo.connect") + "?reset_creds=reset")
                self.assertEqual(200, response.status_code)
                self.assertEqual(list(response.context["form"].fields.keys()), ["auth_id", "auth_token", "loc"])

        # delete existing channels
        Channel.objects.all().delete()

        with patch("temba.channels.views.requests.get") as mock_get:
            with patch("temba.channels.views.requests.post") as mock_post:
                response_body = json.dumps(
                    {
                        "status": "fulfilled",
                        "message": "created",
                        "numbers": [{"status": "Success", "number": "27816855210"}],
                        "api_id": "4334c747-9e83-11e5-9147-22000acb8094",
                    }
                )
                mock_get.side_effect = [
                    MockResponse(200, json.dumps(dict())),  # get account
                    MockResponse(400, json.dumps(dict())),  # failed get number
                    MockResponse(200, json.dumps(dict())),  # successful get number after buying it
                ]
                mock_post.side_effect = [
                    MockResponse(200, json.dumps(dict(app_id="app-id"))),  # create application
                    MockResponse(201, json.dumps(dict())),  # buy number
                    MockResponse(202, response_body),  # update number
                ]

                # claim it the US number
                session = self.client.session
                session[PlivoType.CONFIG_AUTH_ID] = "auth-id"
                session[PlivoType.CONFIG_AUTH_TOKEN] = "auth-token"
                session.save()

                self.assertTrue(PlivoType.CONFIG_AUTH_ID in self.client.session)
                self.assertTrue(PlivoType.CONFIG_AUTH_TOKEN in self.client.session)

                response = self.client.post(claim_plivo_url, dict(phone_number="+1 606-268-1440", country="US"))
                self.assertRedirects(response, reverse("public.public_welcome") + "?success")

                # make sure it is actually connected
                channel = Channel.objects.get(channel_type="PL", org=self.org)
                self.assertEqual(
                    channel.config,
                    {
                        PlivoType.CONFIG_AUTH_ID: "auth-id",
                        PlivoType.CONFIG_AUTH_TOKEN: "auth-token",
                        PlivoType.CONFIG_APP_ID: "app-id",
                        Channel.CONFIG_CALLBACK_DOMAIN: "app.rapidpro.io",
                    },
                )

                self.assertEqual(channel.address, "+16062681440")
                # no more credential in the session
                self.assertFalse(PlivoType.CONFIG_AUTH_ID in self.client.session)
                self.assertFalse(PlivoType.CONFIG_AUTH_TOKEN in self.client.session)

                self.assertEqual(mock_get.call_args_list[0][0][0], "https://api.plivo.com/v1/Account/auth-id/")
                self.assertEqual(
                    mock_get.call_args_list[1][0][0], "https://api.plivo.com/v1/Account/auth-id/Number/16062681440/"
                )
                self.assertEqual(
                    mock_get.call_args_list[2][0][0], "https://api.plivo.com/v1/Account/auth-id/Number/16062681440/"
                )

                self.assertEqual(
                    mock_post.call_args_list[0][0][0], "https://api.plivo.com/v1/Account/auth-id/Application/"
                )
                self.assertEqual(
                    mock_post.call_args_list[1][0][0],
                    "https://api.plivo.com/v1/Account/auth-id/PhoneNumber/16062681440/",
                )
                self.assertEqual(
                    mock_post.call_args_list[2][0][0], "https://api.plivo.com/v1/Account/auth-id/Number/16062681440/"
                )

        with patch("requests.delete") as mock_delete:
            channel.type.deactivate(channel)
            mock_delete.assert_called_once()

    @patch("requests.get")
    def test_search(self, mock_get):
        self.login(self.admin)

        search_url = reverse("channels.types.plivo.search")

        mock_get.return_value = MockResponse(
            200, json.dumps({"objects": [{"number": "16331111111"}, {"number": "16332222222"}]})
        )

        response = self.client.post(search_url, {"country": "US", "pattern": ""})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(["+1 633-111-1111", "+1 633-222-2222"], response.json())

        # missing key to throw exception
        mock_get.return_value = MockResponse(200, json.dumps({}))
        response = self.client.post(search_url, {"country": "US", "pattern": ""})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "error")

        mock_get.return_value = MockResponse(400, "Bad request")
        response = self.client.post(search_url, {"country": "US", "pattern": ""})

        self.assertContains(response, "Bad request")

    def test_connect_plivo(self):
        self.login(self.admin)

        # connect plivo
        connect_url = reverse("channels.types.plivo.connect")

        # simulate invalid credentials
        with patch("requests.get") as mock_get:
            mock_get.return_value = MockResponse(
                401, "Could not verify your access level for that URL." "\nYou have to login with proper credentials"
            )
            response = self.client.post(connect_url, dict(auth_id="auth-id", auth_token="auth-token"))
            self.assertContains(
                response, "Your Plivo auth ID and auth token seem invalid. Please check them again and retry."
            )
            self.assertFalse(PlivoType.CONFIG_AUTH_ID in self.client.session)
            self.assertFalse(PlivoType.CONFIG_AUTH_TOKEN in self.client.session)

        # ok, now with a success
        with patch("requests.get") as mock_get:
            mock_get.return_value = MockResponse(200, json.dumps(dict()))
            response = self.client.post(connect_url, dict(auth_id="auth-id", auth_token="auth-token"))

            # plivo should be added to the session
            self.assertEqual(self.client.session[PlivoType.CONFIG_AUTH_ID], "auth-id")
            self.assertEqual(self.client.session[PlivoType.CONFIG_AUTH_TOKEN], "auth-token")

            self.assertRedirect(response, reverse("channels.types.plivo.claim"))
