from django.urls import reverse

from temba.tests import CRUDLTestMixin, TembaTest

from ...models import Channel


class TestTypeTest(TembaTest, CRUDLTestMixin):
    def test_claim(self):
        claim_url = reverse("channels.types.test.claim")

        self.assertStaffOnly(claim_url)

        response = self.requestView(claim_url, self.customer_support, post_data={"tps": 50}, choose_org=self.org)

        self.assertEqual(302, response.status_code)

        channel = Channel.objects.filter(channel_type="TST").first()

        self.assertIsNotNone(channel)
        self.assertEqual(50, channel.tps)
        self.assertEqual(["ext"], channel.schemes)
