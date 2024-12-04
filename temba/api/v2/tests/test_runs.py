import iso8601

from django.urls import reverse

from temba.api.v2.serializers import format_datetime
from temba.flows.models import FlowStart
from temba.tests.engine import MockSessionWriter

from . import APITest


class RunsEndpointTest(APITest):
    def test_endpoint(self):
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

        joe = self.create_contact("Joe Blow", phone="+250788123123")
        frank = self.create_contact("Frank", urns=["tel:123456"])
        start1 = FlowStart.create(flow1, self.admin, contacts=[joe])
        joe_msg = self.create_incoming_msg(joe, "it is blue")
        frank_msg = self.create_incoming_msg(frank, "Indigo")

        joe_run1 = (
            MockSessionWriter(joe, flow1, start=start1)
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
            MockSessionWriter(frank, flow1)
            .visit(color_prompt)
            .visit(color_split)
            .wait()
            .resume(msg=frank_msg)
            .set_result("Color", "Indigo", "Other", "Indigo")
            .wait()
            .save()
        ).session.runs.get()

        joe_run2 = (
            MockSessionWriter(joe, flow1).visit(color_prompt).visit(color_split).wait().save()
        ).session.runs.get()
        frank_run2 = (
            MockSessionWriter(frank, flow1).visit(color_prompt).visit(color_split).wait().save()
        ).session.runs.get()

        joe_run3 = MockSessionWriter(joe, flow2).wait().save().session.runs.get()

        # add a run for another org
        flow3 = self.create_flow("Test", org=self.org2)
        hans = self.create_contact("Hans Gruber", phone="+4921551511", org=self.org2)
        MockSessionWriter(hans, flow3).wait().save()

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
            num_queries=self.BASE_SESSION_QUERIES + 6,
        )
        resp_json = response.json()
        self.assertEqual(
            {
                "id": frank_run2.id,
                "uuid": str(frank_run2.uuid),
                "flow": {"uuid": str(flow1.uuid), "name": "Colors"},
                "contact": {
                    "uuid": str(frank.uuid),
                    "name": frank.name,
                    "urn": "tel:123456",
                    "urn_display": "123456",
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
                    "uuid": str(joe.uuid),
                    "name": joe.name,
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
                    "uuid": str(frank.uuid),
                    "name": frank.name,
                    "urn": "tel:123456",
                    "urn_display": "123456",
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
                        "uuid": frank.uuid,
                        "name": frank.name,
                        "urn": "tel:********",
                        "urn_display": None,
                        "anon_display": f"{frank.id:010}",
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
        self.assertGet(endpoint_url + f"?contact={joe.uuid}", [self.admin], results=[joe_run3, joe_run2, joe_run1])

        # filter by invalid contact
        self.assertGet(endpoint_url + "?contact=invalid", [self.admin], results=[])

        # filter by contact + responded
        self.assertGet(endpoint_url + f"?contact={joe.uuid}&responded=yes", [self.admin], results=[joe_run1])

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
            endpoint_url + f"?contact={joe.uuid}&flow={flow1.uuid}",
            [self.admin],
            errors={None: "You may only specify one of the contact, flow parameters"},
        )
