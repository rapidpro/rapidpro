from django.urls import reverse
from django.utils import timezone

from temba.api.v2.serializers import format_datetime
from temba.classifiers.models import Classifier
from temba.classifiers.types.luis import LuisType
from temba.classifiers.types.wit import WitType

from . import APITest


class ClassifiersEndpointTest(APITest):
    def test_endpoint(self):
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
            num_queries=self.BASE_SESSION_QUERIES + 2,
        )

        # filter by uuid (not there)
        self.assertGet(endpoint_url + "?uuid=09d23a05-47fe-11e4-bfe9-b8f6b119e9ab", [self.editor], results=[])

        # filter by uuid present
        self.assertGet(endpoint_url + f"?uuid={c1.uuid}", [self.user, self.editor, self.admin], results=[c1])
