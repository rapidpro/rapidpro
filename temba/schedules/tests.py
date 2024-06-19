from datetime import datetime, timezone as tzone
from zoneinfo import ZoneInfo

from django.utils import timezone

from temba import settings
from temba.msgs.models import Broadcast, Media
from temba.tests import TembaTest
from temba.utils.compose import compose_deserialize_attachments

from .models import Schedule


class ScheduleTest(TembaTest):
    def setUp(self):
        super().setUp()
        self.joe = self.create_contact("Joe Blow", phone="123")

    def test_get_repeat_days_display(self):
        sched = Schedule.create(self.org, timezone.now(), Schedule.REPEAT_WEEKLY, "M")
        self.assertEqual(sched.get_repeat_days_display(), ["Monday"])

        sched = Schedule.create(self.org, timezone.now(), Schedule.REPEAT_WEEKLY, "TRFSU")
        self.assertEqual(sched.get_repeat_days_display(), ["Tuesday", "Thursday", "Friday", "Saturday", "Sunday"])

        sched = Schedule.create(self.org, timezone.now(), Schedule.REPEAT_WEEKLY, "MTWRFSU")
        self.assertEqual(
            sched.get_repeat_days_display(),
            ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
        )

    def test_schedules(self):
        default_tz = ZoneInfo("Africa/Kigali")

        tcs = [
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
                tz=ZoneInfo("America/Los_Angeles"),
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
                tz=ZoneInfo("America/Los_Angeles"),
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
                tz=ZoneInfo("America/Los_Angeles"),
                first=datetime(2019, 11, 2, hour=10),
                next=[datetime(2019, 11, 9, hour=10), datetime(2019, 11, 16, hour=10)],
                display="each week on Saturday",
            ),
            dict(
                label="daily across start of DST",
                trigger_date=datetime(2019, 3, 8, hour=10),
                now=datetime(2019, 1, 1, hour=9),
                repeat_period=Schedule.REPEAT_DAILY,
                tz=ZoneInfo("America/Los_Angeles"),
                first=datetime(2019, 3, 8, hour=10),
                next=[datetime(2019, 3, 9, hour=10), datetime(2019, 3, 10, hour=10), datetime(2019, 3, 11, hour=10)],
                display="each day at 10:00",
            ),
            dict(
                label="daily across end of DST",
                trigger_date=datetime(2019, 11, 2, hour=10),
                now=datetime(2019, 1, 1, hour=9),
                repeat_period=Schedule.REPEAT_DAILY,
                tz=ZoneInfo("America/Los_Angeles"),
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
            trigger_date = tc["trigger_date"].replace(tzinfo=tz)
            now = tc["now"].replace(tzinfo=tz)

            sched = Schedule.create(
                self.org, trigger_date, tc["repeat_period"], repeat_days_of_week=tc.get("repeat_days_of_week"), now=now
            )

            first = tc.get("first")
            first = first.replace(tzinfo=tz) if first else None

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
                next_fire = sched.calculate_next_fire(next_fire)
                expected_next = next.replace(tzinfo=tz) if next else None
                self.assertEqual(expected_next, next_fire, f"{label}: {expected_next} != {next_fire}")

            self.assertEqual(tc["display"], sched.get_display(), f"display mismatch for {label}")

    def test_update_near_day_boundary(self):
        self.org.timezone = ZoneInfo("US/Eastern")
        self.org.save()
        tz = self.org.timezone

        text = "A broadcast to Joe"
        media_attachments = []
        media = Media.from_upload(
            self.org,
            self.admin,
            self.upload(f"{settings.MEDIA_ROOT}/test_media/steve marten.jpg", "image/jpeg"),
            process=False,
        )
        media_attachments.append({"content_type": media.content_type, "url": media.url})
        compose_deserialize_attachments(media_attachments)

        sched = Schedule.create(self.org, timezone.now(), Schedule.REPEAT_DAILY)

        # our view asserts that our schedule is connected to a broadcast
        self.create_broadcast(
            self.admin,
            {"eng": {"text": text}},
            contacts=[self.joe],
            status=Broadcast.STATUS_QUEUED,
            schedule=sched,
        )

        # way off into the future, but at 11pm NYT
        start_date = datetime(2050, 1, 3, 23, 0, 0, 0)
        start_date = start_date.replace(tzinfo=tz)
        start_date = start_date.astimezone(tzone.utc)

        sched.update_schedule(start_date, Schedule.REPEAT_DAILY, "")
        sched.refresh_from_db()

        # 11pm in NY should be 4am UTC the next day
        self.assertEqual("2050-01-04 04:00:00+00:00", str(sched.next_fire))

        start_date = datetime(2050, 1, 3, 23, 45, 0, 0)
        start_date = start_date.replace(tzinfo=tz)
        start_date = start_date.astimezone(tzone.utc)

        sched.update_schedule(start_date, Schedule.REPEAT_DAILY, "")
        sched.refresh_from_db()

        # next fire should fall at the right hour and minute
        self.assertIn("04:45:00+00:00", str(sched.next_fire))
