import random
from unittest import mock
from unittest.mock import MagicMock

from django.test.utils import override_settings

from temba.tests import TembaTest

from .backend import CrispBackend


@override_settings(CRISP_IDENTIFIER="CI123", CRISP_KEY="CK234", CRISP_WEBSITE_ID="CW345")
class CrispTest(TembaTest):
    def setUp(self):
        super().setUp()

        random.seed(1)

        self.mock_website = MagicMock()

        self.backend = CrispBackend()
        self.backend.client.website = self.mock_website

    def test_gauge(self):
        self.backend.gauge("temba.foo_level", 12)  # noop

    def test_track(self):
        # signup events in green and None properties not sent
        self.backend.track(self.user, "user_signup", {"user_id": "123", "nick_name": None})

        self.mock_website.add_people_event.assert_called_with(
            "CW345", "User@nyaruka.com", {"color": "green", "text": "user_signup", "data": {"user_id": "123"}}
        )

        # created events in blue...
        self.backend.track(self.admin, "foo_created", {"foo_id": "234"})

        self.mock_website.add_people_event.assert_called_with(
            "CW345", "Administrator@nyaruka.com", {"color": "blue", "text": "foo_created", "data": {"foo_id": "234"}}
        )

        # export events in purple...
        self.backend.track(self.admin, "foo_export", {"foo_id": "345"})

        self.mock_website.add_people_event.assert_called_with(
            "CW345",
            "Administrator@nyaruka.com",
            {"color": "purple", "text": "foo_export", "data": {"foo_id": "345"}},
        )

    def test_identify(self):
        self.mock_website.get_people_profile.side_effect = Exception("No Profile")
        self.mock_website.add_new_people_profile.return_value = {"people_id": 1234}

        self.backend.identify(self.admin, {"slug": "test", "host": "rapidpro.io"}, self.org)

        # did we actually call save?
        self.mock_website.add_new_people_profile.assert_called_with(
            "CW345",
            {
                "person": {"nickname": " "},
                "company": {
                    "name": "Temba",
                    "url": f"https://rapidpro.io/org/update/{self.org.id}/",
                    "domain": f"rapidpro.io/org/update/{self.org.id}",
                },
                "segments": ["test", "random-3"],
                "email": "Administrator@nyaruka.com",
            },
        )

        # now identify when there is an existing profile
        self.mock_website.get_people_profile.side_effect = None
        self.mock_website.get_people_profile.return_value = {"people_id": 2345, "segments": []}

        self.backend.identify(self.admin, {"slug": "test", "host": "rapidpro.io"}, self.org)

        self.mock_website.update_people_profile.assert_called_with(
            "CW345",
            self.admin.email,
            {
                "person": {"nickname": " "},
                "company": {
                    "name": "Temba",
                    "url": f"https://rapidpro.io/org/update/{self.org.id}/",
                    "domain": f"rapidpro.io/org/update/{self.org.id}",
                },
                "segments": mock.ANY,
            },
        )

    def test_change_consent(self):
        # valid user which did not consent
        self.mock_website.get_people_profile.return_value = {"segments": []}

        self.backend.change_consent(self.admin, True)

        self.mock_website.update_people_profile.assert_called_with(
            "CW345", "Administrator@nyaruka.com", {"segments": ["consented"]}
        )

        # valid user which did not consent
        self.mock_website.get_people_profile.return_value = {"segments": ["random-3", "consented"]}
        self.mock_website.get_people_data.return_value = {"data": {}}

        self.backend.change_consent(self.admin, False)

        self.mock_website.save_people_data.assert_called_with(
            "CW345", self.admin.email, {"data": {"consent_changed": mock.ANY}}
        )

    def test_get_hook_template(self):
        self.assertEqual("utils/analytics/crisp/login.html", self.backend.get_hook_template("login"))

        request = MagicMock()
        request.user = self.admin

        self.assertEqual({"crisp_website_id": "CW345", "crisp_token_id": None}, self.backend.get_hook_context(request))

        user_settings = self.admin.get_settings()
        user_settings.external_id = "AD567"
        user_settings.save()

        self.assertEqual(
            {"crisp_website_id": "CW345", "crisp_token_id": "AD567"}, self.backend.get_hook_context(request)
        )
