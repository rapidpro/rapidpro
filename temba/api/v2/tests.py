import base64
import time
from collections import OrderedDict
from datetime import datetime, timezone as tzone
from decimal import Decimal
from unittest.mock import call, patch
from urllib.parse import quote_plus

import iso8601
from rest_framework import serializers

from django.conf import settings
from django.contrib.gis.geos import GEOSGeometry
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from temba.api.models import APIToken, Resthook, WebHookEvent
from temba.archives.models import Archive
from temba.campaigns.models import Campaign, CampaignEvent
from temba.channels.models import ChannelEvent
from temba.classifiers.models import Classifier
from temba.classifiers.types.luis import LuisType
from temba.classifiers.types.wit import WitType
from temba.contacts.models import Contact, ContactField, ContactGroup, ContactURN
from temba.flows.models import Flow, FlowLabel, FlowRun, FlowStart
from temba.globals.models import Global
from temba.locations.models import BoundaryAlias
from temba.msgs.models import Broadcast, Label, Media, Msg, OptIn
from temba.orgs.models import Org, OrgRole
from temba.schedules.models import Schedule
from temba.tests import TembaTest, matchers, mock_mailroom, mock_uuids
from temba.tests.engine import MockSessionWriter
from temba.tickets.models import Topic
from temba.triggers.models import Trigger

from ..tests import APITestMixin
from . import fields
from .serializers import format_datetime, normalize_extra

NUM_BASE_SESSION_QUERIES = 4  # number of queries required for any request using session auth
NUM_BASE_TOKEN_QUERIES = 3  # number of queries required for any request using token auth


class APITest(APITestMixin, TembaTest):
    def upload_media(self, user, filename: str):
        self.login(user)

        with open(filename, "rb") as data:
            response = self.client.post(
                reverse("api.v2.media") + ".json", {"file": data}, HTTP_X_FORWARDED_HTTPS="https"
            )
            self.assertEqual(201, response.status_code)

        return Media.objects.get(uuid=response.json()["uuid"])


class FieldsTest(APITest):
    def assert_field(self, f, *, submissions: dict, representations: dict):
        f._context = {"org": self.org}  # noqa

        for submitted, expected in submissions.items():
            if isinstance(expected, type) and issubclass(expected, Exception):
                with self.assertRaises(expected, msg=f"expected exception for '{submitted}'"):
                    f.run_validation(submitted)
            else:
                self.assertEqual(f.run_validation(submitted), expected, f"to_internal_value mismatch for '{submitted}'")

        for value, expected in representations.items():
            self.assertEqual(f.to_representation(value), expected, f"to_representation mismatch for '{value}'")

    def test_contact(self):
        joe = self.create_contact("Joe", urns=["tel:+593999123456"])
        frank = self.create_contact("Frank", urns=["twitterid:2352463463#franky"])  # urn has display fragment
        voldemort = self.create_contact("", urns=[])  # no name or URNs

        self.assert_field(
            fields.ContactField(source="test"),
            submissions={
                joe.uuid: joe,  # by UUID
                joe.get_urn().urn: joe,  # by URN
                0: serializers.ValidationError,
                (joe.uuid, frank.uuid): serializers.ValidationError,
            },
            representations={
                joe: {"uuid": str(joe.uuid), "name": "Joe"},
            },
        )

        self.assert_field(
            fields.ContactField(source="test", as_summary=True),
            submissions={
                joe.uuid: joe,  # by UUID
                joe.get_urn().urn: joe,  # by URN
                0: serializers.ValidationError,
                (joe.uuid, frank.uuid): serializers.ValidationError,
            },
            representations={
                joe: {
                    "uuid": str(joe.uuid),
                    "name": "Joe",
                    "urn": "tel:+593999123456",
                    "urn_display": "099 912 3456",
                },
                frank: {
                    "uuid": str(frank.uuid),
                    "name": "Frank",
                    "urn": "twitterid:2352463463",
                    "urn_display": "franky",
                },
                voldemort: {
                    "uuid": str(voldemort.uuid),
                    "name": "",
                    "urn": None,
                    "urn_display": None,
                },
            },
        )

        self.assert_field(
            fields.ContactField(source="test", many=True),
            submissions={
                (joe.uuid, frank.uuid): [joe, frank],
                joe.uuid: serializers.ValidationError,
            },
            representations={
                (joe, frank): [
                    {"uuid": str(joe.uuid), "name": "Joe"},
                    {"uuid": str(frank.uuid), "name": "Frank"},
                ]
            },
        )

        with self.anonymous(self.org):
            # load contacts again without cached org on them or their urns
            joe = Contact.objects.get(id=joe.id)
            frank = Contact.objects.get(id=frank.id)
            voldemort = Contact.objects.get(id=voldemort.id)

            self.assert_field(
                fields.ContactField(source="test"),
                submissions={
                    joe.uuid: joe,  # by UUID
                    joe.get_urn().urn: joe,  # by URN
                    0: serializers.ValidationError,
                    (joe.uuid, frank.uuid): serializers.ValidationError,
                },
                representations={
                    joe: {"uuid": str(joe.uuid), "name": "Joe"},
                    frank: {"uuid": str(frank.uuid), "name": "Frank"},
                    voldemort: {"uuid": str(voldemort.uuid), "name": ""},
                },
            )

            self.assert_field(
                fields.ContactField(source="test", as_summary=True),
                submissions={
                    joe.uuid: joe,  # by UUID
                    joe.get_urn().urn: joe,  # by URN
                    0: serializers.ValidationError,
                    (joe.uuid, frank.uuid): serializers.ValidationError,
                },
                representations={
                    joe: {
                        "uuid": str(joe.uuid),
                        "name": "Joe",
                        "urn": "tel:********",
                        "urn_display": None,
                        "anon_display": f"{joe.id:010}",
                    },
                    frank: {
                        "uuid": str(frank.uuid),
                        "name": "Frank",
                        "urn": "twitterid:********",
                        "urn_display": None,
                        "anon_display": f"{frank.id:010}",
                    },
                    voldemort: {
                        "uuid": str(voldemort.uuid),
                        "name": "",
                        "urn": None,
                        "urn_display": None,
                        "anon_display": f"{voldemort.id:010}",
                    },
                },
            )

    def test_language_and_translations(self):
        self.assert_field(
            fields.LanguageField(source="test"),
            submissions={
                "eng": "eng",
                "kin": "kin",
                123: serializers.ValidationError,
                "base": serializers.ValidationError,
            },
            representations={"eng": "eng"},
        )

        field = fields.LimitedDictField(source="test", max_length=2)
        self.assertEqual({"foo": "bar", "zed": 123}, field.run_validation({"foo": "bar", "zed": 123}))
        self.assertRaises(serializers.ValidationError, field.run_validation, {"1": 1, "2": 2, "3": 3})

        field = fields.LanguageDictField(source="test")
        self.assertEqual(field.run_validation({"eng": "Hello"}), {"eng": "Hello"})
        self.assertRaises(serializers.ValidationError, field.run_validation, {"base": ""})

        field = fields.TranslatedTextField(source="test", max_length=10)
        field._context = {"org": self.org}

        self.assertEqual(field.run_validation("Hello"), {"eng": "Hello"})
        self.assertEqual(field.run_validation({"eng": "Hello"}), {"eng": "Hello"})
        self.assertEqual(field.run_validation({"eng": "Hello", "spa": "Hola"}), {"eng": "Hello", "spa": "Hola"})
        self.assertRaises(serializers.ValidationError, field.run_validation, {"eng": ""})  # empty
        self.assertRaises(serializers.ValidationError, field.run_validation, "")  # empty
        self.assertRaises(serializers.ValidationError, field.run_validation, "  ")  # blank
        self.assertRaises(serializers.ValidationError, field.run_validation, 123)  # not a string or dict
        self.assertRaises(serializers.ValidationError, field.run_validation, {})  # no translations
        self.assertRaises(serializers.ValidationError, field.run_validation, {123: "Hello"})  # lang not a str
        self.assertRaises(serializers.ValidationError, field.run_validation, {"base": "Hello"})  # lang not valid
        self.assertRaises(serializers.ValidationError, field.run_validation, "HelloHello1")  # translation too long
        self.assertRaises(serializers.ValidationError, field.run_validation, {"eng": "HelloHello1"})

        media1 = self.upload_media(self.admin, f"{settings.MEDIA_ROOT}/test_media/steve marten.jpg")
        media2 = self.upload_media(self.admin, f"{settings.MEDIA_ROOT}/test_media/snow.mp4")

        field = fields.TranslatedAttachmentsField(source="test")
        field._context = {"org": self.org}

        self.assertEqual(field.run_validation([f"image/jpeg:{media1.url}"]), {"eng": [media1]})
        self.assertEqual(field.run_validation({"eng": [str(media1.uuid)]}), {"eng": [media1]})
        self.assertEqual(
            field.run_validation({"eng": [str(media1.uuid), str(media2.uuid)], "spa": [str(media1.uuid)]}),
            {"eng": [media1, media2], "spa": [media1]},
        )
        self.assertRaises(serializers.ValidationError, field.run_validation, {})  # empty
        self.assertRaises(serializers.ValidationError, field.run_validation, {"eng": [""]})  # empty
        self.assertRaises(serializers.ValidationError, field.run_validation, {"eng": [" "]})  # blank
        self.assertRaises(serializers.ValidationError, field.run_validation, {"base": ["Hello"]})  # lang not valid
        self.assertRaises(
            serializers.ValidationError, field.run_validation, {"eng": ["Hello"]}
        )  # translation not valid attachment
        self.assertRaises(
            serializers.ValidationError, field.run_validation, {"kin": f"image/jpeg:{media1.url}"}
        )  # translation not a list
        self.assertRaises(
            serializers.ValidationError, field.run_validation, {"eng": [f"image/jpeg:{media1.url}"] * 11}
        )  # too many

        # check that default language is based on first flow language
        self.org.flow_languages = ["spa", "kin"]
        self.org.save(update_fields=("flow_languages",))

        self.assertEqual(field.to_internal_value([str(media1.uuid)]), {"spa": [media1]})

    def test_others(self):
        group = self.create_group("Customers")
        field_obj = self.create_field("registered", "Registered On", value_type=ContactField.TYPE_DATETIME)
        flow = self.create_flow("Test")
        campaign = Campaign.create(self.org, self.admin, "Reminders #1", group)
        event = CampaignEvent.create_flow_event(
            self.org, self.admin, campaign, field_obj, 6, CampaignEvent.UNIT_HOURS, flow, delivery_hour=12
        )
        media = self.upload_media(self.admin, f"{settings.MEDIA_ROOT}/test_media/steve marten.jpg")

        field = fields.CampaignField(source="test")
        field._context = {"org": self.org}

        self.assertEqual(field.to_internal_value(str(campaign.uuid)), campaign)
        self.assertRaises(serializers.ValidationError, field.to_internal_value, {"id": 3})  # not a string or int

        field = fields.CampaignEventField(source="test")
        field._context = {"org": self.org}

        self.assertEqual(field.to_internal_value(str(event.uuid)), event)

        field._context = {"org": self.org2}

        self.assertRaises(serializers.ValidationError, field.to_internal_value, event.uuid)

        deleted_channel = self.create_channel("A", "My Android", "123456")
        deleted_channel.is_active = False
        deleted_channel.save(update_fields=("is_active",))

        self.assert_field(
            fields.ChannelField(source="test"),
            submissions={self.channel.uuid: self.channel, deleted_channel.uuid: serializers.ValidationError},
            representations={self.channel: {"uuid": str(self.channel.uuid), "name": "Test Channel"}},
        )

        self.assert_field(
            fields.ContactGroupField(source="test"),
            submissions={group.uuid: group},
            representations={group: {"uuid": str(group.uuid), "name": "Customers"}},
        )

        field_created_on = self.org.fields.get(key="created_on")

        self.assert_field(
            fields.ContactFieldField(source="test"),
            submissions={"registered": field_obj, "created_on": field_created_on, "xyz": serializers.ValidationError},
            representations={field_obj: {"key": "registered", "name": "Registered On", "label": "Registered On"}},
        )

        self.assert_field(
            fields.FlowField(source="test"),
            submissions={flow.uuid: flow},
            representations={flow: {"uuid": str(flow.uuid), "name": flow.name}},
        )

        self.assert_field(
            fields.MediaField(source="test"),
            submissions={str(media.uuid): media, "xyz": serializers.ValidationError},
            representations={media: str(media.uuid)},
        )

        self.assert_field(
            fields.TopicField(source="test"),
            submissions={str(self.org.default_ticket_topic.uuid): self.org.default_ticket_topic},
            representations={
                self.org.default_ticket_topic: {"uuid": str(self.org.default_ticket_topic.uuid), "name": "General"}
            },
        )

        self.assert_field(
            fields.URNField(source="test"),
            submissions={
                "tel:+1-800-123-4567": "tel:+18001234567",
                "tel:0788 123 123": "tel:+250788123123",  # using org country
                "tel:(078) 812-3123": "tel:+250788123123",
                "12345": serializers.ValidationError,  # un-parseable
                "tel:800-123-4567": serializers.ValidationError,  # no country code
                f"external:{'1' * 256}": serializers.ValidationError,  # too long
                18_001_234_567: serializers.ValidationError,  # non-string
            },
            representations={"tel:+18001234567": "tel:+18001234567"},
        )

        self.editor.is_active = False
        self.editor.save(update_fields=("is_active",))

        self.assert_field(
            fields.UserField(source="test"),
            submissions={
                "VIEWER@NYARUKA.COM": self.user,
                "admin@nyaruka.com": self.admin,
                self.editor.email: serializers.ValidationError,  # deleted
                self.admin2.email: serializers.ValidationError,  # not in org
            },
            representations={
                self.user: {"email": "viewer@nyaruka.com", "name": ""},
                self.editor: {"email": "editor@nyaruka.com", "name": "Ed McEdits"},
            },
        )
        self.assert_field(
            fields.UserField(source="test", assignable_only=True),
            submissions={
                self.user.email: serializers.ValidationError,  # not assignable
                self.admin.email: self.admin,
                self.agent.email: self.agent,
            },
            representations={self.agent: {"email": "agent@nyaruka.com", "name": "Agnes"}},
        )

    def test_serialize_urn(self):
        urn_obj = ContactURN.objects.create(
            org=self.org, scheme="tel", path="+250788383383", identity="tel:+250788383383", priority=50, display="xyz"
        )
        urn_dict = {
            "channel": {"name": "Twilio", "uuid": "74729f45-7f29-4868-9dc4-90e491e3c7d8"},
            "scheme": "tel",
            "path": "+250788383383",
            "display": "xyz",
        }

        self.assertEqual("tel:+250788383383", fields.serialize_urn(self.org, urn_obj))
        self.assertEqual(urn_dict, fields.serialize_urn(self.org, urn_dict))

        with self.anonymous(self.org):
            self.assertEqual("tel:********", fields.serialize_urn(self.org, urn_obj))
            self.assertEqual(
                {
                    "channel": {"name": "Twilio", "uuid": "74729f45-7f29-4868-9dc4-90e491e3c7d8"},
                    "scheme": "tel",
                    "path": "********",
                    "display": "xyz",
                },
                fields.serialize_urn(self.org, urn_dict),
            )


