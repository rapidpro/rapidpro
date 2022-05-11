import json
from unittest.mock import patch

from django.test import override_settings
from django.urls import reverse

from temba.tests import MockResponse, TembaTest

from ...models import Channel


class WhatsAppCloudTypeTest(TembaTest):
    @override_settings(
        FACEBOOK_APPLICATION_ID="FB_APP_ID",
        FACEBOOK_APPLICATION_SECRET="FB_APP_SECRET",
        WHATSAPP_FACEBOOK_BUSINESS_ID="FB_BUSINESS_ID",
    )
    def test_claim(self):
        Channel.objects.all().delete()
        self.login(self.admin)

        # remove any existing channels
        self.org.channels.update(is_active=False)

        connect_whatsapp_cloud_url = reverse("orgs.org_whatsapp_cloud_connect")
        claim_whatsapp_cloud_url = reverse("channels.types.whatsapp_cloud.claim")

        # make sure plivo is on the claim page
        response = self.client.get(reverse("channels.channel_claim"))
        self.assertEqual(200, response.status_code)
        self.assertNotContains(response, claim_whatsapp_cloud_url)

        with patch("requests.get") as wa_cloud_get:
            wa_cloud_get.return_value = MockResponse(400, {})
            response = self.client.get(claim_whatsapp_cloud_url)

            self.assertEqual(response.status_code, 302)

            response = self.client.get(claim_whatsapp_cloud_url, follow=True)

            self.assertEqual(response.request["PATH_INFO"], connect_whatsapp_cloud_url)

        session = self.client.session
        session[Channel.CONFIG_WHATSAPP_CLOUD_USER_TOKEN] = "user-token"
        session.save()

        self.assertTrue(Channel.CONFIG_WHATSAPP_CLOUD_USER_TOKEN in self.client.session)

        with patch("requests.get") as wa_cloud_get:
            with patch("requests.post") as wa_cloud_post:

                wa_cloud_get.side_effect = [
                    # pre-process for get
                    MockResponse(
                        200,
                        json.dumps(
                            {
                                "data": {
                                    "scopes": [
                                        "business_management",
                                        "whatsapp_business_management",
                                        "whatsapp_business_messaging",
                                    ]
                                }
                            }
                        ),
                    ),
                    # getting target waba
                    MockResponse(
                        200,
                        json.dumps(
                            {
                                "data": {
                                    "granular_scopes": [
                                        {
                                            "scope": "business_management",
                                            "target_ids": [
                                                "2222222222222",
                                            ],
                                        },
                                        {
                                            "scope": "whatsapp_business_management",
                                            "target_ids": [
                                                "111111111111111",
                                            ],
                                        },
                                        {
                                            "scope": "whatsapp_business_messaging",
                                            "target_ids": [
                                                "111111111111111",
                                            ],
                                        },
                                    ]
                                }
                            }
                        ),
                    ),
                    # getting waba details
                    MockResponse(
                        200,
                        json.dumps(
                            {
                                "id": "111111111111111",
                                "currency": "USD",
                                "message_template_namespace": "namespace-uuid",
                                "on_behalf_of_business_info": {"id": "2222222222222"},
                            }
                        ),
                    ),
                    # getting waba phone numbers
                    MockResponse(
                        200,
                        json.dumps(
                            {
                                "data": [
                                    {"id": "123123123", "display_phone_number": "1234", "verified_name": "WABA name"}
                                ]
                            }
                        ),
                    ),
                    # pre-process for post
                    MockResponse(
                        200,
                        json.dumps(
                            {
                                "data": {
                                    "scopes": [
                                        "business_management",
                                        "whatsapp_business_management",
                                        "whatsapp_business_messaging",
                                    ]
                                }
                            }
                        ),
                    ),
                    # getting te credit line ID
                    MockResponse(200, json.dumps({"data": [{"id": "567567567"}]})),
                ]

                wa_cloud_post.return_value = MockResponse(200, json.dumps({"success": "true"}))

                response = self.client.get(claim_whatsapp_cloud_url, follow=True)

                self.assertEqual(len(response.context["phone_numbers"]), 1)
                self.assertEqual(response.context["phone_numbers"][0]["waba_id"], "111111111111111")
                self.assertEqual(response.context["phone_numbers"][0]["phone_number_id"], "123123123")
                self.assertEqual(response.context["phone_numbers"][0]["business_id"], "2222222222222")
                self.assertEqual(response.context["phone_numbers"][0]["currency"], "USD")
                self.assertEqual(response.context["phone_numbers"][0]["verified_name"], "WABA name")

                post_data = response.context["form"].initial
                post_data["number"] = "1234"
                post_data["verified_name"] = "WABA name"
                post_data["phone_number_id"] = "123123123"
                post_data["waba_id"] = "111111111111111"
                post_data["business_id"] = "2222222222222"
                post_data["currency"] = "USD"
                post_data["message_template_namespace"] = "namespace-uuid"

                response = self.client.post(claim_whatsapp_cloud_url, post_data, follow=True)
                self.assertEqual(200, response.status_code)

                self.assertFalse(Channel.CONFIG_WHATSAPP_CLOUD_USER_TOKEN in self.client.session)

                self.assertEqual(3, wa_cloud_post.call_count)

                channel = Channel.objects.get()

                self.assertEqual("WABA name", channel.name)
                self.assertEqual("123123123", channel.address)
                self.assertEqual("WAC", channel.channel_type)
                self.assertTrue(channel.get_type().has_attachment_support(channel))

                self.assertEqual("1234", channel.config["wa_number"])
                self.assertEqual("WABA name", channel.config["wa_verified_name"])
                self.assertEqual("111111111111111", channel.config["wa_waba_id"])
                self.assertEqual("USD", channel.config["wa_currency"])
                self.assertEqual("2222222222222", channel.config["wa_business_id"])
                self.assertEqual("namespace-uuid", channel.config["wa_message_template_namespace"])
