from django.urls import reverse

from temba.api.v2.serializers import format_datetime

from . import APITest


class ChannelEndpointTest(APITest):
    def test_endpoint(self):
        endpoint_url = reverse("api.v2.channels") + ".json"

        self.assertGetNotPermitted(endpoint_url, [None, self.agent])
        self.assertPostNotAllowed(endpoint_url)
        self.assertDeleteNotAllowed(endpoint_url)

        facebook = self.create_channel("FBA", "Facebook Channel", "billy_bob")

        # create deleted channel
        deleted = self.create_channel("JC", "Deleted", "nyaruka")
        deleted.release(self.admin)

        # create channel for other org
        self.create_channel("FBA", "Facebook Channel", "nyaruka", org=self.org2)

        # no filtering
        self.assertGet(
            endpoint_url,
            [self.user, self.editor, self.admin],
            results=[
                {
                    "uuid": str(facebook.uuid),
                    "name": "Facebook Channel",
                    "address": "billy_bob",
                    "country": None,
                    "device": None,
                    "last_seen": None,
                    "created_on": format_datetime(facebook.created_on),
                },
                {
                    "uuid": str(self.channel.uuid),
                    "name": "Test Channel",
                    "address": "+250785551212",
                    "country": "RW",
                    "device": {
                        "name": "Nexus 5X",
                        "network_type": None,
                        "power_level": -1,
                        "power_source": None,
                        "power_status": None,
                    },
                    "last_seen": format_datetime(self.channel.last_seen),
                    "created_on": format_datetime(self.channel.created_on),
                },
            ],
            num_queries=self.BASE_SESSION_QUERIES + 2,
        )

        # filter by UUID
        self.assertGet(endpoint_url + f"?uuid={facebook.uuid}", [self.admin], results=[facebook])

        # filter by address
        self.assertGet(endpoint_url + "?address=billy_bob", [self.admin], results=[facebook])
