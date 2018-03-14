# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from django.urls import reverse
from temba.tests import TembaTest
from ...models import Channel


class JasminTypeTest(TembaTest):

    def test_claim(self):
        Channel.objects.all().delete()

        url = reverse('channels.types.jasmin.claim')

        self.login(self.admin)

        # check that claim page URL appears on claim list page
        response = self.client.get(reverse('channels.channel_claim'))
        self.assertContains(response, url)

        # try to claim a channel
        response = self.client.get(url)
        post_data = response.context['form'].initial

        post_data['country'] = 'UG'
        post_data['number'] = '250788123123'
        post_data['username'] = 'user1'
        post_data['password'] = 'pass1'
        post_data['url'] = "https://jasmin.gateway.io/send"

        response = self.client.post(url, post_data)

        channel = Channel.objects.get()

        self.assertEqual('UG', channel.country)
        self.assertEqual(post_data['username'], channel.config['username'])
        self.assertEqual(post_data['password'], channel.config['password'])
        self.assertEqual(post_data['url'], channel.config['send_url'])
        self.assertEqual('+250788123123', channel.address)
        self.assertEqual('JS', channel.channel_type)

        config_url = reverse('channels.channel_configuration', args=[channel.uuid])
        self.assertRedirect(response, config_url)

        response = self.client.get(config_url)
        self.assertEqual(200, response.status_code)

        self.assertContains(response, reverse('courier.js', args=[channel.uuid, 'receive']))

        Channel.objects.all().delete()

        response = self.client.get(url)
        post_data = response.context['form'].initial

        post_data['country'] = 'UG'
        post_data['number'] = '20050'
        post_data['username'] = 'user1'
        post_data['password'] = 'pass1'
        post_data['url'] = "https://jasmin.gateway.io/send"

        response = self.client.post(url, post_data)

        channel = Channel.objects.get()

        self.assertEqual('UG', channel.country)
        self.assertEqual(post_data['username'], channel.config['username'])
        self.assertEqual(post_data['password'], channel.config['password'])
        self.assertEqual(post_data['url'], channel.config['send_url'])
        self.assertEqual('20050', channel.address)
        self.assertEqual('JS', channel.channel_type)
