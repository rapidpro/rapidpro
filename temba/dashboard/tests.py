from __future__ import unicode_literals

from datetime import datetime
from django.core.urlresolvers import reverse
from temba.msgs.models import Label
from temba.flows.models import ActionSet, Flow
from temba.tests import TembaTest


class DashboardTest(TembaTest):
    def setUp(self):
        super(DashboardTest, self).setUp()

        self.user = self.create_user("tito")
        self.flow_label = Label.label_objects.create(name="Color", org=self.org,
                                                     created_by=self.admin, modified_by=self.admin)

    def test_dashboard_home(self):
        dashboard_url = reverse('dashboard.dashboard_home')

        # visit this page without authenticating
        response = self.client.get(dashboard_url, follow=True)

        # nope! cannot visit dashboard.
        self.assertRedirects(response, "/users/login/?next=%s" % dashboard_url)

        self.login(self.superuser)
        response = self.client.get(dashboard_url, follow=True)

        # yep! it works
        self.assertEqual(response.request['PATH_INFO'], dashboard_url)

        # and some message and call activity
        joe = self.create_contact("Joe", twitter="joe")
        self.create_msg(contact=joe, direction='O', text="Tea of coffee?", channel=self.channel)
        self.create_msg(contact=joe, direction='I', text="Coffee", channel=self.channel)
        self.create_msg(contact=joe, direction='O', text="OK", channel=self.channel)
        self.create_msg(contact=joe, direction='O', text="Wanna hang?", channel=self.channel, msg_type='V')
        self.create_msg(contact=joe, direction='I', text="Sure", channel=self.channel, msg_type='V')

        response = self.client.get(dashboard_url)

        today = datetime.utcnow().date()

        self.assertEqual(response.context['message_stats'], [
            {'name': "Incoming Text", 'data': [{'count': 1, 'date': today}]},
            {'name': "Outgoing Text", 'data': [{'count': 2, 'date': today}]},
            {'name': "Incoming IVR", 'data': [{'count': 1, 'date': today}]},
            {'name': "Outgoing IVR", 'data': [{'count': 1, 'date': today}]}
        ])

    def test_dashboard_flows(self):
        dashflows_url = reverse('dashboard.dashboard_flows')

        # visit this page without authenticating
        response = self.client.get(dashflows_url, follow=True)

        # nope! cannot visit dashboard.
        self.assertRedirects(response, "/users/login/?next=%s" % dashflows_url)

        self.login(self.superuser)
        response = self.client.get(dashflows_url, follow=True)

        # yep! it works
        self.assertEqual(response.request['PATH_INFO'], dashflows_url)
        self.assertEqual(len(response.context['recent']), 0)

        # create a flow with action sets
        recent_flow = Flow.objects.create(name="Recent Flow", org=self.org, saved_by=self.superuser,
                                          created_by=self.superuser, modified_by=self.superuser)

        ActionSet.objects.create(uuid='123456789012345678901234567890123456',
                                 flow=recent_flow, actions="{type:'reply', msg:'Wow!'}", x=10, y=10)
        ActionSet.objects.create(uuid='123456789012345678901234567890123457',
                                 flow=recent_flow, actions="{type:'reply', msg:'Wow!'}", x=10, y=10)
        ActionSet.objects.create(uuid='123456789012345678901234567890123458',
                                 flow=recent_flow, actions="{type:'reply', msg:'Wow!'}", x=10, y=10)
        ActionSet.objects.create(uuid='123456789012345678901234567890123459',
                                 flow=recent_flow, actions="{type:'reply', msg:'Wow!'}", x=10, y=10)

        response = self.client.get(dashflows_url, follow=True)
        self.assertEqual(response.request['PATH_INFO'], dashflows_url)
        self.assertEqual(len(response.context['recent']), 1)
