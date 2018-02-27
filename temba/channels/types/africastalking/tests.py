# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from django.urls import reverse
from temba.tests import TembaTest
from ...models import Channel


class AfricastalkingTypeTest(TembaTest):

    def test_claim(self):
        Channel.objects.all().delete()

        url = reverse('channels.types.africastalking.claim')
        self.login(self.admin)

        response = self.client.get(reverse('channels.channel_claim'))
        self.assertNotContains(response, url)

        self.org.timezone = "Africa/Nairobi"
        self.org.save()

        # check that claim page URL appears on claim list page
        response = self.client.get(reverse('channels.channel_claim'))
        self.assertContains(response, url)
        # visit the africa's talking page
        response = self.client.get(url)
        self.assertEqual(200, response.status_code)
        post_data = response.context['form'].initial

        post_data['shortcode'] = '5259'
        post_data['username'] = 'temba'
        post_data['api_key'] = 'asdf-asdf-asdf-asdf-asdf'
        post_data['country'] = 'KE'

        response = self.client.post(url, post_data)

        channel = Channel.objects.get()

        self.assertEqual('temba', channel.config['username'])
        self.assertEqual('asdf-asdf-asdf-asdf-asdf', channel.config['api_key'])
        self.assertEqual('5259', channel.address)
        self.assertEqual('KE', channel.country)
        self.assertEqual('AT', channel.channel_type)

        config_url = reverse('channels.channel_configuration', args=[channel.uuid])
        self.assertRedirect(response, config_url)

        response = self.client.get(config_url)
        self.assertEqual(200, response.status_code)

        self.assertContains(response, reverse('courier.at', args=[channel.uuid, 'receive']))
        self.assertContains(response, reverse('courier.at', args=[channel.uuid, 'status']))
