from unittest.mock import call

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from temba.api.v2.serializers import format_datetime
from temba.msgs.models import Msg
from temba.tests import mock_mailroom

from . import APITest


class MessagesEndpointTest(APITest):
    @mock_mailroom
    def test_endpoint(self, mr_mocks):
        endpoint_url = reverse("api.v2.messages") + ".json"

        self.assertGetNotPermitted(endpoint_url, [None, self.agent])
        self.assertPostNotPermitted(endpoint_url, [None, self.user])
        self.assertDeleteNotAllowed(endpoint_url)

        joe = self.create_contact("Joe Blow", phone="+250788123123")
        frank = self.create_contact("Frank", urns=["facebook:123456"])

        hans = self.create_contact("Hans Gruber", phone="+4921551511", org=self.org2)
        self.create_channel("A", "Org2Channel", "123456", country="RW", org=self.org2)

        # create some messages
        facebook = self.create_channel("FBA", "Facebook Channel", "billy_bob")
        flow = self.create_flow("Test")
        joe_msg1 = self.create_incoming_msg(joe, "Howdy", flow=flow)
        frank_msg1 = self.create_incoming_msg(frank, "Bonjour", channel=facebook)
        joe_msg2 = self.create_outgoing_msg(joe, "How are you?", status="Q")
        frank_msg2 = self.create_outgoing_msg(frank, "Ça va?", status="D")
        joe_msg3 = self.create_incoming_msg(
            joe, "Good", flow=flow, attachments=["image/jpeg:https://example.com/test.jpg"]
        )
        frank_msg3 = self.create_incoming_msg(frank, "Bien", channel=facebook, visibility="A")
        frank_msg4 = self.create_outgoing_msg(frank, "Ça va?", status="F")

        # add a failed message with no URN or channel
        joe_msg4 = self.create_outgoing_msg(joe, "Sorry", failed_reason=Msg.FAILED_NO_DESTINATION)

        # add an unhandled message
        self.create_incoming_msg(joe, "Just in!", status="P")

        # add a deleted message
        deleted_msg = self.create_incoming_msg(frank, "!@$!%", visibility="D")

        # add message in other org
        self.create_incoming_msg(hans, "Guten tag!", channel=None)

        # label some of the messages, this will change our modified on as well for our `incoming` view
        label = self.create_label("Spam")

        # we do this in two calls so that we can predict ordering later
        label.toggle_label([frank_msg3], add=True)
        label.toggle_label([frank_msg1], add=True)
        label.toggle_label([joe_msg3], add=True)

        frank_msg1.refresh_from_db(fields=("modified_on",))
        joe_msg3.refresh_from_db(fields=("modified_on",))

        # make this message sent later than other sent message created before it to check ordering of sent messages
        frank_msg2.sent_on = timezone.now()
        frank_msg2.save(update_fields=("sent_on",))

        # default response is all messages sorted by created_on
        self.assertGet(
            endpoint_url,
            [self.user, self.editor, self.admin],
            results=[joe_msg4, frank_msg4, frank_msg3, joe_msg3, frank_msg2, joe_msg2, frank_msg1, joe_msg1],
            num_queries=self.BASE_SESSION_QUERIES + 6,
        )

        # filter by inbox
        self.assertGet(
            endpoint_url + "?folder=INBOX",
            [self.admin],
            results=[
                {
                    "id": frank_msg1.id,
                    "type": "text",
                    "channel": {"uuid": str(facebook.uuid), "name": "Facebook Channel"},
                    "contact": {"uuid": str(frank.uuid), "name": "Frank"},
                    "urn": "facebook:123456",
                    "text": "Bonjour",
                    "attachments": [],
                    "archived": False,
                    "broadcast": None,
                    "created_on": format_datetime(frank_msg1.created_on),
                    "direction": "in",
                    "flow": None,
                    "labels": [{"uuid": str(label.uuid), "name": "Spam"}],
                    "media": None,
                    "modified_on": format_datetime(frank_msg1.modified_on),
                    "sent_on": None,
                    "status": "handled",
                    "visibility": "visible",
                }
            ],
            num_queries=self.BASE_SESSION_QUERIES + 5,
        )

        # filter by incoming, should get deleted messages too
        self.assertGet(
            endpoint_url + "?folder=incoming",
            [self.admin],
            results=[joe_msg3, frank_msg1, frank_msg3, deleted_msg, joe_msg1],
        )

        # filter by other folders..
        self.assertGet(endpoint_url + "?folder=flows", [self.admin], results=[joe_msg3, joe_msg1])
        self.assertGet(endpoint_url + "?folder=archived", [self.admin], results=[frank_msg3])
        self.assertGet(endpoint_url + "?folder=outbox", [self.admin], results=[joe_msg2])
        self.assertGet(endpoint_url + "?folder=sent", [self.admin], results=[frank_msg2])
        self.assertGet(endpoint_url + "?folder=failed", [self.admin], results=[joe_msg4, frank_msg4])

        # filter by invalid folder
        self.assertGet(endpoint_url + "?folder=invalid", [self.admin], results=[])

        # filter by id
        self.assertGet(endpoint_url + f"?id={joe_msg3.id}", [self.admin], results=[joe_msg3])

        # filter by contact
        self.assertGet(
            endpoint_url + f"?contact={joe.uuid}", [self.admin], results=[joe_msg4, joe_msg3, joe_msg2, joe_msg1]
        )

        # filter by invalid contact
        self.assertGet(endpoint_url + "?contact=invalid", [self.admin], results=[])

        # filter by label UUID / name
        self.assertGet(endpoint_url + f"?label={label.uuid}", [self.admin], results=[frank_msg3, joe_msg3, frank_msg1])
        self.assertGet(endpoint_url + "?label=Spam", [self.admin], results=[frank_msg3, joe_msg3, frank_msg1])

        # filter by invalid label
        self.assertGet(endpoint_url + "?label=invalid", [self.admin], results=[])

        # filter by before (inclusive)
        self.assertGet(
            endpoint_url + f"?folder=incoming&before={format_datetime(frank_msg1.modified_on)}",
            [self.editor],
            results=[frank_msg1, frank_msg3, deleted_msg, joe_msg1],
        )

        # filter by after (inclusive)
        self.assertGet(
            endpoint_url + f"?folder=incoming&after={format_datetime(frank_msg1.modified_on)}",
            [self.editor],
            results=[joe_msg3, frank_msg1],
        )

        # filter by broadcast
        broadcast = self.create_broadcast(self.user, {"eng": {"text": "A beautiful broadcast"}}, contacts=[joe, frank])
        self.assertGet(
            endpoint_url + f"?broadcast={broadcast.id}",
            [self.editor],
            results=broadcast.msgs.order_by("-id"),
        )

        # can't filter with invalid id
        self.assertGet(endpoint_url + "?id=xyz", [self.editor], errors={None: "Value for id must be an integer"})

        # can't filter by more than one of contact, folder, label or broadcast together
        for query in (
            f"?contact={joe.uuid}&label=Spam",
            "?label=Spam&folder=inbox",
            "?broadcast=12345&folder=inbox",
            "?broadcast=12345&label=Spam",
        ):
            self.assertGet(
                endpoint_url + query,
                [self.editor],
                errors={None: "You may only specify one of the contact, folder, label, broadcast parameters"},
            )

        with self.anonymous(self.org):
            # for anon orgs, don't return URN values
            response = self.assertGet(endpoint_url + f"?id={joe_msg3.id}", [self.admin], results=[joe_msg3])
            self.assertIsNone(response.json()["results"][0]["urn"])

        # try to create a message with empty request
        self.assertPost(endpoint_url, self.admin, {}, errors={"contact": "This field is required."})

        # try to create empty message
        self.assertPost(
            endpoint_url,
            self.admin,
            {"contact": joe.uuid},
            errors={"non_field_errors": "Must provide either text or attachments."},
        )

        # create a new message with just text - which shouldn't need to read anything about the msg from the db
        response = self.assertPost(
            endpoint_url,
            self.admin,
            {"contact": joe.uuid, "text": "Interesting"},
            status=201,
        )

        msg = Msg.objects.order_by("id").last()
        self.assertEqual(
            {
                "id": msg.id,
                "type": "text",
                "channel": {"uuid": str(self.channel.uuid), "name": "Test Channel"},
                "contact": {"uuid": str(joe.uuid), "name": "Joe Blow"},
                "urn": "tel:+250788123123",
                "text": "Interesting",
                "attachments": [],
                "archived": False,
                "broadcast": None,
                "created_on": format_datetime(msg.created_on),
                "direction": "out",
                "flow": None,
                "labels": [],
                "media": None,
                "modified_on": format_datetime(msg.modified_on),
                "sent_on": None,
                "status": "queued",
                "visibility": "visible",
            },
            response.json(),
        )

        self.assertEqual(
            call(self.org, self.admin, joe, "Interesting", [], None),
            mr_mocks.calls["msg_send"][-1],
        )

        # try to create a message with an invalid attachment media UUID
        self.assertPost(
            endpoint_url,
            self.admin,
            {"contact": joe.uuid, "text": "Hi", "attachments": ["xxxx"]},
            errors={"attachments": "No such object: xxxx"},
        )

        # try to create a message with an non-existent attachment media UUID
        self.assertPost(
            endpoint_url,
            self.admin,
            {"contact": joe.uuid, "text": "Hi", "attachments": ["67ffe746-8771-40fb-89c1-5388e7ddd439"]},
            errors={"attachments": "No such object: 67ffe746-8771-40fb-89c1-5388e7ddd439"},
        )

        upload = self.upload_media(self.admin, f"{settings.MEDIA_ROOT}/test_media/steve marten.jpg")

        # create a new message with an attachment as the media UUID...
        self.assertPost(endpoint_url, self.admin, {"contact": joe.uuid, "attachments": [str(upload.uuid)]}, status=201)
        self.assertEqual(  # check that was sent via mailroom
            call(self.org, self.admin, joe, "", [f"image/jpeg:{upload.url}"], None),
            mr_mocks.calls["msg_send"][-1],
        )

        # create a new message with an attachment as <content-type>:<url>...
        self.assertPost(
            endpoint_url,
            self.admin,
            {"contact": joe.uuid, "attachments": [f"image/jpeg:https://example.com/{upload.uuid}.jpg"]},
            status=201,
        )
        self.assertEqual(
            call(self.org, self.admin, joe, "", [f"image/jpeg:{upload.url}"], None),
            mr_mocks.calls["msg_send"][-1],
        )

        # try to create a message with too many attachments
        self.assertPost(
            endpoint_url,
            self.admin,
            {"contact": joe.uuid, "attachments": [str(upload.uuid)] * 11},
            errors={"attachments": "Ensure this field has no more than 10 elements."},
        )

        # try to create an unsendable message
        billy_no_phone = self.create_contact("Billy", urns=[])
        response = self.assertPost(
            endpoint_url,
            self.admin,
            {"contact": billy_no_phone.uuid, "text": "well?"},
            status=201,
        )

        msg_json = response.json()
        self.assertIsNone(msg_json["channel"])
        self.assertIsNone(msg_json["urn"])
        self.assertEqual("failed", msg_json["status"])
