# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import pytz

from django.urls import reverse

from temba.tests import TembaTest

from ...models import Channel


class MessangiTypeTest(TembaTest):
    def test_claim(self):
        Channel.objects.all().delete()

        self.login(self.admin)
        url = reverse("channels.types.messangi.claim")

        # shouldn't be able to see the claim messangi page if we aren't part of that group
        response = self.client.get(reverse("channels.channel_claim"))
        self.assertNotContains(response, url)

        # but if we are in the proper time zone
        self.org.timezone = pytz.timezone("America/Jamaica")
        self.org.save()

        response = self.client.get(reverse("channels.channel_claim"))
        self.assertContains(response, "Messangi")
        self.assertContains(response, url)

        # try to claim a channel
        response = self.client.get(url)
        post_data = response.context["form"].initial

        post_data["shortcode"] = "17657273786"
        post_data["carrier_id"] = 7
        post_data["public_key"] = "mgPublicKey"
        post_data["private_key"] = "mgPrivateKey"
        post_data["instance_id"] = 2

        response = self.client.post(url, post_data)

        channel = Channel.objects.get()

        from .type import MessangiType

        self.assertEqual("JM", channel.country)
        self.assertEqual(7, channel.config[MessangiType.CONFIG_CARRIER_ID])
        self.assertEqual("mgPublicKey", channel.config[MessangiType.CONFIG_PUBLIC_KEY])
        self.assertEqual("mgPrivateKey", channel.config[MessangiType.CONFIG_PRIVATE_KEY])
        self.assertEqual(2, channel.config[MessangiType.CONFIG_INSTANCE_ID])
        self.assertEqual(post_data["shortcode"], channel.address)
        self.assertEqual("MG", channel.channel_type)

        config_url = reverse("channels.channel_configuration", args=[channel.uuid])
        self.assertRedirect(response, config_url)

        response = self.client.get(config_url)
        self.assertEqual(200, response.status_code)

        self.assertContains(response, reverse("courier.mg", args=[channel.uuid, "receive"]))
