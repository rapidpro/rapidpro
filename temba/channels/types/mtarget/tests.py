from __future__ import unicode_literals, absolute_import

from django.urls import reverse
from temba.tests import TembaTest
from temba.channels.models import Channel


class MtargetTypeTest(TembaTest):

    def test_claim(self):
        self.login(self.admin)
        response = self.client.get(reverse('channels.channel_claim'))

        claim_url = reverse('channels.claim_mtarget')
        self.assertContains(response, claim_url)

        # claim it
        response = self.client.get(claim_url)
        post_data = response.context['form'].initial

        post_data['country'] = 'FR'
        post_data['number'] = '+33509758351'
        post_data['username'] = 'user1'
        post_data['password'] = 'pass1'

        response = self.client.post(claim_url, post_data, follow=True)

        channel = Channel.objects.get(channel_type='MT', address='+33509758351', country='FR')
        self.assertContains(response, reverse('courier.mt', args=[channel.uuid, 'receive']))
        self.assertContains(response, reverse('courier.mt', args=[channel.uuid, 'status']))
        self.assertContains(response, reverse('courier.mt', args=[channel.uuid, 'stop']))
