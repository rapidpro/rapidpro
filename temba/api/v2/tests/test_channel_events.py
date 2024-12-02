from django.urls import reverse

from temba.api.v2.serializers import format_datetime
from temba.channels.models import ChannelEvent

from . import APITest


class ChannelEventsEndpointTest(APITest):
    def test_endpoint(self):
        endpoint_url = reverse("api.v2.channel_events") + ".json"

        self.assertGetNotPermitted(endpoint_url, [None, self.agent])
        self.assertPostNotAllowed(endpoint_url)
        self.assertDeleteNotAllowed(endpoint_url)

        joe = self.create_contact("Joe Blow", phone="+250788123123")
        call1 = self.create_channel_event(self.channel, "tel:+250788123123", ChannelEvent.TYPE_CALL_IN_MISSED)
        call2 = self.create_channel_event(
            self.channel, "tel:+250788124124", ChannelEvent.TYPE_CALL_IN, extra=dict(duration=36)
        )
        call3 = self.create_channel_event(self.channel, "tel:+250788124124", ChannelEvent.TYPE_CALL_OUT_MISSED)
        call4 = self.create_channel_event(
            self.channel, "tel:+250788123123", ChannelEvent.TYPE_CALL_OUT, extra=dict(duration=15)
        )

        # no filtering
        response = self.assertGet(
            endpoint_url,
            [self.user, self.editor, self.admin],
            results=[call4, call3, call2, call1],
            num_queries=self.BASE_SESSION_QUERIES + 3,
        )

        resp_json = response.json()
        self.assertEqual(
            resp_json["results"][0],
            {
                "id": call4.pk,
                "channel": {"uuid": self.channel.uuid, "name": "Test Channel"},
                "type": "call-out",
                "contact": {"uuid": joe.uuid, "name": joe.name},
                "occurred_on": format_datetime(call4.occurred_on),
                "extra": dict(duration=15),
                "created_on": format_datetime(call4.created_on),
            },
        )

        # filter by id
        self.assertGet(endpoint_url + f"?id={call1.id}", [self.editor], results=[call1])

        # filter by contact
        self.assertGet(endpoint_url + f"?contact={joe.uuid}", [self.editor], results=[call4, call1])

        # filter by invalid contact
        self.assertGet(endpoint_url + "?contact=invalid", [self.editor], results=[])

        # filter by before / after
        self.assertGet(
            endpoint_url + f"?before={format_datetime(call3.created_on)}", [self.editor], results=[call3, call2, call1]
        )
        self.assertGet(
            endpoint_url + f"?after={format_datetime(call2.created_on)}", [self.editor], results=[call4, call3, call2]
        )
