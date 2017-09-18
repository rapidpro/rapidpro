from __future__ import unicode_literals, absolute_import

import uuid

from django.urls import reverse
from temba.tests import TembaTest
from ...models import Channel


class VumiTypeTest(TembaTest):

    def test_claim(self):
        Channel.objects.all().delete()
        url = reverse('channels.claim_vumi')

        self.login(self.admin)

        response = self.client.get(reverse('channels.channel_claim'))
        self.assertNotContains(response, url)

        self.org.timezone = "Africa/Lagos"
        self.org.save()

        # check that claim page URL appears on claim list page
        response = self.client.get(reverse('channels.channel_claim'))
        self.assertContains(response, url)

        response = self.client.get(url)
        self.assertEquals(200, response.status_code)

        post_data = {
            "country": "NG",
            "number": "+273454325324",
            "account_key": "account1",
            "conversation_key": "conversation1",
            "api_url": "http://custom.api.url"
        }

        response = self.client.post(url, post_data)

        channel = Channel.objects.get()

        self.assertTrue(uuid.UUID(channel.config_json()['access_token'], version=4))
        self.assertEquals(channel.country, post_data['country'])
        self.assertEquals(channel.address, post_data['number'])
        self.assertEquals(channel.config_json()['account_key'], post_data['account_key'])
        self.assertEquals(channel.config_json()['conversation_key'], post_data['conversation_key'])
        self.assertEquals(channel.config_json()['api_url'], "http://custom.api.url")
        self.assertEquals(channel.channel_type, 'VM')
        self.assertEquals(channel.role, Channel.DEFAULT_ROLE)

        config_url = reverse('channels.channel_configuration', args=[channel.pk])
        self.assertRedirect(response, config_url)

        response = self.client.get(config_url)
        self.assertEquals(200, response.status_code)

        self.assertContains(response, reverse('courier.vm', args=[channel.uuid, 'receive']))
        self.assertContains(response, reverse('courier.vm', args=[channel.uuid, 'event']))

        Channel.objects.all().delete()

        post_data = {
            "country": "NG",
            "number": "+273454325324",
            "account_key": "account1",
            "conversation_key": "conversation1",
        }

        response = self.client.post(url, post_data)

        channel = Channel.objects.get()

        self.assertTrue(uuid.UUID(channel.config_json()['access_token'], version=4))
        self.assertEquals(channel.country, post_data['country'])
        self.assertEquals(channel.address, post_data['number'])
        self.assertEquals(channel.config_json()['account_key'], post_data['account_key'])
        self.assertEquals(channel.config_json()['conversation_key'], post_data['conversation_key'])
        self.assertEquals(channel.config_json()['api_url'], "https://go.vumi.org/api/v1/go/http_api_nostream")
        self.assertEquals(channel.channel_type, 'VM')
        self.assertEquals(channel.role, Channel.DEFAULT_ROLE)
