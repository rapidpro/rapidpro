from __future__ import unicode_literals, absolute_import

from django.urls import reverse
from temba.tests import TembaTest
from ...models import Channel


class JunebugTypeTest(TembaTest):
    def test_claim(self):
        Channel.objects.all().delete()
        self.login(self.admin)

        url = reverse('channels.claim_junebug')

        # check that claim page URL appears on claim list page
        response = self.client.get(reverse('channels.channel_claim'))
        self.assertContains(response, url)

        response = self.client.get(url)
        self.assertEquals(200, response.status_code)

        post_data = {
            "country": "ZA",
            "number": "+273454325324",
            "url": "http://example.com/messages.json",
            "username": "foo",
            "password": "bar",
        }

        response = self.client.post(url, post_data)

        channel = Channel.objects.get()

        self.assertEquals(channel.country, post_data['country'])
        self.assertEquals(channel.address, post_data['number'])
        self.assertEquals(channel.config_json()['send_url'], post_data['url'])
        self.assertEquals(channel.config_json()['username'], post_data['username'])
        self.assertEquals(channel.config_json()['password'], post_data['password'])
        self.assertEquals(channel.channel_type, 'JN')
        self.assertEquals(channel.role, Channel.DEFAULT_ROLE)

        config_url = reverse('channels.channel_configuration', args=[channel.pk])
        self.assertRedirect(response, config_url)

        response = self.client.get(config_url)
        self.assertEquals(200, response.status_code)

        self.assertContains(response, reverse('courier.jn', args=[channel.uuid, 'inbound']))

        Channel.objects.all().delete()
        self.login(self.admin)

        response = self.client.get(url)
        self.assertEquals(200, response.status_code)

        post_data = {
            "country": "ZA",
            "number": "+273454325324",
            "url": "http://example.com/messages.json",
            "username": "foo",
            "password": "bar",
            "secret": "UjOq8ATo2PDS6L08t6vlqSoK"
        }

        response = self.client.post(url, post_data)

        channel = Channel.objects.get()

        self.assertEquals(channel.country, post_data['country'])
        self.assertEquals(channel.address, post_data['number'])
        self.assertEquals(channel.secret, post_data['secret'])
        self.assertEquals(channel.config_json()['send_url'], post_data['url'])
        self.assertEquals(channel.config_json()['username'], post_data['username'])
        self.assertEquals(channel.config_json()['password'], post_data['password'])
        self.assertEquals(channel.channel_type, 'JN')
        self.assertEquals(channel.role, Channel.DEFAULT_ROLE)

        config_url = reverse('channels.channel_configuration', args=[channel.pk])
        self.assertRedirect(response, config_url)

        response = self.client.get(config_url)
        self.assertEquals(200, response.status_code)

        self.assertContains(response, reverse('courier.jn', args=[channel.uuid, 'inbound']))
