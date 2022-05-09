from temba.tests import MockResponse, TembaTest

from ...models import Channel


from unittest.mock import call, patch

from django_redis import get_redis_connection
from requests import RequestException

from django.forms import ValidationError
from django.urls import reverse

from temba.request_logs.models import HTTPLog
from temba.templates.models import TemplateTranslation
from temba.tests import MockResponse, TembaTest
from temba.utils.whatsapp.tasks import refresh_whatsapp_contacts, refresh_whatsapp_templates

from ...models import Channel
from .type import WhatsAppCloudType

class WhatsAppCloudTypeTest(TembaTest):
    def test_claim(self):
        self.login(self.admin)

        # remove any existing channels
        self.org.channels.update(is_active=False)

        connect_whatsapp_cloud_url = reverse("orgs.org_whatsapp_cloud_connect")
        claim_whatsapp_cloud_url = reverse("channels.types.whatsapp_cloud.claim")

        # make sure plivo is on the claim page
        response = self.client.get(reverse("channels.channel_claim"))
        self.assertEqual(200, response.status_code)
        self.assertNotContains(response, claim_whatsapp_cloud_url)

        with patch("requests.get") as wa_cloud_get:
            wa_cloud_get.return_value = MockResponse(400, {})
            response = self.client.get(claim_whatsapp_cloud_url)

            self.assertEqual(response.status_code, 302)

            response = self.client.get(claim_whatsapp_cloud_url, follow=True)

            self.assertEqual(response.request["PATH_INFO"], connect_whatsapp_cloud_url)
