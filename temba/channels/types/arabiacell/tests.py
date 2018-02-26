# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from django.urls import reverse
from temba.tests import TembaTest
from temba.channels.models import Channel


class ArabiaCellTest(TembaTest):

    def test_claim(self):
        self.login(self.admin)
        response = self.client.get(reverse('channels.channel_claim'))

        claim_url = reverse('channels.types.arabiacell.claim')
        response = self.client.get(claim_url)
        post_data = response.context['form'].initial

        post_data['country'] = 'JO'
        post_data['shortcode'] = '2020'
        post_data['charging_level'] = '0'
        post_data['service_id'] = '151515'
        post_data['username'] = 'user1'
        post_data['password'] = 'pass1'

        response = self.client.post(claim_url, post_data, follow=True)

        channel = Channel.objects.get(channel_type='AC', address='2020', country='JO', name="2020")
        self.assertEqual("user1", channel.config['username'])
        self.assertEqual("pass1", channel.config['password'])
        self.assertEqual("151515", channel.config['service_id'])
        self.assertEqual("0", channel.config['charging_level'])
        self.assertContains(response, '/c/ac/' + channel.uuid + '/receive')
