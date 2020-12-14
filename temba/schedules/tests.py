from datetime import datetime, timedelta

import pytz

from django.urls import reverse
from django.utils import timezone

from temba.contacts.search.omnibox import omnibox_serialize
from temba.msgs.models import Broadcast
from temba.tests import TembaTest
from temba.utils.dates import datetime_to_str

from .models import Schedule


class ScheduleTest(TembaTest):
    def setUp(self):
        super().setUp()
        self.joe = self.create_contact("Joe Blow", phone="123")

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

    def test_schedules(self):
        default_tz = pytz.timezone("Africa/Kigali")

        tcs = [
            dict(
                label="one time in the past (noop)",
                trigger_date=datetime(2013, 1, 2, hour=10),
                now=datetime(2013, 1, 3, hour=9),
                repeat_period=Schedule.REPEAT_NEVER,
                first=None,
                next=[None],
                display="",
            ),
            dict(
                label="one time in the future (fire once)",
                trigger_date=datetime(2013, 1, 2, hour=10),
                now=datetime(2013, 1, 1, hour=9),
                repeat_period=Schedule.REPEAT_NEVER,
                first=datetime(2013, 1, 2, hour=10),
                next=[],
                display="in 0\xa0minutes",
            ),
            dict(
                label="daily repeating starting in the past",
                trigger_date=datetime(2013, 1, 2, hour=10),
                now=datetime(2013, 1, 3, hour=9),
                repeat_period=Schedule.REPEAT_DAILY,
                first=datetime(2013, 1, 3, hour=10),
                next=[datetime(2013, 1, 4, hour=10), datetime(2013, 1, 5, hour=10)],
                display="each day at 10:00",
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
                display="each month on the 10th",
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
                display="each week on Saturday",
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
                display="each week on Saturday",
            ),
            dict(
                label="daily across start of DST",
                trigger_date=datetime(2019, 3, 8, hour=10),
                now=datetime(2019, 1, 1, hour=9),
                repeat_period=Schedule.REPEAT_DAILY,
                tz=pytz.timezone("America/Los_Angeles"),
                first=datetime(2019, 3, 8, hour=10),
                next=[datetime(2019, 3, 9, hour=10), datetime(2019, 3, 10, hour=10), datetime(2019, 3, 11, hour=10)],
                display="each day at 10:00",
            ),
            dict(
                label="daily across end of DST",
                trigger_date=datetime(2019, 11, 2, hour=10),
                now=datetime(2019, 1, 1, hour=9),
                repeat_period=Schedule.REPEAT_DAILY,
                tz=pytz.timezone("America/Los_Angeles"),
                first=datetime(2019, 11, 2, hour=10),
                next=[datetime(2019, 11, 3, hour=10), datetime(2019, 11, 4, hour=10), datetime(2019, 11, 5, hour=10)],
                display="each day at 10:00",
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
                display="each week on Thursday, Saturday",
            ),
            dict(
                label="weekly repeat starting in the past",
                trigger_date=datetime(2013, 1, 2, hour=10),
                now=datetime(2013, 1, 3, hour=9),
                repeat_period=Schedule.REPEAT_WEEKLY,
                repeat_days_of_week="RS",
                first=datetime(2013, 1, 3, hour=10),
                next=[datetime(2013, 1, 5, hour=10), datetime(2013, 1, 10, hour=10), datetime(2013, 1, 12, hour=10)],
                display="each week on Thursday, Saturday",
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
                display="each month on the 2nd",
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
                display="each month on the 31st",
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

            self.assertEqual(tc["display"], sched.get_display(), f"display mismatch for {label}")

    def test_schedule_ui(self):
        self.login(self.admin)

        # test missing recipients
        omnibox = omnibox_serialize(self.org, [], [], True)
        post_data = dict(text="message content", omnibox=omnibox, sender=self.channel.pk, schedule=True)
        response = self.client.post(reverse("msgs.broadcast_send"), post_data, follow=True)
        self.assertContains(response, "At least one recipient is required")

        # missing message
        omnibox = omnibox_serialize(self.org, [], [self.joe], True)
        post_data = dict(text="", omnibox=omnibox, sender=self.channel.pk, schedule=True)
        response = self.client.post(reverse("msgs.broadcast_send"), post_data, follow=True)
        self.assertContains(response, "This field is required")

        # finally create our message
        post_data = dict(text="A scheduled message to Joe", omnibox=omnibox, sender=self.channel.pk, schedule=True)

        headers = {"HTTP_X_PJAX": "True"}
        response = self.client.post(reverse("msgs.broadcast_send"), post_data, **headers)
        self.assertIn("/broadcast/schedule_read", response["Temba-Success"])

        # should have a schedule with no next fire
        bcast = Broadcast.objects.get()
        schedule = bcast.schedule

        self.assertIsNone(schedule.next_fire)
        self.assertEqual(Schedule.REPEAT_NEVER, schedule.repeat_period)

        # fetch our formax page
        response = self.client.get(response["Temba-Success"])
        self.assertContains(response, "id-schedule")
        broadcast = response.context["object"]

        # update our message
        omnibox = omnibox_serialize(self.org, [], [self.joe], True)
        post_data = dict(message="An updated scheduled message", omnibox=omnibox)
        self.client.post(reverse("msgs.broadcast_update", args=[broadcast.pk]), post_data)
        self.assertEqual(Broadcast.objects.get(id=broadcast.id).text, {"base": "An updated scheduled message"})

        start = datetime(2045, 9, 19, hour=10, minute=15, second=0, microsecond=0)
        start = pytz.utc.normalize(self.org.timezone.localize(start))

        # update the schedule
        post_data = dict(
            repeat_period=Schedule.REPEAT_WEEKLY,
            repeat_days_of_week="W",
            start="later",
            start_datetime=datetime_to_str(start, "%Y-%m-%d %H:%M", self.org.timezone),
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

        self.assertIsNotNone(str(schedule))

    def test_update(self):
        self.login(self.admin)

        # create a schedule broadcast
        self.client.post(
            reverse("msgs.broadcast_send"),
            {
                "text": "A scheduled message to Joe",
                "omnibox": omnibox_serialize(self.org, [], [self.joe], True),
                "sender": self.channel.id,
                "schedule": True,
            },
        )

        schedule = Broadcast.objects.get().schedule

        update_url = reverse("schedules.schedule_update", args=[schedule.id])

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

        tommorrow = now + timedelta(days=1)

        # user in other org can't make changes
        self.login(self.admin2)
        response = self.client.post(update_url, {"start": "never", "repeat_period": "D"})
        self.assertLoginRedirect(response)

        # check schedule is unchanged
        schedule.refresh_from_db()
        self.assertEqual("O", schedule.repeat_period)

        self.login(self.admin)

        # update to never start
        response = self.client.post(update_url, {"start": "never", "repeat_period": "O"})
        self.assertEqual(302, response.status_code)

        schedule.refresh_from_db()
        self.assertIsNone(schedule.next_fire)

        self.client.post(update_url, {"start": "stop", "repeat_period": "O"})

        schedule.refresh_from_db()
        self.assertIsNone(schedule.next_fire)

        response = self.client.post(
            update_url,
            {
                "start": "now",
                "repeat_period": "O",
                "start_datetime": datetime_to_str(now, "%Y-%m-%d %H:%M", self.org.timezone),
            },
        )
        self.assertEqual(302, response.status_code)

        schedule.refresh_from_db()
        self.assertEqual(schedule.repeat_period, "O")
        self.assertFalse(schedule.next_fire)

        response = self.client.post(
            update_url,
            {
                "repeat_period": "D",
                "start": "later",
                "start_datetime": datetime_to_str(tommorrow, "%Y-%m-%d %H:%M", self.org.timezone),
            },
        )
        self.assertEqual(302, response.status_code)

        schedule.refresh_from_db()
        self.assertEqual(schedule.repeat_period, "D")

        response = self.client.post(
            update_url,
            {
                "repeat_period": "D",
                "start": "later",
                "start_datetime": datetime_to_str(tommorrow, "%Y-%m-%d %H:%M", self.org.timezone),
            },
        )
        self.assertEqual(302, response.status_code)

        schedule.refresh_from_db()
        self.assertEqual(schedule.repeat_period, "D")

        # can't omit repeat_days_of_week for weekly
        response = self.client.post(
            update_url,
            {
                "repeat_period": "W",
                "start": "later",
                "repeat_days_of_week": "",
                "start_datetime": datetime_to_str(now, "%Y-%m-%d %H:%M", self.org.timezone),
            },
        )

        self.assertFormError(response, "form", "__all__", "Must specify at least one day of the week")

        schedule.refresh_from_db()
        self.assertEqual(schedule.repeat_period, "D")  # unchanged
        self.assertEqual(schedule.repeat_days_of_week, "")

        # can't set repeat_days_of_week to invalid day
        response = self.client.post(
            update_url,
            {
                "repeat_period": "W",
                "start": "later",
                "repeat_days_of_week": "X",
                "start_datetime": datetime_to_str(now, "%Y-%m-%d %H:%M", self.org.timezone),
            },
        )

        self.assertFormError(
            response, "form", "repeat_days_of_week", "Select a valid choice. X is not one of the available choices."
        )

        schedule.refresh_from_db()
        self.assertEqual(schedule.repeat_period, "D")  # unchanged
        self.assertEqual(schedule.repeat_days_of_week, "")

        # can set to valid days
        response = self.client.post(
            update_url,
            {
                "repeat_period": "W",
                "start": "later",
                "repeat_days_of_week": ["M", "F"],
                "start_datetime": datetime_to_str(now, "%Y-%m-%d %H:%M", self.org.timezone),
            },
        )

        self.assertEqual(response.status_code, 302)

        schedule.refresh_from_db()
        self.assertEqual(schedule.repeat_period, "W")
        self.assertEqual(schedule.repeat_days_of_week, "MF")

    def test_update_near_day_boundary(self):
        self.org.timezone = pytz.timezone("US/Eastern")
        self.org.save()
        tz = self.org.timezone

        omnibox = omnibox_serialize(self.org, [], [self.joe], True)

        self.login(self.admin)
        post_data = dict(text="A scheduled message to Joe", omnibox=omnibox, sender=self.channel.pk, schedule=True)
        self.client.post(reverse("msgs.broadcast_send"), post_data, follow=True)

        bcast = Broadcast.objects.get()
        sched = bcast.schedule

        update_url = reverse("schedules.schedule_update", args=[sched.pk])

        # way off into the future, but at 11pm NYT
        start_date = datetime(2050, 1, 3, 23, 0, 0, 0)
        start_date = tz.localize(start_date)
        start_date = pytz.utc.normalize(start_date.astimezone(pytz.utc))

        post_data = dict()
        post_data["repeat_period"] = "D"
        post_data["start"] = "later"
        post_data["start_datetime"] = (datetime_to_str(start_date, "%Y-%m-%d %H:%M", self.org.timezone),)
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
        post_data["start_datetime"] = (datetime_to_str(start_date, "%Y-%m-%d %H:%M", self.org.timezone),)
        self.client.post(update_url, post_data)
        sched = Schedule.objects.get(pk=sched.pk)

        # next fire should fall at the right hour and minute
        self.assertIn("04:45:00+00:00", str(sched.next_fire))
