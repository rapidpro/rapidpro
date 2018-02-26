# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from django.urls import reverse
from temba.tests import TembaTest
from ...models import Channel


class ExternalTypeTest(TembaTest):
    def test_claim(self):
        url = reverse('channels.types.external.claim')

        self.login(self.admin)

        # check that claim page URL appears on claim list page
        response = self.client.get(reverse('channels.channel_claim'))
        self.assertContains(response, url)

        # try to claim a channel
        Channel.objects.all().delete()
        response = self.client.get(url)
        post_data = response.context['form'].initial

        ext_url = 'http://test.com/send.php?from={{from}}&text={{text}}&to={{to}}'

        post_data['number'] = '12345'
        post_data['country'] = 'RW'
        post_data['url'] = ext_url
        post_data['method'] = 'POST'
        post_data['body'] = 'send=true'
        post_data['scheme'] = 'tel'
        post_data['content_type'] = Channel.CONTENT_TYPE_JSON
        post_data['max_length'] = 180

        response = self.client.post(url, post_data)
        channel = Channel.objects.get()

        self.assertEqual(channel.country, 'RW')
        self.assertTrue(channel.uuid)
        self.assertEqual(post_data['number'], channel.address)
        self.assertEqual(post_data['url'], channel.config[Channel.CONFIG_SEND_URL])
        self.assertEqual(post_data['method'], channel.config[Channel.CONFIG_SEND_METHOD])
        self.assertEqual(post_data['content_type'], channel.config[Channel.CONFIG_CONTENT_TYPE])
        self.assertEqual(channel.config[Channel.CONFIG_MAX_LENGTH], 180)
        self.assertEqual(channel.channel_type, 'EX')

        config_url = reverse('channels.channel_configuration', args=[channel.uuid])
        self.assertRedirect(response, config_url)

        response = self.client.get(config_url)
        self.assertEqual(response.status_code, 200)

        self.assertContains(response, reverse('courier.ex', args=[channel.uuid, 'sent']))
        self.assertContains(response, reverse('courier.ex', args=[channel.uuid, 'delivered']))
        self.assertContains(response, reverse('courier.ex', args=[channel.uuid, 'failed']))
        self.assertContains(response, reverse('courier.ex', args=[channel.uuid, 'receive']))

        # test substitution in our url
        self.assertEqual('http://test.com/send.php?from=5080&text=test&to=%2B250788383383',
                         channel.replace_variables(ext_url, {'from': "5080", 'text': "test", 'to': "+250788383383"}))

        # test substitution with unicode
        self.assertEqual(
            'http://test.com/send.php?from=5080&text=Reply+%E2%80%9C1%E2%80%9D+for+good&to=%2B250788383383',
            channel.replace_variables(ext_url, {
                'from': "5080",
                'text': "Reply “1” for good",
                'to': "+250788383383"
            }))

        # test substitution with XML encoding
        body = "<xml>{{text}}</xml>"
        self.assertEqual("<xml>Hello &amp; World</xml>",
                         channel.replace_variables(body, {'text': "Hello & World"}, Channel.CONTENT_TYPE_XML))

        self.assertEqual("<xml>التوطين</xml>",
                         channel.replace_variables(body, {'text': "التوطين"}, Channel.CONTENT_TYPE_XML))

        # test substitution with JSON encoding
        body = "{ body: {{text}} }"
        self.assertEqual("{ body: \"this is \\\"quote\\\"\" }",
                         channel.replace_variables(body, {'text': "this is \"quote\""}, Channel.CONTENT_TYPE_JSON))
