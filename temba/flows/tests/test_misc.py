from django.test.utils import override_settings

from temba.flows.checks import mailroom_url
from temba.tests import TembaTest


class AssetServerTest(TembaTest):
    def test_languages(self):
        self.login(self.admin)
        response = self.client.get("/flow/assets/%d/1234/language/" % self.org.id)
        self.assertEqual(
            response.json(), {"results": [{"iso": "eng", "name": "English"}, {"iso": "kin", "name": "Kinyarwanda"}]}
        )


class SystemChecksTest(TembaTest):
    def test_mailroom_url(self):
        with override_settings(MAILROOM_URL="http://mailroom.io"):
            self.assertEqual(len(mailroom_url(None)), 0)

        with override_settings(MAILROOM_URL=None):
            self.assertEqual(mailroom_url(None)[0].msg, "No mailroom URL set, simulation will not be available")
