from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from temba.api.v2.serializers import format_datetime
from temba.msgs.models import Broadcast
from temba.orgs.models import Org
from temba.schedules.models import Schedule
from temba.tests import mock_mailroom

from . import APITest


class BroadcastsEndpointTest(APITest):
    @mock_mailroom
    def test_endpoint(self, mr_mocks):
        endpoint_url = reverse("api.v2.broadcasts") + ".json"

        self.assertGetNotPermitted(endpoint_url, [None, self.agent])
        self.assertPostNotPermitted(endpoint_url, [None, self.user, self.agent])
        self.assertDeleteNotAllowed(endpoint_url)

        self.create_channel("FBA", "Facebook Channel", "billy_bob")
        joe = self.create_contact("Joe Blow", phone="+250788123123")
        frank = self.create_contact("Frank", urns=["facebook:123456"])
        reporters = self.create_group("Reporters", [joe, frank])

        hans = self.create_contact("Hans Gruber", phone="+4921551511", org=self.org2)
        self.create_channel("A", "Org2Channel", "123456", country="RW", org=self.org2)

        bcast1 = self.create_broadcast(
            self.admin, {"eng": {"text": "Hello 1"}}, urns=["facebook:12345"], status=Broadcast.STATUS_PENDING
        )
        bcast2 = self.create_broadcast(
            self.admin, {"eng": {"text": "Hello 2"}}, contacts=[joe], status=Broadcast.STATUS_PENDING
        )
        bcast3 = self.create_broadcast(
            self.admin, {"eng": {"text": "Hello 3"}}, contacts=[frank], status=Broadcast.STATUS_COMPLETED
        )
        bcast4 = self.create_broadcast(
            self.admin,
            {"eng": {"text": "Hello 4"}},
            urns=["facebook:12345"],
            contacts=[joe],
            groups=[reporters],
            status=Broadcast.STATUS_FAILED,
        )
        self.create_broadcast(
            self.admin,
            {"eng": {"text": "Scheduled"}},
            contacts=[joe],
            schedule=Schedule.create(self.org, timezone.now(), Schedule.REPEAT_DAILY),
        )
        self.create_broadcast(self.admin2, {"eng": {"text": "Different org..."}}, contacts=[hans], org=self.org2)

        # no filtering
        response = self.assertGet(
            endpoint_url,
            [self.user, self.editor, self.admin],
            results=[bcast4, bcast3, bcast2, bcast1],
            num_queries=self.BASE_SESSION_QUERIES + 4,
        )
        resp_json = response.json()

        self.assertEqual(
            {
                "id": bcast2.id,
                "status": "pending",
                "progress": {"total": -1, "started": 0},
                "urns": [],
                "contacts": [{"uuid": joe.uuid, "name": joe.name}],
                "groups": [],
                "text": {"eng": "Hello 2"},
                "attachments": {"eng": []},
                "base_language": "eng",
                "created_on": format_datetime(bcast2.created_on),
            },
            resp_json["results"][2],
        )
        self.assertEqual(
            {
                "id": bcast4.id,
                "status": "failed",
                "progress": {"total": 2, "started": 2},
                "urns": ["facebook:12345"],
                "contacts": [{"uuid": joe.uuid, "name": joe.name}],
                "groups": [{"uuid": reporters.uuid, "name": reporters.name}],
                "text": {"eng": "Hello 4"},
                "attachments": {"eng": []},
                "base_language": "eng",
                "created_on": format_datetime(bcast4.created_on),
            },
            resp_json["results"][0],
        )

        # filter by id
        self.assertGet(endpoint_url + f"?id={bcast3.id}", [self.editor], results=[bcast3])

        # filter by before / after
        self.assertGet(
            endpoint_url + f"?before={format_datetime(bcast2.created_on)}", [self.editor], results=[bcast2, bcast1]
        )
        self.assertGet(
            endpoint_url + f"?after={format_datetime(bcast3.created_on)}", [self.editor], results=[bcast4, bcast3]
        )

        with self.anonymous(self.org):
            response = self.assertGet(endpoint_url + f"?id={bcast1.id}", [self.editor], results=[bcast1])

            # URNs shouldn't be included
            self.assertIsNone(response.json()["results"][0]["urns"])

        # try to create new broadcast with no data at all
        self.assertPost(
            endpoint_url, self.admin, {}, errors={"non_field_errors": "Must provide either text or attachments."}
        )

        # try to create new broadcast with no recipients
        self.assertPost(
            endpoint_url,
            self.admin,
            {"text": "Hello"},
            errors={"non_field_errors": "Must provide either urns, contacts or groups."},
        )

        # try to create new broadcast with invalid group lookup
        self.assertPost(
            endpoint_url,
            self.admin,
            {"text": "Hello", "groups": [123456]},
            errors={"groups": "No such object: 123456"},
        )

        # try to create new broadcast with translations that don't include base language
        self.assertPost(
            endpoint_url,
            self.admin,
            {"text": {"kin": "Muraho"}, "base_language": "eng", "contacts": [joe.uuid]},
            errors={"non_field_errors": "No text translation provided in base language."},
        )

        media1 = self.upload_media(self.admin, f"{settings.MEDIA_ROOT}/test_media/steve marten.jpg")
        media2 = self.upload_media(self.admin, f"{settings.MEDIA_ROOT}/test_media/snow.mp4")

        # try to create new broadcast with attachment translations that don't include base language
        self.assertPost(
            endpoint_url,
            self.admin,
            {
                "text": {"eng": "Hello"},
                "attachments": {"spa": [str(media1.uuid)]},
                "base_language": "eng",
                "contacts": [joe.uuid],
            },
            errors={"non_field_errors": "No attachment translations provided in base language."},
        )

        # create new broadcast with all fields
        response = self.assertPost(
            endpoint_url,
            self.admin,
            {
                "text": {"eng": "Hello @contact.name", "spa": "Hola @contact.name"},
                "attachments": {
                    "eng": [str(media1.uuid), f"video/mp4:http://example.com/{media2.uuid}.mp4"],
                    "kin": [str(media2.uuid)],
                },
                "base_language": "eng",
                "urns": ["facebook:12345"],
                "contacts": [joe.uuid, frank.uuid],
                "groups": [reporters.uuid],
            },
            status=201,
        )

        broadcast = Broadcast.objects.get(id=response.json()["id"])
        self.assertEqual(
            {
                "eng": {
                    "text": "Hello @contact.name",
                    "attachments": [f"image/jpeg:{media1.url}", f"video/mp4:{media2.url}"],
                },
                "spa": {"text": "Hola @contact.name"},
                "kin": {"attachments": [f"video/mp4:{media2.url}"]},
            },
            broadcast.translations,
        )
        self.assertEqual("eng", broadcast.base_language)
        self.assertEqual(["facebook:12345"], broadcast.urns)
        self.assertEqual({joe, frank}, set(broadcast.contacts.all()))
        self.assertEqual({reporters}, set(broadcast.groups.all()))

        # create new broadcast without translations
        response = self.assertPost(
            endpoint_url,
            self.admin,
            {
                "text": "Hello",
                "attachments": [str(media1.uuid), str(media2.uuid)],
                "contacts": [joe.uuid, frank.uuid],
            },
            status=201,
        )

        broadcast = Broadcast.objects.get(id=response.json()["id"])
        self.assertEqual(
            {
                "eng": {
                    "text": "Hello",
                    "attachments": [f"image/jpeg:{media1.url}", f"video/mp4:{media2.url}"],
                }
            },
            broadcast.translations,
        )
        self.assertEqual("eng", broadcast.base_language)
        self.assertEqual({joe, frank}, set(broadcast.contacts.all()))

        # create new broadcast without translations containing only text, no attachments
        response = self.assertPost(
            endpoint_url,
            self.admin,
            {"text": "Hello", "contacts": [joe.uuid, frank.uuid]},
            status=201,
        )

        broadcast = Broadcast.objects.get(id=response.json()["id"])
        self.assertEqual({"eng": {"text": "Hello"}}, broadcast.translations)

        # create new broadcast without translations containing only attachments, no text
        response = self.assertPost(
            endpoint_url,
            self.admin,
            {"attachments": [str(media1.uuid), str(media2.uuid)], "contacts": [joe.uuid, frank.uuid]},
            status=201,
        )

        broadcast = Broadcast.objects.get(id=response.json()["id"])
        self.assertEqual(
            {"eng": {"attachments": [f"image/jpeg:{media1.url}", f"video/mp4:{media2.url}"]}},
            broadcast.translations,
        )

        # try sending as a flagged org
        self.org.flag()
        self.assertPost(
            endpoint_url,
            self.admin,
            {"text": "Hello", "contacts": [joe.uuid]},
            errors={"non_field_errors": Org.BLOCKER_FLAGGED},
        )
