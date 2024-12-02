from django.urls import reverse

from temba.api.models import Resthook, WebHookEvent
from temba.api.v2.serializers import format_datetime

from . import APITest


class ResthooksEndpointTest(APITest):
    def test_endpoint(self):
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
            num_queries=self.BASE_SESSION_QUERIES + 1,
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
            num_queries=self.BASE_SESSION_QUERIES + 1,
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
            num_queries=self.BASE_SESSION_QUERIES + 1,
        )
