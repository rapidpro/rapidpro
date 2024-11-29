from django.urls import reverse

from temba.api.v2.serializers import format_datetime
from temba.msgs.models import OptIn
from temba.tests import matchers

from . import APITest


class OptInEndpointTest(APITest):
    def test_endpoint(self):
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
            num_queries=self.BASE_SESSION_QUERIES + 1,
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
