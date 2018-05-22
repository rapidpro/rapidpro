# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from django.urls import reverse
from mock import patch
from temba.contacts.models import URN
from temba.tests import TembaTest, MockResponse
from temba.utils.jiochat import JiochatClient
from ...models import Channel, ChannelLog
from temba.channels.types.jiochat.tasks import refresh_jiochat_access_tokens


class JioChatTypeTest(TembaTest):
    def test_claim(self):
        url = reverse('channels.types.jiochat.claim')

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

        self.assertEqual(
            channel.config,
            {
                'jiochat_app_id': post_data['app_id'],
                'jiochat_app_secret': post_data['app_secret'],
                'secret': channel.config['secret'],
            }
        )

        config_url = reverse('channels.channel_configuration', args=[channel.uuid])
        self.assertRedirect(response, config_url)

        response = self.client.get(config_url)
        self.assertEqual(200, response.status_code)

        self.assertContains(response, reverse('courier.jc', args=[channel.uuid]))
        self.assertContains(response, channel.config[Channel.CONFIG_SECRET])

        contact = self.create_contact('Jiochat User', urn=URN.from_jiochat('1234'))

        # make sure we our jiochat channel satisfies as a send channel
        response = self.client.get(reverse('contacts.contact_read', args=[contact.uuid]))
        send_channel = response.context['send_channel']
        self.assertIsNotNone(send_channel)
        self.assertEqual(send_channel.channel_type, 'JC')

    @patch('requests.post')
    def test_refresh_jiochat_tokens(self, mock_post):
        channel = Channel.create(self.org, self.user, None, 'JC', None, '1212',
                                 config={'jiochat_app_id': 'app-id',
                                         'jiochat_app_secret': 'app-secret',
                                         'secret': Channel.generate_secret(32)},
                                 uuid='00000000-0000-0000-0000-000000001234')

        mock_post.return_value = MockResponse(400, '{ "error":"Failed" }')

        self.assertFalse(ChannelLog.objects.all())
        refresh_jiochat_access_tokens()

        self.assertEqual(ChannelLog.objects.all().count(), 1)
        self.assertTrue(ChannelLog.objects.filter(is_error=True).count(), 1)

        self.assertEqual(mock_post.call_count, 1)

        channel_client = JiochatClient.from_channel(channel)

        self.assertIsNone(channel_client.get_access_token())

        mock_post.reset_mock()
        mock_post.return_value = MockResponse(200, '{ "access_token":"ABC1234" }')

        refresh_jiochat_access_tokens()

        self.assertEqual(ChannelLog.objects.all().count(), 2)
        self.assertTrue(ChannelLog.objects.filter(is_error=True).count(), 1)
        self.assertTrue(ChannelLog.objects.filter(is_error=False).count(), 1)
        self.assertEqual(mock_post.call_count, 1)

        self.assertEqual(channel_client.get_access_token(), b'ABC1234')
        self.assertEqual(mock_post.call_args_list[0][1]['data'], {'client_secret': u'app-secret',
                                                                  'grant_type': 'client_credentials',
                                                                  'client_id': u'app-id'})
        self.login(self.admin)
        response = self.client.get(reverse("channels.channellog_list") + '?channel=%d&others=1' % channel.id,
                                   follow=True)
        self.assertEqual(len(response.context['object_list']), 2)
