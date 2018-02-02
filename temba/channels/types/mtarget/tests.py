from __future__ import unicode_literals, absolute_import

from django.urls import reverse
from temba.tests import TembaTest


class MtargetTypeTest(TembaTest):

    def test_claim(self):
        self.login(self.admin)
        response = self.client.get(reverse('channels.channel_claim'))
        self.assertContains(response, reverse('channels.claim_mtarget'))
