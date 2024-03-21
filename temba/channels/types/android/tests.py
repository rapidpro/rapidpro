import json

from django.urls import reverse

from temba.contacts.models import URN
from temba.orgs.models import Org
from temba.tests import CRUDLTestMixin, TembaTest
from temba.utils import get_anonymous_user

from ...models import Channel


class AndroidTypeTest(TembaTest, CRUDLTestMixin):
    def test_claim(self):
        # remove our explicit country so it needs to be derived from channels
        self.org.country = None
        self.org.timezone = "UTC"
        self.org.save(update_fields=("country", "timezone"))

        Channel.objects.all().delete()

        reg_data = dict(cmds=[dict(cmd="fcm", fcm_id="FCM111", uuid="uuid"), dict(cmd="status", cc="RW", dev="Nexus")])

        # must be a post
        response = self.client.get(reverse("register"), content_type="application/json")
        self.assertEqual(500, response.status_code)

        # try a legit register
        response = self.client.post(reverse("register"), json.dumps(reg_data), content_type="application/json")
        self.assertEqual(200, response.status_code)

        android1 = Channel.objects.get()
        self.assertIsNone(android1.org)
        self.assertIsNone(android1.address)
        self.assertEqual(android1.country, "RW")
        self.assertEqual(android1.device, "Nexus")
        self.assertEqual(android1.config["FCM_ID"], "FCM111")
        self.assertEqual(android1.uuid, "uuid")
        self.assertTrue(android1.secret)
        self.assertTrue(android1.claim_code)
        self.assertEqual(android1.created_by, get_anonymous_user())

        # check channel JSON in response
        response_json = response.json()
        self.assertEqual(
            response_json,
            dict(
                cmds=[
                    dict(
                        cmd="reg",
                        relayer_claim_code=android1.claim_code,
                        relayer_secret=android1.secret,
                        relayer_id=android1.id,
                    )
                ]
            ),
        )

        # try registering again with same details
        response = self.client.post(reverse("register"), json.dumps(reg_data), content_type="application/json")
        self.assertEqual(response.status_code, 200)

        android1 = Channel.objects.get()
        response_json = response.json()

        self.assertEqual(
            response_json,
            dict(
                cmds=[
                    dict(
                        cmd="reg",
                        relayer_claim_code=android1.claim_code,
                        relayer_secret=android1.secret,
                        relayer_id=android1.id,
                    )
                ]
            ),
        )

        # view claim page
        self.login(self.admin)
        response = self.client.get(reverse("channels.types.android.claim"))
        self.assertContains(response, "https://app.rapidpro.io/android/")

        # try to claim as non-admin
        self.login(self.user)
        response = self.client.post(
            reverse("channels.types.android.claim"), dict(claim_code=android1.claim_code, phone_number="0788123123")
        )
        self.assertLoginRedirect(response)

        # try to claim with an invalid phone number
        self.login(self.admin)
        response = self.client.post(
            reverse("channels.types.android.claim"), dict(claim_code=android1.claim_code, phone_number="078123")
        )
        self.assertEqual(response.status_code, 200)
        self.assertFormError(response.context["form"], "phone_number", "Invalid phone number, try again.")

        # Add a Dialog360 whatsapp channel and bulk sender channel that should not block us to claim an Android channel
        channel = self.create_channel(
            "D3",
            "360Dialog channel",
            address="+250788123123",
            country="RW",
            schemes=[URN.WHATSAPP_SCHEME],
            config={
                Channel.CONFIG_BASE_URL: "https://example.com/whatsapp",
                Channel.CONFIG_AUTH_TOKEN: "123456789",
            },
        )
        Channel.create(
            self.org, self.admin, "RW", "NX", "", "+250788123123", schemes=[URN.TEL_SCHEME], role=Channel.ROLE_SEND
        )

        # claim our channel
        response = self.client.post(
            reverse("channels.types.android.claim"), dict(claim_code=android1.claim_code, phone_number="0788123123")
        )

        # redirect to welcome page
        self.assertIn("success", response.get("Location", None))
        self.assertRedirect(response, reverse("public.public_welcome"))

        # channel is updated with org details and claim code is now blank
        android1.refresh_from_db()
        secret = android1.secret
        self.assertEqual(android1.org, self.org)
        self.assertEqual(android1.address, "+250788123123")  # normalized
        self.assertEqual(android1.config["FCM_ID"], "FCM111")
        self.assertEqual(android1.uuid, "uuid")
        self.assertFalse(android1.claim_code)

        # try having a device register again
        response = self.client.post(reverse("register"), json.dumps(reg_data), content_type="application/json")
        self.assertEqual(response.status_code, 200)

        # should return same channel but with a new claim code and secret
        android1.refresh_from_db()
        self.assertEqual(android1.org, self.org)
        self.assertEqual(android1.address, "+250788123123")
        self.assertEqual(android1.config["FCM_ID"], "FCM111")
        self.assertEqual(android1.uuid, "uuid")
        self.assertEqual(android1.is_active, True)
        self.assertTrue(android1.claim_code)
        self.assertNotEqual(android1.secret, secret)

        # should be able to claim again
        response = self.client.post(
            reverse("channels.types.android.claim"), dict(claim_code=android1.claim_code, phone_number="0788123123")
        )
        self.assertRedirect(response, reverse("public.public_welcome"))

        # try having a device register yet again with new FCM ID
        reg_data["cmds"][0]["fcm_id"] = "FCM222"
        response = self.client.post(reverse("register"), json.dumps(reg_data), content_type="application/json")
        self.assertEqual(response.status_code, 200)

        # should return same channel but with FCM updated
        android1.refresh_from_db()
        self.assertEqual(android1.org, self.org)
        self.assertEqual(android1.address, "+250788123123")
        self.assertEqual(android1.config["FCM_ID"], "FCM222")
        self.assertEqual(android1.uuid, "uuid")
        self.assertEqual(android1.is_active, True)

        # we can claim again with new phone number
        response = self.client.post(
            reverse("channels.types.android.claim"), dict(claim_code=android1.claim_code, phone_number="+250788123124")
        )
        self.assertRedirect(response, reverse("public.public_welcome"))

        android1.refresh_from_db()
        self.assertEqual(android1.org, self.org)
        self.assertEqual(android1.address, "+250788123124")
        self.assertEqual(android1.config["FCM_ID"], "FCM222")
        self.assertEqual(android1.uuid, "uuid")
        self.assertEqual(android1.is_active, True)

        # release and then register with same details and claim again
        old_uuid = android1.uuid
        android1.release(self.admin)

        response = self.client.post(reverse("register"), json.dumps(reg_data), content_type="application/json")
        claim_code = response.json()["cmds"][0]["relayer_claim_code"]
        self.assertEqual(response.status_code, 200)
        response = self.client.post(
            reverse("channels.types.android.claim"), dict(claim_code=claim_code, phone_number="+250788123124")
        )
        self.assertRedirect(response, reverse("public.public_welcome"))

        android1.refresh_from_db()

        self.assertNotEqual(android1.uuid, old_uuid)  # inactive channel now has new UUID

        # and we have a new Android channel with our UUID
        android2 = Channel.objects.filter(is_active=True, channel_type="A").first()
        self.assertNotEqual(android2, android1)
        self.assertEqual(android2.uuid, "uuid")

        # try to claim a bogus channel
        response = self.client.post(reverse("channels.types.android.claim"), dict(claim_code="Your Mom"))
        self.assertEqual(response.status_code, 200)
        self.assertFormError(response.context["form"], "claim_code", "Invalid claim code, please check and try again.")

        # check our primary tel channel is the same as our outgoing
        default_sender = self.org.get_send_channel(URN.TEL_SCHEME)
        self.assertEqual(default_sender, android2)
        self.assertEqual(default_sender, self.org.get_receive_channel(URN.TEL_SCHEME))

        # re-register device with country as US
        reg_data = dict(
            cmds=[dict(cmd="fcm", fcm_id="FCM222", uuid="uuid"), dict(cmd="status", cc="US", dev="Nexus 5X")]
        )
        response = self.client.post(reverse("register"), json.dumps(reg_data), content_type="application/json")
        self.assertEqual(response.status_code, 200)

        # channel country and device updated
        android2.refresh_from_db()
        self.assertEqual(android2.country, "US")
        self.assertEqual(android2.device, "Nexus 5X")
        self.assertEqual(android2.org, self.org)
        self.assertEqual(android2.config["FCM_ID"], "FCM222")
        self.assertEqual(android2.uuid, "uuid")
        self.assertTrue(android2.is_active)

        # set back to RW...
        android2.country = "RW"
        android2.save()

        # our country is RW
        self.assertEqual(self.org.default_country_code, "RW")

        self.assertEqual(self.org.default_country_code, "RW")

        # register another device with country as US
        reg_data = dict(
            cmds=[dict(cmd="fcm", fcm_id="FCM444", uuid="uuid4"), dict(cmd="status", cc="US", dev="Nexus 6P")]
        )
        response = self.client.post(reverse("register"), json.dumps(reg_data), content_type="application/json")

        claim_code = response.json()["cmds"][0]["relayer_claim_code"]

        # try to claim it...
        self.client.post(
            reverse("channels.types.android.claim"), dict(claim_code=claim_code, phone_number="12065551212")
        )

        # should work, can have two channels in different countries
        channel = Channel.objects.get(country="US")
        self.assertEqual(channel.address, "+12065551212")

        self.assertEqual(Channel.objects.filter(org=self.org, is_active=True).count(), 4)

        # normalize a URN with a fully qualified number
        normalized = URN.normalize_number("+12061112222", "")
        self.assertEqual("+12061112222", normalized)

        # not international format
        normalized = URN.normalize_number("0788383383", "")
        self.assertEqual("0788383383", normalized)

        # get our send channel without a URN, should just default to last
        default_channel = self.org.get_send_channel(URN.TEL_SCHEME)
        self.assertEqual(default_channel, channel)

        self.org = Org.objects.get(id=self.org.id)
        self.assertEqual("", self.org.default_country_code)

        # yet another registration in rwanda
        reg_data = dict(
            cmds=[dict(cmd="fcm", fcm_id="FCM555", uuid="uuid5"), dict(cmd="status", cc="RW", dev="Nexus 5")]
        )
        response = self.client.post(reverse("register"), json.dumps(reg_data), content_type="application/json")
        claim_code = response.json()["cmds"][0]["relayer_claim_code"]

        # try to claim it with number taken by other Android channel
        response = self.client.post(
            reverse("channels.types.android.claim"), dict(claim_code=claim_code, phone_number="+250788123124")
        )
        self.assertFormError(
            response.context["form"],
            "phone_number",
            "Another channel has this number. Please remove that channel first.",
        )

        # create channel in another org
        Channel.create(self.org2, self.admin2, "RW", "A", "", "+250788382382")

        # can claim it with this number, and because it's a fully qualified RW number, doesn't matter that channel is US
        response = self.client.post(
            reverse("channels.types.android.claim"), dict(claim_code=claim_code, phone_number="+250788382382")
        )
        self.assertRedirect(response, reverse("public.public_welcome"))

        # should be added with RW as the country
        self.assertTrue(Channel.objects.get(address="+250788382382", country="RW", org=self.org))

    def test_update(self):
        update_url = reverse("channels.channel_update", args=[self.channel.id])

        self.login(self.admin)
        response = self.client.get(update_url)
        self.assertEqual(["name", "allow_international", "loc"], list(response.context["form"].fields.keys()))
