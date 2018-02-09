from __future__ import unicode_literals, absolute_import

from django.urls import reverse
from temba.tests import TembaTest
from temba.channels.models import Channel


class ArabiaCellTest(TembaTest):

    def test_claim(self):
        self.login(self.admin)
        response = self.client.get(reverse('channels.channel_claim'))

        claim_url = reverse('channels.claim_arabiacell')
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
        self.assertEqual("user1", channel.config_json()['username'])
        self.assertEqual("pass1", channel.config_json()['password'])
        self.assertEqual("151515", channel.config_json()['service_id'])
        self.assertEqual("0", channel.config_json()['charging_level'])
        self.assertContains(response, '/c/ac/' + channel.uuid + '/receive')
