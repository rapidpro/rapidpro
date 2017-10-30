from __future__ import unicode_literals, absolute_import

from django.urls import reverse
from mock import patch

from temba.tests import TembaTest, MockResponse
from ...models import Channel


class DMarkTypeTest(TembaTest):

    def test_claim(self):
        Channel.objects.all().delete()

        url = reverse('channels.claim_dmark')
        self.login(self.admin)

        response = self.client.get(reverse('channels.channel_claim'))
        self.assertNotContains(response, url)

        self.org.timezone = "Africa/Kinshasa"
        self.org.save()

        # check that claim page URL appears on claim list page
        response = self.client.get(reverse('channels.channel_claim'))
        self.assertContains(response, url)

        response = self.client.get(url)
        self.assertEqual(200, response.status_code)
        post_data = response.context['form'].initial

        post_data['shortcode'] = '2020'
        post_data['username'] = 'temba'
        post_data['password'] = 'tembapasswd'
        post_data['country'] = 'CD'

        # try once with an error
        with patch('requests.post') as mock_post:
            mock_post.return_value = MockResponse(400, '{ "error": "Invalid username or password" }')
            response = self.client.post(url, post_data)
            self.assertEqual(200, response.status_code)
            self.assertFalse(Channel.objects.all())

        # then success
        with patch('requests.post') as mock_post:
            mock_post.return_value = MockResponse(200, '{ "token": "Authy" }')
            response = self.client.post(url, post_data)
            self.assertEqual(302, response.status_code)

        channel = Channel.objects.get()

        self.assertEqual('temba', channel.config_json()['username'])
        self.assertEqual('tembapasswd', channel.config_json()['password'])
        self.assertEqual('Authy', channel.config_json()['auth_token'])

        self.assertEqual('2020', channel.address)
        self.assertEqual('CD', channel.country)
        self.assertEqual('DK', channel.channel_type)

        config_url = reverse('channels.channel_configuration', args=[channel.pk])
        self.assertRedirect(response, config_url)

        response = self.client.get(config_url)
        self.assertEqual(200, response.status_code)

        self.assertContains(response, reverse('courier.dk', args=[channel.uuid, 'receive']))
