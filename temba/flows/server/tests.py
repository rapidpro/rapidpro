from datetime import datetime

import pytz
from mock import patch

from temba.channels.models import Channel
from temba.msgs.models import Label
from temba.tests import MockResponse, TembaTest, matchers
from temba.values.constants import Value

from .assets import ChannelType, get_asset_type, get_asset_urls
from .client import FlowServerException, get_client
from .serialize import serialize_channel, serialize_field, serialize_label

TEST_ASSETS_BASE = "http://localhost:8000/flow/assets/"


class AssetsTest(TembaTest):
    def test_get_asset_urls(self):
        self.assertEqual(
            get_asset_urls(self.org),
            {
                "channel": matchers.String(pattern=f"{TEST_ASSETS_BASE}{self.org.id}/\d+/channel/"),
                "field": matchers.String(pattern=f"{TEST_ASSETS_BASE}{self.org.id}/\d+/field/"),
                "flow": matchers.String(pattern=f"{TEST_ASSETS_BASE}{self.org.id}/\d+/flow/"),
                "group": matchers.String(pattern=f"{TEST_ASSETS_BASE}{self.org.id}/\d+/group/"),
                "label": matchers.String(pattern=f"{TEST_ASSETS_BASE}{self.org.id}/\d+/label/"),
                "location_hierarchy": f"{TEST_ASSETS_BASE}{self.org.id}/1/location_hierarchy/",
                "resthook": matchers.String(pattern=f"{TEST_ASSETS_BASE}{self.org.id}/\d+/resthook/"),
            },
        )

    def test_bundling(self):
        self.assertEqual(
            get_asset_type(ChannelType).bundle_set(self.org, simulator=True),
            {
                "type": "channel",
                "url": matchers.String(pattern=f"{TEST_ASSETS_BASE}{self.org.id}/\d+/channel/"),
                "content": [
                    {
                        "address": "+250785551212",
                        "country": "RW",
                        "name": "Test Channel",
                        "roles": ["send", "receive"],
                        "schemes": ["tel"],
                        "uuid": str(self.channel.uuid),
                    },
                    {
                        "address": "+18005551212",
                        "name": "Simulator Channel",
                        "roles": ["send"],
                        "schemes": ["tel"],
                        "uuid": "440099cf-200c-4d45-a8e7-4a564f4a0e8b",
                    },
                ],
            },
        )
        self.assertEqual(
            get_asset_type(ChannelType).bundle_item(self.org, uuid=self.channel.uuid),
            {
                "type": "channel",
                "url": matchers.String(
                    pattern=f"{TEST_ASSETS_BASE}{self.org.id}/\d+/channel/{str(self.channel.uuid)}/"
                ),
                "content": {
                    "address": "+250785551212",
                    "country": "RW",
                    "name": "Test Channel",
                    "roles": ["send", "receive"],
                    "schemes": ["tel"],
                    "uuid": str(self.channel.uuid),
                },
            },
        )


class SerializationTest(TembaTest):
    def test_serialize_field(self):
        gender = self.create_field("gender", "Gender", Value.TYPE_TEXT)
        age = self.create_field("age", "Age", Value.TYPE_NUMBER)

        self.assertEqual(serialize_field(gender), {"key": "gender", "name": "Gender", "value_type": "text"})
        self.assertEqual(serialize_field(age), {"key": "age", "name": "Age", "value_type": "number"})

    def test_serialize_label(self):
        spam = Label.get_or_create(self.org, self.admin, "Spam")
        self.assertEqual(serialize_label(spam), {"uuid": str(spam.uuid), "name": "Spam"})

    def test_serialize_channel(self):
        nexmo = Channel.create(
            self.org,
            self.admin,
            country="",
            channel_type="NX",
            name="Bulk",
            address="1234",
            parent=self.channel,
            config={Channel.CONFIG_SHORTCODE_MATCHING_PREFIXES: ["25078"]},
        )

        self.assertEqual(
            serialize_channel(nexmo),
            {
                "uuid": str(nexmo.uuid),
                "name": "Bulk",
                "address": "1234",
                "roles": ["send", "receive"],
                "schemes": ["tel"],
                "match_prefixes": ["25078"],
                "parent": {"uuid": str(self.channel.uuid), "name": "Test Channel"},
            },
        )

        self.assertEqual(
            serialize_channel(self.channel),
            {
                "uuid": str(self.channel.uuid),
                "name": "Test Channel",
                "address": "+250785551212",
                "roles": ["send", "receive"],
                "schemes": ["tel"],
                "country": "RW",
            },
        )


class ClientTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.gender = self.create_field("gender", "Gender", Value.TYPE_TEXT)
        self.age = self.create_field("age", "Age", Value.TYPE_NUMBER)
        self.contact = self.create_contact("Bob", number="+12345670987", urn="twitterid:123456785#bobby")
        self.testers = self.create_group("Testers", [self.contact])
        self.client = get_client()

    @patch("temba.flows.server.client.FlowServerClient.resume")
    def test_resume_by_msg(self, mock_resume):
        twitter = Channel.create(
            self.org, self.admin, None, "TT", "Twitter", "nyaruka", schemes=["twitter", "twitterid"]
        )
        self.contact.set_preferred_channel(twitter)
        self.contact.urns.filter(scheme="twitterid").update(channel=twitter)
        self.contact.clear_urn_cache()

        with patch("django.utils.timezone.now", return_value=datetime(2018, 1, 18, 14, 24, 30, 0, tzinfo=pytz.UTC)):
            self.contact.set_field(self.admin, "gender", "M")
            self.contact.set_field(self.admin, "age", 36)

            msg = self.create_msg(direction="I", text="hello", contact=self.contact, channel=twitter)

            self.client.request_builder(self.org).resume_by_msg({}, msg, self.contact)

            mock_resume.assert_called_once_with(
                {
                    "assets": [],
                    "config": {},
                    "resume": {
                        "type": "msg",
                        "resumed_on": "2018-01-18T14:24:30+00:00",
                        "contact": {
                            "uuid": str(self.contact.uuid),
                            "id": self.contact.id,
                            "name": "Bob",
                            "language": None,
                            "urns": ["twitterid:123456785?channel=%s#bobby" % str(twitter.uuid), "tel:+12345670987"],
                            "fields": {"gender": {"text": "M"}, "age": {"text": "36", "number": "36"}},
                            "groups": [{"uuid": str(self.testers.uuid), "name": "Testers"}],
                        },
                        "msg": {
                            "uuid": str(msg.uuid),
                            "text": "hello",
                            "urn": "twitterid:123456785#bobby",
                            "channel": {"uuid": str(twitter.uuid), "name": "Twitter"},
                        },
                    },
                    "session": {},
                }
            )

    @patch("temba.flows.server.client.FlowServerClient.resume")
    def test_resume_by_run_expiration(self, mock_resume):
        flow = self.get_flow("color")
        run, = flow.start([], [self.contact])
        run.set_interrupted()

        with patch("django.utils.timezone.now", return_value=datetime(2018, 1, 18, 14, 24, 30, 0, tzinfo=pytz.UTC)):
            self.client.request_builder(self.org).resume_by_run_expiration({}, run)

            mock_resume.assert_called_once_with(
                {
                    "assets": [],
                    "config": {},
                    "resume": {"type": "run_expiration", "resumed_on": "2018-01-18T14:24:30+00:00"},
                    "session": {},
                }
            )

    @patch("requests.post")
    def test_request_failure(self, mock_post):
        mock_post.return_value = MockResponse(400, '{"errors":["Bad request", "Doh!"]}')

        flow = self.get_flow("color")
        contact = self.create_contact("Joe", number="+29638356667")

        with self.assertRaises(FlowServerException) as e:
            self.client.request_builder(self.org).start_manual(contact, flow)

        self.assertEqual(
            e.exception.as_json(),
            {"endpoint": "start", "request": matchers.Dict(), "response": {"errors": ["Bad request", "Doh!"]}},
        )
