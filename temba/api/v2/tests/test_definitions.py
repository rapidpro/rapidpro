from django.urls import reverse

from temba.campaigns.models import Campaign
from temba.flows.models import Flow

from . import APITest


class DefinitionsEndpointTest(APITest):
    def test_endpoint(self):
        endpoint_url = reverse("api.v2.definitions") + ".json"

        self.assertGetNotPermitted(endpoint_url, [None, self.agent])
        self.assertPostNotAllowed(endpoint_url)
        self.assertDeleteNotAllowed(endpoint_url)

        self.import_file("test_flows/subflow.json")
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
        self.import_file("test_flows/the_clinic.json")

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
        self.import_file("test_flows/favorites_v13.json")

        flow = Flow.objects.get(name="Favorites")
        self.assertGet(
            endpoint_url + f"?flow={flow.uuid}",
            [self.editor],
            raw=lambda j: len(j["flows"]) == 1 and j["flows"][0]["spec_version"] == Flow.CURRENT_SPEC_VERSION,
        )

        # test fetching docs anonymously
        self.client.logout()
        response = self.client.get(reverse("api.v2.definitions"))
        self.assertContains(response, "Deprecated endpoint")