class EndpointsTest(APITest):
    def setUp(self):
        super().setUp()

        self.joe = self.create_contact("Joe Blow", phone="+250788123123")
        self.frank = self.create_contact("Frank", urns=["twitter:franky"])

        self.twitter = self.create_channel("TWT", "Twitter Channel", "billy_bob")

        self.hans = self.create_contact("Hans Gruber", phone="+4921551511", org=self.org2)

        self.org2channel = self.create_channel("A", "Org2Channel", "123456", country="RW", org=self.org2)

    def assertResultsById(self, response, expected):
        self.assertEqual(response.status_code, 200)
        self.assertEqual([r["id"] for r in response.json()["results"]], [o.pk for o in expected])

    def assertResultsByUUID(self, response, expected):
        self.assertEqual(response.status_code, 200)
        self.assertEqual([r["uuid"] for r in response.json()["results"]], [str(o.uuid) for o in expected])

    def assert404(self, response):
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"detail": "Not found."})

    @override_settings(REST_HANDLE_EXCEPTIONS=True)
    @patch("temba.api.v2.views.FieldsEndpoint.get_queryset")
    def test_error_handling(self, mock_get_queryset):
        mock_get_queryset.side_effect = ValueError("DOH!")

        self.login(self.admin)

        response = self.client.get(
            reverse("api.v2.fields") + ".json", content_type="application/json", HTTP_X_FORWARDED_HTTPS="https"
        )
        self.assertContains(response, "Server Error. Site administrators have been notified.", status_code=500)

    @override_settings(FLOW_START_PARAMS_SIZE=4)
    def test_normalize_extra(self):
        self.assertEqual(OrderedDict(), normalize_extra({}))
        self.assertEqual(
            OrderedDict([("0", "a"), ("1", True), ("2", Decimal("1.0")), ("3", "")]),
            normalize_extra(["a", True, Decimal("1.0"), None]),
        )
        self.assertEqual(OrderedDict([("_3__x", "z")]), normalize_extra({"%3 !x": "z"}))
        self.assertEqual(
            OrderedDict([("0", "a"), ("1", "b"), ("2", "c"), ("3", "d")]), normalize_extra(["a", "b", "c", "d", "e"])
        )
        self.assertEqual(
            OrderedDict([("a", 1), ("b", 2), ("c", 3), ("d", 4)]),
            normalize_extra({"a": 1, "b": 2, "c": 3, "d": 4, "e": 5}),
        )
        self.assertEqual(OrderedDict([("a", "x" * 640)]), normalize_extra({"a": "x" * 641}))

    def test_authentication(self):
        def request(endpoint, **headers):
            return self.client.get(
                f"{endpoint}.json", content_type="application/json", HTTP_X_FORWARDED_HTTPS="https", **headers
            )

        def request_by_token(endpoint, token):
            return request(endpoint, HTTP_AUTHORIZATION=f"Token {token}")

        def request_by_basic_auth(endpoint, username, token):
            credentials_base64 = base64.b64encode(f"{username}:{token}".encode()).decode()
            return request(endpoint, HTTP_AUTHORIZATION=f"Basic {credentials_base64}")

        def request_by_session(endpoint, user):
            self.login(user)
            resp = request(endpoint)
            self.client.logout()
            return resp

        contacts_url = reverse("api.v2.contacts")
        campaigns_url = reverse("api.v2.campaigns")
        fields_url = reverse("api.v2.fields")

        token1 = APIToken.get_or_create(self.org, self.admin, role=OrgRole.ADMINISTRATOR)
        token2 = APIToken.get_or_create(self.org, self.editor, role=OrgRole.EDITOR)
        token3 = APIToken.get_or_create(self.org, self.customer_support, role=OrgRole.ADMINISTRATOR)

        # can request fields endpoint using all 3 methods
        response = request_by_token(fields_url, token1.key)
        self.assertEqual(200, response.status_code)
        response = request_by_basic_auth(fields_url, self.admin.username, token1.key)
        self.assertEqual(200, response.status_code)
        response = request_by_session(fields_url, self.admin)
        self.assertEqual(200, response.status_code)

        # can't fetch endpoint with invalid token
        response = request_by_token(contacts_url, "1234567890")
        self.assertResponseError(response, None, "Invalid token", status_code=403)

        # can't fetch endpoint with invalid token
        response = request_by_basic_auth(contacts_url, self.admin.username, "1234567890")
        self.assertResponseError(response, None, "Invalid token or email", status_code=403)

        # can't fetch endpoint with invalid username
        response = request_by_basic_auth(contacts_url, "some@name.com", token1.key)
        self.assertResponseError(response, None, "Invalid token or email", status_code=403)

        # can fetch campaigns endpoint with valid admin token
        response = request_by_token(campaigns_url, token1.key)
        self.assertEqual(200, response.status_code)
        self.assertEqual(str(self.org.id), response["X-Temba-Org"])

        response = request_by_basic_auth(contacts_url, self.editor.username, token2.key)
        self.assertEqual(200, response.status_code)
        self.assertEqual(str(self.org.id), response["X-Temba-Org"])

        # simulate the admin user exceeding the rate limit for the v2 scope
        cache.set(f"throttle_v2_{self.org.id}", [time.time() for r in range(10000)])

        # next request they make using a token will be rejected
        response = request_by_token(fields_url, token1.key)
        self.assertEqual(response.status_code, 429)

        # same with basic auth
        response = request_by_basic_auth(fields_url, self.admin.username, token1.key)
        self.assertEqual(response.status_code, 429)

        # or if another user in same org makes a request
        response = request_by_token(fields_url, token2.key)
        self.assertEqual(response.status_code, 429)

        # but they can still make a request if they have a session
        response = request_by_session(fields_url, self.admin)
        self.assertEqual(response.status_code, 200)

        # or if they're a staff user because they are user-scoped
        response = request_by_token(fields_url, token3.key)
        self.assertEqual(response.status_code, 200)

        # are allowed to access if we have not reached the configured org api rates
        self.org.api_rates = {"v2": "15000/hour"}
        self.org.save(update_fields=("api_rates",))

        response = request_by_basic_auth(fields_url, self.admin.username, token1.key)
        self.assertEqual(response.status_code, 200)

        cache.set(f"throttle_v2_{self.org.id}", [time.time() for r in range(15000)])

        # next request they make using a token will be rejected
        response = request_by_token(fields_url, token1.key)
        self.assertEqual(response.status_code, 429)

        # if user loses access to the token's role, don't allow the request
        self.org.add_user(self.admin, OrgRole.EDITOR)

        self.assertEqual(request_by_token(campaigns_url, token1.key).status_code, 403)
        self.assertEqual(request_by_basic_auth(campaigns_url, self.admin.username, token1.key).status_code, 403)
        self.assertEqual(request_by_token(contacts_url, token2.key).status_code, 200)  # other token unaffected
        self.assertEqual(request_by_basic_auth(contacts_url, self.editor.username, token2.key).status_code, 200)

        # and if user is inactive, disallow the request
        self.admin.is_active = False
        self.admin.save()

        response = request_by_token(contacts_url, token1.key)
        self.assertResponseError(response, None, "Invalid token", status_code=403)

        response = request_by_basic_auth(contacts_url, self.admin.username, token1.key)
        self.assertResponseError(response, None, "Invalid token or email", status_code=403)

    @override_settings(SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_HTTPS", "https"))
    def test_root(self):
        root_url = reverse("api.v2.root")

        # browse as HTML anonymously (should still show docs)
        response = self.client.get(root_url)
        self.assertContains(response, "We provide a RESTful JSON API")

        # POSTing just returns the docs with a 405
        response = self.client.post(root_url, {})
        self.assertContains(response, "We provide a RESTful JSON API", status_code=405)

        # same thing if user navigates to just /api
        response = self.client.get(reverse("api"), follow=True)
        self.assertContains(response, "We provide a RESTful JSON API")

        # try to browse as JSON anonymously
        response = self.client.get(root_url + ".json")
        self.assertEqual(200, response.status_code)
        self.assertIsInstance(response.json(), dict)
        self.assertEqual(response.json()["runs"], "http://testserver/api/v2/runs")  # endpoints are listed

    def test_docs(self):
        messages_url = reverse("api.v2.messages")

        # test fetching docs anonymously
        response = self.client.get(messages_url)
        self.assertContains(response, "This endpoint allows you to list messages in your account.")

        # you can also post to docs endpoints tho it just returns the docs with a 403
        response = self.client.post(messages_url, {})
        self.assertContains(response, "This endpoint allows you to list messages in your account.", status_code=403)

        # test fetching docs logged in
        self.login(self.editor)
        response = self.client.get(messages_url)
        self.assertContains(response, "This endpoint allows you to list messages in your account.")

    def test_explorer(self):
        explorer_url = reverse("api.v2.explorer")

        response = self.client.get(explorer_url)
        self.assertLoginRedirect(response)

        # viewers can't access
        self.login(self.user)
        response = self.client.get(explorer_url)
        self.assertLoginRedirect(response)

        # editors and administrators can
        self.login(self.editor)
        response = self.client.get(explorer_url)
        self.assertEqual(200, response.status_code)

        self.login(self.admin)
        response = self.client.get(explorer_url)
        self.assertEqual(200, response.status_code)

    def test_pagination(self):
        endpoint_url = reverse("api.v2.runs") + ".json"
        self.login(self.admin)

        # create 1255 test runs (5 full pages of 250 items + 1 partial with 5 items)
        flow = self.create_flow("Test")
        runs = []
        for r in range(1255):
            runs.append(FlowRun(org=self.org, flow=flow, contact=self.joe, status="C", exited_on=timezone.now()))
        FlowRun.objects.bulk_create(runs)
        actual_ids = list(FlowRun.objects.order_by("-pk").values_list("pk", flat=True))

        # give them all the same modified_on
        FlowRun.objects.all().update(modified_on=datetime(2015, 9, 15, 0, 0, 0, 0, tzone.utc))

        returned_ids = []

        # fetch all full pages
        with self.mockReadOnly():
            resp_json = None
            for p in range(5):
                response = self.client.get(
                    endpoint_url if p == 0 else resp_json["next"], content_type="application/json"
                )
                self.assertEqual(200, response.status_code)

                resp_json = response.json()

                self.assertEqual(len(resp_json["results"]), 250)
                self.assertIsNotNone(resp_json["next"])

                returned_ids += [r["id"] for r in response.json()["results"]]

        # fetch final partial page
        with self.mockReadOnly():
            response = self.client.get(resp_json["next"], content_type="application/json")

        resp_json = response.json()
        self.assertEqual(len(resp_json["results"]), 5)
        self.assertIsNone(resp_json["next"])

        returned_ids += [r["id"] for r in response.json()["results"]]

        self.assertEqual(returned_ids, actual_ids)  # ensure all results were returned and in correct order

    @patch("temba.flows.models.FlowStart.create")
    def test_transactions(self, mock_flowstart_create):
        """
        Serializer writes are wrapped in a transaction. This test simulates FlowStart.create blowing up and checks that
        contacts aren't created.
        """
        mock_flowstart_create.side_effect = ValueError("DOH!")

        flow = self.create_flow("Test")

        try:
            self.assertPost(
                reverse("api.v2.flow_starts") + ".json",
                self.admin,
                {"flow": str(flow.uuid), "urns": ["tel:+12067791212"]},
                status=201,
            )
            self.fail()  # ensure exception is thrown
        except ValueError:
            pass

        self.assertFalse(Contact.objects.filter(urns__path="+12067791212"))

    def test_archives(self):
        endpoint_url = reverse("api.v2.archives") + ".json"

        self.assertGetNotPermitted(endpoint_url, [None, self.user, self.agent])
        self.assertPostNotAllowed(endpoint_url)
        self.assertDeleteNotAllowed(endpoint_url)

        # create some archives
        Archive.objects.create(
            org=self.org,
            start_date=datetime(2017, 4, 5),
            build_time=12,
            record_count=34,
            size=345,
            hash="c4ca4238a0b923820dcc509a6f75849b",
            archive_type=Archive.TYPE_MSG,
            period=Archive.PERIOD_DAILY,
        )
        archive2 = Archive.objects.create(
            org=self.org,
            start_date=datetime(2017, 5, 5),
            build_time=12,
            record_count=34,
            size=345,
            hash="c81e728d9d4c2f636f067f89cc14862c",
            archive_type=Archive.TYPE_MSG,
            period=Archive.PERIOD_MONTHLY,
        )
        archive3 = Archive.objects.create(
            org=self.org,
            start_date=datetime(2017, 6, 5),
            build_time=12,
            record_count=34,
            size=345,
            hash="eccbc87e4b5ce2fe28308fd9f2a7baf3",
            archive_type=Archive.TYPE_FLOWRUN,
            period=Archive.PERIOD_DAILY,
        )
        archive4 = Archive.objects.create(
            org=self.org,
            start_date=datetime(2017, 7, 5),
            build_time=12,
            record_count=34,
            size=345,
            hash="a87ff679a2f3e71d9181a67b7542122c",
            archive_type=Archive.TYPE_FLOWRUN,
            period=Archive.PERIOD_MONTHLY,
        )
        # this archive has been rolled up and it should not be included in the API responses
        Archive.objects.create(
            org=self.org,
            start_date=datetime(2017, 5, 1),
            build_time=12,
            record_count=34,
            size=345,
            hash="e4da3b7fbbce2345d7772b0674a318d5",
            archive_type=Archive.TYPE_FLOWRUN,
            period=Archive.PERIOD_DAILY,
            rollup=archive2,
        )

        # create archive for other org
        Archive.objects.create(
            org=self.org2,
            start_date=datetime(2017, 5, 1),
            build_time=12,
            record_count=34,
            size=345,
            hash="1679091c5a880faf6fb5e6087eb1b2dc",
            archive_type=Archive.TYPE_FLOWRUN,
            period=Archive.PERIOD_DAILY,
        )

        # there should be 4 archives in the response, because one has been rolled up
        self.assertGet(
            endpoint_url,
            [self.editor],
            results=[
                {
                    "archive_type": "run",
                    "download_url": "",
                    "hash": "a87ff679a2f3e71d9181a67b7542122c",
                    "period": "monthly",
                    "record_count": 34,
                    "size": 345,
                    "start_date": "2017-07-05",
                },
                {
                    "archive_type": "run",
                    "download_url": "",
                    "hash": "eccbc87e4b5ce2fe28308fd9f2a7baf3",
                    "period": "daily",
                    "record_count": 34,
                    "size": 345,
                    "start_date": "2017-06-05",
                },
                {
                    "archive_type": "message",
                    "download_url": "",
                    "hash": "c81e728d9d4c2f636f067f89cc14862c",
                    "period": "monthly",
                    "record_count": 34,
                    "size": 345,
                    "start_date": "2017-05-05",
                },
                {
                    "archive_type": "message",
                    "download_url": "",
                    "hash": "c4ca4238a0b923820dcc509a6f75849b",
                    "period": "daily",
                    "record_count": 34,
                    "size": 345,
                    "start_date": "2017-04-05",
                },
            ],
            num_queries=NUM_BASE_SESSION_QUERIES + 2,
        )

        self.assertGet(endpoint_url + "?after=2017-05-01", [self.editor], results=[archive4, archive3, archive2])
        self.assertGet(endpoint_url + "?after=2017-05-01&archive_type=run", [self.editor], results=[archive4, archive3])

        # unknown archive type
        self.assertGet(endpoint_url + "?archive_type=invalid", [self.editor], results=[])

        # only for dailies
        self.assertGet(
            endpoint_url + "?after=2017-05-01&archive_type=run&period=daily", [self.editor], results=[archive3]
        )

        # only for monthlies
        self.assertGet(endpoint_url + "?period=monthly", [self.editor], results=[archive4, archive2])

        # test access from a user with no org
        self.login(self.non_org_user)
        response = self.client.get(endpoint_url)
        self.assertEqual(403, response.status_code)

    def test_boundaries(self):
        endpoint_url = reverse("api.v2.boundaries") + ".json"

        self.assertGetNotPermitted(endpoint_url, [None])
        self.assertPostNotAllowed(endpoint_url)
        self.assertDeleteNotAllowed(endpoint_url)

        self.setUpLocations()

        BoundaryAlias.create(self.org, self.admin, self.state1, "Kigali")
        BoundaryAlias.create(self.org, self.admin, self.state2, "East Prov")
        BoundaryAlias.create(self.org2, self.admin2, self.state1, "Other Org")  # shouldn't be returned

        self.state1.simplified_geometry = GEOSGeometry("MULTIPOLYGON(((1 1, 1 -1, -1 -1, -1 1, 1 1)))")
        self.state1.save()

        # test without geometry
        self.assertGet(
            endpoint_url,
            [self.user, self.editor, self.admin],
            results=[
                {
                    "osm_id": "1708283",
                    "name": "Kigali City",
                    "parent": {"osm_id": "171496", "name": "Rwanda"},
                    "level": 1,
                    "aliases": ["Kigali", "Kigari"],
                    "geometry": None,
                },
                {
                    "osm_id": "171113181",
                    "name": "Kageyo",
                    "parent": {"osm_id": "R1711131", "name": "Gatsibo"},
                    "level": 3,
                    "aliases": [],
                    "geometry": None,
                },
                {
                    "osm_id": "1711142",
                    "name": "Rwamagana",
                    "parent": {"osm_id": "171591", "name": "Eastern Province"},
                    "level": 2,
                    "aliases": [],
                    "geometry": None,
                },
                {
                    "osm_id": "1711163",
                    "name": "Kay\u00f4nza",
                    "parent": {"osm_id": "171591", "name": "Eastern Province"},
                    "level": 2,
                    "aliases": [],
                    "geometry": None,
                },
                {
                    "osm_id": "171116381",
                    "name": "Kabare",
                    "parent": {"osm_id": "1711163", "name": "Kay\u00f4nza"},
                    "level": 3,
                    "aliases": [],
                    "geometry": None,
                },
                {"osm_id": "171496", "name": "Rwanda", "parent": None, "level": 0, "aliases": [], "geometry": None},
                {
                    "osm_id": "171591",
                    "name": "Eastern Province",
                    "parent": {"osm_id": "171496", "name": "Rwanda"},
                    "level": 1,
                    "aliases": ["East Prov"],
                    "geometry": None,
                },
                {
                    "osm_id": "3963734",
                    "name": "Nyarugenge",
                    "parent": {"osm_id": "1708283", "name": "Kigali City"},
                    "level": 2,
                    "aliases": [],
                    "geometry": None,
                },
                {
                    "osm_id": "R1711131",
                    "name": "Gatsibo",
                    "parent": {"osm_id": "171591", "name": "Eastern Province"},
                    "level": 2,
                    "aliases": [],
                    "geometry": None,
                },
                {
                    "osm_id": "VMN.49.1_1",
                    "name": "Bukure",
                    "parent": {"osm_id": "1711142", "name": "Rwamagana"},
                    "level": 3,
                    "aliases": [],
                    "geometry": None,
                },
            ],
            num_queries=NUM_BASE_SESSION_QUERIES + 3,
        )

        # test with geometry
        self.assertGet(
            endpoint_url + "?geometry=true",
            [self.admin],
            results=[
                {
                    "osm_id": "1708283",
                    "name": "Kigali City",
                    "parent": {"osm_id": "171496", "name": "Rwanda"},
                    "level": 1,
                    "aliases": ["Kigali", "Kigari"],
                    "geometry": {
                        "type": "MultiPolygon",
                        "coordinates": [[[[1.0, 1.0], [1.0, -1.0], [-1.0, -1.0], [-1.0, 1.0], [1.0, 1.0]]]],
                    },
                },
                matchers.Dict(),
                matchers.Dict(),
                matchers.Dict(),
                matchers.Dict(),
                matchers.Dict(),
                matchers.Dict(),
                matchers.Dict(),
                matchers.Dict(),
                matchers.Dict(),
            ],
            num_queries=NUM_BASE_SESSION_QUERIES + 3,
        )

        # if org doesn't have a country, just return no results
        self.org.country = None
        self.org.save(update_fields=("country",))

        self.assertGet(endpoint_url, [self.admin], results=[])

    @mock_mailroom
    def test_broadcasts(self, mr_mocks):
        endpoint_url = reverse("api.v2.broadcasts") + ".json"

        self.assertGetNotPermitted(endpoint_url, [None, self.agent])
        self.assertPostNotPermitted(endpoint_url, [None, self.user, self.agent])
        self.assertDeleteNotAllowed(endpoint_url)

        reporters = self.create_group("Reporters", [self.joe, self.frank])

        bcast1 = self.create_broadcast(self.admin, {"eng": {"text": "Hello 1"}}, urns=["twitter:franky"], status="Q")
        bcast2 = self.create_broadcast(self.admin, {"eng": {"text": "Hello 2"}}, contacts=[self.joe], status="Q")
        bcast3 = self.create_broadcast(self.admin, {"eng": {"text": "Hello 3"}}, contacts=[self.frank], status="S")
        bcast4 = self.create_broadcast(
            self.admin,
            {"eng": {"text": "Hello 4"}},
            urns=["twitter:franky"],
            contacts=[self.joe],
            groups=[reporters],
            status="F",
        )
        self.create_broadcast(
            self.admin,
            {"eng": {"text": "Scheduled"}},
            contacts=[self.joe],
            schedule=Schedule.create(self.org, timezone.now(), Schedule.REPEAT_DAILY),
        )
        self.create_broadcast(self.admin2, {"eng": {"text": "Different org..."}}, contacts=[self.hans], org=self.org2)

        # no filtering
        response = self.assertGet(
            endpoint_url,
            [self.user, self.editor, self.admin],
            results=[bcast4, bcast3, bcast2, bcast1],
            num_queries=NUM_BASE_SESSION_QUERIES + 3,
        )
        resp_json = response.json()

        self.assertEqual(
            {
                "id": bcast2.id,
                "urns": [],
                "contacts": [{"uuid": self.joe.uuid, "name": self.joe.name}],
                "groups": [],
                "text": {"eng": "Hello 2"},
                "attachments": {"eng": []},
                "base_language": "eng",
                "status": "queued",
                "created_on": format_datetime(bcast2.created_on),
            },
            resp_json["results"][2],
        )
        self.assertEqual(
            {
                "id": bcast4.id,
                "urns": ["twitter:franky"],
                "contacts": [{"uuid": self.joe.uuid, "name": self.joe.name}],
                "groups": [{"uuid": reporters.uuid, "name": reporters.name}],
                "text": {"eng": "Hello 4"},
                "attachments": {"eng": []},
                "base_language": "eng",
                "status": "failed",
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
            {"text": {"kin": "Muraho"}, "base_language": "eng", "contacts": [self.joe.uuid]},
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
                "contacts": [self.joe.uuid],
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
                "urns": ["twitter:franky"],
                "contacts": [self.joe.uuid, self.frank.uuid],
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
        self.assertEqual(["twitter:franky"], broadcast.urns)
        self.assertEqual({self.joe, self.frank}, set(broadcast.contacts.all()))
        self.assertEqual({reporters}, set(broadcast.groups.all()))

        # create new broadcast without translations
        response = self.assertPost(
            endpoint_url,
            self.admin,
            {
                "text": "Hello",
                "attachments": [str(media1.uuid), str(media2.uuid)],
                "contacts": [self.joe.uuid, self.frank.uuid],
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
        self.assertEqual({self.joe, self.frank}, set(broadcast.contacts.all()))

        # create new broadcast without translations containing only text, no attachments
        response = self.assertPost(
            endpoint_url,
            self.admin,
            {"text": "Hello", "contacts": [self.joe.uuid, self.frank.uuid]},
            status=201,
        )

        broadcast = Broadcast.objects.get(id=response.json()["id"])
        self.assertEqual({"eng": {"text": "Hello"}}, broadcast.translations)

        # create new broadcast without translations containing only attachments, no text
        response = self.assertPost(
            endpoint_url,
            self.admin,
            {"attachments": [str(media1.uuid), str(media2.uuid)], "contacts": [self.joe.uuid, self.frank.uuid]},
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
            {"text": "Hello", "contacts": [self.joe.uuid]},
            errors={"non_field_errors": Org.BLOCKER_FLAGGED},
        )

    def test_campaigns(self):
        endpoint_url = reverse("api.v2.campaigns") + ".json"

        self.assertGetNotPermitted(endpoint_url, [None, self.agent])
        self.assertPostNotPermitted(endpoint_url, [None, self.user, self.agent])
        self.assertDeleteNotAllowed(endpoint_url)

        reporters = self.create_group("Reporters", [self.joe, self.frank])
        other_group = self.create_group("Others", [])
        campaign1 = Campaign.create(self.org, self.admin, "Reminders #1", reporters)
        campaign2 = Campaign.create(self.org, self.admin, "Reminders #2", reporters)

        # create campaign for other org
        spammers = ContactGroup.get_or_create(self.org2, self.admin2, "Spammers")
        spam = Campaign.create(self.org2, self.admin2, "Spam", spammers)

        # no filtering
        response = self.assertGet(
            endpoint_url,
            [self.user, self.editor, self.admin],
            results=[
                {
                    "uuid": str(campaign2.uuid),
                    "name": "Reminders #2",
                    "archived": False,
                    "group": {"uuid": reporters.uuid, "name": "Reporters"},
                    "created_on": format_datetime(campaign2.created_on),
                },
                {
                    "uuid": str(campaign1.uuid),
                    "name": "Reminders #1",
                    "archived": False,
                    "group": {"uuid": reporters.uuid, "name": "Reporters"},
                    "created_on": format_datetime(campaign1.created_on),
                },
            ],
            num_queries=NUM_BASE_SESSION_QUERIES + 2,
        )

        # filter by UUID
        self.assertGet(endpoint_url + f"?uuid={campaign1.uuid}", [self.editor], results=[campaign1])

        # try to create empty campaign
        self.assertPost(
            endpoint_url,
            self.editor,
            {},
            errors={"name": "This field is required.", "group": "This field is required."},
        )

        # create new campaign
        response = self.assertPost(
            endpoint_url, self.editor, {"name": "Reminders #3", "group": reporters.uuid}, status=201
        )

        campaign3 = Campaign.objects.get(name="Reminders #3")
        self.assertEqual(
            response.json(),
            {
                "uuid": str(campaign3.uuid),
                "name": "Reminders #3",
                "archived": False,
                "group": {"uuid": reporters.uuid, "name": "Reporters"},
                "created_on": format_datetime(campaign3.created_on),
            },
        )

        # try to create another campaign with same name
        self.assertPost(
            endpoint_url,
            self.editor,
            {"name": "Reminders #3", "group": reporters.uuid},
            errors={"name": "This field must be unique."},
        )

        # it's fine if a campaign in another org has that name
        self.assertPost(endpoint_url, self.editor, {"name": "Spam", "group": reporters.uuid}, status=201)

        # try to create a campaign with name that's too long
        self.assertPost(
            endpoint_url,
            self.editor,
            {"name": "x" * 65, "group": reporters.uuid},
            errors={"name": "Ensure this field has no more than 64 characters."},
        )

        # update campaign by UUID
        self.assertPost(
            endpoint_url + f"?uuid={campaign3.uuid}", self.editor, {"name": "Reminders III", "group": other_group.uuid}
        )

        campaign3.refresh_from_db()
        self.assertEqual(campaign3.name, "Reminders III")
        self.assertEqual(campaign3.group, other_group)

        # can't update campaign in other org
        self.assertPost(
            endpoint_url + f"?uuid={spam.uuid}", self.editor, {"name": "Won't work", "group": spammers.uuid}, status=404
        )

        # can't update deleted campaign
        campaign1.is_active = False
        campaign1.save(update_fields=("is_active",))

        self.assertPost(
            endpoint_url + f"?uuid={campaign1.uuid}",
            self.editor,
            {"name": "Won't work", "group": spammers.uuid},
            status=404,
        )

        # can't update inactive or archived campaign
        campaign1.is_active = True
        campaign1.is_archived = True
        campaign1.save(update_fields=("is_active", "is_archived"))

        self.assertPost(
            endpoint_url + f"?uuid={campaign1.uuid}",
            self.editor,
            {"name": "Won't work", "group": spammers.uuid},
            status=404,
        )

    @mock_mailroom
    def test_campaign_events(self, mr_mocks):
        endpoint_url = reverse("api.v2.campaign_events") + ".json"

        self.assertGetNotPermitted(endpoint_url, [None, self.agent])
        self.assertPostNotPermitted(endpoint_url, [None, self.user, self.agent])
        self.assertDeleteNotPermitted(endpoint_url, [None, self.user, self.agent])

        flow = self.create_flow("Test Flow")
        reporters = self.create_group("Reporters", [self.joe, self.frank])
        registration = self.create_field("registration", "Registration", value_type=ContactField.TYPE_DATETIME)
        field_created_on = self.org.fields.get(key="created_on")

        # create our contact and set a registration date
        contact = self.create_contact(
            "Joe", phone="+12065551515", fields={"registration": self.org.format_datetime(timezone.now())}
        )
        reporters.contacts.add(contact)

        campaign1 = Campaign.create(self.org, self.admin, "Reminders", reporters)
        event1 = CampaignEvent.create_message_event(
            self.org,
            self.admin,
            campaign1,
            registration,
            1,
            CampaignEvent.UNIT_DAYS,
            "Don't forget to brush your teeth",
        )

        campaign2 = Campaign.create(self.org, self.admin, "Notifications", reporters)
        event2 = CampaignEvent.create_flow_event(
            self.org, self.admin, campaign2, registration, 6, CampaignEvent.UNIT_HOURS, flow, delivery_hour=12
        )

        campaign3 = Campaign.create(self.org, self.admin, "Alerts", reporters)
        event3 = CampaignEvent.create_flow_event(
            self.org, self.admin, campaign3, field_created_on, 6, CampaignEvent.UNIT_HOURS, flow, delivery_hour=12
        )

        # create event for another org
        joined = self.create_field("joined", "Joined On", value_type=ContactField.TYPE_DATETIME)
        spammers = ContactGroup.get_or_create(self.org2, self.admin2, "Spammers")
        spam = Campaign.create(self.org2, self.admin2, "Cool stuff", spammers)
        CampaignEvent.create_flow_event(
            self.org2, self.admin2, spam, joined, 6, CampaignEvent.UNIT_HOURS, flow, delivery_hour=12
        )

        # no filtering
        self.assertGet(
            endpoint_url,
            [self.user, self.editor, self.admin],
            results=[
                {
                    "uuid": str(event3.uuid),
                    "campaign": {"uuid": str(campaign3.uuid), "name": "Alerts"},
                    "relative_to": {"key": "created_on", "name": "Created On", "label": "Created On"},
                    "offset": 6,
                    "unit": "hours",
                    "delivery_hour": 12,
                    "flow": {"uuid": flow.uuid, "name": "Test Flow"},
                    "message": None,
                    "created_on": format_datetime(event3.created_on),
                },
                {
                    "uuid": str(event2.uuid),
                    "campaign": {"uuid": str(campaign2.uuid), "name": "Notifications"},
                    "relative_to": {"key": "registration", "name": "Registration", "label": "Registration"},
                    "offset": 6,
                    "unit": "hours",
                    "delivery_hour": 12,
                    "flow": {"uuid": flow.uuid, "name": "Test Flow"},
                    "message": None,
                    "created_on": format_datetime(event2.created_on),
                },
                {
                    "uuid": str(event1.uuid),
                    "campaign": {"uuid": str(campaign1.uuid), "name": "Reminders"},
                    "relative_to": {"key": "registration", "name": "Registration", "label": "Registration"},
                    "offset": 1,
                    "unit": "days",
                    "delivery_hour": -1,
                    "flow": None,
                    "message": {"eng": "Don't forget to brush your teeth"},
                    "created_on": format_datetime(event1.created_on),
                },
            ],
            num_queries=NUM_BASE_SESSION_QUERIES + 4,
        )

        # filter by UUID
        self.assertGet(endpoint_url + f"?uuid={event1.uuid}", [self.editor], results=[event1])

        # filter by campaign name
        self.assertGet(endpoint_url + "?campaign=Reminders", [self.editor], results=[event1])

        # filter by campaign UUID
        self.assertGet(endpoint_url + f"?campaign={campaign1.uuid}", [self.editor], results=[event1])

        # filter by invalid campaign
        self.assertGet(endpoint_url + "?campaign=Invalid", [self.editor], results=[])

        # try to create empty campaign event
        self.assertPost(
            endpoint_url,
            self.editor,
            {},
            errors={
                "campaign": "This field is required.",
                "relative_to": "This field is required.",
                "offset": "This field is required.",
                "unit": "This field is required.",
                "delivery_hour": "This field is required.",
            },
        )

        # try again with some invalid values
        self.assertPost(
            endpoint_url,
            self.editor,
            {
                "campaign": str(campaign1.uuid),
                "relative_to": "registration",
                "offset": 15,
                "unit": "epocs",
                "delivery_hour": 25,
                "message": {"kin": "Muraho"},
            },
            errors={
                "unit": '"epocs" is not a valid choice.',
                "delivery_hour": "Ensure this value is less than or equal to 23.",
                "message": "Message text in default flow language is required.",
            },
        )

        # provide valid values for those fields.. but not a message or flow
        self.assertPost(
            endpoint_url,
            self.editor,
            {
                "campaign": str(campaign1.uuid),
                "relative_to": "registration",
                "offset": 15,
                "unit": "weeks",
                "delivery_hour": -1,
            },
            errors={
                "non_field_errors": "Flow or a message text required.",
            },
        )

        # create a message event
        self.assertPost(
            endpoint_url,
            self.editor,
            {
                "campaign": str(campaign1.uuid),
                "relative_to": "registration",
                "offset": 15,
                "unit": "weeks",
                "delivery_hour": -1,
                "message": "You are @fields.age",
            },
            status=201,
        )

        event1 = CampaignEvent.objects.filter(campaign=campaign1).order_by("-id").first()
        self.assertEqual(event1.event_type, CampaignEvent.TYPE_MESSAGE)
        self.assertEqual(event1.relative_to, registration)
        self.assertEqual(event1.offset, 15)
        self.assertEqual(event1.unit, "W")
        self.assertEqual(event1.delivery_hour, -1)
        self.assertEqual(event1.message, {"eng": "You are @fields.age"})
        self.assertIsNotNone(event1.flow)

        # try to create a message event with an empty message
        self.assertPost(
            endpoint_url,
            self.editor,
            {
                "campaign": str(campaign1.uuid),
                "relative_to": "registration",
                "offset": 15,
                "unit": "weeks",
                "delivery_hour": -1,
                "message": "",
            },
            errors={("message", "eng"): "This field may not be blank."},
        )

        self.assertPost(
            endpoint_url,
            self.editor,
            {
                "campaign": str(campaign1.uuid),
                "relative_to": "created_on",
                "offset": 15,
                "unit": "days",
                "delivery_hour": -1,
                "message": "Nice unit of work @fields.code",
            },
            status=201,
        )

        event1 = CampaignEvent.objects.filter(campaign=campaign1).order_by("-id").first()
        self.assertEqual(event1.event_type, CampaignEvent.TYPE_MESSAGE)
        self.assertEqual(event1.relative_to, field_created_on)
        self.assertEqual(event1.offset, 15)
        self.assertEqual(event1.unit, "D")
        self.assertEqual(event1.delivery_hour, -1)
        self.assertEqual(event1.message, {"eng": "Nice unit of work @fields.code"})
        self.assertIsNotNone(event1.flow)

        # create a flow event
        self.assertPost(
            endpoint_url,
            self.editor,
            {
                "campaign": str(campaign1.uuid),
                "relative_to": "registration",
                "offset": 15,
                "unit": "weeks",
                "delivery_hour": -1,
                "flow": str(flow.uuid),
            },
            status=201,
        )

        event2 = CampaignEvent.objects.filter(campaign=campaign1).order_by("-id").first()
        self.assertEqual(event2.event_type, CampaignEvent.TYPE_FLOW)
        self.assertEqual(event2.relative_to, registration)
        self.assertEqual(event2.offset, 15)
        self.assertEqual(event2.unit, "W")
        self.assertEqual(event2.delivery_hour, -1)
        self.assertEqual(event2.message, None)
        self.assertEqual(event2.flow, flow)

        # make sure we queued a mailroom task to schedule this event
        self.assertEqual(
            {
                "org_id": self.org.id,
                "type": "schedule_campaign_event",
                "queued_on": matchers.Datetime(),
                "task": {"campaign_event_id": event2.id, "org_id": self.org.id},
            },
            mr_mocks.queued_batch_tasks[-1],
        )

        # update the message event to be a flow event
        self.assertPost(
            endpoint_url + f"?uuid={event1.uuid}",
            self.editor,
            {
                "campaign": str(campaign1.uuid),
                "relative_to": "registration",
                "offset": 15,
                "unit": "weeks",
                "delivery_hour": -1,
                "flow": str(flow.uuid),
            },
        )

        event1 = CampaignEvent.objects.filter(campaign=campaign1).order_by("-id").first()

        self.assertEqual(event1.event_type, CampaignEvent.TYPE_FLOW)
        self.assertIsNone(event1.message)
        self.assertEqual(event1.flow, flow)

        # and update the flow event to be a message event
        self.assertPost(
            endpoint_url + f"?uuid={event2.uuid}",
            self.editor,
            {
                "campaign": str(campaign1.uuid),
                "relative_to": "registration",
                "offset": 15,
                "unit": "weeks",
                "delivery_hour": -1,
                "message": {"eng": "OK @(format_urn(urns.tel))", "fra": "D'accord"},
            },
        )

        event2 = CampaignEvent.objects.filter(campaign=campaign1).order_by("-id").first()
        self.assertEqual(event2.event_type, CampaignEvent.TYPE_MESSAGE)
        self.assertEqual(event2.message, {"eng": "OK @(format_urn(urns.tel))", "fra": "D'accord"})

        # and update update it's message again
        self.assertPost(
            endpoint_url + f"?uuid={event2.uuid}",
            self.editor,
            {
                "campaign": str(campaign1.uuid),
                "relative_to": "registration",
                "offset": 15,
                "unit": "weeks",
                "delivery_hour": -1,
                "message": {"eng": "OK", "fra": "D'accord", "kin": "Sawa"},
            },
        )

        event2 = CampaignEvent.objects.filter(campaign=campaign1).order_by("-id").first()
        self.assertEqual(event2.event_type, CampaignEvent.TYPE_MESSAGE)
        self.assertEqual(event2.message, {"eng": "OK", "fra": "D'accord", "kin": "Sawa"})

        # try to change an existing event's campaign
        self.assertPost(
            endpoint_url + f"?uuid={event1.uuid}",
            self.editor,
            {
                "campaign": str(campaign2.uuid),
                "relative_to": "registration",
                "offset": 15,
                "unit": "weeks",
                "delivery_hour": -1,
                "message": {"eng": "OK", "fra": "D'accord", "kin": "Sawa"},
            },
            errors={"campaign": "Cannot change campaign for existing events"},
        )

        # try an empty delete request
        self.assertDelete(
            endpoint_url, self.editor, errors={None: "URL must contain one of the following parameters: uuid"}
        )

        # delete an event by UUID
        self.assertDelete(endpoint_url + f"?uuid={event1.uuid}", self.editor)

        self.assertFalse(CampaignEvent.objects.filter(id=event1.id, is_active=True).exists())

        # can't make changes to events on archived campaigns
        campaign1.archive(self.admin)

        self.assertPost(
            endpoint_url + f"?uuid={event2.uuid}",
            self.editor,
            {
                "campaign": str(campaign1.uuid),
                "relative_to": "registration",
                "offset": 15,
                "unit": "weeks",
                "delivery_hour": -1,
                "message": {"eng": "OK", "fra": "D'accord", "kin": "Sawa"},
            },
            errors={"campaign": f"No such object: {campaign1.uuid}"},
        )

    def test_channels(self):
        endpoint_url = reverse("api.v2.channels") + ".json"

        self.assertGetNotPermitted(endpoint_url, [None, self.agent])
        self.assertPostNotAllowed(endpoint_url)
        self.assertDeleteNotAllowed(endpoint_url)

        # create deleted channel
        deleted = self.create_channel("JC", "Deleted", "nyaruka")
        deleted.release(self.admin)

        # create channel for other org
        self.create_channel("TWT", "Twitter Channel", "nyaruka", org=self.org2)

        # no filtering
        self.assertGet(
            endpoint_url,
            [self.user, self.editor, self.admin],
            results=[
                {
                    "uuid": self.twitter.uuid,
                    "name": "Twitter Channel",
                    "address": "billy_bob",
                    "country": None,
                    "device": None,
                    "last_seen": None,
                    "created_on": format_datetime(self.twitter.created_on),
                },
                {
                    "uuid": self.channel.uuid,
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
            num_queries=NUM_BASE_SESSION_QUERIES + 2,
        )

        # filter by UUID
        self.assertGet(endpoint_url + f"?uuid={self.twitter.uuid}", [self.admin], results=[self.twitter])

        # filter by address
        self.assertGet(endpoint_url + "?address=billy_bob", [self.admin], results=[self.twitter])

    def test_channel_events(self):
        endpoint_url = reverse("api.v2.channel_events") + ".json"

        self.assertGetNotPermitted(endpoint_url, [None, self.agent])
        self.assertPostNotAllowed(endpoint_url)
        self.assertDeleteNotAllowed(endpoint_url)

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
            num_queries=NUM_BASE_SESSION_QUERIES + 3,
        )

        resp_json = response.json()
        self.assertEqual(
            resp_json["results"][0],
            {
                "id": call4.pk,
                "channel": {"uuid": self.channel.uuid, "name": "Test Channel"},
                "type": "call-out",
                "contact": {"uuid": self.joe.uuid, "name": self.joe.name},
                "occurred_on": format_datetime(call4.occurred_on),
                "extra": dict(duration=15),
                "created_on": format_datetime(call4.created_on),
            },
        )

        # filter by id
        self.assertGet(endpoint_url + f"?id={call1.id}", [self.editor], results=[call1])

        # filter by contact
        self.assertGet(endpoint_url + f"?contact={self.joe.uuid}", [self.editor], results=[call4, call1])

        # filter by invalid contact
        self.assertGet(endpoint_url + "?contact=invalid", [self.editor], results=[])

        # filter by before / after
        self.assertGet(
            endpoint_url + f"?before={format_datetime(call3.created_on)}", [self.editor], results=[call3, call2, call1]
        )
        self.assertGet(
            endpoint_url + f"?after={format_datetime(call2.created_on)}", [self.editor], results=[call4, call3, call2]
        )

    def test_classifiers(self):
        endpoint_url = reverse("api.v2.classifiers") + ".json"

        self.assertGetNotPermitted(endpoint_url, [None, self.agent])
        self.assertPostNotAllowed(endpoint_url)
        self.assertDeleteNotAllowed(endpoint_url)

        # create some classifiers
        c1 = Classifier.create(self.org, self.admin, WitType.slug, "Booker", {})
        c1.intents.create(name="book_flight", external_id="book_flight", created_on=timezone.now(), is_active=True)
        c1.intents.create(name="book_hotel", external_id="book_hotel", created_on=timezone.now(), is_active=False)
        c1.intents.create(name="book_car", external_id="book_car", created_on=timezone.now(), is_active=True)

        c2 = Classifier.create(self.org, self.admin, WitType.slug, "Old Booker", {})
        c2.is_active = False
        c2.save()

        # on another org
        Classifier.create(self.org2, self.admin, LuisType.slug, "Org2 Booker", {})

        # no filtering
        self.assertGet(
            endpoint_url,
            [self.user, self.editor, self.admin],
            results=[
                {
                    "name": "Booker",
                    "type": "wit",
                    "uuid": str(c1.uuid),
                    "intents": ["book_car", "book_flight"],
                    "created_on": format_datetime(c1.created_on),
                }
            ],
            num_queries=NUM_BASE_SESSION_QUERIES + 2,
        )

        # filter by uuid (not there)
        self.assertGet(endpoint_url + "?uuid=09d23a05-47fe-11e4-bfe9-b8f6b119e9ab", [self.editor], results=[])

        # filter by uuid present
        self.assertGet(endpoint_url + f"?uuid={c1.uuid}", [self.user, self.editor, self.admin], results=[c1])

    @mock_mailroom
    def test_contacts(self, mr_mocks):
        endpoint_url = reverse("api.v2.contacts") + ".json"

        self.assertGetNotPermitted(endpoint_url, [None])
        self.assertPostNotPermitted(endpoint_url, [None, self.user])
        self.assertDeleteNotPermitted(endpoint_url, [None, self.user, self.agent])

        # create some more contacts (in addition to Joe and Frank)
        contact1 = self.create_contact(
            "Ann", phone="0788000001", language="fra", fields={"nickname": "Annie", "gender": "female"}
        )
        contact2 = self.create_contact("Bob", phone="0788000002")
        contact3 = self.create_contact("Cat", phone="0788000003")
        contact4 = self.create_contact(
            "Don", phone="0788000004", language="fra", fields={"nickname": "Donnie", "gender": "male"}
        )

        contact1.stop(self.user)
        contact2.block(self.user)
        contact3.release(self.user)

        # put some contacts in a group
        group = self.create_group("Customers", contacts=[self.joe, contact4])
        other_org_group = self.create_group("Nerds", org=self.org2)

        # tweak modified_on so we get the order we want
        self.joe.modified_on = timezone.now()
        self.joe.save(update_fields=("modified_on",))

        survey = self.create_flow("Survey")
        contact4.modified_on = timezone.now()
        contact4.last_seen_on = datetime(2020, 8, 12, 13, 30, 45, 123456, tzone.utc)
        contact4.current_flow = survey
        contact4.save(update_fields=("modified_on", "last_seen_on", "current_flow"))

        contact1.refresh_from_db()
        contact4.refresh_from_db()
        self.joe.refresh_from_db()

        # create contact for other org
        hans = self.create_contact("Hans", phone="0788000004", org=self.org2)

        # no filtering
        response = self.assertGet(
            endpoint_url,
            [self.user, self.editor, self.admin, self.agent],
            results=[contact4, self.joe, contact2, contact1, self.frank],
            num_queries=NUM_BASE_SESSION_QUERIES + 7,
        )
        self.assertEqual(
            {
                "uuid": contact4.uuid,
                "name": "Don",
                "status": "active",
                "language": "fra",
                "urns": ["tel:+250788000004"],
                "groups": [{"uuid": group.uuid, "name": group.name}],
                "notes": [],
                "fields": {"nickname": "Donnie", "gender": "male"},
                "flow": {"uuid": str(survey.uuid), "name": "Survey"},
                "created_on": format_datetime(contact4.created_on),
                "modified_on": format_datetime(contact4.modified_on),
                "last_seen_on": "2020-08-12T13:30:45.123456Z",
                "blocked": False,
                "stopped": False,
            },
            response.json()["results"][0],
        )

        # no filtering with token auth
        response = self.assertGet(
            endpoint_url,
            [self.admin],
            results=[contact4, self.joe, contact2, contact1, self.frank],
            by_token=True,
            num_queries=NUM_BASE_TOKEN_QUERIES + 7,
        )

        # with expanded URNs
        response = self.assertGet(
            endpoint_url + "?expand_urns=true",
            [self.user],
            results=[contact4, self.joe, contact2, contact1, self.frank],
        )
        self.assertEqual(
            {
                "uuid": contact4.uuid,
                "name": "Don",
                "status": "active",
                "language": "fra",
                "urns": [
                    {
                        "channel": {"uuid": str(self.channel.uuid), "name": "Test Channel"},
                        "scheme": "tel",
                        "path": "+250788000004",
                        "display": None,
                    }
                ],
                "groups": [{"uuid": group.uuid, "name": group.name}],
                "notes": [],
                "fields": {"nickname": "Donnie", "gender": "male"},
                "flow": {"uuid": str(survey.uuid), "name": "Survey"},
                "created_on": format_datetime(contact4.created_on),
                "modified_on": format_datetime(contact4.modified_on),
                "last_seen_on": "2020-08-12T13:30:45.123456Z",
                "blocked": False,
                "stopped": False,
            },
            response.json()["results"][0],
        )

        # reversed
        response = self.assertGet(
            endpoint_url + "?reverse=true",
            [self.user],
            results=[self.frank, contact1, contact2, self.joe, contact4],
        )

        with self.anonymous(self.org):
            response = self.assertGet(
                endpoint_url,
                [self.user, self.editor, self.admin, self.agent],
                results=[contact4, self.joe, contact2, contact1, self.frank],
                num_queries=NUM_BASE_SESSION_QUERIES + 7,
            )
            self.assertEqual(
                {
                    "uuid": contact4.uuid,
                    "name": "Don",
                    "anon_display": f"{contact4.id:010}",
                    "status": "active",
                    "language": "fra",
                    "urns": ["tel:********"],
                    "groups": [{"uuid": group.uuid, "name": group.name}],
                    "notes": [],
                    "fields": {"nickname": "Donnie", "gender": "male"},
                    "flow": {"uuid": str(survey.uuid), "name": "Survey"},
                    "created_on": format_datetime(contact4.created_on),
                    "modified_on": format_datetime(contact4.modified_on),
                    "last_seen_on": "2020-08-12T13:30:45.123456Z",
                    "blocked": False,
                    "stopped": False,
                },
                response.json()["results"][0],
            )

            # with expanded URNs
            response = self.assertGet(
                endpoint_url + "?expand_urns=true",
                [self.user],
                results=[contact4, self.joe, contact2, contact1, self.frank],
            )
            self.assertEqual(
                {
                    "uuid": contact4.uuid,
                    "name": "Don",
                    "anon_display": f"{contact4.id:010}",
                    "status": "active",
                    "language": "fra",
                    "urns": [
                        {
                            "channel": {"uuid": str(self.channel.uuid), "name": "Test Channel"},
                            "scheme": "tel",
                            "path": "********",
                            "display": None,
                        }
                    ],
                    "groups": [{"uuid": group.uuid, "name": group.name}],
                    "notes": [],
                    "fields": {"nickname": "Donnie", "gender": "male"},
                    "flow": {"uuid": str(survey.uuid), "name": "Survey"},
                    "created_on": format_datetime(contact4.created_on),
                    "modified_on": format_datetime(contact4.modified_on),
                    "last_seen_on": "2020-08-12T13:30:45.123456Z",
                    "blocked": False,
                    "stopped": False,
                },
                response.json()["results"][0],
            )

        # filter by UUID
        self.assertGet(endpoint_url + f"?uuid={contact2.uuid}", [self.editor], results=[contact2])

        # filter by URN (which should be normalized)
        self.assertGet(endpoint_url + f"?urn={quote_plus('tel:078-8000004')}", [self.editor], results=[contact4])

        # error if URN can't be parsed
        self.assertGet(endpoint_url + "?urn=12345", [self.editor], errors={None: "Invalid URN: 12345"})

        # filter by group UUID / name
        self.assertGet(endpoint_url + f"?group={group.uuid}", [self.editor], results=[contact4, self.joe])
        self.assertGet(endpoint_url + "?group=Customers", [self.editor], results=[contact4, self.joe])

        # filter by invalid group
        self.assertGet(endpoint_url + "?group=invalid", [self.editor], results=[])

        # filter by before / after
        self.assertGet(
            endpoint_url + f"?before={format_datetime(contact1.modified_on)}",
            [self.editor],
            results=[contact1, self.frank],
        )
        self.assertGet(
            endpoint_url + f"?after={format_datetime(self.joe.modified_on)}",
            [self.editor],
            results=[contact4, self.joe],
        )

        # view the deleted contact
        self.assertGet(
            endpoint_url + "?deleted=true",
            [self.editor],
            results=[
                {
                    "uuid": contact3.uuid,
                    "name": None,
                    "status": None,
                    "language": None,
                    "urns": [],
                    "groups": [],
                    "notes": [],
                    "fields": {},
                    "flow": None,
                    "created_on": format_datetime(contact3.created_on),
                    "modified_on": format_datetime(contact3.modified_on),
                    "last_seen_on": None,
                    "blocked": None,
                    "stopped": None,
                }
            ],
        )

        # try to post something other than an object
        self.assertPost(
            endpoint_url, self.editor, [], errors={"non_field_errors": "Request body should be a single JSON object"}
        )

        # create an empty contact
        response = self.assertPost(endpoint_url, self.editor, {}, status=201)

        empty = Contact.objects.get(name=None, is_active=True)
        self.assertEqual(
            {
                "uuid": empty.uuid,
                "name": None,
                "status": "active",
                "language": None,
                "urns": [],
                "groups": [],
                "notes": [],
                "fields": {"nickname": None, "gender": None},
                "flow": None,
                "created_on": format_datetime(empty.created_on),
                "modified_on": format_datetime(empty.modified_on),
                "last_seen_on": None,
                "blocked": False,
                "stopped": False,
            },
            response.json(),
        )

        # create with all fields but empty
        response = self.assertPost(
            endpoint_url,
            self.editor,
            {"name": None, "language": None, "urns": [], "groups": [], "fields": {}},
            status=201,
        )

        jaqen = Contact.objects.order_by("id").last()
        self.assertIsNone(jaqen.name)
        self.assertIsNone(jaqen.language)
        self.assertEqual(Contact.STATUS_ACTIVE, jaqen.status)
        self.assertEqual(set(), set(jaqen.urns.all()))
        self.assertEqual(set(), set(jaqen.get_groups()))
        self.assertIsNone(jaqen.fields)

        # create with all fields
        self.assertPost(
            endpoint_url,
            self.editor,
            {
                "name": "Jean",
                "language": "fra",
                "urns": ["tel:+250783333333", "twitter:JEAN"],
                "groups": [group.uuid],
                "fields": {"nickname": "Jado"},
            },
            status=201,
        )

        # URNs will be normalized
        nickname = self.org.fields.get(key="nickname")
        gender = self.org.fields.get(key="gender")
        jean = Contact.objects.filter(name="Jean", language="fra").order_by("-pk").first()
        self.assertEqual(set(jean.urns.values_list("identity", flat=True)), {"tel:+250783333333", "twitter:jean"})
        self.assertEqual(set(jean.get_groups()), {group})
        self.assertEqual(jean.get_field_value(nickname), "Jado")

        # try to create with group from other org
        self.assertPost(
            endpoint_url,
            self.editor,
            {"name": "Jim", "groups": [other_org_group.uuid]},
            errors={"groups": f"No such object: {other_org_group.uuid}"},
        )

        # try to create with invalid fields
        response = self.assertPost(
            endpoint_url,
            self.editor,
            {
                "name": "Jim",
                "language": "xyz",
                "urns": ["1234556789"],
                "groups": ["59686b4e-14bc-4160-9376-b649b218c806"],
                "fields": {"hmmm": "X"},
            },
            errors={
                "language": "Not a valid ISO639-3 language code.",
                "groups": "No such object: 59686b4e-14bc-4160-9376-b649b218c806",
                "fields": "Invalid contact field key: hmmm",
                ("urns", "0"): "Invalid URN: 1234556789. Ensure phone numbers contain country codes.",
            },
        )

        # update an existing contact by UUID but don't provide any fields
        self.assertPost(endpoint_url + f"?uuid={jean.uuid}", self.editor, {})

        # contact should be unchanged
        jean.refresh_from_db()
        self.assertEqual(jean.name, "Jean")
        self.assertEqual(jean.language, "fra")
        self.assertEqual(set(jean.urns.values_list("identity", flat=True)), {"tel:+250783333333", "twitter:jean"})
        self.assertEqual(set(jean.get_groups()), {group})
        self.assertEqual(jean.get_field_value(nickname), "Jado")

        # update by UUID and change all fields
        self.assertPost(
            endpoint_url + f"?uuid={jean.uuid}",
            self.editor,
            {
                "name": "Jason Undead",
                "language": "ita",
                "urns": ["tel:+250784444444"],
                "groups": [],
                "fields": {"nickname": "Žan", "gender": "frog"},
            },
        )

        jean.refresh_from_db()
        self.assertEqual(jean.name, "Jason Undead")
        self.assertEqual(jean.language, "ita")
        self.assertEqual(set(jean.urns.values_list("identity", flat=True)), {"tel:+250784444444"})
        self.assertEqual(set(jean.get_groups()), set())
        self.assertEqual(jean.get_field_value(nickname), "Žan")
        self.assertEqual(jean.get_field_value(gender), "frog")

        # change the language field
        self.assertPost(
            endpoint_url + f"?uuid={jean.uuid}",
            self.editor,
            {"name": "Jean II", "language": "eng", "urns": ["tel:+250784444444"], "groups": [], "fields": {}},
        )

        jean.refresh_from_db()
        self.assertEqual(jean.name, "Jean II")
        self.assertEqual(jean.language, "eng")
        self.assertEqual(set(jean.urns.values_list("identity", flat=True)), {"tel:+250784444444"})
        self.assertEqual(set(jean.get_groups()), set())
        self.assertEqual(jean.get_field_value(nickname), "Žan")

        # update by uuid and remove all fields
        self.assertPost(
            endpoint_url + f"?uuid={jean.uuid}",
            self.editor,
            {
                "name": "Jean II",
                "language": "eng",
                "urns": ["tel:+250784444444"],
                "groups": [],
                "fields": {"nickname": "", "gender": ""},
            },
        )

        jean.refresh_from_db()
        self.assertEqual(jean.get_field_value(nickname), None)
        self.assertEqual(jean.get_field_value(gender), None)

        # update by uuid and update/remove fields
        self.assertPost(
            endpoint_url + f"?uuid={jean.uuid}",
            self.editor,
            {
                "name": "Jean II",
                "language": "eng",
                "urns": ["tel:+250784444444"],
                "groups": [],
                "fields": {"nickname": "Jado", "gender": ""},
            },
        )

        jean.refresh_from_db()
        self.assertEqual(jean.get_field_value(nickname), "Jado")
        self.assertEqual(jean.get_field_value(gender), None)

        # update by URN (which should be normalized)
        self.assertPost(endpoint_url + f"?urn={quote_plus('tel:+250-78-4444444')}", self.editor, {"name": "Jean III"})

        jean.refresh_from_db()
        self.assertEqual(jean.name, "Jean III")

        # try to specify URNs field whilst referencing by URN
        self.assertPost(
            endpoint_url + f"?urn={quote_plus('tel:+250-78-4444444')}",
            self.editor,
            {"urns": ["tel:+250785555555"]},
            errors={"urns": "Field not allowed when using URN in URL"},
        )

        # if contact doesn't exist with URN, they're created
        self.assertPost(
            endpoint_url + f"?urn={quote_plus('tel:+250-78-5555555')}", self.editor, {"name": "Bobby"}, status=201
        )

        # URN should be normalized
        bobby = Contact.objects.get(name="Bobby")
        self.assertEqual(set(bobby.urns.values_list("identity", flat=True)), {"tel:+250785555555"})

        # try to create a contact with a URN belonging to another contact
        self.assertPost(
            endpoint_url,
            self.editor,
            {"name": "Robert", "urns": ["tel:+250-78-5555555"]},
            errors={("urns", "0"): "URN is in use by another contact."},
        )

        # try to update a contact with non-existent UUID
        self.assertPost(endpoint_url + "?uuid=ad6acad9-959b-4d70-b144-5de2891e4d00", self.editor, {}, status=404)

        # try to update a contact in another org
        self.assertPost(endpoint_url + f"?uuid={hans.uuid}", self.editor, {}, status=404)

        # try to add a contact to a dynamic group
        dyn_group = self.create_group("Dynamic Group", query="name = Frank")
        ContactGroup.objects.filter(id=dyn_group.id).update(status=ContactGroup.STATUS_READY)
        self.assertPost(
            endpoint_url + f"?uuid={jean.uuid}",
            self.editor,
            {"groups": [dyn_group.uuid]},
            errors={"groups": "Contact group must not be query based: %s" % dyn_group.uuid},
        )

        # try to give a contact more than 100 URNs
        self.assertPost(
            endpoint_url + f"?uuid={jean.uuid}",
            self.editor,
            {"urns": ["twitter:bob%d" % u for u in range(101)]},
            errors={"urns": "Ensure this field has no more than 100 elements."},
        )

        # try to give a contact more than 100 contact fields
        self.assertPost(
            endpoint_url + f"?uuid={jean.uuid}",
            self.editor,
            {"fields": {"field_%d" % f: f for f in range(101)}},
            errors={"fields": "Ensure this field has no more than 100 elements."},
        )

        # ok to give them 100 URNs
        self.assertPost(
            endpoint_url + f"?uuid={jean.uuid}",
            self.editor,
            {"urns": ["twitter:bob%d" % u for u in range(100)]},
        )
        self.assertEqual(jean.urns.count(), 100)

        # try to move a blocked contact into a group
        jean.block(self.user)
        self.assertPost(
            endpoint_url + f"?uuid={jean.uuid}",
            self.editor,
            {"groups": [group.uuid]},
            errors={"groups": "Non-active contacts can't be added to groups"},
        )

        # try to update a contact by both UUID and URN
        self.assertPost(
            endpoint_url + f"?uuid={jean.uuid}&urn={quote_plus('tel:+250784444444')}",
            self.editor,
            {},
            errors={None: "URL can only contain one of the following parameters: urn, uuid"},
        )

        # try an empty delete request
        self.assertDelete(
            endpoint_url,
            self.editor,
            errors={None: "URL must contain one of the following parameters: urn, uuid"},
        )

        # delete a contact by UUID
        self.assertDelete(endpoint_url + f"?uuid={jean.uuid}", self.editor, status=204)

        jean.refresh_from_db()
        self.assertFalse(jean.is_active)

        self.assertPost(
            endpoint_url + f"?uuid={jean.uuid}",
            self.admin,
            {},
            errors={"non_field_errors": "Deleted contacts can't be modified."},
        )

        # create xavier
        self.assertPost(
            endpoint_url, self.admin, {"name": "Xavier", "urns": ["tel:+250-78-7777777", "twitter:XAVIER"]}, status=201
        )

        xavier = Contact.objects.get(name="Xavier")
        self.assertEqual(set(xavier.urns.values_list("identity", flat=True)), {"twitter:xavier", "tel:+250787777777"})

        # updating fields by urn should keep all exiting urns
        self.assertPost(
            endpoint_url + f"?urn={quote_plus('tel:+250787777777')}", self.admin, {"fields": {"gender": "Male"}}
        )

        xavier.refresh_from_db()
        self.assertEqual(set(xavier.urns.values_list("identity", flat=True)), {"twitter:xavier", "tel:+250787777777"})
        self.assertEqual(xavier.get_field_value(gender), "Male")

        # delete a contact by URN (which should be normalized)
        self.assertDelete(endpoint_url + f"?urn={quote_plus('twitter:XAVIER')}", self.editor, status=204)

        xavier.refresh_from_db()
        self.assertFalse(xavier.is_active)

        # try deleting a contact by a non-existent URN
        self.assertDelete(endpoint_url + "?urn=twitter:billy", self.editor, status=404)

        # try to delete a contact in another org
        self.assertDelete(endpoint_url + f"?uuid={hans.uuid}", self.editor, status=404)

        # add some notes for frank
        frank_url = endpoint_url + f"?uuid={self.frank.uuid}"
        for i in range(1, 6):
            self.assertPost(
                frank_url,
                self.admin,
                {"note": f"Frank is a good guy ({i})"},
            )

        # four more notes by another user to make sure prefetch works
        for i in range(6, 10):
            self.assertPost(
                frank_url,
                self.editor,
                {"note": f"Frank is an okay guy ({i})"},
            )

        self.frank.refresh_from_db()
        response = self.assertGet(
            frank_url, [self.editor], results=[self.frank], num_queries=NUM_BASE_SESSION_QUERIES + 7
        )

        # our oldest note should be number 5
        self.assertEqual(
            "Frank is a good guy (5)",
            response.json()["results"][0]["notes"][0]["text"],
        )

        # our newest note should be number 6
        self.assertEqual(
            "Frank is an okay guy (9)",
            response.json()["results"][0]["notes"][-1]["text"],
        )

    @mock_mailroom
    def test_contacts_as_agent(self, mr_mocks):
        endpoint_url = reverse("api.v2.contacts") + ".json"

        self.create_field("gender", "Gender", ContactField.TYPE_TEXT, agent_access=ContactField.ACCESS_NONE)
        self.create_field("age", "Age", ContactField.TYPE_NUMBER, agent_access=ContactField.ACCESS_VIEW)
        self.create_field("height", "Height", ContactField.TYPE_NUMBER, agent_access=ContactField.ACCESS_EDIT)

        contact = self.create_contact(
            "Bob", urns=["telegram:12345"], fields={"gender": "M", "age": "40", "height": "180"}
        )

        # fetching a contact returns only the fields that agents can access
        self.assertGet(
            endpoint_url + f"?uuid={contact.uuid}",
            [self.agent],
            results=[
                {
                    "uuid": str(contact.uuid),
                    "name": "Bob",
                    "status": "active",
                    "language": None,
                    "urns": ["telegram:12345"],
                    "groups": [],
                    "notes": [],
                    "fields": {"age": "40", "height": "180"},
                    "flow": None,
                    "created_on": format_datetime(contact.created_on),
                    "modified_on": format_datetime(contact.modified_on),
                    "last_seen_on": None,
                    "blocked": False,
                    "stopped": False,
                }
            ],
        )

        # can't edit the field that we don't have any access to
        self.assertPost(
            endpoint_url + f"?uuid={contact.uuid}",
            self.agent,
            {"fields": {"gender": "M"}},
            errors={"fields": "Invalid contact field key: gender"},
        )

        # nor the field that we have view access to
        self.assertPost(
            endpoint_url + f"?uuid={contact.uuid}",
            self.agent,
            {"fields": {"age": "30"}},
            errors={"fields": "Editing of 'age' values disallowed for current user."},
        )

        # but can edit the field we have edit access for
        self.assertPost(
            endpoint_url + f"?uuid={contact.uuid}",
            self.agent,
            {"fields": {"height": "160"}},
        )

    def test_contacts_prevent_null_chars(self):
        endpoint_url = reverse("api.v2.contacts") + ".json"

        self.create_field("string_field", "String")
        self.create_field("number_field", "Number", value_type=ContactField.TYPE_NUMBER)

        # test create with a null chars \u0000
        self.login(self.admin)
        self.assertPost(
            endpoint_url,
            self.editor,
            {
                "name": "Jean",
                "urns": ["tel:+250783333334"],
                "fields": {"string_field": "crayons on the wall \u0000, pudding on the wall \x00, yeah \0"},
            },
            errors={("fields", "string_field"): "Null characters are not allowed."},
        )

    @mock_mailroom
    def test_contacts_update_datetime_field(self, mr_mocks):
        endpoint_url = reverse("api.v2.contacts") + ".json"

        self.create_field("activated_at", "Tag activation", ContactField.TYPE_DATETIME)

        # update contact with valid date format for the org - DD-MM-YYYY
        response = self.assertPost(
            endpoint_url + f"?uuid={self.joe.uuid}", self.editor, {"fields": {"activated_at": "31-12-2017"}}
        )
        self.assertIsNotNone(response.json()["fields"]["activated_at"])

        # update contact with valid ISO8601 timestamp value with timezone
        response = self.assertPost(
            endpoint_url + f"?uuid={self.joe.uuid}", self.editor, {"fields": {"activated_at": "2017-11-11T11:12:13Z"}}
        )
        self.assertEqual(response.json()["fields"]["activated_at"], "2017-11-11T13:12:13+02:00")

        # update contact with valid ISO8601 timestamp value, 'T' replaced with space
        response = self.assertPost(
            endpoint_url + f"?uuid={self.joe.uuid}", self.editor, {"fields": {"activated_at": "2017-11-11 11:12:13Z"}}
        )
        self.assertEqual(response.json()["fields"]["activated_at"], "2017-11-11T13:12:13+02:00")

        # update contact with invalid ISO8601 timestamp value without timezone
        response = self.assertPost(
            endpoint_url + f"?uuid={self.joe.uuid}", self.editor, {"fields": {"activated_at": "2017-11-11T11:12:13"}}
        )
        self.assertIsNone(response.json()["fields"]["activated_at"])

        # update contact with invalid date format for the org - MM-DD-YYYY
        response = self.assertPost(
            endpoint_url + f"?uuid={self.joe.uuid}", self.editor, {"fields": {"activated_at": "12-31-2017"}}
        )
        self.assertIsNone(response.json()["fields"]["activated_at"])

        # update contact with invalid timestamp value
        response = self.assertPost(
            endpoint_url + f"?uuid={self.joe.uuid}", self.editor, {"fields": {"activated_at": "el123a41"}}
        )
        self.assertIsNone(response.json()["fields"]["activated_at"])

    @mock_mailroom
    def test_contacts_anonymous_org(self, mr_mocks):
        endpoint_url = reverse("api.v2.contacts") + ".json"

        group = ContactGroup.get_or_create(self.org, self.admin, "Customers")

        self.assertPost(
            endpoint_url,
            self.editor,
            {
                "name": "Jean",
                "language": "fra",
                "urns": ["tel:+250783333333", "twitter:JEAN"],
                "groups": [group.uuid],
                "fields": {},
            },
            status=201,
        )

        jean = Contact.objects.filter(name="Jean", language="fra").get()

        with self.anonymous(self.org):
            # can't update via URN
            self.assertPost(
                endpoint_url + "?urn=tel:+250785555555",
                self.editor,
                {},
                errors={None: "URN lookups not allowed for anonymous organizations"},
                status=400,
            )

            # can't update contact URNs
            self.assertPost(
                endpoint_url + f"?uuid={jean.uuid}",
                self.editor,
                {"urns": ["tel:+250786666666"]},
                errors={"urns": "Updating URNs not allowed for anonymous organizations"},
                status=400,
            )

            # output shouldn't include URNs
            response = self.assertGet(endpoint_url + f"?uuid={jean.uuid}", [self.admin], results=[jean])
            self.assertEqual(response.json()["results"][0]["urns"], ["tel:********", "twitter:********"])

            # but can create with URNs
            response = self.assertPost(
                endpoint_url,
                self.admin,
                {"name": "Xavier", "urns": ["tel:+250-78-7777777", "twitter:XAVIER"]},
                status=201,
            )

            # TODO should UUID be masked in response??
            xavier = Contact.objects.get(name="Xavier")
            self.assertEqual(
                set(xavier.urns.values_list("identity", flat=True)), {"tel:+250787777777", "twitter:xavier"}
            )

            # can't filter by URN
            self.assertGet(
                endpoint_url + f"?urn={quote_plus('tel:+250-78-8000004')}",
                [self.admin],
                errors={None: "URN lookups not allowed for anonymous organizations"},
            )

    @mock_mailroom
    def test_contact_actions(self, mr_mocks):
        endpoint_url = reverse("api.v2.contact_actions") + ".json"

        self.assertGetNotAllowed(endpoint_url)
        self.assertPostNotPermitted(endpoint_url, [None, self.user])
        self.assertDeleteNotAllowed(endpoint_url)

        for contact in Contact.objects.all():
            contact.release(self.admin)
            contact.delete()

        # create some contacts to act on
        contact1 = self.create_contact("Ann", phone="+250788000001")
        contact2 = self.create_contact("Bob", phone="+250788000002")
        contact3 = self.create_contact("Cat", phone="+250788000003")
        contact4 = self.create_contact("Don", phone="+250788000004")  # a blocked contact
        contact5 = self.create_contact("Eve", phone="+250788000005")  # a deleted contact
        contact4.block(self.user)
        contact5.release(self.user)

        group = self.create_group("Testers")
        self.create_field("isdeveloper", "Is developer")
        self.create_group("Developers", query="isdeveloper = YES")
        other_org_group = self.create_group("Testers", org=self.org2)

        # create some waiting runs for some of the contacts
        flow = self.create_flow("Favorites")
        MockSessionWriter(contact1, flow).wait().save()
        MockSessionWriter(contact2, flow).wait().save()
        MockSessionWriter(contact3, flow).wait().save()

        self.create_incoming_msg(contact1, "Hello")
        self.create_incoming_msg(contact2, "Hello")
        self.create_incoming_msg(contact3, "Hello")
        self.create_incoming_msg(contact4, "Hello")

        # try adding more contacts to group than this endpoint is allowed to operate on at one time
        self.assertPost(
            endpoint_url,
            self.agent,
            {"contacts": [str(x) for x in range(101)], "action": "add", "group": "Testers"},
            errors={"contacts": "Ensure this field has no more than 100 elements."},
        )

        # try adding all contacts to a group by its name
        self.assertPost(
            endpoint_url,
            self.editor,
            {
                "contacts": [contact1.uuid, "tel:+250788000002", contact3.uuid, contact4.uuid, contact5.uuid],
                "action": "add",
                "group": "Testers",
            },
            errors={"contacts": "No such object: %s" % contact5.uuid},
        )

        # try adding a blocked contact to a group
        self.assertPost(
            endpoint_url,
            self.admin,
            {
                "contacts": [contact1.uuid, contact2.uuid, contact3.uuid, contact4.uuid],
                "action": "add",
                "group": "Testers",
            },
            errors={"non_field_errors": "Non-active contacts cannot be added to groups: %s" % contact4.uuid},
        )

        # add valid contacts to the group by name
        self.assertPost(
            endpoint_url,
            self.admin,
            {"contacts": [contact1.uuid, "tel:+250788000002"], "action": "add", "group": "Testers"},
            status=204,
        )
        self.assertEqual(set(group.contacts.all()), {contact1, contact2})

        # try to add to a non-existent group
        self.assertPost(
            endpoint_url,
            self.admin,
            {"contacts": [contact1.uuid], "action": "add", "group": "Spammers"},
            errors={"group": "No such object: Spammers"},
        )

        # try to add to a dynamic group
        self.assertPost(
            endpoint_url,
            self.admin,
            {"contacts": [contact1.uuid], "action": "add", "group": "Developers"},
            errors={"group": "Contact group must not be query based: Developers"},
        )

        # add contact 3 to a group by its UUID
        self.assertPost(
            endpoint_url, self.admin, {"contacts": [contact3.uuid], "action": "add", "group": group.uuid}, status=204
        )
        self.assertEqual(set(group.contacts.all()), {contact1, contact2, contact3})

        # try adding with invalid group UUID
        self.assertPost(
            endpoint_url,
            self.admin,
            {"contacts": [contact3.uuid], "action": "add", "group": "15611256-95b5-46d5-b857-abafe0d32fe9"},
            errors={"group": "No such object: 15611256-95b5-46d5-b857-abafe0d32fe9"},
        )

        # try to add to a group in another org
        self.assertPost(
            endpoint_url,
            self.admin,
            {"contacts": [contact3.uuid], "action": "add", "group": other_org_group.uuid},
            errors={"group": f"No such object: {other_org_group.uuid}"},
        )

        # remove contact 2 from group by its name (which is case-insensitive)
        self.assertPost(
            endpoint_url, self.admin, {"contacts": [contact2.uuid], "action": "remove", "group": "testers"}, status=204
        )
        self.assertEqual(set(group.contacts.all()), {contact1, contact3})

        # and remove contact 3 from group by its UUID
        self.assertPost(
            endpoint_url, self.admin, {"contacts": [contact3.uuid], "action": "remove", "group": group.uuid}, status=204
        )
        self.assertEqual(set(group.contacts.all()), {contact1})

        # try to add to group without specifying a group
        self.assertPost(
            endpoint_url,
            self.admin,
            {"contacts": [contact1.uuid], "action": "add"},
            errors={"non_field_errors": 'For action "add" you should also specify a group'},
        )
        self.assertPost(
            endpoint_url,
            self.admin,
            {"contacts": [contact1.uuid], "action": "add", "group": ""},
            errors={"group": "This field may not be null."},
        )

        # block all contacts
        self.assertPost(
            endpoint_url,
            self.admin,
            {"contacts": [contact1.uuid, contact2.uuid, contact3.uuid, contact4.uuid], "action": "block"},
            status=204,
        )
        self.assertEqual(
            set(Contact.objects.filter(status=Contact.STATUS_BLOCKED)), {contact1, contact2, contact3, contact4}
        )

        # unblock contact 1
        self.assertPost(
            endpoint_url,
            self.admin,
            {"contacts": [contact1.uuid], "action": "unblock"},
            status=204,
        )
        self.assertEqual(set(self.org.contacts.filter(status=Contact.STATUS_ACTIVE)), {contact1, contact5})
        self.assertEqual(set(self.org.contacts.filter(status=Contact.STATUS_BLOCKED)), {contact2, contact3, contact4})

        # interrupt any active runs of contacts 1 and 2
        with patch("temba.mailroom.queue_interrupt") as mock_queue_interrupt:
            self.assertPost(
                endpoint_url,
                self.admin,
                {"contacts": [contact1.uuid, contact2.uuid], "action": "interrupt"},
                status=204,
            )

            mock_queue_interrupt.assert_called_once_with(self.org, contacts=[contact1, contact2])

        # archive all messages for contacts 1 and 2
        self.assertPost(
            endpoint_url,
            self.admin,
            {"contacts": [contact1.uuid, contact2.uuid], "action": "archive_messages"},
            status=204,
        )
        self.assertFalse(Msg.objects.filter(contact__in=[contact1, contact2], direction="I", visibility="V").exists())
        self.assertTrue(Msg.objects.filter(contact=contact3, direction="I", visibility="V").exists())

        # delete contacts 1 and 2
        self.assertPost(
            endpoint_url,
            self.admin,
            {"contacts": [contact1.uuid, contact2.uuid], "action": "delete"},
            status=204,
        )
        self.assertEqual(set(self.org.contacts.filter(is_active=False)), {contact1, contact2, contact5})
        self.assertEqual(set(self.org.contacts.filter(is_active=True)), {contact3, contact4})
        self.assertFalse(Msg.objects.filter(contact__in=[contact1, contact2]).exclude(visibility="D").exists())
        self.assertTrue(Msg.objects.filter(contact=contact3).exclude(visibility="D").exists())

        # try to provide a group for a non-group action
        self.assertPost(
            endpoint_url,
            self.admin,
            {"contacts": [contact3.uuid], "action": "block", "group": "Testers"},
            errors={"non_field_errors": 'For action "block" you should not specify a group'},
        )

        # trying to act on zero contacts is an error
        self.assertPost(
            endpoint_url,
            self.admin,
            {"contacts": [], "action": "block"},
            errors={"contacts": "Contacts can't be empty."},
        )

        # try to invoke an invalid action
        self.assertPost(
            endpoint_url,
            self.admin,
            {"contacts": [contact3.uuid], "action": "like"},
            errors={"action": '"like" is not a valid choice.'},
        )

    def test_definitions(self):
        endpoint_url = reverse("api.v2.definitions") + ".json"

        self.assertGetNotPermitted(endpoint_url, [None, self.agent])
        self.assertPostNotAllowed(endpoint_url)
        self.assertDeleteNotAllowed(endpoint_url)

        self.import_file("subflow")
        flow = Flow.objects.get(name="Parent Flow")

        # all flow dependencies and we should get the child flow
        self.assertGet(
            endpoint_url + f"?flow={flow.uuid}",
            [self.editor],
            raw=lambda j: {f["name"] for f in j["flows"]} == {"Child Flow", "Parent Flow"},
        )

        # export just the parent flow
        self.assertGet(
            endpoint_url + f"?flow={flow.uuid}&dependencies=none",
            [self.editor],
            raw=lambda j: {f["name"] for f in j["flows"]} == {"Parent Flow"},
        )

        # import the clinic app which has campaigns
        self.import_file("the_clinic")

        # our catchall flow, all alone
        flow = Flow.objects.get(name="Catch All")
        self.assertGet(
            endpoint_url + f"?flow={flow.uuid}&dependencies=none",
            [self.editor],
            raw=lambda j: len(j["flows"]) == 1 and len(j["campaigns"]) == 0 and len(j["triggers"]) == 0,
        )

        # with its trigger dependency
        self.assertGet(
            endpoint_url + f"?flow={flow.uuid}",
            [self.editor],
            raw=lambda j: len(j["flows"]) == 1 and len(j["campaigns"]) == 0 and len(j["triggers"]) == 1,
        )

        # our registration flow, all alone
        flow = Flow.objects.get(name="Register Patient")
        self.assertGet(
            endpoint_url + f"?flow={flow.uuid}&dependencies=none",
            [self.editor],
            raw=lambda j: len(j["flows"]) == 1 and len(j["campaigns"]) == 0 and len(j["triggers"]) == 0,
        )

        # touches a lot of stuff
        self.assertGet(
            endpoint_url + f"?flow={flow.uuid}",
            [self.editor],
            raw=lambda j: len(j["flows"]) == 6 and len(j["campaigns"]) == 1 and len(j["triggers"]) == 2,
        )

        # ignore campaign dependencies
        self.assertGet(
            endpoint_url + f"?flow={flow.uuid}&dependencies=flows",
            [self.editor],
            raw=lambda j: len(j["flows"]) == 2 and len(j["campaigns"]) == 0 and len(j["triggers"]) == 1,
        )

        # add our missed call flow
        missed_call = Flow.objects.get(name="Missed Call")
        self.assertGet(
            endpoint_url + f"?flow={flow.uuid}&flow={missed_call.uuid}&dependencies=all",
            [self.editor],
            raw=lambda j: len(j["flows"]) == 7 and len(j["campaigns"]) == 1 and len(j["triggers"]) == 3,
        )

        campaign = Campaign.objects.get(name="Appointment Schedule")
        self.assertGet(
            endpoint_url + f"?campaign={campaign.uuid}&dependencies=none",
            [self.editor],
            raw=lambda j: len(j["flows"]) == 0 and len(j["campaigns"]) == 1 and len(j["triggers"]) == 0,
        )

        self.assertGet(
            endpoint_url + f"?campaign={campaign.uuid}",
            [self.editor],
            raw=lambda j: len(j["flows"]) == 6 and len(j["campaigns"]) == 1 and len(j["triggers"]) == 2,
        )

        # test an invalid value for dependencies
        self.assertGet(
            endpoint_url + f"?flow={flow.uuid}&dependencies=xx",
            [self.editor],
            errors={None: "dependencies must be one of none, flows, all"},
        )

        # test that flows are migrated
        self.import_file("favorites_v13")

        flow = Flow.objects.get(name="Favorites")
        self.assertGet(
            endpoint_url + f"?flow={flow.uuid}",
            [self.editor],
            raw=lambda j: len(j["flows"]) == 1 and j["flows"][0]["spec_version"] == Flow.CURRENT_SPEC_VERSION,
        )

    @override_settings(ORG_LIMIT_DEFAULTS={"fields": 10})
    def test_fields(self):
        endpoint_url = reverse("api.v2.fields") + ".json"

        self.assertGetNotPermitted(endpoint_url, [None])
        self.assertPostNotPermitted(endpoint_url, [None, self.user])
        self.assertDeleteNotAllowed(endpoint_url)

        nick_name = self.create_field("nick_name", "Nick Name", agent_access=ContactField.ACCESS_EDIT)
        registered = self.create_field("registered", "Registered On", value_type=ContactField.TYPE_DATETIME)
        self.create_field("not_ours", "Something Else", org=self.org2)

        # add our date field to a campaign event
        campaign = Campaign.create(self.org, self.admin, "Reminders", self.create_group("Farmers"))
        CampaignEvent.create_flow_event(
            self.org, self.admin, campaign, registered, offset=1, unit="W", flow=self.create_flow("Flow")
        )

        deleted = self.create_field("deleted", "Deleted")
        deleted.release(self.admin)

        # no filtering
        self.assertGet(
            endpoint_url,
            [self.user, self.editor],
            results=[
                {
                    "key": "registered",
                    "name": "Registered On",
                    "type": "datetime",
                    "featured": False,
                    "priority": 0,
                    "usages": {"campaign_events": 1, "flows": 0, "groups": 0},
                    "agent_access": "view",
                    "label": "Registered On",
                    "value_type": "datetime",
                },
                {
                    "key": "nick_name",
                    "name": "Nick Name",
                    "type": "text",
                    "featured": False,
                    "priority": 0,
                    "usages": {"campaign_events": 0, "flows": 0, "groups": 0},
                    "agent_access": "edit",
                    "label": "Nick Name",
                    "value_type": "text",
                },
            ],
            num_queries=NUM_BASE_SESSION_QUERIES + 1,
        )

        # filter by key
        self.assertGet(endpoint_url + "?key=nick_name", [self.editor], results=[nick_name])

        # try to create empty field
        self.assertPost(endpoint_url, self.admin, {}, errors={"non_field_errors": "Field 'name' is required."})

        # try to create field without type
        self.assertPost(
            endpoint_url, self.admin, {"name": "goats"}, errors={"non_field_errors": "Field 'type' is required."}
        )

        # try again with some invalid values
        self.assertPost(
            endpoint_url,
            self.admin,
            {"name": "!@#$%", "type": "video"},
            errors={"name": "Can only contain letters, numbers and hypens.", "type": '"video" is not a valid choice.'},
        )

        # try again with some invalid values using deprecated field names
        self.assertPost(
            endpoint_url,
            self.admin,
            {"label": "!@#$%", "value_type": "video"},
            errors={
                "label": "Can only contain letters, numbers and hypens.",
                "value_type": '"video" is not a valid choice.',
            },
        )

        # try again with a label that would generate an invalid key
        self.assertPost(
            endpoint_url,
            self.admin,
            {"name": "HAS", "type": "text"},
            errors={"name": 'Generated key "has" is invalid or a reserved name.'},
        )

        # try again with a label that's already taken
        self.assertPost(
            endpoint_url,
            self.admin,
            {"label": "nick name", "value_type": "text"},
            errors={"label": "This field must be unique."},
        )

        # create a new field
        self.assertPost(endpoint_url, self.editor, {"name": "Age", "type": "number"}, status=201)

        age = ContactField.objects.get(
            org=self.org, name="Age", value_type="N", is_proxy=False, is_system=False, is_active=True
        )

        # update a field by its key
        self.assertPost(endpoint_url + "?key=age", self.admin, {"name": "Real Age", "type": "datetime"})
        age.refresh_from_db()
        self.assertEqual(age.name, "Real Age")
        self.assertEqual(age.value_type, "D")

        # try to update with key of deleted field
        self.assertPost(endpoint_url + "?key=deleted", self.admin, {"name": "Something", "type": "text"}, status=404)

        # try to update with non-existent key
        self.assertPost(endpoint_url + "?key=not_ours", self.admin, {"name": "Something", "type": "text"}, status=404)

        # try to change type of date field used by campaign event
        self.assertPost(
            endpoint_url + "?key=registered",
            self.admin,
            {"name": "Registered", "type": "text"},
            errors={"type": "Can't change type of date field being used by campaign events."},
        )

        CampaignEvent.objects.all().delete()
        ContactField.objects.filter(is_system=False).delete()

        for i in range(10):
            self.create_field("field%d" % i, "Field%d" % i)

        self.assertPost(
            endpoint_url,
            self.admin,
            {"label": "Age", "value_type": "numeric"},
            errors={None: "Cannot create object because workspace has reached limit of 10."},
            status=409,
        )

    def test_flows(self):
        endpoint_url = reverse("api.v2.flows") + ".json"

        self.assertGetNotPermitted(endpoint_url, [None, self.agent])
        self.assertPostNotAllowed(endpoint_url)
        self.assertDeleteNotAllowed(endpoint_url)

        survey = self.get_flow("media_survey")
        color = self.get_flow("color")
        archived = self.get_flow("favorites")
        archived.archive(self.admin)

        # add a campaign message flow that should be filtered out
        Flow.create_single_message(self.org, self.admin, dict(eng="Hello world"), "eng")

        # add a flow label
        reporting = FlowLabel.create(self.org, self.admin, "Reporting")
        color.labels.add(reporting)

        # make it look like joe completed the color flow
        FlowRun.objects.create(
            org=self.org, flow=color, contact=self.joe, status=FlowRun.STATUS_COMPLETED, exited_on=timezone.now()
        )

        # flow belong to other org
        other_org = self.create_flow("Other", org=self.org2)

        # no filtering
        self.assertGet(
            endpoint_url,
            [self.user, self.editor],
            results=[
                {
                    "uuid": archived.uuid,
                    "name": "Favorites",
                    "type": "message",
                    "archived": True,
                    "labels": [],
                    "expires": 720,
                    "runs": {"active": 0, "waiting": 0, "completed": 0, "interrupted": 0, "expired": 0, "failed": 0},
                    "results": [
                        {
                            "key": "color",
                            "name": "Color",
                            "categories": ["Red", "Green", "Blue", "Cyan", "Other"],
                            "node_uuids": [matchers.UUID4String()],
                        },
                        {
                            "key": "beer",
                            "name": "Beer",
                            "categories": ["Mutzig", "Primus", "Turbo King", "Skol", "Other"],
                            "node_uuids": [matchers.UUID4String()],
                        },
                        {
                            "key": "name",
                            "name": "Name",
                            "categories": ["All Responses"],
                            "node_uuids": [matchers.UUID4String()],
                        },
                    ],
                    "parent_refs": [],
                    "created_on": format_datetime(archived.created_on),
                    "modified_on": format_datetime(archived.modified_on),
                },
                {
                    "uuid": color.uuid,
                    "name": "Color Flow",
                    "type": "message",
                    "archived": False,
                    "labels": [{"uuid": str(reporting.uuid), "name": "Reporting"}],
                    "expires": 10080,
                    "runs": {"active": 0, "waiting": 0, "completed": 1, "interrupted": 0, "expired": 0, "failed": 0},
                    "results": [
                        {
                            "key": "color",
                            "name": "color",
                            "categories": ["Orange", "Blue", "Other", "Nothing"],
                            "node_uuids": [matchers.UUID4String()],
                        }
                    ],
                    "parent_refs": [],
                    "created_on": format_datetime(color.created_on),
                    "modified_on": format_datetime(color.modified_on),
                },
                {
                    "uuid": survey.uuid,
                    "name": "Media Survey",
                    "type": "survey",
                    "archived": False,
                    "labels": [],
                    "expires": 10080,
                    "runs": {"active": 0, "waiting": 0, "completed": 0, "interrupted": 0, "expired": 0, "failed": 0},
                    "results": [
                        {
                            "key": "name",
                            "name": "Name",
                            "categories": ["All Responses"],
                            "node_uuids": [matchers.UUID4String()],
                        },
                        {
                            "key": "photo",
                            "name": "Photo",
                            "categories": ["All Responses"],
                            "node_uuids": [matchers.UUID4String()],
                        },
                        {
                            "key": "location",
                            "name": "Location",
                            "categories": ["All Responses"],
                            "node_uuids": [matchers.UUID4String()],
                        },
                        {
                            "key": "video",
                            "name": "Video",
                            "categories": ["All Responses"],
                            "node_uuids": [matchers.UUID4String()],
                        },
                    ],
                    "parent_refs": [],
                    "created_on": format_datetime(survey.created_on),
                    "modified_on": format_datetime(survey.modified_on),
                },
            ],
            num_queries=NUM_BASE_SESSION_QUERIES + 5,
        )

        self.assertGet(endpoint_url, [self.admin2], results=[other_org])

        # filter by key
        self.assertGet(endpoint_url + f"?uuid={color.uuid}", [self.editor], results=[color])

        # filter by type
        self.assertGet(endpoint_url + "?type=message", [self.editor], results=[archived, color])
        self.assertGet(endpoint_url + "?type=survey", [self.editor], results=[survey])

        # filter by archived
        self.assertGet(endpoint_url + "?archived=1", [self.editor], results=[archived])
        self.assertGet(endpoint_url + "?archived=0", [self.editor], results=[color, survey])
        self.assertGet(endpoint_url + "?archived=false", [self.editor], results=[color, survey])

        # filter by before / after
        self.assertGet(
            endpoint_url + f"?before={format_datetime(color.modified_on)}", [self.editor], results=[color, survey]
        )
        self.assertGet(
            endpoint_url + f"?after={format_datetime(color.modified_on)}", [self.editor], results=[archived, color]
        )

        # inactive flows are never returned
        archived.is_active = False
        archived.save()

        self.assertGet(endpoint_url, [self.editor], results=[color, survey])

    @patch("temba.flows.models.FlowStart.async_start")
    def test_flow_starts(self, mock_async_start):
        endpoint_url = reverse("api.v2.flow_starts") + ".json"

        self.assertGetNotPermitted(endpoint_url, [None, self.agent])
        self.assertPostNotPermitted(endpoint_url, [None, self.agent, self.user])
        self.assertDeleteNotAllowed(endpoint_url)

        flow = self.get_flow("favorites_v13")

        # try to create an empty flow start
        self.assertPost(endpoint_url, self.editor, {}, errors={"flow": "This field is required."})

        # start a flow with the minimum required parameters
        response = self.assertPost(
            endpoint_url, self.editor, {"flow": flow.uuid, "contacts": [self.joe.uuid]}, status=201
        )

        start1 = flow.starts.get(id=response.json()["id"])
        self.assertEqual(start1.flow, flow)
        self.assertEqual(set(start1.contacts.all()), {self.joe})
        self.assertEqual(set(start1.groups.all()), set())
        self.assertEqual(start1.exclusions, {"in_a_flow": False, "started_previously": False})
        self.assertEqual(start1.params, {})

        # check we tried to start the new flow start
        mock_async_start.assert_called_once()
        mock_async_start.reset_mock()

        # start a flow with all parameters
        hans_group = self.create_group("hans", contacts=[self.hans])
        response = self.assertPost(
            endpoint_url,
            self.admin,
            {
                "urns": ["tel:+12067791212"],
                "contacts": [self.joe.uuid],
                "groups": [hans_group.uuid],
                "flow": flow.uuid,
                "restart_participants": False,
                "extra": {"first_name": "Ryan", "last_name": "Lewis"},
            },
            status=201,
        )

        # assert our new start
        start2 = flow.starts.get(id=response.json()["id"])
        self.assertEqual(start2.flow, flow)
        self.assertEqual(start2.start_type, FlowStart.TYPE_API)
        self.assertEqual(["tel:+12067791212"], start2.urns)
        self.assertEqual({self.joe}, set(start2.contacts.all()))
        self.assertEqual({hans_group}, set(start2.groups.all()))
        self.assertEqual(start2.exclusions, {"in_a_flow": False, "started_previously": True})
        self.assertEqual(start2.params, {"first_name": "Ryan", "last_name": "Lewis"})

        # check we tried to start the new flow start
        mock_async_start.assert_called_once()
        mock_async_start.reset_mock()

        response = self.assertPost(
            endpoint_url,
            self.admin,
            {
                "urns": ["tel:+12067791212"],
                "contacts": [self.joe.uuid],
                "groups": [hans_group.uuid],
                "flow": flow.uuid,
                "restart_participants": False,
                "extra": {"first_name": "Ryan", "last_name": "Lewis"},
                "params": {"first_name": "Bob", "last_name": "Marley"},
            },
            status=201,
        )

        # assert our new start
        start3 = flow.starts.get(id=response.json()["id"])
        self.assertEqual(start3.flow, flow)
        self.assertEqual(["tel:+12067791212"], start3.urns)
        self.assertEqual({self.joe}, set(start3.contacts.all()))
        self.assertEqual({hans_group}, set(start3.groups.all()))
        self.assertEqual(start3.exclusions, {"in_a_flow": False, "started_previously": True})
        self.assertEqual(start3.params, {"first_name": "Bob", "last_name": "Marley"})

        # check we tried to start the new flow start
        mock_async_start.assert_called_once()
        mock_async_start.reset_mock()

        # calls from Zapier have user-agent set to Zapier
        response = self.assertPost(
            endpoint_url,
            self.admin,
            {"contacts": [self.joe.uuid], "flow": flow.uuid},
            HTTP_USER_AGENT="Zapier",
            status=201,
        )

        # assert our new start has start_type of Zapier
        start4 = flow.starts.get(id=response.json()["id"])
        self.assertEqual(FlowStart.TYPE_API_ZAPIER, start4.start_type)

        # try to start a flow with no contact/group/URN
        self.assertPost(
            endpoint_url,
            self.admin,
            {"flow": flow.uuid, "restart_participants": True},
            errors={"non_field_errors": "Must specify at least one group, contact or URN"},
        )

        # should raise validation error for invalid JSON in extra
        self.assertPost(
            endpoint_url,
            self.admin,
            {
                "urns": ["tel:+12067791212"],
                "contacts": [self.joe.uuid],
                "groups": [hans_group.uuid],
                "flow": flow.uuid,
                "restart_participants": False,
                "extra": "YES",
            },
            errors={"extra": "Must be a valid JSON object"},
        )

        # a list is valid JSON, but extra has to be a dict
        self.assertPost(
            endpoint_url,
            self.admin,
            {
                "urns": ["tel:+12067791212"],
                "contacts": [self.joe.uuid],
                "groups": [hans_group.uuid],
                "flow": flow.uuid,
                "restart_participants": False,
                "extra": [1],
            },
            errors={"extra": "Must be a valid JSON object"},
        )

        self.assertPost(
            endpoint_url,
            self.admin,
            {
                "urns": ["tel:+12067791212"],
                "contacts": [self.joe.uuid],
                "groups": [hans_group.uuid],
                "flow": flow.uuid,
                "restart_participants": False,
                "params": "YES",
            },
            errors={"params": "Must be a valid JSON object"},
        )

        # a list is valid JSON, but extra has to be a dict
        self.assertPost(
            endpoint_url,
            self.admin,
            {
                "urns": ["tel:+12067791212"],
                "contacts": [self.joe.uuid],
                "groups": [hans_group.uuid],
                "flow": flow.uuid,
                "restart_participants": False,
                "params": [1],
            },
            errors={"params": "Must be a valid JSON object"},
        )

        # invalid URN
        self.assertPost(
            endpoint_url,
            self.admin,
            {"flow": flow.uuid, "urns": ["foo:bar"], "contacts": [self.joe.uuid]},
            errors={("urns", "0"): "Invalid URN: foo:bar. Ensure phone numbers contain country codes."},
        )

        # invalid contact uuid
        self.assertPost(
            endpoint_url,
            self.admin,
            {"flow": flow.uuid, "urns": ["tel:+12067791212"], "contacts": ["abcde"]},
            errors={"contacts": "No such object: abcde"},
        )

        # invalid group uuid
        self.assertPost(
            endpoint_url,
            self.admin,
            {"flow": flow.uuid, "urns": ["tel:+12067791212"], "groups": ["abcde"]},
            errors={"groups": "No such object: abcde"},
        )

        # invalid flow uuid
        self.assertPost(
            endpoint_url,
            self.admin,
            {
                "flow": "abcde",
                "urns": ["tel:+12067791212"],
            },
            errors={"flow": "No such object: abcde"},
        )

        # too many groups
        group_uuids = []
        for g in range(101):
            group_uuids.append(self.create_group("Group %d" % g).uuid)

        self.assertPost(
            endpoint_url,
            self.admin,
            {"flow": flow.uuid, "groups": group_uuids},
            errors={"groups": "Ensure this field has no more than 100 elements."},
        )

        # check fetching with no filtering
        response = self.assertGet(
            endpoint_url,
            [self.user, self.editor],
            results=[start4, start3, start2, start1],
            num_queries=NUM_BASE_SESSION_QUERIES + 4,
        )
        self.assertEqual(
            response.json()["results"][1],
            {
                "id": start3.id,
                "uuid": str(start3.uuid),
                "flow": {"uuid": flow.uuid, "name": "Favorites"},
                "contacts": [{"uuid": self.joe.uuid, "name": "Joe Blow"}],
                "groups": [{"uuid": hans_group.uuid, "name": "hans"}],
                "restart_participants": False,
                "exclude_active": False,
                "status": "pending",
                "extra": {"first_name": "Bob", "last_name": "Marley"},
                "params": {"first_name": "Bob", "last_name": "Marley"},
                "created_on": format_datetime(start3.created_on),
                "modified_on": format_datetime(start3.modified_on),
            },
        )

        # check filtering by UUID
        self.assertGet(endpoint_url + f"?uuid={start2.uuid}", [self.admin], results=[start2])

        # check filtering by in invalid UUID
        self.assertGet(endpoint_url + "?uuid=xyz", [self.editor], errors={None: "Value for uuid must be a valid UUID"})

        # check filtering by id (deprecated)
        response = self.assertGet(endpoint_url + f"?id={start2.id}", [self.editor], results=[start2])

        response = self.assertPost(
            endpoint_url,
            self.editor,
            {
                "urns": ["tel:+12067791212"],
                "contacts": [self.joe.uuid],
                "groups": [hans_group.uuid],
                "flow": flow.uuid,
                "restart_participants": True,
                "exclude_active": False,
                "extra": {"first_name": "Ryan", "last_name": "Lewis"},
                "params": {"first_name": "Bob", "last_name": "Marley"},
            },
            status=201,
        )

        start4 = flow.starts.get(id=response.json()["id"])
        self.assertEqual({"started_previously": False, "in_a_flow": False}, start4.exclusions)

        response = self.assertPost(
            endpoint_url,
            self.editor,
            {
                "urns": ["tel:+12067791212"],
                "contacts": [self.joe.uuid],
                "groups": [hans_group.uuid],
                "flow": flow.uuid,
                "restart_participants": True,
                "exclude_active": True,
                "extra": {"first_name": "Ryan", "last_name": "Lewis"},
                "params": {"first_name": "Bob", "last_name": "Marley"},
            },
            status=201,
        )

        start5 = flow.starts.get(id=response.json()["id"])
        self.assertEqual({"started_previously": False, "in_a_flow": True}, start5.exclusions)

    @override_settings(ORG_LIMIT_DEFAULTS={"globals": 3})
    def test_globals(self):
        endpoint_url = reverse("api.v2.globals") + ".json"

        self.assertGetNotPermitted(endpoint_url, [None, self.agent])
        self.assertPostNotPermitted(endpoint_url, [None, self.user, self.agent])
        self.assertDeleteNotAllowed(endpoint_url)

        # create some globals
        deleted = Global.get_or_create(self.org, self.admin, "org_name", "Org Name", "Acme Ltd")
        deleted.release(self.admin)

        global1 = Global.get_or_create(self.org, self.admin, "org_name", "Org Name", "Acme Ltd")
        global2 = Global.get_or_create(self.org, self.admin, "access_token", "Access Token", "23464373")

        # on another org
        global3 = Global.get_or_create(self.org2, self.admin, "thingy", "Thingy", "xyz")

        # check no filtering
        response = self.assertGet(
            endpoint_url,
            [self.user, self.editor],
            results=[
                {
                    "key": "access_token",
                    "name": "Access Token",
                    "value": "23464373",
                    "modified_on": format_datetime(global2.modified_on),
                },
                {
                    "key": "org_name",
                    "name": "Org Name",
                    "value": "Acme Ltd",
                    "modified_on": format_datetime(global1.modified_on),
                },
            ],
            num_queries=NUM_BASE_SESSION_QUERIES + 1,
        )

        # check no filtering with token auth
        response = self.assertGet(
            endpoint_url,
            [self.editor, self.admin],
            results=[global2, global1],
            by_token=True,
            num_queries=NUM_BASE_TOKEN_QUERIES + 1,
        )

        self.assertGet(endpoint_url, [self.admin2], results=[global3])

        # filter by key
        self.assertGet(endpoint_url + "?key=org_name", [self.editor], results=[global1])

        # filter by before / after
        self.assertGet(
            endpoint_url + f"?before={format_datetime(global1.modified_on)}", [self.editor], results=[global1]
        )
        self.assertGet(
            endpoint_url + f"?after={format_datetime(global1.modified_on)}", [self.editor], results=[global2, global1]
        )

        # lets change a global
        self.assertPost(endpoint_url + "?key=org_name", self.admin, {"value": "Acme LLC"})
        global1.refresh_from_db()
        self.assertEqual(global1.value, "Acme LLC")

        # try to create a global with no name
        response = self.assertPost(
            endpoint_url,
            self.admin,
            {"value": "yes"},
            errors={"non_field_errors": "Name is required when creating new global."},
        )

        # try to create a global with invalid name
        response = self.assertPost(
            endpoint_url, self.admin, {"name": "!!!#$%^"}, errors={"name": "Name contains illegal characters."}
        )

        # try to create a global with name that creates an invalid key
        response = self.assertPost(
            endpoint_url,
            self.admin,
            {"name": "2cool key", "value": "23464373"},
            errors={"name": "Name creates Key that is invalid"},
        )

        # try to create a global with name that's too long
        response = self.assertPost(
            endpoint_url,
            self.admin,
            {"name": "x" * 37},
            errors={"name": "Ensure this field has no more than 36 characters."},
        )

        # lets create a new global
        response = self.assertPost(endpoint_url, self.admin, {"name": "New Global", "value": "23464373"}, status=201)
        global3 = Global.objects.get(key="new_global")
        self.assertEqual(
            response.json(),
            {
                "key": "new_global",
                "name": "New Global",
                "value": "23464373",
                "modified_on": format_datetime(global3.modified_on),
            },
        )

        # try again now that we've hit the mocked limit of globals per org
        self.assertPost(
            endpoint_url,
            self.admin,
            {"name": "Website URL", "value": "http://example.com"},
            errors={None: "Cannot create object because workspace has reached limit of 3."},
            status=409,
        )

    @override_settings(ORG_LIMIT_DEFAULTS={"groups": 10})
    @mock_mailroom
    def test_groups(self, mr_mocks):
        endpoint_url = reverse("api.v2.groups") + ".json"

        self.assertGetNotPermitted(endpoint_url, [None])
        self.assertPostNotPermitted(endpoint_url, [None, self.user, self.agent])
        self.assertDeleteNotPermitted(endpoint_url, [None, self.user, self.agent])

        self.create_field("isdeveloper", "Is developer")
        open_tickets = self.org.groups.get(name="Open Tickets")
        customers = self.create_group("Customers", [self.frank])
        developers = self.create_group("Developers", query='isdeveloper = "YES"')
        ContactGroup.objects.filter(id=developers.id).update(status=ContactGroup.STATUS_READY)

        dynamic = self.create_group("Big Group", query='isdeveloper = "NO"')
        ContactGroup.objects.filter(id=dynamic.id).update(status=ContactGroup.STATUS_EVALUATING)

        # an initializing group
        ContactGroup.create_manual(self.org, self.admin, "Initializing", status=ContactGroup.STATUS_INITIALIZING)

        # group belong to other org
        spammers = ContactGroup.get_or_create(self.org2, self.admin2, "Spammers")

        # no filtering
        self.assertGet(
            endpoint_url,
            [self.user, self.editor],
            results=[
                {
                    "uuid": dynamic.uuid,
                    "name": "Big Group",
                    "query": 'isdeveloper = "NO"',
                    "status": "evaluating",
                    "system": False,
                    "count": 0,
                },
                {
                    "uuid": developers.uuid,
                    "name": "Developers",
                    "query": 'isdeveloper = "YES"',
                    "status": "ready",
                    "system": False,
                    "count": 0,
                },
                {
                    "uuid": customers.uuid,
                    "name": "Customers",
                    "query": None,
                    "status": "ready",
                    "system": False,
                    "count": 1,
                },
                {
                    "uuid": open_tickets.uuid,
                    "name": "Open Tickets",
                    "query": "tickets > 0",
                    "status": "ready",
                    "system": True,
                    "count": 0,
                },
            ],
            num_queries=NUM_BASE_SESSION_QUERIES + 2,
        )

        # filter by UUID
        self.assertGet(endpoint_url + f"?uuid={customers.uuid}", [self.editor], results=[customers])

        # filter by name
        self.assertGet(endpoint_url + "?name=developers", [self.editor], results=[developers])

        # try to filter by both
        self.assertGet(
            endpoint_url + f"?uuid={customers.uuid}&name=developers",
            [self.editor],
            errors={None: "You may only specify one of the uuid, name parameters"},
        )

        # try to create empty group
        self.assertPost(endpoint_url, self.admin, {}, errors={"name": "This field is required."})

        # create new group
        response = self.assertPost(endpoint_url, self.admin, {"name": "Reporters"}, status=201)

        reporters = ContactGroup.objects.get(name="Reporters")
        self.assertEqual(
            response.json(),
            {
                "uuid": reporters.uuid,
                "name": "Reporters",
                "query": None,
                "status": "ready",
                "system": False,
                "count": 0,
            },
        )

        # try to create another group with same name
        self.assertPost(endpoint_url, self.admin, {"name": "reporters"}, errors={"name": "This field must be unique."})

        # try to create another group with same name as a system group..
        self.assertPost(endpoint_url, self.admin, {"name": "blocked"}, errors={"name": "This field must be unique."})

        # it's fine if a group in another org has that name
        self.assertPost(endpoint_url, self.admin, {"name": "Spammers"}, status=201)

        # try to create a group with invalid name
        self.assertPost(
            endpoint_url, self.admin, {"name": '"People"'}, errors={"name": 'Cannot contain the character: "'}
        )

        # try to create a group with name that's too long
        self.assertPost(
            endpoint_url,
            self.admin,
            {"name": "x" * 65},
            errors={"name": "Ensure this field has no more than 64 characters."},
        )

        # update group by UUID
        self.assertPost(endpoint_url + f"?uuid={reporters.uuid}", self.admin, {"name": "U-Reporters"})

        reporters.refresh_from_db()
        self.assertEqual(reporters.name, "U-Reporters")

        # can't update a system group
        self.assertPost(
            endpoint_url + f"?uuid={open_tickets.uuid}",
            self.admin,
            {"name": "Won't work"},
            errors={None: "Cannot modify system object."},
            status=403,
        )
        self.assertTrue(self.org.groups.filter(name="Open Tickets").exists())

        # can't update a group from other org
        self.assertPost(endpoint_url + f"?uuid={spammers.uuid}", self.admin, {"name": "Won't work"}, status=404)

        # try an empty delete request
        self.assertDelete(
            endpoint_url, self.admin, errors={None: "URL must contain one of the following parameters: uuid"}
        )

        # delete a group by UUID
        self.assertDelete(endpoint_url + f"?uuid={reporters.uuid}", self.admin, status=204)

        reporters.refresh_from_db()
        self.assertFalse(reporters.is_active)

        # can't delete a system group
        self.assertDelete(
            endpoint_url + f"?uuid={open_tickets.uuid}",
            self.admin,
            errors={None: "Cannot delete system object."},
            status=403,
        )
        self.assertTrue(self.org.groups.filter(name="Open Tickets").exists())

        # can't delete a group with a trigger dependency
        trigger = Trigger.create(
            self.org,
            self.admin,
            Trigger.TYPE_KEYWORD,
            self.create_flow("Test"),
            keywords=["block_group"],
            match_type=Trigger.MATCH_FIRST_WORD,
        )
        trigger.groups.add(customers)

        self.assertDelete(
            endpoint_url + f"?uuid={customers.uuid}",
            self.admin,
            errors={None: "Group is being used by triggers which must be archived first."},
            status=400,
        )

        # or a campaign dependency
        trigger.groups.clear()
        campaign = Campaign.create(self.org, self.admin, "Reminders", customers)

        self.assertDelete(
            endpoint_url + f"?uuid={customers.uuid}",
            self.admin,
            errors={None: "Group is being used by campaigns which must be archived first."},
            status=400,
        )

        # can't delete a group in another org
        self.assertDelete(endpoint_url + f"?uuid={spammers.uuid}", self.admin, status=404)

        campaign.delete()
        for group in ContactGroup.objects.filter(is_system=False):
            group.release(self.admin)

        for i in range(10):
            ContactGroup.create_manual(self.org2, self.admin2, "group%d" % i)

        self.assertPost(endpoint_url, self.admin, {"name": "Reporters"}, status=201)

        ContactGroup.objects.filter(is_system=False, is_active=True).delete()

        for i in range(10):
            ContactGroup.create_manual(self.org, self.admin, "group%d" % i)

        self.assertPost(
            endpoint_url,
            self.admin,
            {"name": "Reporters"},
            errors={None: "Cannot create object because workspace has reached limit of 10."},
            status=409,
        )

    def test_labels(self):
        endpoint_url = reverse("api.v2.labels") + ".json"

        self.assertGetNotPermitted(endpoint_url, [None, self.agent])
        self.assertPostNotPermitted(endpoint_url, [None, self.user, self.agent])
        self.assertDeleteNotPermitted(endpoint_url + "?uuid=123", [None, self.user, self.agent])

        important = self.create_label("Important")
        feedback = self.create_label("Feedback")

        # a deleted label
        deleted = self.create_label("Deleted")
        deleted.release(self.admin)

        # create label for other org
        spam = self.create_label("Spam", org=self.org2)

        msg = self.create_incoming_msg(self.frank, "Hello")
        important.toggle_label([msg], add=True)

        # no filtering
        self.assertGet(
            endpoint_url,
            [self.user, self.editor],
            results=[
                {"uuid": str(feedback.uuid), "name": "Feedback", "count": 0},
                {"uuid": str(important.uuid), "name": "Important", "count": 1},
            ],
            num_queries=NUM_BASE_SESSION_QUERIES + 2,
        )

        # filter by UUID
        self.assertGet(endpoint_url + f"?uuid={feedback.uuid}", [self.editor], results=[feedback])

        # filter by name
        self.assertGet(endpoint_url + "?name=important", [self.editor], results=[important])

        # try to filter by both
        self.assertGet(
            endpoint_url + f"?uuid={important.uuid}&name=important",
            [self.editor],
            errors={None: "You may only specify one of the uuid, name parameters"},
        )

        # try to create empty label
        self.assertPost(endpoint_url, self.editor, {}, errors={"name": "This field is required."})

        # create new label
        response = self.assertPost(endpoint_url, self.editor, {"name": "Interesting"}, status=201)

        interesting = Label.objects.get(name="Interesting")
        self.assertEqual(response.json(), {"uuid": str(interesting.uuid), "name": "Interesting", "count": 0})

        # try to create another label with same name
        self.assertPost(
            endpoint_url, self.admin, {"name": "interesting"}, errors={"name": "This field must be unique."}
        )

        # it's fine if a label in another org has that name
        self.assertPost(endpoint_url, self.admin, {"name": "Spam"}, status=201)

        # try to create a label with invalid name
        self.assertPost(endpoint_url, self.admin, {"name": '""'}, errors={"name": 'Cannot contain the character: "'})

        # try to create a label with name that's too long
        self.assertPost(
            endpoint_url,
            self.admin,
            {"name": "x" * 65},
            errors={"name": "Ensure this field has no more than 64 characters."},
        )

        # update label by UUID
        response = self.assertPost(endpoint_url + f"?uuid={interesting.uuid}", self.admin, {"name": "More Interesting"})
        interesting.refresh_from_db()
        self.assertEqual(interesting.name, "More Interesting")

        # can't update label from other org
        self.assertPost(endpoint_url + f"?uuid={spam.uuid}", self.admin, {"name": "Won't work"}, status=404)

        # try an empty delete request
        self.assertDelete(
            endpoint_url, self.admin, errors={None: "URL must contain one of the following parameters: uuid"}
        )

        # delete a label by UUID
        self.assertDelete(endpoint_url + f"?uuid={interesting.uuid}", self.admin)
        interesting.refresh_from_db()
        self.assertFalse(interesting.is_active)

        # try to delete a label in another org
        self.assertDelete(endpoint_url + f"?uuid={spam.uuid}", self.admin, status=404)

        # try creating a new label after reaching the limit on labels
        with override_settings(ORG_LIMIT_DEFAULTS={"labels": self.org.msgs_labels.filter(is_active=True).count()}):
            self.assertPost(
                endpoint_url,
                self.admin,
                {"name": "Interesting"},
                errors={None: "Cannot create object because workspace has reached limit of 3."},
                status=409,
            )

    @mock_uuids
    def test_media(self):
        endpoint_url = reverse("api.v2.media") + ".json"

        self.assertGetNotAllowed(endpoint_url)
        self.assertPostNotPermitted(endpoint_url, [None, self.user])
        self.assertDeleteNotAllowed(endpoint_url)

        def upload(user, filename: str):
            self.login(user)
            with open(filename, "rb") as data:
                return self.client.post(endpoint_url, {"file": data}, HTTP_X_FORWARDED_HTTPS="https")

        self.login(self.admin)
        response = self.client.post(endpoint_url, {}, HTTP_X_FORWARDED_HTTPS="https")
        self.assertResponseError(response, "file", "No file was submitted.")

        response = upload(self.agent, f"{settings.MEDIA_ROOT}/test_imports/simple.xls")
        self.assertResponseError(response, "file", "Unsupported file type.")

        with patch("temba.msgs.models.Media.MAX_UPLOAD_SIZE", 1024):
            response = upload(self.editor, f"{settings.MEDIA_ROOT}/test_media/snow.mp4")
            self.assertResponseError(response, "file", "Limit for file uploads is 0.0009765625 MB.")

        response = upload(self.admin, f"{settings.MEDIA_ROOT}/test_media/steve marten.jpg")
        self.assertEqual(201, response.status_code)
        self.assertEqual(
            {
                "uuid": "b97f69f7-5edf-45c7-9fda-d37066eae91d",
                "content_type": "image/jpeg",
                "url": f"/media/test_orgs/{self.org.id}/media/b97f/b97f69f7-5edf-45c7-9fda-d37066eae91d/steve%20marten.jpg",
                "filename": "steve marten.jpg",
                "size": 7461,
            },
            response.json(),
        )

        media = Media.objects.get()
        self.assertEqual(Media.STATUS_READY, media.status)

        self.clear_storage()

    @mock_mailroom
    def test_messages(self, mr_mocks):
        endpoint_url = reverse("api.v2.messages") + ".json"

        self.assertGetNotPermitted(endpoint_url, [None, self.agent])
        self.assertPostNotPermitted(endpoint_url, [None, self.user])
        self.assertDeleteNotAllowed(endpoint_url)

        # create some messages
        flow = self.create_flow("Test")
        joe_msg1 = self.create_incoming_msg(self.joe, "Howdy", flow=flow)
        frank_msg1 = self.create_incoming_msg(self.frank, "Bonjour", channel=self.twitter)
        joe_msg2 = self.create_outgoing_msg(self.joe, "How are you?", status="Q")
        frank_msg2 = self.create_outgoing_msg(self.frank, "Ça va?", status="D")
        joe_msg3 = self.create_incoming_msg(
            self.joe, "Good", flow=flow, attachments=["image/jpeg:https://example.com/test.jpg"]
        )
        frank_msg3 = self.create_incoming_msg(self.frank, "Bien", channel=self.twitter, visibility="A")
        frank_msg4 = self.create_outgoing_msg(self.frank, "Ça va?", status="F")

        # add a failed message with no URN or channel
        joe_msg4 = self.create_outgoing_msg(self.joe, "Sorry", failed_reason=Msg.FAILED_NO_DESTINATION)

        # add an unhandled message
        self.create_incoming_msg(self.joe, "Just in!", status="P")

        # add a deleted message
        deleted_msg = self.create_incoming_msg(self.frank, "!@$!%", visibility="D")

        # add message in other org
        self.create_incoming_msg(self.hans, "Guten tag!", channel=None)

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
            num_queries=NUM_BASE_SESSION_QUERIES + 6,
        )

        # filter by inbox
        self.assertGet(
            endpoint_url + "?folder=INBOX",
            [self.admin],
            results=[
                {
                    "id": frank_msg1.id,
                    "type": "text",
                    "channel": {"uuid": str(self.twitter.uuid), "name": "Twitter Channel"},
                    "contact": {"uuid": str(self.frank.uuid), "name": "Frank"},
                    "urn": "twitter:franky",
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
            num_queries=NUM_BASE_SESSION_QUERIES + 5,
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
            endpoint_url + f"?contact={self.joe.uuid}", [self.admin], results=[joe_msg4, joe_msg3, joe_msg2, joe_msg1]
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
        broadcast = self.create_broadcast(
            self.user, {"eng": {"text": "A beautiful broadcast"}}, contacts=[self.joe, self.frank]
        )
        self.assertGet(
            endpoint_url + f"?broadcast={broadcast.id}",
            [self.editor],
            results=broadcast.msgs.order_by("-id"),
        )

        # can't filter with invalid id
        self.assertGet(endpoint_url + "?id=xyz", [self.editor], errors={None: "Value for id must be an integer"})

        # can't filter by more than one of contact, folder, label or broadcast together
        for query in (
            f"?contact={self.joe.uuid}&label=Spam",
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
            {"contact": self.joe.uuid},
            errors={"non_field_errors": "Must provide either text or attachments."},
        )

        # create a new message with just text - which shouldn't need to read anything about the msg from the db
        response = self.assertPost(
            endpoint_url,
            self.admin,
            {"contact": self.joe.uuid, "text": "Interesting"},
            status=201,
        )

        msg = Msg.objects.order_by("id").last()
        self.assertEqual(
            {
                "id": msg.id,
                "type": "text",
                "channel": {"uuid": str(self.channel.uuid), "name": "Test Channel"},
                "contact": {"uuid": str(self.joe.uuid), "name": "Joe Blow"},
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
            call(self.org, self.admin, self.joe, "Interesting", [], None),
            mr_mocks.calls["msg_send"][-1],
        )

        # try to create a message with an invalid attachment media UUID
        self.assertPost(
            endpoint_url,
            self.admin,
            {"contact": self.joe.uuid, "text": "Hi", "attachments": ["xxxx"]},
            errors={"attachments": "No such object: xxxx"},
        )

        # try to create a message with an non-existent attachment media UUID
        self.assertPost(
            endpoint_url,
            self.admin,
            {"contact": self.joe.uuid, "text": "Hi", "attachments": ["67ffe746-8771-40fb-89c1-5388e7ddd439"]},
            errors={"attachments": "No such object: 67ffe746-8771-40fb-89c1-5388e7ddd439"},
        )

        upload = self.upload_media(self.admin, f"{settings.MEDIA_ROOT}/test_media/steve marten.jpg")

        # create a new message with an attachment as the media UUID...
        self.assertPost(
            endpoint_url, self.admin, {"contact": self.joe.uuid, "attachments": [str(upload.uuid)]}, status=201
        )
        self.assertEqual(  # check that was sent via mailroom
            call(self.org, self.admin, self.joe, "", [f"image/jpeg:{upload.url}"], None),
            mr_mocks.calls["msg_send"][-1],
        )

        # create a new message with an attachment as <content-type>:<url>...
        self.assertPost(
            endpoint_url,
            self.admin,
            {"contact": self.joe.uuid, "attachments": [f"image/jpeg:https://example.com/{upload.uuid}.jpg"]},
            status=201,
        )
        self.assertEqual(
            call(self.org, self.admin, self.joe, "", [f"image/jpeg:{upload.url}"], None),
            mr_mocks.calls["msg_send"][-1],
        )

        # try to create a message with too many attachments
        self.assertPost(
            endpoint_url,
            self.admin,
            {"contact": self.joe.uuid, "attachments": [str(upload.uuid)] * 11},
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

        self.clear_storage()

    def test_message_actions(self):
        endpoint_url = reverse("api.v2.message_actions") + ".json"

        self.assertGetNotAllowed(endpoint_url)
        self.assertPostNotPermitted(endpoint_url, [None, self.user, self.agent])
        self.assertDeleteNotAllowed(endpoint_url)

        # create some messages to act on
        msg1 = self.create_incoming_msg(self.joe, "Msg #1")
        msg2 = self.create_incoming_msg(self.joe, "Msg #2")
        msg3 = self.create_incoming_msg(self.joe, "Msg #3")
        label = self.create_label("Test")

        # add label by name to messages 1 and 2
        self.assertPost(
            endpoint_url, self.editor, {"messages": [msg1.id, msg2.id], "action": "label", "label": "Test"}, status=204
        )
        self.assertEqual(set(label.get_messages()), {msg1, msg2})

        # add label by its UUID to message 3
        self.assertPost(
            endpoint_url, self.admin, {"messages": [msg3.id], "action": "label", "label": str(label.uuid)}, status=204
        )
        self.assertEqual(set(label.get_messages()), {msg1, msg2, msg3})

        # try to label with an invalid UUID
        self.assertPost(
            endpoint_url,
            self.admin,
            {"messages": [msg1.id], "action": "label", "label": "nope"},
            errors={"label": "No such object: nope"},
        )

        # remove label from message 2 by name (which is case-insensitive)
        self.assertPost(
            endpoint_url,
            self.admin,
            {"messages": [msg2.id], "action": "unlabel", "label": "test"},
            status=204,
        )
        self.assertEqual(set(label.get_messages()), {msg1, msg3})

        # and remove from messages 1 and 3 by UUID
        self.assertPost(
            endpoint_url,
            self.admin,
            {"messages": [msg1.id, msg3.id], "action": "unlabel", "label": str(label.uuid)},
            status=204,
        )
        self.assertEqual(set(label.get_messages()), set())

        # add new label via label_name
        self.assertPost(
            endpoint_url,
            self.admin,
            {"messages": [msg2.id, msg3.id], "action": "label", "label_name": "New"},
            status=204,
        )
        new_label = Label.objects.get(org=self.org, name="New", is_active=True)
        self.assertEqual(set(new_label.get_messages()), {msg2, msg3})

        # no difference if label already exists as it does now
        self.assertPost(
            endpoint_url,
            self.admin,
            {"messages": [msg1.id], "action": "label", "label_name": "New"},
            status=204,
        )
        self.assertEqual(set(new_label.get_messages()), {msg1, msg2, msg3})

        # can also remove by label_name
        self.assertPost(
            endpoint_url,
            self.admin,
            {"messages": [msg3.id], "action": "unlabel", "label_name": "New"},
            status=204,
        )
        self.assertEqual(set(new_label.get_messages()), {msg1, msg2})

        # and no error if label doesn't exist
        self.assertPost(
            endpoint_url,
            self.admin,
            {"messages": [msg3.id], "action": "unlabel", "label_name": "XYZ"},
            status=204,
        )
        # and label not lazy created in this case
        self.assertIsNone(Label.objects.filter(name="XYZ").first())

        # try to use invalid label name
        self.assertPost(
            endpoint_url,
            self.admin,
            {"messages": [msg1.id, msg2.id], "action": "label", "label_name": '"Hi"'},
            errors={"label_name": 'Cannot contain the character: "'},
        )

        # try to label without specifying a label
        self.assertPost(
            endpoint_url,
            self.admin,
            {"messages": [msg1.id, msg2.id], "action": "label"},
            errors={"non_field_errors": 'For action "label" you should also specify a label'},
        )
        self.assertPost(
            endpoint_url,
            self.admin,
            {"messages": [msg1.id, msg2.id], "action": "label", "label": ""},
            errors={"label": "This field may not be null."},
        )

        # try to provide both label and label_name
        self.assertPost(
            endpoint_url,
            self.admin,
            {"messages": [msg1.id], "action": "label", "label": "Test", "label_name": "Test"},
            errors={"non_field_errors": "Can't specify both label and label_name."},
        )

        # archive all messages
        self.assertPost(
            endpoint_url, self.admin, {"messages": [msg1.id, msg2.id, msg3.id], "action": "archive"}, status=204
        )
        self.assertEqual(set(Msg.objects.filter(visibility=Msg.VISIBILITY_ARCHIVED)), {msg1, msg2, msg3})

        # restore message 1
        self.assertPost(endpoint_url, self.admin, {"messages": [msg1.id], "action": "restore"}, status=204)
        self.assertEqual(set(Msg.objects.filter(visibility=Msg.VISIBILITY_VISIBLE)), {msg1})
        self.assertEqual(set(Msg.objects.filter(visibility=Msg.VISIBILITY_ARCHIVED)), {msg2, msg3})

        # delete messages 2
        self.assertPost(endpoint_url, self.admin, {"messages": [msg2.id], "action": "delete"}, status=204)
        self.assertEqual(set(Msg.objects.filter(visibility=Msg.VISIBILITY_VISIBLE)), {msg1})
        self.assertEqual(set(Msg.objects.filter(visibility=Msg.VISIBILITY_ARCHIVED)), {msg3})
        self.assertEqual(set(Msg.objects.filter(visibility=Msg.VISIBILITY_DELETED_BY_USER)), {msg2})

        # try to act on a a valid message and a deleted message
        response = self.assertPost(
            endpoint_url, self.admin, {"messages": [msg2.id, msg3.id], "action": "restore"}, status=200
        )

        # should get a partial success
        self.assertEqual(response.json(), {"failures": [msg2.id]})
        self.assertEqual(set(Msg.objects.filter(visibility=Msg.VISIBILITY_VISIBLE)), {msg1, msg3})

        # try to act on an outgoing message
        msg4 = self.create_outgoing_msg(self.joe, "Hi Joe")
        self.assertPost(
            endpoint_url,
            self.admin,
            {"messages": [msg1.id, msg4.id], "action": "archive"},
            errors={"messages": f"Not an incoming message: {msg4.id}"},
        )

        # try to provide a label for a non-labelling action
        self.assertPost(
            endpoint_url,
            self.admin,
            {"messages": [msg1.id], "action": "archive", "label": "Test"},
            errors={"non_field_errors": 'For action "archive" you should not specify a label'},
        )

        # try to invoke an invalid action
        self.assertPost(
            endpoint_url,
            self.admin,
            {"messages": [msg1.id], "action": "like"},
            errors={"action": '"like" is not a valid choice.'},
        )

    def test_runs(self):
        endpoint_url = reverse("api.v2.runs") + ".json"

        self.assertGetNotPermitted(endpoint_url, [None, self.agent])
        self.assertPostNotAllowed(endpoint_url)
        self.assertDeleteNotAllowed(endpoint_url)

        flow1 = self.get_flow("color_v13")
        flow2 = flow1.clone(self.user)

        flow1_nodes = flow1.get_definition()["nodes"]
        color_prompt = flow1_nodes[0]
        color_split = flow1_nodes[4]
        blue_reply = flow1_nodes[2]

        start1 = FlowStart.create(flow1, self.admin, contacts=[self.joe])
        joe_msg = self.create_incoming_msg(self.joe, "it is blue")
        frank_msg = self.create_incoming_msg(self.frank, "Indigo")

        joe_run1 = (
            MockSessionWriter(self.joe, flow1, start=start1)
            .visit(color_prompt)
            .visit(color_split)
            .wait()
            .resume(msg=joe_msg)
            .set_result("Color", "blue", "Blue", "it is blue")
            .visit(blue_reply)
            .complete()
            .save()
        ).session.runs.get()

        frank_run1 = (
            MockSessionWriter(self.frank, flow1)
            .visit(color_prompt)
            .visit(color_split)
            .wait()
            .resume(msg=frank_msg)
            .set_result("Color", "Indigo", "Other", "Indigo")
            .wait()
            .save()
        ).session.runs.get()

        joe_run2 = (
            MockSessionWriter(self.joe, flow1).visit(color_prompt).visit(color_split).wait().save()
        ).session.runs.get()
        frank_run2 = (
            MockSessionWriter(self.frank, flow1).visit(color_prompt).visit(color_split).wait().save()
        ).session.runs.get()

        joe_run3 = MockSessionWriter(self.joe, flow2).wait().save().session.runs.get()

        # add a run for another org
        flow3 = self.create_flow("Test", org=self.org2)
        MockSessionWriter(self.hans, flow3).wait().save()

        # refresh runs which will have been modified by being interrupted
        joe_run1.refresh_from_db()
        joe_run2.refresh_from_db()
        frank_run1.refresh_from_db()
        frank_run2.refresh_from_db()

        # no filtering
        response = self.assertGet(
            endpoint_url,
            [self.user, self.editor],
            results=[joe_run3, joe_run2, frank_run2, frank_run1, joe_run1],
            num_queries=NUM_BASE_SESSION_QUERIES + 6,
        )
        resp_json = response.json()
        self.assertEqual(
            {
                "id": frank_run2.id,
                "uuid": str(frank_run2.uuid),
                "flow": {"uuid": str(flow1.uuid), "name": "Colors"},
                "contact": {
                    "uuid": str(self.frank.uuid),
                    "name": self.frank.name,
                    "urn": "twitter:franky",
                    "urn_display": "franky",
                },
                "start": None,
                "responded": False,
                "path": [
                    {
                        "node": color_prompt["uuid"],
                        "time": format_datetime(iso8601.parse_date(frank_run2.path[0]["arrived_on"])),
                    },
                    {
                        "node": color_split["uuid"],
                        "time": format_datetime(iso8601.parse_date(frank_run2.path[1]["arrived_on"])),
                    },
                ],
                "values": {},
                "created_on": format_datetime(frank_run2.created_on),
                "modified_on": format_datetime(frank_run2.modified_on),
                "exited_on": None,
                "exit_type": None,
            },
            resp_json["results"][2],
        )
        self.assertEqual(
            {
                "id": joe_run1.id,
                "uuid": str(joe_run1.uuid),
                "flow": {"uuid": str(flow1.uuid), "name": "Colors"},
                "contact": {
                    "uuid": str(self.joe.uuid),
                    "name": self.joe.name,
                    "urn": "tel:+250788123123",
                    "urn_display": "0788 123 123",
                },
                "start": {"uuid": str(joe_run1.start.uuid)},
                "responded": True,
                "path": [
                    {
                        "node": color_prompt["uuid"],
                        "time": format_datetime(iso8601.parse_date(joe_run1.path[0]["arrived_on"])),
                    },
                    {
                        "node": color_split["uuid"],
                        "time": format_datetime(iso8601.parse_date(joe_run1.path[1]["arrived_on"])),
                    },
                    {
                        "node": blue_reply["uuid"],
                        "time": format_datetime(iso8601.parse_date(joe_run1.path[2]["arrived_on"])),
                    },
                ],
                "values": {
                    "color": {
                        "value": "blue",
                        "category": "Blue",
                        "node": color_split["uuid"],
                        "time": format_datetime(iso8601.parse_date(joe_run1.results["color"]["created_on"])),
                        "name": "Color",
                        "input": "it is blue",
                    }
                },
                "created_on": format_datetime(joe_run1.created_on),
                "modified_on": format_datetime(joe_run1.modified_on),
                "exited_on": format_datetime(joe_run1.exited_on),
                "exit_type": "completed",
            },
            resp_json["results"][4],
        )

        # can request without path data
        response = self.assertGet(
            endpoint_url + "?paths=false", [self.editor], results=[joe_run3, joe_run2, frank_run2, frank_run1, joe_run1]
        )
        resp_json = response.json()
        self.assertEqual(
            {
                "id": frank_run2.id,
                "uuid": str(frank_run2.uuid),
                "flow": {"uuid": str(flow1.uuid), "name": "Colors"},
                "contact": {
                    "uuid": str(self.frank.uuid),
                    "name": self.frank.name,
                    "urn": "twitter:franky",
                    "urn_display": "franky",
                },
                "start": None,
                "responded": False,
                "path": None,
                "values": {},
                "created_on": format_datetime(frank_run2.created_on),
                "modified_on": format_datetime(frank_run2.modified_on),
                "exited_on": None,
                "exit_type": None,
            },
            resp_json["results"][2],
        )

        # reversed
        self.assertGet(
            endpoint_url + "?reverse=true",
            [self.editor],
            results=[joe_run1, frank_run1, frank_run2, joe_run2, joe_run3],
        )

        # filter by id
        self.assertGet(endpoint_url + f"?id={frank_run2.id}", [self.admin], results=[frank_run2])

        # anon orgs should not have a URN field
        with self.anonymous(self.org):
            response = self.assertGet(endpoint_url + f"?id={frank_run2.id}", [self.admin], results=[frank_run2])
            self.assertEqual(
                {
                    "id": frank_run2.pk,
                    "uuid": str(frank_run2.uuid),
                    "flow": {"uuid": flow1.uuid, "name": "Colors"},
                    "contact": {
                        "uuid": self.frank.uuid,
                        "name": self.frank.name,
                        "urn": "twitter:********",
                        "urn_display": None,
                        "anon_display": f"{self.frank.id:010}",
                    },
                    "start": None,
                    "responded": False,
                    "path": [
                        {
                            "node": color_prompt["uuid"],
                            "time": format_datetime(iso8601.parse_date(frank_run2.path[0]["arrived_on"])),
                        },
                        {
                            "node": color_split["uuid"],
                            "time": format_datetime(iso8601.parse_date(frank_run2.path[1]["arrived_on"])),
                        },
                    ],
                    "values": {},
                    "created_on": format_datetime(frank_run2.created_on),
                    "modified_on": format_datetime(frank_run2.modified_on),
                    "exited_on": None,
                    "exit_type": None,
                },
                response.json()["results"][0],
            )

        # filter by uuid
        self.assertGet(endpoint_url + f"?uuid={frank_run2.uuid}", [self.admin], results=[frank_run2])

        # filter by id and uuid
        self.assertGet(endpoint_url + f"?uuid={frank_run2.uuid}&id={joe_run1.id}", [self.admin], results=[])
        self.assertGet(endpoint_url + f"?uuid={frank_run2.uuid}&id={frank_run2.id}", [self.admin], results=[frank_run2])

        # filter by flow
        self.assertGet(
            endpoint_url + f"?flow={flow1.uuid}", [self.admin], results=[joe_run2, frank_run2, frank_run1, joe_run1]
        )

        # doesn't work if flow is inactive
        flow1.is_active = False
        flow1.save()

        self.assertGet(endpoint_url + f"?flow={flow1.uuid}", [self.admin], results=[])

        # restore to active
        flow1.is_active = True
        flow1.save()

        # filter by invalid flow
        self.assertGet(endpoint_url + "?flow=invalid", [self.admin], results=[])

        # filter by flow + responded
        self.assertGet(
            endpoint_url + f"?flow={flow1.uuid}&responded=TrUe", [self.admin], results=[frank_run1, joe_run1]
        )

        # filter by contact
        self.assertGet(endpoint_url + f"?contact={self.joe.uuid}", [self.admin], results=[joe_run3, joe_run2, joe_run1])

        # filter by invalid contact
        self.assertGet(endpoint_url + "?contact=invalid", [self.admin], results=[])

        # filter by contact + responded
        self.assertGet(endpoint_url + f"?contact={self.joe.uuid}&responded=yes", [self.admin], results=[joe_run1])

        # filter by before / after
        self.assertGet(
            endpoint_url + f"?before={format_datetime(frank_run1.modified_on)}",
            [self.admin],
            results=[frank_run1, joe_run1],
        )
        self.assertGet(
            endpoint_url + f"?after={format_datetime(frank_run1.modified_on)}",
            [self.admin],
            results=[joe_run3, joe_run2, frank_run2, frank_run1],
        )

        # filter by invalid before / after
        self.assertGet(endpoint_url + "?before=longago", [self.admin], results=[])
        self.assertGet(endpoint_url + "?after=thefuture", [self.admin], results=[])

        # can't filter by both contact and flow together
        self.assertGet(
            endpoint_url + f"?contact={self.joe.uuid}&flow={flow1.uuid}",
            [self.admin],
            errors={None: "You may only specify one of the contact, flow parameters"},
        )

    def test_optins(self):
        endpoint_url = reverse("api.v2.optins") + ".json"

        self.assertGetNotPermitted(endpoint_url, [None, self.agent])
        self.assertPostNotPermitted(endpoint_url, [None, self.agent, self.user])
        self.assertDeleteNotAllowed(endpoint_url)

        # create some optins
        polls = OptIn.create(self.org, self.admin, "Polls")
        offers = OptIn.create(self.org, self.admin, "Offers")
        OptIn.create(self.org2, self.admin, "Promos")

        # no filtering
        self.assertGet(
            endpoint_url,
            [self.user, self.editor],
            results=[
                {
                    "uuid": str(offers.uuid),
                    "name": "Offers",
                    "created_on": format_datetime(offers.created_on),
                },
                {
                    "uuid": str(polls.uuid),
                    "name": "Polls",
                    "created_on": format_datetime(polls.created_on),
                },
            ],
            num_queries=NUM_BASE_SESSION_QUERIES + 1,
        )

        # try to create empty optin
        self.assertPost(endpoint_url, self.admin, {}, errors={"name": "This field is required."})

        # create new optin
        response = self.assertPost(endpoint_url, self.admin, {"name": "Alerts"}, status=201)

        alerts = OptIn.objects.get(name="Alerts")
        self.assertEqual(
            response.json(),
            {
                "uuid": str(alerts.uuid),
                "name": "Alerts",
                "created_on": matchers.ISODate(),
            },
        )

        # try to create another optin with same name
        self.assertPost(endpoint_url, self.admin, {"name": "Alerts"}, errors={"name": "This field must be unique."})

        # it's fine if a optin in another org has that name
        self.assertPost(endpoint_url, self.editor, {"name": "Promos"}, status=201)

        # try to create a optin with invalid name
        self.assertPost(endpoint_url, self.admin, {"name": '"Hi"'}, errors={"name": 'Cannot contain the character: "'})

        # try to create a optin with name that's too long
        self.assertPost(
            endpoint_url,
            self.admin,
            {"name": "x" * 65},
            errors={"name": "Ensure this field has no more than 64 characters."},
        )

    def test_resthooks(self):
        hooks_url = reverse("api.v2.resthooks") + ".json"
        subs_url = reverse("api.v2.resthook_subscribers") + ".json"
        events_url = reverse("api.v2.resthook_events") + ".json"

        self.assertGetNotPermitted(hooks_url, [None, self.agent, self.user])
        self.assertPostNotAllowed(hooks_url)
        self.assertDeleteNotAllowed(hooks_url)

        self.assertGetNotPermitted(subs_url, [None, self.agent, self.user])
        self.assertPostNotPermitted(subs_url, [None, self.agent, self.user])
        self.assertDeleteNotPermitted(subs_url, [None, self.agent, self.user])

        self.assertGetNotPermitted(events_url, [None, self.agent, self.user])
        self.assertPostNotAllowed(events_url)
        self.assertDeleteNotAllowed(events_url)

        # create some resthooks
        resthook1 = Resthook.get_or_create(self.org, "new-mother", self.admin)
        resthook2 = Resthook.get_or_create(self.org, "new-father", self.admin)
        resthook3 = Resthook.get_or_create(self.org, "not-active", self.admin)
        resthook3.is_active = False
        resthook3.save()

        # create a resthook for another org
        other_org_resthook = Resthook.get_or_create(self.org2, "spam", self.admin2)

        # fetch hooks with no filtering
        self.assertGet(
            hooks_url,
            [self.editor, self.admin],
            results=[
                {
                    "resthook": "new-father",
                    "created_on": format_datetime(resthook2.created_on),
                    "modified_on": format_datetime(resthook2.modified_on),
                },
                {
                    "resthook": "new-mother",
                    "created_on": format_datetime(resthook1.created_on),
                    "modified_on": format_datetime(resthook1.modified_on),
                },
            ],
            num_queries=NUM_BASE_SESSION_QUERIES + 1,
        )

        # try to create empty subscription
        self.assertPost(
            subs_url,
            self.admin,
            {},
            errors={"resthook": "This field is required.", "target_url": "This field is required."},
        )

        # try to create one for resthook in other org
        self.assertPost(
            subs_url,
            self.admin,
            {"resthook": "spam", "target_url": "https://foo.bar/"},
            errors={"resthook": "No resthook with slug: spam"},
        )

        # create subscribers on each resthook
        self.assertPost(
            subs_url, self.editor, {"resthook": "new-mother", "target_url": "https://foo.bar/mothers"}, status=201
        )
        self.assertPost(
            subs_url, self.admin, {"resthook": "new-father", "target_url": "https://foo.bar/fathers"}, status=201
        )

        hook1_subscriber = resthook1.subscribers.get()
        hook2_subscriber = resthook2.subscribers.get()

        # create a subscriber on our other resthook
        other_org_subscriber = other_org_resthook.add_subscriber("https://bar.foo", self.admin2)

        # fetch subscribers with no filtering
        self.assertGet(
            subs_url,
            [self.editor, self.admin],
            results=[
                {
                    "id": hook2_subscriber.id,
                    "resthook": "new-father",
                    "target_url": "https://foo.bar/fathers",
                    "created_on": format_datetime(hook2_subscriber.created_on),
                },
                {
                    "id": hook1_subscriber.id,
                    "resthook": "new-mother",
                    "target_url": "https://foo.bar/mothers",
                    "created_on": format_datetime(hook1_subscriber.created_on),
                },
            ],
            num_queries=NUM_BASE_SESSION_QUERIES + 1,
        )

        # filter by id
        self.assertGet(subs_url + f"?id={hook1_subscriber.id}", [self.editor], results=[hook1_subscriber])

        # filter by resthook
        self.assertGet(subs_url + "?resthook=new-father", [self.editor], results=[hook2_subscriber])

        # remove a subscriber
        self.assertDelete(subs_url + f"?id={hook2_subscriber.id}", self.admin)

        # subscriber should no longer be active
        hook2_subscriber.refresh_from_db()
        self.assertFalse(hook2_subscriber.is_active)

        # try to delete without providing id
        self.assertDelete(
            subs_url + "?", self.editor, errors={None: "URL must contain one of the following parameters: id"}
        )

        # try to delete a subscriber from another org
        self.assertDelete(subs_url + f"?id={other_org_subscriber.id}", self.editor, status=404)

        # create some events on our resthooks
        event1 = WebHookEvent.objects.create(
            org=self.org,
            resthook=resthook1,
            data={"event": "new mother", "values": {"name": "Greg"}, "steps": {"uuid": "abcde"}},
        )
        event2 = WebHookEvent.objects.create(
            org=self.org,
            resthook=resthook2,
            data={"event": "new father", "values": {"name": "Yo"}, "steps": {"uuid": "12345"}},
        )

        # fetch events with no filtering
        self.assertGet(
            events_url,
            [self.editor, self.admin],
            results=[
                {
                    "resthook": "new-father",
                    "created_on": format_datetime(event2.created_on),
                    "data": {"event": "new father", "values": {"name": "Yo"}, "steps": {"uuid": "12345"}},
                },
                {
                    "resthook": "new-mother",
                    "created_on": format_datetime(event1.created_on),
                    "data": {"event": "new mother", "values": {"name": "Greg"}, "steps": {"uuid": "abcde"}},
                },
            ],
            num_queries=NUM_BASE_SESSION_QUERIES + 1,
        )

    @mock_mailroom
    def test_tickets(self, mr_mocks):
        endpoint_url = reverse("api.v2.tickets") + ".json"

        self.assertGetNotPermitted(endpoint_url, [None])
        self.assertPostNotAllowed(endpoint_url)
        self.assertDeleteNotAllowed(endpoint_url)

        # create some tickets
        ann = self.create_contact("Ann", urns=["twitter:annie"])
        bob = self.create_contact("Bob", urns=["twitter:bobby"])
        flow = self.create_flow("Support")

        ticket1 = self.create_ticket(
            ann, opened_by=self.admin, closed_on=datetime(2021, 1, 1, 12, 30, 45, 123456, tzone.utc)
        )
        ticket2 = self.create_ticket(bob, opened_in=flow)
        ticket3 = self.create_ticket(bob, assignee=self.agent)

        # on another org
        self.create_ticket(self.create_contact("Jim", urns=["twitter:jimmy"], org=self.org2))

        # no filtering
        self.assertGet(
            endpoint_url,
            [self.user, self.editor, self.admin, self.agent],
            results=[
                {
                    "uuid": str(ticket3.uuid),
                    "assignee": {"email": "agent@nyaruka.com", "name": "Agnes"},
                    "contact": {"uuid": str(bob.uuid), "name": "Bob"},
                    "status": "open",
                    "topic": {"uuid": str(self.org.default_ticket_topic.uuid), "name": "General"},
                    "body": None,
                    "opened_on": format_datetime(ticket3.opened_on),
                    "opened_by": None,
                    "opened_in": None,
                    "modified_on": format_datetime(ticket3.modified_on),
                    "closed_on": None,
                },
                {
                    "uuid": str(ticket2.uuid),
                    "assignee": None,
                    "contact": {"uuid": str(bob.uuid), "name": "Bob"},
                    "status": "open",
                    "topic": {"uuid": str(self.org.default_ticket_topic.uuid), "name": "General"},
                    "body": None,
                    "opened_on": format_datetime(ticket2.opened_on),
                    "opened_by": None,
                    "opened_in": {"uuid": str(flow.uuid), "name": "Support"},
                    "modified_on": format_datetime(ticket2.modified_on),
                    "closed_on": None,
                },
                {
                    "uuid": str(ticket1.uuid),
                    "assignee": None,
                    "contact": {"uuid": str(ann.uuid), "name": "Ann"},
                    "status": "closed",
                    "topic": {"uuid": str(self.org.default_ticket_topic.uuid), "name": "General"},
                    "body": None,
                    "opened_on": format_datetime(ticket1.opened_on),
                    "opened_by": {"email": "admin@nyaruka.com", "name": "Andy"},
                    "opened_in": None,
                    "modified_on": format_datetime(ticket1.modified_on),
                    "closed_on": "2021-01-01T12:30:45.123456Z",
                },
            ],
            num_queries=NUM_BASE_SESSION_QUERIES + 6,
        )

        # filter by contact uuid (not there)
        self.assertGet(endpoint_url + "?contact=09d23a05-47fe-11e4-bfe9-b8f6b119e9ab", [self.admin], results=[])

        # filter by contact uuid present
        self.assertGet(endpoint_url + f"?contact={bob.uuid}", [self.admin], results=[ticket3, ticket2])

        # filter further by ticket uuid
        self.assertGet(endpoint_url + f"?uuid={ticket3.uuid}", [self.admin], results=[ticket3])

    @mock_mailroom
    def test_ticket_actions(self, mr_mocks):
        endpoint_url = reverse("api.v2.ticket_actions") + ".json"

        self.assertGetNotAllowed(endpoint_url)
        self.assertPostNotPermitted(endpoint_url, [None, self.user])
        self.assertDeleteNotAllowed(endpoint_url)

        # create some tickets
        sales = Topic.create(self.org, self.admin, "Sales")
        ticket1 = self.create_ticket(self.joe, closed_on=datetime(2021, 1, 1, 12, 30, 45, 123456, tzone.utc))
        ticket2 = self.create_ticket(self.joe)
        self.create_ticket(self.frank)

        # on another org
        ticket4 = self.create_ticket(self.create_contact("Jim", urns=["twitter:jimmy"], org=self.org2))

        # try actioning more tickets than this endpoint is allowed to operate on at one time
        self.assertPost(
            endpoint_url,
            self.agent,
            {"tickets": [str(x) for x in range(101)], "action": "close"},
            errors={"tickets": "Ensure this field has no more than 100 elements."},
        )

        # try actioning a ticket which is not in this org
        self.assertPost(
            endpoint_url,
            self.agent,
            {"tickets": [str(ticket1.uuid), str(ticket4.uuid)], "action": "close"},
            errors={"tickets": f"No such object: {ticket4.uuid}"},
        )

        # try to close tickets without specifying any tickets
        self.assertPost(
            endpoint_url,
            self.agent,
            {"action": "close"},
            errors={"tickets": "This field is required."},
        )

        # try to assign ticket without specifying assignee
        self.assertPost(
            endpoint_url,
            self.agent,
            {"tickets": [str(ticket1.uuid)], "action": "assign"},
            errors={"non_field_errors": 'For action "assign" you must specify the assignee'},
        )

        # try to add a note without specifying note
        self.assertPost(
            endpoint_url,
            self.agent,
            {"tickets": [str(ticket1.uuid)], "action": "add_note"},
            errors={"non_field_errors": 'For action "add_note" you must specify the note'},
        )

        # try to change topic without specifying topic
        self.assertPost(
            endpoint_url,
            self.agent,
            {"tickets": [str(ticket1.uuid)], "action": "change_topic"},
            errors={"non_field_errors": 'For action "change_topic" you must specify the topic'},
        )

        # assign valid tickets to a user
        self.assertPost(
            endpoint_url,
            self.agent,
            {"tickets": [str(ticket1.uuid), str(ticket2.uuid)], "action": "assign", "assignee": "agent@nyaruka.com"},
            status=204,
        )

        ticket1.refresh_from_db()
        ticket2.refresh_from_db()
        self.assertEqual(self.agent, ticket1.assignee)
        self.assertEqual(self.agent, ticket2.assignee)

        # unassign tickets
        self.assertPost(
            endpoint_url,
            self.agent,
            {"tickets": [str(ticket1.uuid)], "action": "assign", "assignee": None},
            status=204,
        )

        ticket1.refresh_from_db()
        self.assertIsNone(ticket1.assignee)

        # add a note to tickets
        self.assertPost(
            endpoint_url,
            self.agent,
            {"tickets": [str(ticket1.uuid), str(ticket2.uuid)], "action": "add_note", "note": "Looks important"},
            status=204,
        )

        self.assertEqual("Looks important", ticket1.events.last().note)
        self.assertEqual("Looks important", ticket2.events.last().note)

        # change topic of tickets
        self.assertPost(
            endpoint_url,
            self.agent,
            {"tickets": [str(ticket1.uuid), str(ticket2.uuid)], "action": "change_topic", "topic": str(sales.uuid)},
            status=204,
        )

        ticket1.refresh_from_db()
        ticket2.refresh_from_db()
        self.assertEqual(sales, ticket1.topic)
        self.assertEqual(sales, ticket2.topic)

        # close tickets
        self.assertPost(
            endpoint_url,
            self.agent,
            {"tickets": [str(ticket1.uuid), str(ticket2.uuid)], "action": "close"},
            status=204,
        )

        ticket1.refresh_from_db()
        ticket2.refresh_from_db()
        self.assertEqual("C", ticket1.status)
        self.assertEqual("C", ticket2.status)

        # and finally reopen them
        self.assertPost(
            endpoint_url,
            self.agent,
            {"tickets": [str(ticket1.uuid), str(ticket2.uuid)], "action": "reopen"},
            status=204,
        )

        ticket1.refresh_from_db()
        ticket2.refresh_from_db()
        self.assertEqual("O", ticket1.status)
        self.assertEqual("O", ticket2.status)

    def test_topics(self):
        endpoint_url = reverse("api.v2.topics") + ".json"

        self.assertGetNotPermitted(endpoint_url, [None])
        self.assertPostNotPermitted(endpoint_url, [None, self.agent, self.user])
        self.assertDeleteNotAllowed(endpoint_url)

        # create some topics
        support = Topic.create(self.org, self.admin, "Support")
        sales = Topic.create(self.org, self.admin, "Sales")
        other_org = Topic.create(self.org2, self.admin, "Bugs")

        contact = self.create_contact("Ann", phone="+1234567890")
        self.create_ticket(contact, topic=support)

        # no filtering
        self.assertGet(
            endpoint_url,
            [self.user, self.editor],
            results=[
                {
                    "uuid": str(sales.uuid),
                    "name": "Sales",
                    "counts": {"open": 0, "closed": 0},
                    "system": False,
                    "created_on": format_datetime(sales.created_on),
                },
                {
                    "uuid": str(support.uuid),
                    "name": "Support",
                    "counts": {"open": 1, "closed": 0},
                    "system": False,
                    "created_on": format_datetime(support.created_on),
                },
                {
                    "uuid": str(self.org.default_ticket_topic.uuid),
                    "name": "General",
                    "counts": {"open": 0, "closed": 0},
                    "system": True,
                    "created_on": format_datetime(self.org.default_ticket_topic.created_on),
                },
            ],
            num_queries=NUM_BASE_SESSION_QUERIES + 3,
        )

        # try to create empty topic
        response = self.assertPost(endpoint_url, self.editor, {}, errors={"name": "This field is required."})

        # create new topic
        response = self.assertPost(endpoint_url, self.editor, {"name": "Food"}, status=201)

        food = Topic.objects.get(name="Food")
        self.assertEqual(
            response.json(),
            {
                "uuid": str(food.uuid),
                "name": "Food",
                "counts": {"open": 0, "closed": 0},
                "system": False,
                "created_on": matchers.ISODate(),
            },
        )

        # try to create another topic with same name
        self.assertPost(endpoint_url, self.editor, {"name": "Food"}, errors={"name": "This field must be unique."})

        # it's fine if a topic in another org has that name
        self.assertPost(endpoint_url, self.editor, {"name": "Bugs"}, status=201)

        # try to create a topic with invalid name
        self.assertPost(endpoint_url, self.editor, {"name": '"Hi"'}, errors={"name": 'Cannot contain the character: "'})

        # try to create a topic with name that's too long
        self.assertPost(
            endpoint_url,
            self.editor,
            {"name": "x" * 65},
            errors={"name": "Ensure this field has no more than 64 characters."},
        )

        # update topic by UUID
        self.assertPost(endpoint_url + f"?uuid={support.uuid}", self.admin, {"name": "Support Tickets"})

        support.refresh_from_db()
        self.assertEqual(support.name, "Support Tickets")

        # can't update default topic for an org
        self.assertPost(
            endpoint_url + f"?uuid={self.org.default_ticket_topic.uuid}",
            self.admin,
            {"name": "Won't work"},
            errors={None: "Cannot modify system object."},
            status=403,
        )

        # can't update topic from other org
        self.assertPost(endpoint_url + f"?uuid={other_org.uuid}", self.admin, {"name": "Won't work"}, status=404)

        # can't update topic to same name as existing topic
        self.assertPost(
            endpoint_url + f"?uuid={support.uuid}",
            self.admin,
            {"name": "General"},
            errors={"name": "This field must be unique."},
        )

        # try creating a new topic after reaching the limit
        current_count = self.org.topics.filter(is_system=False, is_active=True).count()
        with override_settings(ORG_LIMIT_DEFAULTS={"topics": current_count}):
            response = self.assertPost(
                endpoint_url,
                self.admin,
                {"name": "Interesting"},
                errors={None: "Cannot create object because workspace has reached limit of 4."},
                status=409,
            )

    def test_users(self):
        endpoint_url = reverse("api.v2.users") + ".json"

        self.assertGetNotPermitted(endpoint_url, [None])
        self.assertPostNotAllowed(endpoint_url)
        self.assertDeleteNotAllowed(endpoint_url)

        self.assertGet(
            endpoint_url,
            [self.agent, self.user, self.editor, self.admin],
            results=[
                {
                    "avatar": None,
                    "email": "agent@nyaruka.com",
                    "first_name": "Agnes",
                    "last_name": "",
                    "role": "agent",
                    "created_on": format_datetime(self.agent.date_joined),
                },
                {
                    "avatar": None,
                    "email": "viewer@nyaruka.com",
                    "first_name": "",
                    "last_name": "",
                    "role": "viewer",
                    "created_on": format_datetime(self.user.date_joined),
                },
                {
                    "avatar": None,
                    "email": "editor@nyaruka.com",
                    "first_name": "Ed",
                    "last_name": "McEdits",
                    "role": "editor",
                    "created_on": format_datetime(self.editor.date_joined),
                },
                {
                    "avatar": None,
                    "email": "admin@nyaruka.com",
                    "first_name": "Andy",
                    "last_name": "",
                    "role": "administrator",
                    "created_on": format_datetime(self.admin.date_joined),
                },
            ],
            # one query per user for their settings
            num_queries=NUM_BASE_SESSION_QUERIES + 3,
        )

        # filter by roles
        self.assertGet(endpoint_url + "?role=agent&role=editor", [self.editor], results=[self.agent, self.editor])

        # non-existent roles ignored
        self.assertGet(endpoint_url + "?role=caretaker&role=editor", [self.editor], results=[self.editor])

    def test_workspace(self):
        endpoint_url = reverse("api.v2.workspace") + ".json"

        self.assertGetNotPermitted(endpoint_url, [None])
        self.assertPostNotAllowed(endpoint_url)
        self.assertDeleteNotAllowed(endpoint_url)

        # no filtering options.. just gets the current org
        self.assertGet(
            endpoint_url,
            [self.agent, self.user, self.editor, self.admin],
            raw={
                "uuid": str(self.org.uuid),
                "name": "Nyaruka",
                "country": "RW",
                "languages": ["eng", "kin"],
                "primary_language": "eng",
                "timezone": "Africa/Kigali",
                "date_style": "day_first",
                "credits": {"used": -1, "remaining": -1},
                "anon": False,
            },
        )

        self.org.set_flow_languages(self.admin, ["kin"])

        self.assertGet(
            endpoint_url,
            [self.agent],
            raw={
                "uuid": str(self.org.uuid),
                "name": "Nyaruka",
                "country": "RW",
                "languages": ["kin"],
                "primary_language": "kin",
                "timezone": "Africa/Kigali",
                "date_style": "day_first",
                "credits": {"used": -1, "remaining": -1},
                "anon": False,
            },
        )
