from django.test import override_settings
from django.urls import reverse

from temba.api.v2.serializers import format_datetime
from temba.globals.models import Global

from . import APITest


class GlobalsEndpointTest(APITest):
    @override_settings(ORG_LIMIT_DEFAULTS={"globals": 3})
    def test_endpoint(self):
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
            num_queries=self.BASE_SESSION_QUERIES + 1,
        )

        # check no filtering with token auth
        response = self.assertGet(
            endpoint_url,
            [self.editor, self.admin],
            results=[global2, global1],
            by_token=True,
            num_queries=self.BASE_TOKEN_QUERIES + 1,
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
