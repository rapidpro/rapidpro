from datetime import timedelta

from django.contrib.auth.models import Group
from django.db import connection
from django.test import override_settings
from django.utils import timezone

from temba.api.models import APIToken, Resthook, WebHookEvent
from temba.api.tasks import trim_webhook_events
from temba.orgs.models import OrgRole
from temba.tests import TembaTest


class APITokenTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.admins_group = Group.objects.get(name="Administrators")
        self.editors_group = Group.objects.get(name="Editors")

        self.org2.add_user(self.admin, OrgRole.EDITOR)  # our admin can act as editor for other org

    def test_get_or_create(self):
        token1 = APIToken.get_or_create(self.org, self.admin)
        self.assertEqual(self.org, token1.org)
        self.assertEqual(self.admin, token1.user)
        self.assertEqual(self.admins_group, token1.role)
        self.assertTrue(token1.key)
        self.assertEqual(str(token1), token1.key)

        # tokens for different roles with same user should differ
        token2 = APIToken.get_or_create(self.org, self.admin, role=OrgRole.ADMINISTRATOR)
        token3 = APIToken.get_or_create(self.org, self.admin, role=OrgRole.EDITOR)

        self.assertEqual(token1, token2)
        self.assertNotEqual(token1, token3)
        self.assertNotEqual(token1.key, token3.key)

        self.assertEqual(self.editors_group, token3.role)

        # tokens with same role for different users should differ
        token5 = APIToken.get_or_create(self.org, self.editor)

        self.assertNotEqual(token3, token5)

        # can't create token for viewer users or other users using viewers role
        self.assertRaises(ValueError, APIToken.get_or_create, self.org, self.admin, role=OrgRole.VIEWER)
        self.assertRaises(ValueError, APIToken.get_or_create, self.org, self.user)

    def test_is_valid(self):
        token1 = APIToken.get_or_create(self.org, self.admin, role=OrgRole.ADMINISTRATOR)
        token2 = APIToken.get_or_create(self.org, self.admin, role=OrgRole.EDITOR)

        # demote admin to an editor
        self.org.add_user(self.admin, OrgRole.EDITOR)
        self.admin.refresh_from_db()

        self.assertFalse(token1.is_valid())
        self.assertTrue(token2.is_valid())

    def test_get_default_role(self):
        self.assertEqual(APIToken.get_default_role(self.org, self.admin), OrgRole.ADMINISTRATOR)
        self.assertEqual(APIToken.get_default_role(self.org, self.editor), OrgRole.EDITOR)
        self.assertIsNone(APIToken.get_default_role(self.org, self.user))

        # user from another org has no API roles in this org
        self.assertIsNone(APIToken.get_default_role(self.org, self.admin2))


class WebHookTest(TembaTest):
    def test_trim_events_and_results(self):
        five_hours_ago = timezone.now() - timedelta(hours=5)

        # create some events
        resthook = Resthook.get_or_create(org=self.org, slug="registration", user=self.admin)
        WebHookEvent.objects.create(org=self.org, resthook=resthook, data={}, created_on=five_hours_ago)

        with override_settings(RETENTION_PERIODS={"webhookevent": None}):
            trim_webhook_events()
            self.assertTrue(WebHookEvent.objects.all())

        with override_settings(RETENTION_PERIODS={"webhookevent": timedelta(hours=12)}):  # older than our event
            trim_webhook_events()
            self.assertTrue(WebHookEvent.objects.all())

        with override_settings(RETENTION_PERIODS={"webhookevent": timedelta(hours=2)}):
            trim_webhook_events()
            self.assertFalse(WebHookEvent.objects.all())


