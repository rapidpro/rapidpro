import random
from types import SimpleNamespace
from unittest import mock
from unittest.mock import MagicMock

from django.utils import timezone

import temba.utils.analytics
from temba.tests import TembaTest


class CrispTest(TembaTest):
    def setUp(self):
        super().setUp()

        random.seed(1)

        # create org and user stubs
        self.org = SimpleNamespace(
            id=1000, name="Some Org", brand="Some Brand", created_on=timezone.now(), account_value=lambda: 1000
        )

        self.admin = MagicMock()
        self.admin.username = "admin@example.com"
        self.admin.first_name = ""
        self.admin.last_name = ""
        self.admin.email = "admin@example.com"
        self.admin.is_authenticated = True

        self.crisp_mock = MagicMock()
        temba.utils.analytics._crisp = self.crisp_mock

        temba.utils.analytics.init()

    def test_identify(self):
        self.crisp_mock.website.get_people_profile.side_effect = Exception("No Profile")
        temba.utils.analytics.identify(self.admin, {"slug": "test", "host": "rapidpro.io"}, self.org)

        # did we actually call save?
        self.crisp_mock.website.add_new_people_profile.assert_called_with(
            self.crisp_mock.website_id,
            {
                "person": {"nickname": " "},
                "company": {
                    "name": "Some Org",
                    "url": "https://rapidpro.io/org/update/1000/",
                    "domain": "rapidpro.io/org/update/1000",
                },
                "segments": ["test", "random-3"],
                "email": "admin@example.com",
            },
        )

        # now identify when there is an existing profile
        self.crisp_mock = MagicMock()
        temba.utils.analytics._crisp = self.crisp_mock
        temba.utils.analytics.identify(self.admin, {"slug": "test", "host": "rapidpro.io"}, self.org)

        self.crisp_mock.website.update_people_profile.assert_called_with(
            self.crisp_mock.website_id,
            self.admin.email,
            {
                "person": {"nickname": " "},
                "company": {
                    "name": "Some Org",
                    "url": "https://rapidpro.io/org/update/1000/",
                    "domain": "rapidpro.io/org/update/1000",
                },
                "segments": mock.ANY,
            },
        )

    def test_track(self):
        temba.utils.analytics.track(self.admin, "temba.flow_created", properties={"name": "My Flow"})

        self.crisp_mock.website.add_people_event.assert_called_with(
            self.crisp_mock.website_id,
            self.admin.username,
            {"color": "blue", "text": "temba.flow_created", "data": {"name": "My Flow"}},
        )

        # different events get different colors in crisp
        temba.utils.analytics.track(self.admin, "temba.user_signup")
        self.crisp_mock.website.add_people_event.assert_called_with(
            self.crisp_mock.website_id,
            self.admin.username,
            {"color": "green", "text": "temba.user_signup", "data": {}},
        )

        # test None is removed
        temba.utils.analytics.track(
            self.admin,
            "temba.flow_broadcast",
            dict(contacts=1, groups=0, query=None),
        )

        self.crisp_mock.website.add_people_event.assert_called_with(
            self.crisp_mock.website_id,
            self.admin.username,
            {"color": "grey", "text": "temba.flow_broadcast", "data": {"contacts": 1, "groups": 0}},
        )

    def test_consent_valid_user(self):
        # valid user which did not consent
        self.crisp_mock.website.get_people_profile.return_value = {"segments": []}

        temba.utils.analytics.change_consent(self.admin, consent=True)

        self.crisp_mock.website.update_people_profile.assert_called_with(
            self.crisp_mock.website_id,
            "admin@example.com",
            {"segments": ["consented"]},
        )

    def test_consent_valid_user_decline(self):
        # valid user which did not consent
        self.crisp_mock.website.get_people_profile.return_value = {"segments": ["random-3", "consented"]}
        self.crisp_mock.website.get_people_data.return_value = {"data": {}}

        temba.utils.analytics.change_consent(self.admin, consent=False)

        self.crisp_mock.website.save_people_data.assert_called_with(
            self.crisp_mock.website_id, self.admin.email, {"data": {"consent_changed": mock.ANY}}
        )
