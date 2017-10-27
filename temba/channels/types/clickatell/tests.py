from __future__ import unicode_literals, absolute_import

from django.urls import reverse
from temba.tests import TembaTest
from ...models import Channel


class ClickatellTypeTest(TembaTest):

    def test_claim(self):
        Channel.objects.all().delete()

        self.login(self.admin)

        url = reverse('channels.claim_clickatell')

        # should see the general channel claim page
        response = self.client.get(reverse('channels.channel_claim'))
        self.assertContains(response, url)

        # try to claim a channel
        response = self.client.get(url)
        post_data = response.context['form'].initial

        post_data['api_id'] = '12345'
        post_data['username'] = 'uname'
        post_data['password'] = 'pword'
        post_data['country'] = 'US'
        post_data['number'] = '(206) 555-1212'

        response = self.client.post(url, post_data)

        channel = Channel.objects.get()

        self.assertEqual('US', channel.country)
        self.assertTrue(channel.uuid)
        self.assertEqual('+12065551212', channel.address)
        self.assertEqual(post_data['api_id'], channel.config_json()['api_id'])
        self.assertEqual(post_data['username'], channel.config_json()['username'])
        self.assertEqual(post_data['password'], channel.config_json()['password'])
        self.assertEqual('CT', channel.channel_type)

        config_url = reverse('channels.channel_configuration', args=[channel.pk])
        self.assertRedirect(response, config_url)

        response = self.client.get(config_url)
        self.assertEqual(200, response.status_code)

        self.assertContains(response, reverse('courier.ct', args=[channel.uuid, 'status']))
        self.assertContains(response, reverse('courier.ct', args=[channel.uuid, 'receive']))
