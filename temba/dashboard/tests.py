# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from django.core.urlresolvers import reverse

from temba.channels.models import Channel
from temba.msgs.models import Label
from temba.tests import TembaTest


class DashboardTest(TembaTest):
    def setUp(self):
        super(DashboardTest, self).setUp()

        self.user = self.create_user("tito")
        self.flow_label = Label.label_objects.create(name="Color", org=self.org,
                                                     created_by=self.admin, modified_by=self.admin)

    def create_activity(self):

        # and some message and call activity
        joe = self.create_contact("Joe", twitter="joe")
        self.create_msg(contact=joe, direction='O', text="Tea of coffee?", channel=self.channel)
        self.create_msg(contact=joe, direction='I', text="Coffee", channel=self.channel)
        self.create_msg(contact=joe, direction='O', text="OK", channel=self.channel)
        self.create_msg(contact=joe, direction='O', text="Wanna hang?", channel=self.channel, msg_type='V')
        self.create_msg(contact=joe, direction='I', text="Sure", channel=self.channel, msg_type='V')

    def test_dashboard_home(self):
        dashboard_url = reverse('dashboard.dashboard_home')

        # visit this page without authenticating
        response = self.client.get(dashboard_url, follow=True)

        # nope! cannot visit dashboard.
        self.assertRedirects(response, "/users/login/?next=%s" % dashboard_url)

        self.login(self.admin)
        response = self.client.get(dashboard_url, follow=True)

        # yep! it works
        self.assertEqual(response.request['PATH_INFO'], dashboard_url)

    def test_message_history(self):

        url = reverse('dashboard.dashboard_message_history')

        # visit this page without authenticating
        response = self.client.get(url, follow=True)

        # nope!
        self.assertRedirects(response, "/users/login/?next=%s" % url)

        self.login(self.admin)
        self.create_activity()
        response = self.client.get(url).json()

        # in, out, and total
        self.assertEqual(3, len(response))

        # incoming messages
        self.assertEqual(2, response[0]['data'][0][1])

        # outgoing messages
        self.assertEqual(3, response[1]['data'][0][1])

        # total messages
        self.assertEqual(5, response[2]['data'][0][1])

    def test_range_details(self):

        url = reverse('dashboard.dashboard_range_details')

        # visit this page without authenticating
        response = self.client.get(url, follow=True)

        # nope!
        self.assertRedirects(response, "/users/login/?next=%s" % url)

        self.login(self.admin)
        self.create_activity()

        types = ['T', 'TT', 'FB', 'NX', 'AT', 'KN', 'CK']
        michael = self.create_contact("Michael", twitter="mjackson")
        for t in types:
            channel = Channel.create(self.org, self.user, None, t, name="Test Channel %s" % t, address="%s:1234" % t)
            self.create_msg(contact=michael, direction='O', text="Message on %s" % t, channel=channel)
        response = self.client.get(url)

        # org message activity
        self.assertEqual(12, response.context['orgs'][0]['count_sum'])
        self.assertEqual('Temba', response.context['orgs'][0]['channel__org__name'])

        # our pie chart
        self.assertEqual(5, response.context['channel_types'][0]['count_sum'])
        self.assertEqual('Android', response.context['channel_types'][0]['channel__name'])
        self.assertEqual(7, len(response.context['channel_types']))
        self.assertEqual('Other', response.context['channel_types'][6]['channel__name'])
