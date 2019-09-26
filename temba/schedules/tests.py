import time
from datetime import datetime, timedelta

import pytz

from django.urls import reverse
from django.utils import timezone

from temba.msgs.models import Broadcast
from temba.tests import TembaTest
from temba.utils import json
from temba.tests import MigrationTest
from temba.triggers.models import Trigger

from .models import Schedule
from .tasks import check_schedule_task

MONDAY = 0  # 2
TUESDAY = 1  # 4
WEDNESDAY = 2  # 8
THURSDAY = 3  # 16
FRIDAY = 4  # 32
SATURDAY = 5  # 64
SUNDAY = 6  # 128


def create_schedule(org, user, repeat_period, repeat_days_of_week=None, start_date=None, now=None):
    if not now:
        now = timezone.now()

    if not start_date:
        # Test date is 10am on a Thursday, Jan 3rd
        start_date = datetime(2013, 1, 3, hour=10, minute=0, second=0, microsecond=0).replace(tzinfo=pytz.utc)

    return Schedule.create_schedule(org, user, start_date, repeat_period, repeat_days_of_week, now=now)


class ScheduleTest(TembaTest):
    def setUp(self):
        super().setUp()
        self.joe = self.create_contact(name="Joe Blow", number="123")

    def test_get_repeat_days_display(self):
        sched = Schedule.create_schedule(self.org, self.user, timezone.now(), Schedule.REPEAT_WEEKLY, "M")
        self.assertEqual(sched.get_repeat_days_display(), ["Monday"])

        sched = Schedule.create_schedule(self.org, self.user, timezone.now(), Schedule.REPEAT_WEEKLY, "TRFSU")
        self.assertEqual(sched.get_repeat_days_display(), ["Tuesday", "Thursday", "Friday", "Saturday", "Sunday"])

        sched = Schedule.create_schedule(self.org, self.user, timezone.now(), Schedule.REPEAT_WEEKLY, "MTWRFSU")
        self.assertEqual(
            sched.get_repeat_days_display(),
            ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
        )

    def test_next_fire(self):
        default_tz = pytz.timezone("Africa/Kigali")

        tcs = [
            dict(
                label="one time in the past (noop)",
                trigger_date=datetime(2013, 1, 2, hour=10),
                now=datetime(2013, 1, 3, hour=9),
                repeat_period=Schedule.REPEAT_NEVER,
                first=None,
                next=[None],
            ),
            dict(
                label="one time in the future (fire once)",
                trigger_date=datetime(2013, 1, 2, hour=10),
                now=datetime(2013, 1, 1, hour=9),
                repeat_period=Schedule.REPEAT_NEVER,
                first=datetime(2013, 1, 2, hour=10),
                next=[],
            ),
            dict(
                label="daily repeating starting in the past",
                trigger_date=datetime(2013, 1, 2, hour=10),
                now=datetime(2013, 1, 3, hour=9),
                repeat_period=Schedule.REPEAT_DAILY,
                first=datetime(2013, 1, 3, hour=10),
                next=[datetime(2013, 1, 4, hour=10), datetime(2013, 1, 5, hour=10)],
            ),
            dict(
                label="monthly across start of DST",
                trigger_date=datetime(2019, 2, 10, hour=10),
                now=datetime(2019, 1, 1, hour=9),
                repeat_period=Schedule.REPEAT_MONTHLY,
                tz=pytz.timezone("America/Los_Angeles"),
                first=datetime(2019, 2, 10, hour=10),
                next=[
                    datetime(2019, 3, 10, hour=10),
                    datetime(2019, 4, 10, hour=10),
                    datetime(2019, 5, 10, hour=10),
                    datetime(2019, 6, 10, hour=10),
                    datetime(2019, 7, 10, hour=10),
                    datetime(2019, 8, 10, hour=10),
                    datetime(2019, 9, 10, hour=10),
                    datetime(2019, 10, 10, hour=10),
                    datetime(2019, 11, 10, hour=10),
                ],
            ),
            dict(
                label="weekly across start of DST",
                trigger_date=datetime(2019, 3, 2, hour=10),
                now=datetime(2019, 1, 1, hour=9),
                repeat_period=Schedule.REPEAT_WEEKLY,
                repeat_days_of_week="S",
                tz=pytz.timezone("America/Los_Angeles"),
                first=datetime(2019, 3, 2, hour=10),
                next=[datetime(2019, 3, 9, hour=10), datetime(2019, 3, 16, hour=10)],
            ),
            dict(
                label="weekly across end of DST",
                trigger_date=datetime(2019, 11, 2, hour=10),
                now=datetime(2019, 11, 1, hour=9),
                repeat_period=Schedule.REPEAT_WEEKLY,
                repeat_days_of_week="S",
                tz=pytz.timezone("America/Los_Angeles"),
                first=datetime(2019, 11, 2, hour=10),
                next=[datetime(2019, 11, 9, hour=10), datetime(2019, 11, 16, hour=10)],
            ),
            dict(
                label="daily across start of DST",
                trigger_date=datetime(2019, 3, 8, hour=10),
                now=datetime(2019, 1, 1, hour=9),
                repeat_period=Schedule.REPEAT_DAILY,
                tz=pytz.timezone("America/Los_Angeles"),
                first=datetime(2019, 3, 8, hour=10),
                next=[datetime(2019, 3, 9, hour=10), datetime(2019, 3, 10, hour=10), datetime(2019, 3, 11, hour=10)],
            ),
            dict(
                label="daily across end of DST",
                trigger_date=datetime(2019, 11, 2, hour=10),
                now=datetime(2019, 1, 1, hour=9),
                repeat_period=Schedule.REPEAT_DAILY,
                tz=pytz.timezone("America/Los_Angeles"),
                first=datetime(2019, 11, 2, hour=10),
                next=[datetime(2019, 11, 3, hour=10), datetime(2019, 11, 4, hour=10), datetime(2019, 11, 5, hour=10)],
            ),
            dict(
                label="weekly repeating starting on non weekly day of the week",
                trigger_date=datetime(2013, 1, 2, hour=10),
                now=datetime(2013, 1, 2, hour=9),
                repeat_period=Schedule.REPEAT_WEEKLY,
                repeat_days_of_week="RS",
                first=datetime(2013, 1, 2, hour=10),
                next=[
                    datetime(2013, 1, 3, hour=10),
                    datetime(2013, 1, 5, hour=10),
                    datetime(2013, 1, 10, hour=10),
                    datetime(2013, 1, 12, hour=10),
                ],
            ),
            dict(
                label="weekly repeat starting in the past",
                trigger_date=datetime(2013, 1, 2, hour=10),
                now=datetime(2013, 1, 3, hour=9),
                repeat_period=Schedule.REPEAT_WEEKLY,
                repeat_days_of_week="RS",
                first=datetime(2013, 1, 3, hour=10),
                next=[datetime(2013, 1, 5, hour=10), datetime(2013, 1, 10, hour=10), datetime(2013, 1, 12, hour=10)],
            ),
            dict(
                label="monthly repeat starting in the past",
                trigger_date=datetime(2013, 1, 2, hour=10, minute=35),
                now=datetime(2013, 1, 3, hour=9),
                repeat_period=Schedule.REPEAT_MONTHLY,
                first=datetime(2013, 2, 2, hour=10, minute=35),
                next=[
                    datetime(2013, 3, 2, hour=10, minute=35),
                    datetime(2013, 4, 2, hour=10, minute=35),
                    datetime(2013, 5, 2, hour=10, minute=35),
                ],
            ),
            dict(
                label="monthly on 31st",
                trigger_date=datetime(2013, 1, 31, hour=10, minute=35),
                now=datetime(2013, 1, 3, hour=9),
                repeat_period=Schedule.REPEAT_MONTHLY,
                first=datetime(2013, 1, 31, hour=10, minute=35),
                next=[
                    datetime(2013, 2, 28, hour=10, minute=35),
                    datetime(2013, 3, 31, hour=10, minute=35),
                    datetime(2013, 4, 30, hour=10, minute=35),
                ],
            ),
        ]

        for tc in tcs:
            tz = tc.get("tz", default_tz)
            self.org.timezone = tz

            label = tc["label"]
            trigger_date = tz.localize(tc["trigger_date"])
            now = tz.localize(tc["now"])

            sched = Schedule.create_schedule(
                self.org,
                self.admin,
                trigger_date,
                tc["repeat_period"],
                repeat_days_of_week=tc.get("repeat_days_of_week"),
                now=now,
            )

            first = tc.get("first")
            first = tz.localize(first) if first else None

            self.assertEqual(tc["repeat_period"], sched.repeat_period, label)
            self.assertEqual(tc.get("repeat_days_of_week"), sched.repeat_days_of_week, label)

            if tc["repeat_period"] != Schedule.REPEAT_NEVER:
                self.assertEqual(trigger_date.hour, sched.repeat_hour_of_day, label)
                self.assertEqual(trigger_date.minute, sched.repeat_minute_of_hour, label)
            else:
                self.assertIsNone(sched.repeat_hour_of_day, label)
                self.assertIsNone(sched.repeat_minute_of_hour, label)

            self.assertEqual(first, sched.next_fire, label)

            next_fire = sched.next_fire
            for next in tc["next"]:
                next_fire = Schedule.get_next_fire(sched, next_fire)
                expected_next = tz.localize(next) if next else None
                self.assertEqual(expected_next, next_fire, f"{label}: {expected_next} != {next_fire}")

    def test_schedule_ui(self):
        self.login(self.admin)

        joe = self.create_contact("Joe Blow", "123")

        # test missing recipients
        post_data = dict(text="message content", omnibox="", sender=self.channel.pk, _format="json", schedule=True)
        response = self.client.post(reverse("msgs.broadcast_send"), post_data, follow=True)
        self.assertContains(response, "At least one recipient is required")

        # missing message
        post_data = dict(text="", omnibox="c-%s" % joe.uuid, sender=self.channel.pk, _format="json", schedule=True)
        response = self.client.post(reverse("msgs.broadcast_send"), post_data, follow=True)
        self.assertContains(response, "This field is required")

        # finally create our message
        post_data = dict(
            text="A scheduled message to Joe", omnibox="c-%s" % joe.uuid, sender=self.channel.pk, schedule=True
        )
        response = json.loads(
            self.client.post(reverse("msgs.broadcast_send") + "?_format=json", post_data, follow=True).content
        )
        self.assertIn("/broadcast/schedule_read", response["redirect"])

        # should have a schedule with no next fire
        bcast = Broadcast.objects.get()
        schedule = bcast.schedule

        self.assertIsNone(schedule.next_fire)
        self.assertEqual(Schedule.REPEAT_NEVER, schedule.repeat_period)

        # fetch our formax page
        response = self.client.get(response["redirect"])
        self.assertContains(response, "id-schedule")
        broadcast = response.context["object"]

        # update our message
        post_data = dict(message="An updated scheduled message", omnibox="c-%s" % joe.uuid)
        self.client.post(reverse("msgs.broadcast_update", args=[broadcast.pk]), post_data)
        self.assertEqual(Broadcast.objects.get(id=broadcast.id).text, {"base": "An updated scheduled message"})

        start = datetime(2045, 9, 19, hour=10, minute=15, second=0, microsecond=0)
        start = pytz.utc.normalize(self.org.timezone.localize(start))
        start_stamp = time.mktime(start.timetuple())

        # update the schedule
        post_data = dict(
            repeat_period=Schedule.REPEAT_WEEKLY,
            repeat_days_of_week="W",
            start="later",
            start_datetime_value=start_stamp,
        )
        response = self.client.post(reverse("schedules.schedule_update", args=[broadcast.schedule.pk]), post_data)

        # assert out next fire was updated properly
        schedule.refresh_from_db()
        self.assertEqual(Schedule.REPEAT_WEEKLY, schedule.repeat_period)
        self.assertEqual("W", schedule.repeat_days_of_week)
        self.assertEqual(10, schedule.repeat_hour_of_day)
        self.assertEqual(15, schedule.repeat_minute_of_hour)
        self.assertEqual(start, schedule.next_fire)

        # manually set our fire in the past
        schedule.next_fire = timezone.now() - timedelta(days=1)
        schedule.save(update_fields=["next_fire"])

        # run our task to fire schedules
        check_schedule_task()

        # should have a new broadcasts now
        self.assertEqual(1, Broadcast.objects.filter(id__gt=bcast.id).count())

        # we should have a new fire in the future
        schedule.refresh_from_db()
        self.assertTrue(schedule.next_fire > timezone.now())

    def test_update(self):
        sched = create_schedule(self.org, self.admin, Schedule.REPEAT_WEEKLY, "RS")
        update_url = reverse("schedules.schedule_update", args=[sched.pk])

        # viewer can't access
        self.login(self.user)
        response = self.client.get(update_url)
        self.assertLoginRedirect(response)

        # editor can access
        self.login(self.editor)
        response = self.client.get(update_url)
        self.assertEqual(response.status_code, 200)

        # as can admin user
        self.login(self.admin)
        response = self.client.get(update_url)
        self.assertEqual(response.status_code, 200)

        now = timezone.now()
        now_stamp = time.mktime(now.timetuple())

        tommorrow = now + timedelta(days=1)
        tommorrow_stamp = time.mktime(tommorrow.timetuple())

        post_data = dict()
        post_data["start"] = "never"
        post_data["repeat_period"] = "O"

        response = self.client.post(update_url, post_data)

        schedule = Schedule.objects.get(pk=sched.pk)
        self.assertIsNone(schedule.next_fire)

        post_data = dict()
        post_data["start"] = "stop"
        post_data["repeat_period"] = "O"

        response = self.client.post(update_url, post_data)

        schedule = Schedule.objects.get(pk=sched.pk)
        self.assertIsNone(schedule.next_fire)

        post_data = dict()
        post_data["start"] = "now"
        post_data["repeat_period"] = "O"
        post_data["start_datetime_value"] = "%d" % now_stamp

        response = self.client.post(update_url, post_data)

        schedule = Schedule.objects.get(pk=sched.pk)
        self.assertEqual(schedule.repeat_period, "O")
        self.assertFalse(schedule.next_fire)

        post_data = dict()
        post_data["repeat_period"] = "D"
        post_data["start"] = "later"
        post_data["start_datetime_value"] = "%d" % tommorrow_stamp

        response = self.client.post(update_url, post_data)

        schedule = Schedule.objects.get(pk=sched.pk)
        self.assertEqual(schedule.repeat_period, "D")

        post_data = dict()
        post_data["repeat_period"] = "D"
        post_data["start"] = "later"
        post_data["start_datetime_value"] = "%d" % tommorrow_stamp

        response = self.client.post(update_url, post_data)

        schedule = Schedule.objects.get(pk=sched.pk)
        self.assertEqual(schedule.repeat_period, "D")

    def test_update_near_day_boundary(self):

        self.org.timezone = pytz.timezone("US/Eastern")
        self.org.save()
        tz = self.org.timezone

        sched = create_schedule(self.org, self.admin, Schedule.REPEAT_DAILY)
        Broadcast.create(self.org, self.admin, "Message", schedule=sched, contacts=[self.joe])
        sched = Schedule.objects.get(pk=sched.pk)

        update_url = reverse("schedules.schedule_update", args=[sched.pk])

        self.login(self.admin)

        # way off into the future, but at 11pm NYT
        start_date = datetime(2050, 1, 3, 23, 0, 0, 0)
        start_date = tz.localize(start_date)
        start_date = pytz.utc.normalize(start_date.astimezone(pytz.utc))

        post_data = dict()
        post_data["repeat_period"] = "D"
        post_data["start"] = "later"
        post_data["start_datetime_value"] = "%d" % time.mktime(start_date.timetuple())
        self.client.post(update_url, post_data)
        sched = Schedule.objects.get(pk=sched.pk)

        # 11pm in NY should be 4am UTC the next day
        self.assertEqual("2050-01-04 04:00:00+00:00", str(sched.next_fire))

        start_date = datetime(2050, 1, 3, 23, 45, 0, 0)
        start_date = tz.localize(start_date)
        start_date = pytz.utc.normalize(start_date.astimezone(pytz.utc))

        post_data = dict()
        post_data["repeat_period"] = "D"
        post_data["start"] = "later"
        post_data["start_datetime_value"] = "%d" % time.mktime(start_date.timetuple())
        self.client.post(update_url, post_data)
        sched = Schedule.objects.get(pk=sched.pk)

        # next fire should fall at the right hour and minute
        self.assertIn("04:45:00+00:00", str(sched.next_fire))


