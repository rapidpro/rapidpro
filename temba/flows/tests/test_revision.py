from datetime import timedelta

from django.db.models.functions import TruncDate
from django.utils import timezone

from temba.flows.models import FlowRevision
from temba.flows.tasks import trim_flow_revisions
from temba.tests import TembaTest


class FlowRevisionTest(TembaTest):
    def test_validate_legacy_definition(self):
        def validate(flow_def: dict, expected_error: str):
            with self.assertRaises(ValueError) as cm:
                FlowRevision.validate_legacy_definition(flow_def)
            self.assertEqual(expected_error, str(cm.exception))

        validate({"flow_type": "U", "nodes": []}, "unsupported flow type")
        validate(self.load_json("test_flows/legacy/invalid/not_fully_localized.json"), "non-localized flow definition")

        # base_language of null, but spec version 8
        validate(self.load_json("test_flows/legacy/invalid/no_base_language_v8.json"), "non-localized flow definition")

        # base_language of 'eng' but non localized actions
        validate(
            self.load_json("test_flows/legacy/invalid/non_localized_with_language.json"),
            "non-localized flow definition",
        )

        validate(
            self.load_json("test_flows/legacy/invalid/non_localized_ruleset.json"), "non-localized flow definition"
        )

    def test_trim_revisions(self):
        start = timezone.now()

        flow1 = self.create_flow("Flow 1")
        flow2 = self.create_flow("Flow 2")

        revision = 100
        FlowRevision.objects.all().update(revision=revision)

        # create a single old clinic revision
        FlowRevision.objects.create(
            flow=flow2,
            definition=dict(),
            revision=99,
            created_on=timezone.now() - timedelta(days=7),
            created_by=self.admin,
        )

        # make a bunch of revisions for flow 1 on the same day
        created = timezone.now().replace(hour=6) - timedelta(days=1)
        for i in range(25):
            revision -= 1
            created = created - timedelta(minutes=1)
            FlowRevision.objects.create(
                flow=flow1, definition=dict(), revision=revision, created_by=self.admin, created_on=created
            )

        # then for 5 days prior, make a few more
        for i in range(5):
            created = created - timedelta(days=1)
            for i in range(10):
                revision -= 1
                created = created - timedelta(minutes=1)
                FlowRevision.objects.create(
                    flow=flow1, definition=dict(), revision=revision, created_by=self.admin, created_on=created
                )

        # trim our flow revisions, should be left with original (today), 25 from yesterday, 1 per day for 5 days = 31
        self.assertEqual(76, FlowRevision.objects.filter(flow=flow1).count())
        self.assertEqual(45, FlowRevision.trim(start))
        self.assertEqual(31, FlowRevision.objects.filter(flow=flow1).count())
        self.assertEqual(
            7,
            FlowRevision.objects.filter(flow=flow1)
            .annotate(created_date=TruncDate("created_on"))
            .distinct("created_date")
            .count(),
        )

        # trim our clinic flow manually, should remain unchanged
        self.assertEqual(2, FlowRevision.objects.filter(flow=flow2).count())
        self.assertEqual(0, FlowRevision.trim_for_flow(flow2.id))
        self.assertEqual(2, FlowRevision.objects.filter(flow=flow2).count())

        # call our task
        trim_flow_revisions()
        self.assertEqual(2, FlowRevision.objects.filter(flow=flow2).count())
        self.assertEqual(31, FlowRevision.objects.filter(flow=flow1).count())

        # call again (testing reading redis key)
        trim_flow_revisions()
        self.assertEqual(2, FlowRevision.objects.filter(flow=flow2).count())
        self.assertEqual(31, FlowRevision.objects.filter(flow=flow1).count())