class APITestMixin:
    def setUp(self):
        super().setUp()

        # this is needed to prevent REST framework from rolling back transaction created around each unit test
        connection.settings_dict["ATOMIC_REQUESTS"] = False

    def tearDown(self):
        super().tearDown()

        connection.settings_dict["ATOMIC_REQUESTS"] = True

    def _getJSON(self, endpoint_url: str, user, *, by_token: bool = False, num_queries: int = None):
        self.client.logout()

        kwargs = {"HTTP_X_FORWARDED_HTTPS": "https"}
        if user:
            if by_token:
                token = APIToken.get_or_create(self.org, user)
                kwargs["HTTP_AUTHORIZATION"] = f"Token {token.key}"
            else:
                self.login(user)

        with self.mockReadOnly():
            if num_queries:
                with self.assertNumQueries(num_queries):
                    response = self.client.get(endpoint_url, content_type="application/json", **kwargs)
            else:
                response = self.client.get(endpoint_url, content_type="application/json", **kwargs)

        response.json()  # this will fail if our response isn't valid json

        return response

    def _deleteJSON(self, endpoint_url: str, user):
        self.client.logout()
        if user:
            self.login(user)

        return self.client.delete(endpoint_url, content_type="application/json", HTTP_X_FORWARDED_HTTPS="https")

    def _postJSON(self, endpoint_url: str, user, data: dict, **kwargs):
        self.client.logout()
        if user:
            self.login(user)

        return self.client.post(
            endpoint_url, data, content_type="application/json", HTTP_X_FORWARDED_HTTPS="https", **kwargs
        )

    def assertGetNotAllowed(self, endpoint_url: str):
        response = self._getJSON(endpoint_url, self.admin)
        self.assertEqual(405, response.status_code)

    def assertGetNotPermitted(self, endpoint_url: str, users: list):
        for user in users:
            response = self._getJSON(endpoint_url, user)
            self.assertEqual(403, response.status_code, f"status code mismatch for {user}")

    def assertGet(
        self,
        endpoint_url: str,
        users: list,
        *,
        results: list = None,
        errors: dict = None,
        raw=None,
        by_token: bool = False,
        num_queries: int = None,
    ):
        assert (results is not None) ^ (errors is not None) ^ (raw is not None)

        matchers = (
            ("uuid", lambda o: str(o.uuid)),
            ("id", lambda o: o.id),
            ("key", lambda o: o.key),
            ("email", lambda o: o.email),
            ("hash", lambda o: o.hash),
        )

        def as_user(user, expected_results: list, expected_queries: int = None):
            response = self._getJSON(endpoint_url, user, by_token=by_token, num_queries=expected_queries)

            if results is not None:
                self.assertEqual(200, response.status_code)

                actual_results = response.json()["results"]
                full_check = expected_results and isinstance(expected_results[0], dict)

                if results and not full_check:
                    for id_key, id_fn in matchers:
                        if id_key in actual_results[0]:
                            actual_ids = [r[id_key] for r in actual_results]
                            expected_ids = [id_fn(o) for o in expected_results]
                            break
                    else:
                        self.fail("results contain no matchable values")

                    self.assertEqual(expected_ids, actual_ids)
                else:
                    self.assertEqual(expected_results, actual_results)
            elif errors is not None:
                for field, msg in errors.items():
                    self.assertResponseError(response, field, msg, status_code=400)
            elif callable(raw):
                self.assertTrue(raw(response.json()))
            else:
                self.assertEqual(raw, response.json())

            return response

        for user in users:
            response = as_user(user, results, num_queries)

        return response

    def assertPostNotAllowed(self, endpoint_url: str):
        response = self._postJSON(endpoint_url, self.admin, {})
        self.assertEqual(405, response.status_code)

    def assertPostNotPermitted(self, endpoint_url: str, users: list):
        for user in users:
            response = self._postJSON(endpoint_url, user, {})
            self.assertEqual(403, response.status_code, f"status code mismatch for user {user}")

    def assertPost(self, endpoint_url: str, user, data: dict, *, errors: dict = None, status=None, **kwargs):
        response = self._postJSON(endpoint_url, user, data, **kwargs)
        if errors:
            for field, msg in errors.items():
                self.assertResponseError(response, field, msg, status_code=status or 400)
        else:
            self.assertEqual(status or 200, response.status_code)
        return response

    def assertDeleteNotAllowed(self, endpoint_url: str):
        response = self._deleteJSON(endpoint_url, self.admin)
        self.assertEqual(405, response.status_code)

    def assertDeleteNotPermitted(self, endpoint_url: str, users: list):
        for user in users:
            response = self._deleteJSON(endpoint_url, user)
            self.assertEqual(403, response.status_code, f"status code mismatch for user {user}")

    def assertDelete(self, endpoint_url: str, user, *, errors: dict = None, status=None):
        response = self._deleteJSON(endpoint_url, user)
        if errors:
            for field, msg in errors.items():
                self.assertResponseError(response, field, msg, status_code=status or 400)
        else:
            self.assertEqual(status or 204, response.status_code)
        return response

    def assertResponseError(self, response, field, expected_message: str, status_code=400):
        self.assertEqual(response.status_code, status_code)
        resp_json = response.json()
        if field:
            if isinstance(field, tuple):
                field, sub_field = field
            else:
                sub_field = None

            self.assertIn(field, resp_json)

            if sub_field:
                self.assertIsInstance(resp_json[field], dict)
                self.assertIn(sub_field, resp_json[field])
                self.assertIn(expected_message, resp_json[field][sub_field])
            else:
                self.assertIsInstance(resp_json[field], list)
                self.assertIn(expected_message, resp_json[field])
        else:
            self.assertIsInstance(resp_json, dict)
            self.assertIn("detail", resp_json)
            self.assertEqual(resp_json["detail"], expected_message)
