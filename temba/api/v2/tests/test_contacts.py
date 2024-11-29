from datetime import datetime, timezone as tzone
from urllib.parse import quote_plus

from django.urls import reverse
from django.utils import timezone

from temba.api.v2.serializers import format_datetime
from temba.contacts.models import Contact, ContactField, ContactGroup
from temba.tests import mock_mailroom

from . import APITest


class ContactsEndpointTest(APITest):
    def setUp(self):
        super().setUp()

        self.joe = self.create_contact("Joe Blow", phone="+250788123123")
        self.frank = self.create_contact("Frank", urns=["facebook:123456"])

    @mock_mailroom
    def test_endpoint(self, mr_mocks):
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
            num_queries=self.BASE_SESSION_QUERIES + 7,
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
            num_queries=self.BASE_TOKEN_QUERIES + 7,
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
                num_queries=self.BASE_SESSION_QUERIES + 7,
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
            frank_url, [self.editor], results=[self.frank], num_queries=self.BASE_SESSION_QUERIES + 7
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
    def test_as_agent(self, mr_mocks):
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

    def test_prevent_null_chars(self):
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
    def test_update_datetime_field(self, mr_mocks):
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
    def test_anonymous_org(self, mr_mocks):
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
