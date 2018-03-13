from __future__ import unicode_literals, absolute_import

from django.urls import reverse
from temba.tests import TembaTest
from ...models import Channel


class VerboiceTypeTest(TembaTest):

    def test_claim(self):
        Channel.objects.all().delete()

        url = reverse('channels.types.verboice.claim')

        self.login(self.admin)

        # check that claim page URL does not appears on claim list page
        response = self.client.get(reverse('channels.channel_claim'))
        self.assertNotContains(response, url)

        # try to claim a channel
        response = self.client.get(url)
        post_data = response.context['form'].initial

        post_data['country'] = 'RW'
        post_data['number'] = '250788123123'
        post_data['username'] = 'user1'
        post_data['password'] = 'pass1'
        post_data['channel'] = 'Testing'

        response = self.client.post(url, post_data)

        channel = Channel.objects.get()

        self.assertEqual('RW', channel.country)
        self.assertEqual(post_data['username'], channel.config.get('username'))
        self.assertEqual(post_data['password'], channel.config.get('password'))
        self.assertEqual(post_data['channel'], channel.config.get('channel'))
        self.assertEqual('+250788123123', channel.address)
        self.assertEqual('VB', channel.channel_type)
        self.assertEqual('CA', channel.role)

        config_url = reverse('channels.channel_configuration', args=[channel.uuid])
        self.assertRedirect(response, config_url)

        response = self.client.get(config_url)
        self.assertEqual(200, response.status_code)

        self.assertContains(response, reverse('courier.vb', args=[channel.uuid, 'status']))

        Channel.objects.all().delete()

        response = self.client.get(url)
        post_data = response.context['form'].initial

        post_data['country'] = 'RW'
        post_data['number'] = '12345'
        post_data['username'] = 'user1'
        post_data['password'] = 'pass1'
        post_data['channel'] = 'Testing'

        response = self.client.post(url, post_data)

        channel = Channel.objects.get()

        self.assertEqual('RW', channel.country)
        self.assertEqual(post_data['username'], channel.config.get('username'))
        self.assertEqual(post_data['password'], channel.config.get('password'))
        self.assertEqual(post_data['channel'], channel.config.get('channel'))
        self.assertEqual('12345', channel.address)
        self.assertEqual('VB', channel.channel_type)
        self.assertEqual('CA', channel.role)
