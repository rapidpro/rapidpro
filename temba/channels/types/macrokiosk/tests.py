# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from django.urls import reverse
from temba.tests import TembaTest
from ...models import Channel


class MacrokioskTypeTest(TembaTest):

    def test_claim(self):
        Channel.objects.all().delete()

        url = reverse('channels.types.macrokiosk.claim')

        self.login(self.admin)

        response = self.client.get(reverse('channels.channel_claim'))
        self.assertNotContains(response, url)

        self.org.timezone = 'Asia/Kuala_Lumpur'
        self.org.save()

        response = self.client.get(reverse('channels.channel_claim'))
        self.assertContains(response, url)

        # try to claim a channel
        response = self.client.get(url)
        post_data = response.context['form'].initial

        post_data['country'] = 'MY'
        post_data['number'] = '250788123123'
        post_data['username'] = 'user1'
        post_data['password'] = 'pass1'
        post_data['sender_id'] = 'macro'
        post_data['service_id'] = 'SERVID'

        response = self.client.post(url, post_data)

        channel = Channel.objects.get()

        self.assertEqual(channel.country, 'MY')
        self.assertEqual(channel.config['username'], post_data['username'])
        self.assertEqual(channel.config['password'], post_data['password'])
        self.assertEqual(channel.config[Channel.CONFIG_MACROKIOSK_SENDER_ID], post_data['sender_id'])
        self.assertEqual(channel.config[Channel.CONFIG_MACROKIOSK_SERVICE_ID], post_data['service_id'])
        self.assertEqual(channel.address, '250788123123')
        self.assertEqual(channel.channel_type, 'MK')

        config_url = reverse('channels.channel_configuration', args=[channel.uuid])
        self.assertRedirect(response, config_url)

        response = self.client.get(config_url)
        self.assertEqual(200, response.status_code)

        self.assertContains(response, reverse('courier.mk', args=[channel.uuid, 'receive']))
        self.assertContains(response, reverse('courier.mk', args=[channel.uuid, 'status']))
