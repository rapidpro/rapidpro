from datetime import date, timedelta, timezone as tzone

from django.db import connection
from django.utils import timezone

from temba.flows.models import FlowActivityCount, FlowRun, FlowSession
from temba.flows.tasks import squash_activity_counts
from temba.tests import TembaTest
from temba.utils.uuid import uuid4


class FlowActivityCountTest(TembaTest):
    def test_node_counts(self):
        flow = self.create_flow("Test 1")
        contact = self.create_contact("Bob", phone="+1234567890")
        session = FlowSession.objects.create(
            uuid=uuid4(),
            org=self.org,
            contact=contact,
            status=FlowSession.STATUS_WAITING,
            output_url="http://sessions.com/123.json",
            created_on=timezone.now(),
            wait_started_on=timezone.now(),
            wait_expires_on=timezone.now() + timedelta(days=7),
            wait_resume_on_expire=False,
        )

        def create_run(status, node_uuid):
            return FlowRun.objects.create(
                uuid=uuid4(),
                org=self.org,
                session=session,
                flow=flow,
                contact=contact,
                status=status,
                created_on=timezone.now(),
                modified_on=timezone.now(),
                exited_on=timezone.now() if status not in ("A", "W") else None,
                current_node_uuid=node_uuid,
            )

        run1 = create_run(FlowRun.STATUS_ACTIVE, "ebb534e1-e2e0-40e9-8652-d195e87d832b")
        run2 = create_run(FlowRun.STATUS_WAITING, "ebb534e1-e2e0-40e9-8652-d195e87d832b")
        run3 = create_run(FlowRun.STATUS_WAITING, "bbb71aab-e026-442e-9971-6bc4f48941fb")
        create_run(FlowRun.STATUS_INTERRUPTED, "bbb71aab-e026-442e-9971-6bc4f48941fb")

        self.assertEqual(
            {"node:ebb534e1-e2e0-40e9-8652-d195e87d832b": 2, "node:bbb71aab-e026-442e-9971-6bc4f48941fb": 1},
            flow.counts.prefix("node:").scope_totals(),
        )

        run1.status = FlowRun.STATUS_EXPIRED
        run1.exited_on = timezone.now()
        run1.save(update_fields=("status", "exited_on"))

        run3.current_node_uuid = "85b0c928-4bd9-4a2e-84b2-164802c32486"
        run3.save(update_fields=("current_node_uuid",))

        self.assertEqual(
            {
                "node:ebb534e1-e2e0-40e9-8652-d195e87d832b": 1,
                "node:bbb71aab-e026-442e-9971-6bc4f48941fb": 0,
                "node:85b0c928-4bd9-4a2e-84b2-164802c32486": 1,
            },
            flow.counts.prefix("node:").scope_totals(),
        )

        run2.delete()

        self.assertEqual(
            {
                "node:ebb534e1-e2e0-40e9-8652-d195e87d832b": 0,
                "node:bbb71aab-e026-442e-9971-6bc4f48941fb": 0,
                "node:85b0c928-4bd9-4a2e-84b2-164802c32486": 1,
            },
            flow.counts.prefix("node:").scope_totals(),
        )

    def test_status_counts(self):
        contact = self.create_contact("Bob", phone="+1234567890")
        session = FlowSession.objects.create(
            uuid=uuid4(),
            org=self.org,
            contact=contact,
            status=FlowSession.STATUS_WAITING,
            output_url="http://sessions.com/123.json",
            created_on=timezone.now(),
            wait_started_on=timezone.now(),
            wait_expires_on=timezone.now() + timedelta(days=7),
            wait_resume_on_expire=False,
        )

        def create_runs(flow_status_pairs: tuple) -> list:
            runs = []
            for flow, status in flow_status_pairs:
                runs.append(
                    FlowRun(
                        uuid=uuid4(),
                        org=self.org,
                        session=session,
                        flow=flow,
                        contact=contact,
                        status=status,
                        created_on=timezone.now(),
                        modified_on=timezone.now(),
                        exited_on=timezone.now() if status not in ("A", "W") else None,
                    )
                )
            return FlowRun.objects.bulk_create(runs)

        flow1 = self.create_flow("Test 1")
        flow2 = self.create_flow("Test 2")

        runs1 = create_runs(
            (
                (flow1, FlowRun.STATUS_ACTIVE),
                (flow2, FlowRun.STATUS_WAITING),
                (flow1, FlowRun.STATUS_ACTIVE),
                (flow2, FlowRun.STATUS_WAITING),
                (flow1, FlowRun.STATUS_WAITING),
                (flow1, FlowRun.STATUS_COMPLETED),
            )
        )

        self.assertEqual(
            {(flow1, "status:A"): 2, (flow2, "status:W"): 2, (flow1, "status:W"): 1, (flow1, "status:C"): 1},
            {(c.flow, c.scope): c.count for c in FlowActivityCount.objects.all()},
        )
        self.assertEqual({"status:A": 2, "status:W": 1, "status:C": 1}, flow1.counts.scope_totals())
        self.assertEqual({"status:W": 2}, flow2.counts.scope_totals())

        # no difference after squashing
        squash_activity_counts()

        self.assertEqual({"status:A": 2, "status:W": 1, "status:C": 1}, flow1.counts.scope_totals())
        self.assertEqual({"status:W": 2}, flow2.counts.scope_totals())

        runs2 = create_runs(
            (
                (flow1, FlowRun.STATUS_ACTIVE),
                (flow1, FlowRun.STATUS_ACTIVE),
                (flow2, FlowRun.STATUS_EXPIRED),
            )
        )

        self.assertEqual({"status:A": 4, "status:W": 1, "status:C": 1}, flow1.counts.scope_totals())
        self.assertEqual({"status:W": 2, "status:X": 1}, flow2.counts.scope_totals())

        # bulk update runs like they're being interrupted
        FlowRun.objects.filter(id__in=[r.id for r in runs1]).update(
            status=FlowRun.STATUS_INTERRUPTED, exited_on=timezone.now()
        )

        self.assertEqual({"status:A": 2, "status:W": 0, "status:C": 0, "status:I": 4}, flow1.counts.scope_totals())
        self.assertEqual({"status:W": 0, "status:X": 1, "status:I": 2}, flow2.counts.scope_totals())

        # no difference after squashing except zeros gone
        squash_activity_counts()

        self.assertEqual({"status:A": 2, "status:I": 4}, flow1.counts.scope_totals())
        self.assertEqual({"status:X": 1, "status:I": 2}, flow2.counts.scope_totals())

        # do manual deletion of some runs
        FlowRun.objects.filter(id__in=[r.id for r in runs2]).update(delete_from_results=True)
        FlowRun.objects.filter(id__in=[r.id for r in runs2]).delete()

        self.assertEqual({"status:A": 0, "status:I": 4}, flow1.counts.scope_totals())
        self.assertEqual({"status:X": 0, "status:I": 2}, flow2.counts.scope_totals())

        # do archival deletion of the rest
        FlowRun.objects.filter(id__in=[r.id for r in runs1]).delete()

        # status counts are unchanged
        self.assertEqual({"status:A": 0, "status:I": 4}, flow1.counts.scope_totals())
        self.assertEqual({"status:X": 0, "status:I": 2}, flow2.counts.scope_totals())

    def test_msgsin_counts(self):
        flow1 = self.create_flow("Test 1")
        flow2 = self.create_flow("Test 2")

        def handle(msg, flow):
            msg.status = "H"
            msg.flow = flow
            msg.save(update_fields=("status", "flow"))

        contact = self.create_contact("Bob", phone="+1234567890")
        self.create_outgoing_msg(contact, "Out")  # should be ignored
        in1 = self.create_incoming_msg(contact, "In 1", status="P")
        in2 = self.create_incoming_msg(contact, "In 2", status="P")
        in3 = self.create_incoming_msg(contact, "In 3", status="P")

        self.assertEqual(0, flow1.counts.count())
        self.assertEqual(0, flow2.counts.count())

        handle(in1, flow1)
        handle(in2, flow1)
        handle(in3, flow2)

        self.assertEqual(6, flow1.counts.count())
        self.assertEqual(3, flow2.counts.count())

        today = date.today().isoformat()  # date as YYYY-MM-DD
        dow = date.today().isoweekday()  # weekday as 1(Mon)-7(Sun)
        hour = timezone.now().astimezone(tzone.utc).hour

        self.assertEqual(
            {f"msgsin:date:{today}": 2, f"msgsin:dow:{dow}": 2, f"msgsin:hour:{hour}": 2},
            flow1.counts.filter(scope__startswith="msgsin:").scope_totals(),
        )
        self.assertEqual(
            {f"msgsin:date:{today}": 1, f"msgsin:dow:{dow}": 1, f"msgsin:hour:{hour}": 1},
            flow2.counts.filter(scope__startswith="msgsin:").scope_totals(),
        )

        # other changes to msgs shouldn't create new counts
        in1.archive()
        in2.archive()

        self.assertEqual(6, flow1.counts.count())
        self.assertEqual(3, flow2.counts.count())

    def test_squashing(self):
        flow1 = self.create_flow("Test 1")
        flow1.counts.create(scope="foo:1", count=1)
        flow1.counts.create(scope="foo:1", count=2)
        flow1.counts.create(scope="foo:2", count=4)
        flow1.counts.create(scope="foo:3", count=-6)
        flow1.counts.create(scope="foo:3", count=-1)

        flow2 = self.create_flow("Test 2")
        flow2.counts.create(scope="foo:1", count=7)
        flow2.counts.create(scope="foo:1", count=3)
        flow2.counts.create(scope="foo:2", count=8)  # unsquashed that sum to zero
        flow2.counts.create(scope="foo:2", count=-8)
        flow2.counts.create(scope="foo:3", count=5)

        self.assertEqual(3, flow1.counts.filter(scope="foo:1").sum())
        self.assertEqual(4, flow1.counts.filter(scope="foo:2").sum())
        self.assertEqual(-7, flow1.counts.filter(scope="foo:3").sum())  # negative counts supported
        self.assertEqual(0, flow1.counts.filter(scope="foo:4").sum())  # zero if no such scope exists
        self.assertEqual(10, flow2.counts.filter(scope="foo:1").sum())
        self.assertEqual(0, flow2.counts.filter(scope="foo:2").sum())
        self.assertEqual(5, flow2.counts.filter(scope="foo:3").sum())

        squash_activity_counts()

        self.assertEqual({"foo:1", "foo:2", "foo:3"}, set(flow1.counts.values_list("scope", flat=True)))

        # flow2/foo:2 should be gone because it squashed to zero
        self.assertEqual({"foo:1", "foo:3"}, set(flow2.counts.values_list("scope", flat=True)))

        self.assertEqual(3, flow1.counts.filter(scope="foo:1").sum())
        self.assertEqual(4, flow1.counts.filter(scope="foo:2").sum())
        self.assertEqual(-7, flow1.counts.filter(scope="foo:3").sum())
        self.assertEqual(10, flow2.counts.filter(scope="foo:1").sum())
        self.assertEqual(0, flow2.counts.filter(scope="foo:2").sum())
        self.assertEqual(5, flow2.counts.filter(scope="foo:3").sum())

        flow2.counts.create(scope="foo:3", count=-5)  # unsquashed zero + squashed zero

        squash_activity_counts()

        # flow2/foo:3 should be gone because it squashed to zero
        self.assertEqual({"foo:1"}, set(flow2.counts.values_list("scope", flat=True)))

        # test that model being asked to squash a set that matches no rows doesn't insert anytihng
        with connection.cursor() as cursor:
            sql, params = FlowActivityCount.get_squash_query({"flow_id": flow1.id, "scope": "foo:9"})
            cursor.execute(sql, params)

        self.assertEqual({"foo:1", "foo:2", "foo:3"}, set(flow1.counts.values_list("scope", flat=True)))
