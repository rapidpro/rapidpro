from temba.tests import TembaTest


class AssetServerTest(TembaTest):
    def test_languages(self):
        self.login(self.admin)
        response = self.client.get("/flow/assets/%d/1234/language/" % self.org.id)
        self.assertEqual(
            response.json(), {"results": [{"iso": "eng", "name": "English"}, {"iso": "kin", "name": "Kinyarwanda"}]}
        )
