from unittest.mock import patch

from django.urls import reverse
from django.utils import timezone

from temba.tests import TembaTest
from .models import Report, DataCollectionProcess, CollectedFlowResultsData, DataCollectionProcessConfig
from .tasks import automatically_collect_flow_results_data, manually_collect_flow_results_data
from ..flows.models import Flow


class ReportTest(TembaTest):
    def test_report_model(self):
        Report.create_report(
            self.org, self.admin, dict(title="first", description="blah blah text", config=dict(fields=[1, 2, 3]))
        )
        self.assertEqual(Report.objects.all().count(), 1)

        Report.create_report(
            self.org, self.admin, dict(title="second", description="yeah yeah yeah", config=dict(fields=[4, 5, 6]))
        )
        self.assertEqual(Report.objects.all().count(), 2)

        report_id = Report.objects.filter(title="first")[0].pk
        Report.create_report(
            self.org,
            self.admin,
            dict(
                title="updated", description="yeah yeah yeahnew description", config=dict(fields=[8, 4]), id=report_id
            ),
        )
        self.assertEqual(Report.objects.all().count(), 2)
        self.assertFalse(Report.objects.filter(title="first"))
        self.assertEqual(Report.objects.get(title="updated").pk, report_id)

    @patch("temba.reports.models.DataCollectionProcess.start_process")
    def test_report_endpoints(self, mock_collection_process):
        self.login(self.admin)
        flow = self.create_flow()

        setattr(self, "_flow", flow)
        mock_collection_process.side_effect = self.mocking_collecting_data_process

        response = self.client.get(reverse("reports.report_analytics"))
        self.assertEqual(response.status_code, 200)

        response = self.client.post(reverse("reports.report_configure_flows"), {"flows": [flow.id]})
        self.assertEqual(response.status_code, 200)

        response = self.client.post(reverse("reports.report_update_charts_data"), {"flow": flow.id})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(DataCollectionProcess.objects.count(), 2)
        self.assertEqual(CollectedFlowResultsData.objects.count(), 0)

        response = self.client.post(reverse("reports.report_charts_data"))
        self.assertEqual(response.status_code, 200)

        response = self.client.post(
            reverse("reports.report_create"),
            {
                "text": "",
                "description": "",
                "config": {},
            },
            HTTP_X_FORWARDED_HTTPS="https",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Report.objects.count(), 1)

        report = Report.objects.first()
        response = self.client.delete(
            reverse("reports.report_delete"),
            {"report_id": report.id},
            content_type="application/json",
        )
        self.assertIsNotNone(response)
        self.assertEqual(Report.objects.count(), 0)

    def mocking_collecting_data_process(self, org, user, flows=None):
        flow = getattr(self, "_flow") if flows is None else Flow.objects.get(id=flows[0])
        DataCollectionProcess.objects.create(
            start_type=DataCollectionProcess.TYPE_MANUAL,
            flows_total=1,
            started_by=user,
            related_org=org,
            completed_on=timezone.now(),
        )
        CollectedFlowResultsData.collect_results_data(flow)

    def test_celery_tasks(self):
        flow = self.create_flow()
        config = DataCollectionProcessConfig.objects.create(org=self.org)
        config.flows.add(flow)
        automatically_collect_flow_results_data()

        cp = DataCollectionProcess.objects.create(
            start_type=DataCollectionProcess.TYPE_MANUAL,
            flows_total=1,
            started_by=self.admin,
            related_org=self.org,
        )
        manually_collect_flow_results_data(cp.id, [flow.id])
        cp.refresh_from_db(fields=["completed_on"])
        self.assertEqual(DataCollectionProcess.objects.count(), 2)
