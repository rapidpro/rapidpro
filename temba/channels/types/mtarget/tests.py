# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from django.urls import reverse
from temba.tests import TembaTest
from temba.channels.models import Channel


class MtargetTypeTest(TembaTest):

    def test_claim(self):
        self.login(self.admin)
        response = self.client.get(reverse('channels.channel_claim'))

        claim_url = reverse('channels.types.mtarget.claim')
        self.assertContains(response, claim_url)

        # claim it
        response = self.client.get(claim_url)
        post_data = response.context['form'].initial

        post_data['country'] = 'FR'
        post_data['service_id'] = '151515'
        post_data['username'] = 'user1'
        post_data['password'] = 'pass1'

        response = self.client.post(claim_url, post_data, follow=True)

        channel = Channel.objects.get(channel_type='MT', address='151515', country='FR', name="151515")
        self.assertEqual("user1", channel.config['username'])
        self.assertEqual("pass1", channel.config['password'])
        self.assertContains(response, reverse('courier.mt', args=[channel.uuid, 'receive']))
        self.assertContains(response, reverse('courier.mt', args=[channel.uuid, 'status']))