class PopulateDaysAndOrgMigrationTest(MigrationTest):
    app = "schedules"
    migrate_from = "0009_auto_20190822_1823"
    migrate_to = "0011_populate_org"

    def setUpBeforeMigration(self, apps):
        # create a schedule with no org but a broadcast
        self.bcast_schedule = Schedule.create_blank_schedule(self.org, self.admin)
        self.bcast_schedule.org = None
        self.bcast_schedule.save(update_fields=["org"])

        group = self.create_group("Test")
        bcast = Broadcast.create(self.org, self.admin, "hello world", groups=[group])
        bcast.schedule = self.bcast_schedule
        bcast.save(update_fields=["schedule"])

        # create a schedule with a trigger
        self.trigger_schedule = Schedule.create_blank_schedule(self.org, self.admin)
        self.trigger_schedule.org = None
        self.trigger_schedule.save(update_fields=["org"])

        flow = self.get_flow("favorites_v13")
        trigger = Trigger.create(self.org, self.admin, Trigger.TYPE_SCHEDULE, flow)
        trigger.schedule = self.trigger_schedule
        trigger.save(update_fields=["schedule"])

        # create a weekly schedule
        self.weekly_schedule = Schedule.create_schedule(
            self.org, self.admin, timezone.now(), Schedule.REPEAT_WEEKLY, repeat_days_of_week="MTR"
        )
        self.weekly_schedule.repeat_days = 22
        self.weekly_schedule.repeat_days_of_week = None
        self.weekly_schedule.repeat_minute_of_hour = None
        self.weekly_schedule.repeat_hour_of_day = 12
        self.weekly_schedule.save(
            update_fields=["repeat_days", "repeat_days_of_week", "repeat_minute_of_hour", "repeat_hour_of_day"]
        )

    def test_org_populated(self):
        self.bcast_schedule.refresh_from_db()
        self.assertEqual(self.org.id, self.bcast_schedule.org_id)

        self.trigger_schedule.refresh_from_db()
        self.assertEqual(self.org.id, self.trigger_schedule.org_id)

    def test_fields_populated(self):
        self.weekly_schedule.refresh_from_db()
        self.assertEqual("MTR", self.weekly_schedule.repeat_days_of_week)
        self.assertEqual(0, self.weekly_schedule.repeat_minute_of_hour)
        self.assertEqual(14, self.weekly_schedule.repeat_hour_of_day)
