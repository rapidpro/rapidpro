from django.test import override_settings
from django.urls import reverse

from temba.msgs.models import Label

from . import APITest


class LabelsEndpointTest(APITest):
    def test_endpoint(self):
        endpoint_url = reverse("api.v2.labels") + ".json"

        self.assertGetNotPermitted(endpoint_url, [None, self.agent])
        self.assertPostNotPermitted(endpoint_url, [None, self.user, self.agent])
        self.assertDeleteNotPermitted(endpoint_url + "?uuid=123", [None, self.user, self.agent])

        frank = self.create_contact("Frank", urns=["tel:123456"])
        important = self.create_label("Important")
        feedback = self.create_label("Feedback")

        # a deleted label
        deleted = self.create_label("Deleted")
        deleted.release(self.admin)

        # create label for other org
        spam = self.create_label("Spam", org=self.org2)

        msg = self.create_incoming_msg(frank, "Hello")
        important.toggle_label([msg], add=True)

        # no filtering
        self.assertGet(
            endpoint_url,
            [self.user, self.editor],
            results=[
                {"uuid": str(feedback.uuid), "name": "Feedback", "count": 0},
                {"uuid": str(important.uuid), "name": "Important", "count": 1},
            ],
            num_queries=self.BASE_SESSION_QUERIES + 2,
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
