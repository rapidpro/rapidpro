from django.db import connection

from temba.api.models import APIToken


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
                token = APIToken.create(self.org, user)
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
                self.assertEqual(200, response.status_code, f"status code mismatch for user {user}")

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
