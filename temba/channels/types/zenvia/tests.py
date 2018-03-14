# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import pytz
from django.urls import reverse
from temba.tests import TembaTest
from ...models import Channel


class ZenviaTypeTest(TembaTest):

    def test_claim(self):
        Channel.objects.all().delete()

        self.login(self.admin)
        url = reverse('channels.types.zenvia.claim')

        # shouldn't be able to see the claim zenvia page if we aren't part of that group
        response = self.client.get(reverse('channels.channel_claim'))
        self.assertNotContains(response, url)

        # but if we are in the proper time zone
        self.org.timezone = pytz.timezone('America/Sao_Paulo')
        self.org.save()

        response = self.client.get(reverse('channels.channel_claim'))
        self.assertContains(response, "Zenvia")
        self.assertContains(response, url)

        # try to claim a channel
        response = self.client.get(url)
        post_data = response.context['form'].initial

        post_data['username'] = 'zvUsername'
        post_data['password'] = 'zvPassword'
        post_data['shortcode'] = '28595'

        response = self.client.post(url, post_data)

        channel = Channel.objects.get()

        self.assertEqual('BR', channel.country)
        self.assertEqual('zvUsername', channel.config[Channel.CONFIG_USERNAME])
        self.assertEqual('zvPassword', channel.config[Channel.CONFIG_PASSWORD])
        self.assertEqual(post_data['shortcode'], channel.address)
        self.assertEqual('ZV', channel.channel_type)

        config_url = reverse('channels.channel_configuration', args=[channel.uuid])
        self.assertRedirect(response, config_url)

        response = self.client.get(config_url)
        self.assertEqual(200, response.status_code)

        self.assertContains(response, reverse('courier.zv', args=[channel.uuid, 'status']))
        self.assertContains(response, reverse('courier.zv', args=[channel.uuid, 'receive']))
