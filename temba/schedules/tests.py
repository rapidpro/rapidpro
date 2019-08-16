import time
from datetime import datetime, timedelta

import pytz

from django.urls import reverse
from django.utils import timezone

from temba.msgs.models import Broadcast
from temba.tests import MigrationTest, TembaTest
from temba.triggers.models import Trigger
from temba.utils import json

from .models import Schedule

MONDAY = 0  # 2
TUESDAY = 1  # 4
WEDNESDAY = 2  # 8
THURSDAY = 3  # 16
FRIDAY = 4  # 32
SATURDAY = 5  # 64
SUNDAY = 6  # 128


def create_schedule(user, repeat_period, repeat_days=(), start_date=None):
    if not start_date:
        # Test date is 10am on a Thursday, Jan 3rd
        start_date = datetime(2013, 1, 3, hour=10, minute=0).replace(tzinfo=pytz.utc)

    # create a our bitmask from repeat_days
    bitmask = 0
    for day in repeat_days:
        bitmask += pow(2, (day + 1) % 7)

    return Schedule.create_schedule(start_date, repeat_period, user, bitmask)


class ScheduleTest(TembaTest):
    def setUp(self):
        super().setUp()
        self.joe = self.create_contact(name="Joe Blow", number="123")

    def test_get_repeat_days_display(self):
        sched = Schedule.create_schedule(timezone.now(), "W", self.user, repeat_days=2)
        self.assertEqual(sched.get_repeat_days_display(), ["Monday"])

        sched = Schedule.create_schedule(timezone.now(), "W", self.user, repeat_days=244)
        self.assertEqual(sched.get_repeat_days_display(), ["Tuesday", "Thursday", "Friday", "Saturday", "Sunday"])

        sched = Schedule.create_schedule(timezone.now(), "W", self.user, repeat_days=255)
        self.assertEqual(
            sched.get_repeat_days_display(),
            ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
        )

    def test_schedule(self):
        # updates two days later on Saturday
        tomorrow = timezone.now() + timedelta(days=1)
        sched = create_schedule(self.admin, "W", [THURSDAY, SATURDAY], start_date=tomorrow)

        self.assertTrue(sched.has_pending_fire())
        self.assertEqual(sched.status, "S")

        self.assertEqual(sched.get_repeat_days_display(), ["Thursday", "Saturday"])

        sched.unschedule()
        self.assertEqual(sched.status, "U")

    def assertScheduleFires(self, sched, start, dates):
        last = start
        for date in dates:
            d = sched.get_next_fire(last)
            self.assertEqual(d, date)

            last = d

    def test_month_fire(self):
        start = datetime(2019, 1, 31, hour=10).replace(tzinfo=pytz.utc)
        sched = create_schedule(self.admin, "M", start_date=start)
        create = datetime(2019, 1, 8, hour=10).replace(tzinfo=pytz.utc)

        self.assertScheduleFires(
            sched,
            create,
            [
                datetime(2019, 1, 31, hour=10).replace(tzinfo=pytz.utc),
                datetime(2019, 2, 28, hour=10).replace(tzinfo=pytz.utc),
                datetime(2019, 3, 31, hour=10).replace(tzinfo=pytz.utc),
                datetime(2019, 4, 30, hour=10).replace(tzinfo=pytz.utc),
                datetime(2019, 5, 31, hour=10).replace(tzinfo=pytz.utc),
                datetime(2019, 6, 30, hour=10).replace(tzinfo=pytz.utc),
                datetime(2019, 7, 31, hour=10).replace(tzinfo=pytz.utc),
                datetime(2019, 8, 31, hour=10).replace(tzinfo=pytz.utc),
                datetime(2019, 9, 30, hour=10).replace(tzinfo=pytz.utc),
                datetime(2019, 10, 31, hour=10).replace(tzinfo=pytz.utc),
                datetime(2019, 11, 30, hour=10).replace(tzinfo=pytz.utc),
                datetime(2019, 12, 31, hour=10).replace(tzinfo=pytz.utc),
                datetime(2020, 1, 31, hour=10).replace(tzinfo=pytz.utc),
                datetime(2020, 2, 29, hour=10).replace(tzinfo=pytz.utc),
            ],
        )

        start = datetime(2019, 1, 5, hour=10).replace(tzinfo=pytz.utc)
        sched = create_schedule(self.admin, "M", start_date=start)
        create = datetime(2019, 1, 8, hour=10).replace(tzinfo=pytz.utc)

        self.assertScheduleFires(
            sched,
            create,
            [
                datetime(2019, 2, 5, hour=10).replace(tzinfo=pytz.utc),
                datetime(2019, 3, 5, hour=10).replace(tzinfo=pytz.utc),
                datetime(2019, 4, 5, hour=10).replace(tzinfo=pytz.utc),
                datetime(2019, 5, 5, hour=10).replace(tzinfo=pytz.utc),
                datetime(2019, 6, 5, hour=10).replace(tzinfo=pytz.utc),
                datetime(2019, 7, 5, hour=10).replace(tzinfo=pytz.utc),
                datetime(2019, 8, 5, hour=10).replace(tzinfo=pytz.utc),
                datetime(2019, 9, 5, hour=10).replace(tzinfo=pytz.utc),
                datetime(2019, 10, 5, hour=10).replace(tzinfo=pytz.utc),
                datetime(2019, 11, 5, hour=10).replace(tzinfo=pytz.utc),
                datetime(2019, 12, 5, hour=10).replace(tzinfo=pytz.utc),
                datetime(2020, 1, 5, hour=10).replace(tzinfo=pytz.utc),
                datetime(2020, 2, 5, hour=10).replace(tzinfo=pytz.utc),
            ],
        )

    def test_next_fire(self):

        # updates two days later on Saturday
        sched = create_schedule(self.admin, "W", [THURSDAY, SATURDAY])

        self.assertEqual(sched.repeat_days, 80)
        self.assertEqual(
            datetime(2013, 1, 5, hour=10).replace(tzinfo=timezone.pytz.utc), sched.get_next_fire(sched.next_fire)
        )

        # updates six days later on Wednesday
        sched = create_schedule(self.admin, "W", [WEDNESDAY, THURSDAY])
        self.assertEqual(
            datetime(2013, 1, 9, hour=10).replace(tzinfo=timezone.pytz.utc), sched.get_next_fire(sched.next_fire)
        )

        # since we are starting thursday, a thursday should be 7 days out
        sched = create_schedule(self.admin, "W", [THURSDAY])
        self.assertEqual(
            datetime(2013, 1, 10, hour=10).replace(tzinfo=timezone.pytz.utc), sched.get_next_fire(sched.next_fire)
        )

        # now update, should advance to next thursday (present time)
        now = timezone.now()
        next_update = datetime(now.year, now.month, now.day, hour=10).replace(tzinfo=timezone.pytz.utc)

        # make sure we are looking at the following week if it is a thursday
        if next_update.weekday() == THURSDAY:  # pragma: no cover
            next_update += timedelta(days=7)

        else:  # pragma: no cover
            # add days until we get to the next thursday
            while next_update.weekday() != THURSDAY:
                next_update += timedelta(days=1)

        self.assertTrue(sched.update_schedule())
        self.assertEqual(next_update, sched.next_fire)

        # try a weekly schedule
        sched = create_schedule(self.admin, "W", [THURSDAY])
        self.assertEqual(datetime(2013, 1, 10, hour=10).replace(tzinfo=pytz.utc), sched.get_next_fire(sched.next_fire))
        self.assertTrue(sched.update_schedule())
        self.assertEqual(next_update, sched.next_fire)

        # lastly, a daily schedule
        sched = create_schedule(self.admin, "D")
        self.assertEqual(datetime(2013, 1, 4, hour=10).replace(tzinfo=pytz.utc), sched.get_next_fire(sched.next_fire))

        sched = create_schedule(self.admin, "M")
        self.assertEqual(datetime(2013, 2, 3, hour=10).replace(tzinfo=pytz.utc), sched.get_next_fire(sched.next_fire))
        self.assertTrue(sched.update_schedule(datetime(2013, 4, 1).replace(tzinfo=pytz.utc)))
        self.assertEqual(str(datetime(2013, 4, 3, hour=10).replace(tzinfo=pytz.utc)), str(sched.next_fire))

        sched = create_schedule(self.admin, "M", start_date=datetime(2014, 1, 31, hour=10).replace(tzinfo=pytz.utc))
        self.assertEqual(datetime(2014, 2, 28, hour=10).replace(tzinfo=pytz.utc), sched.get_next_fire(sched.next_fire))
        self.assertTrue(sched.update_schedule(datetime(2014, 2, 28).replace(tzinfo=pytz.utc)))
        self.assertEqual(str(datetime(2014, 3, 31, hour=10).replace(tzinfo=pytz.utc)), str(sched.next_fire))
        self.assertTrue(sched.update_schedule(datetime(2014, 3, 31).replace(tzinfo=pytz.utc)))
        self.assertEqual(str(datetime(2014, 4, 30, hour=10).replace(tzinfo=pytz.utc)), str(sched.next_fire))

        sched = create_schedule(self.admin, "M", start_date=datetime(2014, 1, 31, hour=10).replace(tzinfo=pytz.utc))
        self.assertEqual(
            datetime(2014, 2, 28, hour=10).replace(tzinfo=pytz.utc),
            sched.get_next_fire(datetime(2014, 2, 27, hour=10).replace(tzinfo=pytz.utc)),
        )

        sched = create_schedule(self.admin, "M", start_date=datetime(2014, 1, 29, hour=10).replace(tzinfo=pytz.utc))
        self.assertTrue(sched.update_schedule(datetime(2014, 2, 1).replace(tzinfo=pytz.utc)))
        self.assertEqual(str(datetime(2014, 2, 28, hour=10).replace(tzinfo=pytz.utc)), str(sched.next_fire))
        self.assertTrue(sched.update_schedule(datetime(2014, 3, 1).replace(tzinfo=pytz.utc)))
        self.assertEqual(str(datetime(2014, 3, 29, hour=10).replace(tzinfo=pytz.utc)), str(sched.next_fire))
        self.assertTrue(sched.update_schedule(datetime(2014, 4, 1).replace(tzinfo=pytz.utc)))
        self.assertEqual(str(datetime(2014, 4, 29, hour=10).replace(tzinfo=pytz.utc)), str(sched.next_fire))

        sched = create_schedule(self.admin, "M", start_date=datetime(2013, 12, 27, hour=10).replace(tzinfo=pytz.utc))
        self.assertTrue(sched.update_schedule(datetime(2014, 1, 1).replace(tzinfo=pytz.utc)))
        self.assertEqual(str(datetime(2014, 1, 27, hour=10).replace(tzinfo=pytz.utc)), str(sched.next_fire))
        self.assertTrue(sched.update_schedule(datetime(2014, 2, 1).replace(tzinfo=pytz.utc)))
        self.assertEqual(str(datetime(2014, 2, 27, hour=10).replace(tzinfo=pytz.utc)), str(sched.next_fire))
        self.assertTrue(sched.update_schedule(datetime(2014, 3, 1).replace(tzinfo=pytz.utc)))
        self.assertEqual(str(datetime(2014, 3, 27, hour=10).replace(tzinfo=pytz.utc)), str(sched.next_fire))
        self.assertTrue(sched.update_schedule(datetime(2014, 4, 1).replace(tzinfo=pytz.utc)))
        self.assertEqual(str(datetime(2014, 4, 27, hour=10).replace(tzinfo=pytz.utc)), str(sched.next_fire))

        sched = create_schedule(self.admin, "M", start_date=datetime(2013, 12, 28, hour=10).replace(tzinfo=pytz.utc))
        self.assertTrue(sched.update_schedule(datetime(2014, 1, 1).replace(tzinfo=pytz.utc)))
        self.assertEqual(str(datetime(2014, 1, 28, hour=10).replace(tzinfo=pytz.utc)), str(sched.next_fire))
        self.assertTrue(sched.update_schedule(datetime(2014, 2, 1).replace(tzinfo=pytz.utc)))
        self.assertEqual(str(datetime(2014, 2, 28, hour=10).replace(tzinfo=pytz.utc)), str(sched.next_fire))
        self.assertTrue(sched.update_schedule(datetime(2014, 3, 1).replace(tzinfo=pytz.utc)))
        self.assertEqual(str(datetime(2014, 3, 28, hour=10).replace(tzinfo=pytz.utc)), str(sched.next_fire))
        self.assertTrue(sched.update_schedule(datetime(2014, 4, 1).replace(tzinfo=pytz.utc)))
        self.assertEqual(str(datetime(2014, 4, 28, hour=10).replace(tzinfo=pytz.utc)), str(sched.next_fire))

        sched = create_schedule(self.admin, "M", start_date=datetime(2013, 12, 29, hour=10).replace(tzinfo=pytz.utc))
        self.assertTrue(sched.update_schedule(datetime(2014, 1, 1).replace(tzinfo=pytz.utc)))
        self.assertEqual(str(datetime(2014, 1, 29, hour=10).replace(tzinfo=pytz.utc)), str(sched.next_fire))
        self.assertTrue(sched.update_schedule(datetime(2014, 2, 1).replace(tzinfo=pytz.utc)))
        self.assertEqual(str(datetime(2014, 2, 28, hour=10).replace(tzinfo=pytz.utc)), str(sched.next_fire))
        self.assertTrue(sched.update_schedule(datetime(2014, 3, 1).replace(tzinfo=pytz.utc)))
        self.assertEqual(str(datetime(2014, 3, 29, hour=10).replace(tzinfo=pytz.utc)), str(sched.next_fire))
        self.assertTrue(sched.update_schedule(datetime(2014, 4, 1).replace(tzinfo=pytz.utc)))
        self.assertEqual(str(datetime(2014, 4, 29, hour=10).replace(tzinfo=pytz.utc)), str(sched.next_fire))

        sched = create_schedule(self.admin, "M", start_date=datetime(2013, 12, 30, hour=10).replace(tzinfo=pytz.utc))
        self.assertTrue(sched.update_schedule(datetime(2014, 1, 1).replace(tzinfo=pytz.utc)))
        self.assertEqual(str(datetime(2014, 1, 30, hour=10).replace(tzinfo=pytz.utc)), str(sched.next_fire))
        self.assertTrue(sched.update_schedule(datetime(2014, 2, 1).replace(tzinfo=pytz.utc)))
        self.assertEqual(str(datetime(2014, 2, 28, hour=10).replace(tzinfo=pytz.utc)), str(sched.next_fire))
        self.assertTrue(sched.update_schedule(datetime(2014, 3, 1).replace(tzinfo=pytz.utc)))
        self.assertEqual(str(datetime(2014, 3, 30, hour=10).replace(tzinfo=pytz.utc)), str(sched.next_fire))
        self.assertTrue(sched.update_schedule(datetime(2014, 4, 1).replace(tzinfo=pytz.utc)))
        self.assertEqual(str(datetime(2014, 4, 30, hour=10).replace(tzinfo=pytz.utc)), str(sched.next_fire))

        sched = create_schedule(self.admin, "M", start_date=datetime(2013, 12, 31, hour=10).replace(tzinfo=pytz.utc))
        self.assertTrue(sched.update_schedule(datetime(2014, 1, 1).replace(tzinfo=pytz.utc)))
        self.assertEqual(str(datetime(2014, 1, 31, hour=10).replace(tzinfo=pytz.utc)), str(sched.next_fire))
        self.assertTrue(sched.update_schedule(datetime(2014, 2, 1).replace(tzinfo=pytz.utc)))
        self.assertEqual(str(datetime(2014, 2, 28, hour=10).replace(tzinfo=pytz.utc)), str(sched.next_fire))
        self.assertTrue(sched.update_schedule(datetime(2014, 3, 1).replace(tzinfo=pytz.utc)))
        self.assertEqual(str(datetime(2014, 3, 31, hour=10).replace(tzinfo=pytz.utc)), str(sched.next_fire))
        self.assertTrue(sched.update_schedule(datetime(2014, 4, 1).replace(tzinfo=pytz.utc)))
        self.assertEqual(str(datetime(2014, 4, 30, hour=10).replace(tzinfo=pytz.utc)), str(sched.next_fire))

        sched = create_schedule(self.admin, "M", start_date=datetime(2013, 12, 31, hour=10).replace(tzinfo=pytz.utc))
        self.assertTrue(sched.update_schedule(datetime(2013, 12, 31).replace(tzinfo=pytz.utc)))
        self.assertEqual(str(datetime(2014, 1, 31, hour=10).replace(tzinfo=pytz.utc)), str(sched.next_fire))
        self.assertTrue(sched.update_schedule(datetime(2014, 1, 31).replace(tzinfo=pytz.utc)))
        self.assertEqual(str(datetime(2014, 2, 28, hour=10).replace(tzinfo=pytz.utc)), str(sched.next_fire))
        self.assertTrue(sched.update_schedule(datetime(2014, 2, 28).replace(tzinfo=pytz.utc)))
        self.assertEqual(str(datetime(2014, 3, 31, hour=10).replace(tzinfo=pytz.utc)), str(sched.next_fire))
        self.assertTrue(sched.update_schedule(datetime(2014, 3, 31).replace(tzinfo=pytz.utc)))
        self.assertEqual(str(datetime(2014, 4, 30, hour=10).replace(tzinfo=pytz.utc)), str(sched.next_fire))
        self.assertTrue(sched.update_schedule(datetime(2014, 4, 30).replace(tzinfo=pytz.utc)))
        self.assertEqual(str(datetime(2014, 5, 31, hour=10).replace(tzinfo=pytz.utc)), str(sched.next_fire))

        sched = create_schedule(self.admin, "M", start_date=datetime(2013, 12, 5, hour=10).replace(tzinfo=pytz.utc))
        self.assertTrue(sched.update_schedule(datetime(2013, 12, 5).replace(tzinfo=pytz.utc)))
        self.assertEqual(str(datetime(2014, 1, 5, hour=10).replace(tzinfo=pytz.utc)), str(sched.next_fire))
        self.assertTrue(sched.update_schedule(datetime(2014, 1, 5).replace(tzinfo=pytz.utc)))
        self.assertEqual(str(datetime(2014, 2, 5, hour=10).replace(tzinfo=pytz.utc)), str(sched.next_fire))
        self.assertTrue(sched.update_schedule(datetime(2014, 2, 5).replace(tzinfo=pytz.utc)))
        self.assertEqual(str(datetime(2014, 3, 5, hour=10).replace(tzinfo=pytz.utc)), str(sched.next_fire))
        self.assertTrue(sched.update_schedule(datetime(2014, 3, 5).replace(tzinfo=pytz.utc)))
        self.assertEqual(str(datetime(2014, 4, 5, hour=10).replace(tzinfo=pytz.utc)), str(sched.next_fire))
        self.assertTrue(sched.update_schedule(datetime(2014, 4, 5).replace(tzinfo=pytz.utc)))
        self.assertEqual(str(datetime(2014, 5, 5, hour=10).replace(tzinfo=pytz.utc)), str(sched.next_fire))

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

        # fetch our formax page
        response = self.client.get(response["redirect"])
        self.assertContains(response, "id-schedule")
        broadcast = response.context["object"]

        # update our message
        post_data = dict(message="An updated scheduled message", omnibox="c-%s" % joe.uuid)
        self.client.post(reverse("msgs.broadcast_update", args=[broadcast.pk]), post_data)
        self.assertEqual(Broadcast.objects.get(id=broadcast.id).text, {"base": "An updated scheduled message"})

        # update the schedule
        post_data = dict(repeat_period="W", repeat_days=6, start="later", start_datetime_value=1)
        response = self.client.post(reverse("schedules.schedule_update", args=[broadcast.schedule.pk]), post_data)

        # broadcast = Broadcast.objects.get(pk=broadcast.pk)
        # self.assertTrue(broadcast.schedule.has_pending_fire())

    def test_update(self):
        sched = create_schedule(self.admin, "W", [THURSDAY, SATURDAY])
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
        self.assertEqual(schedule.status, "U")

        post_data = dict()
        post_data["start"] = "stop"
        post_data["repeat_period"] = "O"

        response = self.client.post(update_url, post_data)

        schedule = Schedule.objects.get(pk=sched.pk)
        self.assertEqual(schedule.status, "U")

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

        response = self.client.post(update_url, post_data)

        schedule = Schedule.objects.get(pk=sched.pk)
        self.assertEqual(schedule.repeat_period, "D")

    def test_calculating_next_fire(self):

        self.org.timezone = pytz.timezone("US/Eastern")
        self.org.save()

        tz = self.org.timezone
        eleven_fifteen_est = tz.localize(datetime(2013, 1, 3, hour=23, minute=15, second=0, microsecond=0))

        # Test date is 10:15am on a Thursday, Jan 3rd
        schedule = create_schedule(self.admin, "D", start_date=eleven_fifteen_est)
        schedule.save()

        Broadcast.create(self.org, self.admin, "Message", schedule=schedule, contacts=[self.joe])
        schedule = Schedule.objects.get(pk=schedule.pk)

        # when is the next fire once our first one passes
        sched_date = tz.localize(datetime(2013, 1, 3, hour=23, minute=30, second=0, microsecond=0))

        schedule.update_schedule(sched_date)
        self.assertEqual("2013-01-04 23:15:00-05:00", str(schedule.next_fire))

    def test_update_near_day_boundary(self):

        self.org.timezone = pytz.timezone("US/Eastern")
        self.org.save()
        tz = self.org.timezone

        sched = create_schedule(self.admin, "D")
        Broadcast.create(self.org, self.admin, "Message", schedule=sched, contacts=[self.joe])
        sched = Schedule.objects.get(pk=sched.pk)

        update_url = reverse("schedules.schedule_update", args=[sched.pk])

        self.login(self.admin)

        # way off into the future
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

        # a time in the past
        start_date = datetime(2010, 1, 3, 23, 45, 0, 0)
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


class RemoveOrphansMigrationTest(MigrationTest):
    app = "schedules"
    migrate_from = "0006_initial"
    migrate_to = "0007_remove_orphans"

    def setUpBeforeMigration(self, apps):
        contact1 = self.create_contact("Bob", twitter="bob")
        favorites = self.get_flow("favorites")

        # create schedule attached to a trigger
        self.trigger = Trigger.create(
            self.org, self.admin, Trigger.TYPE_SCHEDULE, flow=favorites, schedule=create_schedule(self.admin, "D")
        )

        # create schedule attached to a broadcast
        self.broadcast = Broadcast.create(
            self.org, self.admin, "hi there", contacts=[contact1], schedule=create_schedule(self.admin, "W")
        )

        # create orphan schedule
        create_schedule(self.admin, "M")

    def test_merged(self):
        self.assertEqual(Schedule.objects.count(), 2)
