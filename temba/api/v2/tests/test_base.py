import base64
import time
from collections import OrderedDict
from datetime import datetime, timezone as tzone
from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from temba.api.models import APIToken
from temba.api.v2.serializers import normalize_extra
from temba.contacts.models import Contact
from temba.flows.models import FlowRun
from temba.orgs.models import OrgRole

from . import APITest


class EndpointsTest(APITest):
    def setUp(self):
        super().setUp()

        self.joe = self.create_contact("Joe Blow", phone="+250788123123")
        self.frank = self.create_contact("Frank", urns=["facebook:123456"])

        self.facebook_channel = self.create_channel("FBA", "Facebook Channel", "billy_bob")

        self.hans = self.create_contact("Hans Gruber", phone="+4921551511", org=self.org2)

        self.org2channel = self.create_channel("A", "Org2Channel", "123456", country="RW", org=self.org2)

    @override_settings(REST_HANDLE_EXCEPTIONS=True)
    @patch("temba.api.v2.views.FieldsEndpoint.get_queryset")
    def test_error_handling(self, mock_get_queryset):
        mock_get_queryset.side_effect = ValueError("DOH!")

        self.login(self.admin)

        response = self.client.get(
            reverse("api.v2.fields") + ".json", content_type="application/json", HTTP_X_FORWARDED_HTTPS="https"
        )
        self.assertContains(response, "Server Error. Site administrators have been notified.", status_code=500)

    @override_settings(FLOW_START_PARAMS_SIZE=4)
    def test_normalize_extra(self):
        self.assertEqual(OrderedDict(), normalize_extra({}))
        self.assertEqual(
            OrderedDict([("0", "a"), ("1", True), ("2", Decimal("1.0")), ("3", "")]),
            normalize_extra(["a", True, Decimal("1.0"), None]),
        )
        self.assertEqual(OrderedDict([("_3__x", "z")]), normalize_extra({"%3 !x": "z"}))
        self.assertEqual(
            OrderedDict([("0", "a"), ("1", "b"), ("2", "c"), ("3", "d")]), normalize_extra(["a", "b", "c", "d", "e"])
        )
        self.assertEqual(
            OrderedDict([("a", 1), ("b", 2), ("c", 3), ("d", 4)]),
            normalize_extra({"a": 1, "b": 2, "c": 3, "d": 4, "e": 5}),
        )
        self.assertEqual(OrderedDict([("a", "x" * 640)]), normalize_extra({"a": "x" * 641}))

    def test_authentication(self):
        def _request(endpoint, post_data, **kwargs):
            if post_data:
                return self.client.post(endpoint, post_data, content_type="application/json", secure=True, **kwargs)
            else:
                return self.client.get(endpoint, secure=True, **kwargs)

        def request_by_token(endpoint, token, post_data=None):
            return _request(endpoint, post_data, HTTP_AUTHORIZATION=f"Token {token}")

        def request_by_basic_auth(endpoint, username, password, post_data=None):
            credentials_base64 = base64.b64encode(f"{username}:{password}".encode()).decode()
            return _request(endpoint, post_data, HTTP_AUTHORIZATION=f"Basic {credentials_base64}")

        def request_by_session(endpoint, user, post_data=None):
            self.login(user, choose_org=self.org)
            resp = _request(endpoint, post_data)
            self.client.logout()
            return resp

        contacts_url = reverse("api.v2.contacts") + ".json"
        campaigns_url = reverse("api.v2.campaigns") + ".json"
        fields_url = reverse("api.v2.fields") + ".json"

        token1 = APIToken.create(self.org, self.admin)
        token2 = APIToken.create(self.org, self.editor)

        # can GET fields endpoint using all 3 tokens
        response = request_by_token(fields_url, token1.key)
        self.assertEqual(200, response.status_code)
        response = request_by_token(fields_url, token2.key)
        self.assertEqual(200, response.status_code)

        # can POST with all tokens
        response = request_by_token(fields_url, token1.key, {"name": "Field 1", "type": "text"})
        self.assertEqual(201, response.status_code)
        response = request_by_token(fields_url, token2.key, {"name": "Field 2", "type": "text"})
        self.assertEqual(201, response.status_code)

        response = request_by_basic_auth(fields_url, self.admin.username, token1.key)
        self.assertEqual(200, response.status_code)

        # can GET using session auth for admins, editors and servicing staff
        response = request_by_session(fields_url, self.admin)
        self.assertEqual(200, response.status_code)
        response = request_by_session(fields_url, self.editor)
        self.assertEqual(200, response.status_code)
        response = request_by_session(fields_url, self.customer_support)
        self.assertEqual(200, response.status_code)

        # can POST using session auth for admins and editors
        response = request_by_session(fields_url, self.admin, {"name": "Field 4", "type": "text"})
        self.assertEqual(201, response.status_code)
        response = request_by_session(fields_url, self.editor, {"name": "Field 5", "type": "text"})
        self.assertEqual(201, response.status_code)
        response = request_by_session(fields_url, self.customer_support, {"name": "Field 6", "type": "text"})
        self.assertEqual(403, response.status_code)

        # if a staff user is actually a member of the org, they can POST
        self.org.add_user(self.customer_support, OrgRole.ADMINISTRATOR)
        response = request_by_session(fields_url, self.customer_support, {"name": "Field 6", "type": "text"})
        self.assertEqual(201, response.status_code)

        # can't fetch endpoint with invalid token
        response = request_by_token(contacts_url, "1234567890")
        self.assertResponseError(response, None, "Invalid token", status_code=403)

        # can't fetch endpoint with invalid token
        response = request_by_basic_auth(contacts_url, self.admin.username, "1234567890")
        self.assertResponseError(response, None, "Invalid token or email", status_code=403)

        # can't fetch endpoint with invalid username
        response = request_by_basic_auth(contacts_url, "some@name.com", token1.key)
        self.assertResponseError(response, None, "Invalid token or email", status_code=403)

        # can fetch campaigns endpoint with valid admin token
        response = request_by_token(campaigns_url, token1.key)
        self.assertEqual(200, response.status_code)
        self.assertEqual(str(self.org.id), response["X-Temba-Org"])

        response = request_by_basic_auth(contacts_url, self.editor.username, token2.key)
        self.assertEqual(200, response.status_code)
        self.assertEqual(str(self.org.id), response["X-Temba-Org"])

        # simulate the admin user exceeding the rate limit for the v2 scope
        cache.set(f"throttle_v2_{self.org.id}", [time.time() for r in range(10000)])

        # next request they make using a token will be rejected
        response = request_by_token(fields_url, token1.key)
        self.assertEqual(response.status_code, 429)

        # same with basic auth
        response = request_by_basic_auth(fields_url, self.admin.username, token1.key)
        self.assertEqual(response.status_code, 429)

        # or if another user in same org makes a request
        response = request_by_token(fields_url, token2.key)
        self.assertEqual(response.status_code, 429)

        # but they can still make a request if they have a session
        response = request_by_session(fields_url, self.admin)
        self.assertEqual(response.status_code, 200)

        # are allowed to access if we have not reached the configured org api rates
        self.org.api_rates = {"v2": "15000/hour"}
        self.org.save(update_fields=("api_rates",))

        response = request_by_basic_auth(fields_url, self.admin.username, token1.key)
        self.assertEqual(response.status_code, 200)

        cache.set(f"throttle_v2_{self.org.id}", [time.time() for r in range(15000)])

        # next request they make using a token will be rejected
        response = request_by_token(fields_url, token1.key)
        self.assertEqual(response.status_code, 429)

        # if user is demoted to a role that can't use tokens, tokens shouldn't work for them
        self.org.add_user(self.admin, OrgRole.VIEWER)

        self.assertEqual(request_by_token(campaigns_url, token1.key).status_code, 403)
        self.assertEqual(request_by_basic_auth(campaigns_url, self.admin.username, token1.key).status_code, 403)

        # and if user is inactive, disallow the request
        self.org.add_user(self.admin, OrgRole.ADMINISTRATOR)
        self.admin.is_active = False
        self.admin.save()

        response = request_by_token(contacts_url, token1.key)
        self.assertResponseError(response, None, "Invalid token", status_code=403)

        response = request_by_basic_auth(contacts_url, self.admin.username, token1.key)
        self.assertResponseError(response, None, "Invalid token or email", status_code=403)

    @override_settings(SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_HTTPS", "https"))
    def test_root(self):
        root_url = reverse("api.v2.root")

        # browse as HTML anonymously (should still show docs)
        response = self.client.get(root_url)
        self.assertContains(response, "We provide a RESTful JSON API")

        # POSTing just returns the docs with a 405
        response = self.client.post(root_url, {})
        self.assertContains(response, "We provide a RESTful JSON API", status_code=405)

        # same thing if user navigates to just /api
        response = self.client.get(reverse("api"), follow=True)
        self.assertContains(response, "We provide a RESTful JSON API")

        # try to browse as JSON anonymously
        response = self.client.get(root_url + ".json")
        self.assertEqual(200, response.status_code)
        self.assertIsInstance(response.json(), dict)
        self.assertEqual(response.json()["runs"], "http://testserver/api/v2/runs")  # endpoints are listed

    def test_docs(self):
        messages_url = reverse("api.v2.messages")

        # test fetching docs anonymously
        response = self.client.get(messages_url)
        self.assertContains(response, "This endpoint allows you to list messages in your account.")

        # you can also post to docs endpoints tho it just returns the docs with a 403
        response = self.client.post(messages_url, {})
        self.assertContains(response, "This endpoint allows you to list messages in your account.", status_code=403)

        # test fetching docs logged in
        self.login(self.editor)
        response = self.client.get(messages_url)
        self.assertContains(response, "This endpoint allows you to list messages in your account.")

    def test_explorer(self):
        explorer_url = reverse("api.v2.explorer")

        response = self.client.get(explorer_url)
        self.assertLoginRedirect(response)

        # viewers can't access
        self.login(self.user)
        response = self.client.get(explorer_url)
        self.assertLoginRedirect(response)

        # editors and administrators can
        self.login(self.editor)
        response = self.client.get(explorer_url)
        self.assertEqual(200, response.status_code)

        self.login(self.admin)

        response = self.client.get(explorer_url)
        self.assertContains(response, "All operations work against real data in the <b>Nyaruka</b> workspace.")

    def test_pagination(self):
        endpoint_url = reverse("api.v2.runs") + ".json"
        self.login(self.admin)

        # create 1255 test runs (5 full pages of 250 items + 1 partial with 5 items)
        flow = self.create_flow("Test")
        runs = []
        for r in range(1255):
            runs.append(FlowRun(org=self.org, flow=flow, contact=self.joe, status="C", exited_on=timezone.now()))
        FlowRun.objects.bulk_create(runs)
        actual_ids = list(FlowRun.objects.order_by("-pk").values_list("pk", flat=True))

        # give them all the same modified_on
        FlowRun.objects.all().update(modified_on=datetime(2015, 9, 15, 0, 0, 0, 0, tzone.utc))

        returned_ids = []

        # fetch all full pages
        with self.mockReadOnly():
            resp_json = None
            for p in range(5):
                response = self.client.get(
                    endpoint_url if p == 0 else resp_json["next"], content_type="application/json"
                )
                self.assertEqual(200, response.status_code)

                resp_json = response.json()

                self.assertEqual(len(resp_json["results"]), 250)
                self.assertIsNotNone(resp_json["next"])

                returned_ids += [r["id"] for r in response.json()["results"]]

        # fetch final partial page
        with self.mockReadOnly():
            response = self.client.get(resp_json["next"], content_type="application/json")

        resp_json = response.json()
        self.assertEqual(len(resp_json["results"]), 5)
        self.assertIsNone(resp_json["next"])

        returned_ids += [r["id"] for r in response.json()["results"]]

        self.assertEqual(returned_ids, actual_ids)  # ensure all results were returned and in correct order

    @patch("temba.flows.models.FlowStart.create")
    def test_transactions(self, mock_flowstart_create):
        """
        Serializer writes are wrapped in a transaction. This test simulates FlowStart.create blowing up and checks that
        contacts aren't created.
        """
        mock_flowstart_create.side_effect = ValueError("DOH!")

        flow = self.create_flow("Test")

        try:
            self.assertPost(
                reverse("api.v2.flow_starts") + ".json",
                self.admin,
                {"flow": str(flow.uuid), "urns": ["tel:+12067791212"]},
                status=201,
            )
            self.fail()  # ensure exception is thrown
        except ValueError:
            pass

        self.assertFalse(Contact.objects.filter(urns__path="+12067791212"))
