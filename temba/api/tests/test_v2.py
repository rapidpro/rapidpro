# -*- coding: utf-8 -*-
from __future__ import absolute_import, unicode_literals

import json

from django.core.urlresolvers import reverse
from django.db import connection
from temba.tests import TembaTest
from temba.utils.profiler import SegmentProfiler


class APITest(TembaTest):

    def setUp(self):
        super(APITest, self).setUp()

        self.joe = self.create_contact("Joe Blow", "0788123123")

        # this is needed to prevent REST framework from rolling back transaction created around each unit test
        connection.settings_dict['ATOMIC_REQUESTS'] = False

    def tearDown(self):
        super(APITest, self).tearDown()

        connection.settings_dict['ATOMIC_REQUESTS'] = True

    def fetchHTML(self, url, query=None):
        if query:
            url += ('?' + query)

        return self.client.get(url, HTTP_X_FORWARDED_HTTPS='https')

    def fetchJSON(self, url, query=None):
        url += '.json'
        if query:
            url += ('?' + query)

        response = self.client.get(url, content_type="application/json", HTTP_X_FORWARDED_HTTPS='https')

        # this will fail if our response isn't valid json
        response.json = json.loads(response.content)
        return response

    def assert403(self, url):
        response = self.fetchHTML(url)
        self.assertEquals(403, response.status_code)

    def test_api_runs(self):
        url = reverse('api.v2.runs')

        # can't access, get 403
        self.assert403(url)

        # login as plain user
        self.login(self.user)
        self.assert403(url)

        # login as administrator
        self.login(self.admin)

        # browse endpoint as HTML docs
        response = self.fetchHTML(url)
        self.assertEqual(response.status_code, 200)

        flow = self.create_flow()

        answers = ["Blue, ""Orange"]
        for n in range(1000):
            flow.start([], [self.joe], restart_participants=True)
            msg = self.create_msg(direction='I', contact=self.joe, text=answers[n % len(answers)])
            msg.handle()

        # now test fetching them instead.....

        # no filtering

        with SegmentProfiler("Fetching runs"):
            response = self.fetchJSON(url)
            self.assertEqual(response.status_code, 200)

            import pdb; pdb.set_trace()

            next_url = response.json['next']

            response = self.fetchJSON(next_url)

