import io

from django.urls import reverse

from temba.flows.models import FlowSession
from temba.tests import TembaTest
from temba.tests.engine import MockSessionWriter
from temba.utils import json, s3


class FlowSessionCRUDLTest(TembaTest):
    def test_session_json(self):
        contact = self.create_contact("Bob", phone="+1234567890")
        flow = self.get_flow("color_v13")

        session = MockSessionWriter(contact, flow).wait().save().session

        # normal users can't see session json
        json_url = reverse("flows.flowsession_json", args=[session.uuid])
        response = self.client.get(json_url)
        self.assertLoginRedirect(response)

        self.login(self.admin)
        response = self.client.get(json_url)
        self.assertLoginRedirect(response)

        # but logged in as a CS rep we can
        self.login(self.customer_support, choose_org=self.org)

        response = self.client.get(json_url)
        self.assertEqual(200, response.status_code)

        response_json = json.loads(response.content)
        self.assertEqual("Nyaruka", response_json["_metadata"]["org"])
        self.assertEqual(session.uuid, response_json["uuid"])

        # now try with an s3 session
        s3.client().put_object(
            Bucket="test-sessions", Key="c/session.json", Body=io.BytesIO(json.dumps(session.output).encode())
        )
        FlowSession.objects.filter(id=session.id).update(
            output_url="http://minio:9000/test-sessions/c/session.json", output=None
        )

        # fetch our contact history
        response = self.client.get(json_url)
        self.assertEqual(200, response.status_code)
        self.assertEqual("Nyaruka", response_json["_metadata"]["org"])
        self.assertEqual(session.uuid, response_json["uuid"])
