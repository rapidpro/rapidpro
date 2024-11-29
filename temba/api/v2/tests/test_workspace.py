from django.urls import reverse

from . import APITest


class WorkspaceEndpointTest(APITest):
    def test_endpoint(self):
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
