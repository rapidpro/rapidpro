from __future__ import unicode_literals, absolute_import

from django.urls import reverse
from temba.contacts.models import URN
from temba.tests import TembaTest
from ...models import Channel


class JioChatTypeTest(TembaTest):
    def test_claim(self):
        url = reverse('channels.claim_jiochat')

        self.login(self.admin)

        # check that claim page URL appears on claim list page
        response = self.client.get(reverse('channels.channel_claim'))
        self.assertContains(response, url)

        # try to claim a channel
        response = self.client.get(url)
        post_data = response.context['form'].initial
        post_data['app_id'] = 'foofoofoofoofoofoofoofoofoofoofoofoofoofoofoofoofoofoofoofoo'
        post_data['app_secret'] = 'barbarbarbarbarbarbarbarbarbarbarbarbarbarbarbarbarbarbarbar'

        response = self.client.post(url, post_data)

        channel = Channel.objects.get(channel_type='JC')

        self.assertEqual(channel.config_json(), {'jiochat_app_id': post_data['app_id'], 'jiochat_app_secret': post_data['app_secret']})

        config_url = reverse('channels.channel_configuration', args=[channel.pk])
        self.assertRedirect(response, config_url)

        response = self.client.get(config_url)
        self.assertEqual(200, response.status_code)

        self.assertContains(response, reverse('courier.jc', args=[channel.uuid]))
        self.assertContains(response, channel.secret)

        contact = self.create_contact('Jiochat User', urn=URN.from_jiochat('1234'))

        # make sure we our jiochat channel satisfies as a send channel
        response = self.client.get(reverse('contacts.contact_read', args=[contact.uuid]))
        send_channel = response.context['send_channel']
        self.assertIsNotNone(send_channel)
        self.assertEqual(send_channel.channel_type, 'JC')
