from temba.tests import TembaTest
from .models import Report


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
