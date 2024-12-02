from rest_framework import serializers

from django.conf import settings

from temba.api.v2 import fields
from temba.campaigns.models import Campaign, CampaignEvent
from temba.contacts.models import Contact, ContactField, ContactURN

from . import APITest


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
                "VIEWER@TEXTIT.COM": self.user,
                "admin@textit.com": self.admin,
                self.editor.email: serializers.ValidationError,  # deleted
                self.admin2.email: serializers.ValidationError,  # not in org
            },
            representations={
                self.user: {"email": "viewer@textit.com", "name": ""},
                self.editor: {"email": "editor@textit.com", "name": "Ed McEdits"},
            },
        )
        self.assert_field(
            fields.UserField(source="test", assignable_only=True),
            submissions={
                self.user.email: serializers.ValidationError,  # not assignable
                self.admin.email: self.admin,
                self.agent.email: self.agent,
            },
            representations={self.agent: {"email": "agent@textit.com", "name": "Agnes"}},
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
