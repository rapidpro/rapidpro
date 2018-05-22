# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals


from django.urls import reverse
from temba.tests import TembaTest
from ...models import Channel


class JunebugTypeTest(TembaTest):

    def test_claim(self):
        Channel.objects.all().delete()
        self.login(self.admin)

        url = reverse('channels.types.junebug_ussd.claim')

        # check that claim page URL appears on claim list page
        response = self.client.get(reverse('channels.channel_claim'))
        self.assertContains(response, url)

        response = self.client.get(url)
        self.assertEqual(200, response.status_code)

        post_data = {
            "country": "ZA",
            "number": "+273454325324",
            "url": "http://example.com/messages.json",
            "username": "foo",
            "password": "bar",
            "secret": "secret-word"
        }

        response = self.client.post(url, post_data)

        channel = Channel.objects.get()
        self.assertEqual(channel.channel_type, 'JNU')
        self.assertEqual(channel.role, Channel.ROLE_USSD)
        self.assertEqual(channel.config[Channel.CONFIG_SECRET], "secret-word")
