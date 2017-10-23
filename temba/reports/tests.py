from __future__ import unicode_literals

from django.core.urlresolvers import reverse
from models import Report
from temba.tests import TembaTest


class ReportTest(TembaTest):

    def test_create(self):
        self.login(self.admin)

        create_url = reverse('reports.report_create')

        response = self.client.get(create_url)
        self.assertEqual(response.status_code, 302)

        response = self.client.get(create_url, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.request['PATH_INFO'], reverse('flows.ruleset_analytics'))

        response = self.client.post(create_url, {"title": "first report", "description": "some description", "config": "{}"})
        self.assertEqual(response.json()['status'], "error")
        self.assertFalse('report' in response.json())

        response = self.client.post(create_url, data='{"title":"first report", "description":"some description", "config":""}', content_type='application/json')
        self.assertEqual(response.json()['status'], "success")
        self.assertTrue('report' in response.json())
        self.assertTrue('id' in response.json()['report'])
        report = Report.objects.get()
        self.assertEqual(report.pk, response.json()['report']['id'])

    def test_report_model(self):
        Report.create_report(self.org, self.admin, dict(title="first", description="blah blah text",
                                                        config=dict(fields=[1, 2, 3])))
        self.assertEqual(Report.objects.all().count(), 1)

        Report.create_report(self.org, self.admin, dict(title="second", description="yeah yeah yeah",
                                                        config=dict(fields=[4, 5, 6])))
        self.assertEqual(Report.objects.all().count(), 2)

        report_id = Report.objects.filter(title="first")[0].pk
        Report.create_report(self.org, self.admin, dict(title="updated",
                                                        description="yeah yeah yeahnew description",
                                                        config=dict(fields=[8, 4]), id=report_id))
        self.assertEqual(Report.objects.all().count(), 2)
        self.assertFalse(Report.objects.filter(title="first"))
        self.assertEqual(Report.objects.get(title="updated").pk, report_id)
